# Lessons Learned

跨项目可复用的技术经验沉淀。每篇聚焦一个具体技术陷阱，给出**直接可用的代码 / 命令**和**必做的验证步骤**，让新项目开工时能快速借鉴。

## 索引

| 主题 | 适用场景 | 文件 |
| --- | --- | --- |
| 多段旁白的音画同步 | script 是 N 段时间槽、每段旁白 < 槽位长的项目 | [`multi-segment-audio-timing.md`](multi-segment-audio-timing.md) |

## 与 `incidents/` 的区别

- **`incidents/`** — 事件复盘，绑项目 + 日期 + 时间线。回答"那次为什么坏了"。
- **`lessons-learned/`** — 抽象经验，跨项目。回答"下次做类似事怎么直接做对"。

写 lessons 时，incident 文档应被引用（`相关事件` 章节），便于追溯来源。
