"""《证券二部 — Systematic Analytics》渲染（Markdown + PDF）。

渲染层的纪律和引擎一样重要：
  · 每个数字旁边必须带口径（窗口写在列头，百分位带 basis）。
  · Exceptions 区块底部必须印出当期阈值原文——让读者分清"测量"与"我们定的红线"。
  · 缺失一律显示 "—"，绝不显示 0。0 是一个值，缺失不是。
  · 全篇不出现任何方向性措辞（买/卖/看多/看空/推荐/超配）。
"""
from __future__ import annotations

from .models import AnalyticsReport, AnalyticsRow

# 异常类别 → 展示名（顺序即报告内的分组顺序）
_KIND_LABEL = [
    ("corp_action", "Corporate action"),
    ("price_anomaly", "Volatility dominated by one session"),
    ("price_stale", "Stale price series"),
    ("stale", "Stale fundamentals"),
    ("drawdown", "Drawdown"),
    ("volatility", "Volatility"),
    ("beta", "Beta"),
    ("liab_assets", "Liabilities / Assets"),
    ("correlation", "Correlation"),
    ("extended", "Price extended vs average"),
]


def _n(v, digits: int = 1, suffix: str = "", missing: str = "—") -> str:
    """数值格式化。None → missing。缺失就是缺失，绝不用 0 冒充。

    missing 有两种值，**必须区分开**：
      "—"    公司确实没披露这个科目（数据在，指标不在）
      "n/a"  我们的 parser 覆盖不到这类申报（20-F / IFRS）——不是公司的问题，是我们的
    两者在 build55 里长得一模一样，读者无从分辨是"这家公司不报"还是"我们没做"。
    """
    if v is None:
        return missing
    try:
        return f"{float(v):.{digits}f}{suffix}"
    except Exception:
        return "—"


def _d(r: AnalyticsRow, field: str) -> str:
    """该字段是不是由恒等式反推来的 → 返回星号或空串。

    星号必须打在【那个格子】上，不能打在 ticker 上：
    看到 "ABBV*" 读者不知道是负债率反推的、毛利率反推的、还是两个都是。
    """
    return "*" if field in (r.derived_fields or []) else ""


def _pc(r: AnalyticsRow, field: str) -> str:
    """百分位单元格：值 + basis。basis 必须随值出现——
    7 只样本里的 90th 和 500 只里的 90th 印出来一模一样，不标就是误导。"""
    p = r.pctile.get(field)
    if not p:
        return "—"
    tag = "s" if p.basis == "sector" else "u"
    return f"{p.value:.0f}{tag}"


# 脚注标记用【方括号数字】，不用上标字符。
# 原因是一个真实的静默失败：PDF 用的 CJK 字体里有 ¹²³⁴ 却【没有 ⁵】(U+2075)，
# 于是第 5 个标记在 PDF 里渲染成空白——行上什么都不显示，图例那行以空格开头。
# 而带这个标记的恰好是最需要解释的几行（外国发行人）。
# 一个会凭空消失的脚注标记，正是本项目最忌讳的那类错误：不报错、看起来正常、信息没了。
_MARKS = [
    ("member", "[1]", "not a benchmark constituent — measured on its own data, ranked against the index distribution"),
    ("identity", "[2]", "corporate-action discontinuity — price history spans two different economic entities"),
    ("filing", "[3]", "fundamentals older than this company's own filing cadence (see Exceptions)"),
    ("price", "[4]", "price series stale — every risk number in that row ends at its last bar, not at the as-of date"),
    ("ifrs", "[5]", "files under IFRS (20-F) — outside the us-gaap concepts we read, so fundamentals are blank by "
                    "coverage, not by fetch failure"),
]


def _mark(r: AnalyticsRow) -> str:
    m = []
    if not r.member:
        m.append(_MARKS[0][1])
    if r.identity_flag:
        m.append(_MARKS[1][1])
    if r.filing_stale:
        m.append(_MARKS[2][1])
    if r.price_stale_days:
        m.append(_MARKS[3][1])
    if r.no_us_gaap:
        m.append(_MARKS[4][1])
    return "".join(m)


