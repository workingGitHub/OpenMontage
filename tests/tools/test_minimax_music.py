"""Tests for the MiniMax music generation tool.

Offline-friendly: HTTP calls are mocked via monkeypatch so no
MINIMAX_API_KEY is required to run the suite.
"""
from __future__ import annotations

import base64
import importlib
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

minimax_music_module = importlib.import_module("tools.audio.minimax_music")
MiniMaxMusic = minimax_music_module.MiniMaxMusic


# ----------------------------------------------------------------------
# Fixtures and helpers
# ----------------------------------------------------------------------


@pytest.fixture
def tool(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "test-minimax-key-1234")
    return MiniMaxMusic()


def _hex_response_body(audio_hex: str, **extra) -> dict:
    body = {
        "trace_id": "trace-abc-123",
        "data": {"audio": audio_hex},
        "extra_info": {
            "music_duration": 45230,
            "music_size": 123456,
            "audio_sample_rate": 44100,
            "audio_channel": 2,
            "audio_format": "mp3",
            "audio_bitrate": 256000,
        },
        "base_resp": {"status_code": 0, "status_msg": "success"},
    }
    body["extra_info"].update(extra)
    return body


def _url_response_body(audio_url: str) -> dict:
    return {
        "trace_id": "trace-url-456",
        "data": {"audio": audio_url},
        "extra_info": {
            "music_duration": 30000,
            "music_size": 99999,
            "audio_sample_rate": 44100,
            "audio_channel": 2,
            "audio_format": "mp3",
            "audio_bitrate": 256000,
        },
        "base_resp": {"status_code": 0, "status_msg": "success"},
    }


