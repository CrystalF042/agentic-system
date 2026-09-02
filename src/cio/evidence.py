"""证券一部 —— 固定量化证据面板（Fixed Quantitative Evidence Panel）。

**这个模块存在的唯一理由，是不让 LLM 自己挑因子。**

Vibe-Trading 的 Alpha Zoo 有 462 个 alpha，配套 IC/IR 排名工具。
诱惑是让多头去里面搜出 12 个利好、空头搜出 9 个利空——那几乎必然是 cherry-picking：
462 个因子里总能找到支持任何结论的那一撮，而且找出来的东西看起来完全像证据。
二部刚用三天证明了这条路的终点（18 个因子严格检验，0 个通过；
46 次检验 7 个名义显著，全是搜索噪声）。462 个只是同一个池子的更大样本。

所以这里反过来做：**面板预先定死**，每次分析同一套指标，多空双方拿到的是
【同一张表的全部内容】，只能解释它，不能挑选它。

    Deterministic evidence  →  LLM reasoning

分层（一部的信息权重，因子只是 Level 4）：
    Level 1  公司基本面 / 申报        最高
    Level 2  事件 / 催化剂 / 预期修正  很高
    Level 3  估值 / 市场定位          高
    Level 4  量化状态证据（本模块）    辅助
    Level 5  技术形态                 辅助

一部真正的价值在二部处理不了的因果信息——产品周期、竞争格局、管理层指引、
监管变化、盈利质量、资本开支周期、市场预期、已被 price in 的部分。
本面板是给那些论证提供【客观地基】，不是让一部再造一个量化选股模型。

铁律（与二部同源）：
  · 只调 measures.py 的共享计算，**不 import 二部的 analytics 模块**——
    一部不读二部的结论，连结构依赖都不建立。
  · 缺失就是缺失，如实留空并写明原因，绝不填 0 或中位数。
  · 每个数字带口径（窗口 / 分位分母 / 数据截止日）。
  · 面板本身不做方向性判断——它只说"是什么"，多空双方各自论证"意味着什么"。
"""
from __future__ import annotations

from . import measures
from .utils import get_logger

log = get_logger("cio.evidence")

# 面板分组与呈现顺序。**改这里就是改一部看什么，属于制度变更，不是调参。**
PANEL_GROUPS = ["Valuation", "Quality", "Growth", "Market behaviour", "Positioning / event"]


class Metric:
    """面板上的一格。value 为 None 表示【真的没有】，note 说明为什么没有。"""

    # note 与 miss 是【两件事】，不能共用一个字段：
    #   note —— 口径说明，只在【有值】时显示（"总负债，非有息债务"）
    #   miss —— 缺失原因，只在【没值】时显示（"缺流通股本"）
    # 混用会印出 "总负债/总资产: 无数据（总负债，非有息债务）"——
    # 把口径说明当成了缺失理由，读者完全无法知道到底为什么没有。
    __slots__ = ("key", "label", "group", "value", "unit", "pctile", "basis",
                 "n", "note", "miss", "asof", "level")

    def __init__(self, key, label, group, value=None, unit="", pctile=None,
                 basis="", n=0, note="", miss="", asof="", level=4):
        self.key, self.label, self.group = key, label, group
        self.value, self.unit = value, unit
        self.pctile, self.basis, self.n = pctile, basis, n
        self.note, self.miss, self.asof, self.level = note, miss, asof, level

    def text(self) -> str:
        """喂给 LLM 的一行。缺失时写明原因，让模型知道"没有"而不是"是 0"。"""
        if self.value is None:
            return f"{self.label}: 无数据（{self.miss or '该口径不可得'}）"
        v = f"{self.value:,.2f}{self.unit}" if abs(self.value) < 1000 else f"{self.value:,.0f}{self.unit}"
        out = f"{self.label}: {v}"
        if self.pctile is not None:
            where = "同业内" if self.basis == "sector" else "全域"
            out += f"（{where}第 {self.pctile:.0f} 百分位，n={self.n}）"
        if self.asof:
            out += f"　[截至 {self.asof}]"
        if self.note:
            out += f"　※{self.note}"
        return out


def _pct_of(value, dist) -> "float | None":
    return measures._rank_pct(value, dist) if (value is not None and dist) else None


