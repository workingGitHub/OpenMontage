---
name: minimax-image
description: Generate images with MiniMax image-01 / image-01-live via the /v1/image_generation endpoint. Use when (1) generating text-to-image with 8 aspect ratios (including 21:9 cinematic), (2) image-to-image with character / subject reference for identity consistency, (3) batch-generating up to 9 images in one request, (4) the user prefers MiniMax over FLUX / Imagen / Grok. Always read this skill before calling minimax_image.
---

# MiniMax Image

Requires `MINIMAX_API_KEY` in `.env` (same key as MiniMax TTS).
Optional env vars: `MINIMAX_IMAGE_MODEL` (default `image-01`) and
`MINIMAX_IMAGE_RESPONSE_FORMAT` (`url` default, or `base64`).

## API

Single endpoint that handles both T2I and I2I based on the payload:

```text
POST https://api.minimaxi.com/v1/image_generation
Authorization: Bearer ${MINIMAX_API_KEY}
Content-Type: application/json
```

The tool auto-retries against `https://api-bj.minimaxi.com/v1/image_generation`
when the primary host returns a 5xx or DNS error.

## Models

| Model | Notes |
|-------|-------|
| `image-01` (default) | Production model. Supports `width`/`height` (multiple of 8, 512-2048) and the `21:9` aspect ratio. |
| `image-01-live` | Newer model. Supports the `style` object for drawing-style presets. Does NOT honor `21:9`, `width`, or `height`. |

When `model="image-01"`, if both `width` and `height` are provided they take
precedence over `aspect_ratio`. The tool only forwards `width`/`height` for
`image-01` to avoid API rejections.

## Request body

### Common fields

| Field | Type | Notes |
|-------|------|-------|
| `model` | string | `image-01` or `image-01-live`. |
| `prompt` | string | Up to 1500 characters. Required. |
| `aspect_ratio` | string | `1:1` (1024×1024), `16:9` (1280×720), `4:3`, `3:2`, `2:3`, `3:4`, `9:16`, `21:9` (image-01 only). Default `1:1`. |
| `response_format` | string | `url` (24h expiry) or `base64`. |
| `seed` | int64 | Same seed + params → near-identical output. |
| `n` | int 1–9 | Batch size. Default 1. |
| `prompt_optimizer` | bool | Default false. When true, MiniMax rewrites the prompt for quality. **Keep false for prompt fidelity** unless the user accepts rewrites. |
| `aigc_watermark` | bool | Default false. |
| `style` | object | Only honored when `model=image-01-live`. Pass-through; consult platform console for valid sub-keys. |
| `subject_reference` | array | I2I only. List of `{type, image_file}` entries. |

### I2I `subject_reference` shape

```json
{
  "subject_reference": [
    {"type": "character", "image_file": "https://.../ref1.jpg"}
  ]
}
```

- `type` — semantic tag (`character`, `object`, `style`, etc.).
- `image_file` — public HTTPS URL, or a `data:<mime>;base64,...` URI for
  local files (the tool encodes local paths automatically).

The tool auto-switches to I2I mode when the caller passes any of:
`image_url`, `image_path`, `image_urls`, `image_paths`, or
`generation_mode="edit"`. Each input image becomes its own
`subject_reference` entry with `type = subject_reference_type`
(default `character`).

### Response shape

```json
{
  "id": "03ff3cd0820949eba410056b5f21d38",
  "data": {"image_urls": ["https://.../0.png", "https://.../1.png"]},
  "metadata": {"failed_count": "0", "success_count": "2"},
  "base_resp": {"status_code": 0, "status_msg": "success"}
}
```

`failed_count` is non-zero when some images were blocked by content safety;
`success_count` tells you how many came back.

## OpenMontage Usage

Use the image selector (preferred — let the agent route):

```python
from tools.graphics.image_selector import ImageSelector

# Text-to-image
result = ImageSelector().execute({
    "preferred_provider": "minimax",
    "prompt": "Cinematic wide shot of a desert highway at golden hour, 35mm film, slight grain",
    "aspect_ratio": "21:9",
    "n": 4,
})

# Image-to-image with character reference
result = ImageSelector().execute({
    "preferred_provider": "minimax",
    "prompt": "The same character walking through a neon Tokyo alley, rain reflections",
    "image_url": "https://example.com/character_reference.jpg",
    "aspect_ratio": "16:9",
})
```

