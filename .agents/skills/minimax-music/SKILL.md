---
name: minimax-music
description: Generate songs, instrumentals, and covers with MiniMax music-2.6 / music-cover. Use when (1) producing Mandarin or multilingual songs with structured lyrics tags ([Verse], [Chorus], [Bridge]), (2) instrumental background music with rich style/mood prompts, (3) letting the model auto-write lyrics from a single style prompt via `lyrics_optimizer`, (4) cover re-recording from a reference audio (URL, base64, or a two-step `cover_feature_id`), or (5) the user prefers MiniMax over Suno / ElevenLabs Music.
---

# MiniMax Music

Requires `MINIMAX_API_KEY` in `.env` (the same platform key as MiniMax TTS
and Image). Optional env vars: `MINIMAX_MUSIC_MODEL` (default `music-2.6`)
and `MINIMAX_MUSIC_RESPONSE_FORMAT` (`hex` default, or `url`).

## API

Synchronous HTTP endpoint:

```text
POST https://api.minimaxi.com/v1/music_generation
Authorization: Bearer ${MINIMAX_API_KEY}
Content-Type: application/json
```

The tool automatically retries against
`https://api-bj.minimaxi.com/v1/music_generation` when the primary host
returns a 5xx or DNS error. The endpoint is synchronous — expect 20-60
seconds per generation. There is no streaming variant for the free tiers.

## Models

| Model | Mode | Notes |
|-------|------|-------|
| `music-2.6` (default) | Text-to-music | Paid. Supports `is_instrumental`, `lyrics_optimizer`, structure tags, up to 2000-char prompts. |
| `music-2.6-free` | Text-to-music | Free tier with RPM limits. Same fields as `music-2.6`. |
| `music-cover` | Cover re-recording | Paid. Requires one of `audio_url` / `audio_base64` / `cover_feature_id`. |
| `music-cover-free` | Cover re-recording | Free tier with RPM limits. Same fields as `music-cover`. |

Use `-free` models only for experiments and smoke tests. Production work
should use the paid tier for predictable latency and no RPM surprises.

## Generation modes

The tool picks the mode automatically from the `model` and inputs:

| `model` family | Trigger | `generation_mode` |
|----------------|---------|--------------------|
| `music-2.6` / `music-2.6-free` | `is_instrumental=True` | `instrumental` |
| `music-2.6` / `music-2.6-free` | lyrics or `lyrics_optimizer` | `song` |
| `music-cover` / `music-cover-free` | `audio_url` or `audio_base64` | `cover` |
| `music-cover` / `music-cover-free` | `cover_feature_id` | `cover_two_step` |

## Lyrics structure tags

MiniMax recognizes inline structure tags inside the `lyrics` string.
Place them on their own line to mark section boundaries:

```text
[Intro]
[Verse 1]
Walking through the morning light
Coffee in my hand feels right
[Chorus]
We keep moving forward
Through the noise and doubt
[Bridge]
[Outro]
```

Recognized tags include `[Intro]`, `[Verse]`, `[Pre Chorus]`,
`[Chorus]`, `[Interlude]`, `[Bridge]`, `[Outro]`, `[Post Chorus]`,
`[Transition]`, `[Break]`, `[Hook]`, `[Build Up]`, `[Inst]`, `[Solo]`.
Tags are case-sensitive. Unknown tags are passed through but the model
treats them as plain text.

## `lyrics_optimizer`

When `lyrics_optimizer=True` and `lyrics` is empty, MiniMax auto-generates
lyrics from the `prompt`. Useful when you have only a style description
("独立民谣,忧郁,内省,渴望") and want the model to write the words.

- Only honored by `music-2.6` and `music-2.6-free`.
- Cannot combine with `is_instrumental=True`.
- Combine with a strong style prompt for best results — vague prompts
  produce generic lyrics.

## Cover workflow

The cover models re-record a reference audio in a different style or with
different lyrics. Three input modes:

