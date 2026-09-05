"""事件研究 —— **跑一次，结果好坏都照报。**

## 这份结果能声称什么，不能声称什么

**能：** 在今天这 502 只票的过去约一年里，冻结的 setup 触发之后，
5/10/20 日相对 SPY 的超额、MFE/MAE、胜率，以及**同日同板块同波动档
的对照组**是什么样。

**不能：** 声称这套 setup 在未来有效。三条硬约束限制了它：

**一、幸存者偏差。** 成分名单是**今天**的。窗口期内被剔除、被并购、
退市的名字不在里面。标普 500 一年约 20–25 只变动（约 4–5%），
而且被剔除的多数是跌下去的——所以这个偏差的方向是**向上**的。
对偏"强势"的 setup 影响小于对"抄底"型的，但不是零。
`late_entrants` 那个数是它可测的一部分：窗口内才被加进来的票，
它们的历史起点晚于窗口起点，这一段是看得见的。

**二、参数是从这段历史的横截面分布上定的。** 阈值取自基础率表
（A≥5 落在 90 分位外、B 取上四分位），而那张表来自同一段行情。
所以这不是样本外检验，是**同一份数据的另一种切法**。

**三、样本量小，而且不独立。** 事件按天聚集，同一天的票有行业相关性；
指标用 20 日重叠窗口。所以统计单位取**"日"而不是"事件"**——
每个有事件的日子算一个 (事件组均值 − 对照组均值)，然后看这些日差。
这样处理不能凭空造出独立性，但至少不假装每个事件都是一个独立样本。

## 对照组不能匹配 setup 自己的成分

匹配同日、同板块、相近波动档——**不匹配"距上方价区的距离"或
"近20日放量天数"**。匹配掉那两个，就等于把要检验的东西匹配掉了，
剩下的差异必然接近零，而那个零什么也不说明。

**同日匹配是四项里最重要的一项**：它按构造消掉了"那个月大盘在涨"
这个混淆——事件组和对照组经历的是同一天的市场。

## 这份结果不许回头改任何东西

看完收益再去调阈值，就是这个项目吃过两次亏的做法。
有一条探针钉住：`score.py` 和 `setups.py` 都不许 import 本模块。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from .numbers import finite
from .setups import SETUP_ID, SETUP_VERSION, derive_events, evaluate
from .volatility import atr

HORIZONS = (5, 10, 20)
N_CONTROLS = 5
"""每个事件配几只对照。多了会把匹配质量拉低（找不到那么多同板块同波动的），
少了噪音大。5 是常见取值，**它不影响方向，只影响方差**。"""


@dataclass
class EventOutcome:
    symbol: str = ""
    start: str = ""
    sector: str = ""
    atr_pct_bucket: Optional[int] = None
    excess: dict = field(default_factory=dict)
    """{horizon: 相对 SPY 的超额收益}。取不到的是 None，不是 0。"""
    mfe_atr: dict = field(default_factory=dict)
    """最大有利偏移，**以 ATR 为单位**（跨票可比；用百分比不可比）。"""
    mae_atr: dict = field(default_factory=dict)
    controls: list = field(default_factory=list)
    control_excess: dict = field(default_factory=dict)


def _idx_of(panel, date: str) -> Optional[int]:
    dates = [str(d)[:10] for d in panel["date"].tolist()]
    try:
        return dates.index(str(date)[:10])
    except ValueError:
        return None


def forward_excess(panel, bench, i: int, h: int) -> Optional[float]:
    """从下标 i 起 h 个交易日的收益，减去同期基准。

    **进场价用 i 的收盘，不是 i 的开盘。** 卡片是收盘后算出来的，
    收盘价是当天唯一一个卡片生成时已经知道的价格。用开盘价等于
    假装我们在信号出现之前就进场了。
    """
    close = [float(x) for x in panel["close"].tolist()]
    if i + h >= len(close) or close[i] <= 0:
        return None
    r = close[i + h] / close[i] - 1.0
    if bench is None:
        return None
    bd = [str(d)[:10] for d in bench["date"].tolist()]
    bc = [float(x) for x in bench["close"].tolist()]
    d0 = str(panel["date"].iloc[i])[:10]
    if d0 not in bd:
        return None
    j = bd.index(d0)
    if j + h >= len(bc) or bc[j] <= 0:
        return None
    return r - (bc[j + h] / bc[j] - 1.0)


def mfe_mae(panel, i: int, h: int) -> tuple:
    """最大有利/不利偏移，以进场日的 ATR20 为单位。

    只用 i 之前的数据算 ATR（**进场时就知道的那个值**），
    用整段的 ATR 会把未来的波动泄进分母。
    """
    a = atr(panel.iloc[:i + 1], 20)
    if not a or a <= 0:
        return None, None
    close = [float(x) for x in panel["close"].tolist()]
    high = [float(x) for x in panel["high"].tolist()]
    low = [float(x) for x in panel["low"].tolist()]
    if i + h >= len(close):
        return None, None
    entry = close[i]
    seg_hi = max(high[i + 1:i + h + 1])
    seg_lo = min(low[i + 1:i + h + 1])
    return round((seg_hi - entry) / a, 3), round((seg_lo - entry) / a, 3)


def _bucket(x: Optional[float], n: int = 5) -> Optional[int]:
    v = finite(x)
    return None if v is None else min(int(v * n), n - 1)


def build_events(cards_by_day: dict) -> list:
    """从逐日卡片推导事件（去重后的），返回 [(symbol, start_date)]。

    **一次事件不是一个 stock-day** —— 连着三天成立是一个事件。
    不去重的话，5/10/20 日收益窗口互相覆盖，样本量看着几百个，
    独立信息远没有那么多。
    """
    days = sorted(cards_by_day)
    series: dict = {}
    for d in days:
        for c in cards_by_day[d]:
            series.setdefault(c.symbol, []).append((d, bool(evaluate(c)["hit"])))
    out = []
    for sym, ser in series.items():
        for e in derive_events(sym, ser):
            out.append((sym, e.start))
    return sorted(out, key=lambda x: (x[1], x[0]))


def pick_controls(sym: str, day: str, cards_by_day: dict, sectors: dict,
                  k: int = N_CONTROLS) -> list:
    """同日、同板块、波动档最接近，**且当天没通过闸门**的对照。

    **刻意不匹配 setup 的成分**（距价区距离、放量天数）——匹配掉它们，
    剩下的差异必然接近零，而那个零什么都不说明。
    """
    same_day = cards_by_day.get(day) or []
    me = next((c for c in same_day if c.symbol == sym), None)
    if me is None:
        return []
    my_b = _bucket(me.volatility.get("atr_percentile_252"))
    my_sec = sectors.get(sym, "")
    pool = []
    for c in same_day:
        if c.symbol == sym or evaluate(c)["hit"]:
            continue                       # 通过闸门的不能当对照
        if my_sec and sectors.get(c.symbol, "") != my_sec:
            continue
        b = _bucket(c.volatility.get("atr_percentile_252"))
        if b is None or my_b is None:
            continue
        pool.append((abs(b - my_b), c.symbol))
    pool.sort()
    return [s for _d, s in pool[:k]]


def run(cards_by_day: dict, panels: dict, bench, sectors: dict,
        horizons=HORIZONS) -> dict:
    """跑一次事件研究。**返回原始明细 + 按日聚合，不给结论。**"""
    events = build_events(cards_by_day)
    out: list = []
    for sym, day in events:
        panel = panels.get(sym)
        if panel is None:
            continue
        i = _idx_of(panel, day)
        if i is None:
            continue
        eo = EventOutcome(symbol=sym, start=day, sector=sectors.get(sym, ""))
        me = next((c for c in (cards_by_day.get(day) or []) if c.symbol == sym), None)
        if me is not None:
            eo.atr_pct_bucket = _bucket(me.volatility.get("atr_percentile_252"))
        for h in horizons:
            eo.excess[h] = forward_excess(panel, bench, i, h)
            mfe, mae = mfe_mae(panel, i, h)
            eo.mfe_atr[h], eo.mae_atr[h] = mfe, mae
        eo.controls = pick_controls(sym, day, cards_by_day, sectors)
        for h in horizons:
            vals = []
            for cs in eo.controls:
                cp = panels.get(cs)
                if cp is None:
                    continue
                ci = _idx_of(cp, day)
                if ci is None:
                    continue
                v = forward_excess(cp, bench, ci, h)
                if v is not None:
                    vals.append(v)
            eo.control_excess[h] = (sum(vals) / len(vals)) if vals else None
        out.append(eo)

    # **统计单位是"日"，不是"事件"。** 同一天的票有行业相关性，
    # 指标用 20 日重叠窗口——把每个事件当独立样本是自欺。
    by_day: dict = {}
    for e in out:
        by_day.setdefault(e.start, []).append(e)
    day_diff: dict = {h: [] for h in horizons}
    for _d, evs in by_day.items():
        for h in horizons:
            a = [e.excess[h] for e in evs if e.excess.get(h) is not None]
            b = [e.control_excess[h] for e in evs if e.control_excess.get(h) is not None]
            if a and b:
                day_diff[h].append(sum(a) / len(a) - sum(b) / len(b))
    return {"events": out, "n_events": len(out), "n_event_days": len(by_day),
            "day_diff": day_diff, "setup_id": SETUP_ID,
            "setup_version": SETUP_VERSION, "horizons": list(horizons)}


def survivorship_note(panels: dict, window_start: str) -> dict:
    """**能测的那部分幸存者偏差。**

    窗口内才被加进指数的票，历史起点晚于窗口起点——这一段看得见，
    可以数。看不见的是**被剔除的那些**：它们今天不在名单里，
    所以连缺了几只都数不出来。

    所以这个函数报的是一个**下界**，不是偏差本身。报下界比不报好，
    比假装没有偏差好得多。
    """
    late = []
    for sym, p in panels.items():
        if p is None or not len(p):
            continue
        first = str(p["date"].iloc[0])[:10]
        if first > str(window_start)[:10]:
            late.append((sym, first))
    return {
        "universe": len(panels),
        "late_entrants": sorted(late, key=lambda x: x[1]),
        "n_late": len(late),
        "note": ("这是今天的成分名单。窗口内被剔除/退市的票完全不在其中，"
                 "**数不出来**。标普500 一年约 20–25 只变动，被剔除的多数是跌下去的，"
                 "所以残余偏差的方向是**向上**。上面 late_entrants 只是可测的一半。"),
    }


def summarize(report: dict, surv: dict) -> list:
    """报告文本。**先说这份结果不能声称什么。**"""
    h_list = report["horizons"]
    out = [
        f"{report['setup_id']}（{report['setup_version']}）事件研究",
        "",
        "**这份结果不是样本外检验。** 阈值取自同一段行情的横截面分布；",
        "成分是今天的名单（幸存者偏差方向向上）；样本按天聚集、指标窗口重叠。",
        f"universe {surv['universe']} 只，其中 {surv['n_late']} 只是窗口内才有历史的",
        f"（被剔除的票数不出来 —— {surv['note'][:40]}…）",
        "",
        f"事件 {report['n_events']} 个，分布在 {report['n_event_days']} 个交易日",
    ]
    if report["n_events"] < 30:
        out.append(f"  **{report['n_events']} 个事件估不出任何东西。** "
                   f"下面的数只是把它们如实列出来，不构成任何结论。")
    out.append("")
    out.append(f"{'视界':<6}{'事件均超额':>12}{'对照均超额':>12}"
               f"{'按日差均值':>12}{'按日差>0':>10}{'有效日':>8}")
    out.append("-" * 64)
    for h in h_list:
        evs = [e for e in report["events"] if e.excess.get(h) is not None]
        a = [e.excess[h] for e in evs]
        b = [e.control_excess[h] for e in evs if e.control_excess.get(h) is not None]
        dd = report["day_diff"][h]
        pos = sum(1 for x in dd if x > 0)
        out.append(
            f"{h:>3}日 " +
            (f"{sum(a) / len(a):>11.2%}" if a else f"{'—':>12}") +
            (f"{sum(b) / len(b):>12.2%}" if b else f"{'—':>12}") +
            (f"{sum(dd) / len(dd):>12.2%}" if dd else f"{'—':>12}") +
            (f"{f'{pos}/{len(dd)}':>10}" if dd else f"{'—':>10}") +
            f"{len(dd):>8}")
    out.append("")
    out.append(f"{'视界':<6}{'MFE 中位(ATR)':>16}{'MAE 中位(ATR)':>16}")
    out.append("-" * 40)
    for h in h_list:
        mfe = sorted(e.mfe_atr[h] for e in report["events"] if e.mfe_atr.get(h) is not None)
        mae = sorted(e.mae_atr[h] for e in report["events"] if e.mae_atr.get(h) is not None)
        out.append(f"{h:>3}日 "
                   + (f"{mfe[len(mfe) // 2]:>16.2f}" if mfe else f"{'—':>16}")
                   + (f"{mae[len(mae) // 2]:>16.2f}" if mae else f"{'—':>16}"))
    out.append("")
    out.append("**看完这份结果不许回头调阈值。** 那是这个项目吃过两次亏的做法，")
    out.append("而且一旦那么做，上面每一个数就都不再有意义。")
    return out
