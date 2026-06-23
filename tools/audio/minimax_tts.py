"""MiniMax T2A v2 (synchronous HTTP) text-to-speech provider tool.

Wraps the MiniMax platform ``POST /v1/t2a_v2`` synchronous endpoint.
The MiniMax T2A HTTP endpoint returns a hex-encoded audio blob together
with extra_info metadata (sample rate, audio length, word count, etc.).
This tool decodes the hex payload to a binary audio file and surfaces the
extra_info fields to callers for downstream subtitle / cost use.
"""

from __future__ import annotations

import json
import os
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


class MiniMaxTTS(BaseTool):
    name = "minimax_tts"
    version = "0.1.0"
    tier = ToolTier.VOICE
    capability = "tts"
    provider = "minimax"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    dependencies = ["env:MINIMAX_API_KEY"]
    install_instructions = (
        "Set MINIMAX_API_KEY to your MiniMax platform API key.\n"
        "  Get one at https://platform.minimaxi.com/user-center/basic-information/Interface-key\n"
        "Optional: set MINIMAX_VOICE_ID (default: male-qn-qingse) and "
        "MINIMAX_TTS_MODEL (default: speech-2.8-hd)."
    )
    fallback = "doubao_tts"
    fallback_tools = ["doubao_tts", "elevenlabs_tts", "google_tts", "openai_tts", "piper_tts"]
    agent_skills = ["minimax-tts", "text-to-speech"]

    capabilities = [
        "text_to_speech",
        "voice_selection",
        "emotion_control",
        "multilingual",
        "pause_tags",
        "pronunciation_dict",
        "subtitle_alignment",
    ]
    supports = {
        "voice_cloning": False,
        "multilingual": True,
        "offline": False,
        "native_audio": True,
        "emotion": True,
        "timestamps": True,
        "pause_tags": True,
    }
    best_for = [
        "Mandarin and multilingual narration with rich emotion tags",
        "bilingual explainers needing sound effects such as (laughs) / (sighs)",
        "long-form voiceover with character-level subtitle timestamps",
    ]
    not_good_for = [
        "fully offline production",
        "voice clone matching",
        "real-time interactive speech playback (use the streaming variant)",
    ]

    # ---- Endpoint config ----
    PRIMARY_URL = "https://api.minimaxi.com/v1/t2a_v2"
    FALLBACK_URL = "https://api-bj.minimaxi.com/v1/t2a_v2"

    DEFAULT_MODEL = "speech-2.8-hd"
    DEFAULT_VOICE_ID = "male-qn-qingse"
    DEFAULT_VOICE_ENV = "MINIMAX_VOICE_ID"
    DEFAULT_MODEL_ENV = "MINIMAX_TTS_MODEL"

    # Voices and emotions below are gated to the speech-2.8 family.
    _EMOTION_CAPABLE_MODELS = {"speech-2.8-hd", "speech-2.8-turbo"}
    _ALLOWED_MODELS = (
        "speech-2.8-hd",
        "speech-2.8-turbo",
        "speech-2.6-hd",
        "speech-2.6-turbo",
        "speech-02-hd",
        "speech-02-turbo",
        "speech-01-hd",
        "speech-01-turbo",
    )
    _ALLOWED_EMOTIONS = (
        "happy",
        "sad",
        "angry",
        "fearful",
        "disgusted",
        "surprised",
        "neutral",
    )
    _ALLOWED_FORMATS = ("mp3", "wav", "flac", "pcm")

    input_schema = {
        "type": "object",
        "required": ["text"],
        "properties": {
            "text": {
                "type": "string",
                "description": "Text to synthesize. Up to 10000 characters. "
                "Supports <#x#> pause tags (seconds, 0.01-99.99), inline "
                "pronunciation overrides via pinyin/IPA, and emotion/sound tags.",
            },
            "voice_id": {
                "type": "string",
                "description": "MiniMax voice_id. Defaults to MINIMAX_VOICE_ID or male-qn-qingse.",
            },
            "model": {
                "type": "string",
                "default": "speech-2.8-hd",
                "enum": list(_ALLOWED_MODELS),
                "description": "TTS model. speech-2.8-hd / -turbo support emotion + sound tags.",
            },
            "language": {
                "type": "string",
                "default": "auto",
                "description": "Boost for specific language/dialect. 'auto' lets the model decide.",
            },
            "speed": {
                "type": "number",
                "default": 1.0,
                "minimum": 0.5,
                "maximum": 2.0,
                "description": "Speech speed multiplier. 1.0 = normal.",
            },
            "vol": {
                "type": "number",
                "default": 1.0,
                "minimum": 0.0,
                "maximum": 10.0,
                "description": "Volume. 1.0 = normal.",
            },
            "pitch": {
                "type": "number",
                "default": 0,
                "minimum": -12,
                "maximum": 12,
                "description": "Pitch adjustment in semitones.",
            },
            "emotion": {
                "type": "string",
                "enum": list(_ALLOWED_EMOTIONS),
                "description": "Emotion tag. Only honored by speech-2.8-hd / speech-2.8-turbo.",
            },
            "format": {
                "type": "string",
                "default": "mp3",
                "enum": list(_ALLOWED_FORMATS),
            },
            "sample_rate": {
                "type": "integer",
                "default": 32000,
                "enum": [8000, 16000, 22050, 24000, 32000, 44100, 48000],
            },
            "bitrate": {
                "type": "integer",
                "default": 128000,
                "enum": [32000, 64000, 96000, 128000, 192000, 256000],
                "description": "Output bitrate. Ignored for wav/flac/pcm.",
            },
            "channel": {
                "type": "integer",
                "default": 1,
                "enum": [1, 2],
            },
            "subtitle_enable": {
                "type": "boolean",
                "default": False,
                "description": "Request sentence/word timestamps. Required for subtitle alignment.",
            },
            "subtitle_granularity": {
                "type": "string",
                "default": "sentence",
                "enum": ["sentence", "word", "word_streaming"],
                "description": "Timestamp granularity. word_streaming only works with stream=true.",
            },
            "pronunciation_dict": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Pronunciation overrides, e.g. ['处理/(chu3)(li3)', '危险/dangerous'].",
            },
            "sound_effects": {
                "type": "string",
                "description": "Audio effect preset string passed to audio_setting.sound_effects.",
            },
            "output_path": {"type": "string"},
            "metadata_path": {
                "type": "string",
                "description": "Where to save the full API response JSON. Defaults next to output_path.",
            },
        },
    }

    output_schema = {
        "type": "object",
        "properties": {
            "output": {"type": "string"},
            "metadata_path": {"type": "string"},
            "model": {"type": "string"},
            "voice_id": {"type": "string"},
            "format": {"type": "string"},
            "sample_rate": {"type": "integer"},
            "text_length": {"type": "integer"},
            "audio_length_ms": {"type": ["integer", "null"]},
            "audio_duration_seconds": {"type": ["number", "null"]},
            "audio_size_bytes": {"type": ["integer", "null"]},
            "usage_characters": {"type": ["integer", "null"]},
            "trace_id": {"type": "string"},
            "subtitle_file": {"type": ["string", "null"]},
        },
    }

    artifact_schema = {
        "type": "array",
        "items": {"type": "string"},
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=50, network_required=True
    )
    retry_policy = RetryPolicy(
        max_retries=2,
        backoff_seconds=1.5,
        retryable_errors=["timeout", "rate_limit", "internal error", "server_error"],
    )
    idempotency_key_fields = ["text", "voice_id", "model", "speed", "pitch", "emotion", "format"]
    side_effects = [
        "writes audio file to output_path",
        "writes MiniMax response JSON next to output_path",
        "calls MiniMax T2A v2 HTTP API",
    ]
    user_visible_verification = [
        "Listen to generated audio for naturalness and emotion fidelity",
        "Inspect extra_info.usage_characters against your text length to validate billing",
    ]
    quality_score = 0.9
    latency_p50_seconds = 4.0

    # ---- Lifecycle ----

    def _get_api_key(self) -> str | None:
        return (
            os.environ.get("MINIMAX_API_KEY")
            or os.environ.get("MINIMAX_T2A_API_KEY")
            or os.environ.get("MM_API_KEY")
            or os.environ.get("MM_TTS_API_KEY")
        )

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if self._get_api_key() else ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        # Approximate: MiniMax T2A v2 charges per 1k characters.
        # Use a conservative mid-tier rate until usage data is available.
        chars = len(inputs.get("text", ""))
        return round((chars / 1000.0) * 0.05, 4)

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        api_key = self._get_api_key()
        if not api_key:
            return ToolResult(
                success=False,
                error="No MiniMax API key. " + self.install_instructions,
            )

        text = inputs.get("text", "")
        if not text:
            return ToolResult(success=False, error="text is required")
        if len(text) > 10000:
            return ToolResult(
                success=False,
                error=(
                    f"text length {len(text)} exceeds MiniMax T2A limit of 10000 chars. "
                    "Use the streaming T2A endpoint for longer inputs."
                ),
            )

        voice_id = inputs.get("voice_id") or os.environ.get(self.DEFAULT_VOICE_ENV) or self.DEFAULT_VOICE_ID
        model = inputs.get("model") or os.environ.get(self.DEFAULT_MODEL_ENV) or self.DEFAULT_MODEL
        if model not in self._ALLOWED_MODELS:
            return ToolResult(
                success=False,
                error=f"Unsupported model {model!r}. Allowed: {', '.join(self._ALLOWED_MODELS)}",
            )

        start = time.time()
        try:
            result = self._generate(inputs, api_key=api_key, voice_id=voice_id, model=model)
        except Exception as exc:
            return ToolResult(success=False, error=f"MiniMax TTS failed: {self._safe_error(exc)}")

        result.duration_seconds = round(time.time() - start, 2)
        if not result.cost_usd:
            result.cost_usd = self.estimate_cost(inputs)
        return result

    # ---- Core request flow ----

    def _generate(
        self,
        inputs: dict[str, Any],
        *,
        api_key: str,
        voice_id: str,
        model: str,
    ) -> ToolResult:
        import requests

        text = inputs["text"]
        fmt = inputs.get("format", "mp3")
        sample_rate = int(inputs.get("sample_rate", 32000))
        bitrate = int(inputs.get("bitrate", 128000))
        channel = int(inputs.get("channel", 1))
        speed = float(inputs.get("speed", 1.0))
        vol = float(inputs.get("vol", 1.0))
        pitch = int(inputs.get("pitch", 0))
        emotion = inputs.get("emotion")

        if emotion and model not in self._EMOTION_CAPABLE_MODELS:
            # Drop the parameter rather than fail; the API will silently ignore it,
            # and a missing-feature warning is more useful than a hard error.
            emotion = None

        subtitle_enable = bool(inputs.get("subtitle_enable", False))
        subtitle_granularity = inputs.get("subtitle_granularity", "sentence")

        output_ext = self._extension_for_format(fmt)
        output_path = Path(inputs.get("output_path", f"minimax_tts.{output_ext}"))
        metadata_path = Path(
            inputs.get("metadata_path") or output_path.with_suffix(output_path.suffix + ".json")
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path.parent.mkdir(parents=True, exist_ok=True)

        payload = self._build_payload(
            text=text,
            voice_id=voice_id,
            model=model,
            speed=speed,
            vol=vol,
            pitch=pitch,
            emotion=emotion,
            fmt=fmt,
            sample_rate=sample_rate,
            bitrate=bitrate,
            channel=channel,
            subtitle_enable=subtitle_enable,
            subtitle_granularity=subtitle_granularity,
            language=inputs.get("language"),
            pronunciation_dict=inputs.get("pronunciation_dict"),
            sound_effects=inputs.get("sound_effects"),
        )

        response = requests.post(
            self.PRIMARY_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=120,
        )

        # Fallback endpoint on DNS/network failure — same contract, different host.
        if response.status_code == 0 or response.status_code >= 500:
            response = requests.post(
                self.FALLBACK_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=120,
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

        audio_hex = (data.get("data") or {}).get("audio") or ""
        if not audio_hex:
            raise RuntimeError("MiniMax response missing data.audio")

        try:
            audio_bytes = bytes.fromhex(audio_hex)
        except ValueError as exc:
            raise RuntimeError("MiniMax returned malformed hex audio payload") from exc

        output_path.write_bytes(audio_bytes)

        extra_info = data.get("extra_info") or {}
        usage_characters = extra_info.get("usage_characters")
        cost = self._cost_from_usage(usage_characters) or self.estimate_cost({"text": text})

        subtitle_file = None
        subtitle_payload = (data.get("data") or {}).get("subtitle_file")
        if subtitle_enable and subtitle_payload:
            subtitle_file = str(metadata_path.with_name(output_path.stem + ".srt"))
            Path(subtitle_file).write_text(subtitle_payload, encoding="utf-8")

        response_to_persist = {
            "trace_id": data.get("trace_id"),
            "base_resp": data.get("base_resp"),
            "extra_info": extra_info,
            "request": {
                "model": model,
                "voice_id": voice_id,
                "format": fmt,
                "sample_rate": sample_rate,
                "bitrate": bitrate,
                "channel": channel,
                "speed": speed,
                "vol": vol,
                "pitch": pitch,
                "emotion": emotion,
                "language": inputs.get("language"),
                "subtitle_enable": subtitle_enable,
                "subtitle_granularity": subtitle_granularity,
                "text_length": len(text),
            },
        }
        metadata_path.write_text(
            json.dumps(response_to_persist, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        audio_duration = self._audio_duration(output_path) or (
            (extra_info.get("audio_length", 0) / 1000.0) if extra_info.get("audio_length") else None
        )

        return ToolResult(
            success=True,
            data={
                "provider": self.provider,
                "model": model,
                "voice_id": voice_id,
                "format": fmt,
                "sample_rate": sample_rate,
                "text_length": len(text),
                "audio_length_ms": extra_info.get("audio_length"),
                "audio_duration_seconds": round(audio_duration, 2) if audio_duration else None,
                "audio_size_bytes": extra_info.get("audio_size") or len(audio_bytes),
                "usage_characters": usage_characters,
                "trace_id": data.get("trace_id"),
                "output": str(output_path),
                "metadata_path": str(metadata_path),
                "subtitle_file": subtitle_file,
            },
            artifacts=[str(output_path), str(metadata_path)]
            + ([subtitle_file] if subtitle_file else []),
            cost_usd=cost,
            model=model,
        )

    # ---- Payload construction ----

    def _build_payload(
        self,
        *,
        text: str,
        voice_id: str,
        model: str,
        speed: float,
        vol: float,
        pitch: int,
        emotion: str | None,
        fmt: str,
        sample_rate: int,
        bitrate: int,
        channel: int,
        subtitle_enable: bool,
        subtitle_granularity: str,
        language: str | None,
        pronunciation_dict: list[str] | None,
        sound_effects: str | None,
    ) -> dict[str, Any]:
        voice_setting: dict[str, Any] = {
            "voice_id": voice_id,
            "speed": speed,
            "vol": vol,
            "pitch": pitch,
        }
        if emotion:
            voice_setting["emotion"] = emotion

        audio_setting: dict[str, Any] = {
            "sample_rate": sample_rate,
            "bitrate": bitrate,
            "format": fmt,
            "channel": channel,
        }
        if sound_effects:
            audio_setting["sound_effects"] = sound_effects

        payload: dict[str, Any] = {
            "model": model,
            "text": text,
            "stream": False,
            "voice_setting": voice_setting,
            "audio_setting": audio_setting,
            "subtitle_enable": subtitle_enable,
        }
        if language and language != "auto":
            payload["language_boost"] = language
        if pronunciation_dict:
            payload["pronunciation_dict"] = {"tone": list(pronunciation_dict)}
        if subtitle_enable:
            payload["subtitle_granularity"] = subtitle_granularity

        return payload

    # ---- Error handling ----

    def _raise_for_minimax_error(self, payload: dict[str, Any]) -> None:
        base_resp = payload.get("base_resp") or {}
        status_code = base_resp.get("status_code")
        if status_code in (None, 0):
            return
        status_msg = base_resp.get("status_msg", "unknown error")
        raise RuntimeError(f"MiniMax error {status_code}: {status_msg}{self._diagnostic_hint(status_msg)}")

    @staticmethod
    def _diagnostic_hint(message: str) -> str:
        lowered = str(message).lower()
        if "authentication" in lowered or "auth" in lowered or "token" in lowered:
            return " (check MINIMAX_API_KEY and that it is a MiniMax platform key, not a third-party one)"
        if "balance" in lowered or "insufficient" in lowered or "quota" in lowered:
            return " (top up MiniMax account or check usage quota)"
        if "voice" in lowered and "permission" in lowered:
            return " (check voice_id is authorized on your account)"
        if "param" in lowered or "invalid" in lowered:
            return " (check model/voice_id/format/sample_rate/bitrate values)"
        return ""

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        # Never echo the bearer token in user-visible errors.
        # Use word-boundary regex to avoid replacing the key when it
        # happens to be a substring of an unrelated word ("hex" containing "x").
        import re

        candidates = (
            os.environ.get("MINIMAX_API_KEY"),
            os.environ.get("MINIMAX_T2A_API_KEY"),
            os.environ.get("MM_API_KEY"),
            os.environ.get("MM_TTS_API_KEY"),
        )
        text = str(exc)
        for key in candidates:
            if not key or len(key) < 4:
                continue
            pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(key)}(?![A-Za-z0-9])")
            text = pattern.sub("[redacted]", text)
        return text

    @staticmethod
    def _truncate(text: str, *, limit: int = 500) -> str:
        if len(text) <= limit:
            return text
        return text[:limit] + "..."

    @staticmethod
    def _extension_for_format(fmt: str) -> str:
        return {"mp3": "mp3", "wav": "wav", "flac": "flac", "pcm": "pcm"}.get(fmt, fmt)

    @staticmethod
    def _audio_duration(path: Path) -> float | None:
        try:
            from tools.analysis.audio_probe import probe_duration

            return probe_duration(path)
        except Exception:
            return None

    @staticmethod
    def _cost_from_usage(usage_characters: Any) -> float | None:
        if not isinstance(usage_characters, (int, float)) or usage_characters <= 0:
            return None
        # Per-character billing at the conservative mid-tier rate.
        return round((float(usage_characters) / 1000.0) * 0.05, 4)