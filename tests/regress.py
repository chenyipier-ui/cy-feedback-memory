"""回归测试：外部审核复现过的问题逐条验收。临时目录运行，不碰真实定时器。用法：python tests/regress.py"""
import json, os, shutil, subprocess, sys, tempfile, datetime as dt
from unittest.mock import patch

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
FM = os.path.join(ROOT, "scripts", "fm.py")
home = tempfile.mkdtemp(prefix="fm-reg-")
env = dict(os.environ, HOME=home, USERPROFILE=home, APPDATA=os.path.join(home, "AppData"), PYTHONIOENCODING="utf-8")
lib = os.path.join(home, "lib")
os.makedirs(os.path.join(home, ".claude"))


def fm(*args, ok=(0,), agent="a"):
    r = subprocess.run([sys.executable, FM, *args, *([] if args[0] in ("read", "lint", "scan", "tick", "sync", "export") else ["--agent", agent])],
                       env=env, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode in ok, f"fm {args} → {r.returncode}\n{r.stdout}\n{r.stderr}"
    return r.stdout + r.stderr


def check(name, cond):
    print(("✓ " if cond else "✗ ") + name)
    assert cond, name


fm("init", lib)

# 1 安装器：复制失败时旧版和用户自定义文件都在；成功时自定义文件带过去
sys.path.insert(0, ROOT)
import install  # noqa: E402
skills = os.path.join(home, "skills")
dst = os.path.join(skills, "cy-feedback-memory")
os.makedirs(skills)
install.safe_replace(dst)
open(os.path.join(dst, "VERSION"), "w", encoding="utf-8").write("old")
open(os.path.join(dst, "my-notes.md"), "w", encoding="utf-8").write("自定义")
with patch.object(install.shutil, "copytree", side_effect=OSError("磁盘满")):
    try:
        install.safe_replace(dst)
    except OSError:
        pass
check("安装失败时旧版保留", open(os.path.join(dst, "VERSION"), encoding="utf-8").read() == "old" and os.path.exists(os.path.join(dst, "my-notes.md")))

rename = os.rename
def fail_new_rename(src, target):
    if os.path.basename(src) == "cy-feedback-memory" and target == dst and src != dst:
        raise OSError("换入新版失败")
    return rename(src, target)


with patch.object(install.os, "rename", side_effect=fail_new_rename):
    try:
        install.safe_replace(dst)
    except OSError:
        pass
check("换入新版失败时还原旧版", open(os.path.join(dst, "VERSION"), encoding="utf-8").read() == "old")
install.safe_replace(dst)
check("安装成功后是新版且自定义文件还在", open(os.path.join(dst, "VERSION"), encoding="utf-8").read().strip() != "old" and os.path.exists(os.path.join(dst, "my-notes.md")))
check("没有留下临时目录", [x for x in os.listdir(skills) if x.startswith(".")] == [])

def run_install(*args, root=ROOT):
    return subprocess.run([sys.executable, os.path.join(root, "install.py"), *args],
                          env=env, capture_output=True, text=True, encoding="utf-8")


other = os.path.join(home, "other")
other_skill = os.path.join(other, "cy-feedback-memory")
shutil.copytree(dst, other_skill)
other_metadata = os.path.join(other_skill, "SKILL.md")
other_contents = "---\nname: other-project\n---\n独立项目\n"
with open(other_metadata, "w", encoding="utf-8") as f:
    f.write(other_contents)
r = run_install("--agents", "codex", "--dest", other, "--no-scan")
with open(other_metadata, encoding="utf-8") as f:
    check("部分安装失败返回非零，同名项目原样保留", r.returncode == 1 and "安装失败" in r.stderr and f.read() == other_contents)
check("其余目标仍可安装", os.path.isfile(os.path.join(home, ".codex", "skills", "cy-feedback-memory", "SKILL.md")))
r = run_install("--agents", "codex", "--dest", os.path.join(other, "missing"), "--no-scan")
check("重复安装成功返回零", r.returncode == 0)

incomplete = os.path.join(home, "incomplete")
shutil.copytree(ROOT, incomplete, ignore=install.SKIP)
incomplete_dst = os.path.join(home, "AppData", "incomplete-test")
os.remove(os.path.join(incomplete, "scripts", "fm_convert.py"))
r = run_install("--agents", "codex", "--dest", incomplete_dst, "--no-scan", root=incomplete)
check("新版缺少运行文件时全部失败，退出非零", r.returncode == 1 and "已安装到" not in r.stdout and "fm_convert.py" in r.stderr)
shutil.copy2(os.path.join(ROOT, "scripts", "fm_convert.py"), os.path.join(incomplete, "scripts", "fm_convert.py"))
with open(os.path.join(incomplete, "scripts", "fm.py"), "w", encoding="utf-8") as f:
    f.write("raise SystemExit(7)\n")
r = run_install("--agents", "codex", "--dest", incomplete_dst, root=incomplete)
check("盘点失败退出非零，安装结果仍逐项打印", r.returncode == 1 and "已安装到" in r.stdout and "盘点失败（退出码 7）" in r.stderr)

# 2 两个客户目录的同名资料：导入、转换后两份 MD 和两份原件都在
for c in ("客户A", "客户B"):
    os.makedirs(os.path.join(home, c))
    open(os.path.join(home, c, "规范.txt"), "w", encoding="utf-8").write(f"{c} 的规范")
fm("import", os.path.join(home, "客户A", "规范.txt"), os.path.join(home, "客户B", "规范.txt"))
fm("convert")
drop = os.path.join(lib, "投放区")
mds = sorted(f for f in os.listdir(drop) if f.endswith(".md"))
texts = "".join(open(os.path.join(drop, f), encoding="utf-8").read() for f in mds)
origs = [f for dp, _, fs in os.walk(os.path.join(lib, ".fm", "originals")) for f in fs]
check("同名资料两份 MD 都在", "客户A 的规范" in texts and "客户B 的规范" in texts)
check("同名资料两份原件都在", len(origs) == 2)
for f in mds:
    fm("place", os.path.join(drop, f), "--done")

# 3 同一个键 -2 dB → +2 dB：更新而不是当重复
fm("add", "--branch", "协作方式", "--text", "口播增益 -2 dB", "--key", "vo.gain", "--words", "以后口播 -2")
fm("add", "--branch", "协作方式", "--text", "口播增益 +2 dB", "--key", "vo.gain", "--words", "改成 +2")
br = open(os.path.join(lib, "通用", "协作方式", "经验.md"), encoding="utf-8").read()
check("正负号不同的标准被更新", "+2 dB" in br and "-2 dB" not in br and "×2" not in br)
check("旧值进了归档", "-2 dB" in open(os.path.join(lib, "归档", "协作方式.md"), encoding="utf-8").read())
fm("add", "--branch", "协作方式", "--text", "节奏 ≥ 1.2 倍", "--words", "x")
fm("add", "--branch", "协作方式", "--text", "节奏 ≤ 1.2 倍", "--words", "y")
br = open(os.path.join(lib, "通用", "协作方式", "经验.md"), encoding="utf-8").read()
check("比较符不同的两条分开保留", "≥ 1.2" in br and "≤ 1.2" in br)

# 4 别人不能提交我这一轮维护
open(os.path.join(lib, "收件箱.md"), "a", encoding="utf-8").write("- 记一下：以后封面不用红色\n")
out = fm("maint", "start", agent="A")
check("A 开始维护", "READY" in out)
out = fm("maint", "finish", "--summary", "x", ok=(1,), agent="B")
check("B 提交被拒", "NOT-YOURS" in out)
check("A 的锁还在", os.path.exists(os.path.join(lib, ".fm", "maint.lock")))
check("收件箱没被清", "封面不用红色" in open(os.path.join(lib, "收件箱.md"), encoding="utf-8").read())
fm("maint", "finish", "--summary", "ok", agent="A")

# 5 试跑只读：tick --dry 之后正式判断结果一致
cfg_p = os.path.join(lib, "fm.config.json")
cfg = json.load(open(cfg_p, encoding="utf-8"))
cfg["maintenance"].update({"enabled": True, "at": "00:00", "weekday": "mon" if dt.datetime.now().weekday() != 0 else "tue", "backlog_chars": 1})
json.dump(cfg, open(cfg_p, "w", encoding="utf-8"), ensure_ascii=False)
st_p = os.path.join(lib, ".fm", "state.json")
st = json.load(open(st_p))
st["maint_done_at"] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).isoformat()
st["maint_day"] = "2000-01-01"
st.pop("backlog_day", None)
json.dump(st, open(st_p, "w"))
open(os.path.join(drop, "新资料.md"), "w", encoding="utf-8").write("以后标题短")
d1 = fm("tick", "--dry")
d2 = fm("due", ok=(0, 1))
check("试跑后正式判断仍是 RUN", d1.startswith("RUN") and d2.startswith("RUN"))
os.remove(os.path.join(drop, "新资料.md"))

