# 多段旁白的音画同步

> 范围：任何把多段 TTS / 录音按"每段一个时间槽"嵌进时间轴的项目（讲解课、纪录片、电子书朗读）。
> 适用：`script.json` 设计为 N 个 30s 槽位、每段旁白只占槽位前 17s、后 13s 留给视觉/文字卡的"留白式"叙事结构。

## 一句话结论

**多段音频按顺序 concat ≠ 按时间播放**。合成 `full.mp3` 时必须按 `script.json` 里每段的 `start_seconds` 用 `adelay` 显式偏移，不能让 Remotion 的 `Audio` 组件从 0:00 顺播到尾。

## 标准合成命令（直接照抄）

```python
import subprocess

target_dur = 240  # 视频总长
sections = script["sections"]  # 每个 section 必须有 start_seconds

# 1) 8 段旁白各自 adelay 到目标时间戳
filter_parts = []
inputs = []
for i, s in enumerate(sections):
    delay_ms = int(s["start_seconds"] * 1000)
    inputs.extend(["-i", f"assets/narration/{s['id']}.mp3"])
    filter_parts.append(
        f"[{i}:a]adelay={delay_ms}|{delay_ms},asetpts=PTS-STARTPTS[a{i}]"
    )

# 2) 叠一条 240s anullsrc 静音底铺满时间轴（第 N+1 个输入）
inputs.extend(["-f", "lavfi", "-t", str(target_dur),
               "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"])
silence_idx = len(sections)

# 3) amix duration=longest 自然处理空段；apad 锁死总长
mix_inputs = "".join(f"[a{i}]" for i in range(len(sections))) + f"[{silence_idx}:a]"
mix_filter = (
    f"{mix_inputs}"
    f"amix=inputs={len(sections)+1}:duration=longest:normalize=0,"
    f"apad=whole_dur={target_dur}:pad_dur=0[mix]"
)
filter_complex = ";".join(filter_parts) + ";" + mix_filter

cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + inputs + [
    "-filter_complex", filter_complex,
    "-map", "[mix]",
    "-c:a", "libmp3lame", "-b:a", "128k", "-ar", "44100",
    "assets/narration/full.mp3",
]
subprocess.run(cmd, check=True)
```

## 三个不能踩的坑

1. **`apad` 必须带 `whole_dur` 参数**。不带 → 无限延伸，曾实测膨胀到 11.5GB / 723500s。规则：**用 `apad` 必给 `whole_dur`**。
2. **不要相信"按顺序 concat 出来就是按顺序播放"**。ffmpeg 的 `concat` demuxer / `-filter_complex amix` 默认按输入时间叠加，不做时间戳重映射。
3. **`asetpts=PTS-STARTPTS` 是 `adelay` 后的必加项**，否则 `adelay` 引入的"音频起播前的那段空白"会被 PTS 算成"已存在的内容"导致后续混音时长计算偏移 0–1s。

## 必做的验证（不要只听一遍就过）

```python
# 5s 窗口 RMS 包络，截断阈值 -35dB 判断是否人声活跃
import subprocess
clusters = []
cur_active = False
cur_start = None
for i in range(0, 48):  # 48 个 5s 窗口 = 240s
    start = i * 5
    rms = parse_volumedetect(f"assets/narration/full.mp3", start, 5)
    is_speech = rms > -35
    if is_speech and not cur_active:
        cur_start, cur_active = start, True
    elif not is_speech and cur_active:
        clusters.append((cur_start, start))
        cur_active = False

# 把检出簇 vs script.sections 做对齐表，Y/N 一目了然
```

视觉交叉抽样：每个 section 的 narration 中点（`start_seconds + narration_duration/2`）抽一帧，人眼判断画面是否对应旁白主题。这一步比 RMS 更直接——画面讲春草、旁白念春草才叫对。

## 复用清单（每次新项目都过一遍）

- [ ] `script.json` 是多段时间槽结构（每段 < 槽位长）→ 用本文件的合成命令
- [ ] `script.json` 是单段顺播（旁白总长 == 视频总长）→ 简单 concat 即可
- [ ] 合成后做 5s 窗口 RMS 校验，N 个簇 vs N 个 sections 严格对齐
- [ ] 渲染后抽帧做视觉交叉抽样，至少覆盖首/中/末三个 section
- [ ] ffprobe 校验最终 MP4 的 audio stream duration == video stream duration（容差 < 0.5s）

## 相关事件

- 2026-06-24 — `zhu-ziqing-spring` 项目首版音画错位 25s，根因即本文件核心规则。详见 `docs/incidents/2026-06-24-zhu-ziqing-spring-audio-video-desync.md`。

## 后续工程化（建议沉淀到代码库）

- [ ] `lib/audio_timing.py` 增加 `compose_full_narration(script, asset_manifest, target_duration)`，封装本文件合成命令
- [ ] 渲染验证 stage-gate 增加"音画时间对齐校验"步骤（5s 窗口 RMS + 主题帧抽样）
- [ ] `tools/video/video_compose._render` 的 `subprocess.run(timeout=600)` 调高到 1800s（240s 1080p 渲染实测 800s，刚踩 600s 红线）
