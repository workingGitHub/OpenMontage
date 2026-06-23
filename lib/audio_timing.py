"""Audio timing helpers — use to align TTS segments with video xfade boundaries.

The "narration cut off before next scene" bug (see projects/byakuya-60s v2):
  TTS mp3s have trailing silence baked in (typically 0.14-0.32s after the
  last audible phoneme). When edit_decisions used the mp3's total duration
  as `out_seconds` and the compose script computed video xfade offset as
  `out_seconds - xfade_dur`, the xfade started ~0.6s before the last
  phoneme ended. Users heard "picture already changed but narration
  hasn't finished".

Fix: every compose script should derive `out_seconds` from the actual
last audible frame of each TTS segment, not from the mp3's total duration.
Use `compute_aligned_durations()` to get a list of out_seconds that:

  1. starts each video xfade at the previous segment's last_sound_end
     (so the picture only begins changing after the narration finishes
     its last word)
  2. leaves room for a full xfade_dur transition on top of the trailing
     silence
  3. pads each TTS segment with apad to match out_seconds exactly, so
     the audio segment boundary = the video segment boundary

Usage (mirrors what compose_v3.py does for byakuya-60s):

    from lib.audio_timing import probe_tts_segments, compute_aligned_durations

    probes = probe_tts_segments([Path("scene_01.mp3"), Path("scene_02.mp3"), ...])
    durations = compute_aligned_durations(probes, xfade_dur=0.6)
    # durations[i] = last_sound_end[i] + xfade_dur
    # audio[i]   = atrim + apad to durations[i]
    # xfade offset for segment i+1 = durations[i] - xfade_dur
    # audio chain uses acrossfade(d=xfade_dur) between segments
"""
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TTSTimingProbe:
    path: Path
    mp3_total_s: float
    last_sound_end_s: float
    trailing_silence_s: float

    def __str__(self) -> str:
        return (f"{self.path.name}: total={self.mp3_total_s:.3f}s, "
                f"last_sound={self.last_sound_end_s:.3f}s, "
                f"trail_sil={self.trailing_silence_s:.3f}s")


def ffprobe_duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(r.stdout.strip())


def silencedetect_last_sound_end(
    path: Path,
    silence_db: float = -40.0,
    silence_min_s: float = 0.10,
) -> float:
    """Return the timestamp of the last audible sample in the mp3.

    Take the LAST silence_start in the silencedetect output — that is the
    first frame of the trailing silence region. The last audible sample
    is just before it. If no silence is detected, returns the file's
    total duration (i.e. the whole file is audible).
    """
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path),
         "-af", f"silencedetect=noise={silence_db}dB:d={silence_min_s}",
         "-f", "null", "-"],
        capture_output=True, text=True,
    )
    last_silence_start: float | None = None
    for line in r.stderr.splitlines():
        m = re.match(r"\[silencedetect @ \S+\] silence_start: ([\d.]+)", line)
        if m:
            last_silence_start = float(m.group(1))
    if last_silence_start is None:
        return ffprobe_duration(path)
    return last_silence_start


def probe_tts_segment(path: Path) -> TTSTimingProbe:
    """Measure a TTS mp3's total duration and last audible frame."""
    total = ffprobe_duration(path)
    last_snd = silencedetect_last_sound_end(path)
    return TTSTimingProbe(
        path=path,
        mp3_total_s=total,
        last_sound_end_s=last_snd,
        trailing_silence_s=total - last_snd,
    )


def probe_tts_segments(paths: list[Path]) -> list[TTSTimingProbe]:
    return [probe_tts_segment(p) for p in paths]


def compute_aligned_durations(
    probes: list[TTSTimingProbe],
    xfade_dur: float = 0.6,
) -> list[float]:
    """Return out_seconds for each segment, aligned so the xfade chain
    starts at the previous segment's last_sound_end.

    out_seconds[i] = last_sound_end_s[i] + xfade_dur

    This guarantees:
      - xfade[i+1] starts at out_seconds[i] - xfade_dur = last_sound_end[i]
      - audio segment boundary = video segment boundary
      - audio segment is padded with apad to fill out_seconds[i]
      - audio uses acrossfade(d=xfade_dur) between segments, mirroring
        the video xfade chain
    """
    return [round(p.last_sound_end_s + xfade_dur, 3) for p in probes]


def assert_xfade_aligned_with_audio(
    out_seconds_list: list[float],
    probes: list[TTSTimingProbe],
    xfade_dur: float,
    tolerance_s: float = 0.05,
) -> None:
    """Guard rail: the video xfade at boundary i must begin at or after
    probe[i-1].last_sound_end_s. Used by compose scripts to fail fast
    if out_seconds was derived from mp3_total_s instead of
    last_sound_end_s — the exact mistake that produced the
    byakuya-60s v2 "narration cut off" bug.
    """
    for i in range(1, len(out_seconds_list)):
        xfade_start = out_seconds_list[i - 1] - xfade_dur
        last_snd = probes[i - 1].last_sound_end_s
        if xfade_start + tolerance_s < last_snd:
            raise AssertionError(
                f"Scene {i} xfade starts at {xfade_start:.3f}s but previous "
                f"scene's last audible frame is at {last_snd:.3f}s — picture "
                f"will change {last_snd - xfade_start:.3f}s BEFORE narration "
                f"ends. Recompute out_seconds with "
                f"compute_aligned_durations()."
            )
