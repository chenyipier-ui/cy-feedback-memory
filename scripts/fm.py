#!/usr/bin/env python3
"""cy-feedback-memory：所有智能体接力维护的反馈经验库。

只用 Python 标准库（3.8+），macOS / Linux / Windows 通用。

建库与接入
  init      建库（--lang zh|en），并把本工具复制进库里（.fm/tool），各智能体都用库里这一份
  join      让某个智能体加入接力：写入路由块、登记到成员表
  sync      把「核心约定 + 路由表」写进各智能体的全局配置；--project 写项目级 AGENTS.md
  scan      建库前的只读盘点：本机有哪些智能体、它们留下的规则/记忆/对话、散落的规则文件（不写任何东西）
  doctor    体检：库、各智能体接入、对话记录、自带记忆、维护设置，并给出建议
  upgrade   用当前 skill 的新版本更新库里的工具
写入（任何智能体）
  add       按规范写一条标准/待验证/资料（自动加锁、去重、计数、归档旧版、写日志）
  remember  拿不准放哪时，往收件箱记一条原话
  place     把投放区的文件归到分支的文档目录，或标记为已处理
  journal   看接力日志：谁、什么时候、写了什么
维护（每轮一个智能体值班，默认每周一次）
  maint     start / finish / check：维护的开始与收尾（含格式转换、采集、检查、同步、提交）
  collect   首次汇集：各智能体已有的规则和记忆 → 投放区；--days N 回溯对话
  convert   把库里非 MD 的文件转成 MD，原件另存
  harvest   从各智能体对话记录和收件箱抽取材料（脚本预筛，零 token）
  lint      检查是否合规
  schedule  设置定期维护：--weekday 每周固定一次（建议额度刷新前 1 小时），平日积压超阈值提前；--install 装系统定时器
  tick      系统定时器每小时调用：零 token 判断该不该维护，该才唤起值班智能体
其他
  export / import / status / due
"""
import argparse, glob, hashlib, json, os, platform, re, shutil, subprocess, sys, time, zipfile
from datetime import datetime, timedelta, timezone

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SKILL, "scripts"))
import fm_convert  # noqa: E402

DATA = os.path.join(SKILL, "data")
VERSION = open(os.path.join(SKILL, "VERSION"), encoding="utf-8").read().strip()
FORMAT = 1
HOME = os.path.expanduser("~")
USERDIR = os.path.join(HOME, ".cy-feedback-memory")
POINTER = os.path.join(USERDIR, "config.json")
OFFLINE = os.path.join(USERDIR, "offline-inbox.md")
CONFIG = "fm.config.json"
MARK_S = "<!-- cy-feedback-memory:start · 由 cy-feedback-memory 生成，请改库里的核心约定/分支，不要直接改这里 -->"
MARK_E = "<!-- cy-feedback-memory:end -->"
NOTHING = "NOTHING-NEW"
INBOX_LINE = "<!-- ↓ 新条目追加在这条线下面 / append new items below ↓ -->"
TOOL_PARTS = ["scripts", "data", "templates", "references", "VERSION"]
SKILL_DIRS = ["~/.claude/skills", "~/.codex/skills", "~/.agents/skills", "~/.gemini/skills", "~/.config/opencode/skill", "~/.qwen/skills", "~/.cursor/skills", "~/.kiro/skills"]


# ---------------------------------------------------------------- 基础
def jload(p, default=None):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def jsave(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + f".tmp{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def read(p, default=""):
    try:
        with open(p, encoding="utf-8", errors="ignore") as f:
            return f.read()
    except OSError:
        return default


def write(p, s):
    os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    tmp = p + f".tmp{os.getpid()}"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(s)
    os.replace(tmp, p)


def append(p, s):
    os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    with open(p, "a", encoding="utf-8", newline="\n") as f:
        f.write(s)


LAYOUTS = jload(os.path.join(DATA, "layouts.json"))
SECTIONS = LAYOUTS["sections"]
KW = jload(os.path.join(DATA, "keywords.json"))
PLAT = {"Darwin": "mac", "Windows": "win"}.get(platform.system(), "linux")


def expand(p):
    if not p:
        return p
    appsupport = {"mac": os.path.join(HOME, "Library", "Application Support"),
                  "win": os.environ.get("APPDATA", os.path.join(HOME, "AppData", "Roaming")),
                  "linux": os.path.join(HOME, ".config")}[PLAT]
    p = p.replace("{APPSUPPORT}", appsupport).replace("{DOCUMENTS}", os.path.join(HOME, "Documents"))
    return os.path.normpath(os.path.expanduser(p))


def now():
    return datetime.now(timezone.utc)


def today():
    return datetime.now().strftime("%Y-%m-%d")


def stamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def T(lang_or_lib, zh, en):
    lang = lang_or_lib if isinstance(lang_or_lib, str) else lang_or_lib.lang
    return zh if lang == "zh" else en


def parse_ts(ts):
    try:
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(ts / (1000 if ts > 1e12 else 1), timezone.utc)
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError, OSError):
        return None


def who(a):
    return (getattr(a, "agent", None) or os.environ.get("CY_FM_AGENT") or "unknown").strip()


def vtuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or "0"))


# ---------------------------------------------------------------- 库
class Lib:
    def __init__(self, root):
        self.root = os.path.abspath(os.path.expanduser(root))
        self.cfg = jload(os.path.join(self.root, CONFIG))
        if not self.cfg:
            sys.exit(f"不是 cy-feedback-memory 库（缺 {CONFIG}）：{self.root}\nNot a library: {self.root}")
        if self.cfg.get("format_version", 1) > FORMAT:
            sys.exit(f"这个库的格式（v{self.cfg['format_version']}）比本工具新，请先升级 skill。\nLibrary format is newer than this tool; please upgrade.")
        self.lang = self.cfg.get("lang", "zh")
        self.L = self.cfg["layout"]
        for k, v in LAYOUTS[self.lang].items():
            self.L.setdefault(k, v)

    def p(self, *parts):
        return os.path.join(self.root, *parts)

    @property
    def fm(self):
        return self.p(".fm")

    @property
    def drop(self):
        return self.p(self.L["drop"])

    @property
    def tool(self):
        local = os.path.join(LOCAL_TOOL, "scripts", "fm.py")
        return local if os.path.exists(local) else self.p(".fm", "tool", "scripts", "fm.py")

    def fmcmd(self):
        py = self.cfg.get("python") or ("py" if PLAT == "win" else "python3")
        return f'{py} "{self.tool}"'

    def layer_dir(self, layer):
        return self.p(self.L["layers"][layer])

    def branches(self):
        out = []
        for layer in ("general", "tasks", "subjects"):
            d = self.layer_dir(layer)
            if not os.path.isdir(d):
                continue
            for name in sorted(os.listdir(d)):
                bf = os.path.join(d, name, self.L["branch_file"])
                if name.startswith(("_", ".")) or not os.path.isfile(bf):
                    continue
                meta = front_matter(read(bf))
                meta.setdefault("name", name)
                out.append({"layer": layer, "dir": os.path.join(d, name), "file": bf, "meta": meta})
        return out

    def find_branch(self, name):
        n = (name or "").strip().lower()
        for b in self.branches():
            if n in (str(b["meta"].get("name", "")).lower(), os.path.basename(b["dir"]).lower()):
                return b
        return None


def find_root(arg, offline_ok=False):
    root = arg or os.environ.get("CY_FM_ROOT") or (jload(POINTER, {}) or {}).get("root")
    if not root:
        sys.exit("找不到经验库：先运行 init，或用 --root / 环境变量 CY_FM_ROOT 指定。\nNo library found: run init first, or pass --root / set CY_FM_ROOT.")
    if not os.path.exists(os.path.join(os.path.expanduser(root), CONFIG)):
        if offline_ok:
            return None
        sys.exit(f"经验库不可用（路径不存在或网络盘没挂上）：{root}\nLibrary unreachable: {root}")
    return Lib(root)


def front_matter(text):
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    meta = {}
    if not m:
        return meta
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        v = v.strip()
        if v.startswith("[") and v.endswith("]"):
            v = [x.strip().strip("'\"") for x in re.split(r"[,，、]", v[1:-1]) if x.strip()]
        meta[k.strip()] = v
    return meta


def section_kind(heading):
    return next((k for k, names in SECTIONS.items() if any(heading.startswith(n) for n in names)), None)


def sections(text):
    out, cur = {}, None
    for line in text.splitlines():
        if line.startswith("## "):
            cur = section_kind(line[3:].strip())
            if cur:
                out.setdefault(cur, [])
            continue
        if cur and line.strip():
            out[cur].append(line)
    return out


def is_item(line):
    return bool(re.match(r"^\s*([-*]|\d+[.)])\s", line))


def standard_lines(text):
    return [l for l in sections(text).get("standards", []) if is_item(l)]


def core_items(lib):
    text = re.sub(r"<!--.*?-->", "", read(lib.p(lib.L["core"])), flags=re.S)
    return [l for l in text.splitlines() if l.strip() and not l.startswith(("#", ">"))]


# ---------------------------------------------------------------- 锁、接力日志、成员表
class Lock:
    """跨进程、跨机器（共享盘）可用的文件锁：O_EXCL 创建，超时视为残留。"""

    def __init__(self, lib, agent, name="lock", ttl=600, wait=30):
        self.path, self.agent, self.ttl, self.wait, self.lib = os.path.join(lib.fm, name), agent, ttl, wait, lib

    def holder(self):
        """当前持锁者；锁文件超过 ttl 没更新才算残留（按文件时间判断，避免刚建好还没写内容时被误删）。"""
        try:
            age = time.time() - os.path.getmtime(self.path)
        except OSError:
            return None
        if age > self.ttl:
            return None
        return jload(self.path) or {"who": "?", "at": now().isoformat()}

    def acquire(self, wait=None):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        deadline = time.time() + (self.wait if wait is None else wait)
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, json.dumps({"who": self.agent, "pid": os.getpid(), "at": now().isoformat()}).encode())
                os.close(fd)
                return True
            except FileExistsError:
                if self.holder() is None and os.path.exists(self.path):
                    try:
                        os.remove(self.path)  # 过期残留
                    except OSError:
                        pass
                    continue
                if time.time() >= deadline:
                    return False
                time.sleep(0.5)

    def release(self):
        try:
            os.remove(self.path)
        except OSError:
            pass

    def __enter__(self):
        if not self.acquire():
            h = self.holder() or {}
            sys.exit(T(self.lib, f"库正在被「{h.get('who')}」写入，稍后再试。", f"Library is locked by {h.get('who')}; retry later."))
        return self

    def __exit__(self, *exc):
        self.release()


def journal(lib, agent, op, target="", detail="", words=""):
    row = {"at": now().isoformat(), "agent": agent, "op": op, "target": target, "detail": detail[:300]}
    if words:
        row["words"] = words[:300]
    append(os.path.join(lib.fm, "journal.jsonl"), json.dumps(row, ensure_ascii=False) + "\n")
    roster_touch(lib, agent, last_write=now().isoformat())


def roster_touch(lib, agent, **fields):
    if not agent or agent == "unknown":
        return
    p = os.path.join(lib.fm, "roster.json")
    r = jload(p, {}) or {}
    e = r.setdefault(agent, {"joined": now().isoformat()})
    e.update(fields)
    jsave(p, r)


def journal_since(lib, since):
    out = []
    p = os.path.join(lib.fm, "journal.jsonl")
    if not os.path.exists(p):
        return out
    for line in open(p, encoding="utf-8", errors="ignore"):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        t = parse_ts(d.get("at"))
        if t and t >= since:
            out.append(d)
    return out


# ---------------------------------------------------------------- 模板与建库
def fill(tpl, L, extra=None):
    rep = {"{CORE}": L["core"], "{ROUTES}": L["routes"], "{INDEX}": L["index"], "{INBOX}": L["inbox"],
           "{GENERAL}": L["layers"]["general"], "{TASKS}": L["layers"]["tasks"], "{SUBJECTS}": L["layers"]["subjects"],
           "{ARCHIVE}": L["archive"], "{BRANCH}": L["branch_file"], "{LOG}": L["log_file"], "{ENTRIES}": L["entries_dir"],
           "{DROP}": L["drop"], "{DOCS}": L["docs_dir"], "{MAINTLOG}": L["maint_log"],
           "{DATE}": today(), "{VERSION}": VERSION}
    rep.update(extra or {})
    for k, v in rep.items():
        tpl = tpl.replace(k, v)
    return tpl


def tpl_path(lang, name):
    return os.path.join(SKILL, "templates", lang, name)


LOCAL_TOOL = os.path.join(USERDIR, "tool")