def _legend(r: AnalyticsReport) -> list:
    """表格里用了哪些脚注标记，就必须给出对应图例。
    **PDF 尤其不能漏**——推送到 Telegram 的是 PDF，Markdown 只留在磁盘上；
    只有 MD 有图例、PDF 没有，等于把 ¹²³⁴ 变成读者无从解读的符号，
    而带这些标记的恰好是最需要说明的那几行。"""
    out = []
    if any(not x.member for x in r.rows):
        out.append(_MARKS[0][1] + " " + _MARKS[0][2])
    if any(x.identity_flag for x in r.rows):
        out.append(_MARKS[1][1] + " " + _MARKS[1][2])
    if any(x.filing_stale for x in r.rows):
        out.append(_MARKS[2][1] + " " + _MARKS[2][2])
    if any(x.price_stale_days for x in r.rows):
        out.append(_MARKS[3][1] + " " + _MARKS[3][2])
    if any(x.no_us_gaap for x in r.rows):
        out.append(_MARKS[4][1] + " " + _MARKS[4][2])
    return out


def _theme(r: AnalyticsRow) -> str:
    return ", ".join(r.focus_theme) if r.focus_theme else "—"


def _has_fundamentals(r: AnalyticsReport) -> bool:
    """本轮是否真有基本面数据。全空时应该说明原因，而不是印一张全是破折号的表。"""
    return any(x.filing_accepted_date or x.liab_assets is not None or x.op_margin is not None
               for x in r.rows)


_SATURATION = 0.30       # 触发面达到展示集的这个比例即收敛为 breadth summary
_TOP_EXTREME = 5         # 收敛后仍逐条展开的最极端条数


def _breach_names(kind_items) -> set:
    """这一类异常涉及多少【只】标的。
    相关性异常的 code 形如 "AAPL/MSFT"，是一【对】不是一【只】——
    直接数 code 会得到"66 of 35 displayed names"这种分子分母不同量纲的句子。"""
    names: set = set()
    for e in kind_items:
        names.update(x for x in str(e.code).split("/") if x)
    return names


def _collapse(kind_items, n_displayed: int):
    """大面积越线时把清单收敛成【breadth summary + 最极端几条 + 其余只列代码】。

    为什么要收敛：如果 35 只里有 17 只"异常"，它就不再是异常，而是当前 regime。
    此时读者真正需要知道的是"这是普遍状态"这件事本身，
    而不是逐行读 17 遍同一句话——那只会把报告撑到 5 页，并让真正罕见的异常被淹没。
    收敛【不丢信息】：最极端的仍逐条展开，其余仍点名，只是不重复整句。
    阈值该不该调仍是 CEO/CRO 的决定，二部只负责把广度说出来。

    返回 (summary, 展开的条目, 其余标的代码)。
    """
    names = _breach_names(kind_items)
    n = len(names)
    if not n_displayed or n / n_displayed < _SATURATION:
        return "", list(kind_items), []
    ranked = sorted(kind_items, key=lambda e: -abs(e.extremity or 0.0))
    head = ranked[:_TOP_EXTREME]
    shown = _breach_names(head)
    rest = sorted(names - shown)
    summary = (f"{n} of {n_displayed} displayed names breach this threshold — treated as a broad "
               f"watchlist condition, not {n} separate exceptions. At this breadth the line describes "
               f"the current regime; whether it is still the right line is a setting in "
               f"analytics_thresholds.yaml, not a result. Most extreme shown below.")
    return summary, head, rest


def _saturation_note(kind_items, n_displayed: int) -> str:
    """兼容旧调用（自检用）：只返回 summary 文本。"""
    return _collapse(kind_items, n_displayed)[0]