# ---------------- 估值（需要市值）----------------
def market_cap(fund: dict, px_last: float, as_of):
    """市值 = 最近一次申报的股本 × 现价。返回 (市值, 股本截止日)。

    **这不是实时市值**：股本来自最近一份 10-Q/10-K 封面页，最多约 90 天前，
    期间的回购与增发不会反映。差异对大盘股通常在 1–2% 内，但回购激进的公司会更大。
    因此把股本的截止日一并带出去，报告里必须显示。
    """
    from .fundamentals import pit
    sh, sh_end = pit(fund, "SharesOutstanding", as_of)
    if not sh or sh <= 0 or not px_last or px_last <= 0:
        return None, ""
    return float(sh) * float(px_last), sh_end or ""


def valuation_block(fund: dict, px_last: float, as_of) -> list:
    """估值组：收益率口径（E/P、S/P、FCF/P），不是市盈率。

    为什么用倒数：市盈率在亏损公司上是负数或无穷大，无法排序也无法比较；
    收益率口径（E/P）对亏损公司自然为负，含义连续，可直接放进横截面。
    """
    from .fundamentals import pit
    out = []
    mc, sh_end = market_cap(fund, px_last, as_of)
    if mc is None:
        note = "缺流通股本（SEC 未披露或该发行人不在 us-gaap/dei 覆盖内），估值组整组不可得"
        return [Metric(k, lbl, "Valuation", None, miss=note, level=3)
                for k, lbl in (("earnings_yield", "盈利收益率 E/P"),
                               ("sales_yield", "营收/市值 S/P"),
                               ("fcf_yield", "自由现金流收益率 FCF/P"))]
    ni, ni_e = pit(fund, "NetIncomeLoss", as_of, annual=True)
    rev, rev_e = pit(fund, "Revenues", as_of, annual=True)
    cf, cf_e = pit(fund, "NetCashProvidedByUsedInOperatingActivities", as_of, annual=True)
    cx, cx_e = pit(fund, "PaymentsToAcquirePropertyPlantAndEquipment", as_of, annual=True)
    shnote = f"股本截至 {sh_end}，非实时（期间回购/增发未反映）" if sh_end else ""
    out.append(Metric("earnings_yield", "盈利收益率 E/P", "Valuation",
                      (ni / mc * 100) if ni is not None else None, "%",
                      note=shnote, miss="缺净利润（SEC 未披露年度口径）", asof=ni_e, level=3))
    out.append(Metric("sales_yield", "营收/市值 S/P", "Valuation",
                      (rev / mc * 100) if rev is not None else None, "%",
                      note=shnote, miss="缺营业收入（SEC 未披露年度口径）", asof=rev_e, level=3))
    fcf = (cf - cx) if (cf is not None and cx is not None) else None
    out.append(Metric("fcf_yield", "自由现金流收益率 FCF/P", "Valuation",
                      (fcf / mc * 100) if fcf is not None else None, "%",
                      note=shnote, miss="缺经营现金流或资本开支（缺一不可推，绝不当 0）",
                      asof=cf_e, level=3))
    return out


# ---------------- 质量 ----------------
def quality_block(snap: dict, fund: dict, as_of) -> list:
    """质量组。全部取自 SEC 严格 PIT 快照，方向为自然口径，不翻转符号。"""
    from .fundamentals import pit
    filed = snap.get("filing_accepted_date", "")
    out = [
        Metric("gross_margin", "毛利率", "Quality", snap.get("gross_margin"), "%",
               asof=filed, level=1),
        Metric("op_margin", "营业利润率", "Quality", snap.get("op_margin"), "%",
               asof=filed, level=1),
        Metric("fcf_margin", "自由现金流/营收", "Quality", snap.get("fcf_margin"), "%",
               asof=filed, level=1),
        Metric("liab_assets", "总负债/总资产", "Quality", snap.get("liab_assets"), "%",
               note="总负债，非有息债务", miss="SEC 未披露负债，且资产−权益也不可推",
               asof=filed, level=1),
        Metric("current_ratio", "流动比率", "Quality", snap.get("current_ratio"), "",
               asof=filed, level=1),
        Metric("interest_cover", "利息保障倍数", "Quality", snap.get("interest_cover"), "x",
               asof=filed, level=1),
    ]
    ni, ni_e = pit(fund, "NetIncomeLoss", as_of, annual=True)
    eq, eq_e = pit(fund, "StockholdersEquity", as_of)
    roe = None
    note = ""
    if ni is not None and eq is not None and abs(eq) > 1e-9:
        if eq < 0:
            # 权益为负时 ROE 没有经济含义（分母是负的，盈利越多 ROE 越负）。
            # 这类公司要看的是负债率那一行，不是一个会误导人的比值。
            note = "股东权益为负，ROE 无经济含义，不计算"
        else:
            roe = ni / eq * 100
    else:
        note = "缺净利润或股东权益"
    out.append(Metric("roe", "净资产收益率 ROE", "Quality", roe, "%",
                      miss=note, asof=ni_e or eq_e, level=1))
    return out