# 5b macOS 登记失败要如实报失败
sys.argv = ["x"]
sys.path.insert(0, os.path.join(ROOT, "scripts"))
os.environ.update(HOME=home, USERPROFILE=home, APPDATA=os.path.join(home, "AppData"))
import fm as F  # noqa: E402


class R:
    def __init__(s, rc):
        s.returncode, s.stdout, s.stderr = rc, "", ""


F.PLAT = "mac"
_real_run = subprocess.run
F.subprocess.run = lambda argv, **k: R(1 if argv[:2] == ["launchctl", "list"] else 0)
try:
    lines = F.install_timer(F.Lib(lib), 5)
finally:
    subprocess.run = _real_run
check("launchd 没登记上时报 ✗", lines and lines[0].startswith("✗"))

# 6 Codex 新版记忆路径能被盘点到
os.makedirs(os.path.join(home, ".codex", "memories_v2"))
open(os.path.join(home, ".codex", "memories_v2", "memory_summary.md"), "w", encoding="utf-8").write("偏好：短")
check("盘点找到 memories_v2", "memories_v2" in fm("scan", "--depth", "1"))

# 7 首次回溯分批续跑不漏；长发言保留结尾的拍板
st = json.load(open(st_p))
for k in ("last", "seen"):
    st.pop(k, None)
