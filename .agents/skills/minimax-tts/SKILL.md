---
name: minimax-tts
description: Generate Mandarin and multilingual narration with MiniMax T2A v2 (speech-2.8 family). Use when creating Chinese or bilingual voiceovers, when the user prefers MiniMax / MiniMax TTS, when narration needs emotion tags (happy / sad / angry / surprised / fearful / disgusted / neutral) or sound effect tags like (laughs) / (sighs), or when sentence/word timestamps are required for subtitle alignment.
---

# MiniMax TTS

Requires `MINIMAX_API_KEY` in `.env` (the platform API key from
[Account → API Keys](https://platform.minimaxi.com/user-center/basic-information/Interface-key)).
Optional env vars: `MINIMAX_VOICE_ID` (default `male-qn-qingse`) and
`MINIMAX_TTS_MODEL` (default `speech-2.8-hd`).

## API

Synchronous HTTP endpoint, hex-encoded audio payload:

```text
POST https://api.minimaxi.com/v1/t2a_v2
Authorization: Bearer ${MINIMAX_API_KEY}
Content-Type: application/json
```

The tool automatically retries against `https://api-bj.minimaxi.com/v1/t2a_v2`
when the primary host returns a 5xx or DNS error.

### Request body

```json
{
  "model": "speech-2.8-hd",
  "text": "今天是不是很开心呀(laughs)，当然了！",
  "stream": false,
  "voice_setting": {
    "voice_id": "male-qn-qingse",
    "speed": 1,
    "vol": 1,
    "pitch": 0,
    "emotion": "happy"
  },
  "pronunciation_dict": {
    "tone": ["处理/(chu3)(li3)", "危险/dangerous"]
  },
  "audio_setting": {
    "sample_rate": 32000,
    "bitrate": 128000,
    "format": "mp3",
    "channel": 1
  },
  "subtitle_enable": false
}
```

### Response

The response's `data.audio` is a hex-encoded audio blob; the tool decodes it
to the file at `output_path`. Useful fields on `extra_info`:

- `audio_length` — duration in milliseconds
- `audio_sample_rate`, `audio_size`, `audio_format`, `audio_channel`
- `word_count`, `usage_characters` — for billing reconciliation
- `invisible_character_ratio` — non-zero values mean the text contained
  characters the model could not pronounce; trim before retrying

## Models

| Model | Notes |
|-------|-------|
| `speech-2.8-hd` (default) | Best quality, supports emotion + sound-effect tags |
| `speech-2.8-turbo` | Lower latency, same tag support as `-hd` |
| `speech-2.6-hd` / `-turbo` | Previous generation |
| `speech-02-hd` / `-turbo` | Older family |
| `speech-01-hd` / `-turbo` | Legacy; not recommended for new productions |

Only `speech-2.8-hd` and `speech-2.8-turbo` honor the `emotion` parameter
and the inline sound-effect tags (`(laughs)`, `(sighs)`, ...). The tool
silently drops `emotion` when called with an older model rather than failing.

## Voices

The default is `male-qn-qingse`. Voice selection is by `voice_id` — consult
the MiniMax voice library in the platform console for the latest list. Common
voices include `male-qn-qingse`, `female-shaonv`, `male-qn-jingying`, and
many `presenter_*` / `audiobook_*` / `English_*` variants for narration.
Some voices require account authorization — the API surfaces a
`voice permission denied` style error if a voice is not unlocked for your key.

## Text features

MiniMax TTS understands inline annotations directly in the `text` string:

- **Pause control** — `<#x#>` where `x` is seconds (0.01 – 99.99).
  Place between two speakable segments; do not chain multiple pauses.
- **Pronunciation override** — wrap pinyin (with tone digits) or IPA in
  parentheses, e.g. `危险/dangerous`, `This is (he2)平, not (huo4)面`,
  `去街市買啲(sung3)` for Cantonese, `The word live is pronounced (lɪv)`.
- **Sound-effect tags** — only when the model is `speech-2.8-hd` /
  `speech-2.8-turbo`. Supported: `(laughs)`, `(chuckle)`, `(coughs)`,
  `(clear-throat)`, `(groans)`, `(breath)`, `(pant)`, `(inhale)`,
  `(exhale)`, `(gasps)`, `(sniffs)`, `(sighs)`, `(snorts)`, `(burps)`,
  `(lip-smacking)`, `(humming)`, `(hissing)`, `(emm)`, `(sneezes)`.
- **Paragraph breaks** — newline `\n` to mark paragraph transitions.

## OpenMontage Usage

Generate with the TTS selector (the agent picks the best available provider):

```python
from tools.audio.tts_selector import TTSSelector

result = TTSSelector().execute({
    "preferred_provider": "minimax",
    "text": "如果 AI 真的会改变未来，普通人到底该怎么参与？",
    "voice_id": "male-qn-qingse",
    "model": "speech-2.8-hd",
    "emotion": "neutral",
    "output_path": "projects/my-video/assets/audio/narration.mp3",
    "subtitle_enable": True,
})
```

Or call the provider directly:

```python
from tools.audio.minimax_tts import MiniMaxTTS

result = MiniMaxTTS().execute({
    "text": "短样本试听文本。",
    "voice_id": "male-qn-qingse",
    "model": "speech-2.8-hd",
    "output_path": "projects/my-video/assets/audio/minimax_sample.mp3",
})
```

The provider writes:

- `output_path` — the decoded audio file (mp3/wav/flac/pcm).
- `metadata_path` — full response JSON (default `<output_path>.json`),
  containing `trace_id`, `extra_info`, and the request echo for debugging.
- `subtitle_file` (when `subtitle_enable=true`) — SRT subtitles at
  `<output_path>.srt`.

## Recommended Workflow

1. Pick a model. Start with `speech-2.8-hd` for explainers.
2. Generate a 10–15 second sample before a full paid narration.
3. Ask the user to approve voice naturalness, accent, and emotion delivery.
4. Generate the full narration only after approval.
5. For subtitle pipelines, set `subtitle_enable: true` and use the
   returned SRT (`subtitle_file`) directly — it is the source of truth.
6. If the agent asks for a different voice than `male-qn-qingse`,
   confirm the `voice_id` is unlocked on the MiniMax account before
   spending credits on a full narration.

## Parameters

- `voice_id` — MiniMax speaker id. Defaults to `MINIMAX_VOICE_ID` or
  `male-qn-qingse`.
- `model` — `speech-2.8-hd` (default), `-turbo`, or an older family.
- `speed` — 0.5 – 2.0, default 1.0.
- `pitch` — -12 – 12 semitones, default 0.
- `vol` — 0.0 – 10.0, default 1.0.
- `emotion` — `happy | sad | angry | fearful | disgusted | surprised | neutral`.
  Only honored by `speech-2.8-hd` / `speech-2.8-turbo`.
- `format` — `mp3 | wav | flac | pcm`. Non-streaming mode only.
- `sample_rate` — `8000 / 16000 / 22050 / 24000 / 32000 / 44100 / 48000`,
  default 32000.
- `bitrate` — `32000 / 64000 / 96000 / 128000 / 192000 / 256000`,
  default 128000 (ignored for wav/flac/pcm).
- `subtitle_enable` — `false` by default. Set `true` to receive SRT-style
  subtitle output.
- `pronunciation_dict` — list of override strings, e.g.
  `["处理/(chu3)(li3)", "危险/dangerous"]`.

## Limitations

- Text input capped at **10,000 characters** per request. For longer
  inputs, segment and synthesize in chunks, then concatenate with FFmpeg.
  Inputs over 3,000 characters should normally use the streaming T2A
  endpoint instead — the synchronous variant may time out.
- `speech-01` and `speech-02` families do not support Persian, Filipino,
  or Tamil.
- `sound_effects` and `emotion` are silently ignored when using older models.

## Troubleshooting

- `HTTP 401` / `auth` errors — verify `MINIMAX_API_KEY` is a MiniMax
  platform key (not a third-party / passthrough key).
- `voice permission denied` — unlock the voice on the MiniMax console.
- `insufficient balance` — top up the MiniMax account.
- Long response times — lower the model to `speech-2.8-turbo` or break
  the text into smaller chunks.
- `invisible_character_ratio` > 0 on the response — the text contained
  characters the model could not pronounce; remove them and re-run.
- Subtitle file is empty — confirm `subtitle_enable: true` was sent and
  the model in use actually supports subtitles (all currently listed
  models do).

## Safety

Never print or write the API key to logs, metadata, patches, or project
artifacts. The metadata JSON persisted to `<output_path>.json` contains
only the `Authorization`-less request echo and the public response payload.