# ---------------- 成长 ----------------
def growth_block(snap: dict, fund: dict, as_of) -> list:
    from .fundamentals import pit_yoy
    filed = snap.get("filing_accepted_date", "")
    out = [Metric("rev_growth", "营业收入同比", "Growth", snap.get("rev_growth"), "%",
                  asof=filed, level=1)]
    now, ago = pit_yoy(fund, "NetIncomeLoss", as_of, annual=True)
    eg, note = None, ""
    if now is not None and ago is not None and abs(ago) > 1e-9:
        eg = (now - ago) / abs(ago) * 100
        if ago < 0:
            note = "去年同期为亏损，增速符号需谨慎解读"
    else:
        note = "缺净利润同比对应期"
    out.append(Metric("earnings_growth", "净利润同比", "Growth", eg, "%",
                      note=note if eg is not None else "", miss=note if eg is None else "",
                      asof=filed, level=1))
    # 分析师预期修正：免费源拿不到。**如实留空并写明**，不用别的东西凑。
    out.append(Metric("revisions", "分析师预期修正", "Growth", None,
                      note="免费数据源不提供一致预期；零付费红线下本项长期为空", level=2))
    return out


# ---------------- 市场行为 ----------------
def market_block(df, bench_df, cfg_windows: dict) -> list:
    """市场行为组。全部走共享计算层 measures.py，与二部同源同口径。"""
    w = cfg_windows or {}
    closes = df["close"].values if df is not None and len(df) else []
    vol_d = int(w.get("vol_days", 60))
    beta_d = int(w.get("beta_days", 250))
    out = [
        Metric("vol_60d", f"已实现波动率（{vol_d}日年化）", "Market behaviour",
               measures.ann_vol(closes, vol_d), "%", level=4),
        Metric("downside_60d", f"下行波动（{vol_d}日年化）", "Market behaviour",
               measures.downside_vol(closes, vol_d), "%", level=4),
        Metric("max_dd_250d", "近一年最大回撤", "Market behaviour",
               measures.max_drawdown(closes, int(w.get("maxdd_days", 250))), "%", level=4),
        Metric("px_vs_ma120", "现价相对120日均线", "Market behaviour",
               measures.px_vs_ma(closes, int(w.get("ma_days", 120))), "%", level=5),
        Metric("trail_12_1", "尾随12-1收益", "Market behaviour",
               measures.trailing_return(closes, int(w.get("trail_lookback", 250)),
                                        int(w.get("trail_skip", 21))), "%", level=4),
    ]
    beta, corr, _n = measures.beta_corr(df, bench_df, beta_d, int(w.get("corr_days", 60)))
    out.append(Metric("beta_250d", f"Beta（{beta_d}日，对基准）", "Market behaviour",
                      beta, "", level=4))
    out.append(Metric("corr_bench", "与基准日收益相关性", "Market behaviour", corr, "", level=4))

    # 相对强弱：个股与基准在同一段区间的收益差。日期对齐由 measures 保证。
    rs, rs_note = _relative_strength(df, bench_df, int(w.get("trail_lookback", 250)))
    out.append(Metric("rel_strength", "相对基准超额（近一年）", "Market behaviour",
                      rs, "%", miss=rs_note, level=4))

    # 单日主导：波动率是不是被一天撑起来的。这条对辩论特别重要——
    # 多空双方若围绕"该股波动率极高"论证，而那个数字其实是一次事件，论证就站不住。
    mv, share, _i = measures.vol_concentration(closes, vol_d)
    if share is not None and mv is not None:
        out.append(Metric("vol_single_day", "波动率的单日集中度", "Market behaviour",
                          share * 100, "%",
                          note=f"最大单日 {mv:+.0f}%；占比高说明波动来自一次事件而非持续状态",
                          level=4))
    return out