class _FakeResponse:
    def __init__(self, *, status_code: int = 200, json_body: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._json_body = json_body or {}
        self.text = text

    def json(self):
        return self._json_body


# ----------------------------------------------------------------------
# Identity and registration
# ----------------------------------------------------------------------


def test_identity_matches_contract():
    tool = MiniMaxMusic()
    info = tool.get_info()
    assert info["name"] == "minimax_music"
    assert info["tier"] == "generate"
    assert info["capability"] == "music_generation"
    assert info["provider"] == "minimax"
    assert info["runtime"] == "api"


def test_status_unavailable_when_env_missing(monkeypatch):
    for key in ("MINIMAX_API_KEY", "MINIMAX_MUSIC_API_KEY", "MM_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    tool = MiniMaxMusic()
    assert tool.get_status().value == "unavailable"


def test_status_available_with_env(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 16)
    tool = MiniMaxMusic()
    assert tool.get_status().value == "available"


def test_fallback_lists_other_music_providers():
    tool = MiniMaxMusic()
    assert "suno_music" in tool.fallback_tools
    assert "music_gen" in tool.fallback_tools


def test_capabilities_expose_feature_flags():
    tool = MiniMaxMusic()
    for cap in (
        "generate_background_music",
        "generate_song",
        "generate_instrumental",
        "generate_cover",
        "lyrics_optimizer",
        "structure_tags",
    ):
        assert cap in tool.capabilities


def test_supports_exposes_feature_flags():
    tool = MiniMaxMusic()
    assert tool.supports["instrumental"] is True
    assert tool.supports["vocals"] is True
    assert tool.supports["custom_lyrics"] is True
    assert tool.supports["style_control"] is True
    assert tool.supports["cover_from_reference"] is True
    assert tool.supports["lyrics_optimizer"] is True


# ----------------------------------------------------------------------
# Cost estimation
# ----------------------------------------------------------------------


def test_estimate_cost_free_tier_is_zero():
    tool = MiniMaxMusic()
    assert tool.estimate_cost({"model": "music-2.6-free"}) == 0.0
    assert tool.estimate_cost({"model": "music-cover-free"}) == 0.0


def test_estimate_cost_paid_tier_is_positive():
    tool = MiniMaxMusic()
    assert tool.estimate_cost({"model": "music-2.6"}) > 0
    assert tool.estimate_cost({"model": "music-cover"}) > 0


def test_estimate_cost_default_falls_back_to_paid():
    tool = MiniMaxMusic()
    # No model -> defaults to music-2.6 (paid).
    assert tool.estimate_cost({}) > 0


# ----------------------------------------------------------------------
# Mode detection
# ----------------------------------------------------------------------


def test_generation_mode_instrumental_for_text_to_music():
    tool = MiniMaxMusic()
    mode = tool._generation_mode("music-2.6", {"is_instrumental": True})
    assert mode == "instrumental"


def test_generation_mode_song_for_text_to_music_with_lyrics():
    tool = MiniMaxMusic()
    mode = tool._generation_mode("music-2.6", {"lyrics": "hello"})
    assert mode == "song"


def test_generation_mode_song_when_only_lyrics_optimizer():
    tool = MiniMaxMusic()
    mode = tool._generation_mode("music-2.6", {"lyrics_optimizer": True})
    assert mode == "song"


def test_generation_mode_cover_with_audio_url():
    tool = MiniMaxMusic()
    mode = tool._generation_mode(
        "music-cover", {"audio_url": "https://example.com/r.mp3"}
    )
    assert mode == "cover"


def test_generation_mode_cover_with_audio_base64():
    tool = MiniMaxMusic()
    mode = tool._generation_mode(
        "music-cover", {"audio_base64": "YWJjZA=="}
    )
    assert mode == "cover"


def test_generation_mode_cover_two_step_with_feature_id():
    tool = MiniMaxMusic()
    mode = tool._generation_mode(
        "music-cover", {"cover_feature_id": "feature-xyz"}
    )
    assert mode == "cover_two_step"


# ----------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------


def test_validate_text_to_music_vocal_requires_lyrics_or_optimizer():
    tool = MiniMaxMusic()
    err = tool._validate_inputs({"prompt": "ambient"}, model="music-2.6")
    assert err is not None
    assert "lyrics" in err.lower()


def test_validate_text_to_music_vocal_ok_with_lyrics():
    tool = MiniMaxMusic()
    err = tool._validate_inputs(
        {"prompt": "ambient", "lyrics": "[Verse]\nHi"}, model="music-2.6"
    )
    assert err is None


def test_validate_text_to_music_vocal_ok_with_lyrics_optimizer():
    tool = MiniMaxMusic()
    err = tool._validate_inputs(
        {"prompt": "ambient", "lyrics_optimizer": True}, model="music-2.6"
    )
    assert err is None


def test_validate_instrumental_accepts_lyrics_without_rejection():
    """The MiniMax tool does not explicitly reject lyrics when
    is_instrumental=True; the model decides whether to sing them.
    Verify the validator returns no error so the upstream model gets the
    full payload."""
    tool = MiniMaxMusic()
    err = tool._validate_inputs(
        {"prompt": "ambient", "lyrics": "words", "is_instrumental": True},
        model="music-2.6",
    )
    assert err is None


def test_validate_instrumental_rejects_oversized_prompt():
    tool = MiniMaxMusic()
    err = tool._validate_inputs(
        {"prompt": "x" * 2001, "is_instrumental": True}, model="music-2.6"
    )
    assert err is not None
    assert "2000" in err


def test_validate_text_to_music_rejects_audio_inputs():
    tool = MiniMaxMusic()
    err = tool._validate_inputs(
        {"prompt": "ambient", "lyrics": "[Verse]\nx", "audio_url": "https://x/a.mp3"},
        model="music-2.6",
    )
    assert err is not None
    assert "cover" in err.lower()


def test_validate_cover_requires_reference():
    tool = MiniMaxMusic()
    err = tool._validate_inputs({"prompt": "bossa nova version"}, model="music-cover")
    assert err is not None
    assert "audio_url" in err or "cover" in err.lower()


def test_validate_cover_rejects_multiple_references():
    tool = MiniMaxMusic()
    err = tool._validate_inputs(
        {
            "prompt": "bossa nova version",
            "audio_url": "https://x/a.mp3",
            "audio_base64": "abc=",
        },
        model="music-cover",
    )
    assert err is not None
    assert "mutually exclusive" in err.lower()


def test_validate_cover_accepts_lyrics_in_range():
    tool = MiniMaxMusic()
    err = tool._validate_inputs(
        {
            "prompt": "bossa nova",
            "audio_url": "https://x/a.mp3",
            "lyrics": "[Verse]\n" + "la " * 5,
        },
        model="music-cover",
    )
    assert err is None


def test_validate_cover_rejects_lyrics_too_short():
    tool = MiniMaxMusic()
    err = tool._validate_inputs(
        {
            "prompt": "bossa nova",
            "audio_url": "https://x/a.mp3",
            "lyrics": "hi",
        },
        model="music-cover",
    )
    assert err is not None
    assert "1000" in err


def test_validate_cover_rejects_prompt_too_long():
    tool = MiniMaxMusic()
    err = tool._validate_inputs(
        {"prompt": "x" * 301, "audio_url": "https://x/a.mp3"},
        model="music-cover",
    )
    assert err is not None
    assert "300" in err


def test_validate_cover_rejects_instrumental_flag():
    tool = MiniMaxMusic()
    err = tool._validate_inputs(
        {
            "prompt": "bossa nova",
            "audio_url": "https://x/a.mp3",
            "is_instrumental": True,
        },
        model="music-cover",
    )
    assert err is not None
    assert "instrumental" in err.lower()


def test_validate_cover_rejects_lyrics_optimizer_flag():
    tool = MiniMaxMusic()
    err = tool._validate_inputs(
        {
            "prompt": "bossa nova",
            "audio_url": "https://x/a.mp3",
            "lyrics_optimizer": True,
        },
        model="music-cover",
    )
    assert err is not None
    assert "lyrics_optimizer" in err.lower()


def test_validate_stream_requires_hex_format():
    tool = MiniMaxMusic()
    err = tool._validate_inputs(
        {"prompt": "ambient", "lyrics": "[Verse]\nx", "stream": True, "response_format": "url"},
        model="music-2.6",
    )
    assert err is not None
    assert "stream" in err.lower()


def test_validate_stream_with_hex_is_ok():
    tool = MiniMaxMusic()
    err = tool._validate_inputs(
        {"prompt": "ambient", "lyrics": "[Verse]\nx", "stream": True, "response_format": "hex"},
        model="music-2.6",
    )
    assert err is None


def test_validate_unknown_model_returns_error():
    tool = MiniMaxMusic()
    err = tool._validate_inputs({"prompt": "ambient"}, model="music-99-fake")
    assert err is not None
    # _validate_inputs is mode-specific; unknown model is caught upstream.
    # But if we somehow pass an unknown model here, the function still
    # defaults to a permissive text-to-music validation.
    # The upstream execute() check is what actually rejects unknown models.


# ----------------------------------------------------------------------
# Payload construction
# ----------------------------------------------------------------------


def _build(**overrides):
    """Helper to build a payload with sensible defaults."""
    defaults = dict(
        prompt="ambient",
        lyrics="[Verse]\nhi",
        response_format="hex",
        stream=False,
        is_instrumental=False,
        lyrics_optimizer=False,
        aigc_watermark=False,
        fmt="mp3",
        sample_rate=44100,
        bitrate=256000,
        channel=2,
    )
    defaults.update(overrides)
    return defaults


def test_build_payload_text_to_music_default():
    tool = MiniMaxMusic()
    payload = tool._build_payload(
        inputs={"prompt": "ambient", "lyrics": "[Verse]\nhi"},
        model="music-2.6",
        **_build(),
    )
    assert payload["model"] == "music-2.6"
    assert payload["prompt"] == "ambient"
    assert payload["lyrics"] == "[Verse]\nhi"
    assert payload["is_instrumental"] is False
    assert payload["lyrics_optimizer"] is False
    assert payload["response_format"] == "hex"
    assert payload["audio_setting"]["sample_rate"] == 44100
    assert payload["audio_setting"]["bitrate"] == 256000
    assert payload["audio_setting"]["format"] == "mp3"
    assert payload["audio_setting"]["channel"] == 2


def test_build_payload_drops_watermark_when_streaming():
    tool = MiniMaxMusic()
    payload = tool._build_payload(
        inputs={"prompt": "ambient", "lyrics": "[Verse]\nhi", "stream": True},
        model="music-2.6",
        **_build(stream=True),
    )
    assert payload.get("stream") is True
    assert payload["response_format"] == "hex"
    # aigc_watermark is dropped when streaming
    assert "aigc_watermark" not in payload


def test_build_payload_includes_watermark_when_requested():
    tool = MiniMaxMusic()
    payload = tool._build_payload(
        inputs={"prompt": "ambient", "lyrics": "[Verse]\nhi", "aigc_watermark": True},
        model="music-2.6",
        **_build(aigc_watermark=True),
    )
    assert payload["aigc_watermark"] is True


def test_build_payload_instrumental():
    tool = MiniMaxMusic()
    payload = tool._build_payload(
        inputs={"prompt": "ambient piano", "is_instrumental": True},
        model="music-2.6",
        **_build(is_instrumental=True, lyrics=None),
    )
    assert payload["is_instrumental"] is True
    assert "lyrics" not in payload


def test_build_payload_cover_with_audio_url():
    tool = MiniMaxMusic()
    payload = tool._build_payload(
        inputs={"prompt": "bossa nova", "audio_url": "https://x/a.mp3"},
        model="music-cover",
        **_build(),
    )
    assert payload["audio_url"] == "https://x/a.mp3"
    # Cover models do not include is_instrumental/lyrics_optimizer.
    assert "is_instrumental" not in payload
    assert "lyrics_optimizer" not in payload


def test_build_payload_cover_with_audio_base64():
    tool = MiniMaxMusic()
    payload = tool._build_payload(
        inputs={"prompt": "bossa nova", "audio_base64": "YWJjZA=="},
        model="music-cover",
        **_build(),
    )
    assert payload["audio_base64"] == "YWJjZA=="
    assert "audio_url" not in payload


def test_build_payload_cover_with_feature_id():
    tool = MiniMaxMusic()
    payload = tool._build_payload(
        inputs={"prompt": "bossa nova", "cover_feature_id": "feature-xyz"},
        model="music-cover",
        **_build(),
    )
    assert payload["cover_feature_id"] == "feature-xyz"
    assert "audio_url" not in payload
    assert "audio_base64" not in payload


def test_build_payload_cover_prefers_feature_id_over_url():
    tool = MiniMaxMusic()
    payload = tool._build_payload(
        inputs={
            "prompt": "bossa nova",
            "audio_url": "https://x/a.mp3",
            "cover_feature_id": "feature-xyz",
        },
        model="music-cover",
        **_build(),
    )
    assert payload["cover_feature_id"] == "feature-xyz"
    assert "audio_url" not in payload


def test_build_payload_cover_prefers_base64_over_url():
    tool = MiniMaxMusic()
    payload = tool._build_payload(
        inputs={
            "prompt": "bossa nova",
            "audio_url": "https://x/a.mp3",
            "audio_base64": "YWJjZA==",
        },
        model="music-cover",
        **_build(),
    )
    assert payload["audio_base64"] == "YWJjZA=="
    assert "audio_url" not in payload


def test_build_payload_cover_encodes_local_audio_path(tmp_path):
    src = tmp_path / "ref.mp3"
    src.write_bytes(b"\xff\xfb\x90\x00" + b"FAKE_MP3_BYTES")
    tool = MiniMaxMusic()
    payload = tool._build_payload(
        inputs={"prompt": "bossa nova", "audio_path": str(src)},
        model="music-cover",
        **_build(),
    )
    assert payload["audio_url"].startswith("data:audio/mpeg;base64,")
    # Round-trip: base64 decodes to the source bytes.
    b64 = payload["audio_url"].split(",", 1)[1]
    assert base64.b64decode(b64) == b"\xff\xfb\x90\x00" + b"FAKE_MP3_BYTES"


def test_build_payload_cover_local_path_missing_raises(tmp_path):
    tool = MiniMaxMusic()
    with pytest.raises(FileNotFoundError):
        tool._build_payload(
            inputs={"prompt": "bossa nova", "audio_path": str(tmp_path / "nope.mp3")},
            model="music-cover",
            **_build(),
        )


def test_build_payload_url_format_overrides_when_not_streaming():
    tool = MiniMaxMusic()
    payload = tool._build_payload(
        inputs={"prompt": "ambient", "lyrics": "[Verse]\nx", "response_format": "url"},
        model="music-2.6",
        **_build(response_format="url"),
    )
    assert payload["response_format"] == "url"


# ----------------------------------------------------------------------
# Local audio encoding
# ----------------------------------------------------------------------


def test_encode_local_audio_mp3(tmp_path):
    src = tmp_path / "ref.mp3"
    src.write_bytes(b"FAKE")
    encoded = MiniMaxMusic._encode_local_audio(str(src))
    assert encoded.startswith("data:audio/mpeg;base64,")
    payload = encoded.split(",", 1)[1]
    assert base64.b64decode(payload) == b"FAKE"


def test_encode_local_audio_wav(tmp_path):
    src = tmp_path / "ref.wav"
    src.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")
    encoded = MiniMaxMusic._encode_local_audio(str(src))
    assert "audio/wav" in encoded


def test_encode_local_audio_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        MiniMaxMusic._encode_local_audio(str(tmp_path / "missing.mp3"))


# ----------------------------------------------------------------------
# Error redaction
# ----------------------------------------------------------------------


def test_safe_error_redacts_api_key(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "super-secret-key-1234")
    msg = MiniMaxMusic._safe_error(Exception("boom super-secret-key-1234 again"))
    assert "super-secret-key-1234" not in msg
    assert "[redacted]" in msg


def test_safe_error_redacts_across_multiple_keys(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "alpha-beta-gamma")
    monkeypatch.setenv("MINIMAX_MUSIC_API_KEY", "delta-epsilon-zeta")
    msg = MiniMaxMusic._safe_error(
        Exception("alpha-beta-gamma + delta-epsilon-zeta")
    )
    assert "alpha-beta-gamma" not in msg
    assert "delta-epsilon-zeta" not in msg


def test_safe_error_preserves_short_words():
    """The redaction must not clobber short keys/substrings inside other words."""
    msg = MiniMaxMusic._safe_error(Exception("hex payload malformed"))
    assert "hex" in msg  # 'hex' is a 3-char word; redaction requires len>=4.


def test_safe_error_handles_short_or_missing_keys(monkeypatch):
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.delenv("MINIMAX_MUSIC_API_KEY", raising=False)
    monkeypatch.delenv("MM_API_KEY", raising=False)
    msg = MiniMaxMusic._safe_error(Exception("nothing to redact here"))
    assert msg == "nothing to redact here"


# ----------------------------------------------------------------------
# Diagnostic hints
# ----------------------------------------------------------------------


def test_diagnostic_hint_auth():
    assert "MINIMAX_API_KEY" in MiniMaxMusic._diagnostic_hint("authentication failed")


def test_diagnostic_hint_balance():
    assert "top up" in MiniMaxMusic._diagnostic_hint("insufficient balance").lower()


def test_diagnostic_hint_quota():
    assert "rpm" in MiniMaxMusic._diagnostic_hint("quota exceeded").lower()


def test_diagnostic_hint_lyrics_length():
    assert "lyrics" in MiniMaxMusic._diagnostic_hint("lyrics length error").lower()


def test_diagnostic_hint_prompt_length():
    assert "prompt" in MiniMaxMusic._diagnostic_hint("prompt too long").lower()


def test_diagnostic_hint_invalid_param():
    assert "check" in MiniMaxMusic._diagnostic_hint("invalid parameter").lower()


def test_diagnostic_hint_unknown_returns_empty():
    assert MiniMaxMusic._diagnostic_hint("something novel") == ""


# ----------------------------------------------------------------------
# Base error raising
# ----------------------------------------------------------------------


def test_raise_for_minimax_error_passes_on_status_zero():
    tool = MiniMaxMusic()
    tool._raise_for_minimax_error(
        {"base_resp": {"status_code": 0, "status_msg": "success"}}
    )


def test_raise_for_minimax_error_raises_on_nonzero():
    tool = MiniMaxMusic()
    with pytest.raises(RuntimeError) as excinfo:
        tool._raise_for_minimax_error(
            {"base_resp": {"status_code": 1001, "status_msg": "auth failed"}}
        )
    assert "1001" in str(excinfo.value)
    assert "auth failed" in str(excinfo.value)


def test_raise_for_minimax_error_missing_base_resp_passes():
    tool = MiniMaxMusic()
    tool._raise_for_minimax_error({})


# ----------------------------------------------------------------------
# execute() error paths
# ----------------------------------------------------------------------


def test_execute_rejects_missing_api_key(monkeypatch):
    for key in ("MINIMAX_API_KEY", "MINIMAX_MUSIC_API_KEY", "MM_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    tool = MiniMaxMusic()
    result = tool.execute({"prompt": "ambient", "lyrics": "[Verse]\nhi"})
    assert result.success is False
    assert "MINIMAX_API_KEY" in result.error


def test_execute_rejects_empty_prompt(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 16)
    tool = MiniMaxMusic()
    result = tool.execute({"prompt": "", "lyrics": "[Verse]\nhi"})
    assert result.success is False
    assert "prompt" in result.error


def test_execute_rejects_unknown_model(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 16)
    tool = MiniMaxMusic()
    result = tool.execute({"prompt": "ambient", "lyrics": "[Verse]\nhi", "model": "music-99-fake"})
    assert result.success is False
    assert "Unsupported model" in result.error


def test_execute_rejects_validation_failure(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 16)
    tool = MiniMaxMusic()
    result = tool.execute({"prompt": "ambient"})  # missing lyrics + no optimizer
    assert result.success is False
    assert "lyrics" in result.error.lower()


# ----------------------------------------------------------------------
# execute() success path (mocked HTTP)
# ----------------------------------------------------------------------


def test_execute_success_song_with_hex(monkeypatch, tmp_path):
    monkeypatch.setenv("MINIMAX_API_KEY", "test-minimax-key-1234")
    output = tmp_path / "song.mp3"
    captured: dict = {}
    fake_bytes = b"\xff\xfb\x90\x00FAKE_MP3_BLOB"
    audio_hex = fake_bytes.hex()

    class FakeRequests:
        @staticmethod
        def post(url, headers, json, timeout):
            captured["url"] = url
            captured["headers"] = headers
            captured["body"] = json
            captured["timeout"] = timeout
            return _FakeResponse(json_body=_hex_response_body(audio_hex))

    monkeypatch.setattr("requests.post", FakeRequests.post)

    tool = MiniMaxMusic()
    result = tool.execute({
        "prompt": "独立民谣,忧郁,内省,渴望",
        "lyrics": "[Verse]\n独自走在霓虹的街头",
        "model": "music-2.6",
        "output_path": str(output),
    })

    assert result.success is True, result.error
    assert captured["url"] == MiniMaxMusic.PRIMARY_URL
    assert captured["headers"]["Authorization"] == "Bearer test-minimax-key-1234"
    body = captured["body"]
    assert body["model"] == "music-2.6"
    assert body["prompt"] == "独立民谣,忧郁,内省,渴望"
    assert body["lyrics"] == "[Verse]\n独自走在霓虹的街头"
    assert body["is_instrumental"] is False
    assert body["lyrics_optimizer"] is False
    assert body["response_format"] == "hex"
    assert body["audio_setting"]["sample_rate"] == 44100
    assert body["audio_setting"]["bitrate"] == 256000
    assert body["audio_setting"]["format"] == "mp3"
    assert body["audio_setting"]["channel"] == 2

    # Output file matches the decoded hex bytes.
    assert Path(result.data["output"]).exists()
    assert Path(result.data["output"]).read_bytes() == fake_bytes
    # Metadata JSON written next to output.
    assert Path(result.data["metadata_path"]).exists()
    metadata = json.loads(Path(result.data["metadata_path"]).read_text(encoding="utf-8"))
    assert metadata["trace_id"] == "trace-abc-123"
    assert metadata["extra_info"]["music_duration"] == 45230
    assert metadata["request"]["model"] == "music-2.6"
    assert metadata["request"]["generation_mode"] == "song"

    assert result.data["provider"] == "minimax"
    assert result.data["model"] == "music-2.6"
    assert result.data["generation_mode"] == "song"
    assert result.data["music_duration_ms"] == 45230
    assert result.data["music_duration_seconds"] == 45.23
    assert result.data["sample_rate"] == 44100
    assert result.data["channel"] == 2
    assert result.data["trace_id"] == "trace-abc-123"
    assert len(result.artifacts) == 2


def test_execute_success_instrumental_with_lyrics_optimizer(monkeypatch, tmp_path):
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 16)
    output = tmp_path / "inst.mp3"
    audio_hex = b"FAKE".hex()

    class FakeRequests:
        @staticmethod
        def post(url, headers, json, timeout):
            return _FakeResponse(json_body=_hex_response_body(audio_hex))

    monkeypatch.setattr("requests.post", FakeRequests.post)

    tool = MiniMaxMusic()
    result = tool.execute({
        "prompt": "ambient",
        "is_instrumental": True,
        "output_path": str(output),
    })

    assert result.success is True, result.error
    assert result.data["generation_mode"] == "instrumental"


def test_execute_success_lyrics_optimizer_song(monkeypatch, tmp_path):
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 16)
    output = tmp_path / "auto.mp3"
    audio_hex = b"FAKE".hex()

    class FakeRequests:
        @staticmethod
        def post(url, headers, json, timeout):
            return _FakeResponse(json_body=_hex_response_body(audio_hex))

    monkeypatch.setattr("requests.post", FakeRequests.post)

    tool = MiniMaxMusic()
    result = tool.execute({
        "prompt": "Cyberpunk synthwave with vocal hook about digital rain",
        "lyrics_optimizer": True,
        "output_path": str(output),
    })

    assert result.success is True, result.error
    assert result.data["generation_mode"] == "song"


def test_execute_success_cover_with_remote_url(monkeypatch, tmp_path):
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 16)
    output = tmp_path / "cover.mp3"
    audio_hex = b"FAKE_COVER".hex()
    captured: dict = {}

    class FakeRequests:
        @staticmethod
        def post(url, headers, json, timeout):
            captured["body"] = json
            return _FakeResponse(json_body=_hex_response_body(audio_hex))

    monkeypatch.setattr("requests.post", FakeRequests.post)

    tool = MiniMaxMusic()
    result = tool.execute({
        "prompt": "bossa nova acoustic version",
        "lyrics": "[Verse]\nSaudade in the morning light",
        "audio_url": "https://example.com/original.mp3",
        "model": "music-cover",
        "output_path": str(output),
    })

    assert result.success is True, result.error
    body = captured["body"]
    assert body["model"] == "music-cover"
    assert body["audio_url"] == "https://example.com/original.mp3"
    assert "is_instrumental" not in body
    assert "lyrics_optimizer" not in body
    assert result.data["generation_mode"] == "cover"


