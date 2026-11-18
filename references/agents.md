# 智能体接入表

> 由 `data/agents.json` 生成。「状态」如实标注：只有「作者本机实测」的两家是实际跑通过的，其余按官方文档或公开信息写，欢迎提 PR 纠正。

## 读：路由块写到哪；首次汇集从哪读

| 智能体 | 接入方式 | 全局配置位置 | 首次汇集读取 | 对话采集 | 无头维护命令 | 状态 |
|---|---|---|---|---|---|---|
| Claude Code（命令行/桌面端/IDE 插件） | 插入一段 | `~/.claude/CLAUDE.md` | `~/.claude/CLAUDE.md`<br>`~/.claude/projects/*/memory/*.md` | claude | `claude -p --permission-mode acceptEdits --allowedTools "Bash(python3:*)" {prompt}` | 作者本机实测 |
| OpenAI Codex（命令行/桌面端/IDE 插件） | 插入一段 | `~/.codex/AGENTS.md` | `~/.codex/AGENTS.md`<br>按顺序使用首个存在的 `~/.codex/memories_v2/memory_summary.md`、`~/.codex/memories/memory_summary.md` 或 `~/.codex/memories/MEMORY.md` | codex | `codex exec --full-auto {prompt}` | 作者本机实测 |
| Gemini CLI / Google Antigravity | 插入一段 | `~/.gemini/GEMINI.md` | `~/.gemini/GEMINI.md` | gemini | `gemini --yolo -p {prompt}` | 按官方文档 |
| Qwen Code | 插入一段 | `~/.qwen/QWEN.md` | `~/.qwen/QWEN.md` | qwen | `qwen --yolo -p {prompt}` | 按官方文档 |
| OpenCode | 插入一段 | `~/.config/opencode/AGENTS.md` | `~/.config/opencode/AGENTS.md` | — | `opencode run {prompt}` | 按官方文档 |
| Amp | 插入一段 | `~/.config/amp/AGENTS.md` | `~/.config/amp/AGENTS.md` | — | `amp -x {prompt}` | 推测，待验证 |
| Factory Droid | 插入一段 | `~/.factory/AGENTS.md` | `~/.factory/AGENTS.md` | — | `droid exec {prompt}` | 推测，待验证 |
| GitHub Copilot CLI | 插入一段 | `~/.copilot/copilot-instructions.md` | `~/.copilot/copilot-instructions.md` | — | — | 推测，待验证 |
| CodeBuddy / WorkBuddy | 插入一段 | `~/.codebuddy/CODEBUDDY.md` | `~/.codebuddy/CODEBUDDY.md` | — | — | 推测，待验证 |
| Windsurf | 插入一段 | `~/.codeium/windsurf/memories/global_rules.md` | `~/.codeium/windsurf/memories/*.md` | — | — | 按官方文档 |
| Goose | 插入一段 | `~/.config/goose/.goosehints` | `~/.config/goose/.goosehints` | — | `goose run -t {prompt}` | 按官方文档 |
| Cline | 独立规则文件 | `{DOCUMENTS}/Cline/Rules/cy-feedback-memory.md` | `{DOCUMENTS}/Cline/Rules/*.md` | cline | — | 按官方文档 |
| Roo Code | 独立规则文件 | `~/.roo/rules/cy-feedback-memory.md` | `~/.roo/rules/*.md` | roo | — | 按官方文档 |
| Kilo Code | 独立规则文件 | `~/.kilocode/rules/cy-feedback-memory.md` | `~/.kilocode/rules/*.md` | kilo | — | 推测，待验证 |
| Kiro | 独立规则文件 | `~/.kiro/steering/cy-feedback-memory.md` | `~/.kiro/steering/*.md` | — | — | 按官方文档 |
| Continue | 独立规则文件 | `~/.continue/rules/cy-feedback-memory.md` | `~/.continue/rules/*.md` | continue | — | 推测，待验证 |
| GitHub Copilot（VS Code） | 独立规则文件 | `{APPSUPPORT}/Code/User/prompts/cy-feedback-memory.instructions.md` | `{APPSUPPORT}/Code/User/prompts/*.instructions.md` | — | — | 推测，待验证 |
| Aider | 独立规则文件 | `~/.cy-feedback-memory/aider-conventions.md` | `~/.aider.conf.yml` | aider | — | 按官方文档 |
| Cursor | 手动粘贴 / 项目级 AGENTS.md | — | — | — | — | 按官方文档 |
| Trae | 手动粘贴 / 项目级 AGENTS.md | — | — | — | — | 推测，待验证 |
| Zed | 手动粘贴 / 项目级 AGENTS.md | — | — | — | — | 按官方文档 |
| ChatGPT / Claude.ai / Gemini 网页或桌面聊天 | 手动粘贴 / 项目级 AGENTS.md | — | — | — | — | 按官方文档 |

