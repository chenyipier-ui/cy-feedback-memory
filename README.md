# cy-feedback-memory · 所有 AI 智能体共用的反馈经验库

你在 Claude 里纠正过的事，换到 Codex 又得说一遍；在 Cursor 里定下的规矩，Gemini 不知道。
这个 skill 给本机所有智能体搭一个**共用、纯 Markdown、省 token** 的经验库：谁都能读写，轮流值班整理。

作者 **CY** · [English](#english)

## 它做什么

| | |
|---|---|
| 盘点 | 只读列出本机智能体、规则和记忆文件、对话记录数量，以及项目里的 AGENTS.md / CLAUDE.md / .cursorrules |
| 自动整理归档 | 智能体提炼、去重、处理冲突并写入分支。源文件留档，每次维护前保存快照 |
| 收你的资料 | 把文档、表格、本地网页文件和图片放进 `投放区/`。支持的格式由脚本转换，其余交给能读取相应格式的智能体 |
| 定时整理（混合） | **每周额度刷新前 1 小时**固定跑一次，用本来要作废的剩余额度；平日积压超过阈值就提前跑；判断全由脚本做，没新内容不调用模型 |
| 每轮自动判断 | 每个智能体在每轮回复末尾自查一次：有纠正、拍板、认可、已验证的结论才写一条；漏的由维护从对话记录补收 |
| 多智能体协作 | 写入加锁、去重计数、旧版自动归档、接力日志可追溯；同一轮维护只会有一个智能体真正执行 |

接入表覆盖 Claude Code、Codex、Gemini CLI、Qwen Code、OpenCode、Cursor、Windsurf、Cline、Roo、Kilo、Kiro、Continue、Goose、Aider、Trae、Copilot 等 20 余种。核心工具使用 Python 3.8+ 标准库，支持 macOS、Linux、Windows。[接入方式与验证状态](references/agents.md)

## 安装

在 macOS 或 Linux 终端运行以下命令。

```bash
git clone https://github.com/chenyipier-ui/cy-feedback-memory.git
cd cy-feedback-memory
python3 install.py
```

Windows 用 PowerShell。

```powershell
git clone https://github.com/chenyipier-ui/cy-feedback-memory.git
cd cy-feedback-memory
py .\install.py
```

`install.py` 会把 skill 复制进检测到且安装器支持的 skills 目录，并立即只读盘点。用 `--agents claude,codex` 指定安装目标，用 `--no-scan` 跳过盘点。也可以手动复制文件夹，首次触发 skill 时再盘点。

**装在一个智能体上就够**，其余智能体通过写进它们全局配置的「路由块」读写同一个库。

然后对它说：

> 帮我建一个经验库

默认流程会接入检测到的智能体、汇集规则和记忆、回溯近 30 天对话，再安排定期维护。想先用几条公开或虚构内容试用，可在建库前说明范围和维护方式。[首次建库流程](SKILL.md) · [采集范围设置](references/agents.md#采集范围和隐私)

## 虚构示例

| 场景 | 用户反馈 | 在库里怎么记 |
|---|---|---|
| 软件发布 | “以后生产发版说明要写回滚条件，内部预览不用。” | 发版分支新增标准，保留内部预览的例外。 |
| 餐饮运营 | “周末晚市试一周等位短信，先看看能不能减少过号。” | 门店运营分支记为待验证，保留时段与试行期限。 |
| 法务协作 | “这个供应商的合同要同时标原条款编号和修改后的编号。” | 写进该虚构供应商的分支，供后续审查复用。 |

## 省 token 的设计

- **分三层按需读**：核心约定（≤12 条，每次加载）→ 路由表找到对上号的分支 → 用 `fm read` 只读该分支的「标准」和「待验证」→ 要来历再点详情。分支太大就按主题原样拆成子分支（`fm move`），不删减、不改写。
- **写入前无需读全库**：`fm add` 自动去重、加锁，并归档被同键替换的条目。
- **脚本预筛**：对话记录、收件箱、投放区先用脚本过滤（零 token），只有疑似反馈的发言交给模型。
- **没新内容不调模型**：系统定时器每小时调一次脚本，脚本判断该不该维护、有没有新内容，都没有就直接结束。
- **用作废额度**：固定维护放在额度刷新前 1 小时；平日只有积压过多才提前。

## 库长什么样

```
核心约定.md        每次对话加载（≤12 条）
路由表.md          自动生成，已同步进各智能体全局配置
通用/<分支>/       换任何任务都成立的
任务类型/<分支>/   某一类活
对象/<分支>/       某个对象（客户、项目、仓库……）
  经验.md   标准 / 待验证 / 资料
  条目/     一条一个文件，写来历和例外
  文档/     整份保留的参考
投放区/            待处理的资料
收件箱.md          拿不准放哪的反馈
归档/              被替换的旧结论
.fm/               工具、锁、接力日志、快照
```

目录名都可以在 `fm.config.json` 里改。完整格式见 [references/format-spec.md](references/format-spec.md)。

## 常用命令

```bash
fm() {
  python3 "$HOME/.cy-feedback-memory/tool/scripts/fm.py" "$@"
}

fm scan
fm doctor
fm add --branch 协作方式 --text "内部预览的标题加上预览标记" --words "以后内部预览的标题标一下" --agent claude-code
fm schedule --reset "thu 09:00" --tz Asia/Shanghai --curator claude-code --install
fm tick --dry
```

示例设置在周四 8 点维护。Python 缺少 `zoneinfo` 或指定的 IANA 时区数据时，工具使用系统本地时区。此情况可能发生在 Python 3.8 或部分 Windows 环境。要停止维护并清理路由块，分别运行 `fm schedule --off` 和 `fm sync --remove`。经验库文件夹会保留。

## 平台与测试

| 平台 | 定时器 | 错过补跑 |
|---|---|---|
| macOS | launchd（`~/Library/LaunchAgents`） | 唤醒后补跑 |
| Linux | systemd 用户定时器；没有则 crontab | systemd 开机补跑；crontab 下一小时接上 |
| Windows | 计划任务（`schtasks`，pythonw 不弹窗） | StartWhenAvailable 开机补跑 |

`python tests/smoke.py` 在临时目录里走一遍建库 → 写入 → 定时 → 唤起值班智能体；`python tests/regress.py` 逐条验收外部审核复现过的问题（重复安装不丢旧版、同名资料不覆盖、正负号不被去重、别人不能提交你的维护、定时器登记失败如实报、分批续跑不漏、「这批先给我看」跨智能体生效）；`.github/workflows/test.yml` 在三平台 × Python 3.8/3.12 上跑它（Linux、Windows 会真实注册再删除定时器）。

## 隐私

Python 脚本在本机采集和预筛。云端智能体整理材料时，读到的内容可能进入其服务。NAS 或同步盘也会接收你存入该路径的内容。

常见密钥格式的自动脱敏覆盖对话摘录和部分转换材料。导入原件、手写 Markdown 和直接写入的反馈日志可能保留原文。工作库、投放区、原件和日志应保存在受控位置。敏感资料请先人工筛选，必要时使用本地模型和私有存储。[设置采集范围与脱敏](references/agents.md#采集范围和隐私)

## 已知限制

- 仅网页端的智能体无法直接读本机文件。向其提供规则前，请先筛选 `fm export --all` 的输出。
- 整理全由智能体自主判断，偶尔会记错或放错分支：每次维护前有快照，`维护记录.md` 写清改了什么。
- Linux 上 systemd 用户定时器默认只在登录时运行，要无人登录也跑需 `loginctl enable-linger`。
- 额度刷新时间各家不同，需要你从产品的用量页面告诉它；产品改了刷新规则要重设。
- Claude Code 和 Codex 已在作者本机实测。其余接入按官方文档或公开信息整理，欢迎提交修正。[接入表](references/agents.md)

更多问答见 [references/faq.md](references/faq.md)。

---

## English

A shared Markdown library for AI agent feedback and working standards. Install the skill in one agent and ask it to set up a feedback library. It can scan for existing rule and memory files, curate feedback, and schedule a weekly maintenance run one hour before your quota resets. It can also run early when the backlog crosses a threshold. The core tool uses the Python 3.8+ standard library. Use `fm init --lang en` with your chosen library path for an English layout. Collection runs locally. A cloud-based curator may send the material it reads to its model provider. Review sensitive material before import or curation. [Setup](SKILL.md) · [Data sources and privacy](references/agents.md#采集范围和隐私)

## License

© 2026 CY。采用 **CY 有限使用许可**。允许个人和商业使用，包括用它完成接单工作。再分发、打包出售须取得 CY 的书面授权。[许可全文](LICENSE)