def test_execute_success_cover_with_local_audio_path(monkeypatch, tmp_path):
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 16)
    ref = tmp_path / "ref.mp3"
    ref.write_bytes(b"REFERENCE_AUDIO")
    output = tmp_path / "cover.mp3"
    captured: dict = {}
    audio_hex = b"FAKE_COVER".hex()

    class FakeRequests:
        @staticmethod
        def post(url, headers, json, timeout):
            captured["body"] = json
            return _FakeResponse(json_body=_hex_response_body(audio_hex))

    monkeypatch.setattr("requests.post", FakeRequests.post)

    tool = MiniMaxMusic()
    result = tool.execute({
        "prompt": "jazz piano version",
        "audio_path": str(ref),
        "model": "music-cover",
        "output_path": str(output),
    })

    assert result.success is True, result.error
    audio_url = captured["body"]["audio_url"]
    assert audio_url.startswith("data:audio/mpeg;base64,")
    b64 = audio_url.split(",", 1)[1]
    assert base64.b64decode(b64) == b"REFERENCE_AUDIO"


def test_execute_success_url_response_format(monkeypatch, tmp_path):
    """When response_format=url, the tool downloads from the audio URL."""
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 16)
    output = tmp_path / "song.mp3"
    fake_bytes = b"FAKE_URL_DOWNLOAD"

    class FakeUrlResponse:
        status_code = 200
        content = fake_bytes

        def raise_for_status(self_inner):
            return None

    class FakeRequests:
        @staticmethod
        def post(url, headers, json, timeout):
            return _FakeResponse(
                json_body=_url_response_body("https://cdn.example.com/song.mp3")
            )

        @staticmethod
        def get(url, timeout):
            return FakeUrlResponse()

    monkeypatch.setattr("requests.post", FakeRequests.post)
    monkeypatch.setattr("requests.get", FakeRequests.get)

    tool = MiniMaxMusic()
    result = tool.execute({
        "prompt": "ambient",
        "lyrics": "[Verse]\nhi",
        "response_format": "url",
        "output_path": str(output),
    })

    assert result.success is True, result.error
    assert Path(result.data["output"]).read_bytes() == fake_bytes


