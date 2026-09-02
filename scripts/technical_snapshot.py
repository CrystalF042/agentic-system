#!/usr/bin/env python3
"""每日 Signal Card 快照 —— **只存，不推，不判。**

    .venv/bin/python scripts/technical_snapshot.py              存今天
    .venv/bin/python scripts/technical_snapshot.py --limit 120  少取一些
    .venv/bin/python scripts/technical_snapshot.py --events     看已积累的事件
    .venv/bin/python scripts/technical_snapshot.py --status     看积累了多少天、版本有没有混

这个脚本**不改关注池、不触发一部、不动闸门、不发消息、不打分、不产候选**。
它做的唯一一件事是：把今天全市场的 Signal Card 写到磁盘上，一天一个文件。

## 为什么要每天存，而不是回放历史

回放历史会踩两个坑，都已经在别处踩过：

**幸存者偏差。** `universe_pit` 现在是 False —— 快照只有 6 份、跨 12 天。
拿今天的成分名单回放过去一年，等于只研究"活到今天的那些票"。
对"贴近价区、量能改善"这类形态，被剔除和退市的名字恰恰是信息最多的那一批。

**用历史调出来的参数再拿历史验证。** 阈值是从基础率表里定的，
那张表本身来自这段历史。往前存则不同：**今天存下的卡片，
是明天才会被验证的样本。**

代价是慢。全市场约每天 1–2 只命中，要攒到几百个事件需要大半年。
所以近期的评价标准是**筛子**（每天推几条、值不值得看），
不是交易 setup 的收益 —— 后者要等样本。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cio import quant_data                                    # noqa: E402
from cio.technical import observer as ob                      # noqa: E402
from cio.technical import relative_strength as rsm            # noqa: E402
from cio import schedule as sched                             # noqa: E402
from cio.technical import review, setups, store               # noqa: E402


def _status() -> int:
    ds = store.dates()
    if not ds:
        print("还没有存过任何一天。跑一次 technical_snapshot.py 开始积累。")
        return 0
    print(f"已积累 {len(ds)} 天：{ds[0]} … {ds[-1]}")
    n = sum(len(store.load_day(d)) for d in ds)
    print(f"卡片 {n} 张（平均每天 {n / len(ds):.0f} 张）")
    print("\n版本分布（**混版本不是错，看不出来才是**）")
    for k, box in store.version_drift().items():
        for v, (lo, hi) in sorted(box.items()):
            mark = "" if len(box) == 1 else "  ← 这一栏有多个版本"
            print(f"  {k:<16}{v:<20}{lo} … {hi}{mark}")
    hits = _hits_on_disk()
    todo = review.pending(hits)
    print(f"\n命中 {len(hits)} 条，已复核 {len(hits) - len(todo)}，"
          f"待复核 {len(todo)}　（--review 看清单）")
    for ver, box in sorted(review.stats()["deduped"].items()):
        n = sum(box.values()) or 1
        print(f"  {ver}  值得率 {box['worth'] / n:.0%}"
              f"（值得 {box['worth']} / 不值得 {box['skip']} / 看不出来 {box['unclear']}）")
    evs = store.events()
    print(f"\n已推导出 {len(evs)} 个 {setups.SETUP_ID} 事件")
    print(f"按当前基础率（约每天 1–2 只 / 500 只），攒到 300 个事件约需 "
          f"{300 / max(len(evs) / len(ds), 0.01) / 250:.1f} 年"
          if evs else "\n还没有事件 —— 这是预期内的，命中很稀有。")
    return 0


def _events() -> int:
    evs = store.events()
    if not evs:
        print("还没有事件。")
        return 0
    print("\n".join(setups.describe()))
    print(f"\n{len(evs)} 个事件（起点是 t=0；持续与冷却期内重触发已并入）")
    print(f"{'标的':<8}{'起':<12}{'止':<12}{'天数':>4}   并入的重触发")
    for e in evs:
        print(f"{e.symbol:<8}{e.start:<12}{e.end:<12}{e.days:>4}   "
              f"{'、'.join(e.merged_repeats) or '—'}")
    return 0


def _hits_on_disk() -> list:
    out = []
    for d in store.dates():
        for row in store.load_day(d):
            if (row.get("setup") or {}).get("hit"):
                out.append((d, row.get("symbol", "")))
    return sorted(out)


def _review() -> int:
    hits = _hits_on_disk()
    todo = review.pending(hits)
    done = review.latest()
    print(f"命中共 {len(hits)} 条，已复核 {len(hits) - len(todo)}，待复核 {len(todo)}\n")
    if todo:
        print("待复核（**筛子的主 KPI 就靠这一栏**）")
        for d, sym in todo:
            print(f"  {d}  {sym}")
        print(f"\n记一条：scripts/technical_snapshot.py --mark {todo[0][1]} "
              f"worth|skip|unclear  一句话理由")
        print("  worth 的意思是「值得占用研究时间」，**不是「会涨」**——"
              "用涨跌回填这一栏会把两个 KPI 混成一个。")
    st = review.stats()["deduped"]
    if st:
        print("\n复核统计（按 setup 版本分开；换了阈值就重新计数）")
        for ver, box in sorted(st.items()):
            n = sum(box.values()) or 1
            print(f"  {ver}  值得 {box['worth']} / 不值得 {box['skip']} / "
                  f"看不出来 {box['unclear']}　→ 值得率 {box['worth'] / n:.0%}（n={n}）")
    rev = review.revisions()
    if rev:
        print(f"\n**有 {len(rev)} 条复核被改过**（改过就要看得见）")
        for (d, sym), a, b in rev[:6]:
            print(f"  {d} {sym}: {a} → {b}")
    return 0


def _mark(argv: list) -> int:
    i = argv.index("--mark")
    rest = [a for a in argv[i + 1:] if not a.startswith("--")]
    if len(rest) < 2:
        print("用法：--mark <标的> <worth|skip|unclear> [一句话理由]")
        return 2
    sym, verdict, note = rest[0], rest[1], " ".join(rest[2:])
    hits = [h for h in _hits_on_disk() if h[1].upper() == sym.upper()]
    if not hits:
        print(f"{sym} 不在任何一天的命中里 —— 只复核筛子推出来的东西")
        return 2
    d = hits[-1][0]
    try:
        row = review.mark(d, sym, verdict, note)
    except ValueError as e:
        print(e)
        return 2
    print(f"记下了：{row['as_of']} {row['symbol']} "
          f"{review.VERDICT_CN[row['verdict']]}"
          + (f"　「{row['note']}」" if row["note"] else ""))
    return 0


def _table() -> int:
    """**逐日表。从已存卡片推导，不另建一份存储。**

    两份存储迟早对不上：卡片说 3 条命中、汇总表说 2 条，谁对？
    只存卡片、每次现算，这个问题就不存在。
    """
    ds = store.dates()
    if not ds:
        print("还没有存过任何一天。")
        return 0
    evs = store.events()
    starts: dict = {}
    for e in evs:
        starts.setdefault(e.start, []).append(e.symbol)
    print(f"{'日期':<12}{'universe':>9}{'命中':>6}{'新事件':>7}{'持续中':>7}   新事件标的")
    print("-" * 78)
    dist: dict = {}
    for d in ds:
        rows = store.load_day(d)
        hit = [r.get("symbol") for r in rows if (r.get("setup") or {}).get("hit")]
        new = starts.get(d, [])
        cont = len(hit) - len(new)
        dist[len(new)] = dist.get(len(new), 0) + 1
        print(f"{d:<12}{len(rows):>9}{len(hit):>6}{len(new):>7}{cont:>7}   "
              f"{'、'.join(sorted(new)[:6]) or '—'}")
    n = len(ds)
    print(f"\n每日新事件数的分布（{n} 天）")
    for k in sorted(dist):
        print(f"  {k} 次：{dist[k]}/{n}　{dist[k] / n:.0%}")
    if evs:
        durs = [e.days for e in evs]
        print(f"\n事件持续天数：中位数 {sorted(durs)[len(durs) // 2]}，"
              f"最长 {max(durs)}，共 {len(evs)} 个")
        cut = [e for e in evs if e.ended_by_version_change]
        if cut:
            print(f"  其中 {len(cut)} 个是被**定义变更**截断的，不是被行情截断的")
        lins = {e.lineage for e in evs}
        if len(lins) > 1:
            print(f"  **事件横跨 {len(lins)} 套定义** —— 做事件研究必须按完整血统分组：")
            for lin in sorted(lins):
                k = sum(1 for e in evs if e.lineage == lin)
                print(f"    {k:>4} 个　{lin}")
    print(f"\n**{n} 天还太短。** 她要的那张表（每日命中分布、持续时长、行业分布）"
          f"要跑够 10–20 个交易日才有意义。")
    return 0


def main() -> int:
    argv = sys.argv[1:]
    if "--status" in argv:
        return _status()
    if "--table" in argv:
        return _table()
    if "--review" in argv:
        return _review()
    if "--mark" in argv:
        return _mark(argv)
    if "--events" in argv:
        return _events()
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else 0
    force = "--force" in argv

    # **收盘之后才存。** 盘中跑会把一根没走完的 K 线当成当天的收盘：
    # 量比、CMF、ATR 全算在半天数据上，而卡片上写的日期是今天。
    # 不报错、图上也看不出来 —— 要把两天的卡片摆一起才发现数对不上。
    if not force:
        ok, why = sched.is_snapshot_time()
        if not ok:
            print(f"跳过：{why}（要立刻存一份用 --force）")
            return 0
        print(f"收盘窗口内：{why}")

    stocks, src = quant_data.get_universe(limit=limit)
    meta = quant_data._LAST_UNIVERSE_META
    print(f"universe：{len(stocks)} 只（{src}）　"
          f"universe_pit={meta.get('universe_pit')} snapshot={meta.get('snapshot') or '—'}")
    lo, hi, nsnap = quant_data.snapshot_coverage()
    print(f"  成分快照覆盖 {lo or '—'} … {hi or '—'}（{nsnap} 份）"
          f"　→ 只有这段区间的成分是 point-in-time 的")
    if not meta.get("universe_pit"):
        print("  **回放历史仍会带幸存者偏差** —— 快照之前的日子只有「今天」这一版成分。"
              "所以往前存，不回放。")
    status: dict = {}
    panels = quant_data.get_history(stocks, days=400, status=status)
    bench = quant_data.get_benchmark(days=400, status=status)
    want = sorted({rsm.SECTOR_ETF[s.gics_sector] for s in stocks
                   if s.gics_sector in rsm.SECTOR_ETF})
    etfs = quant_data.get_history(
        [quant_data.Stock(code=t, name=t, yahoo=t) for t in want],
        days=400, status={}) if want else {}

    cards, as_of = [], ""
    for s in stocks:
        df = panels.get(s.code)
        if df is None or len(df) < 30:
            continue
        try:
            etf = rsm.SECTOR_ETF.get(s.gics_sector, "")
            c = ob.observe(df, bench=bench, sector_bench=etfs.get(etf),
                           symbol=s.code, sector_symbol=etf)
        except Exception as e:                                # noqa: BLE001
            print(f"  {s.code} 观察失败：{type(e).__name__}: {e}")
            continue
        cards.append(c)
        as_of = max(as_of, c.as_of_effective)
    if not cards:
        print("一张卡都没出，不写盘")
        return 2

    # **as_of 以卡片自己报的交易日为准**，不是机器今天的日期：
    # 周末或盘前跑，卡片说的是上一个交易日，文件名必须跟着它。
    n, note = store.write_day(as_of, cards, force=force)
    print(note)
    hits = [c for c in cards if setups.evaluate(c)["hit"]]
    print(f"\n{setups.SETUP_ID} 今日命中 {len(hits)} / {len(cards)}："
          f"{sorted(c.symbol for c in hits) or '无'}")
    print("\n" + "\n".join(setups.describe()))
    print("\n**这不是提醒，也不是建议。** v1 只把观察存下来；"
          "命中之后会怎样，要等样本攒够才能回答。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
