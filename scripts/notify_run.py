#!/usr/bin/env python3
"""待批提醒 —— **"有 N 条提案等你批准"这句话，到没到你手机上。**

    python scripts/notify_run.py             有待批且内容变了 / 挂太久 → 推
    python scripts/notify_run.py --dry-run   走同一条路，最后一步不真发
    python scripts/notify_run.py --force     不管去重，现在就推一次
    python scripts/notify_run.py --status    通知台账：上次真的送到是什么时候
    python scripts/notify_run.py --text      只把要发的内容印出来，什么都不发

## 平时不用单独跑它

`scripts/research_run.py` 每次跑完自己会推（在「待你批准」那一节里）。
本入口是给两种情况用的：

    推送失败之后想立刻重试      直接再跑一次，台账里没记过就还会推
    想确认"到底会发什么出去"    `--text`

## 一条规矩

**演习不算送到。** `--dry-run` 和 `CIO_TG_DRYRUN=1` 都只会得到 `dryrun`，
**不写通知台账**——否则一次演习就能让那条真正要她批的消息永远不再发出。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cio import heartbeat, notify, portfolio                    # noqa: E402
from cio import schedule as sched                               # noqa: E402
from cio.config import market                                   # noqa: E402


def _pid(argv) -> str:
    if "--portfolio" in argv:
        i = argv.index("--portfolio")
        if i + 1 < len(argv):
            return argv[i + 1]
    return portfolio.MARKET_PORTFOLIO.get(
        market().get("news_region", "us"), portfolio.US_PAPER)


def main(argv: list) -> int:
    day = sched.market_now().strftime("%Y-%m-%d")
    pid = _pid(argv)

    if "--status" in argv:
        from cio import proposal_store
        s = notify.state(pid)
        rows = proposal_store.pending(pid)
        print(f"通知台账　{pid}")
        print(f"  当前待批　　{len(rows)} 条")
        print(f"  上次真送到　{s.get('last_sent_day') or '**从来没有**'}"
              f"（{s.get('last_sent_at') or '—'}）　累计 {s.get('n_sent', 0)} 次")
        print(f"  记着的那批　{s.get('fingerprint') or '（无）'}")
        print(f"  现在这一批　{notify.fingerprint(rows)}")
        if rows and notify.fingerprint(rows) != s.get("fingerprint"):
            print("  → **内容变了，下次跑会推。**")
        ag = notify.aged(rows, day)
        ex = notify.expiring(rows, day)
        if ag:
            print(f"  挂太久　　　{len(ag)} 条："
                  + "、".join(f"#{r['id']} {r['ticker']}（{r['_age']} 个交易日）"
                              for r in ag))
        if ex:
            print(f"  快过期　　　{len(ex)} 条："
                  + "、".join(f"#{r['id']} {r['ticker']}（还剩 {r['_left']} 天）"
                              for r in ex))
        return 0

    if "--text" in argv:
        from cio import proposal_store
        rows = proposal_store.pending(pid)
        if not rows:
            print(f"{pid}：没有待批的提案 —— **不发消息。**"
                  "\n（每天推一条「今天 0 条」就是一盏常亮的灯。）")
            return 0
        text, kb = notify.message(pid, rows, day)
        print(text)
        print(f"\n（{len(kb)} 组按钮，需要 run_tgbot.py 在跑才点得动）")
        return 0

    rep = heartbeat.Report(as_of=day)
    for k in ("technical_snapshot", "research_router", "research_queue",
              "unit_a", "cro_pc"):
        rep.stages[k].note("本次只跑待批提醒")
    with rep.stage("ceo") as hb:
        res = notify.notify_pending(pid, day, hb=hb,
                                    force="--force" in argv,
                                    dry_run="--dry-run" in argv)
    print("\n".join(notify.describe(res)))
    print()
    print(rep.render())
    # **本入口不推心跳。** 它只负责那一条"有事要你批"的消息；
    # 每天一份的心跳由 research_run.py 推。两个入口都推的话，
    # 同一天会收到两份长得一样的报告，人很快就会两份都不看。
    return 0 if (res["sent"] or not res["pending"]
                 or res["outcome"] == notify.DRYRUN) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