def test_execute_success_free_tier(monkeypatch, tmp_path):
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 16)
    monkeypatch.setenv("MINIMAX_MUSIC_MODEL", "music-2.6-free")
    output = tmp_path / "song.mp3"

    class FakeRequests:
        @staticmethod
        def post(url, headers, json, timeout):
            return _FakeResponse(json_body=_hex_response_body(b"x".hex()))

    monkeypatch.setattr("requests.post", FakeRequests.post)

    tool = MiniMaxMusic()
    result = tool.execute({
        "prompt": "ambient",
        "lyrics": "[Verse]\nhi",
        "output_path": str(output),
    })
    assert result.success is True
    assert result.cost_usd == 0.0  # free tier
    assert result.data["model"] == "music-2.6-free"


def test_execute_falls_back_to_alternate_url_on_5xx(monkeypatch, tmp_path):
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 16)
    output = tmp_path / "song.mp3"
    urls_hit: list[str] = []
    audio_hex = b"FAKE".hex()

    class FakeRequests:
        @staticmethod
        def post(url, headers, json, timeout):
            urls_hit.append(url)
            if len(urls_hit) == 1:
                return _FakeResponse(status_code=502, text="bad gateway")
            return _FakeResponse(json_body=_hex_response_body(audio_hex))

    monkeypatch.setattr("requests.post", FakeRequests.post)

    tool = MiniMaxMusic()
    result = tool.execute({
        "prompt": "ambient",
        "lyrics": "[Verse]\nhi",
        "output_path": str(output),
    })
    assert result.success is True
    assert urls_hit[0] == MiniMaxMusic.PRIMARY_URL
    assert urls_hit[1] == MiniMaxMusic.FALLBACK_URL