| Input | Use when |
|-------|----------|
| `audio_url` | The reference is already hosted on a public HTTPS URL. |
| `audio_path` / `audio_base64` | The reference is local; the tool base64-encodes it automatically. |
| `cover_feature_id` | Two-step flow — MiniMax has already preprocessed the reference; you only have the ID. |

Reference audio constraints:

- Duration: 6 seconds – 6 minutes.
- Size: ≤ 50 MB.
- Format: any common audio format (mp3, wav, flac, m4a, ogg, ...).
- Provide exactly ONE of the three cover inputs — they are mutually
  exclusive. The tool enforces this.

For cover models:

- `prompt` is the target style, 10–300 chars.
- `lyrics` is the rewritten lyrics, 10–1000 chars (optional).
- `is_instrumental` and `lyrics_optimizer` are silently ignored.

## Request body

### Text-to-music

```json
{
  "model": "music-2.6",
  "prompt": "独立民谣,忧郁,内省,渴望,独自漫步,咖啡馆",
  "lyrics": "[Verse]\n独自走在霓虹的街头\n...",
  "is_instrumental": false,
  "lyrics_optimizer": false,
  "aigc_watermark": false,
  "response_format": "hex",
  "audio_setting": {
    "sample_rate": 44100,
    "bitrate": 256000,
    "format": "mp3",
    "channel": 2
  }
}
```

### Cover

```json
{
  "model": "music-cover",
  "prompt": "Bossa nova acoustic version",
  "lyrics": "New rewritten lyrics here",
  "audio_url": "https://example.com/original.mp3",
  "audio_setting": {"sample_rate": 44100, "bitrate": 256000, "format": "mp3"}
}
```

## Response

The `data.audio` field is a hex-encoded audio blob by default (or a 24h
URL when `response_format=url`). The tool decodes the hex to a binary
audio file. Useful fields on `extra_info`:

- `music_duration` — milliseconds
- `music_size` — bytes
- `audio_sample_rate`, `audio_channel`, `audio_format`, `audio_bitrate`

`base_resp.status_code == 0` means success; non-zero values carry
`status_msg` (e.g. `insufficient balance`, `lyrics length error`,
`prompt too long`).

## OpenMontage Usage

Generate via the music selector (the agent picks the best available
provider):

```python
from tools.audio.music_selector import MusicSelector

result = MusicSelector().execute({
    "preferred_provider": "minimax",
    "prompt": "独立民谣,忧郁,内省,渴望,独自漫步,咖啡馆",
    "lyrics": "[Verse]\n独自走在霓虹的街头\n...",
    "model": "music-2.6",
    "is_instrumental": False,
    "output_path": "projects/my-video/assets/music/theme.mp3",
})
```

Or call the provider directly:

```python
from tools.audio.minimax_music import MiniMaxMusic

# Vocal song with structured lyrics
result = MiniMaxMusic().execute({
    "prompt": "独立民谣,忧郁,内省,渴望,独自漫步,咖啡馆",
    "lyrics": "[Verse]\n独自走在霓虹的街头\n...",
    "model": "music-2.6",
    "output_path": "projects/my-video/assets/music/theme.mp3",
})

# Instrumental from a style prompt only
result = MiniMaxMusic().execute({
    "prompt": "Soft ambient piano, 60 BPM, late-night cafe mood",
    "is_instrumental": True,
    "model": "music-2.6",
    "output_path": "projects/my-video/assets/music/ambient.mp3",
})

# Auto-generated lyrics from a single style prompt
result = MiniMaxMusic().execute({
    "prompt": "Cyberpunk synthwave with vocal hook about digital rain",
    "lyrics_optimizer": True,
    "model": "music-2.6",
    "output_path": "projects/my-video/assets/music/synth.mp3",
})

# Cover re-recording from a local reference
result = MiniMaxMusic().execute({
    "prompt": "Acoustic bossa nova version",
    "lyrics": "[Verse]\nSaudade in the morning light",
    "audio_path": "projects/my-video/assets/music/original.mp3",
    "model": "music-cover",
    "output_path": "projects/my-video/assets/music/cover.mp3",
})
```

