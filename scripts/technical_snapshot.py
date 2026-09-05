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
from cio import heartbeat as hb                                # noqa: E402
from cio import heartbeat                                    # noqa: E402
from cio.research import queue as rq, router as rt, trigger as tg  # noqa: E402
from cio.technical import review, score as sc, setups, store, sweep  # noqa: E402


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
        print("  **信号当天判完**：隔了交易日再判，后续走势已经看得见，"
              "那一条不进主 KPI。")
        print(f"  已经错过时机的，用 --mark <标的> excluded "
              f"{review.RETROSPECTIVE_CONTAMINATION} --on <日期>")
        print("  它既不算值得也不算不值得，**不进分母**。")
    _print_kpi()
    rev = review.revisions()
    if rev:
        print(f"\n**有 {len(rev)} 条复核被改过**（改过就要看得见）")
        for (d, sym), a, b in rev[:6]:
            print(f"  {d} {sym}: {a} → {b}")
    return 0


def _mark(argv: list) -> int:
    i = argv.index("--mark")
    # **先把 --on <日期> 这一对摘掉再取位置参数。**
    # 原来只过滤以 -- 开头的词，于是 `--on 2026-09-01` 里那个日期
    # 会掉进"理由"里 —— 不报错，只是理由末尾多一个日期，
    # 而真正想指定的那一天没生效。
    tail = argv[i + 1:]
    rest, k = [], 0
    while k < len(tail):
        a = tail[k]
        if a == "--on":
            k += 2
            continue
        if not a.startswith("--"):
            rest.append(a)
        k += 1
    if len(rest) < 2:
        print("用法：--mark <标的> <worth|skip|unclear|excluded> [一句话理由] "
              "[--on YYYY-MM-DD]")
        return 2
    sym, verdict, note = rest[0], rest[1], " ".join(rest[2:])
    hits = [h for h in _hits_on_disk() if h[1].upper() == sym.upper()]
    if not hits:
        print(f"{sym} 不在任何一天的命中里 —— 只复核筛子推出来的东西")
        return 2
    # **默认标最近那一天**，但可以显式指定：--mark A excluded 理由 --on 2026-09-01
    d = hits[-1][0]
    if "--on" in argv:
        want = argv[argv.index("--on") + 1][:10]
        if (want, sym.upper()) not in [(x, y.upper()) for x, y in hits]:
            print(f"{sym} 在 {want} 不是命中 —— 它命中的日子是："
                  + "、".join(x for x, _ in hits))
            return 2
        d = want
    try:
        row = review.mark(d, sym, verdict, note)
    except ValueError as e:
        print(e)
        return 2

    if row.get("action") == "unchanged":
        # **已经复核过、判定没变 → 不写第二行，而且说出来。**
        # 静默照写会让台账里躺着重复记录，靠统计阶段去重——那是隐藏行为。
        print(f"已经复核过，判定未变：{row['as_of']} {row['symbol']} "
              f"{review.VERDICT_CN[row['verdict']]} —— 没有写入新记录")
        return 0
    head = "改判" if row.get("action") == "revised" else "记下了"
    line = (f"{head}：{row['as_of']} {row['symbol']} "
            f"{review.VERDICT_CN[row['verdict']]}")
    if row.get("previous_verdict"):
        line += f"（原来是 {review.VERDICT_CN.get(row['previous_verdict'])}）"
    if row["note"]:
        line += f"　「{row['note']}」"
    print(line)
    lag = row.get("review_lag_trading_days")
    print(f"    复核时间 {row['reviewed_at']}（市场时区）　"
          f"延迟 {lag} 个交易日　→ {_BUCKET_CN[review.lag_bucket(row)]}")
    if review.lag_bucket(row) not in ("clean",) and row["verdict"] != "excluded":
        print("    **这一条不进主 KPI** —— 判的时候后续走势已经看得见了。")
    return 0


_BUCKET_CN = {"clean": "当天判的（进主 KPI）",
              "t1": "隔一个交易日（次要口径）",
              "retrospective": "事后补判（不进主 KPI）",
              "unknown": "没有复核时间戳（老记录，不进主 KPI）"}