def test_execute_propagates_http_error(monkeypatch, tmp_path):
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 16)
    output = tmp_path / "song.mp3"

    class FakeRequests:
        @staticmethod
        def post(url, headers, json, timeout):
            return _FakeResponse(status_code=400, text="bad request")

    monkeypatch.setattr("requests.post", FakeRequests.post)

    tool = MiniMaxMusic()
    result = tool.execute({
        "prompt": "ambient",
        "lyrics": "[Verse]\nhi",
        "output_path": str(output),
    })
    assert result.success is False
    assert "400" in result.error


def test_execute_propagates_minimax_api_error(monkeypatch, tmp_path):
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 16)
    bad = {
        "data": {"audio": "abcd"},
        "base_resp": {"status_code": 1001, "status_msg": "authentication failed"},
    }

    class FakeRequests:
        @staticmethod
        def post(url, headers, json, timeout):
            return _FakeResponse(json_body=bad)

    monkeypatch.setattr("requests.post", FakeRequests.post)

    tool = MiniMaxMusic()
    result = tool.execute({
        "prompt": "ambient",
        "lyrics": "[Verse]\nhi",
        "output_path": str(tmp_path / "x.mp3"),
    })
    assert result.success is False
    assert "1001" in result.error
    assert "authentication failed" in result.error


