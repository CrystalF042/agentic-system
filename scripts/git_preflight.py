#!/usr/bin/env python3
"""推到 GitHub 之前的闸 —— **红了就别推。**

    python3 scripts/git_preflight.py            检查（红了退出码 1）
    python3 scripts/git_preflight.py --list     把「这次会进仓库的文件」全列出来

## 为什么要有这道闸

git 有历史。**一个文件一旦被 commit 过，后来删掉它并不会把它从仓库里去掉。**
所以这件事和这套系统里其他所有事都不一样：

    别的错   下一版修掉就行
    这个错   推出去的那一刻就不可撤销 —— 只能换密钥、重建仓库

这个目录里同时躺着三样东西：

```
.env                你的 Anthropic key、Telegram token、chat id
*.db                你的账本：持仓、成本、每一笔交易
raw-data/ 等        论点台账、复核台账、信号卡片、心跳
```

`.gitignore` 已经把它们都排除了。**这个脚本不是重写那份名单，
是验证它真的生效了**——两件事：写了规则，和规则确实拦住了。

## 三层检查

    一、将要进仓库的文件清单里有没有黑名单里的东西
    二、那些文件的内容里有没有像密钥的字符串（.gitignore 写对了也可能
        有人把 key 粘进了 README）
    三、**历史里**有没有提交过这些东西（这一条最要紧：
        工作区干净不代表历史干净）

第三条查出问题时，删文件是没用的。脚本会明说：**换密钥、重建仓库。**
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# ---- 文件名黑名单：路径里出现这些片段就不该进仓库 --------------------------
DENY_PATH = [
    ".env",
    ".db",
    "raw-data/",
    "logs/",
    "memory/",
    "lancedb/",
    "out/",
    "Company Archive/",
    "Topic Archive/",
    ".venv/",
    "research/ledger.yaml",
]
ALLOW_PATH = [".env.example", ".gitignore"]
"""例外。`.env.example` 是模板（里面是占位符，不是真值）。"""

# ---- 内容黑名单：像密钥的字符串 --------------------------------------------
SECRET_PATTERNS = [
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"), "Anthropic API key"),
    # 长度写死 35 的话，差一位就漏 —— **安全闸宁可多喊，不可漏喊。**
    (re.compile(r"\b\d{7,12}:[A-Za-z0-9_\-]{30,45}\b"), "Telegram bot token"),
    (re.compile(r"\bsk-[A-Za-z0-9]{32,}\b"), "OpenAI 风格的 key"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"), "GitHub token"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS access key"),
    (re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[=:]\s*"
                r"['\"]?([A-Za-z0-9_\-]{24,})['\"]?"), "看起来是一个真实的密钥值"),
]

PLACEHOLDER_MARKS = ("...", "…", "your_", "xxx", "changeme", "placeholder",
                     "<", ">", "example", "dummy", "sample", "replace_me")
"""占位符不算泄漏。

**只拿它比对「匹配到的那一段」，不比对整行。**

第一版是比对整行的，而且列表里有 `"abcdef"`——于是

    CIO_ANTHROPIC_API_KEY=sk-ant-api03-<一串真的 key>abcdefghij

因为末尾恰好有 `abcdef`，**整行被当成占位符放过了**。
一条真 key 里出现 `abcdef` 一点都不稀奇（base62 就那几十个字符）。