# ---------------- Markdown ----------------
def render_analytics_md(r: AnalyticsReport) -> str:
    L: list[str] = []
    L.append("# Unit B — Systematic Analytics")
    L.append("")
    L.append(f"**As-of trade date:** {r.as_of_trade_date or 'n/a'}　·　"
             f"**Generated:** {r.generated_at_utc} / {r.generated_at_market} (market)")
    L.append("")
    L.append("> **Deterministic · zero LLM · measurement only.** This report describes the current "
             "risk, style and financial state of watchlist names. It contains no directional view, "
             "no ranking of attractiveness, and no position sizing.")
    L.append(">")
    L.append(f"> **Alpha model:** {r.alpha_status}　·　**Formal alpha vote:** **{r.alpha_vote}**　·　"
             f"**Research:** {r.research_status}　·　"
             f"**Production Factor Set:** {', '.join(r.production_factor_set) or '∅ (empty)'}")
    L.append(">")
    L.append("> Unit B measures. **CRO** assesses risk. **Portfolio Construction** sizes. **CEO** decides.")
    L.append("")
    L.append(f"Benchmark {r.benchmark} (source `{r.bench_source}`, basis `{r.bench_basis}`)　·　"
             f"universe `{r.universe_src}`"
             + (f" snapshot `{r.universe_snapshot}`" if r.universe_snapshot else ""))
    L.append("")
    L.append(f"Coverage: {r.funnel}")
    if r.status.degraded:
        L.append("")
        for d in r.status.degraded:
            L.append(f"> ⚠ {d}")

    # ---- 1. Risk snapshot ----
    L.append("")
    L.append("## 1. Watchlist Risk Snapshot")
    L.append("")
    L.append("*Percentile suffix: `s` = within GICS sector, `u` = within full universe "
             "(sector basis falls back to universe when the sector has too few names to rank). "
             "Percentiles are of the raw value, ascending: 90 = higher than 90% of names.*")
    L.append("")
    bcol = f"Corr_{r.bench_source or 'bench'}_60d"
    L.append(f"| Ticker | Theme | Vol_60d | pct | DownVol_60d | pct | Beta_250d | {bcol} | MaxDD_250d | Px_vs_MA120 | Trail_12-1 |")
    L.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for x in r.rows:
        L.append(f"| {x.code}{_mark(x)} | {_theme(x)} | {_n(x.vol_60d, 0, '%')} | {_pc(x,'vol_60d')} | "
                 f"{_n(x.downside_60d, 0, '%')} | {_pc(x,'downside_60d')} | {_n(x.beta_250d, 2)} | "
                 f"{_n(x.corr_bench_60d, 2)} | {_n(x.max_dd_250d, 0, '%')} | "
                 f"{_n(x.px_vs_ma120, 1, '%')} | {_n(x.trail_12_1, 1, '%')} |")
    if not r.rows:
        L.append("| — | — | — | — | — | — | — | — | — | — | — |")
    L.append("")
    for n_ in _legend(r):
        L.append(f"- {n_}")
    L.append("")
    L.append(f"*Estimation windows: {r.windows_note}*")

    # ---- 2. Fundamentals ----
    L.append("")
    L.append("## 2. Fundamental / Balance-Sheet Snapshot")
    L.append("")
    L.append(f"> {r.filing_window_note}")
    L.append("")
    if not _has_fundamentals(r):
        # 一整张全是 "—" 的表没有信息量，只有噪声。说清楚为什么没有，比印 35 行破折号有用。
        L.append(f"*No fundamental data in this run — {r.fundamentals_note or 'source unavailable'}. "
                 f"Risk measurements above are unaffected.*")
    else:
        L.append("| Ticker | Filing accepted | Age | Liab/Assets | pct | Gross margin | Op margin | FCF margin | FCF/Assets | Rev growth | Current ratio | Int. cover |")
        L.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for x in r.rows:
            miss = "n/a" if x.no_us_gaap else "—"
            age = f"{x.filing_age_days}d" if x.filing_age_days is not None else miss
            if x.filing_stale:
                age += " ⚠"
            tk = x.code + (_MARKS[4][1] if x.no_us_gaap else "")
            filed = x.filing_accepted_date or ("20-F / IFRS" if x.no_us_gaap else "—")
            L.append(f"| {tk} | {filed} | {age} | {_n(x.liab_assets,0,'%',miss)}{_d(x,'liab_assets')} | "
                     f"{_pc(x,'liab_assets')} | {_n(x.gross_margin,0,'%',miss)}{_d(x,'gross_margin')} | "
                     f"{_n(x.op_margin,0,'%',miss)} | "
                     f"{_n(x.fcf_margin,0,'%',miss)} | {_n(x.fcf_assets,1,'%',miss)} | "
                     f"{_n(x.rev_growth,0,'%',miss)} | "
                     f"{_n(x.current_ratio,2,'',miss)} | {_n(x.interest_cover,1,'x',miss)} |")
        L.append("")
        L.append("- **Liab/Assets is TOTAL liabilities ÷ total assets — it is not debt/assets.** Total "
                 "liabilities include payables, deferred revenue, lease and pension obligations and taxes; "
                 "interest-bearing debt is only part of it. A true debt/assets ratio would require pulling "
                 "short-term borrowings and long-term debt separately, and cannot be derived from assets "
                 "minus equity. Reported in its natural direction — nothing here is sign-flipped, because "
                 "flipping a sign is a judgment and this report does not make judgments.")
        L.append("- Blank cells mean the company does not disclose that concept under the tags we read. "
                 "Missing values are left missing — never filled with a sector median.")
        if any(x.derived_fields for x in r.rows):
            L.append("- `*` on a cell = that value came from the balance-sheet identity rather than a direct tag. "
                     "Liab/Assets`*`: Liabilities = Assets − total equity incl. non-controlling interests. "
                     "Gross margin`*`: Gross profit = Revenue − Cost of revenue. Both source tags are optional "
                     "in us-gaap; deriving them is exact, not estimated, and still point-in-time.")
        if any((x.liab_assets or 0) > 100 for x in r.rows):
            L.append("- Liab/Assets above 100% means **negative shareholders' equity** — total liabilities exceed "
                     "total assets. This is real, not an error: large buybacks or goodwill-heavy acquisitions "
                     "routinely produce it.")
        L.append("- Two different blanks: `—` = the company does not tag that concept "
                 "(the filing is covered, the line item is not). `n/a` = **our parser does not cover that "
                 "filing regime at all** — these are foreign private issuers filing 20-F under IFRS, "
                 "outside the SEC's `us-gaap` namespace. The first is the company's choice; the second "
                 "is our gap, and it is on the roadmap. Risk measurements above are unaffected either way.")

    # ---- 3. Style / exposure ----
    L.append("")
    L.append("## 3. Style & Exposure")
    L.append("")
    L.append("| Ticker | GICS sector | Focus theme | Benchmark member | Beta_250d | Vol pct | Trail_12-1 pct | Last price |")
    L.append("|---|---|---|---|---:|---:|---:|---:|")
    for x in r.rows:
        L.append(f"| {x.code} | {x.gics_sector or '—'} | {_theme(x)} | {'yes' if x.member else 'no'} | "
                 f"{_n(x.beta_250d,2)} | {_pc(x,'vol_60d')} | {_pc(x,'trail_12_1')} | {_n(x.px_last,2)} |")
    if not r.rows:
        L.append("| — | — | — | — | — | — | — | — |")
    L.append("")
    L.append("- `Trail_12-1` is the trailing 12-month return excluding the most recent month. "
             "It is a **descriptive state variable** — where the price sits relative to its own past — "
             "not a momentum factor and not a forecast.")

    # Portfolio block — 条件渲染
    if r.portfolio.present:
        p = r.portfolio
        L.append("")
        L.append(f"### Portfolio aggregate — account `{p.account}`")
        L.append("")
        L.append(f"- Positions priced: **{p.n_positions}**　·　market value **{r.currency}{p.market_value:,.0f}**")
        L.append(f"- Portfolio Beta_250d (value-weighted): **{_n(p.beta_250d,2)}**")
        L.append(f"- Largest sector: **{p.top_sector or '—'} {p.top_sector_pct:.0f}%**")
        if p.sector_weights:
            L.append("- Sector weights: " + " · ".join(f"{k} {v:.0f}%" for k, v in p.sector_weights.items()))
        if p.corr_clusters:
            L.append("- High-correlation pairs: " + "; ".join(p.corr_clusters))
        L.append(f"- Coverage: {p.coverage_note}")
    elif r.portfolio.coverage_note:
        L.append("")
        L.append(f"*Portfolio aggregate omitted — {r.portfolio.coverage_note}.*")

    # ---- 4. Exceptions ----
    L.append("")
    L.append("## 4. Exceptions")
    L.append("")
    if not r.exceptions:
        L.append("No name breached any configured threshold this run.")
    else:
        by_kind: dict = {}
        for e in r.exceptions:
            by_kind.setdefault(e.kind, []).append(e)
        for kind, label in _KIND_LABEL:
            items = by_kind.get(kind) or []
            if not items:
                continue
            L.append(f"**{label}**")
            L.append("")
            sat, shown, rest = _collapse(items, r.displayed_count)
            if sat:
                L.append(f"> ⚠ {sat}")
                L.append("")
            for e in shown:
                L.append(f"- {e.message}")
            if rest:
                L.append(f"- Also breaching: {', '.join(rest)}")
            L.append("")
    L.append("> These are **state observations**, not recommendations. A breach says a threshold was "
             "crossed; it does not say anything should be bought, sold or resized.")
    L.append("")
    L.append(f"**Thresholds in force** (`{r.thresholds_version}`, from `config/analytics_thresholds.yaml` — "
             f"these are chosen by us, not produced by a model):")
    L.append("")
    for t in r.thresholds_shown:
        L.append(f"- {t}")

    # ---- footer ----
    L.append("")
    L.append("---")
    L.append("")
    L.append(f"*Run `{r.run_id}` · as-of trade date {r.as_of_trade_date or 'n/a'} · "
             f"generated {r.generated_at_utc} · thresholds `{r.thresholds_version}`.*")
    L.append("")
    L.append("*Unit B produces measurements only. It holds no validated alpha model and abstains from "
             "any formal directional vote. Research factor work is dormant and may reopen only under a "
             "newly registered hypothesis passing the Admission Gate. Not an investment recommendation.*")
    return "\n".join(L)