The provider writes:

- `output_path` — the decoded audio file (mp3/wav/flac).
- `metadata_path` — full response JSON (default `<output_path>.json`),
  containing `trace_id`, `extra_info`, `analysis_info`, and the request
  echo for debugging.

## Recommended Workflow

1. Pick a model. Default `music-2.6` for production songs and
   instrumentals; `music-2.6-free` for experiments and smoke tests.
2. Generate a 30-second sample before a full-length paid generation.
3. Ask the user to approve mood, genre, and vocal style.
4. Generate the full track only after approval.
5. For cover workflows, verify the reference audio plays cleanly and is
   within the 6s–6min / 50MB window before submitting.
6. Archive with `response_format="hex"` (default) when the asset is going
   to live past 24 hours; `url` mode expires.

## Parameters

- `prompt` — Style/mood description. ≤ 2000 chars for music-2.6;
  10–300 chars for cover models.
- `lyrics` — Song lyrics with structure tags. 1–3500 chars for vocal
  text-to-music; 10–1000 chars for cover models (optional). Not allowed
  with `is_instrumental=True`.
- `model` — `music-2.6` (default), `music-2.6-free`, `music-cover`,
  `music-cover-free`.
- `is_instrumental` — `False` default. Only honored by `music-2.6` /
  `music-2.6-free`.
- `lyrics_optimizer` — `False` default. Only honored by `music-2.6` /
  `music-2.6-free`.
- `aigc_watermark` — `False` default. Append an AIGC watermark to the
  audio (skipped in streaming mode).
- `response_format` — `hex` (default, decoded inline) or `url`
  (24h-expiring URL).
- `stream` — `False` default. When `True`, `response_format` is forced
  to `hex`.
- `format` — `mp3` (default), `wav`, or `flac`.
- `sample_rate` — 8000/16000/22050/24000/32000/44100/48000,
  default 44100.
- `bitrate` — 64000/96000/128000/192000/256000/320000, default 256000.
- `channel` — 1 or 2, default 2.
- `audio_url` / `audio_path` / `audio_base64` / `cover_feature_id` —
  Cover reference. Exactly ONE.

## Limitations

- Synchronous endpoint — expect 20-60 seconds per generation. Long songs
  may take longer.
- `prompt` capped at **2000 characters** for `music-2.6` and
  **10–300 characters** for cover models.
- `lyrics` capped at **3500 characters** for vocal `music-2.6` and
  **10–1000 characters** for cover models.
- `is_instrumental` and `lyrics_optimizer` are rejected on cover models
  — neither flag makes sense there.
- Cover reference audio: 6 seconds – 6 minutes, ≤ 50 MB.
- URL response format expires after 24 hours. Download the file promptly
  or use `response_format="hex"`.
- Free tiers share platform-wide RPM limits — bursts will rate-limit.

## Troubleshooting

- `HTTP 401` / `auth` errors — verify `MINIMAX_API_KEY` is a MiniMax
  platform key (not a third-party / passthrough key).
- `insufficient balance` — top up the MiniMax account, or switch to the
  `-free` tier for experiments.
- `lyrics length error` — keep lyrics ≤ 3500 chars for music-2.6,
  10–1000 for cover.
- `prompt too long` — trim to ≤ 2000 chars for music-2.6, 10–300 for
  cover.
- Cover reference rejected — verify the audio is 6s–6min, ≤ 50 MB, and
  in a common format (mp3, wav, flac, m4a, ogg).
- `base_resp.status_code != 0` — read `status_msg`; the diagnostic hint
  in the error string will name the constraint that triggered it.

## Safety

Never print or write the API key to logs, metadata, patches, or project
artifacts. The metadata JSON persisted to `<output_path>.json` contains
only the `Authorization`-less request echo and the public response
payload. The tool redacts the bearer token in error messages using
word-boundary-aware regex so short keys don't get clobbered inside other
words.