#!/usr/bin/env python3
"""证券二部 Systematic Analytics —— 自检（合成数据，确定性，不联网）。

每个用例都验证一个【已知答案】，不是"跑通就算过"。
重点覆盖三类历史上真正咬过人的地方：
  1. 日期对齐 vs 位置对齐（错位的 Beta 看起来完全正常）
  2. 百分位的分母与最小样本回退（7 只样本里的 90th 会被当成真的极端值）
  3. PIT：filed <= as_of，绝不用 period end 判可见性

用法：  python scripts/test_analytics.py
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _no_network                                            # noqa: E402,F401
"""测试期间禁止联网 —— 靠真实行情才通过的断言，换台机器就是另一个结果。"""

import numpy as np                      # noqa: E402
import pandas as pd                     # noqa: E402

from cio import analytics, fundamentals   # noqa: E402
from cio.models import AnalyticsRow       # noqa: E402

FAIL = []


def fmt(v, d=4):
    return "None" if v is None else f"{v:.{d}f}"


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))
    if not cond:
        FAIL.append(name)


def approx(a, b, tol):
    return a is not None and abs(a - b) <= tol


def _series(rets, start=100.0, dates=None):
    px = start * np.exp(np.cumsum(np.asarray(rets, float)))
    px = np.concatenate([[start], px])
    if dates is None:
        dates = pd.bdate_range(end="2026-08-21", periods=len(px))
    return pd.DataFrame({"date": dates, "close": px})


print("\n=== 1. 波动率 / 下行波动 / 回撤（已知答案）===")
rng = np.random.default_rng(7)
# 日波动 1% → 年化 ≈ 1% * sqrt(252) ≈ 15.87%
r = rng.normal(0, 0.01, 4000)
df = _series(r, dates=pd.bdate_range(end="2026-08-21", periods=4001))
v = analytics.ann_vol(df["close"].values, 2000)
check("年化波动 ≈ 15.9%", approx(v, 15.87, 1.2), f"got {v:.2f}%")

# 对称分布：下行半标准差 ≈ 年化波动 / sqrt(2)
d = analytics.downside_vol(df["close"].values, 2000)
check("下行波动 ≈ 波动/√2", approx(d, v / (2 ** 0.5), 1.2), f"got {d:.2f}% vs {v/1.414:.2f}%")

# 构造一个精确 -20% 的回撤：涨到 200 再跌到 160
px = np.concatenate([np.linspace(100, 200, 60), np.linspace(200, 160, 40)])
dd = analytics.max_drawdown(px, 100)          # 100 根数据配 100 日窗口——口径必须自洽
check("最大回撤 = -20%", approx(dd, -20.0, 0.01), f"got {fmt(dd)}%")
check("100 根数据不得冒充 250 日回撤", analytics.max_drawdown(px, 250) is None)

# 现价相对均线：常数序列 → 0%
flat = np.full(200, 50.0)
check("平价序列 vs MA120 = 0%", approx(analytics.px_vs_ma(flat, 120), 0.0, 1e-9))

# 尾随 12-1：closes[-21]/closes[-250]-1
px2 = np.linspace(100, 300, 400)
tr = analytics.trailing_return(px2, 250, 21)
exp = (px2[-22] / px2[-251] - 1) * 100        # 起点是"250 日之前的那个收盘"，覆盖完整 250 个交易日
check("尾随 12-1 口径正确（起点 -(lookback+1)）", approx(tr, exp, 1e-6),
      f"got {fmt(tr)} want {exp:.4f}")
check("尾随区间恰好覆盖 250 个交易日",
      len(px2[-251:-21]) == 230 and abs(tr - exp) < 1e-9)

print("\n=== 2. Beta —— 日期对齐（历史上最阴的一类错误）===")
rb = rng.normal(0, 0.011, 900)
rs = 1.5 * rb + rng.normal(0, 0.004, 900)          # 真值 beta = 1.5
dates = pd.bdate_range(end="2026-08-21", periods=901)
bench = _series(rb, dates=dates)
stock = _series(rs, dates=dates)
b, c, n = analytics.beta_corr(stock, bench, 250, 60)
check("对齐序列 Beta ≈ 1.5", approx(b, 1.5, 0.12), f"got {fmt(b,3)}, n={n}")
check("相关性在 (0.8, 1.0)", c is not None and 0.8 < c < 1.0, f"got {fmt(c,3)}")

# 关键用例：个股在【最近 250 根之内】缺了 40 个交易日（停牌 / 数据源缺日）。
# 缺口必须落在测量窗口内，否则位置对齐碰巧也对——这正是这类 bug 难被发现的原因：
# 它只在特定标的、特定时间段发作，其余时候看起来完全正常。
keep = np.ones(len(stock), bool)
keep[np.arange(800, 840)] = False
stock_gap = stock[keep].reset_index(drop=True)
b2, _c2, n2 = analytics.beta_corr(stock_gap, bench, 250, 60)
check("缺日后按日期对齐 Beta 仍 ≈1.5", approx(b2, 1.5, 0.15), f"got {fmt(b2,3)}, aligned n={n2}")
# 对照：同一份数据按位置对齐会算成什么（仅演示，不是产品代码路径）
ps = np.diff(np.log(stock_gap["close"].values))[-250:]
pb = np.diff(np.log(bench["close"].values))[-250:]
bad = float(np.cov(ps, pb, ddof=1)[0, 1] / np.var(pb, ddof=1))
check("位置对齐确实会算错（对照）", abs(bad - 1.5) > 0.3, f"positional beta = {bad:.3f}")

print("\n=== 3. 两两相关性 ===")
r1 = rng.normal(0, 0.01, 400)
a1 = _series(r1)
a2 = _series(r1 * 1.0 + rng.normal(0, 0.0005, 400))   # 几乎同源
a3 = _series(rng.normal(0, 0.01, 400))                # 独立
check("近乎同源 corr > 0.9", (analytics.pair_corr(a1, a2, 60) or 0) > 0.9,
      fmt(analytics.pair_corr(a1, a2, 60), 3))
check("独立序列 |corr| < 0.4", abs(analytics.pair_corr(a1, a3, 60) or 1) < 0.4,
      fmt(analytics.pair_corr(a1, a3, 60), 3))

print("\n=== 4. 百分位：升序口径 + 行业最小样本回退 ===")
check("_rank_pct 最大值 → 高分位", analytics._rank_pct(10, [1, 2, 3, 4, 10]) == 90.0,
      str(analytics._rank_pct(10, [1, 2, 3, 4, 10])))
check("_rank_pct 最小值 → 低分位", analytics._rank_pct(1, [1, 2, 3, 4, 10]) == 10.0)
check("同分取中点", analytics._rank_pct(2, [1, 2, 2, 3]) == 50.0,
      str(analytics._rank_pct(2, [1, 2, 2, 3])))

cfg = {"percentile": {"min_sector_n": 15, "min_universe_n": 10}}
rows = []
for i in range(30):                       # Tech 30 只 → 够 15，走 sector
    rows.append(AnalyticsRow(code=f"T{i}", gics_sector="Tech", vol_60d=float(i)))
for i in range(5):                        # Energy 5 只 → 不够，回退 universe
    rows.append(AnalyticsRow(code=f"E{i}", gics_sector="Energy", vol_60d=float(100 + i)))
analytics.attach_percentiles(rows, cfg)
tech = rows[0].pctile["vol_60d"]
energy = rows[-1].pctile["vol_60d"]
check("大行业走 sector 口径", tech.basis == "sector" and tech.n == 30, f"{tech.basis} n={tech.n}")
check("小行业回退 universe 口径", energy.basis == "universe" and energy.n == 35,
      f"{energy.basis} n={energy.n}")
check("回退后仍是最高分位", energy.value > 95, f"{energy.value}")
no_sector = AnalyticsRow(code="X", gics_sector="", vol_60d=50.0)
analytics.attach_percentiles(rows + [no_sector], cfg)
check("无行业分类者走 universe", no_sector.pctile["vol_60d"].basis == "universe")

print("\n=== 5. SEC PIT：filed <= as_of，绝不用 period end ===")
# rows 格式：[filed, end, val, start]
fund = {
    "Assets": [["2025-02-10", "2024-12-31", 1000.0, ""],
               ["2026-02-10", "2025-12-31", 1200.0, ""]],
    "Liabilities": [["2025-02-10", "2024-12-31", 400.0, ""],
                    ["2026-02-10", "2025-12-31", 600.0, ""]],
    "Revenues": [["2025-02-10", "2024-12-31", 500.0, "2024-01-01"],
                 ["2026-02-10", "2025-12-31", 700.0, "2025-01-01"]],
    "GrossProfit": [["2026-02-10", "2025-12-31", 350.0, "2025-01-01"]],
    "OperatingIncomeLoss": [["2026-02-10", "2025-12-31", 140.0, "2025-01-01"]],
    "NetCashProvidedByUsedInOperatingActivities": [["2026-02-10", "2025-12-31", 200.0, "2025-01-01"]],
    "PaymentsToAcquirePropertyPlantAndEquipment": [["2026-02-10", "2025-12-31", 50.0, "2025-01-01"]],
    "AssetsCurrent": [["2026-02-10", "2025-12-31", 300.0, ""]],
    "LiabilitiesCurrent": [["2026-02-10", "2025-12-31", 150.0, ""]],
    "InterestExpense": [["2026-02-10", "2025-12-31", 20.0, "2025-01-01"]],
    "_schema": 2,
}
# as_of 在 2026 年报公布【之前】：只能看到 2024 年的数字
s_before = fundamentals.snapshot(fund, date(2026, 1, 15))
check("公布前只看得到旧一期（杠杆 40%）", approx(s_before["liab_assets"], 40.0, 1e-6),
      f"got {s_before['liab_assets']}")
check("公布前毛利率不可见（尚未申报）", s_before["gross_margin"] is None)
check("公布前 accepted date = 2025-02-10", s_before["filing_accepted_date"] == "2025-02-10",
      s_before["filing_accepted_date"])

# as_of 在公布【之后】：切换到新一期
s_after = fundamentals.snapshot(fund, date(2026, 3, 1))
check("公布后杠杆 = 50%", approx(s_after["liab_assets"], 50.0, 1e-6), f"got {s_after['liab_assets']}")
check("公布后毛利率 = 50%", approx(s_after["gross_margin"], 50.0, 1e-6), f"got {s_after['gross_margin']}")
check("营业利润率 = 20%", approx(s_after["op_margin"], 20.0, 1e-6))
check("FCF/Rev = (200-50)/700 ≈ 21.4%", approx(s_after["fcf_margin"], 150 / 700 * 100, 1e-6))
check("FCF/Assets = 150/1200 = 12.5%", approx(s_after["fcf_assets"], 12.5, 1e-6))
check("营收同比 = +40%", approx(s_after["rev_growth"], 40.0, 1e-6), f"got {s_after['rev_growth']}")
check("流动比率 = 2.0", approx(s_after["current_ratio"], 2.0, 1e-9))
check("利息保障 = 7.0x", approx(s_after["interest_cover"], 7.0, 1e-9))
check("accepted date = 2026-02-10", s_after["filing_accepted_date"] == "2026-02-10")

# stale：as_of 距最后一次申报 > 阈值
s_stale = fundamentals.snapshot(fund, date(2026, 8, 21), stale_days=100)
check("超期未申报 → stale", s_stale["filing_stale"] is True,
      f"age={s_stale['filing_age_days']}d")
check("stale 时数值仍给出（只是标记陈旧）", s_stale["liab_assets"] is not None)
s_fresh = fundamentals.snapshot(fund, date(2026, 3, 1), stale_days=100)
check("刚申报不算 stale", s_fresh["filing_stale"] is False, f"age={s_fresh['filing_age_days']}d")

# 重述：同一会计期后来又报了一版
fund_restated = dict(fund)
fund_restated["Assets"] = fund["Assets"] + [["2026-06-01", "2025-12-31", 1500.0, ""]]
check("重述公布前看到原值 1200",
      approx(fundamentals.pit(fund_restated, "Assets", date(2026, 3, 1))[0], 1200.0, 1e-9))
check("重述公布后看到新值 1500",
      approx(fundamentals.pit(fund_restated, "Assets", date(2026, 7, 1))[0], 1500.0, 1e-9))

print("\n=== 6. Exceptions：阈值来自配置，且每条都带红线原文 ===")
cfg2 = {"windows": {"corr_days": 60},
        "fundamentals": {"stale_days": 100},
        "exceptions": {"vol_pctile": 95, "downside_pctile": 95, "maxdd_pct": -30.0,
                       "beta": 1.80, "liab_assets_pctile": 90, "corr_pair": 0.85,
                       "trend_extended_pct": 25.0}}
rowset = [
    AnalyticsRow(code="HIVOL", gics_sector="Tech", vol_60d=90.0, max_dd_250d=-10.0),
    AnalyticsRow(code="DEEPDD", gics_sector="Tech", vol_60d=10.0, max_dd_250d=-45.0),
    AnalyticsRow(code="HIBETA", gics_sector="Tech", vol_60d=11.0, beta_250d=2.10, max_dd_250d=-5.0),
    AnalyticsRow(code="EXTEND", gics_sector="Tech", vol_60d=12.0, px_vs_ma120=31.0, max_dd_250d=-5.0),
    AnalyticsRow(code="STALE", gics_sector="Tech", vol_60d=13.0, filing_stale=True,
                 filing_age_days=200, filing_accepted_date="2026-01-01", max_dd_250d=-5.0),
    AnalyticsRow(code="MERGED", gics_sector="Tech", vol_60d=14.0,
                 identity_flag="corp-action:merger", max_dd_250d=-5.0),
]
for i in range(20):                       # 垫满 Tech 行业，让百分位走 sector 口径
    rowset.append(AnalyticsRow(code=f"F{i}", gics_sector="Tech", vol_60d=float(15 + i),
                               max_dd_250d=-5.0))
analytics.attach_percentiles(rowset, {"percentile": {"min_sector_n": 15, "min_universe_n": 10}})
ex = analytics.find_exceptions(rowset, {}, cfg2)
kinds = {e.code: e.kind for e in ex}
check("高波触发 volatility", kinds.get("HIVOL") == "volatility")
check("深回撤触发 drawdown", any(e.code == "DEEPDD" and e.kind == "drawdown" for e in ex))
check("高 Beta 触发 beta", any(e.code == "HIBETA" and e.kind == "beta" for e in ex))
check("偏离均线触发 extended", any(e.code == "EXTEND" and e.kind == "extended" for e in ex))
check("陈旧申报触发 stale", any(e.code == "STALE" and e.kind == "stale" for e in ex))
check("公司行为触发 corp_action", any(e.code == "MERGED" and e.kind == "corp_action" for e in ex))
check("每条异常都带阈值原文", all(e.threshold for e in ex))
check("正常标的不触发", not any(e.code.startswith("F1") for e in ex))
# 阈值确实来自配置：调宽后 HIBETA 不应再触发
cfg3 = {**cfg2, "exceptions": {**cfg2["exceptions"], "beta": 3.0}}
ex3 = analytics.find_exceptions(rowset, {}, cfg3)
check("放宽 Beta 阈值后不再触发", not any(e.kind == "beta" for e in ex3))

print("\n=== 7. 缺失即缺失，绝不用 0 冒充 ===")
short = np.array([100.0, 101.0, 99.0])
check("历史不足 → vol 返回 None", analytics.ann_vol(short, 60) is None)
check("历史不足 → 回撤返回 None", analytics.max_drawdown(short, 250) is None)
check("无基准 → beta 返回 None", analytics.beta_corr(stock, None, 250, 60)[0] is None)
r_empty = AnalyticsRow(code="Z")
analytics.attach_percentiles([r_empty], {"percentile": {"min_sector_n": 15, "min_universe_n": 10}})
check("全空行不产生任何百分位", r_empty.pctile == {})

print("\n=== 8. 渲染层：不得出现方向性措辞 ===")
from cio.models import AnalyticsReport            # noqa: E402
from cio.render_analytics import render_analytics_md   # noqa: E402
rep = AnalyticsReport(as_of_trade_date="2026-08-21", generated_at_utc="2026-08-21T12:00:00Z",
                      generated_at_market="2026-08-21 08:00 EDT", benchmark="S&P 500",
                      rows=rowset[:6], exceptions=ex, universe_count=26, displayed_count=6,
                      thresholds_version="test-v1", thresholds_shown=["vol percentile > 95"])
md = render_analytics_md(rep)
# 免责声明里【否定式】地提到这些词是允许的（"not a recommendation"），
# 断言式地出现才是问题。先剥掉否定语境，再扫描。
low = md.lower()
for neg in ["not a recommendation", "not recommendations", "no directional view",
            "not an investment recommendation", "should be bought, sold or resized",
            "no ranking of attractiveness"]:
    low = low.replace(neg, " ")
banned = ["buy ", "sell ", "overweight", "underweight", "bullish", "bearish",
          "we recommend", "recommended", "target price", "outperform", "top pick"]
hits = [w for w in banned if w in low]
check("报告不含任何方向性措辞", not hits, f"hits={hits}")
check("报告写明 ABSTAIN", "ABSTAIN" in md)
check("报告印出阈值原文", "vol percentile > 95" in md)
check("百分位带 basis 后缀", ("s |" in md or "u |" in md))

# 一行都没有基本面时，应给出原因而不是印一张全是 "—" 的表
rep_nofund = AnalyticsReport(
    as_of_trade_date="2026-08-21", generated_at_utc="2026-08-21T12:00:00Z",
    generated_at_market="2026-08-21 08:00 EDT", benchmark="S&P 500",
    rows=[AnalyticsRow(code="AAA", vol_60d=20.0), AnalyticsRow(code="BBB", vol_60d=25.0)],
    universe_count=2, displayed_count=2, thresholds_version="test-v1",
    fundamentals_note="CIO_SEC_UA is not set")
md2 = render_analytics_md(rep_nofund)
check("空基本面时不印整表破折号", "No fundamental data in this run" in md2)
check("空基本面时说明原因", "CIO_SEC_UA is not set" in md2)
# 有基本面时正常出表
check("有基本面时仍出表", "| Ticker | Filing accepted |" in md)

print("\n=== 9. 回归：审计发现的缺陷（每条都曾产出一个看似正常的错数）===")

# 9.1 别名 break —— 营收被冻结在 ASC 606 之前，毛利率算成 180%
facts = {"facts": {"us-gaap": {
    "Revenues": {"units": {"USD": [
        {"filed": "2017-02-01", "end": "2016-12-31", "start": "2016-01-01", "val": 90.0},
        {"filed": "2018-02-01", "end": "2017-12-31", "start": "2017-01-01", "val": 100.0}]}},
    "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
        {"filed": "2026-02-10", "end": "2025-12-31", "start": "2025-01-01", "val": 400.0}]}},
    "GrossProfit": {"units": {"USD": [
        {"filed": "2026-02-10", "end": "2025-12-31", "start": "2025-01-01", "val": 180.0}]}},
}}}
ex = fundamentals._extract(facts)
gm = fundamentals.snapshot(ex, date(2026, 6, 30))["gross_margin"]
check("ASC606 换标签后仍取到最新营收（毛利率 45% 而非 180%）", approx(gm, 45.0, 1e-6), f"got {gm}")

# 9.2 分子分母跨期 —— 2026 的资产 ÷ 2022 的收入
mixed = {"Assets": [["2026-08-01", "2026-06-30", 1000.0, ""]],
         "Revenues": [["2023-02-10", "2022-12-31", 500.0, "2022-01-01"]],
         "GrossProfit": [["2023-02-10", "2022-12-31", 250.0, "2022-01-01"]]}
sn = fundamentals.snapshot(mixed, date(2026, 8, 21))
check("同期毛利率仍算得出", approx(sn["gross_margin"], 50.0, 1e-6), f"got {sn['gross_margin']}")
mixed2 = {"Assets": [["2026-08-01", "2026-06-30", 1000.0, ""]],
          "NetCashProvidedByUsedInOperatingActivities": [["2023-02-10", "2022-12-31", 200.0, "2022-01-01"]],
          "PaymentsToAcquirePropertyPlantAndEquipment": [["2023-02-10", "2022-12-31", 50.0, "2022-01-01"]]}
check("跨期（资产2026 ÷ FCF2022）拒绝出数",
      fundamentals.snapshot(mixed2, date(2026, 8, 21))["fcf_assets"] is None)

# 9.3 资本开支缺标签不得当 0
nocx = {"Assets": [["2026-02-10", "2025-12-31", 100.0, ""]],
        "Revenues": [["2026-02-10", "2025-12-31", 50.0, "2025-01-01"]],
        "NetCashProvidedByUsedInOperatingActivities": [["2026-02-10", "2025-12-31", 20.0, "2025-01-01"]]}
sn3 = fundamentals.snapshot(nocx, date(2026, 3, 1))
check("资本开支缺失 → FCF 为空而非等于经营现金流", sn3["fcf_margin"] is None and sn3["fcf_assets"] is None,
      f"fcf_margin={sn3['fcf_margin']}")

# 9.4 封顶值不得当作测量结果
check("比率触顶 → None（不返回捏造的 1000%）",
      fundamentals._pct(5e9, 1e6) is None, str(fundamentals._pct(5e9, 1e6)))

# 9.5 脏价格：NaN / 0 不得污染 Beta，也不得变成 0 分位
rb2 = rng.normal(0, 0.011, 500)
rs2 = 1.3 * rb2 + rng.normal(0, 0.003, 500)
d2 = pd.bdate_range(end="2026-08-21", periods=501)
bench2, stock2 = _series(rb2, dates=d2), _series(rs2, dates=d2)
dirty = stock2.copy(); dirty.loc[300, "close"] = np.nan; dirty.loc[310, "close"] = 0.0
bd, _bc, _bn = analytics.beta_corr(dirty, bench2, 250, 60)
check("含 NaN/0 价格时 Beta 仍 ≈1.3（不被 −27 的假收益带跑）", approx(bd, 1.3, 0.15), f"got {fmt(bd,3)}")
check("Beta 结果是有限数或 None，绝不是 NaN", bd is None or np.isfinite(bd))
check("_rank_pct 遇 NaN 返回 None 而非 0.0", analytics._rank_pct(float('nan'), [1, 2, 3]) is None)
rr = [AnalyticsRow(code=f"N{i}", gics_sector="T", vol_60d=float(i)) for i in range(12)]
rr.append(AnalyticsRow(code="BAD", gics_sector="T", vol_60d=float('nan')))
analytics.attach_percentiles(rr, {"percentile": {"min_sector_n": 15, "min_universe_n": 10}})
check("NaN 行不进分布、不撑大分母", rr[-1].pctile == {} and rr[-2].pctile["vol_60d"].n == 12,
      f"n={rr[-2].pctile['vol_60d'].n}")

# 9.6 窗口名必须名副其实
short_hist = np.cumprod(1 + rng.normal(0, 0.02, 62)) * 100
check("62 根历史不得产出 max_dd_250d", analytics.max_drawdown(short_hist, 250) is None)
check("62 根历史不得产出 vol_60d 之外的长窗口指标", analytics.px_vs_ma(short_hist, 120) is None)
check("62 根历史仍可产出 vol_60d", analytics.ann_vol(short_hist, 60) is not None)

# 9.7 全域样本过小不得给百分位
tiny = [AnalyticsRow(code="ONLY", gics_sector="", vol_60d=42.0)]
analytics.attach_percentiles(tiny, {"percentile": {"min_sector_n": 15, "min_universe_n": 10}})
check("n=1 不给百分位", tiny[0].pctile == {})

# 9.8 相关性异常的饱和度按【标的数】而非【对数】
from cio.render_analytics import _saturation_note      # noqa: E402
from cio.models import AnalyticsException as _AE       # noqa: E402
pairs = [_AE(code=f"S{i}/S{j}", kind="correlation") for i in range(12) for j in range(i + 1, 12)]
# 66 对只涉及 12 只标的。修复前分子用的是"对数"(66)，会印出 "66 of 35 displayed names"
# 这种分子分母不同量纲的句子。分子必须是【标的数】。
note = _saturation_note(pairs, 35)
check("广度按标的数计（12 of 35），不是配对数 66", "12 of 35" in note and " 66 " not in note,
      note[:45])
check("远低于阈值时不触发", _saturation_note(pairs, 200) == "")

# 大面积越线时收敛为 breadth summary：最极端 5 条展开，其余只点名（不丢信息，不刷屏）
from cio.render_analytics import _collapse                # noqa: E402
many = [_AE(code=f"D{i}", kind="drawdown", message=f"D{i} 1Y max drawdown -{30+i}%",
            extremity=float(30 + i)) for i in range(17)]
sm, shown, rest = _collapse(many, 35)
check("17/35 触发收敛", bool(sm) and "17 of 35" in sm, sm[:30])
check("只展开最极端的 5 条", len(shown) == 5, str(len(shown)))
check("最极端的确实排在前面", shown[0].code == "D16" and shown[-1].code == "D12",
      f"{shown[0].code}..{shown[-1].code}")
check("其余 12 只仍被点名（不丢信息）", len(rest) == 12, str(len(rest)))
few = [_AE(code=f"E{i}", kind="beta", message="x", extremity=float(i)) for i in range(3)]
check("少量越线不收敛，逐条展开", _collapse(few, 35)[0] == "" and len(_collapse(few, 35)[1]) == 3)

# 9.9 台账：只增不改 + 失败即弃权
from cio import ledger as _L                            # noqa: E402
check("load() 深拷贝，不污染 DEFAULT_LEDGER",
      _L.load()["studies"] is not _L.DEFAULT_LEDGER["studies"])
check("生产集与打分因子集不一致 → 不得恢复投票",
      _L.alpha_vote_allowed(["动量", "反转", "低波", "趋势", "量能"])[0] is False)

print("\n=== 10. build56：第一次真实数据跑出来暴露的问题 ===")

# 10.1 流量÷存量的期末天然错开：年度 FCF 配最近一季资产负债表，可差近一年。
#      旧的 200 天统一容差把 FCF/资产 这一列在半数公司上系统性抹掉了。
fs = {"Assets": [["2026-08-20", "2026-07-31", 1000.0, ""]],                       # 最近一季
      "Revenues": [["2025-12-10", "2025-10-31", 500.0, "2024-11-01"]],            # FY2025 年度
      "NetCashProvidedByUsedInOperatingActivities": [["2025-12-10", "2025-10-31", 200.0, "2024-11-01"]],
      "PaymentsToAcquirePropertyPlantAndEquipment": [["2025-12-10", "2025-10-31", 50.0, "2024-11-01"]]}
sf = fundamentals.snapshot(fs, date(2026, 8, 24))
check("FCF/营收（同期）算得出", approx(sf["fcf_margin"], 30.0, 1e-6), f"got {sf['fcf_margin']}")
check("FCF/资产（年度流量÷最近一季资产）不再被误杀", approx(sf["fcf_assets"], 15.0, 1e-6),
      f"got {sf['fcf_assets']}")
# 但资产负债表明显【早于】流量期末仍应拒绝（旧资产配新流量是错的）
fs_old = dict(fs); fs_old["Assets"] = [["2024-01-10", "2023-12-31", 1000.0, ""]]
check("资产早于流量期末 → 仍然拒绝",
      fundamentals.snapshot(fs_old, date(2026, 8, 24))["fcf_assets"] is None)
# 同期口径反而要更严：差一个季度就不该配
fs_q = {"Revenues": [["2026-02-10", "2025-12-31", 500.0, "2025-01-01"]],
        "GrossProfit": [["2025-02-10", "2024-12-31", 250.0, "2024-01-01"]]}
check("毛利与营收差一整年 → 拒绝出毛利率",
      fundamentals.snapshot(fs_q, date(2026, 8, 24))["gross_margin"] is None)

# 10.2 Liabilities / GrossProfit 是可选标签，必须由恒等式反推
noliab = {"Assets": [["2026-02-10", "2025-12-31", 1000.0, ""]],
          "StockholdersEquity": [["2026-02-10", "2025-12-31", 400.0, ""]],
          "Revenues": [["2026-02-10", "2025-12-31", 800.0, "2025-01-01"]],
          "CostOfRevenue": [["2026-02-10", "2025-12-31", 300.0, "2025-01-01"]]}
sn = fundamentals.snapshot(noliab, date(2026, 3, 1))
check("未标 Liabilities → 由 资产−权益 反推出 60%", approx(sn["liab_assets"], 60.0, 1e-6),
      f"got {sn['liab_assets']}")
check("未标 GrossProfit → 由 营收−营业成本 反推出 62.5%", approx(sn["gross_margin"], 62.5, 1e-6),
      f"got {sn['gross_margin']}")
check("反推的是【字段名】不是公式串（星号才能打到格子上）",
      sorted(sn["derived_fields"]) == ["gross_margin", "liab_assets"], str(sn["derived_fields"]))
check("直接披露时不走反推", fundamentals.snapshot(fund, date(2026, 3, 1))["derived_fields"] == [])

# 10.3 单日主导波动（MRNA 229% 那一类）
base = np.full(80, 100.0)
base[40:] = 200.0                                  # 第 40 天一次 +100% 跳空
mv, share, idx = analytics.vol_concentration(base, 60)
check("识别出最大单日涨跌 ≈ +100%", approx(mv, 100.0, 0.5), f"got {fmt(mv,1)}%")
check("该日占窗口平方和 ≈ 100%（其余日无波动）", share is not None and share > 0.99,
      fmt(share, 3))
calm = 100 * np.cumprod(1 + rng.normal(0, 0.01, 200))
_m2, share2, _i2 = analytics.vol_concentration(calm, 60)
check("正常序列不会被判为单日主导", share2 is not None and share2 < 0.3, fmt(share2, 3))
rows_pa = [AnalyticsRow(code="GAP", gics_sector="T", vol_60d=229.0,
                        max_1d_move=100.0, max_1d_share=0.95, max_1d_date="2026-07-15")]
ex_pa = analytics.find_exceptions(rows_pa, {}, {"exceptions": {"vol_single_day_share": 0.5},
                                                "windows": {"vol_days": 60}})
check("触发 price_anomaly 异常", any(e.kind == "price_anomaly" for e in ex_pa))
check("异常里写明是哪一天", any("2026-07-15" in e.message for e in ex_pa))

# 10.4 外国发行人（20-F / IFRS）：不是取数失败，是覆盖范围之外
check("空的 us-gaap 记录被识别为外国发行人", fundamentals.has_us_gaap({"_schema": 3}) is False)
check("有 us-gaap 事实的正常识别", fundamentals.has_us_gaap(fund) is True)

# 10.5 缓存复用：长档位可以截尾满足短档位，反向绝不允许
from cio import quant_data as QD                    # noqa: E402
check("2y 档不得顶替 5y 请求（禁止静默降级）",
      QD._PERIOD_RANK["2y"] < QD._PERIOD_RANK["5y"])
check("_yf_period 档位映射正确",
      QD._yf_period(350) == "2y" and QD._yf_period(400) == "5y"
      and QD._yf_period(1250) == "10y" and QD._yf_period(2500) == "10y",
      f"{QD._yf_period(400)} / {QD._yf_period(1250)}")
# 关键：二部日报（400 日 → 5y 档）应当能复用准入闸/旧版留下的 10y 缓存，
# 而不是每天重新下载全池 507 只。这正是这次真实运行里白等 80 秒的原因。
check("400 日请求可回退到更长的 10y 缓存",
      QD._PERIOD_RANK[QD._yf_period(1250)] > QD._PERIOD_RANK[QD._yf_period(400)])

print("\n=== 11. build57：日期语义（业务凭证一律跟交易日/市场时区）===")
from cio.config import market_date, market_now                  # noqa: E402
from cio.utils import now_beijing                               # noqa: E402
_mkt, _bj = market_date(), now_beijing().strftime("%Y-%m-%d")
print(f"       市场时区日期={_mkt}  北京日期={_bj}  （两者可能差一天，这正是问题所在）")
check("market_date 取的是市场时区，不是机器/北京时区",
      market_now().tzinfo is not None and len(_mkt) == 10, _mkt)
# 快照命名必须用市场日期
import inspect                                                  # noqa: E402
from cio import quant_data as _QD                               # noqa: E402
_src = inspect.getsource(_QD._us_snapshot_save)
check("universe 快照用 market_date() 命名", "market_date()" in _src and "now_beijing()" not in _src)
# run_id 必须以 as_of 交易日为前缀
_asrc = inspect.getsource(analytics.build_analytics)
check("run_id 以 as_of 交易日为前缀，不用 file_stamp()",
      "_rid_day = (as_of or market_date())" in _asrc and 'f"an-{MARKET}-{file_stamp()}"' not in _asrc)
# 三处日期必须同源：报告正文 as_of、run_id、归档文件名
from cio.models import AnalyticsReport as _AR                   # noqa: E402
_rep = _AR(as_of_trade_date="2026-08-24", generated_at_utc="2026-08-24T22:28:50Z",
           generated_at_market="2026-08-24 18:28 EDT", market="us",
           run_id="an-us-20260824-1828")
check("run_id 的日期段 = as_of 交易日",
      _rep.run_id.split("-")[2] == _rep.as_of_trade_date.replace("-", ""),
      _rep.run_id)
check("run_id 不含生成时刻所在时区的次日日期", "20260825" not in _rep.run_id)

print("\n=== 12. build58：第二份真实报告暴露的问题 ===")

# 12.1 恒等式必须用【含少数股东权益的总权益】。
#      用母公司口径反推，会把 NCI 算进负债，杠杆被系统性高估。
facts_nci = {"facts": {"us-gaap": {
    "Assets": {"units": {"USD": [{"filed": "2026-02-10", "end": "2025-12-31", "val": 1000.0}]}},
    "StockholdersEquity": {"units": {"USD": [
        {"filed": "2026-02-10", "end": "2025-12-31", "val": 200.0}]}},          # 母公司口径
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest": {"units": {"USD": [
        {"filed": "2026-02-10", "end": "2025-12-31", "val": 300.0}]}},          # 含 NCI（正确项）
}}}
ex_nci = fundamentals._extract(facts_nci)
sn_nci = fundamentals.snapshot(ex_nci, date(2026, 3, 1))
check("反推负债用含 NCI 的总权益（70% 而非 80%）", approx(sn_nci["liab_assets"], 70.0, 1e-6),
      f"got {sn_nci['liab_assets']}")
# 只有母公司口径时回退，仍要出数
facts_only = {"facts": {"us-gaap": {
    "Assets": {"units": {"USD": [{"filed": "2026-02-10", "end": "2025-12-31", "val": 1000.0}]}},
    "StockholdersEquity": {"units": {"USD": [
        {"filed": "2026-02-10", "end": "2025-12-31", "val": 200.0}]}}}}}
check("只有母公司口径时回退使用", approx(
    fundamentals.snapshot(fundamentals._extract(facts_only), date(2026, 3, 1))["liab_assets"], 80.0, 1e-6))

# 12.2 杠杆 > 100% 是负权益，真实存在，不能当异常值截掉
neg_eq = {"Assets": [["2026-02-10", "2025-12-31", 1000.0, ""]],
          "StockholdersEquity": [["2026-02-10", "2025-12-31", -40.0, ""]]}
check("负权益 → 杠杆 104%，照实报出", approx(
    fundamentals.snapshot(neg_eq, date(2026, 3, 1))["liab_assets"], 104.0, 1e-6),
    f"got {fundamentals.snapshot(neg_eq, date(2026,3,1))['liab_assets']}")

# 12.3 非成分标的必须能拿到 CIK，否则整行空白且原因不明
import inspect as _insp                                     # noqa: E402
_lu = _insp.getsource(fundamentals.load_universe)
check("缺 CIK 时用 SEC 官方 ticker→CIK 清单补齐", "ticker_cik_map()" in _lu)
check("外国发行人与取数失败分开计数",
      "foreign(20-F/IFRS)" in _lu and "unavailable=" in _lu)
_ba = _insp.getsource(analytics.build_analytics)
check("非成分标的进入基本面取数流程", "all_stocks" in _ba and "load_universe_cached" in _ba)

print("\n=== 13. build59：会消失的脚注 + 按自身节奏判陈旧 ===")

# 13.1 脚注标记必须在 PDF 字体里真的存在。
#      实测：¹²³⁴ 能渲染，⁵(U+2075) 渲染成空白——标记凭空消失，图例那行以空格开头。
from cio.render_analytics import _MARKS                     # noqa: E402
check("脚注标记不含上标字符（改用方括号数字）",
      all(not any(ord(ch) > 127 for ch in m[1]) for m in _MARKS),
      str([m[1] for m in _MARKS]))
check("五个标记各不相同", len({m[1] for m in _MARKS}) == 5)

# 13.2 陈旧判定按【公司自己的申报节奏】校准。
#      年报制的外国发行人（ASML）间隔约 365 天，固定 100 天线会把"准时"误判成"陈旧"。
def _annual(years):
    rows = []
    for y in years:
        rows.append([f"{y}-02-25", f"{y-1}-12-31", 100.0, ""])
    return {"Assets": rows}


ann = _annual([2022, 2023, 2024, 2025, 2026])
cad = fundamentals.filing_cadence(ann, date(2026, 8, 24))
check("年报制申报节奏识别为 ~365 天", cad is not None and 300 <= cad <= 400, fmt(cad, 0))
sn_ann = fundamentals.snapshot(ann, date(2026, 8, 24), stale_days=100)
check("年报制公司 180 天不算陈旧（它就是这个节奏）", sn_ann["filing_stale"] is False,
      f"age={sn_ann['filing_age_days']}d thr={sn_ann['filing_stale_threshold_days']}d")
check("阈值确实被放宽到 1.5×节奏", sn_ann["filing_stale_threshold_days"] > 400,
      str(sn_ann["filing_stale_threshold_days"]))
# 但真的拖过一整轮仍要标
sn_late = fundamentals.snapshot(_annual([2021, 2022, 2023, 2024]), date(2026, 8, 24), stale_days=100)
check("年报制公司拖过 1.5 轮 → 仍标陈旧", sn_late["filing_stale"] is True,
      f"age={sn_late['filing_age_days']}d thr={sn_late['filing_stale_threshold_days']}d")

# 季报制不受影响：节奏 ~90 天，阈值仍在合理范围
def _quarterly(n):
    from datetime import timedelta as _td
    base = date(2026, 8, 1)
    rows = []
    for i in range(n):
        d0 = base - _td(days=90 * i)
        rows.append([d0.strftime("%Y-%m-%d"), d0.strftime("%Y-%m-%d"), 100.0, ""])
    return {"Assets": sorted(rows)}


q = _quarterly(8)
cq = fundamentals.filing_cadence(q, date(2026, 8, 24))
check("季报制节奏识别为 ~90 天", cq is not None and 80 <= cq <= 100, fmt(cq, 0))
sn_q = fundamentals.snapshot(q, date(2026, 8, 24), stale_days=100)
check("季报制公司刚申报不算陈旧", sn_q["filing_stale"] is False,
      f"age={sn_q['filing_age_days']}d thr={sn_q['filing_stale_threshold_days']}d")
check("季报制阈值仍然收紧（不超过 ~140 天）",
      sn_q["filing_stale_threshold_days"] <= 140, str(sn_q["filing_stale_threshold_days"]))
# 节奏样本不足时退回固定线
check("节奏样本不足 → 退回固定 stale_days",
      fundamentals.filing_cadence({"Assets": [["2026-02-10", "2025-12-31", 1.0, ""]]},
                                  date(2026, 8, 24)) is None)

print("\n=== 14. build60：指标名必须与公式一致 ===")
# 资产 1000 / 权益 200 → 总负债 800。这个 80% 是 total liabilities / assets，
# **不是 debt / assets**：总负债里还有应付账款、递延收入、租赁与养老金负债等非债务项。
# 报告过去把它印成 "debt / assets"——数值算得对，说的却不是同一件事。
_lv = {"Assets": [["2026-02-10", "2025-12-31", 1000.0, ""]],
       "StockholdersEquity": [["2026-02-10", "2025-12-31", 200.0, ""]]}
_sn = fundamentals.snapshot(_lv, date(2026, 3, 1))
check("快照字段叫 liab_assets（不再叫 leverage）",
      "liab_assets" in _sn and "leverage" not in _sn, str(sorted(_sn)[:4]))
check("总负债/总资产 = 80%", approx(_sn["liab_assets"], 80.0, 1e-6), fmt(_sn["liab_assets"], 1))

import inspect as _i2                                        # noqa: E402
_fe = _i2.getsource(analytics.find_exceptions)
check("异常文案不再写 debt/assets", "debt/assets" not in _fe)
check("异常文案写 total liabilities/assets", "total liabilities/assets" in _fe)

from cio.render_analytics import render_analytics_md as _rmd, _d as _star  # noqa: E402
from cio.models import AnalyticsReport as _AR2                             # noqa: E402
_row = AnalyticsRow(code="ABBV", liab_assets=104.0, gross_margin=70.0,
                    op_margin=25.0, filing_accepted_date="2026-08-03",
                    filing_age_days=21, derived_fields=["liab_assets"])
_md = _rmd(_AR2(as_of_trade_date="2026-08-24", rows=[_row], displayed_count=1,
                universe_count=1, bench_source="SPY"))
check("表头改为 Liab/Assets", "Liab/Assets" in _md and "| Leverage |" not in _md)
check("报告明确声明不是 debt/assets", "it is not debt/assets" in _md)
check("星号打在反推的那个格子上", "104%*" in _md, [l for l in _md.splitlines() if "ABBV" in l][:1])
check("未反推的格子不带星号", "70%*" not in _md)
check("ticker 上不再挂星号", "ABBV*" not in _md)
check("_d() 只认已登记的字段", _star(_row, "liab_assets") == "*" and _star(_row, "gross_margin") == "")

print("\n" + "=" * 60)
if FAIL:
    print(f"FAILED {len(FAIL)}: " + "; ".join(FAIL))
    raise SystemExit(1)
print("全部通过。")
