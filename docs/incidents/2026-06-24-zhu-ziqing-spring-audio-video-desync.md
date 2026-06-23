# Incident: 朱自清《春》视频 — 旁白与画面错位 25 秒

| 字段 | 值 |
| --- | --- |
| 日期 | 2026-06-24 |
| 项目 | `projects/zhu-ziqing-spring/` |
| 严重度 | 高（最终交付物不可用） |
| 状态 | 已修复并重新渲染 |
| 受影响产物 | `projects/zhu-ziqing-spring/renders/final.mp4`（首版） |
| 修复后产物 | 同上，已覆盖为 v2 |

## 现象

首版渲染完成后用户回放反馈："旁白并没有与视频内容匹配上"。ffprobe 检查 16 个 cut 的视频流本身完全按时间线正确（sepia 肖像→清华时间卡→盼春→春草→春花→春风→春雨→迎春→三连比喻→片尾），但人声与画面之间存在约 25 秒的持续偏移——s3 旁白在 35–52s 念完"小草偷偷地从土里钻出来"，但春草画面要到 60–75s 才出现；类似错位贯穿全程 8 段。

## 时间线

- 09:00 PM（6/23）首次合成 `assets/narration/full.mp3`：把 8 段旁白 mp3 按顺序 concat 成单轨，总长 136.17s。
- 11:50 PM（6/23）Remotion 渲染首次完成（命中 Python 600s 子进程超时警告，但输出文件 82MB / 241s 实际有效）。
- 00:55 AM（6/24）用户验收时发现音画不同步。
- 01:00 AM 定位到 `full.mp3` 是无延迟的连续拼接。
- 01:05 AM 用 `adelay` + 静音底 + `apad=whole_dur=240` 重建 `full.mp3`，调 npx 重渲染，5s 窗口 RMS 包络校验通过。

## 根因

`script.json` 设计的 8 个 section 每个 30s 区间（s1=0–30, s2=30–60, ..., s8=210–240），但每段旁白 mp3 只有 ~17s——设计意图是前 17s 念稿、后 13s 留给用户读文字卡的"视觉呼吸"。

合成 `full.mp3` 时把 8 段顺序拼成一个 0–136s 的连续音轨，**没有按 section 的 `start_seconds` 偏移**。Remotion 的 `Audio` 组件会把这段 136s 的音轨当作整段 240s 视频的旁白从 0:00 一直播到 2:16——但画面 cut 的时间假设旁白在每个 30s 区间的**前 17s** 出现。两者相对位置完全错位 25 秒。

## 修复

`assets/narration/full.mp3` 重建命令（关键步骤）：

```
# 1) 8 段旁白分别 adelay 到对应 section 的 start_seconds
[0:a]adelay=0|0,...,asetpts=PTS-STARTPTS[a0]
[1:a]adelay=30000|30000,...,asetpts=PTS-STARTPTS[a1]
...
[7:a]adelay=210000|210000,...,asetpts=PTS-STARTPTS[a7]

# 2) 叠 240s anullsrc 静音底铺满时间轴（第 9 个输入）
-f lavfi -t 240 -i anullsrc=channel_layout=stereo:sample_rate=44100

# 3) amix duration=longest + apad=whole_dur=240 锁死输出长度
[a0][a1]...[a7][8:a]amix=inputs=9:duration=longest:normalize=0,apad=whole_dur=240:pad_dur=0[mix]
```

校验（5s 窗口 RMS 探测，截断阈值 -35dB）：

| Section | 期望区间 | 检出语音簇 | 状态 |
| --- | --- | --- | --- |
| s1 | 0–30s | 0–20s | ✓ |
| s2 | 30–60s | 30–50s | ✓ |
| s3 | 60–90s | 60–80s | ✓ |
| s4 | 90–120s | 90–110s | ✓ |
| s5 | 120–150s | 120–140s | ✓ |
| s6 | 150–180s | 150–165s | ✓ |
| s7 | 180–210s | 180–200s | ✓ |
| s8 | 210–240s | 210–230s | ✓ |

视觉交叉抽样 t=67s（春草）/ t=127s（柳枝春风）/ t=157s（雨中村庄）— 均与旁白主题吻合。

## 教训

1. **多段音频拼接是显式的时间轴责任**。"按顺序 concat" ≠ "按时间播放"，必须按目标时间戳 `adelay` 偏移。
2. **ffmpeg `apad` 永远带 `whole_dur` 参数**。不带的版本无界延伸——本次首次试错直接膨胀到 11.5GB / 723500s，被强制 kill 才停。
3. **`video_compose._render` 的 600s `subprocess.run` 超时对 240s 1080p 渲染刚踩线**。要么把 timeout 提到 1500s+，要么直接绕开 Python 用 `npx remotion render` 调起（本次做法）。这是隐藏的脆弱点。
4. **渲染验证流程应当包含"画面 + 旁白 + 时间戳"三者交叉抽样**，不能只看"画面渲染对了"就宣布成功——音画是双轨信号，必须双轨都核验。

## 后续行动

- [ ] `video_compose._render` 的 `subprocess.run(timeout=600)` 调整为 1800s（low-risk，单点改动）。
- [ ] `lib/audio_timing.py` 增加一个 `compose_full_narration(script, asset_manifest, target_duration)` 工具函数，把这次的 `adelay`+`anullsrc`+`amix`+`apad` 流程沉淀为可复用代码。
- [ ] 渲染验证工具链增加"音画时间对齐校验"步骤（按 section 抽帧 + 抽音频段，匹配主题词），作为 stage-gate 必过项。
