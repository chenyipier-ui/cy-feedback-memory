#!/usr/bin/env python3
"""安装 cy-feedback-memory：复制进本机各智能体的 skills 目录，然后只读盘点一次。

用法：python3 install.py [--agents claude,codex] [--dest 目录] [--no-scan]
只用标准库；macOS / Linux / Windows 通用。不建库、不改任何智能体配置——那些在你对智能体说「帮我建经验库」后才做。
"""
import argparse, os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "cy-feedback-memory"
# (简称, 智能体目录, skills 目录)
TARGETS = [("claude", "~/.claude", "~/.claude/skills"), ("codex", "~/.codex", "~/.codex/skills"), ("agents", "~/.agents", "~/.agents/skills"),
           ("gemini", "~/.gemini", "~/.gemini/skills"), ("opencode", "~/.config/opencode", "~/.config/opencode/skill"), ("qwen", "~/.qwen", "~/.qwen/skills"),
           ("cursor", "~/.cursor", "~/.cursor/skills"), ("kiro", "~/.kiro", "~/.kiro/skills")]
SKIP = shutil.ignore_patterns(".git", ".github", "__pycache__", "*.pyc", ".DS_Store", "tests")
REQUIRED = ("SKILL.md", "VERSION", "scripts/fm.py", "scripts/fm_convert.py", "data/agents.json", "data/keywords.json", "data/layouts.json")


def is_existing_skill(dst):
    if not all(os.path.isfile(os.path.join(dst, part)) for part in ("VERSION", "scripts/fm.py")):
        return False
    try:
        with open(os.path.join(dst, "SKILL.md"), encoding="utf-8") as skill:
            if skill.readline().strip() != "---":
                return False
            for line in skill:
                if line.strip() == "---":
                    break
                if line.strip() == f"name: {NAME}":
                    return True
    except (OSError, UnicodeError):
        pass
    return False


def safe_replace(dst):
    """校验暂存副本后再替换本项目旧版，保留旧版中的自定义文件。"""
    exists = os.path.lexists(dst)
    if exists and not is_existing_skill(dst):
        raise RuntimeError("存在同名文件或目录，无法识别为本项目的旧版，已保留原样")
    parent = os.path.dirname(dst)
    tmp = tempfile.mkdtemp(prefix=f".{NAME}-new-", dir=parent)
    new = os.path.join(tmp, NAME)
    old = os.path.join(tmp, "old")
    keep_backup = False
    try:
        shutil.copytree(HERE, new, ignore=SKIP)
        for part in REQUIRED:
            path = os.path.join(new, part)
            if not os.path.isfile(path) or os.path.getsize(path) == 0:
                raise RuntimeError(f"新版缺少有效文件 {part}")
        if exists:
            for directory, _, files in os.walk(dst):
                rel = os.path.relpath(directory, dst)
                for name in files:
                    if name.endswith(".pyc") or name == ".DS_Store":
                        continue
                    target = os.path.join(new, rel, name)
                    if not os.path.exists(target):
                        os.makedirs(os.path.dirname(target), exist_ok=True)
                        shutil.copy2(os.path.join(directory, name), target)
        try:
            if exists:
                os.rename(dst, old)
            os.rename(new, dst)
        except BaseException:
            if os.path.lexists(old):
                keep_backup = True
                try:
                    os.rename(old, dst)
                except OSError as error:
                    raise RuntimeError(f"无法还原旧版，备份留在 {old}：{error}") from error
                keep_backup = False
            raise
    finally:
        if not keep_backup:
            shutil.rmtree(tmp, ignore_errors=True)


def main():
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--agents", help="只装到这些（" + ",".join(t[0] for t in TARGETS) + "），默认装到所有检测到的")
    ap.add_argument("--dest", action="append", help="额外的 skills 目录")
    ap.add_argument("--no-scan", action="store_true")
    a = ap.parse_args()
    want = set(a.agents.split(",")) if a.agents else None
    dests = [os.path.expanduser(s) for k, d, s in TARGETS if (want is None and os.path.isdir(os.path.expanduser(d))) or (want and k in want)]
    dests += [os.path.expanduser(d) for d in (a.dest or [])]
    if not dests:
        sys.exit("没检测到常见智能体目录。用 --dest <skills 目录> 指定。")
    done, failed = [], []
    for d in dests:
        dst = os.path.join(d, NAME)
        if os.path.realpath(dst) == os.path.realpath(HERE):
            done.append(dst + "（就是当前目录）")
            continue
        try:
            os.makedirs(d, exist_ok=True)
            safe_replace(dst)
            done.append(dst)
        except Exception as e:
            failed.append(f"{dst}：{e}")
    if done:
        print("已安装到：\n  " + "\n  ".join(done), flush=True)
    if failed:
        print("安装失败：\n  " + "\n  ".join(failed), file=sys.stderr)
    if not done:
        return 1
    if not a.no_scan:
        print("\n—— 只读盘点（不写任何文件）——\n", flush=True)
        scan = subprocess.run([sys.executable, "-B", os.path.join(HERE, "scripts", "fm.py"), "scan"])
        if scan.returncode:
            print(f"盘点失败（退出码 {scan.returncode}）。", file=sys.stderr)
            return 1
    print("\n下一步：在任意一个已安装的智能体里说「帮我建经验库」。")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