一道从来不红的闸，和一道不存在的闸，是同一回事 ——
这条是被自己的用例当场抓到的。"""

TEXT_SUFFIX = {".py", ".md", ".txt", ".yaml", ".yml", ".json", ".toml", ".cfg",
               ".ini", ".sh", ".env", ".example", ".html", ".css", ".js", ".R",
               ".sql", ""}

BIG_MB = 5.0


def _git(*args, check=True):
    r = subprocess.run(["git", *args], cwd=str(ROOT),
                       capture_output=True, text=True)
    if check and r.returncode != 0:
        return None
    return r.stdout


def in_repo() -> bool:
    return _git("rev-parse", "--is-inside-work-tree") is not None


def has_commits() -> bool:
    return _git("rev-parse", "HEAD") is not None


def tracked_and_new() -> list:
    """**这次会进仓库的文件**：已跟踪的 + 未跟踪且没被忽略的。

    不是 `ls` 整个目录 —— 被 `.gitignore` 挡住的本来就不会进去，
    把它们也算进来只会得到一堆假警报，而假警报会让人学会忽略这道闸。
    """
    out = _git("ls-files", "-co", "--exclude-standard")
    return sorted({l.strip() for l in (out or "").splitlines() if l.strip()})


def committed_ever() -> list:
    """**历史里**出现过的所有文件名。工作区干净 ≠ 历史干净。"""
    if not has_commits():
        return []
    out = _git("log", "--all", "--pretty=format:", "--name-only",
               "--diff-filter=A")
    return sorted({l.strip() for l in (out or "").splitlines() if l.strip()})


def denied(path: str) -> str:
    p = path.replace("\\", "/")
    for ok in ALLOW_PATH:
        if p.endswith(ok) or p == ok:
            return ""
    for bad in DENY_PATH:
        if bad.endswith("/"):
            if p.startswith(bad) or f"/{bad}" in p:
                return bad
        elif p.endswith(bad) or f"/{bad}" in p or p == bad.lstrip("."):
            return bad
    return ""


def scan_secrets(paths: list) -> list:
    hits = []
    for rel in paths:
        f = ROOT / rel
        if not f.is_file():
            continue
        if f.suffix.lower() not in TEXT_SUFFIX:
            continue
        try:
            if f.stat().st_size > 2_000_000:
                continue
            text = f.read_text("utf-8", errors="ignore")
        except Exception:                                      # noqa: BLE001
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for pat, what in SECRET_PATTERNS:
                m = pat.search(line)
                if not m:
                    continue
                frag = m.group(0)
                # **拿匹配到的那一段比对占位符，不拿整行。**
                # 拿整行比对的话，一行里任何一处出现 "example"
                # 都会把这一行的真 key 一起放过。
                if any(ph in frag.lower() for ph in PLACEHOLDER_MARKS):
                    continue
                hits.append((rel, i, what,
                             frag[:12] + "…" + frag[-4:] if len(frag) > 20
                             else frag[:8] + "…"))
                break
    return hits


def big_files(paths: list) -> list:
    out = []
    for rel in paths:
        f = ROOT / rel
        try:
            mb = f.stat().st_size / 1e6
        except Exception:                                      # noqa: BLE001
            continue
        if mb >= BIG_MB:
            out.append((rel, mb))
    return sorted(out, key=lambda x: -x[1])


def main(argv: list) -> int:
    print("=" * 68)
    print("推到 GitHub 之前的检查")
    print("=" * 68)

    if not in_repo():
        print("\n这个目录还不是 git 仓库。先跑：")
        print("    cd ~/.openclaw/workspace/cio-agent")
        print("    git init")
        print("\n**顺序很要紧**：先有 .gitignore，再 git init，再 git add。")
        print("反过来的话，第一次 add 就会把 .env 和账本收进去。")
        return 1

    files = tracked_and_new()
    if "--list" in argv:
        print(f"\n这次会进仓库的文件（{len(files)} 个）：\n")
        for f in files:
            print(f"  {f}")
        return 0

    bad = 0

    # ---- 一、.gitignore 真的在拦 ----
    print("\n[1] .gitignore 拦住了该拦的没有")
    if not (ROOT / ".gitignore").exists():
        print("  **NG** 根本没有 .gitignore")
        bad += 1
    else:
        probes = [".env", "cio.db", "raw-data/x.json", "logs/a.log",
                  "memory/2026-01-01.md", "lancedb/x", "out/x.pdf"]
        missed = []
        for p in probes:
            r = subprocess.run(["git", "check-ignore", "-q", p],
                               cwd=str(ROOT), capture_output=True)
            if r.returncode != 0:
                missed.append(p)
        if missed:
            print("  **NG** 这些没有被忽略：" + "、".join(missed))
            bad += 1
        else:
            print(f"  OK   {len(probes)} 个探针全部被忽略")

    # ---- 二、将要进仓库的清单里有没有黑名单 ----
    print(f"\n[2] 这次会进仓库的 {len(files)} 个文件里有没有不该进的")
    offenders = [(f, denied(f)) for f in files if denied(f)]
    if offenders:
        print(f"  **NG** {len(offenders)} 个：")
        for f, why in offenders[:20]:
            print(f"       {f}　（命中 {why}）")
        bad += 1
    else:
        print("  OK   没有")

    # ---- 三、内容里有没有像密钥的字符串 ----
    print("\n[3] 那些文件的内容里有没有像密钥的字符串")
    hits = scan_secrets(files)
    if hits:
        print(f"  **NG** {len(hits)} 处：")
        for rel, ln, what, frag in hits[:20]:
            print(f"       {rel}:{ln}　{what}　{frag}")
        bad += 1
    else:
        print("  OK   没有")

    # ---- 四、历史里有没有提交过 ----
    print("\n[4] **历史**里有没有提交过这些东西（工作区干净 ≠ 历史干净）")
    if not has_commits():
        print("  OK   还没有任何 commit，历史是干净的")
    else:
        past = [(f, denied(f)) for f in committed_ever() if denied(f)]
        if past:
            print(f"  **NG** 历史里有 {len(past)} 个：")
            for f, why in past[:20]:
                print(f"       {f}　（命中 {why}）")
            print()
            print("  **删掉文件是没用的 —— git 记得住。** 要做的是：")
            print("    1. 立刻换掉所有出现过的密钥"
                  "（@BotFather 撤 Telegram token；Anthropic 后台撤 API key）")
            print("    2. 这个仓库不要再用了，删掉重建一个")
            print("    3. 重建时先写 .gitignore，再 git init，再 git add")
            bad += 1
        else:
            print("  OK   历史里没有")

    # ---- 五、大文件（不致命，但值得看一眼）----
    print(f"\n[5] 有没有大于 {BIG_MB:.0f} MB 的文件（不致命，但会让每次 clone 变慢）")
    big = big_files(files)
    if big:
        for rel, mb in big[:10]:
            print(f"  注意 {rel}　{mb:.1f} MB")
    else:
        print("  OK   没有")

    print("\n" + "=" * 68)
    if bad:
        print(f"**{bad} 项不通过 —— 先别推。** 按上面每一条的提示处理完再跑一次。")
        return 1
    print("全部通过。可以推了。")
    print("\n下一步：")
    print("    git add -A")
    print("    git status")
    print("    git commit -m \"build124: 自动流水线 Build 1-5 + 辩论引擎可切换\"")
    print("    git push -u origin main")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
