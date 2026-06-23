"""Tests for the MiniMax image generation tool.

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

minimax_image_module = importlib.import_module("tools.graphics.minimax_image")
MiniMaxImage = minimax_image_module.MiniMaxImage


# ----------------------------------------------------------------------
# Fixtures and helpers
# ----------------------------------------------------------------------


@pytest.fixture
def tool(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "test-minimax-key-1234")
    return MiniMaxImage()


def _url_response_body(image_urls: list[str]) -> dict:
    return {
        "id": "03ff3cd0820949eba410056b5f21d38",
        "data": {"image_urls": image_urls},
        "metadata": {
            "failed_count": "0",
            "success_count": str(len(image_urls)),
        },
        "base_resp": {"status_code": 0, "status_msg": "success"},
    }


def _base64_response_body(b64_chunks: list[str]) -> dict:
    return {
        "id": "03ff3cd0820949eba410056b5f21d38",
        "data": {"image_urls": b64_chunks},
        "metadata": {"failed_count": "0", "success_count": str(len(b64_chunks))},
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
    tool = MiniMaxImage()
    info = tool.get_info()
    assert info["name"] == "minimax_image"
    assert info["tier"] == "generate"
    assert info["capability"] == "image_generation"
    assert info["provider"] == "minimax"
    assert info["runtime"] == "api"


def test_status_unavailable_when_env_missing(monkeypatch):
    for key in ("MINIMAX_API_KEY", "MINIMAX_IMAGE_API_KEY", "MM_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    tool = MiniMaxImage()
    assert tool.get_status().value == "unavailable"


def test_status_available_with_env(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 16)
    tool = MiniMaxImage()
    assert tool.get_status().value == "available"


def test_supports_edit_mode_for_selector_routing():
    """image_selector routes 'edit' candidates by supports.image_edit or schema keys."""
    tool = MiniMaxImage()
    assert tool.supports.get("image_edit") is True
    props = tool.input_schema["properties"]
    # The selector filters by the presence of these keys in input_schema:
    for key in ("image_url", "image_path", "image_urls", "image_paths"):
        assert key in props, f"input_schema missing {key} — selector cannot route edits"


def test_fallback_lists_other_image_providers():
    tool = MiniMaxImage()
    assert "flux_image" in tool.fallback_tools
    assert "google_imagen" in tool.fallback_tools


def test_capabilities_expose_feature_flags():
    tool = MiniMaxImage()
    for cap in ("generate_image", "text_to_image", "image_to_image", "edit_image", "character_reference"):
        assert cap in tool.capabilities


# ----------------------------------------------------------------------
# Cost estimation
# ----------------------------------------------------------------------


def test_estimate_cost_scales_with_n():
    tool = MiniMaxImage()
    cheap = tool.estimate_cost({"n": 1, "model": "image-01"})
    big = tool.estimate_cost({"n": 4, "model": "image-01"})
    assert big > cheap
    live = tool.estimate_cost({"n": 1, "model": "image-01-live"})
    assert live > cheap  # live is the more expensive tier


# ----------------------------------------------------------------------
# Mode detection and reference collection
# ----------------------------------------------------------------------


def test_wants_edit_mode_with_image_url():
    tool = MiniMaxImage()
    assert tool._wants_edit_mode({"image_url": "https://example.com/r.jpg"}) is True


def test_wants_edit_mode_with_image_path(tmp_path):
    tool = MiniMaxImage()
    assert tool._wants_edit_mode({"image_path": str(tmp_path / "r.png")}) is True


def test_wants_edit_mode_with_image_urls_list():
    tool = MiniMaxImage()
    assert tool._wants_edit_mode({"image_urls": ["https://x/a.jpg"]}) is True


def test_wants_edit_mode_with_image_paths_list():
    tool = MiniMaxImage()
    assert tool._wants_edit_mode({"image_paths": ["a.png", "b.png"]}) is True


def test_wants_edit_mode_with_explicit_flag():
    tool = MiniMaxImage()
    assert tool._wants_edit_mode({"generation_mode": "edit"}) is True


def test_wants_edit_mode_false_by_default():
    tool = MiniMaxImage()
    assert tool._wants_edit_mode({"prompt": "hello"}) is False


def test_collect_reference_images_with_remote_url():
    tool = MiniMaxImage()
    refs = tool._collect_reference_images({"image_url": "https://example.com/r.jpg"})
    assert refs == ["https://example.com/r.jpg"]


def test_collect_reference_images_with_local_file(tmp_path):
    src = tmp_path / "ref.png"
    src.write_bytes(b"\x89PNG_FAKE")
    tool = MiniMaxImage()
    refs = tool._collect_reference_images({"image_path": str(src)})
    assert len(refs) == 1
    assert refs[0].startswith("data:image/png;base64,")


def test_collect_reference_images_combines_all_inputs(tmp_path):
    src = tmp_path / "local.png"
    src.write_bytes(b"\x89PNG_FAKE")
    tool = MiniMaxImage()
    refs = tool._collect_reference_images(
        {
            "image_url": "https://example.com/a.jpg",
            "image_path": str(src),
            "image_urls": ["https://example.com/b.jpg", "https://example.com/c.jpg"],
            "image_paths": [str(src)],
        }
    )
    # image_url + image_path + 2 in image_urls + 1 in image_paths = 5
    assert len(refs) == 5
    assert refs[0] == "https://example.com/a.jpg"
    assert refs[1].startswith("data:image/png;base64,")
    assert refs[2] == "https://example.com/b.jpg"
    assert refs[3] == "https://example.com/c.jpg"
    assert refs[4].startswith("data:image/png;base64,")


# ----------------------------------------------------------------------
# Payload construction
# ----------------------------------------------------------------------


def test_build_payload_t2i_default():
    tool = MiniMaxImage()
    payload = tool._build_payload(
        {"prompt": "hi", "n": 2, "seed": 42},
        model="image-01",
        prompt="hi",
        aspect_ratio="16:9",
        response_format="url",
        edit_mode=False,
        subject_reference=[],
    )
    assert payload["model"] == "image-01"
    assert payload["prompt"] == "hi"
    assert payload["aspect_ratio"] == "16:9"
    assert payload["response_format"] == "url"
    assert payload["n"] == 2
    assert payload["seed"] == 42
    # prompt_optimizer / aigc_watermark default to off — must not appear.
    assert "prompt_optimizer" not in payload
    assert "aigc_watermark" not in payload
    assert "subject_reference" not in payload
    # image-01 may receive width/height but not when absent.
    assert "width" not in payload


def test_build_payload_with_width_height_for_image_01():
    tool = MiniMaxImage()
    payload = tool._build_payload(
        {"prompt": "hi", "width": 1024, "height": 576},
        model="image-01",
        prompt="hi",
        aspect_ratio="16:9",
        response_format="url",
        edit_mode=False,
        subject_reference=[],
    )
    assert payload["width"] == 1024
    assert payload["height"] == 576


def test_build_payload_drops_width_height_for_live_model():
    """image-01-live does not honor width/height; the tool must not send them."""
    tool = MiniMaxImage()
    payload = tool._build_payload(
        {"prompt": "hi", "width": 1024, "height": 1024},
        model="image-01-live",
        prompt="hi",
        aspect_ratio="1:1",
        response_format="url",
        edit_mode=False,
        subject_reference=[],
    )
    assert "width" not in payload
    assert "height" not in payload


def test_build_payload_style_only_for_live():
    tool = MiniMaxImage()
    payload_live = tool._build_payload(
        {"prompt": "hi", "style": {"name": "anime"}},
        model="image-01-live",
        prompt="hi",
        aspect_ratio="1:1",
        response_format="url",
        edit_mode=False,
        subject_reference=[],
    )
    assert payload_live.get("style") == {"name": "anime"}
    # For image-01, style must be dropped.
    payload_01 = tool._build_payload(
        {"prompt": "hi", "style": {"name": "anime"}},
        model="image-01",
        prompt="hi",
        aspect_ratio="1:1",
        response_format="url",
        edit_mode=False,
        subject_reference=[],
    )
    assert "style" not in payload_01


def test_build_payload_with_subject_reference_for_i2i():
    tool = MiniMaxImage()
    payload = tool._build_payload(
        {"prompt": "walking through tokyo", "subject_reference_type": "character"},
        model="image-01",
        prompt="walking through tokyo",
        aspect_ratio="16:9",
        response_format="url",
        edit_mode=True,
        subject_reference=[
            {"type": "character", "image_file": "https://example.com/ref.jpg"},
        ],
    )
    assert payload["subject_reference"] == [
        {"type": "character", "image_file": "https://example.com/ref.jpg"}
    ]


def test_build_payload_only_sends_truthy_flags():
    tool = MiniMaxImage()
    payload = tool._build_payload(
        {"prompt": "hi", "prompt_optimizer": True, "aigc_watermark": True},
        model="image-01",
        prompt="hi",
        aspect_ratio="1:1",
        response_format="url",
        edit_mode=False,
        subject_reference=[],
    )
    assert payload["prompt_optimizer"] is True
    assert payload["aigc_watermark"] is True


# ----------------------------------------------------------------------
# execute() error paths
# ----------------------------------------------------------------------


def test_execute_rejects_missing_api_key(monkeypatch):
    for key in ("MINIMAX_API_KEY", "MINIMAX_IMAGE_API_KEY", "MM_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    tool = MiniMaxImage()
    result = tool.execute({"prompt": "hello"})
    assert result.success is False
    assert "MINIMAX_API_KEY" in result.error


def test_execute_rejects_empty_prompt(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 16)
    tool = MiniMaxImage()
    result = tool.execute({"prompt": ""})
    assert result.success is False
    assert "prompt" in result.error


def test_execute_rejects_oversized_prompt(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 16)
    tool = MiniMaxImage()
    result = tool.execute({"prompt": "x" * 1501})
    assert result.success is False
    assert "1500" in result.error


def test_execute_rejects_unknown_model(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 16)
    tool = MiniMaxImage()
    result = tool.execute({"prompt": "hi", "model": "image-99-fake"})
    assert result.success is False
    assert "Unsupported model" in result.error


def test_execute_rejects_bad_width(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 16)
    tool = MiniMaxImage()
    # 1001 is in range but not a multiple of 8
    result = tool.execute({"prompt": "hi", "model": "image-01", "width": 1001, "height": 1024})
    assert result.success is False
    assert "multiple of 8" in result.error


def test_execute_rejects_width_out_of_range(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 16)
    tool = MiniMaxImage()
    result = tool.execute({"prompt": "hi", "model": "image-01", "width": 4096, "height": 4096})
    assert result.success is False
    assert "512, 2048" in result.error


# ----------------------------------------------------------------------
# execute() success path (mocked HTTP)
# ----------------------------------------------------------------------


def test_execute_success_t2i_with_url_format(monkeypatch, tmp_path):
    monkeypatch.setenv("MINIMAX_API_KEY", "test-minimax-key-1234")
    output = tmp_path / "shot.png"
    fake_image_bytes = b"\x89PNG\r\n\x1a\nFAKE_BYTES"
    captured: dict = {}

    def fake_get(url, timeout):
        captured["download_url"] = url
        return _FakeResponse(status_code=200, text="")  # .content below

    class FakeImageResponse:
        status_code = 200
        content = fake_image_bytes

        def raise_for_status(self):
            return None

    class FakeRequests:
        @staticmethod
        def post(url, headers, json, timeout):
            captured["url"] = url
            captured["headers"] = headers
            captured["body"] = json
            captured["timeout"] = timeout
            return _FakeResponse(
                json_body=_url_response_body(
                    ["https://cdn.example.com/a.png", "https://cdn.example.com/b.png"]
                )
            )

        @staticmethod
        def get(url, timeout):
            captured["download_url"] = url
            return FakeImageResponse()

    monkeypatch.setattr("requests.post", FakeRequests.post)
    monkeypatch.setattr("requests.get", FakeRequests.get)

    tool = MiniMaxImage()
    result = tool.execute({
        "prompt": "cinematic desert highway",
        "model": "image-01",
        "aspect_ratio": "21:9",
        "n": 2,
        "seed": 42,
        "response_format": "url",
        "output_path": str(output),
    })

    assert result.success is True, result.error
    # URL requests are downloaded by the tool.
    assert captured["url"] == MiniMaxImage.PRIMARY_URL
    assert captured["headers"]["Authorization"] == "Bearer test-minimax-key-1234"
    body = captured["body"]
    assert body["model"] == "image-01"
    assert body["aspect_ratio"] == "21:9"
    assert body["n"] == 2
    assert body["seed"] == 42
    assert body["response_format"] == "url"
    assert "subject_reference" not in body
    # Both images were downloaded and saved.
    assert result.data["images_generated"] == 2
    assert result.data["generation_mode"] == "generate"
    assert result.data["subject_references"] == 0
    assert result.data["success_count"] == 2
    assert result.data["failed_count"] == 0
    assert Path(result.data["output"]).exists()
    artifacts = result.artifacts
    assert len(artifacts) == 2
    for path in artifacts:
        assert Path(path).exists()
        assert Path(path).read_bytes() == fake_image_bytes


def test_execute_success_t2i_with_base64_format(monkeypatch, tmp_path):
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 16)
    output = tmp_path / "shot.png"
    image_bytes = b"FAKE_BASE64_IMAGE"
    b64 = base64.b64encode(image_bytes).decode("ascii")

    class FakeRequests:
        @staticmethod
        def post(url, headers, json, timeout):
            return _FakeResponse(json_body=_base64_response_body([b64]))

    monkeypatch.setattr("requests.post", FakeRequests.post)

    tool = MiniMaxImage()
    result = tool.execute({
        "prompt": "hello",
        "response_format": "base64",
        "output_path": str(output),
    })
    assert result.success is True
    assert result.data["images_generated"] == 1
    decoded = Path(result.data["output"]).read_bytes()
    assert decoded == image_bytes


def test_execute_success_i2i_with_remote_subject_reference(monkeypatch, tmp_path):
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 16)
    output = tmp_path / "scene.png"
    fake_bytes = b"FAKE_I2I_BYTES"

    class FakeImageResponse:
        status_code = 200
        content = fake_bytes

        def raise_for_status(self):
            return None

    captured: dict = {}

    class FakeRequests:
        @staticmethod
        def post(url, headers, json, timeout):
            captured["body"] = json
            return _FakeResponse(json_body=_url_response_body(["https://cdn.example.com/scene.png"]))

        @staticmethod
        def get(url, timeout):
            return FakeImageResponse()

    monkeypatch.setattr("requests.post", FakeRequests.post)
    monkeypatch.setattr("requests.get", FakeRequests.get)

    tool = MiniMaxImage()
    result = tool.execute({
        "prompt": "the same character on a beach",
        "image_url": "https://example.com/character.png",
        "subject_reference_type": "character",
        "aspect_ratio": "16:9",
        "output_path": str(output),
    })

    assert result.success is True, result.error
    body = captured["body"]
    assert body["subject_reference"] == [
        {"type": "character", "image_file": "https://example.com/character.png"}
    ]
    assert result.data["generation_mode"] == "edit"
    assert result.data["subject_references"] == 1
    assert result.data["images_generated"] == 1


def test_execute_success_i2i_with_local_subject_reference(monkeypatch, tmp_path):
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 16)
    ref_path = tmp_path / "ref.png"
    ref_path.write_bytes(b"REFERENCE_IMAGE_BYTES")
    output = tmp_path / "shot.png"

    captured: dict = {}

    class FakeRequests:
        @staticmethod
        def post(url, headers, json, timeout):
            captured["body"] = json
            return _FakeResponse(json_body=_url_response_body(["https://cdn.example.com/x.png"]))

        @staticmethod
        def get(url, timeout):
            class R:
                status_code = 200
                content = b"FAKE"

                def raise_for_status(self_inner):
                    return None

            return R()

    monkeypatch.setattr("requests.post", FakeRequests.post)
    monkeypatch.setattr("requests.get", FakeRequests.get)

    tool = MiniMaxImage()
    result = tool.execute({
        "prompt": "the same character walking in tokyo",
        "image_path": str(ref_path),
        "output_path": str(output),
    })

    assert result.success is True, result.error
    refs = captured["body"]["subject_reference"]
    assert refs[0]["type"] == "character"
    assert refs[0]["image_file"].startswith("data:image/png;base64,")


def test_execute_records_partial_failures(monkeypatch, tmp_path):
    """If metadata.failed_count > 0, the tool surfaces it without crashing."""
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 16)
    body = _url_response_body(["https://cdn.example.com/a.png"])
    body["metadata"] = {"failed_count": "1", "success_count": "1"}
    output = tmp_path / "shot.png"

    class FakeRequests:
        @staticmethod
        def post(url, headers, json, timeout):
            return _FakeResponse(json_body=body)

        @staticmethod
        def get(url, timeout):
            class R:
                status_code = 200
                content = b"OK"

                def raise_for_status(self_inner):
                    return None

            return R()

    monkeypatch.setattr("requests.post", FakeRequests.post)
    monkeypatch.setattr("requests.get", FakeRequests.get)

    tool = MiniMaxImage()
    result = tool.execute({"prompt": "hi", "output_path": str(output), "n": 2})
    assert result.success is True
    assert result.data["failed_count"] == 1
    assert result.data["success_count"] == 1


def test_execute_falls_back_to_alternate_url_on_5xx(monkeypatch, tmp_path):
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 16)
    output = tmp_path / "shot.png"
    urls_hit: list[str] = []

    class FakeRequests:
        @staticmethod
        def post(url, headers, json, timeout):
            urls_hit.append(url)
            if len(urls_hit) == 1:
                return _FakeResponse(status_code=502, text="bad gateway")
            return _FakeResponse(json_body=_url_response_body(["https://cdn.example.com/a.png"]))

        @staticmethod
        def get(url, timeout):
            class R:
                status_code = 200
                content = b"X"

                def raise_for_status(self_inner):
                    return None

            return R()

    monkeypatch.setattr("requests.post", FakeRequests.post)
    monkeypatch.setattr("requests.get", FakeRequests.get)

    tool = MiniMaxImage()
    result = tool.execute({"prompt": "hi", "output_path": str(output)})
    assert result.success is True
    assert urls_hit[0] == MiniMaxImage.PRIMARY_URL
    assert urls_hit[1] == MiniMaxImage.FALLBACK_URL


def test_execute_propagates_api_level_error(monkeypatch, tmp_path):
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 16)
    bad = {
        "data": {"image_urls": []},
        "metadata": {"failed_count": "0", "success_count": "0"},
        "base_resp": {"status_code": 1001, "status_msg": "authentication failed"},
    }

    class FakeRequests:
        @staticmethod
        def post(url, headers, json, timeout):
            return _FakeResponse(json_body=bad)

    monkeypatch.setattr("requests.post", FakeRequests.post)

    tool = MiniMaxImage()
    result = tool.execute({"prompt": "hi", "output_path": str(tmp_path / "x.png")})
    assert result.success is False
    assert "1001" in result.error
    assert "authentication failed" in result.error


def test_execute_rejects_empty_image_list(monkeypatch, tmp_path):
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 16)

    class FakeRequests:
        @staticmethod
        def post(url, headers, json, timeout):
            return _FakeResponse(json_body={
                "data": {"image_urls": []},
                "metadata": {"failed_count": "0", "success_count": "0"},
                "base_resp": {"status_code": 0, "status_msg": "success"},
            })

    monkeypatch.setattr("requests.post", FakeRequests.post)

    tool = MiniMaxImage()
    result = tool.execute({"prompt": "hi", "output_path": str(tmp_path / "x.png")})
    assert result.success is False
    assert "no image_urls" in result.error.lower()


def test_safe_error_redacts_api_key(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "super-secret-key-1234")
    msg = MiniMaxImage._safe_error(Exception("boom super-secret-key-1234 again"))
    assert "super-secret-key-1234" not in msg
    assert "[redacted]" in msg


# ----------------------------------------------------------------------
# URL/data-URI sniffing
# ----------------------------------------------------------------------


def test_infer_extension_from_url_png():
    assert MiniMaxImage._infer_extension("https://example.com/x.png") == ".png"


def test_infer_extension_from_url_unknown_falls_back_to_png():
    assert MiniMaxImage._infer_extension("https://example.com/x") == ".png"


def test_infer_extension_from_data_uri_png():
    assert MiniMaxImage._infer_extension_from_data_uri(
        "data:image/png;base64,iVBORw0KG"
    ) == ".png"


def test_infer_extension_from_data_uri_webp():
    assert MiniMaxImage._infer_extension_from_data_uri(
        "data:image/webp;base64,UklGRg"
    ) == ".webp"


def test_looks_like_b64_distinguishes_url_from_payload():
    assert MiniMaxImage._looks_like_b64("https://x/a.png") is False
    assert MiniMaxImage._looks_like_b64("data:image/png;base64,iVBORw0KG") is False
    assert MiniMaxImage._looks_like_b64("a" * 300) is True


# ----------------------------------------------------------------------
# Registry integration
# ----------------------------------------------------------------------


def test_registry_discovers_minimax_image():
    from tools.tool_registry import ToolRegistry

    reg = ToolRegistry()
    reg.discover()
    names = {t.name for t in reg.get_by_capability("image_generation")}
    assert "minimax_image" in names
    assert "minimax" in {t.provider for t in reg.get_by_provider("minimax")}


def test_image_selector_filters_to_edit_capable_for_i2i(monkeypatch, tmp_path):
    """When the caller passes an image_path, image_selector should route to
    tools whose schema accepts it (including MiniMax)."""
    monkeypatch.setenv("MINIMAX_API_KEY", "x" * 16)
    from tools.graphics.image_selector import ImageSelector

    sel = ImageSelector()
    filtered = sel._filter_candidates(
        {"image_path": str(tmp_path / "ref.png")},
        [t for t in sel._providers()],
    )
    filtered_names = {t.name for t in filtered}
    assert "minimax_image" in filtered_names
    assert "grok_image" in filtered_names