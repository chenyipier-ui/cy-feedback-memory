"""冒烟测试：在临时用户目录里走一遍建库 → 写入 → 定时 → tick → 卸载。三平台通用。
用法：python tests/smoke.py [--real-timer]   （--real-timer 会真的注册并删除系统定时器，CI 里用）"""
import os, subprocess, sys, tempfile, json, shutil

FM = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "fm.py")
home = tempfile.mkdtemp(prefix="fm-home-")
env = dict(os.environ, HOME=home, USERPROFILE=home, APPDATA=os.path.join(home, "AppData"), PYTHONIOENCODING="utf-8", CY_FM_AGENT="smoke")
real = "--real-timer" in sys.argv
if real and sys.platform.startswith("linux"):
    # An existing systemd user manager loads units from its own configuration directory.
    env["XDG_CONFIG_HOME"] = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
os.makedirs(os.path.join(home, ".claude"))
open(os.path.join(home, ".claude", "CLAUDE.md"), "w", encoding="utf-8").write("- 回答用中文\n")
lib = os.path.join(home, "lib")
fake = os.path.join(home, "fakecur.py")
open(fake, "w").write("import sys,os\nopen(os.path.join(os.path.expanduser('~'),'curated.txt'),'a',encoding='utf-8').write(sys.argv[-1]+'\\n')\n")


def fm(*args, ok=(0,)):
    r = subprocess.run([sys.executable, FM, *args], env=env, capture_output=True, text=True, encoding="utf-8")
    print(f"$ fm {' '.join(args)}  → {r.returncode}\n{r.stdout.strip()[:600]}{r.stderr.strip()[:600]}\n")
    assert r.returncode in ok, r.stderr
    return r.stdout


def timer_registered():
    task = "cy-feedback-memory"
    if sys.platform == "win32":
        return subprocess.run(["schtasks", "/Query", "/TN", task], capture_output=True).returncode == 0
    if sys.platform == "darwin":
        return subprocess.run(["launchctl", "list", "com.cy-feedback-memory.curate"], capture_output=True).returncode == 0
    if shutil.which("systemctl"):
        result = subprocess.run(["systemctl", "--user", "is-active", "--quiet", task + ".timer"], capture_output=True)
        if result.returncode == 0:
            return True
    if shutil.which("crontab"):
        result = subprocess.run(["crontab", "-l"], capture_output=True)
        return task.encode("ascii") in result.stdout
    return False


fm("init", lib)
fm("scan", "--depth", "2")
fm("sync")
fm("collect", "--days", "3")
fm("add", "--branch", "协作方式", "--text", "先给结论（任何任务）", "--words", "以后先给结论")
cfg_p = os.path.join(lib, "fm.config.json")
cfg = json.load(open(cfg_p, encoding="utf-8"))
py = sys.executable.replace("\\", "/")
cfg["custom_agents"] = [{"id": "fake", "name": "Fake", "kind": "manual", "detect": [], "status": "guess", "headless": f'"{py}" "{fake.replace(chr(92), "/")}" {{prompt}}'}]
json.dump(cfg, open(cfg_p, "w", encoding="utf-8"), ensure_ascii=False)
fm("schedule", "--weekday", "mon", "--at", "00:00", "--curator", "fake")
fm("due", ok=(0, 1))
open(os.path.join(lib, "投放区", "note.md"), "w", encoding="utf-8").write("以后标题不超过 12 个字\n")
fm("tick", "--force")
assert os.path.exists(os.path.join(home, "curated.txt")), "值班智能体没有被唤起"
fm("lint")