def embed_tool(root, force=False, dst=None):
    """把工具复制进库（给其他电脑加入用）或本机（~/.cy-feedback-memory/tool，网络盘掉线时也能用）。"""
    dst = dst or os.path.join(root, ".fm", "tool")
    cur = read(os.path.join(dst, "VERSION")).strip()
    if os.path.abspath(dst) == os.path.abspath(SKILL):
        return "self"
    if cur and vtuple(cur) >= vtuple(VERSION) and not force:
        return "kept"
    tmp = dst + ".new"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    for part in TOOL_PARTS:
        s = os.path.join(SKILL, part)
        if os.path.isdir(s):
            shutil.copytree(s, os.path.join(tmp, part), ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
        elif os.path.exists(s):
            shutil.copy2(s, os.path.join(tmp, part))
    shutil.rmtree(dst, ignore_errors=True)
    os.replace(tmp, dst)
    return "updated" if cur else "installed"


def make_branch(lib, layer, name, triggers, summary):
    d = os.path.join(lib.layer_dir(layer), name)
    if os.path.exists(os.path.join(d, lib.L["branch_file"])):
        return lib.find_branch(name)
    trig = ", ".join(triggers) if isinstance(triggers, list) else (triggers or name)
    ext = {"{NAME}": name, "{TRIGGERS}": trig, "{SUMMARY}": summary or name}
    write(os.path.join(d, lib.L["branch_file"]), fill(read(tpl_path(lib.lang, "branch/branch.md")), lib.L, ext))
    write(os.path.join(d, lib.L["log_file"]), fill(read(tpl_path(lib.lang, "branch/log.md")), lib.L, ext))
    os.makedirs(os.path.join(d, lib.L["entries_dir"]), exist_ok=True)
    return lib.find_branch(name)


def cmd_init(a):
    root = os.path.abspath(os.path.expanduser(a.root))
    existing = jload(os.path.join(root, CONFIG))
    if existing and not a.force:
        jsave(POINTER, {"root": root})
        embed_tool(root, dst=LOCAL_TOOL)
        print(T(existing.get("lang", a.lang), f"这里已经有库了，已把本机指向它：{root}\n接下来运行 join --agent <你的名字> 加入接力。",
                f"Library already exists; this machine now points to it: {root}\nRun join --agent <id> next."))
        return
    cfg = existing or {
        "format_version": FORMAT, "lang": a.lang, "layout": json.loads(json.dumps(LAYOUTS[a.lang])),
        "limits": {"core_lines": 12, "standards_per_branch": 15, "route_block_chars": 4000, "repeat_promote_at": 3},
        "agents": "auto", "projects": [], "custom_agents": [], "custom_sources": [], "aider_projects": [],
        "extra_feedback_words": [], "python": "py" if PLAT == "win" else "python3",
        "harvest": {"first_run_days": 1, "max_chars_per_message": 600, "context_chars": 120, "max_material_chars": 60000},
        "maintenance": {"enabled": False, "at": "12:00", "tz": None, "curator": None, "mode": None, "collect_native_each_run": True},
        "privacy": {"redact_patterns": [], "harvest_agents": "all"},
    }
    L = cfg["layout"]
    for d in list(L["layers"].values()) + [L["archive"], L["drop"], ".fm"]:
        os.makedirs(os.path.join(root, d), exist_ok=True)
    jsave(os.path.join(root, CONFIG), cfg)

    def put(rel, tpl, extra=None):
        dst = os.path.join(root, rel)
        if not os.path.exists(dst):
            write(dst, fill(read(tpl_path(a.lang, tpl)), L, extra))

    put(L["protocol"], "AGENTS.md")
    put(L["core"], "core.md")
    put(L["inbox"], "inbox.md")
    put(L["maint_log"], "maint-log.md")
    put(os.path.join(L["drop"], T(a.lang, "说明.txt", "README.txt")), "drop-readme.txt")
    tpl_dir = os.path.join(L["layers"]["tasks"], "_template")
    ph = {"{NAME}": T(a.lang, "<分支名>", "<branch name>"), "{TRIGGERS}": T(a.lang, "触发词1, 触发词2", "keyword1, keyword2"), "{SUMMARY}": T(a.lang, "这类活是什么", "what this branch covers")}
    put(os.path.join(tpl_dir, L["branch_file"]), "branch/branch.md", ph)
    put(os.path.join(tpl_dir, L["log_file"]), "branch/log.md", ph)
    put(os.path.join(tpl_dir, L["entries_dir"], "example-entry.md"), "branch/entry.md")
    write(os.path.join(root, ".gitignore"), ".fm/lock\n.fm/maint.lock\n.fm/*.tmp*\n.fm/snapshots/\n.fm/pending.json\n")
    lib = Lib(root)
    make_branch(lib, "general", T(a.lang, "协作方式", "working-style"), [T(a.lang, "任何任务", "any task")], T(a.lang, "你希望 AI 怎么干活、怎么说话（换任何任务都成立）", "How you want AI to work and talk, across all tasks"))
    embed_tool(root)
    embed_tool(root, dst=LOCAL_TOOL)
    jsave(POINTER, {"root": root})
    roster_touch(lib, who(a), role="creator")
    journal(lib, who(a), "init", root)
    regen(lib)
    print(T(a.lang, f"已建库：{root}\n工具已放进库里：{lib.tool}\n下一步：sync 接入本机智能体，schedule 设置定期维护，collect 汇集各智能体已有的经验。",
            f"Library created: {root}\nTool embedded at {lib.tool}\nNext: sync, schedule, collect."))


def cmd_upgrade(a):
    lib = find_root(a.root)
    r = embed_tool(lib.root, force=a.force)
    embed_tool(lib.root, force=a.force, dst=LOCAL_TOOL)
    journal(lib, who(a), "upgrade", VERSION, r)
    print({"kept": T(lib, f"库里的工具已经是最新（{read(os.path.join(lib.fm, 'tool', 'VERSION')).strip()}）。", "Already up to date."),
           "self": T(lib, "正在用库里的工具本身运行，要升级请用新版 skill 运行 upgrade。", "Run upgrade from the newer skill copy.")}.get(r, T(lib, f"库里的工具已更新到 {VERSION}。", f"Embedded tool updated to {VERSION}.")))
    regen(lib)


# ---------------------------------------------------------------- 路由
def route_block(lib):
    fm = lib.fmcmd()
    b = [f"## {T(lib, '核心约定', 'Core conventions')}", ""] + (core_items(lib) or [T(lib, "（还没有）", "(none yet)")]) + [""]
    b += [f"## {T(lib, '经验库（所有智能体共用，接力维护）', 'Feedback memory (shared by all agents)')}", ""]
    b += [T(lib, f"根目录 `{lib.root}`；完整规则 `{lib.p(lib.L['protocol'])}`；工具 `{fm}`（下称 fm）。",
            f"Root `{lib.root}`; rules `{lib.p(lib.L['protocol'])}`; tool `{fm}` (\"fm\" below)."),
          T(lib, "- 读：只进对上号的分支，用 `fm read 分支名` 读（只输出标准和待验证，比打开整份文件省）；要来历再点详情。不要全量扫描。",
            "- Read: only matching branches, via `fm read NAME` (prints Standards + Candidates only — cheaper than opening the file). Never scan everything."),
          T(lib, "- 写（每轮回复末尾自查一次，没有就跳过、不读库）：本轮出现纠正/否定/拍板/认可/「以后都这样」，或经验证的可复用结论 → `fm add --branch 分支 --text \"一句话结论（适用条件）\" --words \"用户原话\" --scope always|candidate --agent 你的名字`（用户没确认的用 candidate；只对某个客户/项目成立的写进它自己的分支）；不知道放哪就 `fm remember \"原话\" --agent 你的名字`。一次性指令不记。漏记的由定期维护从对话记录补收。不要手改分支结构。",
            "- Write (self-check once at the end of each turn; nothing → skip, don't open the library): a correction / rejection / decision / praise / \"from now on\", or a verified reusable conclusion → `fm add --branch NAME --text \"one-line rule (when it applies)\" --words \"user's words\" --scope always|candidate --agent YOUR_NAME` (unconfirmed → candidate; client/project-specific → that subject's branch); unsure where → `fm remember \"words\" --agent YOUR_NAME`. Skip one-off commands. Misses are picked up from transcripts at maintenance. Don't restructure branch files by hand."),
          T(lib, "- 用户说「这批先给我看」→ `fm hold on`。之后用户看完说可以 → `fm hold apply`；说这批不要 → `fm hold discard`；看清单 → `fm hold status`。",
            "- User says \"show me this batch first\" → `fm hold on`; on their answer: `fm hold apply` (yes) / `fm hold discard` (no); list with `fm hold status`."),
          T(lib, f"- 文件：用户给的资料（任何格式）放进 `{lib.drop}`，定期维护会转成 MD 并归档。",
            f"- Files: put user-provided material (any format) into `{lib.drop}`; scheduled maintenance converts and files it."),
          "", T(lib, "分支（名称（触发词）→ 文件）：", "Branches (name (triggers) → file):")]
    for br in lib.branches():
        trig = br["meta"].get("triggers") or []
        trig = "、".join(trig) if isinstance(trig, list) else str(trig)
        b.append(f"- {br['meta'].get('name')}（{trig}）→ `{br['file']}`")
    return "\n".join(b).strip()


def regen(lib):
    block = route_block(lib)
    write(lib.p(lib.L["routes"]), T(lib, "# 路由表（自动生成，别手改）\n\n> 改分支的 name/triggers 或核心约定后运行 sync。\n\n", "# Routes (generated)\n\n") + block + "\n")
    idx = [T(lib, "# 索引（自动生成）", "# Index (generated)"), ""]
    for layer in ("general", "tasks", "subjects"):
        idx.append(f"## {lib.L['layers'][layer]}")
        for br in [x for x in lib.branches() if x["layer"] == layer]:
            n = len(standard_lines(read(br["file"])))
            rel = os.path.relpath(br["file"], lib.root).replace(os.sep, "/")
            idx.append(f"- [{br['meta'].get('name')}]({rel}) — {br['meta'].get('summary', '')}（{T(lib, '标准', 'standards')} {n}）")
        idx.append("")
    roster = jload(os.path.join(lib.fm, "roster.json"), {}) or {}
    if roster:
        idx.append(f"## {T(lib, '接力成员', 'Agents in the relay')}")
        for k, v in sorted(roster.items()):
            idx.append(f"- {k}：{T(lib, '加入', 'joined')} {v.get('joined', '')[:10]}，{T(lib, '最近写入', 'last write')} {str(v.get('last_write', '-'))[:10]}" + (f"，{v['role']}" if v.get("role") else ""))
    write(lib.p(lib.L["index"]), "\n".join(idx) + "\n")
    return block


def registry(lib):
    return jload(os.path.join(DATA, "agents.json"))["agents"] + list(lib.cfg.get("custom_agents", []))


def mem_globs(ag):
    """agents.json 的 memory：普通 glob，或 a|b 表示取第一个存在的。"""
    for g in ag.get("memory", []):
        alts = g.split("|")
        yield next((x for x in alts if glob.glob(expand(x))), alts[0])


def detected(ag):
    return any(os.path.exists(expand(d)) for d in ag.get("detect", []))


def put_block(path, block, remove=False, check=False):
    old = read(path)
    pat = re.compile(re.escape(MARK_S) + ".*?" + re.escape(MARK_E) + r"\n?", re.S)
    if remove:
        new = pat.sub("", old)
    else:
        full = f"{MARK_S}\n{block}\n{MARK_E}\n"
        new = pat.sub(lambda m: full, old) if pat.search(old) else (old.rstrip() + "\n\n" + full if old.strip() else full)
    if new == old:
        return "same"
    if check:
        return "differs"
    if remove and not new.strip() and os.path.exists(path):
        os.remove(path)
        return "removed"
    write(path, new)
    return "removed" if remove else "written"


def sync_agents(lib, want="auto", projects=(), remove=False, check=False):
    if not remove and not check:
        embed_tool(lib.root, dst=LOCAL_TOOL)  # 本机备一份工具，路由块指向它
    block = regen(lib)
    done, manual, seen = [], [], set()
    for ag in registry(lib):
        if want != "auto" and ag["id"] not in want:
            continue
        if want == "auto" and not detected(ag):
            continue
        if ag["kind"] == "manual":
            manual.append(ag)
            continue
        path = expand(ag["path"])
        if path in seen:
            continue
        seen.add(path)
        if ag["kind"] == "block":
            r = put_block(path, block, remove, check)
        else:
            body = ag.get("header", "") + f"{MARK_S}\n{block}\n{MARK_E}\n"
            if remove:
                r = "removed" if os.path.exists(path) else "same"
                if r == "removed" and not check:
                    os.remove(path)
            elif read(path) == body:
                r = "same"
            elif check:
                r = "differs"
            else:
                write(path, body)
                r = "written"
        done.append((ag, path, r))
    for d in list(projects) + list(lib.cfg.get("projects", [])):
        d = os.path.abspath(os.path.expanduser(d))
        for fn in ["AGENTS.md"] + [f for f in ("CLAUDE.md", "GEMINI.md") if os.path.exists(os.path.join(d, f))]:
            done.append(({"id": "project", "status": "-"}, os.path.join(d, fn), put_block(os.path.join(d, fn), block, remove, check)))
    return block, done, manual


def print_sync(lib, block, done, manual):
    if len(block) > lib.cfg["limits"]["route_block_chars"]:
        print(T(lib, f"⚠ 路由块 {len(block)} 字符，超过上限：每次对话都会加载，考虑精简核心约定或合并分支。", f"⚠ Route block is {len(block)} chars."))
    for ag, path, r in done:
        word = {"same": T(lib, "一致", "up to date"), "written": T(lib, "已写入", "written"), "removed": T(lib, "已移除", "removed"), "differs": T(lib, "不一致", "differs")}[r]
        print(f"[{word}] {ag['id']} ({ag.get('status')}) → {path}")
        if ag.get("note") and r == "written":
            print("    " + ag["note"])
    for ag in manual:
        print(T(lib, f"[需手动] {ag['name']}：{ag.get('note', '')}", f"[manual] {ag['name']}: {ag.get('note', '')}"))


def cmd_sync(a):
    lib = find_root(a.root)
    want = a.agents.split(",") if a.agents else (lib.cfg.get("agents") if isinstance(lib.cfg.get("agents"), list) else "auto")
    block, done, manual = sync_agents(lib, want, a.project or [], a.remove, a.check)
    print_sync(lib, block, done, manual)
    if not done and not manual:
        print(T(lib, "没检测到已安装的智能体。用 --agents 指定，或 --project 写项目级 AGENTS.md。", "No agents detected. Use --agents or --project."))
    for ag, _, r in done:
        if r == "written" and ag["id"] != "project":
            roster_touch(lib, ag["id"], last_sync=now().isoformat())
    if a.check and any(r == "differs" for _, _, r in done):
        sys.exit(1)


def cmd_join(a):
    lib = find_root(a.root)
    ag = next((x for x in registry(lib) if x["id"] == a.agent), None)
    if not ag:
        sys.exit(T(lib, f"登记表里没有「{a.agent}」。可选：{', '.join(x['id'] for x in registry(lib))}\n表里没有的，在 fm.config.json 的 custom_agents 里加一条再 join。",
                   f"Unknown agent {a.agent}. Add it to custom_agents in fm.config.json."))
    block, done, manual = sync_agents(lib, [a.agent], a.project or [])
    print_sync(lib, block, done, manual)
    roster_touch(lib, a.agent, name=ag["name"], last_sync=now().isoformat(), harvest=ag.get("harvest"), headless=ag.get("headless"))
    journal(lib, a.agent, "join", ag["name"])
    regen(lib)
    roster = jload(os.path.join(lib.fm, "roster.json"), {}) or {}
    print(T(lib, f"「{ag['name']}」已加入接力。现在的成员：{', '.join(sorted(roster))}", f"{ag['name']} joined. Members: {', '.join(sorted(roster))}"))
    m = lib.cfg.get("maintenance", {})
    print(T(lib, f"定期维护：{'开，由 ' + str(m.get('curator')) + ' 在 ' + str(m.get('tz') or '本机时区') + ' ' + (WEEKDAYS_ZH[WEEKDAYS.index(m['weekday'])] if m.get('weekday') else '每天') + ' ' + str(m.get('at')) + ' 做' if m.get('enabled') else '还没设置（运行 schedule）'}",
            f"Maintenance: {'on' if m.get('enabled') else 'not set (run schedule)'}"))


# ---------------------------------------------------------------- 写入
def norm(s):
    s = re.sub(r"[〔\[](?:键|key):[^〕\]]*[〕\]]|[〔\[]×\d+[〕\]]|→\s*\[[^\]]*\]\([^)]*\)", "", s)
    # 去掉空白和一般标点，但保留会改变意思的符号：正负号、小数点、比较符、百分号、斜杠、冒号
    s = s.replace("−", "-").replace("＋", "+").replace("：", ":").replace("％", "%").replace("＜", "<").replace("＞", ">")
    return re.sub(r"[^\w+\-.<>=≤≥%/:]+|_", "", s).lower()


def tagfmt(lib, kind, val):
    return f"〔{'键' if kind == 'key' else ''}{':' if kind == 'key' else ''}{val}〕" if lib.lang == "zh" else f"[{kind + ':' if kind == 'key' else ''}{val}]"


def edit_section(text, kind, fn):
    """在某一段里改行：fn(段内行列表) -> 新行列表。段不存在就在末尾补一个。"""
    lines = text.split("\n")
    start = next((i for i, l in enumerate(lines) if l.startswith("## ") and section_kind(l[3:].strip()) == kind), None)
    if start is None:
        lines += ["", "## " + SECTIONS[kind][0], ""]
        start = len(lines) - 2
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    body = [l for l in lines[start + 1:end] if l.strip() and not re.fullmatch(r"\s*[（(](暂无|none)[)）]\s*", l)]
    new = fn(body)
    return "\n".join(lines[:start + 1] + [""] + new + [""] + lines[end:]).replace("\n\n\n", "\n\n")


def prepend_log(path, entry):
    text = read(path)
    i = text.find("\n### ")
    write(path, (text.rstrip() + "\n\n" + entry) if i < 0 else (text[:i + 1] + entry + "\n" + text[i + 1:]))


def maint_active(lib, agent):
    h = Lock(lib, agent, "maint.lock", ttl=3 * 3600).holder()
    return h if h and h.get("who") != agent else None


def cmd_add(a):
    if getattr(a, "batch", None):
        return cmd_add_batch(a)
    agent = who(a)
    lib = find_root(a.root, offline_ok=True)
    words = (a.words or a.text).strip()
    if lib is None or maint_active(lib, agent):
        return remember_line(lib, agent, words, a.scope, a.branch, a.key, a.source, a.text, reason=T(lib.lang if lib else "zh", "维护进行中" if lib else "库不在线", ""))
    with Lock(lib, agent):
        br = lib.find_branch(a.branch)
        if not br:
            if not a.create:
                sys.exit(T(lib, f"没有分支「{a.branch}」。确认是新的一类就加 --create --layer tasks|subjects|general --triggers \"词1,词2\" --summary \"一句话\"；拿不准就用 remember。",
                           f"No branch {a.branch}. Add --create --layer ... or use remember."))
            br = make_branch(lib, a.layer, a.branch, [x.strip() for x in re.split(r"[,，、]", a.triggers or a.branch) if x.strip()], a.summary)
            journal(lib, agent, "new-branch", br["file"], a.summary or "")
        kind = {"always": "standards", "candidate": "candidates", "reference": "reference"}.get(a.scope)
        result, old = "logged", None
        if kind:
            line = "- " + a.text.strip() + (tagfmt(lib, "key", a.key) if a.key else "")
            if a.detail:
                line += f" → [{T(lib, '详情', 'details')}]({lib.L['entries_dir']}/{a.detail})"

            def fn(body):
                nonlocal result, old
                if a.key:
                    for i, l in enumerate(body):
                        if re.search(r"[〔\[](?:键|key):" + re.escape(a.key) + r"[〕\]]", l):
                            if norm(l) == norm(line):
                                break
                            old, body[i], result = l, line, "replaced"
                            return body
                for i, l in enumerate(body):
                    if is_item(l) and norm(l) == norm(line):
                        m = re.search(r"[〔\[]×(\d+)[〕\]]", l)
                        n = int(m.group(1)) + 1 if m else 2
                        body[i] = re.sub(r"[〔\[]×\d+[〕\]]", "", l).rstrip() + tagfmt(lib, "x", f"×{n}")
                        result = f"repeated×{n}"
                        return body
                result = "added"
                return body + [line]

            text = edit_section(read(br["file"]), kind, fn)
            text = re.sub(r"^updated:.*$", f"updated: {today()}", text, count=1, flags=re.M)
            write(br["file"], text)
            if old:
                arch = lib.p(lib.L["archive"], f"{br['meta'].get('name')}.md")
                append(arch, f"\n### {stamp()} {T(lib, '被替换', 'replaced')}（{T(lib, '键', 'key')} {a.key}，{agent}）\n- {T(lib, '旧', 'old')}：{old.lstrip('- ')}\n- {T(lib, '新', 'new')}：{line.lstrip('- ')}\n")
        entry = (f"### {stamp()}《{a.topic or T(lib, '对话', 'session')}》（{agent}）\n"
                 f"- {T(lib, '原话', 'Words')}：「{words}」\n"
                 f"- {T(lib, '写入', 'Result')}：{a.scope} · {result}" + (f" · {T(lib, '来源', 'source')} {a.source}" if a.source else "") + "\n")
        prepend_log(os.path.join(br["dir"], lib.L["log_file"]), entry)
        journal(lib, agent, f"add:{a.scope}:{result}", br["file"], a.text, words)
    regen(lib)
    print(T(lib, f"已保存到「{br['meta'].get('name')}」（{a.scope}，{result}）。", f"Saved to {br['meta'].get('name')} ({a.scope}, {result})."))


# ---------------------------------------------------------------- 「这批先给我看」
def proposal_path(lib):
    return os.path.join(lib.fm, "proposal.jsonl")


def hold_state(lib):
    return (jload(os.path.join(lib.fm, "state.json"), {}) or {}).get("hold") or {}


def set_hold(lib, val):
    st_p = os.path.join(lib.fm, "state.json")
    st = jload(st_p, {}) or {}
    if val:
        st["hold"] = val
    else:
        st.pop("hold", None)
    jsave(st_p, st)


def cmd_hold(a):
    """用户说「这批先给我看」：状态存在库里，换智能体、定时维护都会等用户答复。"""
    lib = find_root(a.root)
    agent = who(a)
    h, pp = hold_state(lib), proposal_path(lib)
    props = [l for l in read(pp).splitlines() if l.strip()]
    if a.action == "on":
        set_hold(lib, {"on": True, "note": a.note or "", "by": agent, "at": now().isoformat()})
        journal(lib, agent, "hold", "on", a.note or "")
        print(T(lib, "已记下：下一批整理只出拟写入清单，等用户确认后才写库。", "Hold on: next batch will be proposed, not written."))
    elif a.action == "status":
        print(T(lib, f"先确认模式：{'开' if h.get('on') else '关'}；待用户答复的拟写入 {len(props)} 条" + (f"（{pp}）" if props else ""), f"Hold: {'on' if h.get('on') else 'off'}; {len(props)} proposed"))
        for l in props[:50]:
            try:
                d = json.loads(l)
                print("  - " + (f"[{d.get('branch')}] {d.get('text')}" if "text" in d else f"[{T(lib, '文件', 'file')}] {d.get('place')} → {d.get('branch') or T(lib, '归档', 'archive')}"))
            except ValueError:
                print("  - " + l[:120])
    elif a.action in ("apply", "discard"):
        if a.action == "apply" and props:
            adds = [l for l in props if '"place"' not in l]
            if adds:
                tmp = pp + ".adds"
                write(tmp, "\n".join(adds) + "\n")
                try:
                    cmd_add_batch(argparse.Namespace(root=lib.root, agent=agent, batch=tmp))
                finally:
                    os.remove(tmp)
            for l in props:
                d = json.loads(l)
                if "place" in d and os.path.exists(d["place"] if os.path.isabs(d["place"]) else lib.p(d["place"])):
                    cmd_place(argparse.Namespace(root=lib.root, agent=agent, file=d["place"], branch=d.get("branch"), done=bool(d.get("done")) or not d.get("branch"), note=d.get("note")))
        commit_harvest(lib)  # 这批材料算处理过了（确认写入或用户否掉），下次不再重复提
        if os.path.exists(pp):
            shutil.move(pp, os.path.join(lib.fm, f"proposal-{stamp().replace(' ', '_').replace(':', '')}-{a.action}.jsonl"))
        set_hold(lib, None)
        journal(lib, agent, "hold", a.action, f"{len(props)}")
        prepend_log(lib.p(lib.L["maint_log"]), f"### {stamp()}（{agent}）\n- {T(lib, '用户确认的一批', 'confirmed batch')}：{T(lib, '已写入', 'applied') if a.action == 'apply' else T(lib, '已否掉', 'discarded')} {len(props)} {T(lib, '条', 'items')}\n")
        print(T(lib, f"{'已按用户确认写入' if a.action == 'apply' else '已按用户意见放弃'}这一批（{len(props)} 条），先确认模式已关闭。", f"Batch {a.action}ed ({len(props)}); hold off."))
    elif a.action == "off":
        if props:
            sys.exit(T(lib, "还有一批拟写入在等用户答复：用 hold apply（确认）或 hold discard（放弃）。", "A proposal is pending: use hold apply or hold discard."))
        set_hold(lib, None)
        print(T(lib, "先确认模式已关闭。", "Hold off."))


# ---------------------------------------------------------------- 读、搬、批量写（无损）
def section_items(text, kind):
    """某一段的条目：每条 = 条目行 + 紧跟的缩进续行。返回 [[行, 续行...], ...]。"""
    items = []
    for l in sections(text).get(kind, []):
        if is_item(l) and not l.startswith((" ", "\t")):
            items.append([l])
        elif items and (l.startswith((" ", "\t")) or not is_item(l)):
            items[-1].append(l)
    return items


def pick_items(lib, items, a):
    """按 --match（条目开头文字，可多个）或 --items（序号）选条目。--match 更安全：序号会随增删变化，开头文字不会。"""
    if getattr(a, "match", None):
        key = lambda x: re.sub(r"[*\s]", "", x)
        idx = []
        for m in a.match:
            hits = [i + 1 for i, it in enumerate(items) if key(re.sub(r"^\s*([-*]|\d+[.)])\s", "", it[0])).startswith(key(m))]
            if len(hits) != 1:
                sys.exit(T(lib, f"「{m}」匹配到 {len(hits)} 条（要恰好 1 条）：写长一点的开头文字", f"'{m}' matched {len(hits)} items (need exactly 1)"))
            idx.append(hits[0])
        return sorted(set(idx))
    if not a.items:
        sys.exit(T(lib, "要 --match \"条目开头文字\"（推荐）或 --items 序号", "Need --match or --items"))
    idx = sorted({int(x) for x in re.split(r"[,，\s]+", a.items) if x.strip()})
    if not idx or idx[0] < 1 or idx[-1] > len(items):
        sys.exit(T(lib, f"条目序号超出范围（这一段共 {len(items)} 条，用 fm read --numbered 看序号）", f"Index out of range (1..{len(items)})"))
    return idx


def cmd_read(a):
    """开工时读一个分支：只输出「标准」和「待验证」原文，不读资料、日志。比打开整份文件省，内容一字不改。"""
    lib = find_root(a.root)
    out = []
    for name in a.branch:
        br = lib.find_branch(name)
        if not br:
            out.append(T(lib, f"（没有分支「{name}」）", f"(no branch {name})"))
            continue
        text, m = read(br["file"]), br["meta"]
        out.append(f"# {m.get('name')}  ·  {br['file']}")
        if m.get("summary"):
            out.append(f"> {m['summary']}")
        for kind, title in (("standards", T(lib, "标准", "Standards")), ("candidates", T(lib, "待验证（未确认，按适用条件参考）", "Candidates (unconfirmed)"))):
            if kind == "candidates" and a.standards_only:
                continue
            items = section_items(text, kind)
            if not items:
                continue
            out.append(f"\n## {title}")
            for i, it in enumerate(items, 1):
                out.append((f"{i}. " + it[0].lstrip()[2:] if a.numbered else it[0]) + "".join("\n" + x for x in it[1:]))
        out.append("")
    print("\n".join(out).rstrip())


def _move_links(lib, src_dir, dst_dir, lines):
    """条目里链到的 条目/、文档/ 文件跟着搬，重名加序号并改写链接。"""
    out = []
    for l in lines:
        def sub(m):
            rel = m.group(2)
            if rel.startswith(("http", "/", "#")) or not os.path.exists(os.path.join(src_dir, rel)):
                return m.group(0)
            d, fn = os.path.split(rel)
            b, e = os.path.splitext(fn)
            new, k = rel, 2
            while os.path.exists(os.path.join(dst_dir, new)):
                new, k = os.path.join(d, f"{b}-{k}{e}").replace(os.sep, "/"), k + 1
            os.makedirs(os.path.dirname(os.path.join(dst_dir, new)), exist_ok=True)
            shutil.move(os.path.join(src_dir, rel), os.path.join(dst_dir, new))
            return f"{m.group(1)}({new})"
        out.append(re.sub(r"(\]\s*)\(([^)]+)\)", sub, l))
    return out


def cmd_move(a):
    """把一个分支里的若干条原样搬到另一个分支（拆分大分支用）。不改一个字；链接的详情文件一起搬；搬前当天自动快照。"""
    agent = who(a)
    lib = find_root(a.root)
    with Lock(lib, agent):
        src = lib.find_branch(a.branch)
        if not src:
            sys.exit(T(lib, f"没有分支「{a.branch}」", f"No branch {a.branch}"))
        dst = lib.find_branch(a.to)
        if not dst:
            if not a.create:
                sys.exit(T(lib, f"没有分支「{a.to}」：新建就加 --create --layer ... --triggers \"词1,词2\" --summary \"一句话\"", f"No branch {a.to}; add --create"))
            dst = make_branch(lib, a.layer, a.to, [x.strip() for x in re.split(r"[,，、]", a.triggers or a.to) if x.strip()], a.summary)
            journal(lib, agent, "new-branch", dst["file"], a.summary or "")
        if src["file"] == dst["file"]:
            sys.exit(T(lib, "源和目标是同一个分支", "Same branch"))
        snap = f"{today()}-move-{datetime.now().strftime('%H%M%S')}"
        snapshot(lib, keep=30, tag=snap[11:])
        kind = a.section
        items = section_items(read(src["file"]), kind)
        idx = pick_items(lib, items, a)
        picked = [items[i - 1] for i in idx]
        moved = [_move_links(lib, src["dir"], dst["dir"], it) for it in picked]

        def take(body):  # 按原样删掉被搬走的条目（含续行）
            keep, groups = [], []
            for l in body:
                if is_item(l) and not l.startswith((" ", "\t")):
                    groups.append([l])
                elif groups and (l.startswith((" ", "\t")) or not is_item(l)):
                    groups[-1].append(l)
                else:
                    keep.append(l)
            sel = {tuple(it) for it in picked}
            return keep + [x for g in groups if tuple(g) not in sel for x in g]
        before = sum(len(x) for x in section_items(read(src["file"]), kind)) + sum(len(x) for x in section_items(read(dst["file"]), kind))
        write(src["file"], edit_section(read(src["file"]), kind, take))
        write(dst["file"], edit_section(read(dst["file"]), kind, lambda body: body + [x for it in moved for x in it]))
        after = sum(len(x) for x in section_items(read(src["file"]), kind)) + sum(len(x) for x in section_items(read(dst["file"]), kind))
        if before != after:  # 保险：行数必须守恒
            sys.exit(T(lib, f"✗ 搬运后行数不守恒（{before}→{after}），请用 .fm/snapshots/{snap}.zip 恢复并报告", f"✗ Line count changed {before}->{after}; restore from snapshot"))
        for br, verb in ((src, T(lib, "搬出到", "moved out to")), (dst, T(lib, "搬入自", "moved in from"))):
            other = dst if br is src else src
            prepend_log(os.path.join(br["dir"], lib.L["log_file"]), f"### {stamp()}《{T(lib, '拆分', 'split')}》（{agent}）\n- {verb}「{other['meta'].get('name')}」{len(picked)} {T(lib, '条（原文不变）', 'items (verbatim)')}\n")
        journal(lib, agent, "move", dst["file"], f"{src['meta'].get('name')} → {dst['meta'].get('name')} ×{len(picked)}")
    regen(lib)
    print(T(lib, f"已把「{src['meta'].get('name')}」的 {len(picked)} 条原样搬到「{dst['meta'].get('name')}」（行数校验通过；搬运前快照 .fm/snapshots/{snap}.zip）。",
            f"Moved {len(picked)} items verbatim to {dst['meta'].get('name')}."))


def cmd_archive(a):
    """把若干条原样挪进归档（被推翻、已升为标准的重复待验证等），写明理由。原文不改，可从归档找回。"""
    agent = who(a)
    lib = find_root(a.root)
    with Lock(lib, agent):
        br = lib.find_branch(a.branch)
        if not br:
            sys.exit(T(lib, f"没有分支「{a.branch}」", f"No branch {a.branch}"))
        snap = f"{today()}-archive-{datetime.now().strftime('%H%M%S')}"
        snapshot(lib, keep=30, tag=snap[11:])
        items = section_items(read(br["file"]), a.section)
        idx = pick_items(lib, items, a)
        picked = [items[i - 1] for i in idx]
        sel = {tuple(it) for it in picked}

        def take(body):
            keep, groups = [], []
            for l in body:
                if is_item(l) and not l.startswith((" ", "\t")):
                    groups.append([l])
                elif groups and (l.startswith((" ", "\t")) or not is_item(l)):
                    groups[-1].append(l)
                else:
                    keep.append(l)
            return keep + [x for g in groups if tuple(g) not in sel for x in g]
        write(br["file"], edit_section(read(br["file"]), a.section, take))
        arch = lib.p(lib.L["archive"], f"{br['meta'].get('name')}.md")
        append(arch, f"\n### {stamp()} {T(lib, '归档', 'archived')}（{agent}）：{a.reason}\n" + "\n".join(x for it in picked for x in it) + "\n")
        prepend_log(os.path.join(br["dir"], lib.L["log_file"]), f"### {stamp()}《{T(lib, '归档', 'archive')}》（{agent}）\n- {len(picked)} {T(lib, '条原样移入', 'items moved verbatim to')} {os.path.relpath(arch, lib.root)}：{a.reason}\n")
        journal(lib, agent, "archive", br["file"], f"{len(picked)}：{a.reason}")
    regen(lib)
    print(T(lib, f"已把「{br['meta'].get('name')}」{len(picked)} 条原样移入归档（理由：{a.reason}；快照 .fm/snapshots/{snap}.zip）。", f"Archived {len(picked)} items."))


def cmd_add_batch(a):
    """--batch 文件：每行一个 JSON（branch、text、words、scope、key、source、topic、create、layer、triggers、summary）。一次调用写多条，少几十次工具往返。"""
    ok, bad = 0, []
    for n, line in enumerate(open(a.batch, encoding="utf-8"), 1):
        if not line.strip():
            continue
        try:
            d = json.loads(line)
            sub = argparse.Namespace(root=a.root, agent=a.agent, branch=d["branch"], text=d["text"], words=d.get("words"), scope=d.get("scope", "always"),
                                     key=d.get("key"), source=d.get("source"), topic=d.get("topic"), detail=d.get("detail"), create=bool(d.get("create")),
                                     layer=d.get("layer", "tasks"), triggers=d.get("triggers"), summary=d.get("summary"), batch=None)
            cmd_add(sub)
            ok += 1
        except SystemExit as e:
            bad.append(f"{n}: {e}")
        except Exception as e:
            bad.append(f"{n}: {e}")
    print(f"批量写入：成功 {ok} 条" + (f"，失败 {len(bad)} 条：\n" + "\n".join(bad) if bad else "。"))
    if bad:
        sys.exit(1)


def remember_line(lib, agent, text, scope=None, branch=None, key=None, source=None, note=None, reason=""):
    tags = "; ".join(f"{k}: {v}" for k, v in (("scope", scope), ("branch", branch), ("key", key), ("source", source), ("agent", agent)) if v)
    line = f"- [{stamp()}] ({tags}) 「{redact(lib, text.strip())}」" + (f" — {note.strip()}" if note and note.strip() != text.strip() else "") + "\n"
    if lib is None:
        append(OFFLINE, line)
        print("经验库暂时不在线，已先记在本机，下次维护时合并。/ Library offline; saved locally, merged at next maintenance.")
        return
    p = lib.p(lib.L["inbox"])
    body = read(p)
    if INBOX_LINE not in body:
        write(p, body.rstrip() + "\n\n" + INBOX_LINE + "\n")
    append(p, line)
    journal(lib, agent, "remember", lib.L["inbox"], text)
    print(T(lib, "已记入收件箱，下次维护时归档。" + (f"（{reason}）" if reason else ""), "Saved to inbox; it will be filed at the next maintenance."))


def cmd_remember(a):
    lib = find_root(a.root, offline_ok=True)
    remember_line(lib, who(a), a.text, a.scope, a.branch, a.key, a.source, a.note)


def cmd_place(a):
    lib = find_root(a.root)
    agent = who(a)
    src = os.path.abspath(a.file if os.path.isabs(a.file) else lib.p(a.file) if os.path.exists(lib.p(a.file)) else a.file)
    if not os.path.exists(src):
        sys.exit(T(lib, f"找不到文件：{a.file}", f"Not found: {a.file}"))
    with Lock(lib, agent):
        if a.done:
            dst_dir = lib.p(lib.L["archive"], T(lib, "已处理的投放", "processed-drops"), today())
        else:
            br = lib.find_branch(a.branch)
            if not br:
                sys.exit(T(lib, f"没有分支「{a.branch}」", f"No branch {a.branch}"))
            dst_dir = os.path.join(br["dir"], lib.L["docs_dir"])
        os.makedirs(dst_dir, exist_ok=True)
        dst = os.path.join(dst_dir, os.path.basename(src))
        base, ext = os.path.splitext(dst)
        i = 2
        while os.path.exists(dst):
            dst, i = f"{base}-{i}{ext}", i + 1
        shutil.move(src, dst)
        if not a.done:
            rel = os.path.relpath(dst, br["dir"]).replace(os.sep, "/")
            desc = a.note or os.path.splitext(os.path.basename(dst))[0]
            write(br["file"], edit_section(read(br["file"]), "reference", lambda body: body + [f"- {desc} → [{T(lib, '文档', 'doc')}]({rel})"]))
        journal(lib, agent, "place", dst, "done" if a.done else a.branch)
    regen(lib)
    print(T(lib, f"已移到 {dst}", f"Moved to {dst}"))


def cmd_journal(a):
    lib = find_root(a.root)
    rows = [json.loads(l) for l in open(os.path.join(lib.fm, "journal.jsonl"), encoding="utf-8") if l.strip()] if os.path.exists(os.path.join(lib.fm, "journal.jsonl")) else []
    for d in rows[-a.n:]:
        print(f"{d['at'][:16]}  {d['agent']:<14} {d['op']:<26} {os.path.basename(str(d.get('target', '')))}  {d.get('detail', '')[:60]}")


# ---------------------------------------------------------------- 采集：各智能体对话记录适配器
def _jsonl(f):
    with open(f, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            try:
                yield json.loads(line)
            except ValueError:
                pass


def _text(c):
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") in (None, "text", "input_text", "output_text"))
    if isinstance(c, dict):
        return c.get("text", "")
    return ""


def _clean(t):
    t = re.sub(r"<system-reminder>.*?</system-reminder>", "", t, flags=re.S)
    t = re.sub(r"<(command-[a-z]+|local-command-stdout)>.*?</\1>", "", t, flags=re.S)
    return t.strip()


def ev_claude(f):
    for d in _jsonl(f):
        if d.get("type") == "assistant":
            yield "assistant", d.get("timestamp"), _text(d.get("message", {}).get("content"))
        elif d.get("type") == "user" and not d.get("isMeta"):
            c = d.get("message", {}).get("content")
            if isinstance(c, list) and c and all(isinstance(x, dict) and x.get("type") == "tool_result" for x in c):
                continue
            yield "user", d.get("timestamp"), _clean(_text(c))


def ev_codex(f):
    for d in _jsonl(f):
        p = d.get("payload") or {}
        if d.get("type") == "response_item" and p.get("type") == "message" and p.get("role") in ("user", "assistant"):
            t = _text(p.get("content"))
            if p["role"] == "user" and "## My request:" in t:
                t = t.split("## My request:", 1)[1]
            yield p["role"], d.get("timestamp"), t.strip()


def ev_gemini(f):
    d = jload(f)
    msgs = d.get("messages") if isinstance(d, dict) else d
    for m in msgs or []:
        if isinstance(m, dict):
            yield ("user" if m.get("type", m.get("role")) == "user" else "assistant"), m.get("timestamp"), _text(m.get("content", m.get("parts", ""))).strip()


def ev_cline(f):
    for m in jload(f, []) or []:
        if isinstance(m, dict):
            t = re.sub(r"<environment_details>.*?</environment_details>", "", _text(m.get("content")), flags=re.S)
            t = re.sub(r"^<(task|feedback)>\s*|\s*</(task|feedback)>$", "", t.strip())
            yield ("user" if m.get("role") == "user" else "assistant"), None, t.strip()


def ev_continue(f):
    for h in (jload(f, {}) or {}).get("history", []):
        m = h.get("message", h) if isinstance(h, dict) else {}
        yield ("user" if m.get("role") == "user" else "assistant"), None, _text(m.get("content")).strip()


def ev_aider(f):
    role, buf = None, []
    for line in read(f).splitlines():
        if line.startswith("#### "):
            if role == "assistant" and buf:
                yield "assistant", None, "\n".join(buf).strip()
                buf = []
            yield "user", None, line[5:].strip()
            role = "assistant"
        elif line.startswith("# aider chat started"):
            role, buf = None, []
        elif role == "assistant" and not line.startswith(">"):
            buf.append(line)


def ev_generic(spec):
    def gen(f):
        fmt = spec.get("format", "jsonl")
        if fmt == "markdown":
            rx = re.compile(spec.get("user_line_regex", r"^#### (.*)"))
            for line in read(f).splitlines():
                m = rx.match(line)
                if m:
                    yield "user", None, m.group(1).strip()
            return
        rows = list(_jsonl(f)) if fmt == "jsonl" else (jload(f, []) or [])
        if isinstance(rows, dict):
            rows = rows.get(spec.get("messages_field", "messages"), [])
        for r in rows:
            if isinstance(r, dict):
                role = r.get(spec.get("role_field", "role"))
                yield ("user" if role == spec.get("user_value", "user") else "assistant"), r.get(spec.get("time_field", "timestamp")), _text(r.get(spec.get("text_field", "content"))).strip()
    return gen


def vscode_family(ext_id, sub):
    return [expand("{APPSUPPORT}/" + b + "/User/globalStorage/" + ext_id + "/" + sub) for b in ("Code", "Code - Insiders", "Cursor", "Windsurf", "VSCodium", "Trae")]


ADAPTERS = {
    "claude": ("Claude", lambda lib: [expand("~/.claude/projects/*/*.jsonl")], ev_claude),
    "codex": ("Codex", lambda lib: [expand("~/.codex/sessions/*/*/*/*.jsonl"), expand("~/.codex/sessions/*.jsonl")], ev_codex),
    "gemini": ("Gemini", lambda lib: [expand("~/.gemini/tmp/*/chats/*.json")], ev_gemini),
    "qwen": ("Qwen", lambda lib: [expand("~/.qwen/tmp/*/chats/*.json")], ev_gemini),
    "cline": ("Cline", lambda lib: vscode_family("saoudrizwan.claude-dev", "tasks/*/api_conversation_history.json"), ev_cline),
    "roo": ("Roo", lambda lib: vscode_family("rooveterinaryinc.roo-cline", "tasks/*/api_conversation_history.json"), ev_cline),
    "kilo": ("Kilo", lambda lib: vscode_family("kilocode.kilo-code", "tasks/*/api_conversation_history.json"), ev_cline),
    "continue": ("Continue", lambda lib: [expand("~/.continue/sessions/*.json")], ev_continue),
    "aider": ("Aider", lambda lib: [os.path.join(os.path.expanduser(d), ".aider.chat.history.md") for d in lib.cfg.get("aider_projects", [])], ev_aider),
}


def sources(lib):
    allowed = lib.cfg.get("privacy", {}).get("harvest_agents", "all")
    S = [(name, g(lib), fn) for k, (name, g, fn) in ADAPTERS.items() if allowed == "all" or k in allowed]
    for spec in lib.cfg.get("custom_sources", []):
        S.append((spec.get("name", "custom"), [expand(g) for g in spec.get("globs", [spec.get("glob", "")])], ev_generic(spec)))
    return S


SESSION_SKIP = ("<scheduled-task", "This is an automated run", "维护反馈经验库", "Maintain the feedback memory")


def excerpt(lib, t, maxc):
    """长发言不再只截前 600 字：保留开头、每个反馈词前后的原文、以及结尾（拍板常在最后）。"""
    if len(t) <= maxc:
        return t
    words = [w for w in KW["feedback"] + lib.cfg.get("extra_feedback_words", []) if w.lower() not in set(x.lower() for x in KW.get("weak", []))]
    low, span = t.lower(), 120
    marks = sorted(i for w in words for i in [m.start() for m in re.finditer(re.escape(w.lower()), low)])
    segs = [(0, min(len(t), maxc // 3)), (max(0, len(t) - maxc // 3), len(t))] + [(max(0, i - span), min(len(t), i + span)) for i in marks]
    segs.sort()
    merged = []
    for a0, b0 in segs:
        if merged and a0 <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b0))
        else:
            merged.append((a0, b0))
    out, total = [], 0
    for a0, b0 in merged:
        piece = t[a0:b0]
        if total + len(piece) > maxc * 3:  # 单条上限：原 600 字的 3 倍
            piece = piece[:max(0, maxc * 3 - total)]
        if piece:
            out.append(piece)
            total += len(piece)
    return " …… ".join(out) + f"（{T(lib, '原文', 'orig')} {len(t)} {T(lib, '字，已摘取开头、反馈词前后与结尾', 'chars; head, feedback context and tail kept')}）"


def keeper(lib):
    weak = set(w.lower() for w in KW.get("weak", []))
    feed_all = [w.lower() for w in KW["feedback"] + lib.cfg.get("extra_feedback_words", [])]
    strong = [w for w in feed_all if w not in weak]
    weak_max = int(lib.cfg.get("harvest", {}).get("weak_max_chars", 200))
    feed = feed_all
    cmds = set(w.lower() for w in KW["commands"])
    skips = tuple(KW["skip_prefixes"])
    long_chars = int(lib.cfg.get("harvest", {}).get("keep_long_chars", 0))  # 0：只收命中反馈词的发言，省 token

    def keep(t):
        if t.startswith(skips):
            return False
        t2 = re.sub(r'@"[^"]*"|@\S+|`[^`]*`', "", t).strip()
        low = t2.lower()
        if len(low) < 4:
            return False
        bare = re.sub(r"[\W_]+", " ", low).strip()
        if bare in cmds or (len(low) < 20 and bare.split(" ")[0] in cmds and not any(w in low for w in feed)):
            return False
        if any(w in low for w in strong):
            return True
        if len(t2) < weak_max and any(w in low for w in weak if w in feed):
            return True  # 常见词只在短发言里算：长段任务描述里的「问题」「改」多半不是反馈
        return long_chars > 0 and len(t2) >= long_chars
    return keep


SECRET = re.compile(r"(sk-[A-Za-z0-9_\-]{16,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|xox[bp]-[A-Za-z0-9\-]{10,}|eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,}|(?i:password|passwd|api[_-]?key|secret|token)\s*[:=]\s*(?![$<{(`\[]|\[已脱敏)\S{6,})")


def redact(lib, t):
    t = SECRET.sub("[已脱敏]", t)
    for p in (lib.cfg.get("privacy", {}).get("redact_patterns", []) if lib else []):
        t = re.sub(p, "[已脱敏]", t)
    return t


def inbox_new(lib):
    body = read(lib.p(lib.L["inbox"]))
    return body.split(INBOX_LINE, 1)[1].strip() if INBOX_LINE in body else ""


def drop_files(lib):
    out = []
    for dp, dns, fns in os.walk(lib.drop):
        dns[:] = [d for d in dns if not d.startswith(".")]
        out += [os.path.join(dp, f) for f in fns if not f.startswith(".") and f not in ("说明.txt", "README.txt")]
    return sorted(out)


def do_harvest(lib, out, dry=False):
    state_p, pend_p = os.path.join(lib.fm, "state.json"), os.path.join(lib.fm, "pending.json")
    state = jload(state_p, {}) or {}
    hc = lib.cfg.get("harvest", {})
    since = parse_ts(state.get("last")) or (now() - timedelta(days=hc.get("first_run_days", 1)))
    maxc, ctxc, budget = hc.get("max_chars_per_message", 600), hc.get("context_chars", 120), hc.get("max_material_chars", 60000)
    keep, seen = keeper(lib), state.get("seen", {})
    ib = inbox_new(lib)
    ib_norm = {norm(x) for x in re.findall(r"「(.*?)」", ib)}
    jsince = parse_ts(state.get("maint_done_at")) or since
    direct = [d for d in journal_since(lib, jsince) if d["op"].startswith(("add:", "new-branch"))]
    ib_norm |= {norm(d.get("words", "")) for d in journal_since(lib, since) if d.get("words")}  # 已被直接写入的原话不再重复交给维护者
    new_seen, blocks, n_s, n_m, more, used = {}, [], 0, 0, False, 0
    for agent, globs, fn in sources(lib):
        files = [f for g in globs if g for f in glob.glob(g)]
        for f in sorted(set(files), key=os.path.getmtime):
            if datetime.fromtimestamp(os.path.getmtime(f), timezone.utc) < since:
                continue
            try:
                evs = list(fn(f))
            except Exception as e:  # 格式变了：跳过这个文件，不让整次失败
                blocks.append(f"\n> ⚠ {agent} {os.path.basename(f)}：{e}\n")
                continue
            users = [i for i, (r, _, t) in enumerate(evs) if r == "user" and t]
            if not users:
                continue
            firsttxt = evs[users[0]][2]
            topic = next((evs[i][2] for i in users if not evs[i][2].lstrip().startswith("<")), firsttxt)
            key = hashlib.sha1(f.encode()).hexdigest()[:16]
            done = seen.get(key)
            if done is not None and done > len(users):
                done = 0
            msgs, last_asst = [], ""
            for k, (role, ts, t) in enumerate(evs):
                if not t:
                    continue
                if role == "assistant":
                    last_asst = t
                    continue
                idx = users.index(k)
                if done is not None and idx < done:
                    continue
                if done is None:
                    when = parse_ts(ts) or datetime.fromtimestamp(os.path.getmtime(f), timezone.utc)
                    if when < since:
                        continue
                if keep(t) and norm(t) not in ib_norm:
                    ctx = last_asst.replace("\n", " ")[-ctxc:] if len(t) < 60 else ""
                    msgs.append((idx, str(ts or "")[:16], redact(lib, ctx), redact(lib, excerpt(lib, t, maxc))))
            if firsttxt.startswith(SESSION_SKIP) or not msgs:
                new_seen[key] = len(users)
                continue
            if more:  # 预算已满：后面的会话整体留到下一轮
                continue
            head_blk = f"\n## {agent} · {os.path.basename(f)[:24]}\n{T(lib, '主题', 'Topic')}：{redact(lib, topic[:120])}\n"
            blk, taken, stop_at = [head_blk], 0, None
            for idx, ts, ctx, t in msgs:  # 逐条计入预算：超长会话也会在中途切开，剩下的下一轮接着处理
                piece = f"\n**{ts} {T(lib, '用户', 'User')}：** {t}\n" + (f"> （{T(lib, '之前 AI 说', 'AI before')}：…{ctx}）\n" if ctx else "")
                if used + len(head_blk) + sum(map(len, blk[1:])) + len(piece) > budget and (taken or used):
                    stop_at = idx
                    break
                blk.append(piece)
                taken += 1
            if taken:
                used += sum(map(len, blk))
                blocks.append("".join(blk))
                n_s += 1
                n_m += taken
            if stop_at is not None:
                more = True
                if taken:
                    new_seen[key] = stop_at
                continue
            new_seen[key] = len(users)
    all_drops = [f for f in drop_files(lib) if f.lower().endswith(".md")]
    drops, drop_used, conv_used = [], 0, used
    for f in sorted(all_drops, key=os.path.getmtime):  # 投放区也计入本轮预算；超出的留到下一轮（至少处理一份）
        n = len(read(f))
        if drops and drop_used + used + n > budget:
            more = True
            continue
        drops.append(f)
        drop_used += n
    used += drop_used
    pending_conv = state.get("needs_agent", [])
    has_new = bool(ib or drops or pending_conv or n_m or direct)
    if dry == "measure":
        ext = sum(len(read(f)) if f.lower().endswith((".md", ".txt")) else os.path.getsize(f) // 3 for f in drop_files(lib) if os.path.isfile(f))
        return {"chars": conv_used + len(ib) + ext + 200 * len(direct), "more": more, "has_new": has_new}
    if dry:
        return has_new
    head = [f"# {T(lib, '待维护材料', 'Material')}（{since.isoformat()[:16]} → {now().isoformat()[:16]}）\n"]
    if ib:
        head.append(f"\n## {T(lib, '收件箱（用户明确要求记下的，先处理）', 'Inbox (explicitly requested — first)')}\n\n{redact(lib, ib)}\n")
    if drops:
        head.append(f"\n## {T(lib, '投放区文件（已是 MD；逐个读，提炼后用 place 归位）', 'Drop zone files (MD; read each, then place)')}\n" + "".join(f"- {f}\n" for f in drops))
    if pending_conv:
        head.append(f"\n## {T(lib, '脚本转不了、需要你读出内容写成同名 .md 的文件', 'Files the script could not convert — read and write a .md')}\n" + "".join(f"- {x['path']}（{x['note']}）\n" for x in pending_conv))
    if direct:
        head.append(f"\n## {T(lib, '各智能体自上次维护以来的直接写入（复核：合规、去重、层级对不对）', 'Direct writes by agents since last maintenance (review)')}\n" + "".join(f"- {d['at'][:16]} {d['agent']} {d['op']} {os.path.relpath(str(d.get('target')), lib.root) if os.path.isabs(str(d.get('target'))) else d.get('target')}：{d.get('detail', '')[:80]}\n" for d in direct))
    tail = [f"\n## {T(lib, '现有分支', 'Existing branches')}\n"] + [f"- [{lib.L['layers'][b['layer']]}] {b['meta'].get('name')} → {b['file']}\n" for b in lib.branches()]
    if more:
        tail.append(f"\n> {T(lib, '对话材料超出本轮预算，剩下的留到下一轮（maint finish 会提示 MORE）。', 'More material remains for the next round.')}\n")
    write(out, NOTHING + "\n" if not has_new else "".join(head + blocks + tail))
    jsave(pend_p, {"at": now().isoformat(), "since": since.isoformat(), "seen": new_seen, "inbox": ib, "more": more})
    return {"has_new": has_new, "sessions": n_s, "messages": n_m, "inbox": bool(ib), "drops": len(drops), "needs_agent": len(pending_conv), "direct": len(direct), "more": more}


def commit_harvest(lib):
    state_p, pend_p = os.path.join(lib.fm, "state.json"), os.path.join(lib.fm, "pending.json")
    pend = jload(pend_p)
    if not pend:
        return False
    state = jload(state_p, {}) or {}
    state.setdefault("seen", {}).update(pend.get("seen", {}))
    if not pend.get("more"):
        state["last"] = pend["at"]
    elif not state.get("last") and pend.get("since"):
        state["last"] = pend["since"]  # 分批续跑：固定本批起点，别让首次回溯窗口往后滑把剩下的漏掉
    if pend.get("inbox"):
        p = lib.p(lib.L["inbox"])
        write(p, read(p).replace(pend["inbox"], "", 1).rstrip() + "\n")
        append(os.path.join(lib.fm, "inbox-archive", datetime.now().strftime("%Y-%m") + ".md"), pend["inbox"] + "\n")
    jsave(state_p, state)
    os.remove(pend_p)
    return True


def cmd_harvest(a):
    lib = find_root(a.root)
    if a.commit:
        print(T(lib, "已标记为整理完成。", "Committed.") if commit_harvest(lib) else T(lib, "没有待提交的整理。", "Nothing to commit."))
        return
    r = do_harvest(lib, a.out)
    print(T(lib, f"会话 {r['sessions']} 个，用户发言 {r['messages']} 条，收件箱 {'有' if r['inbox'] else '无'}，投放区 {r['drops']} 个，直接写入 {r['direct']} 条 → {a.out}",
            f"{r['sessions']} sessions, {r['messages']} messages, inbox {'yes' if r['inbox'] else 'no'}, {r['drops']} drops, {r['direct']} direct writes → {a.out}"))
    if not r["has_new"]:
        print(NOTHING)


# ---------------------------------------------------------------- 首次汇集、格式转换
def cmd_collect(a):
    lib = find_root(a.root)
    n = collect_native(lib, who(a), only_changed=not a.all, only=a.agents.split(",") if a.agents else None)
    state_p = os.path.join(lib.fm, "state.json")
    state = jload(state_p, {}) or {}
    if a.days:
        state["last"] = (now() - timedelta(days=a.days)).isoformat()
        state["seen"] = {}
        jsave(state_p, state)
    print(T(lib, f"已把 {n} 份各智能体自带的规则/记忆放进投放区。" + (f"对话回溯到 {a.days} 天前，" if a.days else "") + "下次维护时整理（材料多会分几轮）。",
            f"Staged {n} native memory/rule files into the drop zone." + (f" Transcripts backfilled {a.days} days." if a.days else "")))


def write_split(path, header, body, limit=40000):
    """大文件切成几份，避免维护者一次读太多；按「## 」段落切，单段太长再硬切。"""
    if len(body) <= limit:
        write(path, header + body + "\n")
        return [path]
    parts, cur = [], ""
    for seg in re.split(r"(?m)^(?=## )", body):
        while len(seg) > limit:
            parts.append(cur + seg[:limit - len(cur)]) if cur else parts.append(seg[:limit])
            seg = seg[limit - len(cur):] if cur else seg[limit:]
            cur = ""
        if len(cur) + len(seg) > limit and cur:
            parts.append(cur)
            cur = ""
        cur += seg
    if cur:
        parts.append(cur)
    base, ext = os.path.splitext(path)
    out = []
    for i, part in enumerate(parts, 1):
        q = f"{base}（{i}／{len(parts)}）{ext}"
        write(q, header + part.strip() + "\n")
        out.append(q)
    return out


def collect_native(lib, agent, only_changed=True, only=None):
    """把各智能体自带的规则/记忆放进投放区。按单个文件记指纹，只收新增或改过的文件。"""
    state_p = os.path.join(lib.fm, "state.json")
    state = jload(state_p, {}) or {}
    hashes = state.setdefault("native", {})
    n = 0
    for ag in registry(lib):
        if not detected(ag) or (only and ag["id"] not in only):
            continue
        groups = {}
        for g in mem_globs(ag):
            for f in glob.glob(expand(g)):
                if not os.path.isfile(f) or os.path.abspath(f).startswith(lib.root) or "cy-feedback-memory" in os.path.basename(f) or os.path.basename(f).startswith("MEMORY.full-backup"):
                    continue
                t = re.sub(re.escape(MARK_S) + ".*?" + re.escape(MARK_E), "", read(f), flags=re.S).strip()
                h = hashlib.sha1(t.encode()).hexdigest()
                if not t or (only_changed and hashes.get(f) == h):
                    hashes[f] = h
                    continue
                hashes[f] = h
                groups.setdefault(os.path.dirname(f) if any(c in g for c in "*?") else f, []).append((f, t))
        for key, items in groups.items():
            body = "\n\n".join(f"## {os.path.basename(f)}\n\n{t}" if len(items) > 1 else t for f, t in sorted(items))
            base = os.path.splitext(os.path.basename(key.rstrip(os.sep)))[0] or "memory"
            name = re.sub(r"[^\w.\-]+", "_", f"{T(lib, '来自', 'from')}-{ag['id']}-{base}")[:80] + ".md"
            header = (f"> {T(lib, '来源', 'Source')}：{ag['name']} · `{key}`（{T(lib, '自动汇集', 'collected')} {stamp()}，{len(items)} {T(lib, '个新增或改动的文件', 'new/changed files')}）\n"
                      f"> {T(lib, '这是该智能体自己的规则或记忆，只收其中用户的反馈和标准；我们写进去的路由块已去掉。', 'Agent-native rules/memory; keep only user feedback and standards.')}\n\n")
            write_split(os.path.join(lib.drop, name), header, redact(lib, body))
            journal(lib, agent, "collect", name, f"{ag['id']} {len(items)}")
            n += 1
    jsave(state_p, state)
    return n


def convert_all(lib, agent):
    """把库里所有非 MD 文件转成 MD；转好的原件移进 .fm/originals，转不了的留给智能体。"""
    state_p = os.path.join(lib.fm, "state.json")
    state = jload(state_p, {}) or {}
    needs, done = [], []
    skip_names = {CONFIG, ".gitignore", "说明.txt", "README.txt"}
    for dp, dns, fns in os.walk(lib.root):
        dns[:] = [d for d in dns if not d.startswith(".")]
        for fn in fns:
            if fn.startswith(".") or fn in skip_names or fn.lower().endswith(".md"):
                continue
            f = os.path.join(dp, fn)
            md, method, note = fm_convert.convert(f)
            if md is None:
                if method != "skip":
                    needs.append({"path": f, "note": note})
                continue
            parent = os.path.dirname(f)
            if os.path.exists(os.path.join(parent, lib.L["branch_file"])):
                parent = os.path.join(parent, lib.L["docs_dir"])
            dst = os.path.join(parent, os.path.splitext(fn)[0] + ".md")
            i = 2
            while os.path.exists(dst):
                dst, i = os.path.join(parent, f"{os.path.splitext(fn)[0]}-{i}.md"), i + 1
            rel_dir = os.path.relpath(dp, lib.root)
            orig_dir = os.path.join(lib.fm, "originals", today(), "" if rel_dir == "." else rel_dir)
            os.makedirs(orig_dir, exist_ok=True)
            orig, j = os.path.join(orig_dir, fn), 2
            while os.path.exists(orig):  # 同一天同目录同名：加序号，绝不覆盖原件
                b, e = os.path.splitext(fn)
                orig, j = os.path.join(orig_dir, f"{b}-{j}{e}"), j + 1
            shutil.move(f, orig)
            outs = write_split(dst, f"> {T(lib, '由', 'Converted from')} `{fn}` {T(lib, '转换', '')}（{method}，{stamp()}）。{T(lib, '原件', 'Original')}：`{os.path.relpath(orig, lib.root)}`\n\n", redact(lib, md.strip()))
            journal(lib, agent, "convert", dst, method)
            done += outs
    state["needs_agent"] = needs
    jsave(state_p, state)
    return done, needs


def cmd_convert(a):
    lib = find_root(a.root)
    done, needs = convert_all(lib, who(a))
    print(T(lib, f"转成 MD：{len(done)} 个；需要智能体处理：{len(needs)} 个", f"Converted {len(done)}; {len(needs)} need an agent"))
    for x in needs:
        print(f"  - {x['path']}：{x['note']}")


# ---------------------------------------------------------------- 维护
def snapshot(lib, keep=14, tag=None):
    d = os.path.join(lib.fm, "snapshots")
    os.makedirs(d, exist_ok=True)
    z = os.path.join(d, f"{today()}{'-' + tag if tag else ''}.zip")
    k = 2
    while tag and os.path.exists(z):  # 同一秒的两次操作各留一份，不共用
        z, k = os.path.join(d, f"{today()}-{tag}-{k}.zip"), k + 1
    if not os.path.exists(z):
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
            for dp, dns, fns in os.walk(lib.root):
                dns[:] = [x for x in dns if not x.startswith(".")]
                for fn in fns:
                    if fn.endswith((".md", ".json")):
                        full = os.path.join(dp, fn)
                        zf.write(full, os.path.relpath(full, lib.root))
    for old in sorted(glob.glob(os.path.join(d, "*.zip")))[:-keep]:
        os.remove(old)


def merge_offline(lib, agent):
    t = read(OFFLINE).strip()
    if not t:
        return 0
    p = lib.p(lib.L["inbox"])
    if INBOX_LINE not in read(p):
        append(p, "\n" + INBOX_LINE + "\n")
    append(p, t + "\n")
    os.remove(OFFLINE)
    journal(lib, agent, "merge-offline", lib.L["inbox"], f"{t.count(chr(10)) + 1}")
    return t.count("\n") + 1


def material_path(lib):
    return os.path.join(lib.fm, "material.md")


def cmd_maint(a):
    lib = find_root(a.root)
    agent = who(a)
    ml = Lock(lib, agent, "maint.lock", ttl=3 * 3600)
    st_p = os.path.join(lib.fm, "state.json")
    if a.action == "check":
        has = do_harvest(lib, material_path(lib) + ".dry", dry=True) or bool(read(OFFLINE).strip()) or any(not f.lower().endswith(".md") for f in drop_files(lib))
        print("HAS-NEW" if has else NOTHING)
        return
    if a.action == "start":
        st = jload(st_p, {}) or {}
        if hold_state(lib).get("on") and read(proposal_path(lib)).strip():
            print("WAITING " + T(lib, f"用户要求先确认的一批还没答复（{proposal_path(lib)}）。不整理新材料，等用户答复后用 hold apply / hold discard。",
                                 "A proposed batch awaits the user's answer; not processing new material."))
            return
        if st.get("maint_day") == today() and not a.force:
            print("ALREADY-DONE " + T(lib, f"今天已由 {st.get('maint_by')} 维护过。", f"Already maintained today by {st.get('maint_by')}."))
            return
        h = ml.holder()
        if h and h.get("who") != agent:
            print("LOCKED " + T(lib, f"「{h.get('who')}」正在维护（{h.get('at', '')[:16]} 开始）。", f"{h.get('who')} is maintaining."))
            return
        if not h:
            ml.acquire(wait=0)
        snapshot(lib)
        merged = merge_offline(lib, agent)
        if lib.cfg.get("maintenance", {}).get("collect_native_each_run", True):
            collect_native(lib, agent)
        conv, needs = convert_all(lib, agent)
        r = do_harvest(lib, material_path(lib))
        if not r["has_new"]:
            return finish(lib, agent, T(lib, "没有新反馈", "no new feedback"), quiet_nothing=True)
        print(f"READY {material_path(lib)}")
        if hold_state(lib).get("on"):
            print("HOLD " + T(lib, f"用户要求这批先给他看：不要 fm add / fm place。把拟写入逐行写进 {proposal_path(lib)}（格式同 add --batch；文件归位写 {{\"place\": 路径, \"branch\": 分支}} 或 {{\"place\": 路径, \"done\": true}}），然后 maint finish --hold。",
                              f"User asked to review this batch first: write proposals to {proposal_path(lib)} (add --batch format), no fm add/place, then maint finish --hold."))
        print(T(lib, f"会话 {r['sessions']}，发言 {r['messages']}，收件箱 {'有' if r['inbox'] else '无'}（离线合并 {merged}），投放区 {r['drops']}（本次转换 {len(conv)}，待智能体处理 {len(needs)}），直接写入 {r['direct']}" + ("，材料分批：还有剩余" if r["more"] else ""),
                f"{r['sessions']} sessions, {r['messages']} messages, {r['drops']} drops, {r['direct']} direct writes" + (", more remains" if r["more"] else "")))
        return
    if a.action == "finish":
        h = ml.holder()
        if (not h or h.get("who") != agent) and not a.force:
            print("NOT-YOURS " + T(lib, f"这一轮不是你开始的（{'持锁者：' + str(h.get('who')) if h else '当前没有进行中的维护'}），不能提交。先运行 maint start --agent {agent}。",
                                  f"This round isn't yours ({h.get('who') if h else 'no active round'}); run maint start first."))
            sys.exit(1)
        if a.hold:
            props = [l for l in read(proposal_path(lib)).splitlines() if l.strip()]
            st = jload(st_p, {}) or {}
            st["maint_day"], st["maint_by"], st["maint_done_at"] = today(), agent, now().isoformat()
            jsave(st_p, st)
            prepend_log(lib.p(lib.L["maint_log"]), f"### {stamp()}（{agent}）\n- {T(lib, '等用户确认', 'awaiting user')}：{T(lib, '拟写入', 'proposed')} {len(props)} {T(lib, '条，未写库', 'items, not written')}（{proposal_path(lib)}）\n")
            journal(lib, agent, "maintain", "hold", f"{len(props)}")
            ml.release()
            print("HELD " + T(lib, f"已列出 {len(props)} 条拟写入，等用户答复（fm hold status 查看）。", f"{len(props)} proposed; awaiting user."))
            return
        return finish(lib, agent, a.summary or "")


def finish(lib, agent, summary, quiet_nothing=False):
    errs = lint_errors(lib)[0]
    if errs and not quiet_nothing:
        print(T(lib, "还有不合规的地方，修好后再运行 maint finish：", "Fix these, then run maint finish again:"))
        for e in errs:
            print("✗ " + e)
        sys.exit(1)
    block, done, manual = sync_agents(lib, "auto")
    repaired = [ag["id"] for ag, _, r in done if r == "written"]
    pend = jload(os.path.join(lib.fm, "pending.json"), {}) or {}
    more = bool(pend.get("more"))
    commit_harvest(lib)
    regen(lib)
    line = f"### {stamp()}（{agent}）\n- {summary or '-'}\n" + (f"- {T(lib, '路由块已更新/修复', 'route block refreshed')}：{', '.join(repaired)}\n" if repaired else "") + (f"- {T(lib, '不合规（未修）', 'lint errors left')}：{len(errs)}\n" if errs else "")
    prepend_log(lib.p(lib.L["maint_log"]), line)
    st_p = os.path.join(lib.fm, "state.json")
    st = jload(st_p, {}) or {}
    st["maint_done_at"] = now().isoformat()
    if not more:
        st["maint_day"], st["maint_by"] = today(), agent
    jsave(st_p, st)
    journal(lib, agent, "maintain", "", summary)
    Lock(lib, agent, "maint.lock").release()
    if more:
        print("MORE " + T(lib, "还有材料没处理完：再运行一次 maint start 继续。", "More material remains: run maint start again."))
    else:
        print((NOTHING + " ") if quiet_nothing else "DONE " + T(lib, "维护完成。", "Maintenance done."))


# ---------------------------------------------------------------- 检查 / 体检 / 状态
def lint_errors(lib):
    errs, warns = [], []
    lim = lib.cfg["limits"]
    core = [l for l in core_items(lib) if is_item(l)]
    if len(core) > lim["core_lines"]:
        warns.append(T(lib, f"核心约定 {len(core)} 条，超过 {lim['core_lines']}：每次对话都加载，挪一些到分支。", f"Core has {len(core)} items (> {lim['core_lines']})."))
    names = {}
    for br in lib.branches():
        text, m = read(br["file"]), br["meta"]
        rel = os.path.relpath(br["file"], lib.root)
        if not m.get("triggers"):
            errs.append(T(lib, f"{rel} 缺 triggers（路由表没法分流）", f"{rel}: missing triggers"))
        if "standards" not in sections(text):
            errs.append(T(lib, f"{rel} 没有「标准」段", f"{rel}: no Standards section"))
        n = len(standard_lines(text))
        if n > lim["standards_per_branch"]:
            warns.append(T(lib, f"{rel} 标准 {n} 条，超过 {lim['standards_per_branch']}：建议按主题拆成子分支（fm move，原文不变），不要删减或改写。", f"{rel}: {n} standards — split by topic with fm move; never trim"))
        chars = sum(len(x) for it in section_items(text, "standards") for x in it)
        if chars > lim.get("standards_chars", 4000):
            warns.append(T(lib, f"{rel} 标准段 {chars} 字，每次开工都要读：建议按主题拆分（fm move）。", f"{rel}: standards {chars} chars — consider splitting"))
        names.setdefault(m.get("name"), []).append(rel)
        for link in re.findall(r"\]\(([^)]+)\)", text):
            if not link.startswith(("http", "/", "#")) and not os.path.exists(os.path.join(br["dir"], link)):
                errs.append(T(lib, f"{rel} 断链：{link}", f"{rel}: broken link {link}"))
        for rep in re.findall(r"[〔\[]×(\d+)[〕\]]", " ".join(standard_lines(text))):
            if int(rep) >= lim.get("repeat_promote_at", 3):
                warns.append(T(lib, f"{rel} 有标准被重复提醒 {rep} 次：智能体没遵守，考虑提到核心约定。", f"{rel}: a standard repeated {rep}×"))
                break
    for n, rels in names.items():
        if len(rels) > 1:
            warns.append(T(lib, f"分支重名「{n}」：{rels}", f"Duplicate branch {n}: {rels}"))
    for dp, dns, fns in os.walk(lib.root):
        dns[:] = [d for d in dns if not d.startswith(".")]
        for fn in fns:
            f = os.path.join(dp, fn)
            rel = os.path.relpath(f, lib.root)
            if fn.endswith(".md") and SECRET.search(read(f)):
                errs.append(T(lib, f"疑似密钥：{rel}（只写存放路径，别写原文）", f"Possible secret: {rel}"))
            elif not fn.endswith(".md") and not fn.startswith(".") and fn not in (CONFIG, "说明.txt", "README.txt"):
                warns.append(T(lib, f"非 MD 文件：{rel}（维护时会转换）", f"Non-MD file: {rel} (will be converted)"))
    for f in drop_files(lib):
        if (time.time() - os.path.getmtime(f)) > 3 * 86400:
            warns.append(T(lib, f"投放区文件放了 3 天以上没处理：{os.path.basename(f)}", f"Drop file pending > 3 days: {os.path.basename(f)}"))
    for layer in ("general", "tasks", "subjects"):
        d = lib.layer_dir(layer)
        for fn in (os.listdir(d) if os.path.isdir(d) else []):
            if fn.endswith(".md"):
                warns.append(T(lib, f"层目录里有散落文件：{lib.L['layers'][layer]}/{fn}（应放进某个分支）", f"Stray file in layer: {fn}"))
    return errs, warns


def cmd_lint(a):
    lib = find_root(a.root)
    errs, warns = lint_errors(lib)
    print(T(lib, f"路由块 {len(route_block(lib))} 字符（每次对话都加载，上限 {lib.cfg['limits']['route_block_chars']}）", f"Route block: {len(route_block(lib))} chars"))
    for w in warns:
        print("⚠ " + w)
    for e in errs:
        print("✗ " + e)
    if not errs and not warns:
        print(T(lib, "✓ 没发现问题", "✓ OK"))
    sys.exit(1 if errs else 0)


LOOSE_RULES = ["AGENTS.md", "CLAUDE.md", "GEMINI.md", "QWEN.md", ".cursorrules", ".windsurfrules", ".clinerules", "copilot-instructions.md", "CONVENTIONS.md", ".goosehints"]
SCAN_SKIP = {"Library", "node_modules", ".git", ".Trash", "Applications", "Pictures", "Movies", "Music", ".cache", ".npm", ".cargo", ".rustup", "venv", ".venv", "site-packages", "Pods", "DerivedData", "dist", "build", ".next"}


class _Stub:
    lang = "zh"
    cfg = {}


def cmd_scan(a):
    """只读盘点：不建库也能跑，不写任何文件。"""
    ptr = jload(POINTER, {}) or {}
    lib = None
    try:
        lib = Lib(ptr["root"]) if ptr.get("root") and os.path.exists(os.path.join(ptr["root"], CONFIG)) else None
    except SystemExit:
        lib = None
    ctx = lib or _Stub()
    collected = (jload(os.path.join(lib.fm, "state.json"), {}) or {}).get("native", {}) if lib else {}

    def info(f):
        st = os.stat(f)
        return {"path": f, "kb": round(st.st_size / 1024, 1), "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d"), "collected": f in collected}

    agents, seen = [], set()
    for ag in registry(ctx):
        if not detected(ag):
            continue
        mem = []
        for g in mem_globs(ag):
            for f in sorted(glob.glob(expand(g))):
                if os.path.isfile(f) and f not in seen and "cy-feedback-memory" not in os.path.basename(f):
                    seen.add(f)
                    mem.append(info(f))
        hv = ag.get("harvest")
        conv = sum(len(glob.glob(g)) for g in ADAPTERS[hv][1](ctx)) if hv in ADAPTERS else None
        has_block = ag["kind"] != "manual" and MARK_S in read(expand(ag["path"]))
        agents.append({"id": ag["id"], "name": ag["name"], "kind": ag["kind"], "status": ag["status"], "memory": mem, "transcripts": conv, "joined": has_block,
                       "headless": bool((ag.get("headless") or "").split(" ")[0] and shutil.which((ag.get("headless") or "").split(" ")[0]))})
    # 散落在各项目里的规则文件（项目级 AGENTS.md / CLAUDE.md / .cursorrules ……）
    loose = []
    roots = [HOME] + [os.path.expanduser(x) for x in (a.path or [])]
    for r0 in roots:
        base = r0.rstrip(os.sep).count(os.sep)
        for dp, dns, fns in os.walk(r0):
            depth = dp.count(os.sep) - base
            dns[:] = [] if depth >= a.depth else [d for d in dns if d not in SCAN_SKIP and not (d.startswith(".") and dp == HOME and d not in (".github",)) and not (lib and os.path.join(dp, d) == lib.root)]
            for fn in fns:
                f = os.path.join(dp, fn)
                rule_dir = dp.replace(os.sep, "/").endswith((".cursor/rules", ".windsurf/rules", ".trae/rules", ".kiro/steering", ".clinerules", ".roo/rules"))
                if (fn in LOOSE_RULES or (rule_dir and fn.endswith((".md", ".mdc")))) and f not in seen and os.path.isfile(f):
                    seen.add(f)
                    body = re.sub(re.escape(MARK_S) + ".*?" + re.escape(MARK_E), "", read(f), flags=re.S).strip()
                    if not body:
                        continue
                    h = hashlib.sha1(body.encode()).hexdigest()
                    dup = next((x for x in loose if x.get("_h") == h), None)
                    if dup:
                        dup["same_as"] = dup.get("same_as", []) + [f]
                        continue
                    it = info(f)
                    it["_h"] = h
                    loose.append(it)
    for x in loose:
        x.pop("_h", None)
    out = {"library": lib.root if lib else None, "scope": {"roots": roots, "depth": a.depth, "skipped": sorted(SCAN_SKIP)}, "agents": agents, "loose_rule_files": loose[:200]}
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return
    print(f"■ 经验库：{lib.root if lib else '还没建'}")
    print("\n■ 本机检测到的智能体（只读，未改动任何文件）")
    for ag in agents:
        tot = sum(m["kb"] for m in ag["memory"])
        print(f"  - {ag['name']}  id={ag['id']}  [{ag['status']}]  {'已接入' if ag['joined'] else '未接入'}  "
              f"规则/记忆 {len(ag['memory'])} 份 {tot:.0f}KB  对话记录 {ag['transcripts'] if ag['transcripts'] is not None else '不可采集'}  命令行 {'有' if ag['headless'] else '无'}")
        for m in ag["memory"][:8]:
            print(f"      · {m['path']}  {m['kb']}KB  {m['mtime']}" + ("  （已汇集过）" if m["collected"] else ""))
        if len(ag["memory"]) > 8:
            print(f"      · ……另 {len(ag['memory']) - 8} 份")
    if not agents:
        print("  （没检测到；可在库的 fm.config.json 的 custom_agents 里登记）")
    print(f"\n■ 散落的规则文件（{len(loose)} 份）")
    print(f"  扫描范围：{'、'.join(roots)}，往下 {a.depth} 层；跳过 {'、'.join(sorted(SCAN_SKIP)[:8])} 等目录和用户目录下的隐藏目录。要扩大：--depth N 或 --path 目录")
    for m in loose[:40]:
        print(f"  · {m['path']}  {m['kb']}KB  {m['mtime']}" + (f"  （另有 {len(m['same_as'])} 份内容相同）" if m.get("same_as") else ""))
    if len(loose) > 40:
        print(f"  · ……另 {len(loose) - 40} 份（--json 看全部）")
    print("\n下一步：collect --days 30 汇集全部（只要部分用 --agents id1,id2）；散落文件里和用户偏好有关的用 import <路径> 放进投放区。")


def skill_installed():
    return [d for d in SKILL_DIRS if os.path.exists(os.path.join(expand(d), "cy-feedback-memory"))]


def cmd_doctor(a):
    lib = find_root(a.root)
    st = jload(os.path.join(lib.fm, "state.json"), {}) or {}
    roster = jload(os.path.join(lib.fm, "roster.json"), {}) or {}
    m = lib.cfg.get("maintenance", {})
    tv = read(os.path.join(lib.fm, "tool", "VERSION")).strip()
    print(T(lib, f"■ 库：{lib.root}（格式 v{lib.cfg.get('format_version')}，库内工具 {tv or '无'}，当前运行 {VERSION}）", f"■ Library: {lib.root} (tool {tv or 'none'}, running {VERSION})"))
    if tv and vtuple(tv) < vtuple(VERSION):
        print(T(lib, "  ⚠ 库内工具比当前 skill 旧 → 运行 upgrade", "  ⚠ Embedded tool is older → run upgrade"))
    h = Lock(lib, "", "maint.lock", ttl=3 * 3600).holder()
    print(T(lib, f"  定期维护：{'开' if m.get('enabled') else '未设置'}" + (f"，{m.get('tz') or '本机时区'} {WEEKDAYS_ZH[WEEKDAYS.index(m['weekday'])] if m.get('weekday') else '每天'} {m.get('at')}，维护者 {m.get('curator')}，方式 {m.get('mode')}，系统任务 {'已安装（' + str(m.get('installed')) + '）' if m.get('installed') else '未安装'}" if m.get("enabled") else "") + f"；上次维护 {st.get('maint_day', '从未')}（{st.get('maint_by', '-')}）" + (f"；{h['who']} 正在维护" if h else ""),
            f"  Maintenance: {'on' if m.get('enabled') else 'off'}; last {st.get('maint_day', 'never')}"))
    drops = drop_files(lib)
    print(T(lib, f"  投放区 {len(drops)} 个文件，收件箱 {'有' if inbox_new(lib) else '空'}，待智能体转换 {len(st.get('needs_agent', []))} 个", f"  Drop {len(drops)}, inbox {'yes' if inbox_new(lib) else 'empty'}"))
    inst = skill_installed()
    print(T(lib, f"  skill 安装在：{', '.join(inst) or '（没找到常见目录，可能装在别处）'} —— 不是每个智能体都要装：有路由块就能读写", f"  Skill installed in: {', '.join(inst) or 'n/a'}"))
    print(T(lib, "\n■ 智能体", "\n■ Agents"))
    tips, curators = [], []
    for ag in registry(lib):
        if not detected(ag):
            continue
        if ag["kind"] == "manual":
            blk = T(lib, "需手动", "manual")
        else:
            blk = "✓" if MARK_S in read(expand(ag["path"])) else "✗"
            if blk == "✗":
                tips.append(T(lib, f"{ag['name']} 还没接入 → join --agent {ag['id']}", f"{ag['name']} not joined → join --agent {ag['id']}"))
        hv = ag.get("harvest")
        nfiles = sum(len(glob.glob(g)) for g in ADAPTERS[hv][1](lib)) if hv in ADAPTERS else 0
        mem = sum(1 for g in mem_globs(ag) for f in glob.glob(expand(g)) if os.path.isfile(f) and "cy-feedback-memory" not in f)
        cli = (ag.get("headless") or "").split(" ")[0]
        has_cli = bool(cli and shutil.which(cli))
        if has_cli:
            curators.append(ag["id"])
        print(f"  {blk} {ag['name']} [{ag['status']}]  " + T(lib, f"对话记录 {nfiles if hv else '不可采集'}，自带规则/记忆 {mem} 份，无头命令 {'有' if has_cli else '无'}，成员表 {'在' if ag['id'] in roster else '不在'}",
                                                        f"transcripts {nfiles if hv else 'n/a'}, native files {mem}, headless {'yes' if has_cli else 'no'}, roster {'yes' if ag['id'] in roster else 'no'}"))
        if ag["id"] == "claude-code" and mem and not read(expand("~/.claude/settings.json")).count('"autoMemoryEnabled": false'):
            tips.append(T(lib, "Claude Code 自带的自动记忆还开着：两套记忆会并存。可保留（每次维护会把它的新内容汇集进来），或在 settings.json 设 autoMemoryEnabled:false。", "Claude auto-memory is on; it will be collected each maintenance, or disable it."))
    if not st.get("native"):
        tips.append(T(lib, "还没做首次汇集 → collect --days 30（把各智能体已有的规则、记忆和近 30 天对话交给下次维护）", "No initial collection yet → collect --days 30"))
    if not m.get("enabled"):
        tips.append(T(lib, "定期维护没开 → schedule --weekday <额度刷新那天> --at <刷新前 1 小时>（可选的维护者：" + (", ".join(curators) or "没检测到命令行智能体，用应用自带定时任务 --print-prompt") + "）", "Maintenance off → schedule"))
    errs, warns = lint_errors(lib)
    if errs:
        tips.append(T(lib, f"有 {len(errs)} 处不合规 → lint", f"{len(errs)} lint errors → lint"))
    print(T(lib, "\n■ 建议", "\n■ Suggestions"))
    for t in tips or [T(lib, "一切正常。", "All good.")]:
        print("  - " + t)


def cmd_status(a):
    lib = find_root(a.root)
    st = jload(os.path.join(lib.fm, "state.json"), {}) or {}
    brs = lib.branches()
    print(T(lib, f"库：{lib.root}；分支 {len(brs)} 个，标准 {sum(len(standard_lines(read(b['file']))) for b in brs)} 条；上次维护 {st.get('maint_day', '从未')}（{st.get('maint_by', '-')}）。详细体检用 doctor。",
            f"Library {lib.root}; {len(brs)} branches; last maintained {st.get('maint_day', 'never')}. Use doctor for details."))


# ---------------------------------------------------------------- 导入导出
def cmd_import(a):
    lib = find_root(a.root)
    n = 0
    for src in a.paths:
        src = os.path.expanduser(src)
        files = [os.path.join(dp, f) for dp, _, fs in os.walk(src) for f in fs if not f.startswith(".")] if os.path.isdir(src) else [src]
        for f in files:
            rel = os.path.relpath(f, src) if os.path.isdir(src) else os.path.basename(f)
            name = re.sub(r"[^\w.\-]+", "_", rel)
            dst, k = os.path.join(lib.drop, name), 2
            while os.path.exists(dst):  # 同名不覆盖：带上来源目录名，再不行加序号
                b0, e0 = os.path.splitext(name)
                tag = re.sub(r"[^\w\-]+", "_", os.path.basename(os.path.dirname(os.path.abspath(f))))[:30]
                dst = os.path.join(lib.drop, f"{b0}（{tag}）{e0}" if k == 2 else f"{b0}（{tag}-{k}）{e0}")
                k += 1
            shutil.copy2(f, dst)
            n += 1
    journal(lib, who(a), "import", lib.L["drop"], f"{n} files")
    print(T(lib, f"已复制 {n} 个文件到投放区，下次维护时转成 MD 并只收其中的反馈和标准。", f"Copied {n} files to the drop zone."))


def cmd_export(a):
    lib = find_root(a.root)
    out = [T(lib, "以下是我的工作偏好和标准，请照做：", "My working preferences and standards — please follow them:"), ""] + core_items(lib)
    for br in lib.branches():
        if a.branch and a.branch not in (br["meta"].get("name"), os.path.basename(br["dir"])):
            continue
        lines = standard_lines(read(br["file"]))
        if lines and (a.all or a.branch):
            out += ["", f"【{br['meta'].get('name')}】"] + [re.sub(r"\s*→\s*\[.*?\]\(.*?\)\s*$", "", l) for l in lines]
    if not (a.all or a.branch):
        out += ["", T(lib, "（加 --all 导出所有分支的标准，或 --branch 名称）", "(use --all or --branch NAME)")]
    print("\n".join(out))


# ---------------------------------------------------------------- 定时
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAYS_ZH = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
TASK = "cy-feedback-memory"
LABEL = "com.cy-feedback-memory.curate"


def parse_weekday(v):
    """mon..sun / 1..7（周一=1）/ 周一..周日 → 0..6（周一=0）；空 → None（每天）。"""
    if v in (None, "", "daily", "day"):
        return None
    v = str(v).strip().lower()
    if v[:3] in WEEKDAYS:
        return WEEKDAYS.index(v[:3])
    for i, z in enumerate(WEEKDAYS_ZH):
        if v in (z, z.replace("周", "星期"), z.replace("周", "礼拜")) or (i == 6 and v in ("周天", "星期天")):
            return i
    if v.isdigit() and 1 <= int(v) <= 7:
        return int(v) - 1
    sys.exit(f"看不懂的星期：{v}（用 mon..sun、1..7 或 周一..周日）")


def tznow(tz):
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(tz)) if tz else datetime.now().astimezone()
    except Exception:
        return datetime.now().astimezone()


def local_minute(at, tz):
    """目标时区 hh:mm 落在本机是第几分（只差半小时/45 分钟的时区才不同）。"""
    hh, mm = map(int, at.split(":"))
    t = tznow(tz).replace(hour=hh, minute=mm, second=0, microsecond=0)
    return t.astimezone().minute


def backlog(lib):
    """零 token 估算积压：对话预筛后的字数 + 收件箱 + 投放区文件大小。"""
    r = do_harvest(lib, material_path(lib) + ".dry", dry="measure")
    return r


def decide(lib, at=None, tz=None, weekday=None, dry=False):
    """到点判断，返回 (动作, 理由)。动作：run / skip。不调用模型。
    混合策略：每周固定一次（weekday+at，默认额度刷新前 1 小时）；
    其余日子每天 at 之后看一次积压，超过阈值就提前维护。"""
    m = lib.cfg.get("maintenance", {})
    at = at or m.get("at") or "12:00"
    tz = tz if tz is not None else m.get("tz")
    wd = parse_weekday(weekday if weekday is not None else m.get("weekday"))
    t = tznow(tz)
    hh, mm = map(int, at.split(":"))
    st_p = os.path.join(lib.fm, "state.json")
    st = jload(st_p, {}) or {}
    last = parse_ts(st.get("maint_done_at"))
    today_t = t.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if wd is None:  # 每天模式
        if t >= today_t and (last is None or last < today_t) and (t - today_t).total_seconds() <= 12 * 3600:
            return "run", T(lib, "每日维护到点", "daily slot")
        return "skip", T(lib, "还没到点或今天已维护", "not due")
    week_t = today_t - timedelta(days=(t.weekday() - wd) % 7)
    if week_t > t:
        week_t -= timedelta(days=7)
    if (last is None or last < week_t) and (t - week_t).total_seconds() <= 48 * 3600:
        return "run", T(lib, f"每周维护到点（{WEEKDAYS_ZH[wd]} {at}）", f"weekly slot ({WEEKDAYS[wd]} {at})")
    # 平日：每天到点后只估算一次积压
    day = t.strftime("%Y-%m-%d")
    if t < today_t or st.get("backlog_day") == day or (last and last.astimezone(t.tzinfo).strftime("%Y-%m-%d") == day):
        return "skip", T(lib, "未到每周维护，今天已看过积压", "not due")
    r = backlog(lib)
    limit = int(m.get("backlog_chars", 40000))
    if not dry:  # 试跑只读：不记「今天已看过积压」，否则紧接着的正式运行会被跳过
        st = jload(st_p, {}) or {}
        st["backlog_day"], st["backlog_chars"] = day, r["chars"]
        jsave(st_p, st)
    if r["more"] or r["chars"] >= limit:
        return "run", T(lib, f"积压 {r['chars']} 字，超过阈值 {limit}，提前维护", f"backlog {r['chars']} ≥ {limit}")
    return "skip", T(lib, f"积压 {r['chars']} 字，未到阈值 {limit}，等每周维护", f"backlog {r['chars']} < {limit}")


def cmd_due(a):
    lib = find_root(a.root)
    act, why = decide(lib, a.at, a.tz, a.weekday)
    print(("RUN " if act == "run" else "SKIP ") + why)
    sys.exit(0 if act == "run" else 1)


def curate_prompt(lib):
    tpl = read(os.path.join(SKILL, "references", f"curate-prompt.{lib.lang}.md"))
    return fill(tpl, lib.L, {"{ROOT}": lib.root, "{FM}": lib.fmcmd(), "{PROTOCOL}": lib.p(lib.L["protocol"])})


def tick_log(lib, s):
    append(os.path.join(lib.fm, "maint.log"), f"[{stamp()}] {s}\n")


def cmd_tick(a):
    """定时器每小时调一次：先零 token 判断，要维护且有新内容才唤起值班智能体。"""
    lib = find_root(a.root)
    m = lib.cfg.get("maintenance", {})
    if not m.get("enabled"):
        print("SKIP " + T(lib, "定期维护没开", "maintenance off"))
        return
    if m.get("path"):
        os.environ["PATH"] = m["path"] + os.pathsep + os.environ.get("PATH", "")
    if hold_state(lib).get("on") and read(proposal_path(lib)).strip():
        print("SKIP " + T(lib, "有一批在等用户确认，不唤起值班智能体", "awaiting user confirmation"))
        return
    act, why = decide(lib, dry=a.dry)
    if act != "run" and not a.force:
        print("SKIP " + why)
        return
    if act != "run":
        why = T(lib, "手动强制（--force）", "forced")
    sub = argparse.Namespace(root=lib.root, agent="scheduler", action="check", summary=None, force=False)
    has = do_harvest(lib, material_path(lib) + ".dry", dry=True) or bool(read(OFFLINE).strip()) or any(not f.lower().endswith(".md") for f in drop_files(lib))
    if not has and a.dry:
        print("RUN " + why + " · " + NOTHING)
        return
    if not has:
        sub.action = "start"
        cmd_maint(sub)  # 没新内容：脚本自己记一笔「已维护」，不调用模型
        tick_log(lib, f"{why} → {NOTHING}")
        return
    if a.dry:
        print("RUN " + why)
        return
    cmd = m.get("curator_cmd")
    if not cmd:
        print("RUN " + why + T(lib, "（没有命令行值班者：由应用里的定时任务接手）", " (no CLI curator; app task will handle it)"))
        return
    write(os.path.join(lib.fm, "curate-prompt.md"), curate_prompt(lib))
    prompt = T(lib, f"按 {os.path.join(lib.fm, 'curate-prompt.md')} 里的步骤维护经验库，你的 --agent 名字是 {m.get('curator')}。",
               f"Maintain the feedback memory following {os.path.join(lib.fm, 'curate-prompt.md')}; use --agent {m.get('curator')}.")
    argv = [prompt if x == "{prompt}" else x for x in cmd]
    tick_log(lib, f"{why} → {' '.join(argv[:3])} …")
    print("RUN " + why)
    flags = 0x08000000 if PLAT == "win" else 0  # CREATE_NO_WINDOW
    with open(os.path.join(lib.fm, "maint.log"), "a", encoding="utf-8") as logf:
        try:
            r = subprocess.run(argv, cwd=lib.root, stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, timeout=3 * 3600, creationflags=flags)
            tick_log(lib, f"exit {r.returncode}")
        except Exception as e:
            tick_log(lib, f"✗ {e}")


def resolve_curator(lib, curator):
    """把值班者的无头命令拆成参数列表，并把可执行文件解析成绝对路径（定时器里 PATH 很短）。"""
    import shlex
    ag = next((x for x in registry(lib) if x["id"] == curator), None)
    raw = (ag or {}).get("headless") or curator
    if "{prompt}" not in raw:
        raw += " {prompt}"
    argv = shlex.split(raw, posix=True)
    exe = shutil.which(argv[0])
    if exe:
        argv[0] = exe
    return argv, bool(exe)


def interp():
    py = sys.executable or "python3"
    if PLAT == "win":
        w = os.path.join(os.path.dirname(py), "pythonw.exe")
        if os.path.exists(w):
            return w  # 每小时触发时不弹黑窗
    return py


def install_timer(lib, minute, remove=False):
    """每小时在第 minute 分触发一次 tick；三个平台都能补跑睡眠期间错过的触发。"""
    py, tool = interp(), lib.tool
    argv = [py, tool, "--root", lib.root, "tick"]
    out = []
    if PLAT == "mac":
        plist = os.path.join(HOME, "Library", "LaunchAgents", LABEL + ".plist")
        subprocess.run(["launchctl", "unload", plist], capture_output=True)
        if remove:
            if os.path.exists(plist):
                os.remove(plist)
            return [T(lib, f"已移除 {plist}", f"Removed {plist}")]
        from xml.sax.saxutils import escape
        args = "".join(f"<string>{escape(x)}</string>" for x in argv)
        write(plist, '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                     f'<plist version="1.0"><dict><key>Label</key><string>{LABEL}</string><key>ProgramArguments</key><array>{args}</array>'
                     f'<key>StartCalendarInterval</key><dict><key>Minute</key><integer>{minute}</integer></dict><key>RunAtLoad</key><false/></dict></plist>\n')
        r = subprocess.run(["launchctl", "load", plist], capture_output=True, text=True)
        chk = subprocess.run(["launchctl", "list", LABEL], capture_output=True, text=True)
        err = (r.stderr or "").strip()
        if r.returncode != 0 or chk.returncode != 0 or "error" in err.lower() or "failed" in err.lower():
            return ["✗ " + T(lib, f"launchd 没有登记成功：{err or '（launchctl list 查不到）'}；plist 留在 {plist}", f"launchd registration failed: {err}")]
        return [T(lib, f"已登记 launchd：{plist}（每小时第 {minute} 分检查一次，睡眠错过的唤醒后补跑）", f"Registered launchd: {plist}")]
    if PLAT == "win":
        if remove:
            r = subprocess.run(["schtasks", "/Delete", "/TN", TASK, "/F"], capture_output=True, text=True)
            return [T(lib, "已删除计划任务", "Scheduled task removed") if r.returncode == 0 else (r.stderr or r.stdout).strip()]
        if (jload(POINTER, {}) or {}).get("root") == lib.root:
            argv = [py, tool, "tick"]  # schtasks 的 /TR 最长 261 字符：本机指针就是这个库时省掉 --root
        tr = subprocess.list2cmdline(argv)
        if len(tr) > 261:
            return [T(lib, f"✗ 命令太长（{len(tr)} 字符，schtasks 上限 261）：把库或 Python 装到短一点的路径，或用 --print-prompt 交给应用定时任务", f"✗ Command too long for schtasks ({len(tr)} > 261)")]
        r = subprocess.run(["schtasks", "/Create", "/SC", "HOURLY", "/ST", f"00:{minute:02d}", "/TN", TASK, "/TR", tr, "/F"], capture_output=True, text=True)
        q = subprocess.run(["schtasks", "/Query", "/TN", TASK], capture_output=True, text=True) if r.returncode == 0 else r
        out.append(T(lib, f"已登记计划任务「{TASK}」（每小时第 {minute} 分）", f"Registered scheduled task {TASK}") if r.returncode == 0 and q.returncode == 0 else "✗ " + T(lib, "计划任务没有登记成功：", "schtasks failed: ") + ((r.stderr or r.stdout or "") + (q.stderr or "")).strip())
        # 让错过的触发在开机/唤醒后补跑（StartWhenAvailable）；PowerShell 不可用时忽略
        ps = shutil.which("powershell") or shutil.which("pwsh")
        if ps and r.returncode == 0:
            subprocess.run([ps, "-NoProfile", "-Command", f"$s=New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries; Set-ScheduledTask -TaskName '{TASK}' -Settings $s | Out-Null"], capture_output=True)
        return out
    # Linux：优先 systemd 用户定时器（Persistent 可补跑），否则 crontab
    unit_dir = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config"), "systemd", "user")
    has_systemd = shutil.which("systemctl") and subprocess.run(["systemctl", "--user", "show-environment"], capture_output=True).returncode == 0
    if has_systemd:
        svc, tim = os.path.join(unit_dir, TASK + ".service"), os.path.join(unit_dir, TASK + ".timer")
        if remove:
            subprocess.run(["systemctl", "--user", "disable", "--now", TASK + ".timer"], capture_output=True)
            for f in (svc, tim):
                if os.path.exists(f):
                    os.remove(f)
            subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
            out.append(T(lib, "已移除 systemd 定时器", "systemd timer removed"))
            if not shutil.which("crontab"):
                return out
        else:
            import shlex
            write(svc, f"[Unit]\nDescription=cy-feedback-memory tick\n\n[Service]\nType=oneshot\nExecStart={' '.join(shlex.quote(x) for x in argv)}\n")
            write(tim, f"[Unit]\nDescription=cy-feedback-memory hourly tick\n\n[Timer]\nOnCalendar=*-*-* *:{minute:02d}:00\nPersistent=true\n\n[Install]\nWantedBy=timers.target\n")
            subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
            r = subprocess.run(["systemctl", "--user", "enable", "--now", TASK + ".timer"], capture_output=True, text=True)
            if r.returncode == 0:
                r = subprocess.run(["systemctl", "--user", "is-active", TASK + ".timer"], capture_output=True, text=True)
            out.append(T(lib, f"已安装 systemd 用户定时器：{tim}（每小时第 {minute} 分，关机错过的开机补跑；想在未登录时也跑：loginctl enable-linger）", f"Installed systemd user timer: {tim}") if r.returncode == 0 else "✗ " + r.stderr.strip())
            return out
    if not shutil.which("crontab"):
        return out + [T(lib, "✗ 没有 systemd 用户会话也没有 crontab：请用应用自带的定时任务（--print-prompt）", "✗ No systemd user session or crontab")]
    import shlex
    cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    keep = [l for l in cur.splitlines() if TASK not in l]
    if not remove:
        keep.append(f"{minute} * * * * {' '.join(shlex.quote(x) for x in argv)} >/dev/null 2>&1 # {TASK}")
    r = subprocess.run(["crontab", "-"], input="\n".join(keep) + "\n", capture_output=True, text=True)
    if r.returncode == 0 and not remove and TASK not in subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout:
        r = subprocess.CompletedProcess([], 1, "", T(lib, "写入后 crontab -l 查不到", "not found in crontab -l"))
    return out + [(T(lib, "已从 crontab 移除", "Removed from crontab") if remove else T(lib, f"已写入 crontab（每小时第 {minute} 分；cron 不补跑睡眠期间的触发，但下一个小时会接上）", "Written to crontab")) if r.returncode == 0 else "✗ " + r.stderr.strip()]


def cmd_schedule(a):
    lib = find_root(a.root)
    cfg_p = lib.p(CONFIG)
    m = lib.cfg.setdefault("maintenance", {})
    if a.off:
        m["enabled"] = False
        jsave(cfg_p, lib.cfg)
        for line in install_timer(lib, 0, remove=True):
            print(line)
        print(T(lib, "定期维护已关闭（应用里自建的定时任务请在应用里删除）。", "Scheduled maintenance turned off."))
        return
    if a.reset:  # 额度刷新时间 → 往前推 1 小时（跨零点星期也往前推），不靠智能体心算
        parts = a.reset.replace("，", " ").replace(",", " ").split()
        if len(parts) != 2 or ":" not in parts[1]:
            sys.exit(T(lib, "--reset 格式：\"星期 时:分\"，如 \"thu 09:00\" 或 \"周四 00:30\"", "--reset format: \"thu 09:00\""))
        rwd = parse_weekday(parts[0])
        rh, rm = map(int, parts[1].split(":"))
        base = datetime(2024, 1, 1 + rwd, rh, rm) - timedelta(minutes=a.lead)  # 2024-01-01 是周一
        a.weekday, a.at = WEEKDAYS[base.weekday()], base.strftime("%H:%M")
        print(T(lib, f"额度刷新：{WEEKDAYS_ZH[rwd]} {parts[1]}（{a.tz or '本机时区'}）→ 每周维护：{WEEKDAYS_ZH[base.weekday()]} {a.at}",
                f"Quota resets {WEEKDAYS[rwd]} {parts[1]} → weekly maintenance {WEEKDAYS[base.weekday()]} {a.at}"))
    wd = parse_weekday(a.weekday)
    write(os.path.join(lib.fm, "curate-prompt.md"), curate_prompt(lib))
    mode = "app-task" if (a.print_prompt or not a.curator) else "os-timer"
    m.update({"enabled": True, "at": a.at, "tz": a.tz, "weekday": WEEKDAYS[wd] if wd is not None else None,
              "curator": a.curator or a.agent or "app-task", "mode": mode, "path": os.environ.get("PATH", "")})
    if a.backlog:
        m["backlog_chars"] = a.backlog
    m.setdefault("backlog_chars", 40000)
    when_zh = (WEEKDAYS_ZH[wd] if wd is not None else "每天") + " " + a.at + f"（{a.tz or '本机时区'}）"
    when_en = (WEEKDAYS[wd] if wd is not None else "daily") + " " + a.at + f" ({a.tz or 'local'})"
    hybrid = T(lib, f"；平日每天 {a.at} 后零 token 估算一次积压，超过 {m['backlog_chars']} 字就提前维护" if wd is not None else "",
               f"; on other days, backlog ≥ {m['backlog_chars']} chars triggers an early run" if wd is not None else "")
    if mode == "os-timer":
        argv, found = resolve_curator(lib, a.curator)
        m["curator_cmd"] = argv
        if not found:
            print(T(lib, f"⚠ 找不到命令 {argv[0]}：先确认这个智能体有命令行版本并已登录，或改用 --print-prompt。", f"⚠ {argv[0]} not found in PATH."))
    else:
        m.pop("curator_cmd", None)
    m["installed"] = "app-task" if mode == "app-task" else False
    jsave(cfg_p, lib.cfg)
    journal(lib, who(a), "schedule", mode, f"{when_en} {m['curator']}")
    if mode == "app-task":
        fmc = lib.fmcmd()
        print(T(lib, f"把下面这段建成智能体应用里的**每天**定时任务（时间 {a.at}，{a.tz or '本机时区'}）。它先用脚本零 token 判断今天要不要维护：每周 {when_zh} 必做{hybrid}。多个应用都建也没关系：同一轮只会有一个真正执行。\n",
                f"Create a DAILY scheduled task in your agent app at {a.at} ({a.tz or 'local'}) with this instruction. A zero-token check decides: weekly run {when_en}{hybrid}.\n"))
        print(T(lib, f"先运行 `{fmc} due --agent app-task`。输出 SKIP 开头：回复一句「今天不用维护」并结束，不做别的。输出 RUN 开头：继续下面的步骤。\n",
                f"First run `{fmc} due`. Output starting with SKIP → reply one line and stop. RUN → continue below.\n"))
        print(curate_prompt(lib))
        return
    print(T(lib, f"✓ 配置已保存：每周 {when_zh}{hybrid}。值班：{m['curator']}。", f"✓ Config saved: {when_en}{hybrid}. Curator: {m['curator']}."))
    if a.install:
        lines = install_timer(lib, local_minute(a.at, a.tz))
        ok = not any(str(x).startswith("✗") for x in lines)
        m["installed"] = PLAT if ok else False
        jsave(cfg_p, lib.cfg)
        for line in lines:
            print(line)
        print(T(lib, "✓ 系统任务已登记（已向系统查询确认）。" if ok else "✗ 系统任务没登记上：配置已保存，但不会自动运行。", "✓ OS timer registered (verified)." if ok else "✗ OS timer NOT registered."))
    else:
        print(T(lib, "✗ 系统任务未安装：只保存了配置，不会自动运行。加 --install 安装（macOS launchd / Linux systemd 或 crontab / Windows 计划任务）。", "✗ OS timer not installed (config only). Add --install."))
    print(T(lib, f"日志：{os.path.join(lib.fm, 'maint.log')}。随时试跑：fm tick --dry", f"Log: {os.path.join(lib.fm, 'maint.log')}. Test: fm tick --dry"))


# ---------------------------------------------------------------- main
def main():
    for st in (sys.stdout, sys.stderr):  # Windows 控制台不是 UTF-8 时别因中文崩掉
        try:
            st.reconfigure(errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(prog="fm.py", description="cy-feedback-memory " + VERSION)
    ap.add_argument("--root", help="库根目录（默认读 ~/.cy-feedback-memory/config.json 或环境变量 CY_FM_ROOT）")
    sp = ap.add_subparsers(dest="cmd", required=True)

    def sub(name, agent=True):
        p = sp.add_parser(name)
        if agent:
            p.add_argument("--agent", help="你是谁（智能体 id 或名字），写进接力日志；也可设环境变量 CY_FM_AGENT")
        return p

    p = sub("init"); p.add_argument("root"); p.add_argument("--lang", choices=["zh", "en"], default="zh"); p.add_argument("--force", action="store_true")
    p = sub("join"); p.add_argument("--project", action="append")
    p = sub("sync", False); p.add_argument("--agents"); p.add_argument("--project", action="append"); p.add_argument("--remove", action="store_true"); p.add_argument("--check", action="store_true")
    sub("doctor", False); sub("status", False)
    p = sub("upgrade"); p.add_argument("--force", action="store_true")
    p = sub("add"); p.add_argument("--branch"); p.add_argument("--text"); p.add_argument("--batch", help="JSONL 文件，一行一条，一次写入多条"); p.add_argument("--words"); p.add_argument("--scope", choices=["always", "candidate", "once", "reference"], default="always")
    p.add_argument("--key"); p.add_argument("--source"); p.add_argument("--topic"); p.add_argument("--detail", help="条目文件名（已写好的 条目/xxx.md）"); p.add_argument("--create", action="store_true")
    p.add_argument("--layer", choices=["general", "tasks", "subjects"], default="tasks"); p.add_argument("--triggers"); p.add_argument("--summary")
    p = sub("remember"); p.add_argument("text"); p.add_argument("--scope", choices=["once", "always", "candidate"]); p.add_argument("--branch"); p.add_argument("--key"); p.add_argument("--source"); p.add_argument("--note")
    p = sub("place"); p.add_argument("file"); p.add_argument("--branch"); p.add_argument("--done", action="store_true"); p.add_argument("--note")
    p = sub("journal", False); p.add_argument("-n", type=int, default=30)
    p = sub("maint"); p.add_argument("action", choices=["start", "finish", "check"]); p.add_argument("--summary"); p.add_argument("--force", action="store_true"); p.add_argument("--hold", action="store_true", help="先确认模式：只列拟写入，不提交")
    p = sub("hold"); p.add_argument("action", nargs="?", default="status", choices=["on", "off", "status", "apply", "discard"]); p.add_argument("--note")
    p = sub("collect"); p.add_argument("--days", type=int); p.add_argument("--agents", help="只汇集这些智能体（逗号分隔 id），默认全部检测到的"); p.add_argument("--all", action="store_true", help="不管有没有变化都重新汇集")
    sub("convert")
    p = sub("harvest", False); p.add_argument("--out", default=os.path.join(os.environ.get("TMPDIR", "/tmp"), "cy-fm-material.md")); p.add_argument("--commit", action="store_true")
    sub("lint", False)
    p = sub("export", False); p.add_argument("--branch"); p.add_argument("--all", action="store_true")
    p = sub("import"); p.add_argument("paths", nargs="+")
    p = sub("schedule"); p.add_argument("--at", default="12:00"); p.add_argument("--tz"); p.add_argument("--weekday", help="每周哪天（mon..sun / 1..7 / 周一..周日）；不填=每天"); p.add_argument("--backlog", type=int, help="平日积压超过多少字就提前维护（默认 40000）"); p.add_argument("--reset", help="每周额度刷新时间，如 \"thu 09:00\"；自动往前推 --lead 分钟作为维护时间"); p.add_argument("--lead", type=int, default=60, help="比额度刷新提前多少分钟（默认 60）"); p.add_argument("--curator"); p.add_argument("--install", action="store_true"); p.add_argument("--print-prompt", action="store_true"); p.add_argument("--off", action="store_true")
    p = sub("due"); p.add_argument("--at"); p.add_argument("--tz"); p.add_argument("--weekday")
    p = sub("tick", False); p.add_argument("--dry", action="store_true", help="只判断不唤起智能体"); p.add_argument("--force", action="store_true", help="不管到没到点")
    p = sub("scan", False); p.add_argument("--json", action="store_true"); p.add_argument("--depth", type=int, default=4, help="在用户目录下找散落规则文件的深度"); p.add_argument("--path", action="append", help="额外要找的目录")
    p = sub("read", False); p.add_argument("branch", nargs="+"); p.add_argument("--numbered", action="store_true"); p.add_argument("--standards-only", action="store_true")
    p = sub("archive"); p.add_argument("--branch", required=True); p.add_argument("--items"); p.add_argument("--match", action="append", help="条目开头文字（推荐，可多次）"); p.add_argument("--reason", required=True)
    p.add_argument("--section", choices=["standards", "candidates", "reference"], default="candidates")
    p = sub("move"); p.add_argument("--branch", required=True); p.add_argument("--items", help="序号，如 3,5,7（fm read --numbered 看）"); p.add_argument("--match", action="append", help="条目开头文字（推荐，可多次；序号会随增删变化）"); p.add_argument("--to", required=True)
    p.add_argument("--section", choices=["standards", "candidates", "reference"], default="standards"); p.add_argument("--create", action="store_true")
    p.add_argument("--layer", choices=["general", "tasks", "subjects"], default="tasks"); p.add_argument("--triggers"); p.add_argument("--summary")
    a = ap.parse_args()
    if a.cmd == "add" and not a.batch and not (a.branch and a.text):
        ap.error("add 需要 --branch 和 --text，或 --batch 文件")
    if a.cmd == "place" and not (a.branch or a.done):
        ap.error("place 需要 --branch 或 --done")
    globals()["cmd_" + a.cmd.replace("-", "_")](a)


if __name__ == "__main__":
    main()