def _print_kpi() -> None:
    """把值得率按"判的时候能不能看见后续走势"分开印。

    **主 KPI 只有 clean 那一档。** 别的照常展示，但绝不并进去——
    "63% 的票人工认为值得研究"这句话，别人一定会问"你什么时候判的"。
    """
    by_lag = review.stats().get("by_lag") or {}
    if not by_lag:
        print("\n还没有复核记录 —— 筛子的主 KPI 从这里开始攒。")
        return
    for ver, buckets in sorted(by_lag.items()):
        print(f"\n复核统计　{ver}　（**换了阈值就重新计数**）")
        for b in review.BUCKETS:
            box = buckets.get(b) or {}
            rate, n = review.worth_rate(box)
            if not sum(box.values()):
                continue
            head = f"  {_BUCKET_CN[b]:<26}"
            if n:
                print(f"{head}值得 {box['worth']} / 不值得 {box['skip']} / "
                      f"看不出来 {box['unclear']}　→ 值得率 {rate:.0%}（n={n}）")
            else:
                print(f"{head}—　（没有进分母的记录）")
            if box.get("excluded"):
                print(f"      另有 {box['excluded']} 条 excluded"
                      f"（**不进分母**）")
        clean = buckets.get("clean") or {}
        rate, n = review.worth_rate(clean)
        if n:
            print(f"  **主 KPI：{rate:.0%}（n={n}）** —— "
                  f"信号当天、在不知道后续走势的情况下判的")
        else:
            print("  **主 KPI：还没有样本** —— 当天判的那一档一条都没有")


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

    # **心跳先建，闸门后判。** 报告里声明了六个阶段，Build 1 只有第一节真的会跑；
    # 其余五节照样出现在报告里、标"未运行"——那不是噪音，
    # 那是"这条链还有五节没接上"这个事实本身。
    #
    # **窗口外退出也要留下一份报告。** 先前这道闸是直接 `return 0` 的，
    # 于是"因为不在窗口所以跳过"和"根本没跑"在磁盘上一模一样——
    # 而那正是盘前简报静默失踪三天时的形状。
    rep = heartbeat.Report(as_of=sched.market_now().strftime("%Y-%m-%d"))
    rc = 0
    with rep.stage("technical_snapshot") as hb:
        # **收盘之后才存。** 盘中跑会把一根没走完的 K 线当成当天的收盘：
        # 量比、CMF、ATR 全算在半天数据上，而卡片上写的日期是今天。
        ok, why = sched.is_snapshot_time()
        if not force and not ok:
            print(f"跳过：{why}（要立刻存一份用 --force）")
            hb.skip(f"不在收盘窗口：{why}")
        else:
            if force and not ok:
                hb.note(f"**--force 越过了收盘窗口**：{why}")
                print(f"**--force：{why}** —— 这一跑不在收盘窗口内")
            else:
                print(f"收盘窗口内：{why}")
            rc = _snapshot_body(argv, limit, force, hb)
    # Build 2：技术入口的 trigger → 路由 → 队列。
    # **两节接上了，剩下三节照样出现在报告里、标"未运行"。**
    if rc == 0 and _last_ranked:
        with rep.stage("research_router") as hb2:
            tasks = _route_technical(_last_ranked, hb2)
        with rep.stage("research_queue") as hb3:
            _fill_queue(tasks, hb3)
    else:
        rep.stages["research_router"].note("上游没有产出，无从路由")
        rep.stages["research_queue"].note("上游没有产出，无从入队")
    for key in ("unit_a", "cro_pc", "ceo"):
        # **不是"还没接上"了**（Build 3–5 已经接上）——是本入口不跑它们。
        # 留着旧那句话的后果：看这份报告的人会得出"流水线还有三节没做"，
        # 而真相是"那三节由 research_run.py 跑"。
        # **一个错的原因比没有原因更糟。**
        rep.stages[key].note("本入口只到入队为止；这一节由 "
                             "scripts/research_run.py 跑（同一天会有它自己那份心跳）")
    print()
    print(rep.render())
    path = rep.save()
    print(f"（心跳已存 {path.name}；每天都会有一份，**没有那一份就说明那天没跑**）")
    rep.push()
    miss = heartbeat.missing_days(back=10)
    if miss:
        print(f"**最近 10 天里这些工作日没有心跳报告**：{'、'.join(miss)}")
    return rc or rep.exit_code()


