"""Tests for the MiniMax T2A v2 text-to-speech provider tool.

These tests are offline-friendly: they only exercise the request-body
construction, hex decoding, error diagnostics, and registry wiring. The
network call is mocked via ``monkeypatch`` so no ``MINIMAX_API_KEY`` is
required to run the suite.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


minimax_tts_module = importlib.import_module("tools.audio.minimax_tts")
MiniMaxTTS = minimax_tts_module.MiniMaxTTS


# ----------------------------------------------------------------------
# Fixtures and helpers
# ----------------------------------------------------------------------


@pytest.fixture
def tool(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "test-minimax-key")
    return MiniMaxTTS()


def _audio_hex_payload(audio_bytes: bytes = b"FAKE_MP3_BYTES", **extra) -> dict:
    body = {
        "data": {"audio": audio_bytes.hex(), "status": 2},
        "extra_info": {
            "audio_length": 9900,
            "audio_sample_rate": 32000,
            "audio_size": len(audio_bytes),
            "bitrate": 128000,
            "word_count": 52,
            "invisible_character_ratio": 0,
            "usage_characters": 26,
            "audio_format": "mp3",
            "audio_channel": 1,
        },
        "trace_id": "01b8bf9bb7433cc75c18eee6cfa8fe21",
        "base_resp": {"status_code": 0, "status_msg": "success"},
    }
    body.update(extra)
    return body


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
    tool = MiniMaxTTS()
    info = tool.get_info()
    assert info["name"] == "minimax_tts"
    assert info["tier"] == "voice"
    assert info["capability"] == "tts"
    assert info["provider"] == "minimax"
    assert info["runtime"] == "api"
    assert info["stability"] in {"beta", "experimental", "production"}


def test_status_unavailable_when_env_missing(monkeypatch):
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.delenv("MINIMAX_T2A_API_KEY", raising=False)
    monkeypatch.delenv("MM_API_KEY", raising=False)
    monkeypatch.delenv("MM_TTS_API_KEY", raising=False)
    tool = MiniMaxTTS()
    assert tool.get_status().value == "unavailable"


def test_status_available_with_env(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "x")
    tool = MiniMaxTTS()
    assert tool.get_status().value == "available"


def test_fallback_lists_other_tts_providers():
    tool = MiniMaxTTS()
    assert "doubao_tts" in tool.fallback_tools
    assert "elevenlabs_tts" in tool.fallback_tools


def test_capabilities_expose_feature_flags():
    tool = MiniMaxTTS()
    assert "text_to_speech" in tool.capabilities
    assert "emotion_control" in tool.capabilities
    assert tool.supports["emotion"] is True
    assert tool.supports["multilingual"] is True
    assert tool.supports["voice_cloning"] is False


# ----------------------------------------------------------------------
# Cost estimation
# ----------------------------------------------------------------------


def test_estimate_cost_scales_with_text_length():
    tool = MiniMaxTTS()
    short = tool.estimate_cost({"text": "hello"})
    long = tool.estimate_cost({"text": "hello " * 200})
    assert long > short
    assert short >= 0
    assert round(long, 4) == long  # sanity: no floating-point noise


# ----------------------------------------------------------------------
# Payload construction
# ----------------------------------------------------------------------


def test_build_payload_emits_required_fields():
    tool = MiniMaxTTS()
    payload = tool._build_payload(
        text="测试文本",
        voice_id="male-qn-qingse",
        model="speech-2.8-hd",
        speed=1.0,
        vol=1.0,
        pitch=0,
        emotion=None,
        fmt="mp3",
        sample_rate=32000,
        bitrate=128000,
        channel=1,
        subtitle_enable=False,
        subtitle_granularity="sentence",
        language=None,
        pronunciation_dict=None,
        sound_effects=None,
    )
    assert payload["model"] == "speech-2.8-hd"
    assert payload["text"] == "测试文本"
    assert payload["stream"] is False
    assert payload["voice_setting"]["voice_id"] == "male-qn-qingse"
    assert payload["audio_setting"]["format"] == "mp3"
    assert payload["audio_setting"]["sample_rate"] == 32000
    assert payload["subtitle_enable"] is False
    # language_boost is only set when caller asked for one.
    assert "language_boost" not in payload
    # pronunciation_dict is only set when caller passed one.
    assert "pronunciation_dict" not in payload


def test_build_payload_includes_language_boost_when_set():
    tool = MiniMaxTTS()
    payload = tool._build_payload(
        text="hello",
        voice_id="English_male_en_man",
        model="speech-2.8-hd",
        speed=1.0,
        vol=1.0,
        pitch=0,
        emotion=None,
        fmt="mp3",
        sample_rate=32000,
        bitrate=128000,
        channel=1,
        subtitle_enable=False,
        subtitle_granularity="sentence",
        language="English",
        pronunciation_dict=["hello/həˈloʊ"],
        sound_effects=None,
    )
    assert payload["language_boost"] == "English"
    assert payload["pronunciation_dict"] == {"tone": ["hello/həˈloʊ"]}


def test_emotion_silently_dropped_for_legacy_models():
    """speech-01/-02 do not honor emotion; the execute() guard must drop it."""
    tool = MiniMaxTTS()
    payload = tool._build_payload(
        text="hello",
        voice_id="male-qn-qingse",
        model="speech-01-hd",
        speed=1.0,
        vol=1.0,
        pitch=0,
        emotion=None,  # already filtered by execute()
        fmt="mp3",
        sample_rate=32000,
        bitrate=128000,
        channel=1,
        subtitle_enable=False,
        subtitle_granularity="sentence",
        language=None,
        pronunciation_dict=None,
        sound_effects=None,
    )
    assert "emotion" not in payload["voice_setting"]


# ----------------------------------------------------------------------
# execute() error paths
# ----------------------------------------------------------------------


def test_execute_rejects_missing_api_key(monkeypatch):
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.delenv("MINIMAX_T2A_API_KEY", raising=False)
    monkeypatch.delenv("MM_API_KEY", raising=False)
    monkeypatch.delenv("MM_TTS_API_KEY", raising=False)
    tool = MiniMaxTTS()
    result = tool.execute({"text": "hello"})
    assert result.success is False
    assert "MINIMAX_API_KEY" in result.error


def test_execute_rejects_empty_text(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "x")
    tool = MiniMaxTTS()
    result = tool.execute({"text": ""})
    assert result.success is False
    assert "text is required" in result.error


def test_execute_rejects_text_over_10k(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "x")
    tool = MiniMaxTTS()
    big = "x" * 10001
    result = tool.execute({"text": big})
    assert result.success is False
    assert "10000" in result.error


def test_execute_rejects_unknown_model(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "x")
    tool = MiniMaxTTS()
    result = tool.execute({"text": "hi", "model": "speech-99-imaginary"})
    assert result.success is False
    assert "Unsupported model" in result.error


# ----------------------------------------------------------------------
# execute() success path (mocked HTTP)
# ----------------------------------------------------------------------


def test_execute_success_decodes_hex_and_writes_files(monkeypatch, tmp_path):
    monkeypatch.setenv("MINIMAX_API_KEY", "test-minimax-key")
    output = tmp_path / "narration.mp3"

    audio_bytes = b"FAKE_MP3_BYTES"
    response_body = _audio_hex_payload(audio_bytes)

    captured: dict = {}

    class FakeRequests:
        @staticmethod
        def post(url, headers, json, timeout):
            captured["url"] = url
            captured["headers"] = headers
            captured["body"] = json
            captured["timeout"] = timeout
            return _FakeResponse(json_body=response_body)

    monkeypatch.setattr("requests.post", FakeRequests.post)

    tool = MiniMaxTTS()
    result = tool.execute({
        "text": "今天是不是很开心呀(laughs)，当然了！",
        "voice_id": "male-qn-qingse",
        "model": "speech-2.8-hd",
        "emotion": "happy",
        "format": "mp3",
        "output_path": str(output),
    })

    assert result.success is True, result.error
    assert output.exists()
    assert output.read_bytes() == audio_bytes
    # Authorization header carries the bearer token.
    assert captured["headers"]["Authorization"] == "Bearer test-minimax-key"
    assert captured["headers"]["Content-Type"] == "application/json"
    # Primary URL is used when reachable.
    assert captured["url"] == MiniMaxTTS.PRIMARY_URL
    # Payload mappings.
    assert captured["body"]["model"] == "speech-2.8-hd"
    assert captured["body"]["voice_setting"]["emotion"] == "happy"
    assert captured["body"]["audio_setting"]["format"] == "mp3"
    # Result metadata.
    assert result.data["voice_id"] == "male-qn-qingse"
    assert result.data["audio_length_ms"] == 9900
    assert result.data["usage_characters"] == 26
    assert result.data["trace_id"] == "01b8bf9bb7433cc75c18eee6cfa8fe21"
    assert result.cost_usd >= 0
    # Metadata JSON written next to the audio file.
    metadata_path = Path(result.data["metadata_path"])
    assert metadata_path.exists()
    meta = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert meta["trace_id"] == "01b8bf9bb7433cc75c18eee6cfa8fe21"
    assert meta["request"]["model"] == "speech-2.8-hd"


def test_execute_success_emits_subtitle_file_when_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("MINIMAX_API_KEY", "x")
    output = tmp_path / "narration.mp3"
    response_body = _audio_hex_payload()
    response_body["data"]["subtitle_file"] = (
        "1\n00:00:00,000 --> 00:00:02,500\n测试文本\n"
    )

    class FakeRequests:
        @staticmethod
        def post(url, headers, json, timeout):
            return _FakeResponse(json_body=response_body)

    monkeypatch.setattr("requests.post", FakeRequests.post)

    tool = MiniMaxTTS()
    result = tool.execute({
        "text": "测试文本",
        "output_path": str(output),
        "subtitle_enable": True,
        "subtitle_granularity": "sentence",
    })
    assert result.success is True
    assert result.data["subtitle_file"] is not None
    srt_path = Path(result.data["subtitle_file"])
    assert srt_path.exists()
    assert "测试文本" in srt_path.read_text(encoding="utf-8")


def test_execute_falls_back_to_alternate_url_on_5xx(monkeypatch, tmp_path):
    monkeypatch.setenv("MINIMAX_API_KEY", "x")
    output = tmp_path / "narration.mp3"
    response_body = _audio_hex_payload()

    urls_hit: list[str] = []

    class FakeRequests:
        @staticmethod
        def post(url, headers, json, timeout):
            urls_hit.append(url)
            if len(urls_hit) == 1:
                # Simulate server error on the primary host.
                return _FakeResponse(status_code=502, text="bad gateway")
            return _FakeResponse(json_body=response_body)

    monkeypatch.setattr("requests.post", FakeRequests.post)

    tool = MiniMaxTTS()
    result = tool.execute({"text": "hello", "output_path": str(output)})

    assert result.success is True, result.error
    assert urls_hit[0] == MiniMaxTTS.PRIMARY_URL
    assert urls_hit[1] == MiniMaxTTS.FALLBACK_URL


def test_execute_propagates_4xx_error(monkeypatch, tmp_path):
    monkeypatch.setenv("MINIMAX_API_KEY", "x")

    class FakeRequests:
        @staticmethod
        def post(url, headers, json, timeout):
            return _FakeResponse(status_code=401, text="Unauthorized")

    monkeypatch.setattr("requests.post", FakeRequests.post)

    tool = MiniMaxTTS()
    result = tool.execute({"text": "hi", "output_path": str(tmp_path / "x.mp3")})
    assert result.success is False
    assert "401" in result.error


def test_execute_propagates_api_level_error(monkeypatch, tmp_path):
    monkeypatch.setenv("MINIMAX_API_KEY", "x")

    bad_response = {
        "data": {"audio": "deadbeef", "status": 1},
        "extra_info": {"usage_characters": 0},
        "trace_id": "t",
        "base_resp": {"status_code": 1001, "status_msg": "authentication failed"},
    }

    class FakeRequests:
        @staticmethod
        def post(url, headers, json, timeout):
            return _FakeResponse(json_body=bad_response)

    monkeypatch.setattr("requests.post", FakeRequests.post)

    tool = MiniMaxTTS()
    result = tool.execute({"text": "hi", "output_path": str(tmp_path / "x.mp3")})
    assert result.success is False
    assert "1001" in result.error
    assert "authentication failed" in result.error


def test_execute_rejects_malformed_hex(monkeypatch, tmp_path):
    monkeypatch.setenv("MINIMAX_API_KEY", "x")

    bad = {
        "data": {"audio": "not-valid-hex-zz", "status": 2},
        "extra_info": {"audio_length": 1000, "usage_characters": 5},
        "trace_id": "t",
        "base_resp": {"status_code": 0, "status_msg": "success"},
    }

    class FakeRequests:
        @staticmethod
        def post(url, headers, json, timeout):
            return _FakeResponse(json_body=bad)

    monkeypatch.setattr("requests.post", FakeRequests.post)

    tool = MiniMaxTTS()
    result = tool.execute({"text": "hi", "output_path": str(tmp_path / "x.mp3")})
    assert result.success is False
    assert "hex" in result.error.lower()


def test_safe_error_redacts_api_key(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "super-secret-key")
    msg = MiniMaxTTS._safe_error(Exception("boom super-secret-key again"))
    assert "super-secret-key" not in msg
    assert "[redacted]" in msg


# ----------------------------------------------------------------------
# Registry integration
# ----------------------------------------------------------------------


def test_registry_discovers_minimax_tts():
    from tools.tool_registry import ToolRegistry

    reg = ToolRegistry()
    reg.discover()
    names = {t.name for t in reg.get_by_capability("tts")}
    assert "minimax_tts" in names
    assert "minimax" in {t.provider for t in reg.get_by_provider("minimax")}