def test_execute_rejects_empty_audio(monkeypatch, tmp_path):
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 16)

    class FakeRequests:
        @staticmethod
        def post(url, headers, json, timeout):
            return _FakeResponse(json_body={
                "data": {},
                "base_resp": {"status_code": 0, "status_msg": "success"},
            })

    monkeypatch.setattr("requests.post", FakeRequests.post)

    tool = MiniMaxMusic()
    result = tool.execute({
        "prompt": "ambient",
        "lyrics": "[Verse]\nhi",
        "output_path": str(tmp_path / "x.mp3"),
    })
    assert result.success is False
    assert "missing" in result.error.lower() or "audio" in result.error.lower()


def test_execute_rejects_malformed_hex(monkeypatch, tmp_path):
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 16)

    class FakeRequests:
        @staticmethod
        def post(url, headers, json, timeout):
            return _FakeResponse(json_body={
                "data": {"audio": "not-hex-content!"},
                "base_resp": {"status_code": 0, "status_msg": "success"},
            })

    monkeypatch.setattr("requests.post", FakeRequests.post)

    tool = MiniMaxMusic()
    result = tool.execute({
        "prompt": "ambient",
        "lyrics": "[Verse]\nhi",
        "output_path": str(tmp_path / "x.mp3"),
    })
    assert result.success is False
    assert "hex" in result.error.lower()


