#!/usr/bin/env python3
"""全市场分布 —— **先看基础率，再谈信号。**

    python scripts/technical_distribution.py                  真实行情（S&P 500）
    python scripts/technical_distribution.py --limit 80       少取一些，跑得快
    CIO_QUANT_MOCK=1 python scripts/technical_distribution.py 合成行情，离线可跑

## 为什么这个脚本要先于任何提醒逻辑存在

"成交量连续加大、资金流入" 听起来是个稀有事件。但在 500 只票上，
**任何一天都有几十只票同时满足它**——如果不知道这个数是 3% 还是 30%，
就没法判断一条提醒值不值得看。

这个项目在证券二部已经吃过一次这个亏：先做打分和阈值，
再回头发现阈值挑的是历史噪音的形状。所以顺序倒过来——
**先把分布印出来，让基础率可见，再决定要不要有阈值、阈值定在哪。**

这个脚本**不打分、不排名、不产候选**。它只回答一句话：
今天全市场在这些度量上长什么样。
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cio import quant_data                                    # noqa: E402
from cio.technical import observer as ob                      # noqa: E402
from cio.technical import relative_strength as rsm            # noqa: E402

FIELDS = [
    ("volume", "rvol_20"),
    ("volume", "obv_slope_20"),
    ("volume", "cmf_20"),
    ("volume", "up_down_volume_ratio_20"),
    ("volume", "days_rvol_over_1_5_of_20"),
    ("volatility", "atr_pct_14"),
    ("volatility", "atr_percentile_252"),
    ("volatility", "range_pct_20_percentile_252"),
    ("price_structure", "position_in_range_252"),
    ("price_structure", "atr_to_nearest_zone_above"),
    ("relative_strength", "excess_mkt_63"),
    ("relative_strength", "rs_mkt_slope_20"),
]


CONDITIONS = [
    ("量比≥1.5", lambda c: (c.volume.get("rvol_20") or 0) >= 1.5),
    ("量比≥3", lambda c: (c.volume.get("rvol_20") or 0) >= 3.0),
    ("近20日≥5天放量", lambda c: (c.volume.get("days_rvol_over_1_5_of_20") or 0) >= 5),
    ("CMF>0.1 且 OBV↑", lambda c: (c.volume.get("cmf_20") or 0) > 0.1
     and (c.volume.get("obv_slope_20") or 0) > 0),
    ("一年区间≥95%", lambda c: (c.price_structure.get("position_in_range_252") or 0) >= 0.95),
    ("NR7", lambda c: c.volatility.get("is_nr7") is True),
    ("距上方价区≤0.5ATR",
     lambda c: (c.price_structure.get("atr_to_nearest_zone_above") or 99) <= 0.5),
]
"""要量基础率的离散条件。**每一条都是"某天某只票满足/不满足"，没有程度、没有分数。**"""


def _q(vals: list, p: float):
    if not vals:
        return None
    s = sorted(vals)
    i = min(int(p * (len(s) - 1) + 0.5), len(s) - 1)
    return s[i]


def _fmt(x):
    if x is None:
        return "  —  "
    if isinstance(x, bool):
        return str(x)
    return f"{x:.4g}"


def main() -> int:
    argv = sys.argv[1:]
    limit = 0
    n_days = 5
    if "--limit" in argv:
        limit = int(argv[argv.index("--limit") + 1])
    if "--days" in argv:
        n_days = max(1, int(argv[argv.index("--days") + 1]))

    stocks, src = quant_data.get_universe(limit=limit)
    print(f"universe：{len(stocks)} 只（{src}）")
    if limit:
        # **`--limit` 拿到的不是随机 120 只。** `get_universe` 先放关注池主题命中的，
        # 再按源给的顺序补齐——所以它偏向科技/医药，且后半段是字母序。
        # 用它算出来的基础率不能当正式数字外推到全市场。
        print(f"  ⚠ **用了 --limit {limit}：这不是随机抽样**（先关注池、再按源顺序补齐）。"
              f"\n     基础率会偏，**不要拿它外推到 500 只**。正式数字请不带 --limit 跑。")
    status: dict = {}
    panels = quant_data.get_history(stocks, days=400, status=status)
    bench = quant_data.get_benchmark(days=400, status=status)
    print(f"行情：{status.get('quant_history', '?')}；基准：{status.get('benchmark', '?')}")
    if bench is None:
        print("**没有基准 —— 相对强度那两栏会整列为空（不是 0）。**")

    # 板块 ETF 单独取一次。**不取的话 relative_strength 那半个模块整列是 null**——
    # 理由写得再清楚，一个永远空着的模块也等于没上线。
    want = sorted({rsm.SECTOR_ETF[s.gics_sector] for s in stocks
                   if s.gics_sector in rsm.SECTOR_ETF})
    etf_status: dict = {}
    etfs = quant_data.get_history(
        [quant_data.Stock(code=t, name=t, yahoo=t) for t in want],
        days=400, status=etf_status) if want else {}
    print(f"板块 ETF：{len(etfs)}/{len(want)} 取到（{etf_status.get('quant_history', '—')}）")

    # **一天的横截面不是基础率。**
    # 今天可能整体放量、可能刚好在财报季。一条提醒"每天响几次"这个问题，
    # 只有跨若干天才答得出来 —— 而且 `observe` 是纯函数、面板已在内存里，
    # 多取几个 as_of 只是多花 CPU，不再取一次数。
    ref = max((p for p in panels.values()), key=len, default=None)
    if ref is None:
        print("没有任何行情，退出")
        return 2
    all_dates = [str(d)[:10] for d in ref["date"].tolist()]
    dates = sorted({all_dates[-1 - i * 5] for i in range(n_days)
                    if len(all_dates) > 1 + i * 5})
    print(f"as_of 采样 {len(dates)} 天（每隔 5 个交易日）：{dates[0]} … {dates[-1]}\n")

    by_date: dict = {d: [] for d in dates}
    cards, failed = [], 0
    for s in stocks:
        df = panels.get(s.code)
        if df is None or len(df) < 30:
            failed += 1
            continue
        etf = rsm.SECTOR_ETF.get(s.gics_sector, "")
        for d in dates:
            try:
                card = ob.observe(df, as_of=d, bench=bench, sector_bench=etfs.get(etf),
                                  symbol=s.code, sector_symbol=etf)
            except Exception as e:                            # noqa: BLE001
                if d == dates[-1]:
                    failed += 1
                    print(f"  {s.code} 观察失败：{type(e).__name__}: {e}")
                continue
            by_date[d].append(card)
            if d == dates[-1]:
                cards.append(card)
    print(f"最新那天出卡 {len(cards)} 张，跳过 {failed} 只"
          f"（分位数表看最新那天，基础率跨 {len(dates)} 天）\n")

    print(f"{'字段':<38}{'有值':>6}{'p10':>10}{'p25':>10}{'p50':>10}"
          f"{'p75':>10}{'p90':>10}")
    print("-" * 94)
    for block, key in FIELDS:
        vals = [getattr(c, block).get(key) for c in cards]
        good = [v for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
        cover = f"{len(good)}/{len(cards)}"
        print(f"{block + '.' + key:<38}{cover:>6}"
              + "".join(f"{_fmt(_q(good, p)):>10}"
                        for p in (0.10, 0.25, 0.50, 0.75, 0.90)))

    # **空值的原因也要有分布。** 一整列空，得看得出来是"数据不够"还是"没有基准"。
    print("\n空值原因 Top 8（这一栏空着，是因为什么）")
    reasons = Counter()
    for c in cards:
        for k, v in c.reasons.items():
            reasons[f"{k} ← {v[:52]}"] += 1
    for r, n in reasons.most_common(8):
        print(f"  {n:>4}  {r}")

    # ---------------------------------------------------------- 基础率
    # **一条提醒有多常见，只有这里能回答。**
    print(f"\n离散事实的基础率（{len(dates)} 个交易日：{dates[0]} … {dates[-1]}）")
    print(f"{'条件':<30}{'均值':>8}{'最低':>8}{'最高':>8}   每天大约命中")
    print("-" * 94)
    per_day = {name: [] for name, _p in CONDITIONS}
    matrices = []
    for d in dates:
        cs = [c for c in by_date[d]]
        n = len(cs) or 1
        m = {name: {c.symbol for c in cs if pred(c)} for name, pred in CONDITIONS}
        matrices.append((d, n, m))
        for name, hits in m.items():
            per_day[name].append(len(hits) / n)
    universe_n = max((n for _d, n, _m in matrices), default=1)
    for name, _p in CONDITIONS:
        r = per_day[name]
        avg = sum(r) / len(r)
        print(f"{name:<30}{avg:>7.1%}{min(r):>8.1%}{max(r):>8.1%}"
              f"   {avg * 500:>5.1f} 只 / 500 只全市场")
    single = {name: sum(per_day[name]) / len(per_day[name]) for name, _p in CONDITIONS}

    # ---------------------------------------------------------- 组合
    # **单条都不稀有的时候，值钱的是交集。**
    # 22% 的票"资金流入"、25% 的票"贴着上方价区"——各自都没法做提醒；
    # 但两者同时成立可能只有百分之几，那才是一条看得过来的提醒。
    # **"倍数"这一列才是重点。**
    #
    # 两件事同时发生得很少，可能只是因为它们各自都很少 —— 把两个小数相乘而已，
    # 那不是发现了什么。真正说明"这两件事有关系"的，是它们同时出现的次数
    # **明显多于（或少于）各自概率相乘**。
    #
    #   倍数 ≈ 1    互不相干。合起来变稀有，纯粹是乘法的结果。
    #   倍数 > 1    真的倾向于一起出现 —— 这才值得往下查。
    #   倍数 < 1    互相排斥（有些是机械必然，见下）。
    #
    # 两条机械必然的负相关可以当**自检**用：量比≥1.5 与 NR7 应当明显 <1
    # （放量那天很难同时是最近七天里振幅最窄的一天）；一年区间≥95% 与
    # 距上方价区≤0.5ATR 也应当 <1（创了年内新高，上方常常根本没有价区）。
    # 这两条如果不成立，先怀疑代码，别急着解释市场。
    print("\n两两同时成立（均值，跨这几天）。**倍数 = 实测 ÷ 各自概率相乘**")
    print(f"  {'组合':<50}{'实测':>7}{'独立时':>8}{'倍数':>7}   每天约")
    print("-" * 94)
    pairs = []
    for i, (na, _pa) in enumerate(CONDITIONS):
        for nb, _pb in CONDITIONS[i + 1:]:
            rates = [len(m[na] & m[nb]) / n for _d, n, m in matrices]
            pairs.append((sum(rates) / len(rates), na, nb))
    for rate, na, nb in sorted(pairs, key=lambda x: -x[0]):
        if rate <= 0:
            continue
        exp = single[na] * single[nb]
        mult = f"{rate / exp:>6.2f}×" if exp > 0 else "     —"
        print(f"  {na + ' ＋ ' + nb:<50}{rate:>6.1%}{exp:>8.1%}{mult}"
              f"   {rate * 500:>5.1f} 只")
    zero = [(na, nb) for rate, na, nb in pairs if rate <= 0]
    if zero:
        print(f"  （另有 {len(zero)} 对在这几天里一次都没同时出现）")

    print("\nCEO 那三条放在一起（成交量持续加大 ＋ 资金流入 ＋ 到了那个高度）")
    trio = ["近20日≥5天放量", "CMF>0.1 且 OBV↑", "距上方价区≤0.5ATR"]
    tot_hit = tot_obs = 0
    for _d, n, m in matrices:
        hit = m[trio[0]] & m[trio[1]] & m[trio[2]]
        tot_hit += len(hit)
        tot_obs += n
        print(f"  {_d}   {len(hit)} / {n}   {sorted(hit)[:8]}")
    exp3 = single[trio[0]] * single[trio[1]] * single[trio[2]]
    print(f"\n  实测 {tot_hit} 次 / {tot_obs} 只日；三条独立时应有 {exp3 * tot_obs:.1f} 次"
          f"　→ 倍数 {(tot_hit / (exp3 * tot_obs)):.2f}×" if exp3 * tot_obs > 0 else "")
    if tot_hit < 20:
        print(f"  **{tot_hit} 次估不出任何东西。** 要判断这个组合值不值钱，"
              f"至少要几百次命中——把票数和天数都放大，不是调阈值。")

    print("\n" + "-" * 94)
    print("**这里没有任何一个数是信号。** 它们是基础率 —— 一条提醒每天会响几次。")
    print("单条命中率超过 10%，那条提醒在 500 只票上每天响 50 次以上，等于没有提醒。")
    print("要做提醒，先在这张表里找到一个**每天只响几次**的组合，再去验证它值不值钱。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