# ---------------- PDF ----------------
def render_analytics_pdf(r: AnalyticsReport, path: str) -> str:
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.platypus import KeepTogether, Paragraph, Spacer, Table, TableStyle

    from .render import _build_pdf, _esc

    def _tbl(data, widths=None, size=7.2, num_from=1):
        """num_from：从第几列开始右对齐（数值列）。文本列必须左对齐，
        否则 GICS sector / Focus theme 这种长文本会被推到右边，读起来像数字。"""
        from .render import _CJK_FONT
        t = Table(data, colWidths=widths, hAlign="LEFT", repeatRows=1)
        t.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), _CJK_FONT),
            ("FONTSIZE", (0, 0), (-1, -1), size),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F3A5F")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f5f8")]),
            ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#c8d2de")),
            ("ALIGN", (0, 0), (num_from - 1, -1), "LEFT"),
            ("ALIGN", (num_from, 1), (-1, -1), "RIGHT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 2.4), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.4),
            ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ]))
        return t

    def story(S):
        el = [Paragraph("Unit B — Systematic Analytics", S["title"])]
        el.append(Paragraph(_esc(f"As-of trade date {r.as_of_trade_date or 'n/a'}  ·  generated "
                                 f"{r.generated_at_utc} / {r.generated_at_market} (market)"), S["small"]))
        el.append(Spacer(1, 5))
        el.append(Paragraph("<b>Deterministic · zero LLM · measurement only.</b> Describes the current risk, "
                            "style and financial state of watchlist names. No directional view, no ranking "
                            "of attractiveness, no position sizing.", S["p"]))
        el.append(Paragraph(_esc(f"Alpha model: {r.alpha_status}   ·   Formal alpha vote: {r.alpha_vote}   ·   "
                                 f"Research: {r.research_status}   ·   Production Factor Set: "
                                 f"{', '.join(r.production_factor_set) or 'empty'}"), S["p"]))
        el.append(Paragraph("Unit B measures.  CRO assesses risk.  Portfolio Construction sizes.  CEO decides.",
                            S["small"]))
        el.append(Paragraph(_esc(f"Benchmark {r.benchmark} (source {r.bench_source}, basis {r.bench_basis})  ·  "
                                 f"universe {r.universe_src}"
                                 + (f" snapshot {r.universe_snapshot}" if r.universe_snapshot else "")
                                 + f"  ·  {r.funnel}"), S["small"]))
        for d in r.status.degraded:
            el.append(Paragraph(_esc("WARNING: " + d), S["p"]))
        el.append(Spacer(1, 7))

        # 1 — risk
        el.append(Paragraph("1. Watchlist Risk Snapshot", S["h"]))
        el.append(Paragraph("Percentile suffix: s = within GICS sector, u = within full universe "
                            "(sector falls back to universe when too few names to rank). Ascending: "
                            "90 = higher than 90% of names.", S["small"]))
        head = ["Ticker", "Vol_60d", "pct", "DownVol", "pct", "Beta_250d",
                f"Corr_{r.bench_source or 'bench'}", "MaxDD", "vs MA120", "Tr 12-1"]
        data = [head] + [[
            f"{x.code}{_mark(x)}", _n(x.vol_60d, 0, "%"), _pc(x, "vol_60d"),
            _n(x.downside_60d, 0, "%"), _pc(x, "downside_60d"), _n(x.beta_250d, 2),
            _n(x.corr_bench_60d, 2), _n(x.max_dd_250d, 0, "%"),
            _n(x.px_vs_ma120, 1, "%"), _n(x.trail_12_1, 1, "%")] for x in r.rows]
        w = [1.9 * cm, 1.7 * cm, 1.0 * cm, 1.7 * cm, 1.0 * cm, 1.7 * cm, 1.5 * cm, 1.5 * cm, 1.7 * cm, 1.6 * cm]
        el.append(_tbl(data, w))
        for n_ in _legend(r):
            el.append(Paragraph(_esc(n_), S["small"]))
        el.append(Paragraph(_esc(f"Estimation windows: {r.windows_note}"), S["small"]))

        # 2 — fundamentals
        el.append(Paragraph("2. Fundamental / Balance-Sheet Snapshot", S["h"]))
        el.append(Paragraph(_esc(r.filing_window_note), S["small"]))
        if not _has_fundamentals(r):
            el.append(Paragraph(_esc(f"No fundamental data in this run — "
                                     f"{r.fundamentals_note or 'source unavailable'}. "
                                     f"Risk measurements above are unaffected."), S["p"]))
        else:
            head2 = ["Ticker", "Filed", "Age", "Liab/Ast", "pct", "GM", "OpM", "FCF/Rev", "FCF/Ast",
                     "RevGr", "CurR", "IntCov"]
            def _frow(x):
                m = "n/a" if x.no_us_gaap else "—"
                return [x.code + (_MARKS[4][1] if x.no_us_gaap else ""),
                        x.filing_accepted_date or ("20-F/IFRS" if x.no_us_gaap else "—"),
                        (f"{x.filing_age_days}d" + (" !" if x.filing_stale else ""))
                        if x.filing_age_days is not None else m,
                        _n(x.liab_assets, 0, "%", m) + _d(x, "liab_assets"), _pc(x, "liab_assets"),
                        _n(x.gross_margin, 0, "%", m) + _d(x, "gross_margin"),
                        _n(x.op_margin, 0, "%", m), _n(x.fcf_margin, 0, "%", m),
                        _n(x.fcf_assets, 1, "%", m), _n(x.rev_growth, 0, "%", m),
                        _n(x.current_ratio, 2, "", m), _n(x.interest_cover, 1, "x", m)]
            data2 = [head2] + [_frow(x) for x in r.rows]
            w2 = [1.6 * cm, 1.8 * cm, 1.2 * cm, 1.5 * cm, 0.9 * cm, 1.1 * cm, 1.1 * cm,
                  1.4 * cm, 1.4 * cm, 1.2 * cm, 1.1 * cm, 1.2 * cm]
            el.append(_tbl(data2, w2, size=6.8, num_from=2))   # ticker / filed date 是文本
            el.append(Paragraph("Liab/Ast is TOTAL liabilities / total assets - NOT debt/assets. Total liabilities "
                                "include payables, deferred revenue, leases, pensions and taxes; interest-bearing "
                                "debt is only part of it. Shown in its natural direction; nothing is sign-flipped. "
                                "Two different blanks: '-' the company does not tag that concept; "
                                "'n/a' our parser does not cover that filing regime (20-F / IFRS foreign issuers). "
                                "Neither is ever filled in.", S["small"]))
            if any(x.derived_fields for x in r.rows):
                el.append(Paragraph("* on a cell = that value came from the balance-sheet identity, not a direct "
                                    "tag (Liabilities = Assets - total equity incl. non-controlling interests; "
                                    "Gross profit = Revenue - Cost of revenue) - exact, not estimated, "
                                    "and still point-in-time.", S["small"]))
            if any((x.liab_assets or 0) > 100 for x in r.rows):
                el.append(Paragraph("Liab/Ast above 100% means negative shareholders' equity - total liabilities "
                                    "exceed total assets. Real, not an error: large buybacks or goodwill-heavy "
                                    "acquisitions routinely produce it.", S["small"]))

        # 3 — style
        el.append(Paragraph("3. Style &amp; Exposure", S["h"]))
        head3 = ["Ticker", "GICS sector", "Focus theme", "Member", "Beta_250d", "Vol pct", "Tr12-1 pct", "Last"]
        data3 = [head3] + [[
            x.code, x.gics_sector or "—", _theme(x), "yes" if x.member else "no",
            _n(x.beta_250d, 2), _pc(x, "vol_60d"), _pc(x, "trail_12_1"), _n(x.px_last, 2)] for x in r.rows]
        w3 = [1.7 * cm, 3.6 * cm, 3.4 * cm, 1.4 * cm, 1.7 * cm, 1.4 * cm, 1.6 * cm, 1.6 * cm]
        el.append(_tbl(data3, w3, num_from=4))     # 前三列是文本（ticker / sector / theme）
        el.append(Paragraph("Trail_12-1 is the trailing 12-month return excluding the most recent month — "
                            "a descriptive state variable, not a momentum factor and not a forecast.", S["small"]))

        p = r.portfolio
        if p.present:
            el.append(Paragraph(_esc(f"Portfolio aggregate — account {p.account}"), S["h"]))
            el.append(Paragraph(_esc(
                f"Positions priced {p.n_positions} · market value {r.currency}{p.market_value:,.0f} · "
                f"portfolio Beta_250d {_n(p.beta_250d,2)} · largest sector {p.top_sector or '—'} "
                f"{p.top_sector_pct:.0f}%"), S["p"]))
            if p.sector_weights:
                el.append(Paragraph(_esc("Sector weights: " + " · ".join(
                    f"{k} {v:.0f}%" for k, v in p.sector_weights.items())), S["small"]))
            if p.corr_clusters:
                el.append(Paragraph(_esc("High-correlation pairs: " + "; ".join(p.corr_clusters)), S["small"]))
            el.append(Paragraph(_esc(f"Coverage: {p.coverage_note}"), S["small"]))
        elif p.coverage_note:
            el.append(Paragraph(_esc(f"Portfolio aggregate omitted — {p.coverage_note}."), S["small"]))

        # 4 — exceptions
        el.append(Paragraph("4. Exceptions", S["h"]))
        if not r.exceptions:
            el.append(Paragraph("No name breached any configured threshold this run.", S["p"]))
        else:
            by_kind: dict = {}
            for e in r.exceptions:
                by_kind.setdefault(e.kind, []).append(e)
            for kind, label in _KIND_LABEL:
                items = by_kind.get(kind) or []
                if not items:
                    continue
                blk = [Paragraph(f"<b>{_esc(label)}</b>", S["p"])]
                sat, shown, rest = _collapse(items, r.displayed_count)
                if sat:
                    blk.append(Paragraph(_esc("! " + sat), S["small"]))
                for e in shown:
                    blk.append(Paragraph("• " + _esc(e.message), S["p"]))
                if rest:
                    blk.append(Paragraph(_esc("• Also breaching: " + ", ".join(rest)), S["p"]))
                el.append(KeepTogether(blk))
        el.append(Spacer(1, 3))
        el.append(Paragraph("These are state observations, not recommendations. A breach says a threshold was "
                            "crossed; it does not say anything should be bought, sold or resized.", S["small"]))
        el.append(Paragraph(_esc(f"Thresholds in force ({r.thresholds_version}, from "
                                 f"config/analytics_thresholds.yaml — chosen by us, not produced by a model):"),
                            S["small"]))
        for t in r.thresholds_shown:
            el.append(Paragraph("• " + _esc(t), S["small"]))

        el.append(Spacer(1, 7))
        el.append(Paragraph(_esc(f"Run {r.run_id} · as-of trade date {r.as_of_trade_date or 'n/a'} · "
                                 f"generated {r.generated_at_utc} · thresholds {r.thresholds_version}."), S["small"]))
        el.append(Paragraph("Unit B produces measurements only. It holds no validated alpha model and abstains "
                            "from any formal directional vote. Research factor work is dormant and may reopen "
                            "only under a newly registered hypothesis passing the Admission Gate. "
                            "Not an investment recommendation.", S["small"]))
        return el

    _build_pdf(path, "Unit B — Systematic Analytics", story)
    return path