st["maint_day"] = "2000-01-01"
json.dump(st, open(st_p, "w"))
proj = os.path.join(home, ".claude", "projects", "p")
os.makedirs(proj)
ts = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=20)).isoformat()
rows = [{"type": "user", "timestamp": ts, "message": {"content": f"第{i}条：以后标题必须短，" + "细节" * 290}} for i in range(180)]
rows.append({"type": "user", "timestamp": ts, "message": {"content": "以后片头这样处理：" + "铺垫" * 800 + "最后拍板：音量定为 -14 LUFS"}})
open(os.path.join(proj, "s.jsonl"), "w", encoding="utf-8").write("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
got, rounds = set(), 0
mat_p = os.path.join(lib, ".fm", "material.md")
while rounds < 6:
    rounds += 1
    out = fm("maint", "start", "--force")
    if "READY" not in out:
        break
    mat = open(mat_p, encoding="utf-8").read()
    got |= {i for i in range(181) if f"第{i}条：" in mat}
    if "-14 LUFS" in mat:
        got.add("tail")
    if "MORE" not in fm("maint", "finish", "--summary", f"r{rounds}"):
        break
    if rounds == 1:  # 分批时起点被固定，不随时间往后滑（否则跨过首次回溯窗口后剩下的会被跳过）
        last = json.load(open(st_p)).get("last")
        check("分批续跑固定了起点", bool(last) and dt.datetime.fromisoformat(last) <= dt.datetime.fromisoformat(ts))
check(f"180 条分 {rounds} 轮全部收齐", all(i in got for i in range(180)))
check("长发言末尾的拍板数字被保留", "tail" in got)

# 8 「这批先给我看」：状态持久，换智能体也等；确认后写入
open(os.path.join(lib, "收件箱.md"), "a", encoding="utf-8").write("- 以后字幕不超过 14 字\n")
fm("hold", "on", "--note", "用户要先看", agent="claude")
out = fm("maint", "start", "--force", agent="codex")
check("开始维护时提示 HOLD", "HOLD" in out)
open(os.path.join(lib, ".fm", "proposal.jsonl"), "w", encoding="utf-8").write(json.dumps({"branch": "协作方式", "text": "字幕不超过 14 字", "words": "以后字幕不超过 14 字"}, ensure_ascii=False) + "\n")
fm("maint", "finish", "--hold", agent="codex")
check("未确认前没写库", "14 字" not in open(os.path.join(lib, "通用", "协作方式", "经验.md"), encoding="utf-8").read())
check("另一个智能体再维护会等待", "WAITING" in fm("maint", "start", "--force", agent="gemini"))
check("定时器不唤起值班者", "SKIP" in fm("tick", "--force"))
fm("hold", "apply", agent="gemini")
check("确认后写入", "字幕不超过 14 字" in open(os.path.join(lib, "通用", "协作方式", "经验.md"), encoding="utf-8").read())
check("收件箱已清", "14 字" not in open(os.path.join(lib, "收件箱.md"), encoding="utf-8").read().split("↓")[-1])

# 9 归档：原样移入归档并写理由
fm("add", "--branch", "协作方式", "--text", "待删的旧候选", "--scope", "candidate", "--words", "z")
out = fm("read", "协作方式", "--numbered")
n = out.split("待验证")[1].count("\n") - 1
fm("archive", "--branch", "协作方式", "--items", str(n), "--reason", "已升为标准")
check("归档后分支里没有了", "待删的旧候选" not in open(os.path.join(lib, "通用", "协作方式", "经验.md"), encoding="utf-8").read())
check("归档文件里原样保留并有理由", "待删的旧候选" in open(os.path.join(lib, "归档", "协作方式.md"), encoding="utf-8").read() and "已升为标准" in open(os.path.join(lib, "归档", "协作方式.md"), encoding="utf-8").read())

# 10 按开头文字选条目：歧义时拒绝，唯一时精确命中
fm("add", "--branch", "协作方式", "--text", "甲方案先给结论", "--scope", "candidate", "--words", "z1")
fm("add", "--branch", "协作方式", "--text", "甲方案附表放后面", "--scope", "candidate", "--words", "z2")
check("开头文字有歧义时拒绝", "匹配到 2 条" in fm("archive", "--branch", "协作方式", "--match", "甲方案", "--reason", "x", ok=(1,)))
fm("archive", "--branch", "协作方式", "--match", "甲方案附表", "--reason", "测试")
br = open(os.path.join(lib, "通用", "协作方式", "经验.md"), encoding="utf-8").read()
check("只归档了命中的那一条", "甲方案附表" not in br and "甲方案先给结论" in br)

shutil.rmtree(home, ignore_errors=True)
print("REGRESS OK")