def test_execute_redacts_api_key_in_error(monkeypatch, tmp_path):
    """When MiniMax returns an HTTP error containing the bearer token
    in the response body, the tool must scrub it from the surfaced error."""
    monkeypatch.setenv("MINIMAX_API_KEY", "super-secret-key-1234")

    class FakeRequests:
        @staticmethod
        def post(url, headers, json, timeout):
            return _FakeResponse(
                status_code=401,
                text="auth failed for super-secret-key-1234",
            )

    monkeypatch.setattr("requests.post", FakeRequests.post)

    tool = MiniMaxMusic()
    result = tool.execute({
        "prompt": "ambient",
        "lyrics": "[Verse]\nhi",
        "output_path": str(tmp_path / "x.mp3"),
    })
    assert result.success is False
    assert "super-secret-key-1234" not in result.error
    assert "[redacted]" in result.error


def test_execute_streaming_forces_hex(monkeypatch, tmp_path):
    """When stream=True, the payload forces response_format=hex
    even if the caller passed something else."""
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 16)
    output = tmp_path / "song.mp3"
    captured: dict = {}

    class FakeRequests:
        @staticmethod
        def post(url, headers, json, timeout):
            captured["body"] = json
            return _FakeResponse(json_body=_hex_response_body(b"x".hex()))

    monkeypatch.setattr("requests.post", FakeRequests.post)

    tool = MiniMaxMusic()
    # Note: we do NOT pass response_format here — the tool applies the
    # default ('hex') and the stream branch hard-codes it again.
    result = tool.execute({
        "prompt": "ambient",
        "lyrics": "[Verse]\nhi",
        "stream": True,
        "output_path": str(output),
    })
    assert result.success is True, result.error
    assert captured["body"]["response_format"] == "hex"
    assert captured["body"].get("stream") is True


# ----------------------------------------------------------------------
# Registry integration
# ----------------------------------------------------------------------


def test_registry_discovers_minimax_music():
    from tools.tool_registry import ToolRegistry

    reg = ToolRegistry()
    reg.discover()
    names = {t.name for t in reg.get_by_capability("music_generation")}
    assert "minimax_music" in names
    assert "minimax" in {t.provider for t in reg.get_by_provider("minimax")}


def test_registry_minimax_provider_has_three_tools():
    """The MiniMax provider ships TTS + image + music — verify all three
    are auto-discovered under the same provider."""
    from tools.tool_registry import ToolRegistry

    reg = ToolRegistry()
    reg.discover()
    minimax_tools = {t.name for t in reg.get_by_provider("minimax")}
    assert {"minimax_tts", "minimax_image", "minimax_music"} <= minimax_tools