Or call the provider directly:

```python
from tools.graphics.minimax_image import MiniMaxImage

result = MiniMaxImage().execute({
    "prompt": "Studio portrait, soft window light, 85mm lens",
    "model": "image-01",
    "aspect_ratio": "4:3",
    "response_format": "base64",  # safer than 24h-expiring URLs
    "n": 2,
    "seed": 42,
    "output_path": "projects/portrait/assets/images/shot.png",
})
```

I2I call with a local reference:

```python
result = MiniMaxImage().execute({
    "prompt": "The same person on a sunlit beach, smiling",
    "image_path": "projects/portrait/assets/images/reference.png",
    "subject_reference_type": "character",
    "output_path": "projects/portrait/assets/images/beach.png",
})
```

## Recommended Workflow

1. **Pick a model.** Default `image-01` for explainer / cinematic visuals;
   `image-01-live` when you need a specific drawing style.
2. **Pick aspect ratio.** Use `21:9` for cinematic shots (image-01 only);
   `9:16` for short-form vertical; `1:1` for thumbnails.
3. **Generate a sample.** Run with `n=4` first to get a batch you can
   rank. The agent should present 2-3 finalists to the user.
4. **Iterate on prompt + seed.** Keep the seed fixed while changing
   prompts to compare variations. Re-roll the seed for diversity.
5. **For identity-consistent characters**, use `image_path` /
   `image_url` to set a `subject_reference`. The model preserves identity
   across shots but may not handle occlusions well — verify the output.
6. **Archive with `response_format="base64"`** when the asset is going
   to live past 24 hours; URLs from `url` mode expire.

## Parameter mapping

| Input field | API field | Notes |
|-------------|-----------|-------|
| `prompt` | `prompt` | Required. ≤ 1500 chars. |
| `model` | `model` | `image-01` (default) or `image-01-live`. |
| `aspect_ratio` | `aspect_ratio` | `1:1` default. |
| `width` / `height` | `width` / `height` | `image-01` only. Range 512–2048, multiple of 8. |
| `n` | `n` | 1–9. |
| `seed` | `seed` | Reproducibility. |
| `response_format` | `response_format` | `url` (default) or `base64`. |
| `prompt_optimizer` | `prompt_optimizer` | Off by default. |
| `aigc_watermark` | `aigc_watermark` | Off by default. |
| `style` | `style` | `image-01-live` only. |
| `image_url` / `image_path` | `subject_reference[]` | Auto-becomes `{type, image_file}`. |
| `image_urls` / `image_paths` | `subject_reference[]` | One entry per image. |
| `subject_reference_type` | `subject_reference[].type` | Defaults to `character`. |
| `generation_mode` | (selector only) | Forces edit mode. |

## Limitations

- `prompt` is capped at 1500 characters. For longer scene descriptions,
  trim aggressively or split into multiple generations.
- `width`/`height` must be a multiple of 8 and in [512, 2048].
- `21:9` aspect ratio is only effective with `image-01`.
- Subject-reference works best when the reference is a clean, front-facing
  portrait. Off-angle or heavily occluded references degrade identity
  preservation.
- URL responses expire after 24 hours. Download the file promptly or use
  `response_format=base64`.

## Troubleshooting

- `HTTP 401` / auth errors — `MINIMAX_API_KEY` is not a valid MiniMax
  platform key, or it has been revoked.
- `prompt length` errors — trim the prompt to ≤ 1500 characters.
- `width/height` errors — make sure they are multiples of 8 within
  [512, 2048], and that you are using `image-01` (not `-live`).
- `subject_reference` errors — make sure each `image_file` is a public
  HTTPS URL or a `data:` URI; the API does not accept raw base64.
- `failed_count > 0` — content safety blocked some images. Soften the
  prompt (avoid named real people, graphic violence, etc.) and re-run.

## Safety

Never print or write the API key to logs, metadata, patches, or project
artifacts. The tool redacts the bearer token in error messages using
word-boundary-aware regex so short keys don't get clobbered inside other
words.