#!/usr/bin/env python3
"""自动流水线入口 —— **研究 → 风控 → 仓位 → 提案，然后停在你面前。**

    python scripts/research_run.py --dry-run     预演：今天会跑谁，**不花一分钱**
    python scripts/research_run.py               真跑
    python scripts/research_run.py --status      今天花了多少、队列什么状态
    python scripts/research_run.py --budget 3    临时改今天的预算

## 这一跑走完哪几节

    队列 → 一部（Build 3）→ CRO → PC → 提案（Build 4）→ **待你批准**

最后一步**不是本入口能跨过去的**：队列的状态机里 `APPROVED` 只能从
`PENDING_APPROVAL` 来，而这条链上的模块里连一个把它改成 `APPROVED`
的调用都没有（有探针钉着）。**那是治理边界，不是效率问题。**

## 为什么先给 `--dry-run`

Build 3 之后系统开始**自己花钱**（一部每跑一只要 6 次模型调用）。
`--dry-run` 用的是**和真跑同一份 plan**，所以"预演说会跑谁"和
"真跑跑了谁"不会不一致——预演如果和实跑走两条代码路径，那预演就没有意义。

## 关掉它

    CIO_RESEARCH_ENABLED=0 python scripts/research_run.py

关掉是一个正常状态，**但它会出现在心跳里**：
一个被关掉的流水线和一个坏掉的流水线，不许长得一样。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cio import heartbeat                                      # noqa: E402
from cio import notify as ntf                                  # noqa: E402
from cio import schedule as sched                              # noqa: E402
from cio.research import queue as q                            # noqa: E402
from cio.research import pipeline as pl                        # noqa: E402
from cio.research import scheduler as sc                       # noqa: E402


def _status(day: str) -> int:
    from cio import llm
    s = sc.spend(day)
    print(f"研究调度状态　{day}")
    print("  " + llm.describe_spec())
    print(f"  自动研究：{'开' if sc.enabled() else '**关**（CIO_RESEARCH_ENABLED=0）'}")
    print(f"  今日预算：{s.get('unit_a_calls', 0)}/{sc.MAX_UNIT_A_PER_DAY} 已用"
          f"（剩 {sc.remaining(day)}）")
    print(f"  今日花费：估算 ${sc.spent_usd(day):.4f} / 上限 "
          f"${sc.MAX_USD_PER_DAY:.2f}"
          + ("　**已达上限**" if sc.over_usd_budget(day) else "")
          + f"　（in {s.get('input_tokens', 0):,} / out {s.get('output_tokens', 0):,}）")
    if s.get("unpriced_calls"):
        print(f"  ⚠ {s['unpriced_calls']} 次用的模型不在价目表里 —— "
              f"上面那个金额**少算了**，不是免费")
    for r in (s.get("symbols") or []):
        cost = f"　${r['usd']:.4f}" if "usd" in r else "　（未回报用量）"
        print(f"    {r.get('symbol'):<6}{r.get('kind', ''):<12}"
              f"{r.get('at', '')}{cost}")
    print()
    print("\n".join(q.describe()))
    print()
    print("\n".join(sc.plan(day).describe()))
    return 0


def main(argv: list) -> int:
    day = sched.market_now().strftime("%Y-%m-%d")
    budget = (int(argv[argv.index("--budget") + 1])
              if "--budget" in argv else sc.MAX_UNIT_A_PER_DAY)
    if "--status" in argv:
        return _status(day)

    dry = "--dry-run" in argv
    # **心跳先建。** 这一跑就算什么都没做，也要留下一份报告——
    # "今天没有可研究的"和"今天调度根本没跑"必须分得开。
    rep = heartbeat.Report(as_of=day)
    rep.stages["technical_snapshot"].note("本次只跑研究调度，快照见当天的快照心跳")
    for k in ("research_router",):
        rep.stages[k].note("本次只跑研究调度")
    with rep.stage("research_queue") as hb0:
        box = q.counts()
        hb0.count(open_items=len(q.open_items()),
                  queued=box.get(q.QUEUED, 0), deferred=box.get(q.DEFERRED, 0))
    with rep.stage("unit_a") as hb:
        res = sc.run(day, budget=budget, dry_run=dry, hb=hb)
    # Build 4：研究完的往下推一层。**不看 res["done"]** ——
    # 昨天研究完、今天才轮到风控的条目，今天 done=0 但它们必须被处理。
    with rep.stage("cro_pc") as hb2:
        adv = pl.advance(day, hb=hb2, dry_run=dry)
    with rep.stage("ceo") as hb3:
        box = q.counts()
        hb3.count(queue_pending=box.get(q.PENDING_APPROVAL, 0),
                  approved=box.get(q.APPROVED, 0),
                  rejected=box.get(q.REJECTED, 0),
                  vetoed=box.get(q.VETOED, 0),
                  no_trade=box.get(q.NO_TRADE, 0))
        # Build 5：**推送在这里，不在另一个入口。**
        # 分开的话就会出现"流水线跑了、提醒没跑"，而两者长得一样。
        #
        # 预演走的是**同一个函数**，只是 `dry_run=True` 让它到发送那一步
        # 停手。在这里手写一个假的返回值就又是"用夹具验证实现"——
        # 预演如果走另一条代码路径，那预演本身就没有意义。
        nres = ntf.notify_pending(adv["portfolio_id"], day, hb=hb3,
                                  dry_run=dry)

    print("\n".join(sc.describe(res)))
    print()
    print("\n".join(pl.describe(adv)))
    print()
    print("\n".join(ntf.describe(nres)))
    print()
    print("\n".join(q.describe()))
    print()
    print(rep.render())
    rep.save()
    rep.push()
    return 0 if not (res["failed"] or adv["failed"]) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