# —— 回归：审核意见里修的几处 ——
# 1) 同一天、不同目录的同名原件不能互相覆盖
import zipfile
def mk_docx(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>' + text + '</w:t></w:r></w:p></w:body></w:document>')
mk_docx(os.path.join(lib, "投放区", "a", "规范.docx"), "甲")
mk_docx(os.path.join(lib, "投放区", "b", "规范.docx"), "乙")
fm("convert")
origs = [os.path.join(dp, f) for dp, _, fs in os.walk(os.path.join(lib, ".fm", "originals")) for f in fs if f.endswith(".docx")]
assert len(origs) == 2, origs
# 2) 超过 300 行的表格要标注截断
open(os.path.join(lib, "投放区", "big.csv"), "w", encoding="utf-8").write("a,b\n" + "".join(f"{i},x\n" for i in range(400)))
fm("convert")
mds = [os.path.join(dp, f) for dp, _, fs in os.walk(os.path.join(lib, "投放区")) for f in fs if f.startswith("big") and f.endswith(".md")]
assert mds and "表格已截断" in open(mds[0], encoding="utf-8").read()
# 3) 预筛：普通长发言不收，命中反馈词才收；超长会话按发言切批
proj = os.path.join(home, ".claude", "projects", "p")
os.makedirs(proj, exist_ok=True)
import datetime as _dt
ts = _dt.datetime.now(_dt.timezone.utc).isoformat()
lines = [json.dumps({"type": "user", "timestamp": ts, "message": {"content": "帮我把这个函数重构一下然后跑一遍测试看看结果是不是都通过了，顺便整理一下目录结构吧"}})]
lines += [json.dumps({"type": "user", "timestamp": ts, "message": {"content": f"第{i}条：以后标题必须短，" + "细节" * 290}}) for i in range(150)]
open(os.path.join(proj, "s.jsonl"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
fm("maint", "start", "--force")
mat = open(os.path.join(lib, ".fm", "material.md"), encoding="utf-8").read()
assert not any(l.startswith("**") and "重构一下然后跑一遍测试" in l for l in mat.splitlines()), "普通长发言不该作为反馈进材料（主题行除外）"
assert "第0条" in mat and "第149条" not in mat, "超长会话应在本轮预算处切开"
assert len(mat) < 70000, len(mat)
out = fm("maint", "finish", "--summary", "smoke", ok=(0, 1))
assert "MORE" in out
fm("maint", "start", "--force")
mat2 = open(os.path.join(lib, ".fm", "material.md"), encoding="utf-8").read()
assert "第0条" not in mat2 and "第149条" in mat2 or "MORE" in fm("maint", "finish", "--summary", "r2", ok=(0, 1)), "第二轮应接着处理剩下的发言"
# 5) 无损拆分：fm move 原样搬运，行数守恒、详情文件跟着走；fm read 只出标准和待验证
br_dir = os.path.join(lib, "通用", "协作方式")
os.makedirs(os.path.join(br_dir, "条目"), exist_ok=True)
open(os.path.join(br_dir, "条目", "d.md"), "w", encoding="utf-8").write("来历")
fm("add", "--branch", "协作方式", "--text", "字幕单行（口播）", "--words", "以后字幕单行")
fm("add", "--branch", "协作方式", "--text", "配乐低于人声（全部）", "--words", "以后配乐小声", "--detail", "d.md")
fm("add", "--branch", "协作方式", "--text", "可能要更短", "--scope", "candidate")
before = open(os.path.join(br_dir, "经验.md"), encoding="utf-8").read()
out = fm("read", "协作方式", "--numbered")
assert "待验证" in out and "资料" not in out.split("待验证")[0][-5:]
fm("move", "--branch", "协作方式", "--items", "3", "--to", "音乐", "--create", "--layer", "general", "--triggers", "配乐,音乐", "--summary", "配乐")
music = open(os.path.join(lib, "通用", "音乐", "经验.md"), encoding="utf-8").read()
assert "- 配乐低于人声（全部） → [详情](条目/d.md)" in music, music
assert os.path.exists(os.path.join(lib, "通用", "音乐", "条目", "d.md"))
assert "配乐低于人声" not in open(os.path.join(br_dir, "经验.md"), encoding="utf-8").read()
fm("lint")
# 6) 批量写入
bf = os.path.join(home, "batch.jsonl")
open(bf, "w", encoding="utf-8").write(json.dumps({"branch": "音乐", "text": "转场处换曲", "words": "以后转场换曲"}, ensure_ascii=False) + "\n" + json.dumps({"branch": "协作方式", "text": "先给结论（任何任务）", "words": "又说一次"}, ensure_ascii=False) + "\n")
out = fm("add", "--batch", bf)
assert "成功 2 条" in out and "〔×2〕" in open(os.path.join(br_dir, "经验.md"), encoding="utf-8").read()

# 4) 额度刷新时间自动往前推 1 小时（跨零点连星期一起推）；未安装要如实说
out = fm("schedule", "--reset", "周一 00:30", "--curator", "fake")
assert "周日 23:30" in out and "系统任务未安装" in out
if real:
    try:
        out = fm("schedule", "--weekday", "mon", "--at", "00:00", "--curator", "fake", "--install")
        assert "系统任务已登记" in out and timer_registered(), out
    finally:
        fm("schedule", "--off")
    assert not timer_registered(), "系统任务仍在"
shutil.rmtree(home, ignore_errors=True)
print("SMOKE OK")