_last_ranked: list = []
"""上一次快照的分流结果。**只在进程内传递，不落第二份存储**——
两份存储迟早对不上，而这个项目已经为这件事付过学费。"""


def _route_technical(ranked: list, hb) -> list:
    """通过闸门的票 → TECHNICAL trigger → 路由。**0 条也要报。**"""
    from cio.technical import setups as st
    lineage = {"setup_version": st.SETUP_VERSION,
               "setup_fingerprint": st.params_fingerprint(),
               "algo_version": __import__(
                   "cio.technical.price_structure", fromlist=["x"]).ALGO_VERSION,
               "score_version": sc.SCORE_VERSION,
               "score_fingerprint": sc.params_fingerprint()}
    raw = []
    for r in ranked:
        if not r.passed_gate:
            continue
        # **事件起始日从卡片流里推导**，不是当天 —— 连续几天的同一形态
        # 必须是同一个 event_id，否则它会连着几天吃掉研究预算。
        start = _event_start(r.symbol, r.as_of)
        raw.append(tg.technical_trigger(
            r.symbol, r.as_of, start, lineage, score=r.score,
            reason_codes=[c for c in st.CONDITIONS],
            note=f"{r.band or '—'}　覆盖度 {r.families_used}/{r.families_possible} 族"))
    ages = rq.ages(today=ranked[0].as_of if ranked else "")
    tasks = rt.route(raw, ages)
    hb.count(raw_triggers=len(raw), unique_symbols=len(tasks),
             merged=len(raw) - len(tasks))
    both = [t for t in tasks if t.both_entrances]
    rechecks = [t for t in tasks if t.kind == rt.RECHECK]
    hb.count(both_entrances=len(both), rechecks=len(rechecks))
    print()
    print("\n".join(rt.describe(tasks, budget=5)))
    return tasks


def _event_start(symbol: str, as_of: str) -> str:
    """这只票当前这一段命中是从哪天开始的。取不到就退回当天。"""
    try:
        evs = [e for e in store.events([symbol]) if e.start <= as_of <= e.end]
        return evs[-1].start if evs else as_of
    except Exception:                                          # noqa: BLE001
        return as_of


def _fill_queue(tasks: list, hb) -> None:
    """入队。**幂等**：重跑一次不该让队列变长。"""
    acted = {"queued": 0, "exists": 0, "reprioritised": 0}
    for t in tasks:
        _it, act = rq.enqueue(t)
        acted[act] = acted.get(act, 0) + 1
    hb.count(**acted)
    box = rq.counts()
    hb.count(open_items=len(rq.open_items()),
             pending_approval=box.get(rq.PENDING_APPROVAL, 0))
    st = rq.stuck()
    if st:
        hb.note(f"**{len(st)} 条卡住**（同一状态超过 2 个交易日）："
                + "、".join(f"{i.symbol}/{i.state}" for i, _d in st[:5]))
    print()
    print("\n".join(rq.describe()))


