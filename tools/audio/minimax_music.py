"""MiniMax Music Generation (synchronous HTTP) provider tool.

Wraps the MiniMax ``POST /v1/music_generation`` synchronous endpoint.
The endpoint returns a hex-encoded audio blob plus ``extra_info``
metadata (sample rate, channel, duration, size). This tool decodes the
hex payload to a binary audio file, mirrors the cover-model inputs
(``audio_url`` / ``audio_base64`` / ``cover_feature_id``), and surfaces
``lyrics_optimizer`` and ``is_instrumental`` switches for text-to-music.

Four models are supported:

- ``music-2.6`` / ``music-2.6-free`` — text-to-music with optional lyrics.
- ``music-cover`` / ``music-cover-free`` — cover re-recording from a
  reference audio (URL or base64) plus optional rewritten lyrics.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

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


class MiniMaxMusic(BaseTool):
    name = "minimax_music"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "music_generation"
    provider = "minimax"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    dependencies = ["env:MINIMAX_API_KEY"]
    install_instructions = (
        "Set MINIMAX_API_KEY to your MiniMax platform API key.\n"
        "  Get one at https://platform.minimaxi.com/user-center/basic-information/Interface-key\n"
        "Optional: set MINIMAX_MUSIC_MODEL (default music-2.6) and "
        "MINIMAX_MUSIC_RESPONSE_FORMAT (url | hex, default hex)."
    )
    fallback = "suno_music"
    fallback_tools = ["suno_music", "music_gen"]
    agent_skills = ["minimax-music", "music"]

    capabilities = [
        "generate_background_music",
        "generate_song",
        "generate_instrumental",
        "generate_cover",
        "lyrics_optimizer",
        "structure_tags",
    ]
    supports = {
        "instrumental": True,
        "vocals": True,
        "custom_lyrics": True,
        "style_control": True,
        "cover_from_reference": True,
        "lyrics_optimizer": True,
    }
    best_for = [
        "Mandarin and multilingual song generation with structured lyrics",
        "instrumental background music with rich style prompts",
        "cover re-recording from a reference audio with rewritten lyrics",
        "lyrics-auto-generation from a single style prompt (lyrics_optimizer)",
    ]
    not_good_for = [
        "fully offline generation",
        "sub-10-second stingers",
        "sound effects (use ElevenLabs SFX instead)",
    ]

    # ---- Endpoint config ----
    PRIMARY_URL = "https://api.minimaxi.com/v1/music_generation"
    FALLBACK_URL = "https://api-bj.minimaxi.com/v1/music_generation"

    DEFAULT_MODEL = "music-2.6"
    DEFAULT_MODEL_ENV = "MINIMAX_MUSIC_MODEL"
    DEFAULT_FORMAT_ENV = "MINIMAX_MUSIC_RESPONSE_FORMAT"

    _TEXT_TO_MUSIC_MODELS = {"music-2.6", "music-2.6-free"}
    _COVER_MODELS = {"music-cover", "music-cover-free"}
    _ALLOWED_MODELS = _TEXT_TO_MUSIC_MODELS | _COVER_MODELS
    _ALLOWED_FORMATS = ("url", "hex")

    # Limits from the public API docs.
    _PROMPT_MAX_TTM = 2000
    _PROMPT_MIN_TTM_INSTRUMENTAL = 1
    _PROMPT_MAX_COVER = 300
    _PROMPT_MIN_COVER = 10
    _LYRICS_MAX_TTM = 3500
    _LYRICS_MIN_COVER = 10
    _LYRICS_MAX_COVER = 1000

    # Recognized structure tags (kept as a soft hint, not enforced).
    _STRUCTURE_TAGS = (
        "[Intro]",
        "[Verse]",
        "[Pre Chorus]",
        "[Chorus]",
        "[Interlude]",
        "[Bridge]",
        "[Outro]",
        "[Post Chorus]",
        "[Transition]",
        "[Break]",
        "[Hook]",
        "[Build Up]",
        "[Inst]",
        "[Solo]",
    )

    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {
                "type": "string",
                "description": (
                    "Music style/mood description. For text-to-music (music-2.6) up to 2000 "
                    "chars; for cover models 10-300 chars describing the target style. "
                    "Examples: '独立民谣,忧郁,内省,渴望,独自漫步,咖啡馆'."
                ),
            },
            "lyrics": {
                "type": "string",
                "description": (
                    "Song lyrics with structure tags ([Verse], [Chorus], etc.). "
                    "Required for vocal text-to-music (1-3500 chars); optional for "
                    "instrumental; optional for cover models (10-1000 chars if provided)."
                ),
            },
            "model": {
                "type": "string",
                "enum": ["music-2.6", "music-2.6-free", "music-cover", "music-cover-free"],
                "default": "music-2.6",
                "description": "music-2.6 / -free = text-to-music; music-cover / -free = cover re-recording.",
            },
            "is_instrumental": {
                "type": "boolean",
                "default": False,
                "description": "True for instrumental (no vocals). Only honored by music-2.6 / music-2.6-free.",
            },
            "lyrics_optimizer": {
                "type": "boolean",
                "default": False,
                "description": (
                    "True to let the model generate lyrics from the prompt when lyrics "
                    "is empty. Only honored by music-2.6 / music-2.6-free."
                ),
            },
            "aigc_watermark": {
                "type": "boolean",
                "default": False,
                "description": "Append an AIGC watermark at the end of the audio.",
            },
            "response_format": {
                "type": "string",
                "enum": list(_ALLOWED_FORMATS),
                "default": "hex",
                "description": "Audio payload format. 'url' URLs expire after 24h; 'hex' is decoded inline.",
            },
            "stream": {
                "type": "boolean",
                "default": False,
                "description": "Enable streaming. When true, response_format must be 'hex'.",
            },
            "format": {
                "type": "string",
                "default": "mp3",
                "enum": ["mp3", "wav", "flac"],
                "description": "Audio file format. Mirrors the audio_setting.format field.",
            },
            "sample_rate": {
                "type": "integer",
                "default": 44100,
                "enum": [8000, 16000, 22050, 24000, 32000, 44100, 48000],
            },
            "bitrate": {
                "type": "integer",
                "default": 256000,
                "enum": [64000, 96000, 128000, 192000, 256000, 320000],
            },
            "channel": {
                "type": "integer",
                "default": 2,
                "enum": [1, 2],
            },
            # ---- Cover inputs (music-cover / music-cover-free only) ----
            "audio_url": {
                "type": "string",
                "description": "Public URL of the reference audio. Cover models only. Mutually exclusive with audio_base64 and cover_feature_id.",
            },
            "audio_path": {
                "type": "string",
                "description": "Local path to the reference audio. Will be base64-encoded.",
            },
            "audio_base64": {
                "type": "string",
                "description": "Pre-encoded base64 reference audio. Cover models only.",
            },
            "cover_feature_id": {
                "type": "string",
                "description": "Two-step cover feature ID from a prior preprocess call. Cover models only.",
            },
            "output_path": {"type": "string"},
            "metadata_path": {
                "type": "string",
                "description": "Where to save the full response JSON. Defaults next to output_path.",
            },
        },
    }

    output_schema = {
        "type": "object",
        "properties": {
            "output": {"type": "string"},
            "metadata_path": {"type": "string"},
            "model": {"type": "string"},
            "prompt": {"type": "string"},
            "format": {"type": "string"},
            "sample_rate": {"type": "integer"},
            "channel": {"type": "integer"},
            "music_duration_ms": {"type": ["integer", "null"]},
            "music_duration_seconds": {"type": ["number", "null"]},
            "music_size_bytes": {"type": ["integer", "null"]},
            "trace_id": {"type": "string"},
            "generation_mode": {"type": "string"},
        },
    }

    artifact_schema = {
        "type": "array",
        "items": {"type": "string"},
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=200, network_required=True
    )
    retry_policy = RetryPolicy(
        max_retries=2,
        backoff_seconds=1.5,
        retryable_errors=["timeout", "rate_limit", "internal error", "server_error"],
    )
    idempotency_key_fields = [
        "prompt",
        "lyrics",
        "model",
        "is_instrumental",
        "lyrics_optimizer",
        "audio_url",
        "cover_feature_id",
        "format",
    ]
    side_effects = [
        "writes audio file to output_path",
        "writes MiniMax response JSON next to output_path",
        "calls MiniMax /v1/music_generation API",
    ]
    user_visible_verification = [
        "Listen to generated music for style and vocal fidelity",
        "Confirm structure tags land at the right sections when lyrics provided",
    ]
    quality_score = 0.88
    latency_p50_seconds = 25.0

    # ---- Lifecycle ----

    def _get_api_key(self) -> str | None:
        return (
            os.environ.get("MINIMAX_API_KEY")
            or os.environ.get("MINIMAX_MUSIC_API_KEY")
            or os.environ.get("MM_API_KEY")
        )

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if self._get_api_key() else ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        # Per-generation cost. MiniMax charges differently for paid vs free tiers;
        # we approximate paid at ~$0.10 and free at ~$0.0 until usage data lands.
        model = inputs.get("model", self.DEFAULT_MODEL)
        if model.endswith("-free"):
            return 0.0
        return 0.10

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

        model = inputs.get("model") or os.environ.get(self.DEFAULT_MODEL_ENV) or self.DEFAULT_MODEL
        if model not in self._ALLOWED_MODELS:
            return ToolResult(
                success=False,
                error=f"Unsupported model {model!r}. Allowed: {', '.join(sorted(self._ALLOWED_MODELS))}",
            )

        validation_error = self._validate_inputs(inputs, model)
        if validation_error:
            return ToolResult(success=False, error=validation_error)

        start = time.time()
        try:
            result = self._generate(inputs, api_key=api_key, model=model)
        except Exception as exc:
            return ToolResult(success=False, error=f"MiniMax music generation failed: {self._safe_error(exc)}")

        result.duration_seconds = round(time.time() - start, 2)
        if not result.cost_usd:
            result.cost_usd = self.estimate_cost({**inputs, "model": model})
        return result

    # ---- Validation ----

    def _validate_inputs(self, inputs: dict[str, Any], model: str) -> str | None:
        prompt_len = len(inputs.get("prompt", ""))
        lyrics = inputs.get("lyrics")
        lyrics_len = len(lyrics) if lyrics else 0
        is_instrumental = bool(inputs.get("is_instrumental", False))

        if model in self._TEXT_TO_MUSIC_MODELS:
            if is_instrumental:
                if prompt_len < self._PROMPT_MIN_TTM_INSTRUMENTAL or prompt_len > self._PROMPT_MAX_TTM:
                    return (
                        f"For instrumental music-2.6: prompt length must be in "
                        f"[{self._PROMPT_MIN_TTM_INSTRUMENTAL}, {self._PROMPT_MAX_TTM}]; got {prompt_len}"
                    )
            else:
                if prompt_len > self._PROMPT_MAX_TTM:
                    return (
                        f"For text-to-music: prompt length must be <= {self._PROMPT_MAX_TTM}; got {prompt_len}"
                    )
                if not lyrics:
                    if not inputs.get("lyrics_optimizer"):
                        return (
                            "lyrics is required for vocal text-to-music. Either provide lyrics, "
                            "or set lyrics_optimizer=True to generate lyrics from prompt."
                        )
                else:
                    if lyrics_len < 1 or lyrics_len > self._LYRICS_MAX_TTM:
                        return (
                            f"lyrics length must be in [1, {self._LYRICS_MAX_TTM}]; got {lyrics_len}"
                        )
            if inputs.get("audio_url") or inputs.get("audio_path") or inputs.get("audio_base64") or inputs.get("cover_feature_id"):
                return "audio_url / audio_base64 / cover_feature_id are only valid for music-cover / music-cover-free"
        else:  # cover models
            if prompt_len < self._PROMPT_MIN_COVER or prompt_len > self._PROMPT_MAX_COVER:
                return (
                    f"For cover models: prompt length must be in "
                    f"[{self._PROMPT_MIN_COVER}, {self._PROMPT_MAX_COVER}]; got {prompt_len}"
                )
            ref_inputs = [
                inputs.get("audio_url"),
                inputs.get("audio_path"),
                inputs.get("audio_base64"),
                inputs.get("cover_feature_id"),
            ]
            present = sum(1 for v in ref_inputs if v)
            if present == 0:
                return (
                    "Cover models require one of audio_url / audio_path / "
                    "audio_base64 / cover_feature_id"
                )
            if present > 1:
                return (
                    "audio_url, audio_base64, and cover_feature_id are mutually exclusive. "
                    "Provide exactly one."
                )
            if inputs.get("is_instrumental"):
                return "is_instrumental is only honored by music-2.6 / music-2.6-free"
            if inputs.get("lyrics_optimizer"):
                return "lyrics_optimizer is only honored by music-2.6 / music-2.6-free"
            if lyrics is not None and (lyrics_len < self._LYRICS_MIN_COVER or lyrics_len > self._LYRICS_MAX_COVER):
                return (
                    f"For cover models with lyrics: length must be in "
                    f"[{self._LYRICS_MIN_COVER}, {self._LYRICS_MAX_COVER}]; got {lyrics_len}"
                )

        # Cross-field
        if inputs.get("stream") and inputs.get("response_format", "hex") == "url":
            return "stream=true requires response_format=hex (urls are only valid for non-streaming responses)"

        return None

    # ---- Core request flow ----

    def _generate(
        self,
        inputs: dict[str, Any],
        *,
        api_key: str,
        model: str,
    ) -> ToolResult:
        import requests

        prompt = inputs["prompt"]
        lyrics = inputs.get("lyrics")
        response_format = (
            inputs.get("response_format")
            or os.environ.get(self.DEFAULT_FORMAT_ENV)
            or "hex"
        )
        fmt = inputs.get("format", "mp3")
        sample_rate = int(inputs.get("sample_rate", 44100))
        bitrate = int(inputs.get("bitrate", 256000))
        channel = int(inputs.get("channel", 2))
        stream = bool(inputs.get("stream", False))
        is_instrumental = bool(inputs.get("is_instrumental", False))
        lyrics_optimizer = bool(inputs.get("lyrics_optimizer", False))
        aigc_watermark = bool(inputs.get("aigc_watermark", False))

        output_ext = fmt
        output_path = Path(inputs.get("output_path", f"minimax_music.{output_ext}"))
        metadata_path = Path(
            inputs.get("metadata_path") or output_path.with_suffix(output_path.suffix + ".json")
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path.parent.mkdir(parents=True, exist_ok=True)

        payload = self._build_payload(
            inputs=inputs,
            model=model,
            prompt=prompt,
            lyrics=lyrics,
            response_format=response_format,
            stream=stream,
            is_instrumental=is_instrumental,
            lyrics_optimizer=lyrics_optimizer,
            aigc_watermark=aigc_watermark,
            fmt=fmt,
            sample_rate=sample_rate,
            bitrate=bitrate,
            channel=channel,
        )

        response = requests.post(
            self.PRIMARY_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=240,
        )

        if response.status_code == 0 or response.status_code >= 500:
            response = requests.post(
                self.FALLBACK_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=240,
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

        audio_field = (data.get("data") or {}).get("audio")
        if not audio_field:
            raise RuntimeError("MiniMax response missing data.audio")

        if response_format == "url":
            audio_response = requests.get(audio_field, timeout=180)
            audio_response.raise_for_status()
            output_path.write_bytes(audio_response.content)
            audio_bytes = audio_response.content
        else:
            try:
                audio_bytes = bytes.fromhex(audio_field)
            except ValueError as exc:
                raise RuntimeError("MiniMax returned malformed hex audio payload") from exc
            output_path.write_bytes(audio_bytes)

        extra_info = data.get("extra_info") or {}
        music_duration_ms = extra_info.get("music_duration")

        response_to_persist = {
            "trace_id": data.get("trace_id"),
            "base_resp": data.get("base_resp"),
            "extra_info": extra_info,
            "analysis_info": data.get("analysis_info"),
            "request": {
                "model": model,
                "prompt_length": len(prompt),
                "lyrics_length": len(lyrics) if lyrics else 0,
                "format": fmt,
                "sample_rate": sample_rate,
                "bitrate": bitrate,
                "channel": channel,
                "response_format": response_format,
                "stream": stream,
                "is_instrumental": is_instrumental if model in self._TEXT_TO_MUSIC_MODELS else None,
                "lyrics_optimizer": lyrics_optimizer if model in self._TEXT_TO_MUSIC_MODELS else None,
                "generation_mode": self._generation_mode(model, inputs),
            },
        }
        metadata_path.write_text(
            json.dumps(response_to_persist, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        return ToolResult(
            success=True,
            data={
                "provider": self.provider,
                "model": model,
                "prompt": prompt,
                "generation_mode": self._generation_mode(model, inputs),
                "format": fmt,
                "sample_rate": sample_rate,
                "channel": channel,
                "bitrate": bitrate,
                "music_duration_ms": music_duration_ms,
                "music_duration_seconds": round(music_duration_ms / 1000.0, 2) if music_duration_ms else None,
                "music_size_bytes": extra_info.get("music_size") or len(audio_bytes),
                "trace_id": data.get("trace_id"),
                "output": str(output_path),
                "metadata_path": str(metadata_path),
            },
            artifacts=[str(output_path), str(metadata_path)],
            cost_usd=self.estimate_cost({**inputs, "model": model}),
            model=model,
        )

    def _generation_mode(self, model: str, inputs: dict[str, Any]) -> str:
        if model in self._COVER_MODELS:
            if inputs.get("cover_feature_id"):
                return "cover_two_step"
            return "cover"
        if inputs.get("is_instrumental"):
            return "instrumental"
        return "song"

    def _build_payload(
        self,
        *,
        inputs: dict[str, Any],
        model: str,
        prompt: str,
        lyrics: str | None,
        response_format: str,
        stream: bool,
        is_instrumental: bool,
        lyrics_optimizer: bool,
        aigc_watermark: bool,
        fmt: str,
        sample_rate: int,
        bitrate: int,
        channel: int,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "audio_setting": {
                "sample_rate": sample_rate,
                "bitrate": bitrate,
                "format": fmt,
            },
        }
        # channel is a documented field; include when set.
        if channel:
            payload["audio_setting"]["channel"] = channel

        if lyrics:
            payload["lyrics"] = lyrics

        # Music-2.6 / -free knobs.
        if model in self._TEXT_TO_MUSIC_MODELS:
            payload["is_instrumental"] = is_instrumental
            payload["lyrics_optimizer"] = lyrics_optimizer
        if aigc_watermark and not stream:
            payload["aigc_watermark"] = True
        if stream:
            payload["stream"] = True
            # Server forces response_format=hex when streaming.
            payload["response_format"] = "hex"
        else:
            payload["response_format"] = response_format

        # Cover model: exactly one of audio_url / audio_base64 / cover_feature_id.
        if model in self._COVER_MODELS:
            if cover_id := inputs.get("cover_feature_id"):
                payload["cover_feature_id"] = cover_id
            elif audio_b64 := inputs.get("audio_base64"):
                payload["audio_base64"] = audio_b64
            else:
                ref = inputs.get("audio_url")
                if not ref and inputs.get("audio_path"):
                    ref = self._encode_local_audio(inputs["audio_path"])
                if ref:
                    payload["audio_url"] = ref

        return payload

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
            return " (top up MiniMax account or check RPM limits for the free tier)"
        if "lyrics" in lowered and "length" in lowered:
            return " (check lyrics length: 1-3500 for music-2.6 vocals, 10-1000 for cover)"
        if "prompt" in lowered and ("length" in lowered or "long" in lowered):
            return " (check prompt length: up to 2000 for music-2.6, 10-300 for cover)"
        if "reference" in lowered or "cover" in lowered and "audio" in lowered:
            return " (check reference audio: 6s-6min, <=50MB, common audio formats)"
        if "param" in lowered or "invalid" in lowered:
            return " (check model / lyrics / audio_setting values)"
        return ""

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        candidates = (
            os.environ.get("MINIMAX_API_KEY"),
            os.environ.get("MINIMAX_MUSIC_API_KEY"),
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
    def _encode_local_audio(path_str: str) -> str:
        import base64
        import mimetypes

        path = Path(path_str)
        if not path.exists():
            raise FileNotFoundError(f"Reference audio not found: {path}")
        mime_type, _ = mimetypes.guess_type(path.name)
        if not mime_type:
            mime_type = "application/octet-stream"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"