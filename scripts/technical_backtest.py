#!/usr/bin/env python3
"""事件研究 —— **跑一次，结果好坏都照报。**

    .venv/bin/python scripts/technical_backtest.py                全市场，约一年
    .venv/bin/python scripts/technical_backtest.py --days 250      窗口长度
    .venv/bin/python scripts/technical_backtest.py --limit 120     少取一些（会警告）

## 跑之前先读这段

这**不是样本外检验**。三条限制写在 `backtest.py` 的开头，报告里也会再印一次：

    成分是今天的名单        幸存者偏差，方向向上
    阈值取自同一段行情       同一份数据的另一种切法
    样本按天聚集、窗口重叠   统计单位取"日"，仍然不是独立样本

**它的价值不在于证明 setup 有效**，而在于：如果连在这种对自己最有利的
条件下都看不出任何东西，那就不用再往下做了。这是一个**证伪**用的工具，
不是一个证实用的工具。

## 一次性

看完收益再回头调阈值，是这个项目吃过两次亏的做法。
setup 的定义已经冻结（`setup-1.0.1`，指纹绑版本号），
跑完之后**不许**因为结果不好看就去动它。要动，就得说明白动的理由
和收益无关，并且升版本号重来。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cio import quant_data                                    # noqa: E402
from cio.technical import backtest as bt                      # noqa: E402
from cio.technical import observer as ob                      # noqa: E402
from cio.technical import relative_strength as rsm            # noqa: E402


def main() -> int:
    argv = sys.argv[1:]
    days = int(argv[argv.index("--days") + 1]) if "--days" in argv else 250
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else 0
    every = int(argv[argv.index("--every") + 1]) if "--every" in argv else 1

    stocks, src = quant_data.get_universe(limit=limit)
    print(f"universe：{len(stocks)} 只（{src}）")
    if limit:
        print(f"  ⚠ **`--limit {limit}` 不是随机抽样** —— 先关注池、再按源顺序补齐。"
              f"结论不要外推到全市场。")
    status: dict = {}
    panels = quant_data.get_history(stocks, days=days + 300, status=status)
    bench = quant_data.get_benchmark(days=days + 300, status=status)
    print(f"行情：{status.get('quant_history', '?')}；基准：{status.get('benchmark', '?')}")
    if bench is None:
        print("**没有基准，超额收益全部算不出来。** 退出。")
        return 2
    want = sorted({rsm.SECTOR_ETF[s.gics_sector] for s in stocks
                   if s.gics_sector in rsm.SECTOR_ETF})
    etfs = quant_data.get_history(
        [quant_data.Stock(code=t, name=t, yahoo=t) for t in want],
        days=days + 300, status={}) if want else {}
    sectors = {s.code: s.gics_sector for s in stocks}

    ref = max(panels.values(), key=len, default=None)
    if ref is None:
        print("没有行情，退出")
        return 2
    all_dates = [str(d)[:10] for d in ref["date"].tolist()]
    replay = all_dates[-days:][::every]
    print(f"回放 {len(replay)} 个交易日：{replay[0]} … {replay[-1]}"
          + (f"（每 {every} 天取一个）" if every > 1 else ""))

    # **卡片按 as_of 逐日重算。** observe 是纯函数、面板已在内存里，
    # 这一步只花 CPU，不再取一次数——也正因为它是纯函数，
    # 回放出来的卡片和当天实际会算出来的完全一致。
    cards_by_day: dict = {}
    for n, d in enumerate(replay, 1):
        day = []
        for s in stocks:
            p = panels.get(s.code)
            if p is None or len(p) < 60:
                continue
            try:
                etf = rsm.SECTOR_ETF.get(s.gics_sector, "")
                day.append(ob.observe(p, as_of=d, bench=bench,
                                      sector_bench=etfs.get(etf), symbol=s.code,
                                      sector_symbol=etf))
            except Exception:                                 # noqa: BLE001
                continue
        cards_by_day[d] = day
        if n % 25 == 0 or n == len(replay):
            print(f"  回放 {n}/{len(replay)} 天…", flush=True)

    surv = bt.survivorship_note(panels, replay[0])
    report = bt.run(cards_by_day, panels, bench, sectors)
    print()
    print("\n".join(bt.summarize(report, surv)))

    if surv["n_late"]:
        print(f"\n窗口内才有历史的票（可测的那半边偏差）：{surv['n_late']} 只，最早几个：")
        for sym, first in surv["late_entrants"][:8]:
            print(f"  {sym:<8}历史起于 {first}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