def _relative_strength(df, bench_df, n: int):
    """近 n 个交易日的个股收益 − 基准收益（按日期对齐取交集）。"""
    import numpy as np
    import pandas as pd
    if df is None or bench_df is None or not len(df) or not len(bench_df):
        return None, "缺个股或基准行情"
    a = pd.DataFrame({"date": pd.to_datetime(df["date"]), "s": df["close"].astype(float)})
    b = pd.DataFrame({"date": pd.to_datetime(bench_df["date"]), "b": bench_df["close"].astype(float)})
    m = a.merge(b, on="date", how="inner").sort_values("date")
    ok = np.isfinite(m["s"].values) & np.isfinite(m["b"].values) & (m["s"].values > 0) & (m["b"].values > 0)
    m = m[ok]
    if len(m) < n * 0.8:
        return None, f"对齐后仅 {len(m)} 根，不足 {n} 日窗口的 80%"
    s = m["s"].values[-n:]
    bb = m["b"].values[-n:]
    return measures._fin((s[-1] / s[0] - bb[-1] / bb[0]) * 100), ""


# ---------------- 定位 / 事件 ----------------
def positioning_block(events_note: str = "") -> list:
    """定位与事件组。

    这一组目前大部分为空，且**应该显式为空**：
    分析师修正与机构持仓变化（13F）在零付费红线下拿不到可靠数据。
    留空并写明原因，比用别的东西凑一个数字诚实——凑出来的数字会被多空双方当成事实援引。
    """
    return [
        Metric("analyst_revision", "分析师评级/目标价修正", "Positioning / event", None,
               miss="免费源不提供；零付费红线下长期为空", level=2),
        Metric("institutional", "机构持仓变化", "Positioning / event", None,
               miss="需解析 SEC 13F，尚未实现（roadmap）", level=2),
        Metric("event_reaction", "近期事件与价格反应", "Positioning / event", None,
               miss=events_note or "由一部自行采集的事件材料承担，见材料清单", level=2),
    ]


# ---------------- 组装 ----------------
def build_panel(code: str, df, bench_df, fund: dict, snap: dict, as_of,
                cfg_windows: dict | None = None, peers: dict | None = None) -> list:
    """产出这只标的的完整固定面板。

    peers：{key: [同业该指标的值]}，用于给出行业分位。没有就不给分位——
    绝不用一个 n=2 的"分位"冒充横截面信息。
    """
    px_last = None
    try:
        if df is not None and len(df):
            px_last = float(df["close"].values[-1])
    except Exception:
        px_last = None

    panel: list = []
    panel += valuation_block(fund or {}, px_last, as_of)
    panel += quality_block(snap or {}, fund or {}, as_of)
    panel += growth_block(snap or {}, fund or {}, as_of)
    panel += market_block(df, bench_df, cfg_windows or {})
    panel += positioning_block()

    if peers:
        min_n = 8          # 分位分母下限；太小的分位没有信息量，只会误导
        for m in panel:
            dist = [v for v in (peers.get(m.key) or []) if v is not None]
            if m.value is not None and len(dist) >= min_n:
                m.pctile = _pct_of(m.value, dist)
                m.basis, m.n = "sector", len(dist)
    return panel


def render_panel(panel: list) -> str:
    """把面板渲染成喂给多空双方的文本。

    **两边拿到的是同一份、完整的一份。**
    面板固定只解决了"不让 LLM 从 462 个里搜索"，
    但多头仍会只援引利好、空头只援引利空——挑选行为会从引用环节回来。
    所以这里刻意把全部指标（含缺失项）一次性摊开，
    再由提示词强制双方回应对自己最不利的三条。
    """
    lines = []
    for g in PANEL_GROUPS:
        items = [m for m in panel if m.group == g]
        if not items:
            continue
        lines.append(f"【{g}】")
        for m in items:
            lines.append("  · " + m.text())
    return "\n".join(lines)


def panel_dict(panel: list) -> dict:
    """结构化留痕，供归档与后续失效条件复检。"""
    return {m.key: {"label": m.label, "group": m.group, "value": m.value,
                    "unit": m.unit, "pctile": m.pctile, "basis": m.basis,
                    "n": m.n, "asof": m.asof, "note": m.note, "miss": m.miss}
            for m in panel}
