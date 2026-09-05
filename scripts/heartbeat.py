#!/usr/bin/env python3
"""心跳查看器 —— **"今天没有"和"今天没跑"的答案在这里。**

    python scripts/heartbeat.py              今天的报告 + 最近缺的日子
    python scripts/heartbeat.py --last 10    最近 10 天，一天一行
    python scripts/heartbeat.py --day 2026-09-04
    python scripts/heartbeat.py --missing 30 只回答"哪些工作日根本没跑"

## 为什么"缺失的日子"要单独列出来

盘前简报静默失踪三天那次，磁盘上、收件箱里、日志里都**什么都没有**，
而"什么都没有"同时是两件事的样子：今天平安无事，和今天根本没跑。

心跳报告一天一份。**有那份文件 = 那天跑过了**，哪怕它全是 0。
没有那份文件 = 那天没跑 —— 这个函数就是把它说出来。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cio import heartbeat                                      # noqa: E402
from cio import schedule as sched                              # noqa: E402


def _one_line(day: str, rep: dict) -> str:
    bits = []
    for st in rep.get("stages", []):
        mark = {"ok": "✓", "failed": "✗", "skipped": "–", "not_run": "·"}.get(
            st.get("status", "not_run"), "?")
        bits.append(mark)
    counts = {}
    for st in rep.get("stages", []):
        counts.update(st.get("counts") or {})
    tail = "　".join(f"{k} {v}" for k, v in counts.items())
    return f"{day}  {''.join(bits)}　{tail}"


def _legend() -> str:
    return "（✓完成　✗失败　–跳过　·未运行；阶段顺序见 heartbeat.PIPELINE）"


def main(argv: list) -> int:
    if "--missing" in argv:
        back = int(argv[argv.index("--missing") + 1])
        miss = heartbeat.missing_days(back=back)
        if not miss:
            print(f"最近 {back} 天的工作日**每天都有心跳报告**。")
            return 0
        print(f"最近 {back} 天里**没有心跳报告**的工作日（= 那天根本没跑）：")
        for d in miss:
            print(f"  {d}")
        return 1

    if "--day" in argv:
        day = argv[argv.index("--day") + 1][:10]
        rep = heartbeat.load(day)
        if rep is None:
            print(f"{day} **没有心跳报告** —— 那天没跑（不是那天没事）。")
            return 1
        print(_render(rep))
        return 0

    if "--last" in argv:
        n = int(argv[argv.index("--last") + 1])
        ds = heartbeat.dates()[-n:]
        if not ds:
            print("一份心跳报告都还没有。")
            return 1
        print(_legend())
        for d in ds:
            rep = heartbeat.load(d)
            if rep:
                print(_one_line(d, rep))
        miss = heartbeat.missing_days(back=n)
        if miss:
            print()
            print("**这些工作日没有报告**：" + "、".join(miss))
        return 0

    today = sched.market_now().strftime("%Y-%m-%d")
    rep = heartbeat.load(today)
    if rep is None:
        print(f"{today}（市场时区）**还没有心跳报告**。")
        print("如果今天是工作日而且已经过了收盘窗口，那就是它没跑。")
    else:
        print(_render(rep))
    miss = heartbeat.missing_days(back=10)
    if miss:
        print()
        print("**最近 10 天里没有报告的工作日**：" + "、".join(miss))
        print("（没有报告 ≠ 那天没事。是那天根本没跑。）")
    return 0


def _render(rep: dict) -> str:
    out = [f"CIO 流水线心跳　{rep.get('as_of')}　"
           f"（{rep.get('schema_version')}）"]
    for st in rep.get("stages", []):
        head = (f"[{st.get('label')}] "
                f"{heartbeat.STATUS_CN.get(st.get('status'), '?')}")
        cs = st.get("counts") or {}
        if cs:
            head += "　" + "　".join(f"{k} {v}" for k, v in cs.items())
        out.append(head)
        if st.get("error"):
            out.append(f"    {st['error']}")
        for n in st.get("notes") or []:
            out.append(f"    {n}")
    return "\n".join(out)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