def _snapshot_body(argv: list, limit: int, force: bool, hb) -> int:
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

    cards, as_of, failed_obs = [], "", []
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
            failed_obs.append(f"{s.code}:{type(e).__name__}")
            continue
        cards.append(c)
        as_of = max(as_of, c.as_of_effective)
    if not cards:
        # **一张卡都没出是失败，不是"今天没事"。**
        # 心跳上如果只写一个"完成"、计数空着，那和一次正常的安静日子
        # 长得一模一样 —— 而这次跑其实是全市场取数挂了。
        print("一张卡都没出，不写盘")
        hb.count(universe=len(stocks), cards=0, written=0)
        hb.note("**一张卡片都没出** —— 全市场取数失败，不是今天没事")
        raise RuntimeError(f"universe {len(stocks)} 只，卡片 0 张：取数全挂")

    # **as_of 以卡片自己报的交易日为准**，不是机器今天的日期：
    # 周末或盘前跑，卡片说的是上一个交易日，文件名必须跟着它。
    n, note = store.write_day(as_of, cards, force=force)
    print(note)
    hb.count(universe=len(stocks), cards=len(cards), written=n,
             observe_failed=len(failed_obs))
    if failed_obs:
        hb.note("观察失败：" + "、".join(failed_obs[:5])
                + (f" 等 {len(failed_obs)} 只" if len(failed_obs) > 5 else ""))
    if len(cards) < len(stocks):
        # **少扫了几只必须说出来**，否则"今天 502 只"和"今天本来 503 只、
        # 有一只取数失败"在报告上一模一样。
        hb.note(f"有 {len(stocks) - len(cards)} 只没出卡片（数据不足或观察失败）")
    if not meta.get("universe_pit"):
        hb.note("universe_pit=False —— 回放历史仍带幸存者偏差")
    # **全市场缺失扫描。** 一只票缺和全市场缺，不是同一件事——
    # 后者在每张卡片上看都只是一句"该字段是 null"，只有横着数才看得出来。
    print()
    sweep_lines = sweep.report(cards)
    print("\n".join(sweep_lines))
    asym = sweep.benchmark_asymmetry(cards)
    if asym:
        hb.note(f"**成对基准不对称**：{asym[0][0]} 缺 {asym[0][2]:.0%} 而 "
                f"{asym[0][1]} 只缺 {asym[0][3]:.0%} —— 一路基准坏了")
    div = sweep.asof_divergence(cards)
    if div:
        (m, sct), cnt = sorted(div.items(), key=lambda kv: -kv[1])[0]
        hb.note(f"两个基准截止日不同：大盘 {m} / 板块 {sct}（{cnt} 张卡片）")
    if status.get("benchmark_short"):
        print("  " + status["benchmark_short"])
    if status.get("benchmark_last_note"):
        print("  " + status["benchmark_last_note"])
    if status.get("benchmark_rows") is not None:
        span = status.get("benchmark_span") or ("—", "—")
        want = status.get("benchmark_want") or 0
        rows = status["benchmark_rows"]
        # **"要 400 行"会让 1255 看起来像出错。** _yf_period() 把 400 个交易日
        # 映射成 yfinance 的 5y 档（避免 {n}d 被当自然日、少给 30% 的 K 线），
        # 所以拿到更多是正常的。措辞要说"至少"。
        enough = "够" if rows >= want else "**不够**"
        print(f"  基准面板：{status.get('benchmark')}　{rows} 行"
              f"（至少要 {want} 行，{enough}）　{span[0]} … {span[1]}")
    print(sweep.closing_line())

    # **v2 分流：闸门决定有没有，排名决定先看谁。**
    ranked = sc.rank_day(cards)
    global _last_ranked
    _last_ranked = ranked
    passed = [r for r in ranked if r.passed_gate]
    scored = [r for r in passed if r.score is not None]
    # **0 也要记。** "今天 0 只通过闸门"是一个结论，不是空白。
    hb.count(gate_passed=len(passed), rankable=len(scored),
             unscorable=len(passed) - len(scored))
    todo = review.pending(_hits_on_disk())
    hb.count(review_pending=len(todo))
    print()
    print(sc.today_line(ranked))
    for r in ranked:
        if r.within_budget:
            print("\n".join(sc.describe(r)))
    print("\n" + "\n".join(setups.describe()))
    # **心跳：跑了就报，0 也报。**
    # 09-02～09-04 那三天的形状是"什么都没发生"和"根本没跑"长得一样。
    hit_n = len([r for r in ranked if r.passed_gate])
    scored_n = len([r for r in ranked if r.passed_gate and r.score is not None])
    failed = [f"{s.code} 观察失败" for s in stocks
              if panels.get(s.code) is not None and s.code not in
              {c.symbol for c in cards}]
    hb.record(as_of, hb.Stage(
        name="TECHNICAL SNAPSHOT",
        counts={"universe": len(stocks), "cards": len(cards),
                "gate hits": hit_n, "scored": scored_n,
                "failed": len(stocks) - len(cards)},
        failures=failed[:5],
        note=(status.get("benchmark_last_note") or ""),
        forced=force))
    print()
    print("\n".join(hb.render(as_of, sched.market_now().strftime("%H:%M %Z"))))
    try:
        hb.deliver(as_of, sched.market_now().strftime("%H:%M %Z"))
    except Exception as e:                                     # noqa: BLE001
        print(f"  心跳推送失败（{type(e).__name__}）—— 本地那份还在盘上")

    print("\n**这不是提醒，也不是建议。** v1 只把观察存下来；"
          "命中之后会怎样，要等样本攒够才能回答。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
