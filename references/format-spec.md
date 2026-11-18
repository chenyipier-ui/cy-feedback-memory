# 库格式规范 v1

格式才是这个项目的核心：只要遵守它，任何智能体、任何脚本都能读写同一个库，不依赖本仓库的代码。

## 目录

| 用途 | 中文布局（`--lang zh`） | 英文布局（`--lang en`） |
|---|---|---|
| 配置 | `fm.config.json` | `fm.config.json` |
| 读写协议 | `AGENTS.md` | `AGENTS.md` |
| 核心约定（第 0 层，每次加载） | `核心约定.md` | `CORE.md` |
| 路由表（第 0 层，自动生成） | `路由表.md` | `ROUTES.md` |
| 索引（自动生成，给人看） | `索引.md` | `INDEX.md` |
| 收件箱 | `收件箱.md` | `inbox.md` |
| 通用层 | `通用/` | `general/` |
| 任务类型层 | `任务类型/` | `tasks/` |
| 对象层（客户、项目、仓库、班级、店铺…） | `对象/` | `subjects/` |
| 归档 | `归档/` | `archive/` |
| 分支文件（第 1 层） | `经验.md` | `branch.md` |
| 反馈日志 | `反馈日志.md` | `log.md` |
| 条目目录（第 2 层） | `条目/` | `entries/` |
| 投放区（任何格式） | `投放区/` | `drop/` |
| 分支里的整份文档 | `文档/` | `docs/` |
| 维护记录 | `维护记录.md` | `MAINTENANCE.md` |
| 工具与状态（不要手改） | `.fm/` | `.fm/` |

所有名字都写在 `fm.config.json` 的 `layout` 里，可以改成任何名字（例如把「对象」改成「客户」），脚本按配置找文件。中英文布局可以混用。用 Git 管理实际经验库时选私有仓。需要跨 macOS 和 Linux 同步时，英文布局可避开中文文件名的编码差异（NFD/NFC）。

## 分支文件

```markdown
---
name: 合同审查
triggers: [合同, 审查, 法务]
summary: 合同审查意见怎么写
updated: 2026-11-12
---

# 合同审查

## 标准
- 先给三条最大风险，其余放附表（对内审查不适用）〔键:review.length〕〔×2〕 → [详情](条目/top3-risks.md)

## 待验证
## 资料
## 库外文档
```

- `name`、`triggers` 必填：路由表就是用这两项生成的。
- 段落标题按前缀识别，中英文都认：`标准 / Standards`、`待验证 / Candidates`、`资料 / Reference`、`库外文档 / External`。
- 标准行的写法：`- 结论（适用条件）〔键:x〕〔×N〕 → [详情](…)`，其中键、次数、详情都是可选的。英文库用 `[key:x] [×N]`。
- 每个分支建议不超过 15 条标准、4000 字（`limits.standards_per_branch`、`limits.standards_chars`）。超过只提醒，处理方式是按主题拆成子分支（`fm move`，原文不变），不是删减。

## 条目

```markdown
---
name: top3-risks
description: 一行
scope: always | candidate
key: review.length
source: 我 / 客户 / 上级
first_seen: 2026-11-12
last_confirmed: 2026-11-12
---
结论、原话、为什么、怎么用、例外。
```

## 反馈日志

只追加，新的写在最上面：`### 日期《话题》`，下面写原话、改了什么、沉淀到哪。

## 收件箱

在分隔线 `<!-- ↓ 新条目追加在这条线下面 / append new items below ↓ -->` 下面，一行一条：

```
- [2026-11-12 14:03] (scope: always; branch: 合同审查; key: review.length; source: 我) 「用户原话」 — 补充
```

整理时会把它们移到 `.fm/inbox-archive/`。

## 路由块

由 `sync` 写进各智能体的全局配置，包在两行标记之间：

```
<!-- cy-feedback-memory:start · … -->
…核心约定 + 分支路由…
<!-- cy-feedback-memory:end -->
```

标记外的内容永远不动。`sync --remove` 会只删掉这一块。

## `.fm/` 里有什么

| 文件 | 作用 |
|---|---|
| `tool/` | 库内工具副本；每台电脑运行 `sync` 时复制到 `~/.cy-feedback-memory/tool/` |
| `lock` / `maint.lock` | 写入锁（10 分钟过期）/ 维护锁（3 小时过期） |
| `journal.jsonl` | 接力日志：每次写入的时间、智能体、操作、目标、原话 |
| `roster.json` | 成员表：哪些智能体加入了、最近一次写入和同步的时间 |
| `state.json` | 采集进度、首次汇集的指纹、当天是否已维护 |
| `originals/日期/` | 转成 MD 之前的原件 |
| `snapshots/日期.zip` | 维护前快照，保留最近 14 份 |
| `inbox-archive/` | 已处理的收件箱条目 |

## 写入规则（所有智能体）

- 写标准、待验证、资料，一律用 `fm add`；用 `fm remember` 记收件箱；用 `fm place` 归位文件。
- 条目和文档可以直接编辑，编辑完运行 `fm lint`。
- 维护锁存在时，`add` 自动改写进收件箱；库不在线时，写到本机的 `~/.cy-feedback-memory/offline-inbox.md`。

## 版本

`fm.config.json` 里的 `format_version` 记录格式版本。不兼容的改动会把版本号加一，并附迁移说明。
