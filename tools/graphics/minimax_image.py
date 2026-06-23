"""MiniMax image generation provider tool.

Wraps the MiniMax ``POST /v1/image_generation`` endpoint, which exposes
both text-to-image (T2I) and image-to-image (I2I) generation behind a
single URL. The mode is selected automatically based on whether the caller
passes any source image reference — ``image_url`` / ``image_path`` for
single-image edit, or ``image_urls`` / ``image_paths`` for multi-reference
edits / character anchoring.

For T2I the body includes ``prompt``, optional ``style`` (when
``model=image-01-live``), ``aspect_ratio``, ``n``, ``seed``, etc.
For I2I the body adds a ``subject_reference`` array describing each
reference image's role (``type: "character"`` etc.) and source URL.
"""

from __future__ import annotations

import base64
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    RetryPolicy,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)


def _file_to_data_uri(path_str: str) -> str:
    """Encode a local file as a ``data:<mime>;base64,...`` URI."""
    import mimetypes

    path = Path(path_str)
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    mime_type, _ = mimetypes.guess_type(path.name)
    if not mime_type:
        mime_type = "application/octet-stream"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


class MiniMaxImage(BaseTool):
    name = "minimax_image"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "image_generation"
    provider = "minimax"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.SEEDED
    runtime = ToolRuntime.API

    dependencies = ["env:MINIMAX_API_KEY"]
    install_instructions = (
        "Set MINIMAX_API_KEY to your MiniMax platform API key.\n"
        "  Get one at https://platform.minimaxi.com/user-center/basic-information/Interface-key\n"
        "Optional: set MINIMAX_IMAGE_MODEL (default image-01) and "
        "MINIMAX_IMAGE_RESPONSE_FORMAT (url | base64, default url)."
    )
    fallback = "flux_image"
    fallback_tools = [
        "flux_image",
        "google_imagen",
        "openai_image",
        "grok_image",
        "recraft_image",
    ]
    agent_skills = ["minimax-image"]

    capabilities = [
        "generate_image",
        "text_to_image",
        "image_to_image",
        "edit_image",
        "character_reference",
        "style_transfer",
    ]
    supports = {
        "image_edit": True,
        "multiple_outputs": True,
        "aspect_ratio": True,
        "seed": True,
        "custom_size": True,  # only for image-01
        "reference_image": True,
        "multiple_reference_images": True,
        "prompt_optimizer": True,
        "watermark": True,
        "native_audio": False,
    }
    best_for = [
        "high-quality text-to-image with 8 aspect ratios including 21:9",
        "image-to-image with character / subject reference for identity consistency",
        "Mandarin-friendly prompt interpretation, 9-image batch in one request",
    ]
    not_good_for = [
        "fully offline generation",
        "models requiring tools the user has not enabled (eg. Veo / Imagen Ultra)",
    ]

    # ---- Endpoint config ----
    PRIMARY_URL = "https://api.minimaxi.com/v1/image_generation"
    FALLBACK_URL = "https://api-bj.minimaxi.com/v1/image_generation"

    DEFAULT_MODEL = "image-01"
    DEFAULT_MODEL_ENV = "MINIMAX_IMAGE_MODEL"
    DEFAULT_FORMAT_ENV = "MINIMAX_IMAGE_RESPONSE_FORMAT"

    _ALLOWED_MODELS = ("image-01", "image-01-live")
    _ALLOWED_ASPECT_RATIOS = (
        "1:1",
        "16:9",
        "4:3",
        "3:2",
        "2:3",
        "3:4",
        "9:16",
        "21:9",  # only effective on image-01
    )
    _ALLOWED_FORMATS = ("url", "base64")

    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {
                "type": "string",
                "maxLength": 1500,
                "description": "Text description of the desired image. Up to 1500 characters.",
            },
            "model": {
                "type": "string",
                "enum": list(_ALLOWED_MODELS),
                "default": "image-01",
                "description": "image-01 (default) supports width/height and 21:9. image-01-live supports the style object.",
            },
            "aspect_ratio": {
                "type": "string",
                "enum": list(_ALLOWED_ASPECT_RATIOS),
                "default": "1:1",
                "description": "Image aspect ratio. Defaults to 1:1. 21:9 only works with image-01.",
            },
            "width": {
                "type": "integer",
                "minimum": 512,
                "maximum": 2048,
                "description": "image-01 only. Must be a multiple of 8. If both width and height are set, they take precedence over aspect_ratio.",
            },
            "height": {
                "type": "integer",
                "minimum": 512,
                "maximum": 2048,
                "description": "image-01 only. Must be a multiple of 8.",
            },
            "response_format": {
                "type": "string",
                "enum": list(_ALLOWED_FORMATS),
                "default": "url",
                "description": "How the API returns the image. 'url' URLs expire after 24 hours; 'base64' is safer for archival.",
            },
            "n": {
                "type": "integer",
                "minimum": 1,
                "maximum": 9,
                "default": 1,
                "description": "Number of images to generate in one request.",
            },
            "seed": {
                "type": "integer",
                "description": "Reproducibility seed. Same seed + params gives near-identical output.",
            },
            "prompt_optimizer": {
                "type": "boolean",
                "default": False,
                "description": "If true, the MiniMax server rewrites the prompt for better quality. Off by default to keep prompt fidelity.",
            },
            "aigc_watermark": {
                "type": "boolean",
                "default": False,
                "description": "Add the AIGC watermark to the generated image.",
            },
            "style": {
                "type": "object",
                "description": "Drawing-style preset. Only honored when model=image-01-live. Pass-through to API; consult platform console for valid sub-keys.",
            },
            # ---- I2I inputs ----
            "generation_mode": {
                "type": "string",
                "enum": ["generate", "edit"],
                "default": "generate",
                "description": "Set to 'edit' to force image-to-image mode even without reference images (rare).",
            },
            "subject_reference_type": {
                "type": "string",
                "default": "character",
                "description": "subject_reference[].type tag. Defaults to 'character' for identity consistency.",
            },
            "image_url": {
                "type": "string",
                "description": "Single source image URL for I2I edit mode.",
            },
            "image_path": {
                "type": "string",
                "description": "Single local source image path for I2I edit mode. Will be base64-encoded into a data URI.",
            },
            "image_urls": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Multiple source image URLs for I2I mode. Each becomes a separate subject_reference entry.",
            },
            "image_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Multiple local source image paths for I2I mode.",
            },
            "output_path": {"type": "string"},
        },
    }

    output_schema = {
        "type": "object",
        "properties": {
            "output": {"type": "string"},
            "outputs": {"type": "array", "items": {"type": "string"}},
            "model": {"type": "string"},
            "prompt": {"type": "string"},
            "aspect_ratio": {"type": "string"},
            "images_generated": {"type": "integer"},
            "success_count": {"type": "integer"},
            "failed_count": {"type": "integer"},
            "task_id": {"type": "string"},
            "generation_mode": {"type": "string"},
        },
    }

    artifact_schema = {
        "type": "array",
        "items": {"type": "string"},
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=512, vram_mb=0, disk_mb=200, network_required=True
    )
    retry_policy = RetryPolicy(
        max_retries=2,
        backoff_seconds=1.5,
        retryable_errors=["timeout", "rate_limit", "internal error", "server_error"],
    )
    idempotency_key_fields = [
        "prompt",
        "model",
        "aspect_ratio",
        "width",
        "height",
        "seed",
        "n",
        "response_format",
        "image_url",
        "image_path",
    ]
    side_effects = [
        "writes image file(s) to output_path",
        "calls MiniMax /v1/image_generation API",
    ]
    user_visible_verification = [
        "Inspect generated image for relevance and quality",
        "Confirm character likeness when using subject_reference",
    ]
    quality_score = 0.88
    latency_p50_seconds = 6.0

    # ---- Lifecycle ----

    def _get_api_key(self) -> str | None:
        return (
            os.environ.get("MINIMAX_API_KEY")
            or os.environ.get("MINIMAX_IMAGE_API_KEY")
            or os.environ.get("MM_API_KEY")
        )

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if self._get_api_key() else ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        # MiniMax publishes per-image pricing that varies by model. We use a
        # conservative flat estimate until usage data is available.
        n = int(inputs.get("n", 1))
        model = inputs.get("model", self.DEFAULT_MODEL)
        per_image = 0.04 if model == "image-01-live" else 0.03
        return round(per_image * n, 4)

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        api_key = self._get_api_key()
        if not api_key:
            return ToolResult(
                success=False,
                error="No MiniMax API key. " + self.install_instructions,
            )

        prompt = inputs.get("prompt")
        if not prompt:
            return ToolResult(success=False, error="prompt is required")
        if len(prompt) > 1500:
            return ToolResult(
                success=False,
                error=f"prompt length {len(prompt)} exceeds MiniMax limit of 1500 characters",
            )

        model = inputs.get("model") or os.environ.get(self.DEFAULT_MODEL_ENV) or self.DEFAULT_MODEL
        if model not in self._ALLOWED_MODELS:
            return ToolResult(
                success=False,
                error=f"Unsupported model {model!r}. Allowed: {', '.join(self._ALLOWED_MODELS)}",
            )

        # Validate width/height range for image-01 (multiple of 8).
        if model == "image-01":
            for key in ("width", "height"):
                v = inputs.get(key)
                if v is not None and (v < 512 or v > 2048 or v % 8 != 0):
                    return ToolResult(
                        success=False,
                        error=f"{key} must be in [512, 2048] and a multiple of 8; got {v}",
                    )

        start = time.time()
        try:
            result = self._generate(inputs, api_key=api_key, model=model)
        except Exception as exc:
            return ToolResult(success=False, error=f"MiniMax image generation failed: {self._safe_error(exc)}")

        result.duration_seconds = round(time.time() - start, 2)
        if not result.cost_usd:
            result.cost_usd = self.estimate_cost(inputs)
        return result

    # ---- Mode detection and payload construction ----

    def _wants_edit_mode(self, inputs: dict[str, Any]) -> bool:
        if inputs.get("generation_mode") == "edit":
            return True
        if any(inputs.get(k) for k in ("image_url", "image_path", "image_urls", "image_paths")):
            return True
        return False

    def _collect_reference_images(self, inputs: dict[str, Any]) -> list[str]:
        """Return a list of remote URLs (or data URIs for local paths) for each reference image."""
        refs: list[str] = []
        primary_url = inputs.get("image_url")
        if primary_url:
            refs.append(primary_url)
        primary_path = inputs.get("image_path")
        if primary_path:
            refs.append(_file_to_data_uri(primary_path))
        for url in inputs.get("image_urls") or []:
            if url:
                refs.append(url)
        for path in inputs.get("image_paths") or []:
            if path:
                refs.append(_file_to_data_uri(path))
        return refs

    def _build_payload(
        self,
        inputs: dict[str, Any],
        *,
        model: str,
        prompt: str,
        aspect_ratio: str,
        response_format: str,
        edit_mode: bool,
        subject_reference: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "response_format": response_format,
            "n": int(inputs.get("n", 1)),
        }
        if inputs.get("seed") is not None:
            payload["seed"] = inputs["seed"]
        # prompt_optimizer and aigc_watermark default to false in the API;
        # only send them when explicitly true to preserve the prompt as written.
        if inputs.get("prompt_optimizer"):
            payload["prompt_optimizer"] = True
        if inputs.get("aigc_watermark"):
            payload["aigc_watermark"] = True
        # image-01 accepts width/height and 21:9 aspect ratio.
        if model == "image-01":
            if inputs.get("width") is not None and inputs.get("height") is not None:
                payload["width"] = int(inputs["width"])
                payload["height"] = int(inputs["height"])
        # image-01-live accepts the style object.
        if model == "image-01-live" and inputs.get("style"):
            payload["style"] = inputs["style"]
        # I2I references.
        if edit_mode and subject_reference:
            payload["subject_reference"] = subject_reference
        return payload

    def _generate(
        self,
        inputs: dict[str, Any],
        *,
        api_key: str,
        model: str,
    ) -> ToolResult:
        import requests

        prompt = inputs["prompt"]
        aspect_ratio = inputs.get("aspect_ratio", "1:1")
        response_format = (
            inputs.get("response_format")
            or os.environ.get(self.DEFAULT_FORMAT_ENV)
            or "url"
        )
        n = int(inputs.get("n", 1))

        edit_mode = self._wants_edit_mode(inputs)
        subject_reference: list[dict[str, Any]] = []
        if edit_mode:
            ref_type = inputs.get("subject_reference_type", "character")
            for ref_url in self._collect_reference_images(inputs):
                subject_reference.append({"type": ref_type, "image_file": ref_url})

        payload = self._build_payload(
            inputs,
            model=model,
            prompt=prompt,
            aspect_ratio=aspect_ratio,
            response_format=response_format,
            edit_mode=edit_mode,
            subject_reference=subject_reference,
        )

        response = requests.post(
            self.PRIMARY_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=180,
        )
        if response.status_code == 0 or response.status_code >= 500:
            response = requests.post(
                self.FALLBACK_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=180,
            )

        if response.status_code >= 400:
            raise RuntimeError(
                f"HTTP {response.status_code}: {self._truncate(response.text)}"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"Non-JSON response from MiniMax: HTTP {response.status_code}"
            ) from exc

        self._raise_for_minimax_error(data)

        image_items = (data.get("data") or {}).get("image_urls") or []
        if not image_items:
            raise RuntimeError("MiniMax returned no image_urls")

        extension = "png"
        if image_items and isinstance(image_items[0], str) and image_items[0].startswith("data:"):
            extension = self._infer_extension_from_data_uri(image_items[0])
        elif image_items and isinstance(image_items[0], str) and image_items[0].startswith("http"):
            extension = self._infer_extension(image_items[0])

        output_paths = self._output_paths(inputs.get("output_path"), len(image_items), extension)

        artifacts: list[str] = []
        outputs: list[str] = []
        for item, output_path in zip(image_items, output_paths):
            output_path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(item, str) and item.startswith("data:"):
                # base64 data URI
                _, _, b64_part = item.partition(",")
                output_path.write_bytes(base64.b64decode(b64_part))
            elif response_format == "base64" or self._looks_like_b64(item):
                output_path.write_bytes(base64.b64decode(item))
            else:
                # URL — fetch the bytes.
                image_response = requests.get(item, timeout=120)
                image_response.raise_for_status()
                output_path.write_bytes(image_response.content)
            artifacts.append(str(output_path))
            outputs.append(str(output_path))

        metadata = data.get("metadata") or {}
        try:
            success_count = int(metadata.get("success_count", len(outputs)))
        except (TypeError, ValueError):
            success_count = len(outputs)
        try:
            failed_count = int(metadata.get("failed_count", 0))
        except (TypeError, ValueError):
            failed_count = 0

        return ToolResult(
            success=True,
            data={
                "provider": self.provider,
                "model": model,
                "prompt": prompt,
                "aspect_ratio": aspect_ratio,
                "generation_mode": "edit" if edit_mode else "generate",
                "subject_references": len(subject_reference),
                "images_generated": len(outputs),
                "success_count": success_count,
                "failed_count": failed_count,
                "task_id": data.get("id"),
                "output": outputs[0],
                "outputs": outputs,
            },
            artifacts=artifacts,
            cost_usd=self.estimate_cost(inputs),
            model=model,
        )

    # ---- Error handling ----

    def _raise_for_minimax_error(self, payload: dict[str, Any]) -> None:
        base_resp = payload.get("base_resp") or {}
        status_code = base_resp.get("status_code")
        if status_code in (None, 0):
            return
        status_msg = base_resp.get("status_msg", "unknown error")
        raise RuntimeError(
            f"MiniMax error {status_code}: {status_msg}{self._diagnostic_hint(status_msg)}"
        )

    @staticmethod
    def _diagnostic_hint(message: str) -> str:
        lowered = str(message).lower()
        if "auth" in lowered or "token" in lowered or "credential" in lowered:
            return " (check MINIMAX_API_KEY is a MiniMax platform key)"
        if "balance" in lowered or "insufficient" in lowered or "quota" in lowered:
            return " (top up MiniMax account or check usage quota)"
        if "prompt" in lowered and "length" in lowered:
            return " (prompt is limited to 1500 characters)"
        if "param" in lowered or "invalid" in lowered:
            return " (check model/aspect_ratio/width/height values)"
        return ""

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        # Word-boundary-aware redaction so short keys don't get clobbered.
        candidates = (
            os.environ.get("MINIMAX_API_KEY"),
            os.environ.get("MINIMAX_IMAGE_API_KEY"),
            os.environ.get("MM_API_KEY"),
        )
        text = str(exc)
        for key in candidates:
            if not key or len(key) < 4:
                continue
            text = re.sub(rf"(?<![A-Za-z0-9]){re.escape(key)}(?![A-Za-z0-9])", "[redacted]", text)
        return text

    @staticmethod
    def _truncate(text: str, *, limit: int = 500) -> str:
        if len(text) <= limit:
            return text
        return text[:limit] + "..."

    @staticmethod
    def _infer_extension(url: str) -> str:
        suffix = Path(urlparse(url).path).suffix.lower()
        if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
            return suffix
        return ".png"

    @staticmethod
    def _infer_extension_from_data_uri(uri: str) -> str:
        head, _, _ = uri.partition(",")
        match = re.match(r"data:image/([a-zA-Z0-9+.-]+);", head)
        if not match:
            return ".png"
        ext = match.group(1).lower()
        return "." + ext

    @staticmethod
    def _looks_like_b64(value: Any) -> bool:
        # Distinguish a URL from a base64 payload. URLs start with http(s)://,
        # data:, or contain a dot+slash path component. Base64 is a long
        # alphanumeric run with possible +/=.
        if not isinstance(value, str) or not value:
            return False
        if value.startswith(("http://", "https://", "data:")):
            return False
        # Long base64 with no slashes
        return len(value) > 256 and "\n" not in value

    @staticmethod
    def _output_paths(output_path: str | None, count: int, extension: str) -> list[Path]:
        if not output_path:
            stem = "minimax_image"
            return [Path(f"{stem}_{idx + 1}{extension}") for idx in range(count)]
        path = Path(output_path)
        suffix = path.suffix or extension
        if count == 1:
            return [path if path.suffix else path.with_suffix(suffix)]
        base = path.with_suffix("") if path.suffix else path
        return [base.parent / f"{base.name}_{idx + 1}{suffix}" for idx in range(count)]