路径记号：`~` 是用户目录；`{APPSUPPORT}` 在 macOS 是 `~/Library/Application Support`，Linux 是 `~/.config`，Windows 是 `%APPDATA%`；`{DOCUMENTS}` 是 `~/Documents`。

## 表里没有的智能体

1. **它能读某个全局说明文件**：在库的 `fm.config.json` 里给 `custom_agents` 加一条，格式同上表（`id / name / kind / path / detect / status`），然后运行 `sync`。
2. **它会读项目里的 AGENTS.md**：这已经是大多数编码智能体的通用约定。运行 `sync --project <项目目录>`，会写进该项目的 `AGENTS.md`；项目里已有 `CLAUDE.md`、`GEMINI.md` 的，也会一起写。
3. **它不能读本地文件**（网页聊天、手机 App）：运行 `export --all`，审查并删去敏感内容后，再将所需标准粘进自定义指令或项目说明。规则更新后要重新导出。

## 写：从哪里采集对话

| 适配器 | 位置 | 状态 |
|---|---|---|
| claude | `~/.claude/projects/*/*.jsonl` | 作者本机实测 |
| codex | `~/.codex/sessions/年/月/日/*.jsonl` | 作者本机实测 |
| gemini / qwen | `~/.gemini/tmp/*/chats/*.json`、`~/.qwen/tmp/*/chats/*.json` | 推测，待验证 |
| cline / roo / kilo | VS Code 系编辑器的 `globalStorage/<扩展>/tasks/*/api_conversation_history.json` | 推测，待验证 |
| continue | `~/.continue/sessions/*.json` | 推测，待验证 |
| aider | 在配置 `aider_projects` 里列出的项目目录下的 `.aider.chat.history.md` | 按官方文档 |
| 自定义 | 配置 `custom_sources`：`{"name","globs":[…],"format":"jsonl|json|markdown","role_field","text_field","user_value","time_field","messages_field","user_line_regex"}` | — |
| 收件箱、直接写入 | 任何智能体用 `fm remember` / `fm add`，不依赖对话记录格式 | — |

对话记录存在数据库里、不是文本文件的智能体（例如 Cursor），没法直接采集，有两种补法：
- 让它在用户说「记下来」时调用 `remember`；
- 用它自带的导出功能导出对话，再放进 `custom_sources`。

## 采集范围和隐私

`fm scan` 只读盘点，列出路径、文件大小和对话记录数量。实际采集范围在库的 `fm.config.json` 中设置。

| 设置 | 作用 |
|---|---|
| `fm collect --agents claude-code,codex` | 只在这次汇集中读取指定智能体的规则和记忆。 |
| `maintenance.collect_native_each_run: false` | 关闭每次维护时自动汇集规则和记忆。 |
| `privacy.harvest_agents: ["claude", "codex"]` | 只采集指定的内置对话来源，值用上表的适配器名。设为 `[]` 可停用全部内置对话来源。 |
| `custom_sources: []` | 停用自定义对话来源。自定义来源独立于 `privacy.harvest_agents`。 |
| `privacy.redact_patterns` | 为调用脱敏的文本额外指定正则。常见密钥格式也会在这些文本中自动脱敏。 |

对话摘录、汇集得到的规则正文和脚本转换的正文会经过脱敏。导入原件、手写 Markdown、路径和直接写入的日志可能包含原文。云端智能体整理材料时，读到的文件内容可能被传给其服务。涉及敏感内容时，先筛选可读材料，再选择模型与存储位置。

`fm sync --agents` 只限制这次同步。常规维护完成时会向自动检测到的智能体刷新路由块。路由块包含核心约定、分支名和库路径，把它同步进项目配置后也可能随该项目一起提交。
