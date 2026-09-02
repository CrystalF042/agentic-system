"""渲染：结构化 Brief / TopicReport → Markdown（可检索资产）+ PDF（阅读/永久档）。
Build 3 版式（卖方研究台风格）：BLUF 置顶、单主标签、要闻一句话、数据锚定、今日待观察。
PDF 用 reportlab 内置 CID 中文字体（STSong-Light），无需外部字体文件，跨机稳定。"""
from __future__ import annotations

import re

from .config import BASE, market
from .models import (Brief, CRORating, DossierReport, IndexQuote, NewsItem, PnLStatement,
                     TopicReport, UnitAAdvice, UnitBAdvice)
from .utils import get_logger, truncate

log = get_logger("cio.render")


def _rlang() -> str:
    """报表语言：us→en，cn→zh。默认 zh，不影响 A 股既有版式。"""
    try:
        return market().get("lang", "zh")
    except Exception:
        return "zh"


# 个股情报档案 · 双语标签（英文用于 CIO_MARKET=us；中文为默认）
_DOSSIER_L = {
    "zh": {
        "stamp": "（北京）/ {ny}",
        "meta": "资料库驱动 ｜ 命中存量库 {ad} 篇 ｜ 本次增量 {fd} 条 ｜ 指令：{subj}",
        "h_anchor": "一、数据锚定（行情真值）",
        "no_anchor": "非个股或行情取数降级，无锚定。",
        "h_timeline": "二、历史脉络（资料库沉淀）",
        "no_timeline": "资料库暂无该标的历史沉淀。",
        "h_recent": "三、近期增量",
        "no_recent": "无新增量（存量充足或免费源暂无新料）。",
        "h_hits": "四、关注池命中回顾",
        "no_hits": "该标的历史上未触发关注池信号。",
        "h_filings": "五、公告追踪",
        "h_cross": "六、交叉验证与冲突",
        "h_complete": "七、数据完备度",
        "h_decisions": "八、待 CEO 决断事项（只列事实 + 来源，无方向判断）",
        "none": "无。",
        "src": "来源",
        "footer": "CIO 个股情报档案：资料库驱动、事实先行、只报事实无方向判断；行情数字取自 yfinance 真值。",
    },
    "en": {
        "stamp": "{ny} ET",
        "meta": "Archive-driven ｜ {ad} docs in store ｜ {fd} fresh this run ｜ Query: {subj}",
        "h_anchor": "I. Data Anchor (market truth)",
        "no_anchor": "Not a single stock, or quote fetch degraded — no anchor.",
        "h_timeline": "II. History (from the archive)",
        "no_timeline": "No historical record for this name in the archive yet.",
        "h_recent": "III. Recent Developments",
        "no_recent": "No fresh items (archive sufficient, or nothing new from free sources).",
        "h_hits": "IV. Watchlist Signal History",
        "no_hits": "This name has never triggered a watchlist signal.",
        "h_filings": "V. Filings",
        "h_cross": "VI. Cross-Check & Conflicts",
        "h_complete": "VII. Data Completeness",
        "h_decisions": "VIII. For CEO Decision (facts + sources only; no directional call)",
        "none": "None.",
        "src": "source",
        "footer": "CIO stock-intelligence dossier: archive-driven, facts first, no directional call; quotes from yfinance (true values).",
    },
}


# 盘前情报简报 · 双语标签
_BRIEF_L = {
    "zh": {
        "title": "CIO 盘前情报简报",
        "stamp": "{bj}（北京）/ {ny}",
        "status": "数据采集状态：采集 {f} / 去重 {d} / 入库向量 {v}；降级：{deg}",
        "none_deg": "无",
        "h_anchor": "一、数据锚定（参考行情源 · 行情数字唯一取数口径）",
        "h_anom": "◆ 市场异动（客观事实 · 无方向解读）",
        "fund": "资金面", "anchor_deg": "行情数据采集降级。",
        "focus": "◆ 本期焦点 · CEO 指定：{label}",
        "h_bluf": "二、核心要点（BLUF）", "no_bluf": "隔夜无重大材料信号。",
        "h_watch_sec": "三、中国财经 / 关注池",
        "hits_lbl": "关注池异动：",
        "th": ["板块", "标的", "事件/数据", "四分卡 C·M·Rel·When", "来源"],
        "score_legend": "四分卡：C=来源可信度1–5 · M=重要性1–5 · Rel=相关度(Dir直接/Sec板块) · When=时效(Td今日/Wk本周)。均为客观事实，非方向判断。",
        "cnnews_lbl": "中国财经要闻：", "none": "无。",
        "h_trend": "四、趋势信号（资金面 / 政策 / 预期修正 / 异动 / 公告）",
        "no_trend": "无显著趋势信号。",
        "h_ahead": "五、今日待观察", "no_ahead": "无临近公告/数据披露提示。",
        "h_dec": "六、待 CEO 决断事项（只列事实 + 来源，无方向判断）",
        "h_world": "七、全球投资相关要闻（须有市场传导路径）",
        "no_world": "采集降级，暂无全球投资相关要闻。",
        "lint_ok": "✓ 方向性泄漏审计：通过（CIO 自撰文本无买卖/方向词）",
        "lint_flag": "⚠ 方向性泄漏审计：{n} 处待 CEO 复核 → {items}",
        "fact_ok": "✓ 数字核验：通过（摘要未见原文之外的数字/年份）",
        "fact_flag": "⚠ 数字核验：{n} 条摘要含原文之外的数字（请点原文复核）→ {items}",
        "footer": ("CIO 自动编撰：行情数字取自数据锚定的参考行情源（yfinance/akshare，为一致的操作口径，"
                   "非交易所权威成交价）；新闻摘要为本地模型转述、关键决策请点原文核验；不含买卖或方向性判断。"),
    },
    "en": {
        "title": "CIO Premarket Brief",
        "stamp": "{ny} ET",
        "status": "Data status: fetched {f} / deduped {d} / vectors {v}; degraded: {deg}",
        "none_deg": "none",
        "h_anchor": "I. Data Anchor (reference market data · single quote source)",
        "h_anom": "◆ Market Anomalies (factual · no interpretation)",
        "fund": "Fund flows", "anchor_deg": "Market-data collection degraded.",
        "focus": "◆ Focus · CEO-set: {label}",
        "h_bluf": "II. Key Points (BLUF)", "no_bluf": "No material overnight signals.",
        "h_watch_sec": "III. Watchlist & Movers",
        "hits_lbl": "Watchlist movers:",
        "th": ["Sector", "Name", "Event / Data", "C·M·Rel·When", "Source"],
        "score_legend": "Four-score card: C=source confidence 1–5 · M=materiality 1–5 · Rel=relevance (Dir/Sec) · When=immediacy (Td/Wk/Med/Bg). All factual, not directional.",
        "cnnews_lbl": "Other market news:", "none": "None.",
        "h_trend": "IV. Trend Signals (Flows / Policy / Revisions / Anomaly / Filings)",
        "no_trend": "No significant trend signals.",
        "h_ahead": "V. Watch Today", "no_ahead": "No upcoming filings / data releases flagged.",
        "h_dec": "VI. For CEO Decision (facts + sources only; no directional call)",
        "h_world": "VII. Global Investment-Relevant Events",
        "no_world": "Collection degraded; no global investment-relevant events.",
        "lint_ok": "✓ Leakage check: clean (no directional wording in CIO-authored text)",
        "lint_flag": "⚠ Leakage check: {n} flagged for CEO review → {items}",
        "fact_ok": "✓ Fact check: clean (no figures beyond sources)",
        "fact_flag": "⚠ Fact check: {n} summaries add figures not in source (verify) → {items}",
        "footer": ("CIO auto-compiled: quotes come from the reference market-data anchor (yfinance — a consistent "
                   "operational source, not authoritative exchange pricing); news summaries are local-model "
                   "paraphrases — click through for anything decision-critical; no buy/sell or directional calls."),
    },
}

# 关键数据加粗：百分比 / 金额（亿·万亿·元·股）/ 倍数 / 点位 —— 让 CEO 一眼扫到重点数字。
# 长单位排在前面，保证贪婪匹配正确（万亿元 先于 万亿、亿股 先于 亿）。
_EMPH = re.compile(r"(\d[\d,]*\.?\d*\s?(?:%|％|个百分点|万亿元|万亿|亿元|亿股|亿|万|倍|元|股|点|BP|bp))")


def _emph_pdf(s: str) -> str:
    """PDF：给关键数字包 <b>（应在 _esc 之后调用，标签本身不被转义）。"""
    return _EMPH.sub(r"<b>\1</b>", s or "")


def _emph_md(s: str) -> str:
    """Markdown：给关键数字包 **粗体**。"""
    return _EMPH.sub(r"**\1**", s or "")

_CJK_FONT = "STSong-Light"   # 运行时若内嵌 TTF 成功则改为 NotoSC
_font_ready = False


def _ensure_font():
    """优先内嵌 Noto Sans SC（真字形、任何阅读器都清晰、字宽正确）；缺字体文件时回退 CID。"""
    global _CJK_FONT, _font_ready
    if _font_ready:
        return
    from reportlab.pdfbase import pdfmetrics
    fdir = BASE / "assets" / "fonts"
    reg = fdir / "NotoSansSC-Regular.ttf"
    bold = fdir / "NotoSansSC-Bold.ttf"
    if reg.exists():
        try:
            from reportlab.pdfbase.ttfonts import TTFont
            pdfmetrics.registerFont(TTFont("NotoSC", str(reg)))
            bold_name = "NotoSC"
            if bold.exists():
                try:
                    pdfmetrics.registerFont(TTFont("NotoSC-Bold", str(bold)))
                    bold_name = "NotoSC-Bold"
                except Exception as e:
                    log.warning("内嵌 Bold 字体失败(%s)，粗体暂回退常规", type(e).__name__)
            # 注册字体家族：<b> → 真粗体字重（缺 bold 文件时退回常规，至少不报错、不缺字）
            pdfmetrics.registerFontFamily("NotoSC", normal="NotoSC", bold=bold_name,
                                          italic="NotoSC", boldItalic=bold_name)
            _CJK_FONT = "NotoSC"
            _font_ready = True
            return
        except Exception as e:
            log.warning("内嵌 TTF 失败(%s)，回退 CID 字体", type(e).__name__)
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    _CJK_FONT = "STSong-Light"
    _font_ready = True


_SIGNAL_EN = {"强": "strong", "中": "medium", "弱": "weak"}
_TAG_EN = {"关注池": "Watchlist"}   # 趋势标签在美股模式下本就是英文（取自 watchlist_us 的 lens 键）


def _sig(n: NewsItem) -> str:
    return _SIGNAL_EN.get(n.signal, n.signal) if _rlang() == "en" else n.signal


_REL_ABBR = {"Direct": "Dir", "Sector": "Sec", "None": "—", "": "—"}
_WHEN_ABBR = {"Today": "Td", "This week": "Wk", "Medium-term": "Med", "Background": "Bg", "": "—"}


def _score_cell(h) -> str:
    """§6 四分卡紧凑单元：C{可信度} M{重要性} {相关度} {时效}。"""
    c = getattr(h, "confidence", 0) or "?"
    m = getattr(h, "materiality", 0) or "?"
    rel = _REL_ABBR.get(getattr(h, "relevance", "") or "", getattr(h, "relevance", "") or "—")
    when = _WHEN_ABBR.get(getattr(h, "immediacy", "") or "", getattr(h, "immediacy", "") or "—")
    return f"C{c} M{m} {rel} {when}"


def _prov_sources(sources, linkfn) -> str:
    """provenance：来源按可信档位排序；有主源(≥5)则标 'primary:'，其余为发现/佐证源 'via'。"""
    from .scoring import source_tier
    en = _rlang() == "en"
    ranked = sorted([s for s in (sources or []) if getattr(s, "url", "") or getattr(s, "name", "")],
                    key=source_tier, reverse=True)
    if not ranked:
        return "—"
    prim = [s for s in ranked if source_tier(s) >= 5]
    rest = [s for s in ranked if source_tier(s) < 5][:4]
    if prim:
        out = ("primary: " if en else "主源: ") + " · ".join(linkfn(s) for s in prim)
        if rest:
            out += (" · via " if en else " · 另: ") + " · ".join(linkfn(s) for s in rest)
        return out
    return " · ".join(linkfn(s) for s in ranked[:5])


# 聚合器域名：Google News / Bing News / Yahoo RSS 等是"包装"链接，能点开但不是原始出版商。
_AGG_HOSTS = ("news.google.", "google.com/rss", "bing.com/news", "finance.yahoo.com/rss", "news.yahoo.com/rss")


def _is_aggregator(s) -> bool:
    u = (getattr(s, "url", "") or "").lower()
    n = (getattr(s, "name", "") or "").lower()
    return any(a in u for a in _AGG_HOSTS) or n.startswith("gnews") or n.startswith("googlenews") or "google news" in n


def best_source(sources):
    """挑【唯一一条最值得点开】的来源：源可信档位最高；同档优先直连出版商（非聚合器）、再优先有链接、有真实名字。
    这样多渠道条目只给 CEO 一个原文链接，方便快速点开核对（对齐 CEO 反馈：贴一个就够）。"""
    from .scoring import source_tier
    cand = [s for s in (sources or []) if getattr(s, "url", "") or getattr(s, "name", "")]
    if not cand:
        return None
    return sorted(cand, key=lambda s: (source_tier(s), not _is_aggregator(s),
                                       bool(getattr(s, "url", "")), bool(getattr(s, "name", ""))),
                  reverse=True)[0]


def _pub_from_title(title: str) -> str:
    """从 Google News 式标题尾部抽出真实出版商：'… - Reuters' / '… | Forbes' → 'Reuters'/'Forbes'。
    严格约束（≤4 词、词首大写/全大写的品牌样）避免把 '… - here's why' 误当成出版商；抽不到返回空。"""
    m = re.search(r"\s[-–—|]\s([^-–—|]{2,28})\s*$", title or "")
    if not m:
        return ""
    cand = m.group(1).strip().rstrip(".")
    words = [w for w in cand.split() if w]
    if not words or len(words) > 4:
        return ""
    if not all(w[:1].isupper() or w.isupper() for w in words):
        return ""
    return re.sub(r"\.(com|net|org|io|co\.uk)$", "", cand, flags=re.I)


def source_label(s, title: str, default: str) -> str:
    """展示名：聚合器(GNews 等)且标题带出版商后缀 → 用真实出版商；否则用来源名。链接 URL 不变（仍可点开原文）。"""
    if s is not None and _is_aggregator(s):
        pub = _pub_from_title(title or "")
        if pub:
            return pub
    return (getattr(s, "name", "") or default) if s is not None else default


def _one_link(sources, linkfn, *, member_count: int = 1) -> str:
    """展示单一最佳来源链接；多渠道时前面标 'N sources ·' 保留佐证信号，但只给一个可点链接。"""
    en = _rlang() == "en"
    s = best_source(sources)
    link = linkfn(s) if s else "—"
    nsrc = max(len([x for x in (sources or []) if getattr(x, "url", "") or getattr(x, "name", "")]), member_count)
    if nsrc > 1:
        return (f"{nsrc} sources · " if en else f"{nsrc} 源 · ") + link
    return link


def _stale_marker(published_at, *, max_days: int = 7) -> str:
    """事件时效标注：源发布超过阈值（CIO_STALE_DAYS，默认 7 天）→ 'stale Nd' / '旧闻 N天'；否则空。"""
    if not published_at:
        return ""
    import os as _os
    from datetime import datetime as _dt, timezone as _tz
    try:
        md = int(_os.environ.get("CIO_STALE_DAYS", str(max_days)))
        now = _dt.now(_tz.utc)
        dt = published_at if getattr(published_at, "tzinfo", None) else published_at.replace(tzinfo=_tz.utc)
        age = (now - dt).days
    except Exception:
        return ""
    if age > md:
        return (f"stale {age}d" if _rlang() == "en" else f"旧闻 {age}天")
    return ""


def _event_card_md(e) -> str:
    """§4 事件卡（Markdown）：标签 + 标题(+标的) + 四分卡 + 一句摘要 + provenance 来源。"""
    en = _rlang() == "en"
    dflt = "source" if en else "来源"
    sec = f"[{e.sector}] " if e.sector else ""
    tk = f" ({', '.join(e.tickers)})" if e.tickers else ""
    out = [f"**▸ {sec}{_emph_md(e.headline)}**{tk}　`{_score_cell(e)}`"]
    if e.summary and e.summary.strip() and e.summary.strip() != (e.headline or "").strip():
        out.append(f"   {_emph_md(e.summary)}")
    link = _one_link(e.sources,
                     lambda s: f"[{source_label(s, e.headline, dflt)}]({s.url})" if s.url else source_label(s, e.headline, dflt),
                     member_count=e.member_count)
    sm = _stale_marker(e.published_at)
    tail = f" · ⚠ {sm}" if sm else ""
    out.append(f"   {e.event_id}{tail} · {link}")
    return "\n".join(out)


def _event_card_flow(e, S):
    """§4 事件卡（PDF flowables）。"""
    from reportlab.platypus import Paragraph
    en = _rlang() == "en"
    dflt = "source" if en else "来源"
    sec = f"[{_esc(e.sector)}] " if e.sector else ""
    tk = f" ({_esc(', '.join(e.tickers))})" if e.tickers else ""
    out = [Paragraph(f"<b>▸ {sec}{_emph_pdf(_esc(e.headline))}</b>{tk}　({_esc(_score_cell(e))})", S["p"])]
    if e.summary and e.summary.strip() and e.summary.strip() != (e.headline or "").strip():
        out.append(Paragraph(_emph_pdf(_esc(e.summary)), S["p"]))
    link = _one_link(e.sources,
                     lambda s: (f'<a href="{_esc(s.url)}">{_esc(source_label(s, e.headline, dflt))}</a>'
                                if s.url else _esc(source_label(s, e.headline, dflt))),
                     member_count=e.member_count)
    sm = _stale_marker(e.published_at)
    tail = f" · ⚠ {_esc(sm)}" if sm else ""
    out.append(Paragraph(f"{_esc(e.event_id)}{tail} · {link}", S["small"]))
    return out


def _tag(n: NewsItem) -> str:
    t = n.primary_tag or (n.trend_tags[0] if n.trend_tags else "")
    if _rlang() == "en":
        t = _TAG_EN.get(t, t)
    return f" #{t}" if t else ""


def _src_md(n: NewsItem) -> str:
    en = _rlang() == "en"
    dft, nolink = ("source", "(no link)") if en else ("来源", "（无链接）")
    s = best_source(n.sources)   # 只给一条最佳原文链接（多渠道时优先直连出版商）
    if not s:
        return nolink
    lbl = source_label(s, n.title_en or n.title_original or n.title_zh, dft)
    return f"[{lbl}]({s.url})" if s.url else lbl


# ============================ Markdown ============================

def _news_detail_md(n: NewsItem, idx: int | None = None) -> str:
    """趋势信号用：标题 + 信号 + 单主标签 + 一句话 + 来源。"""
    head = f"{idx}. " if idx else "- "
    body = f"\n   {_emph_md(n.summary_zh)}" if n.summary_zh else ""
    if _rlang() == "en":
        title = f"**{n.title_en or n.title_original or n.title_zh}**"
        return f"{head}{title} (signal: {_sig(n)}{_tag(n)}){body}\n   source: {_src_md(n)}"
    zh = n.title_zh or n.title_original
    en = n.title_en or (n.title_original if n.title_original != zh else "")
    title = f"**{zh}**" + (f" / {en}" if en else "")
    return f"{head}{title}（信号：{n.signal}{_tag(n)}）{body}\n   来源：{_src_md(n)}"


def _news_oneline_md(n: NewsItem, idx: int) -> str:
    """要闻数字化：尽量一行 —— 标题 / EN — 一句话（信号·标签）来源。"""
    bi = f" — {_emph_md(n.summary_zh)}" if n.summary_zh else ""
    if _rlang() == "en":
        title = n.title_en or n.title_original or n.title_zh
        return f"{idx}. **{title}**{bi} ({_sig(n)}{_tag(n)}) source: {_src_md(n)}"
    zh = n.title_zh or n.title_original
    en = n.title_en or (n.title_original if n.title_original != zh else "")
    entitle = f" / {truncate(en, 60)}" if en else ""
    return f"{idx}. **{zh}**{entitle}{bi}（{n.signal}{_tag(n)}）来源：{_src_md(n)}"


def _idx_str(q: IndexQuote) -> str:
    import math
    en = _rlang() == "en"
    lp, rp = ("(", ")") if en else ("（", "）")
    bad = q.last is None or (isinstance(q.last, float) and math.isnan(q.last))
    if bad:
        na = "—(n/a today)" if en else "—（今日未取到）"
        note = q.note
        if en and note and re.search(r"[一-鿿]", note):   # §5 兜底：en 模式不显示中文降级串
            note = None
        return f"{q.name} {note or na}"    # 绝不显示 nan
    pct_bad = q.change_pct is None or (isinstance(q.change_pct, float) and math.isnan(q.change_pct))
    pct = "—" if pct_bad else f"{q.change_pct:+.2f}%"
    return f"{q.name} {q.last:,.2f}{lp}{pct}{rp}"


# ---------------------------------------------------------------- 盘前市场快照
# **三个渲染器（md / reportlab PDF / HTML PDF）必须同时更新。**
# build62 的教训：MD 改了、PDF 渲染器没跟上，两份报告在同一天给出不同内容，
# 而且都不报错。共用同一个取行函数就是为了让"忘了改另一个"变得不可能。
def _tick_line(t) -> str:
    """一行市场快照。**值、涨跌、以及这个数字自己的时间，缺一不可。**"""
    if t.last is None:
        return f"{t.name}（{t.symbol}）：{t.note or '未取到'}"
    pct = "—" if t.change_pct is None else f"{t.change_pct:+.2f}%"
    age = f"　{t.age_label}" if t.age_label else ""
    warn = "　⚠" if t.stale else ""
    return f"{t.name}　{t.last:,.2f}　{pct}　[{t.as_of}]{age}{warn}"


def _tick_groups(ticks) -> dict:
    g: dict = {}
    for t in ticks or []:
        g.setdefault(t.group or "市场", []).append(t)
    return g


# §6 四分卡图例。**印在报告里，不是写进说明文档。**
# 一份需要口头解释才能读懂的报告，等于把解释义务转嫁给作者——
# 而作者不在场的时候（自动推送的每一天），这些标记就只是噪声。
SCORECARD_LEGEND_ZH = (
    "四分卡图例　C1–5 来源可信度（5=一手来源）｜M1–5 事件重要性｜"
    "Dir 直接命中关注池标的 · Sec 命中所属行业 · — 无关｜"
    "Td 今日 · Wk 本周 · Med 中期 · Bg 背景")
SCORECARD_LEGEND_EN = (
    "Scorecard legend　C1–5 source confidence (5=primary)｜M1–5 materiality｜"
    "Dir direct watchlist hit · Sec sector hit · — none｜"
    "Td today · Wk this week · Med medium-term · Bg background")


def scorecard_legend(en: bool = False) -> str:
    return SCORECARD_LEGEND_EN if en else SCORECARD_LEGEND_ZH


def _anchor_groups(anchor: list[IndexQuote]) -> "dict":
    """按 group 归类数据锚定指数，保序。"""
    from collections import OrderedDict
    groups: "OrderedDict[str, list]" = OrderedDict()
    for q in anchor:
        groups.setdefault(q.group or "指数", []).append(q)
    return groups


def render_brief_md(b: Brief) -> str:
    en = _rlang() == "en"
    T = _BRIEF_L[_rlang()]
    L: list[str] = []
    title = T["title"] if en else b.title
    L.append(f"# {title} — {T['stamp'].format(bj=b.dt_beijing, ny=b.dt_ny)}")
    st = b.status
    deg = ("；".join(st.degraded) if not en else "; ".join(st.degraded)) if st.degraded else T["none_deg"]
    L.append("\n> " + T["status"].format(f=st.fetched, d=st.deduped, v=st.ingested_vectors, deg=deg))
    if b.cluster_stat:
        L.append("> " + b.cluster_stat)

    if b.market_snapshot:
        L.append("\n## 〇、盘前市场快照（此刻 vs 已收盘）"
                 if not en else "\n## 0. Pre-market Snapshot (live vs closed)")
        if b.market_note:
            L.append(f"> {b.market_note}")
        for grp, ts in _tick_groups(b.market_snapshot).items():
            L.append(f"\n**{grp}**")
            for t in ts:
                L.append(f"- {_tick_line(t)}")

    L.append(f"\n## {T['h_anchor']}")
    sep = "; " if en else "；"
    colon = ": " if en else "："
    for grp, qs in _anchor_groups(b.anchor).items():
        L.append(f"- **{grp}**{colon}" + sep.join(_idx_str(q) for q in qs))
    for f in b.fund_flows:
        L.append(f"- **{T['fund']}**：{f.name} {f.value}（{f.source}）")
    if not b.anchor and not b.fund_flows:
        L.append(f"- {T['anchor_deg']}")

    if b.anomalies:
        L.append(f"\n## {T['h_anom']}")
        for a in b.anomalies:
            L.append(f"- {_emph_md(a)}")

    if b.focus_items:
        L.append(f"\n## {T['focus'].format(label=b.focus_label)}")
        for i, n in enumerate(b.focus_items, 1):
            L.append(_news_oneline_md(n, i))

    L.append(f"\n## {T['h_bluf']}")
    if b.bluf:
        for i, s in enumerate(b.bluf, 1):
            L.append(f"{i}. {_emph_md(s)}\n")
    else:
        L.append(f"- {T['no_bluf']}")

    L.append(f"\n## {T['h_watch_sec']}")
    if b.watchlist_events:
        for e in b.watchlist_events:
            L.append("\n" + _event_card_md(e))
        L.append(f"\n*{T['score_legend']}*")
    elif b.watchlist_hits:
        L.append(f"\n**{T['hits_lbl']}**")
        L.append("\n| " + " | ".join(T["th"]) + " |")
        L.append("| --- | --- | --- | --- | --- |")
        for h in b.watchlist_hits:
            src = h.source.url or h.source.name or "—"
            L.append(f"| {h.sector} | {h.target} | {truncate((h.fact or '').replace(chr(10),' '), 68)} | {_score_cell(h)} | {src} |")
        L.append(f"\n*{T['score_legend']}*")
    if b.top_news_china:
        L.append(f"\n**{T['cnnews_lbl']}**")
        for i, n in enumerate(b.top_news_china, 1):
            L.append(_news_oneline_md(n, i))
    if not b.watchlist_events and not b.watchlist_hits and not b.top_news_china:
        L.append(f"- {T['none']}")

    L.append(f"\n## {T['h_trend']}")
    if b.trend_signals:
        for i, n in enumerate(b.trend_signals, 1):
            L.append(_news_detail_md(n, i))
    else:
        L.append(f"- {T['no_trend']}")

    L.append(f"\n## {T['h_ahead']}")
    for w in (b.watch_ahead or [T["no_ahead"]]):
        L.append(f"- {w}")

    L.append(f"\n## {T['h_dec']}")
    for d in (b.decisions or [T["none"]]):
        L.append(f"- {d}")

    L.append(f"\n## {T['h_world']}")
    if b.world_top:
        for i, n in enumerate(b.world_top, 1):
            L.append(_news_oneline_md(n, i))
    else:
        L.append(f"- {T['no_world']}")

    isep = ", " if en else "、"
    if b.leakage_flags:
        L.append(f"\n> {T['lint_flag'].format(n=len(b.leakage_flags), items=isep.join(b.leakage_flags))}")
    else:
        L.append(f"\n> {T['lint_ok']}")
    if b.fact_flags:
        L.append(f"> {T['fact_flag'].format(n=len(b.fact_flags), items=isep.join(b.fact_flags))}")
    else:
        L.append(f"> {T['fact_ok']}")
    L.append(f"\n---\n*{T['footer']}*")
    return "\n".join(L)


def render_report_md(r: TopicReport) -> str:
    L: list[str] = []
    L.append(f"# {r.title or ('《' + r.resolved + ' 专题情况报告》')} — {r.dt_beijing}（北京）")
    L.append(f"\n> 类型：{'个股' if r.subject_type=='stock' else '主题'} ｜ 命中历史资产 {r.archived_from} 条 ｜ 指令：{r.subject}")
    L.append("\n## 核心摘要\n" + (r.summary or "—"))
    if r.quote_facts:
        L.append("\n## 行情事实")
        for q in r.quote_facts:
            L.append(f"- {q}")
    if r.fund_facts:
        L.append("\n## 资金面")
        for f in r.fund_facts:
            L.append(f"- {f.name}：{f.value}（来源：{f.source}）")
    for title, arr in [("关键消息（中英对照）", r.key_news), ("研报与一致预期变动", r.estimate_revisions),
                       ("公告追踪", r.filings), ("政策 / 监管相关", r.policy)]:
        if arr:
            L.append(f"\n## {title}")
            for n in arr:
                L.append(_news_detail_md(n))
    L.append("\n## 待 CEO 决断事项（只列事实 + 来源）")
    for d in (r.decisions or ["- 无。"]):
        L.append(d if d.startswith("-") else f"- {d}")
    L.append("\n---\n*CIO 基于公司资料库 + 增量采集编撰，中英对照、只报事实、不做方向判断。*")
    return "\n".join(L)


# ============================ PDF (reportlab) ============================

def _styles():
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    ss = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=ss["Title"], fontName=_CJK_FONT, fontSize=15, leading=20,
                                wordWrap="CJK"),
        "h": ParagraphStyle("h", parent=ss["Heading2"], fontName=_CJK_FONT, fontSize=12,
                             leading=17, textColor=colors.HexColor("#1F3A5F"), spaceBefore=9, spaceAfter=3,
                             wordWrap="CJK"),
        "p": ParagraphStyle("p", parent=ss["Normal"], fontName=_CJK_FONT, fontSize=9.5, leading=14.5,
                            wordWrap="CJK", spaceAfter=1.5),
        "bluf": ParagraphStyle("b", parent=ss["Normal"], fontName=_CJK_FONT, fontSize=10.5, leading=16,
                               wordWrap="CJK", spaceAfter=2),
        "small": ParagraphStyle("s", parent=ss["Normal"], fontName=_CJK_FONT, fontSize=8, leading=11.5,
                                textColor=colors.grey, wordWrap="CJK"),
    }


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _src_pdf(n: NewsItem) -> str:
    dft, nolink = ("source", "(no link)") if _rlang() == "en" else ("来源", "（无链接）")
    s = best_source(n.sources)   # 只给一条最佳原文链接
    if not s:
        return nolink
    lbl = source_label(s, n.title_en or n.title_original or n.title_zh, dft)
    return f'<a href="{_esc(s.url)}">{_esc(lbl)}</a>' if s.url else _esc(lbl)


def _news_detail_flow(n: NewsItem, S, idx=None):
    from reportlab.platypus import Paragraph
    head = f"{idx}. " if idx else "• "
    tag = _esc(_tag(n))
    if _rlang() == "en":
        t = _esc(n.title_en or n.title_original or n.title_zh)
        title = f'<b>{head}{t}</b>　(signal:{_sig(n)}{tag})'
        src_lbl = "source: "
    else:
        zh = _esc(n.title_zh or n.title_original)
        en = _esc(n.title_en or "")
        title = f'<b>{head}{zh}</b>' + (f' / {en}' if en else '') + f'　(信号:{n.signal}{tag})'
        src_lbl = "来源："
    out = [Paragraph(title, S["p"])]
    if n.summary_zh:
        out.append(Paragraph(_emph_pdf(_esc(n.summary_zh)), S["p"]))
    out.append(Paragraph(src_lbl + _src_pdf(n), S["small"]))
    return out


def _news_oneline_flow(n: NewsItem, S, idx):
    from reportlab.platypus import Paragraph
    bi = f' — {_emph_pdf(_esc(n.summary_zh))}' if n.summary_zh else ''
    if _rlang() == "en":
        t = _esc(n.title_en or n.title_original or n.title_zh)
        line = f'<b>{idx}. {t}</b>{bi}　({_sig(n)}{_esc(_tag(n))})　source: {_src_pdf(n)}'
    else:
        zh = _esc(n.title_zh or n.title_original)
        line = f'<b>{idx}. {zh}</b>{bi}　({n.signal}{_esc(_tag(n))})　来源：{_src_pdf(n)}'
    return [Paragraph(line, S["p"])]


def _build_pdf(path: str, title: str, story_fn):
    _ensure_font()
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate
    from reportlab.lib.units import cm
    doc = SimpleDocTemplate(str(path), pagesize=A4, topMargin=1.4 * cm, bottomMargin=1.4 * cm,
                            leftMargin=1.6 * cm, rightMargin=1.6 * cm, title=title)
    doc.build(story_fn(_styles()))


def render_brief_pdf(b: Brief, path: str):
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle
    from reportlab.lib import colors

    en = _rlang() == "en"
    T = _BRIEF_L[_rlang()]
    sep = "; " if en else "；"

    def story(S):
        title = T["title"] if en else b.title
        el = [Paragraph(_esc(f"{title} — " + T["stamp"].format(bj=b.dt_beijing, ny=b.dt_ny)), S["title"])]
        st = b.status
        deg = (sep.join(st.degraded)) if st.degraded else T["none_deg"]
        el.append(Paragraph(_esc(T["status"].format(f=st.fetched, d=st.deduped, v=st.ingested_vectors, deg=deg)), S["small"]))
        if b.cluster_stat:
            el.append(Paragraph(_esc(b.cluster_stat), S["small"]))
        el.append(Spacer(1, 5))

        if b.market_snapshot:
            el.append(Paragraph(_esc("〇、盘前市场快照（此刻 vs 已收盘）" if not en
                                     else "0. Pre-market Snapshot (live vs closed)"), S["h"]))
            if b.market_note:
                el.append(Paragraph(_emph_pdf(_esc(b.market_note)), S["small"]))
            for _grp, _ts in _tick_groups(b.market_snapshot).items():
                el.append(Paragraph(f"<b>{_esc(_grp)}</b>", S["p"]))
                for _t in _ts:
                    el.append(Paragraph(_esc(_tick_line(_t)), S["p"]))

        el.append(Paragraph(_esc(T["h_anchor"]), S["h"]))
        _c = ": " if en else "： "
        for grp, qs in _anchor_groups(b.anchor).items():
            el.append(Paragraph(_esc(f"{grp}{_c}" + sep.join(_idx_str(q) for q in qs)), S["p"]))
        for f in b.fund_flows:
            el.append(Paragraph(_esc(f"{T['fund']}： {f.name} {f.value}（{f.source}）"), S["p"]))
        if not b.anchor and not b.fund_flows:
            el.append(Paragraph(_esc(T["anchor_deg"]), S["p"]))

        if b.anomalies:
            el.append(Paragraph(_esc(T["h_anom"]), S["h"]))
            for a in b.anomalies:
                el.append(Paragraph(_emph_pdf(_esc(a)), S["p"]))

        if b.focus_items:
            el.append(Paragraph(_esc(T["focus"].format(label=b.focus_label)), S["h"]))
            for i, n in enumerate(b.focus_items, 1):
                el += _news_oneline_flow(n, S, i)
                el.append(Spacer(1, 7))

        el.append(Paragraph(_esc(T["h_bluf"]), S["h"]))
        if b.bluf:
            for i, s in enumerate(b.bluf, 1):
                el.append(Paragraph(f"<b>{i}.</b> {_emph_pdf(_esc(s))}", S["bluf"]))
                el.append(Spacer(1, 6))
        else:
            el.append(Paragraph(_esc(T["no_bluf"]), S["p"]))

        el.append(Paragraph(_esc(T["h_watch_sec"]), S["h"]))
        if b.watchlist_events:
            for e in b.watchlist_events:
                el += _event_card_flow(e, S)
                el.append(Spacer(1, 6))
            el.append(Paragraph(_esc(T["score_legend"]), S["small"]))
        elif b.watchlist_hits:
            el.append(Paragraph(f"<b>{_esc(T['hits_lbl'].rstrip('：:'))}</b>", S["p"]))
            data = [T["th"][:4]]
            for h in b.watchlist_hits:
                data.append([h.sector, (h.target or "—")[:10], (h.fact or "")[:40], _score_cell(h)])
            t = Table(data, colWidths=[66, 60, 246, 82])
            t.setStyle(TableStyle([
                ("FONTNAME", (0, 0), (-1, -1), _CJK_FONT), ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F3A5F")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
            el.append(t)
            el.append(Paragraph(_esc(T["score_legend"]), S["small"]))
        if b.top_news_china:
            el.append(Paragraph(f"<b>{_esc(T['cnnews_lbl'].rstrip('：:'))}</b>", S["p"]))
            for i, n in enumerate(b.top_news_china, 1):
                el += _news_oneline_flow(n, S, i)
                el.append(Spacer(1, 7))
        if not b.watchlist_events and not b.watchlist_hits and not b.top_news_china:
            el.append(Paragraph(_esc(T["none"]), S["p"]))

        el.append(Paragraph(_esc(T["h_trend"]), S["h"]))
        for i, n in enumerate(b.trend_signals, 1):
            el += _news_detail_flow(n, S, i)
            el.append(Spacer(1, 7))
        if not b.trend_signals:
            el.append(Paragraph(_esc(T["no_trend"]), S["p"]))

        el.append(Paragraph(_esc(T["h_ahead"]), S["h"]))
        for w in (b.watch_ahead or [T["no_ahead"]]):
            el.append(Paragraph(_esc("• " + w), S["p"]))

        el.append(Paragraph(_esc(T["h_dec"]), S["h"]))
        for d in (b.decisions or [T["none"]]):
            el.append(Paragraph(_esc("• " + d), S["p"]))

        el.append(Paragraph(_esc(T["h_world"]), S["h"]))
        if b.world_top:
            for i, n in enumerate(b.world_top, 1):
                el += _news_oneline_flow(n, S, i)
                el.append(Spacer(1, 7))
        else:
            el.append(Paragraph(_esc(T["no_world"]), S["p"]))
        el.append(Spacer(1, 6))
        if b.leakage_flags:
            isep = ", " if en else "、"
            el.append(Paragraph(_esc(T["lint_flag"].format(n=len(b.leakage_flags), items=isep.join(b.leakage_flags))), S["small"]))
        else:
            el.append(Paragraph(_esc(T["lint_ok"]), S["small"]))
        _isep = ", " if en else "、"
        if b.fact_flags:
            el.append(Paragraph(_esc(T["fact_flag"].format(n=len(b.fact_flags), items=_isep.join(b.fact_flags))), S["small"]))
        else:
            el.append(Paragraph(_esc(T["fact_ok"]), S["small"]))
        el.append(Paragraph(_esc(T["footer"]), S["small"]))
        return el

    _build_pdf(path, T["title"] if en else b.title, story)


def render_report_pdf(r: TopicReport, path: str):
    from reportlab.platypus import Paragraph, Spacer

    def story(S):
        el = [Paragraph(_esc(r.title or f"《{r.resolved} 专题情况报告》"), S["title"])]
        el.append(Paragraph(_esc(f"{r.dt_beijing}（北京）｜类型:{'个股' if r.subject_type=='stock' else '主题'}"
                                 f"｜命中历史资产 {r.archived_from} 条"), S["small"]))
        el.append(Spacer(1, 5))
        el.append(Paragraph("核心摘要", S["h"]))
        el.append(Paragraph(_esc(r.summary or "—"), S["p"]))
        if r.quote_facts:
            el.append(Paragraph("行情事实", S["h"]))
            for q in r.quote_facts:
                el.append(Paragraph(_esc("• " + q), S["p"]))
        if r.fund_facts:
            el.append(Paragraph("资金面", S["h"]))
            for f in r.fund_facts:
                el.append(Paragraph(_esc(f"• {f.name}：{f.value}（{f.source}）"), S["p"]))
        for title, arr in [("关键消息（中英对照）", r.key_news), ("研报与一致预期变动", r.estimate_revisions),
                           ("公告追踪", r.filings), ("政策 / 监管相关", r.policy)]:
            if arr:
                el.append(Paragraph(title, S["h"]))
                for n in arr:
                    el += _news_detail_flow(n, S)
        el.append(Paragraph("待 CEO 决断事项（只列事实 + 来源）", S["h"]))
        for d in (r.decisions or ["无。"]):
            el.append(Paragraph(_esc("• " + d.lstrip("- ")), S["p"]))
        el.append(Spacer(1, 6))
        el.append(Paragraph("CIO 编撰，中英对照、只报事实、不做方向判断。", S["small"]))
        return el

    _build_pdf(path, r.title or r.resolved, story)


# ============================ 个股情报档案（资料库驱动）============================

def render_dossier_md(r: DossierReport) -> str:
    T = _DOSSIER_L[_rlang()]
    L: list[str] = []
    L.append(f"# {r.title} — {T['stamp'].format(ny=r.dt_ny)}")
    L.append("\n> " + T["meta"].format(ad=r.archive_docs, fd=r.fresh_docs, subj=r.subject))

    L.append(f"\n## {T['h_anchor']}")
    for q in (r.quote_facts or [T["no_anchor"]]):
        L.append(f"- {_emph_md(q.lstrip('- '))}")

    L.append(f"\n## {T['h_timeline']}")
    if r.timeline:
        for e in r.timeline:
            src = f"（[{e.source_name or T['src']}]({e.source_url})）" if e.source_url else ""
            L.append(f"- **{e.date or '—'}** — {e.title} {src}")
    else:
        L.append(f"- {T['no_timeline']}")

    L.append(f"\n## {T['h_recent']}")
    if r.recent:
        for i, n in enumerate(r.recent, 1):
            L.append(_news_oneline_md(n, i))
    else:
        L.append(f"- {T['no_recent']}")

    L.append(f"\n## {T['h_hits']}")
    for h in (r.past_hits or [T["no_hits"]]):
        L.append(f"- {_emph_md(h.lstrip('- '))}")

    if r.filings:
        L.append(f"\n## {T['h_filings']}")
        for i, n in enumerate(r.filings, 1):
            L.append(_news_oneline_md(n, i))

    if r.cross_check:
        L.append(f"\n## {T['h_cross']}")
        for c in r.cross_check:
            L.append(f"- {c}")

    L.append(f"\n## {T['h_complete']}")
    L.append(_emph_md(r.completeness or "—"))

    L.append(f"\n## {T['h_decisions']}")
    for d in (r.decisions or [T["none"]]):
        L.append(d if d.startswith("-") else f"- {d}")

    L.append(f"\n---\n*{T['footer']}*")
    return "\n".join(L)


def render_dossier_pdf(r: DossierReport, path: str):
    from reportlab.platypus import Paragraph, Spacer

    T = _DOSSIER_L[_rlang()]

    def story(S):
        el = [Paragraph(_esc(f"{r.title} — " + T["stamp"].format(ny=r.dt_ny)), S["title"])]
        el.append(Paragraph(_esc(T["meta"].format(ad=r.archive_docs, fd=r.fresh_docs, subj=r.subject)), S["small"]))
        el.append(Spacer(1, 5))

        el.append(Paragraph(_esc(T["h_anchor"]), S["h"]))
        for q in (r.quote_facts or [T["no_anchor"]]):
            el.append(Paragraph(_emph_pdf(_esc(q.lstrip("- "))), S["p"]))

        el.append(Paragraph(_esc(T["h_timeline"]), S["h"]))
        if r.timeline:
            for e in r.timeline:
                src = f'　<a href="{_esc(e.source_url)}">{_esc(e.source_name or T["src"])}</a>' if e.source_url else ""
                el.append(Paragraph(f"<b>{_esc(e.date or '—')}</b> — {_esc(e.title)}{src}", S["p"]))
                el.append(Spacer(1, 4))
        else:
            el.append(Paragraph(_esc(T["no_timeline"]), S["p"]))

        el.append(Paragraph(_esc(T["h_recent"]), S["h"]))
        if r.recent:
            for i, n in enumerate(r.recent, 1):
                el += _news_oneline_flow(n, S, i)
                el.append(Spacer(1, 7))
        else:
            el.append(Paragraph(_esc(T["no_recent"]), S["p"]))

        el.append(Paragraph(_esc(T["h_hits"]), S["h"]))
        for h in (r.past_hits or [T["no_hits"]]):
            el.append(Paragraph(_emph_pdf(_esc(h.lstrip("- "))), S["p"]))
            el.append(Spacer(1, 3))

        if r.filings:
            el.append(Paragraph(_esc(T["h_filings"]), S["h"]))
            for i, n in enumerate(r.filings, 1):
                el += _news_oneline_flow(n, S, i)
                el.append(Spacer(1, 7))

        if r.cross_check:
            el.append(Paragraph(_esc(T["h_cross"]), S["h"]))
            for c in r.cross_check:
                el.append(Paragraph(_esc(c), S["p"]))

        el.append(Paragraph(_esc(T["h_complete"]), S["h"]))
        el.append(Paragraph(_emph_pdf(_esc(r.completeness or "—")), S["p"]))

        el.append(Paragraph(_esc(T["h_decisions"]), S["h"]))
        for d in (r.decisions or [T["none"]]):
            el.append(Paragraph(_esc("• " + d.lstrip("- ")), S["p"]))

        el.append(Spacer(1, 6))
        el.append(Paragraph(_esc(T["footer"]), S["small"]))
        return el

    _build_pdf(path, r.title or r.resolved, story)


# ============================ 证券一部建议（LLM 多空辩论 + 回测）============================

def _para_lines(text: str, S, style="p"):
    """把一段多行文本按行拆成多个 Paragraph（关键数字加粗）。"""
    from reportlab.platypus import Paragraph
    out = []
    for ln in (text or "").split("\n"):
        ln = ln.strip()
        if ln:
            out.append(Paragraph(_emph_pdf(_esc(ln)), S[style]))
    return out or [Paragraph("（无）", S[style])]


def _md_not_activated(r: UnitAAdvice) -> str:
    """一部未启动时的报告。**刻意很短。**

    只有三样东西：正式弃权表述、确定性面板、材料质量摘要。
    没有多空论据——那正是要避免的：0 substantive 时的 Bull/Bear 就是
    拿二部已经算好的数字重新讲故事，与二部完全重叠，且每天措辞漂移是采样噪声。

    但既有论点要列出来：**未启动 ≠ 没有观点。** 既有观点仍然有效、仍在被复检。
    """
    from . import material_gate as MG
    L: list[str] = []
    L.append(f"# 《{r.resolved} 证券一部观点》 — {r.dt_beijing}（北京）/ {r.dt_ny}")
    L.append(f"\n> Evidence Gate = **{r.gate_level}**（{r.material_verdict}："
             f"{r.material_count} 条材料，实质 **{r.material_substantive}** 条）"
             f" ｜ 本地模型调用 **{r.llm_calls}** 次")

    L.append(f"\n## {MG.NOT_ACTIVATED_HEADLINE}")
    L.append(f"\n**{MG.FORMAL_VOTE_ABSTAIN}**")
    L.append(f"\n{MG.PANEL_FOLLOWS}")
    if r.material_banner:
        L.append("\n" + r.material_banner)
    L.append("\n> **没有新的可解释信息，就不制造新的观点。**"
             "本轮采集到的材料没有一条含增量事实，因此一部不启动多空辩论——"
             "在这种情况下辩论只会拿二部已经确定性算好的数字重新讲一遍故事，"
             "既没有新的 information set，也没有真正的因果推理；"
             "而同一批不变的数字每天重跑，方向与信心的来回摆动是**采样噪声，不是市场变化**。")
    L.append("\n> 三个部门的节奏本就不同：CIO 持续捕捉世界发生了什么（每天），"
             "二部测量市场当前状态（每天），**一部只在新证据到来时重新研究**。")

    if r.invalidation_hits:
        L.append("\n## ⚠ 历史论点失效提示")
        L.append("\n> 一部虽未启动，**既有论点的监控照常进行**。以下失效条件被今天的材料命中，"
                 "是提示不是判决——是否真的失效由 CEO 看材料决定。")
        for h in r.invalidation_hits:
            L.append(f"\n- **论点 #{h['thesis_id']}（{h['subject']} {h['direction']}）** "
                     f"失效条件：{h['condition']}")
            src = f"（[{h.get('source') or '来源'}]({h['url']})）" if h.get("url") else ""
            L.append(f"  - 触发材料：{h['fact']} {src}")

    L.append("\n## 一、仍在监控中的既有论点")
    _open = [t for t in (r.open_theses or []) if t.get("invalidations")]
    if _open:
        for t in _open:
            L.append(f"\n- **#{t['id']} {t['subject']} {t['direction']}｜{t['conviction']}**"
                     f"（{t['as_of']} 登记"
                     + (f"，当时材料判定：{t['material_verdict']}" if t.get("material_verdict") else "")
                     + f"，失效条件 {len(t['invalidations'])} 条）")
            for c in t["invalidations"]:
                L.append(f"  - {_emph_md(c)}")
        L.append("\n> **未启动不等于没有观点。** 这些论点仍然有效，其失效条件每天照常与新材料比对。"
                 "今天没有新证据，所以不重写一遍——重写只会制造一个看起来更新、实际没有更多依据的版本。")
    else:
        L.append("- （台账中暂无仍 OPEN 的论点）")
        L.append("\n> 既没有新证据、也没有既有论点——一部对该标的目前没有任何立场。")

    L.append("\n## 二、量化证据面板（固定口径 · 情境感知用）")
    L.append("\n> 面板照常产出，但它是**二部口径的测量**，不是一部的研究结论。"
             "标「无数据」的项目表示确实没有，不是 0。")
    L.append("\n```\n" + (r.panel_text or "（本轮无面板）") + "\n```")

    L.append("\n## 三、采集材料质量（为什么判为无实质）")
    L.append("\n> 判定规则写死可审计——判错了你当场就能看见，回来改规则。")
    for mi in (r.materials or []):
        src = f"（[{mi.source_name or '来源'}]({mi.source_url})）" if mi.source_url else ""
        lab = r.material_labels.get(mi.id) or r.material_labels.get(str(mi.id)) or ""
        tag = f" `{lab}`" if lab else ""
        L.append(f"- **[{mi.id}]**{tag} {mi.text} {src}")
    if not r.materials:
        L.append("- （本轮无采集材料）")

    L.append("\n---\n*证券一部：Fundamental & Event-Driven Adversarial Research —— "
             "**evidence-triggered**，不是每日评论台。"
             "要在无新证据时强制复研，用 `UNIT_A_FORCE_RESEARCH=1` 或 `--force`；"
             "那属于有意的人工决定（首次建仓、季度复审、论点到期），"
             "报告会明确标注它依据的是既有证据集。*")
    return "\n".join(L)


def render_unit_a_md(r: UnitAAdvice) -> str:
    if not r.activated:
        return _md_not_activated(r)
    L: list[str] = []
    L.append(f"# 《{r.resolved} 证券一部观点》 — {r.dt_beijing}（北京）/ {r.dt_ny}")
    # 这个计数已经混装四类：未核实 / 引述失实 / 年份存疑 / 无同业基准 / 方向错误。
    # 继续叫"未核实"就是标签与内容不符——正是这套系统一直在抓的那种错。
    _warn = f" ｜ ⚠存疑论据 {r.unverified_count} 条" if r.unverified_count else " ｜ 论据核验通过 ✓"
    _mat = f"采集材料 {r.material_count} 条"
    if r.material_verdict:
        _mat += f"（实质 {r.material_substantive} 条 · {r.material_verdict}）"
    L.append(f"\n> 对抗式辩论（独立建案 → 交叉反驳 → 论证审计 → 综合）｜ {_mat}"
             f"{_warn} ｜ 本地模型调用 {r.llm_calls} 次")
    L.append("\n> **一部只给方向与论证，不给仓位、止损、目标价或执行方案**——"
             "那是 CRO 与 Portfolio Construction 的职权。研究观点，非投资指令。")
    L.append("\n> Unit A：解释未来　·　Unit B：测量现在　·　CIO：发生了什么　·　"
             "CRO：风险是什么　·　PC：该承担多少　·　CEO：做不做")

    # 材料实质度横幅置顶。**没有这一条，报告会假装自己有基本面依据。**
    # 首跑 8 条材料全是"财报前瞻"标题，辩论完全落回量化面板，
    # 而多头第一条论据"NVDA 预计第二季度业绩将超预期【3】"指向的其实是一篇标题党。
    if r.material_banner:
        L.append("\n" + r.material_banner)

    # 历史论点复检结果放在最前——它是唯一会推翻过去结论的信息，比今天的新观点更该先看到
    if r.invalidation_hits:
        L.append("\n## ⚠ 历史论点失效提示")
        L.append("\n> 以下**过去登记的失效条件**被今天的材料命中。这是提示不是判决——"
                 "是否真的失效由 CEO 看材料决定。")
        for h in r.invalidation_hits:
            L.append(f"\n- **论点 #{h['thesis_id']}（{h['subject']} {h['direction']}）** "
                     f"失效条件：{h['condition']}")
            src = f"（[{h.get('source') or '来源'}]({h['url']})）" if h.get("url") else ""
            L.append(f"  - 触发材料：{h['fact']} {src}")
            L.append(f"  - 匹配覆盖率 {h.get('coverage')}，数字对上：{'是' if h.get('number_matched') else '否'}")

    # 方向漂移放在观点之前：它是对下面这个结论的限定条件，读者应该先看到。
    if r.direction_drift:
        _sev = r.direction_drift.get("severity")
        L.append("\n## " + ("⚠ 方向变化：无新证据" if _sev == "no_evidence"
                            else "⚠ 方向变化：证据偏薄" if _sev == "thin"
                            else "方向变化（有新证据支撑）"))
        L.append("\n" + _emph_md(r.direction_drift.get("text", "")))
        if r.direction_drift.get("prev_material_verdict"):
            L.append(f"\n> 既有论点 #{r.direction_drift['prev_id']} 当时的材料判定："
                     f"**{r.direction_drift['prev_material_verdict']}**。")

    if r.forced:
        L.append("\n> ⚠ **Forced review — no new substantive evidence; "
                 "analysis relies on existing evidence set.** "
                 "本次是人工强制复研，不是自动日常运行：Evidence Gate 判定为 "
                 f"**{r.gate_level}**，以下分析依据的是**既有证据集**，没有新证据。")
    L.append("\n## 一、一部观点")
    _conv = f"**{r.conviction}**"
    if r.conviction_capped:
        _conv += f"（原判 {r.conviction_capped}，因 Evidence Gate = THIN 封顶为「弱」）"
    L.append(f"- **方向：{r.direction}** ｜ 信心：{_conv}")
    if r.thesis_id:
        # 0 条时不能写"已登记、后续每日自动复检"——那句话是假的：
        # 没有条件就没有东西可复检，说成有，等于让人以为回路在工作。
        _note = (f"失效条件 {len(r.invalidations)} 条已登记，后续每日自动复检"
                 if r.invalidations else "⚠ 无可核对的失效条件，此论点不参与每日复检")
        L.append(f"- 论点台账编号 **#{r.thesis_id}**（{_note}）")
    L.append("\n" + (_emph_md(r.synthesis) or "（无）"))

    if r.catalysts:
        L.append("\n### 催化剂（什么会证实这个论点）")
        for c in r.catalysts:
            L.append(f"- {_emph_md(c)}")
    if r.invalidations:
        L.append("\n### 失效条件（什么一旦发生，论点即应视为失效）")
        for c in r.invalidations:
            L.append(f"- {_emph_md(c)}")
        if r.market_only_invalidations:
            L.append(f"\n> ⚠ 其中 **{len(r.market_only_invalidations)} 条只引用了股价/风险统计量**"
                     f"（{'；'.join(r.market_only_invalidations)}）。"
                     "**股价下跌本身不证明论点错**——对一个逆向或长期论点，"
                     "那可能恰恰是它最成立的时候。用股价当失效条件，"
                     "等于把「论点错了」和「暂时亏钱」划等号。真正的失效条件应当指向公司事实。")
        L.append("\n> 这几条已写入论点台账。**这是一部唯一可被后续事实证伪的产出**——"
                 "写下来容易，回来检查才让它有价值。")
    elif r.parse_warnings:
        # 解析失败 ≠ 模型没写。说成后者就是拿自己的 bug 去指责模型，
        # 而且会让人以为回路在工作——台账其实存了 0 条。
        L.append("\n### 失效条件")
        for w in r.parse_warnings:
            L.append(f"- {w}")
    else:
        L.append("\n### 失效条件")
        L.append("- （本轮未产出可核对的失效条件）")
        L.append("\n> ⚠ 没有失效条件的论点**此后无法被证伪**。这本身值得注意——"
                 "要么是材料不足以支撑具体条件，要么是论证过于笼统。")

    L.append("\n## 二、量化证据面板（固定口径 · 多空双方看到的是同一张完整的表）")
    L.append("\n> 面板预先定死，不由模型挑选。这是不让它从数百个 alpha 里"
             "搜出对自己有利那一撮的唯一办法。标「无数据」的项目表示**确实没有**，不是 0。")
    L.append("\n```\n" + (r.panel_text or "（本轮无面板）") + "\n```")

    L.append("\n## 三、多头论据（Round 1 · 独立建案）")
    L.append(_emph_md(r.bull_case) or "（无）")
    if r.bull_rebuttal:
        L.append("\n### 多头 Round 2 · 反驳与直面不利证据")
        L.append(_emph_md(r.bull_rebuttal))
    L.append("\n## 四、空头论据（Round 1 · 独立建案）")
    L.append(_emph_md(r.bear_case) or "（无）")
    if r.bear_rebuttal:
        L.append("\n### 空头 Round 2 · 反驳与直面不利证据")
        L.append(_emph_md(r.bear_rebuttal))
    L.append("\n> Round 1 双方互不可见（防锚定）；Round 2 才交换，"
             "且各自**必须回应面板上对自己最不利的三条**——"
             "否则挑选行为会从「引用哪几格」这个后门回来。")

    L.append("\n## 五、论证审计（Judge：只审计论证，不重做研究）")
    L.append(_emph_md(r.audit) or "（无）")

    L.append("\n## 六、行情 / 回测支撑（yfinance 真值）")
    for q in (r.quant or ["（无）"]):
        L.append(f"- {_emph_md(q)}")
    L.append("\n## 七、采集材料清单（论据引用依据 · 可点链接核对）")
    if r.material_labels:
        L.append("\n> 每条后面的标签是**确定性实质度判定**，规则写死可审计——"
                 "判错了你当场就能看见。「实质」需满足条件才能获得，判不出来一律算不实质："
                 "误判标题党为充分，损失是整份报告的可信度；反过来只是多一句警告。")
    for mi in (r.materials or []):
        src = f"（[{mi.source_name or '来源'}]({mi.source_url})）" if mi.source_url else ""
        lab = r.material_labels.get(mi.id) or r.material_labels.get(str(mi.id)) or ""
        tag = f" `{lab}`" if lab else ""
        L.append(f"- **[{mi.id}]**{tag} {mi.text} {src}")
    if not r.materials:
        L.append("- （本轮无采集材料）")
    L.append("\n---\n*证券一部：基本面与事件驱动的对抗式研究。独立于 CIO 与证券二部产生判断——"
             "自行采集材料，只调用共享的确定性计算层，不读二部报告（避免锚定）。"
             "允许方向性看法（一部职权），但不涉仓位与执行。非投资指令，须经 CRO 与 CEO 决断。*")
    return "\n".join(L)


def _pdf_not_activated(r: UnitAAdvice, S) -> list:
    """一部未启动时的 PDF。**必须与 _md_not_activated 同构**——
    推给 CEO 的是 PDF，Markdown 只留在磁盘上（build62 的教训，不再犯第二次）。
    """
    from reportlab.platypus import Paragraph, Spacer

    from . import material_gate as MG
    el = [Paragraph(_esc(f"《{r.resolved} 证券一部观点》 — {r.dt_beijing}（北京）"), S["title"])]
    el.append(Paragraph(_esc(
        f"Evidence Gate = {r.gate_level}（{r.material_verdict}：{r.material_count} 条材料，"
        f"实质 {r.material_substantive} 条） | 本地模型调用 {r.llm_calls} 次"), S["small"]))
    el.append(Spacer(1, 6))

    el.append(Paragraph(_esc(MG.NOT_ACTIVATED_HEADLINE), S["h"]))
    el.append(Paragraph(f"<b>{_esc(MG.FORMAL_VOTE_ABSTAIN)}</b>", S["bluf"]))
    el.append(Paragraph(_esc(MG.PANEL_FOLLOWS), S["p"]))
    if r.material_banner:
        el.append(Paragraph(_emph_pdf(_esc(r.material_banner)), S["p"]))
    el.append(Paragraph(_esc(
        "没有新的可解释信息，就不制造新的观点。本轮采集到的材料没有一条含增量事实，"
        "因此一部不启动多空辩论——在这种情况下辩论只会拿二部已经确定性算好的数字"
        "重新讲一遍故事，既没有新的 information set，也没有真正的因果推理；"
        "而同一批不变的数字每天重跑，方向与信心的来回摆动是采样噪声，不是市场变化。"), S["small"]))
    el.append(Paragraph(_esc(
        "三个部门的节奏本就不同：CIO 持续捕捉世界发生了什么（每天），"
        "二部测量市场当前状态（每天），一部只在新证据到来时重新研究。"), S["small"]))
    el.append(Spacer(1, 5))

    if r.invalidation_hits:
        el.append(Paragraph("⚠ 历史论点失效提示", S["h"]))
        el.append(Paragraph(_esc("一部虽未启动，既有论点的监控照常进行。以下失效条件被今天的材料命中，"
                                 "是提示不是判决——是否真的失效由 CEO 看材料决定。"), S["small"]))
        for h in r.invalidation_hits:
            el.append(Paragraph(_esc(f"论点 #{h['thesis_id']}（{h['subject']} {h['direction']}）"
                                     f"失效条件：{h['condition']}"), S["p"]))
            link = (f'　<a href="{_esc(h["url"])}">{_esc(h.get("source") or "来源")}</a>'
                    if h.get("url") else "")
            el.append(Paragraph("触发材料：" + _esc(h["fact"]) + link, S["small"]))
        el.append(Spacer(1, 5))

    el.append(Paragraph("一、仍在监控中的既有论点", S["h"]))
    _open = [t for t in (r.open_theses or []) if t.get("invalidations")]
    if _open:
        for t in _open:
            extra = f"，当时材料判定：{t['material_verdict']}" if t.get("material_verdict") else ""
            el.append(Paragraph(
                f"<b>#{t['id']} {_esc(t['subject'])} {_esc(t['direction'])}｜{_esc(t['conviction'])}</b>"
                + _esc(f"（{t['as_of']} 登记{extra}，失效条件 {len(t['invalidations'])} 条）"), S["p"]))
            for c in t["invalidations"]:
                el.append(Paragraph("• " + _emph_pdf(_esc(c)), S["small"]))
        el.append(Paragraph(_esc(
            "未启动不等于没有观点。这些论点仍然有效，其失效条件每天照常与新材料比对。"
            "今天没有新证据，所以不重写一遍——重写只会制造一个看起来更新、"
            "实际没有更多依据的版本。"), S["small"]))
    else:
        el.append(Paragraph(_esc("（台账中暂无仍 OPEN 的论点）"), S["p"]))
        el.append(Paragraph(_esc("既没有新证据、也没有既有论点——一部对该标的目前没有任何立场。"),
                            S["small"]))

    el.append(Paragraph("二、量化证据面板（固定口径 · 情境感知用）", S["h"]))
    el.append(Paragraph(_esc("面板照常产出，但它是二部口径的测量，不是一部的研究结论。"
                             "标「无数据」的项目表示确实没有，不是 0。"), S["small"]))
    for ln in (r.panel_text or "（本轮无面板）").split("\n"):
        if ln.strip():
            el.append(Paragraph(_emph_pdf(_esc(ln)), S["p"] if ln.startswith("【") else S["small"]))

    el.append(Paragraph("三、采集材料质量（为什么判为无实质）", S["h"]))
    el.append(Paragraph(_esc("判定规则写死可审计——判错了当场就能看见，回来改规则。"), S["small"]))
    if r.materials:
        for mi in r.materials:
            src = (f'　<a href="{_esc(mi.source_url)}">{_esc(mi.source_name or "来源")}</a>'
                   if mi.source_url else "")
            lab = r.material_labels.get(mi.id) or r.material_labels.get(str(mi.id)) or ""
            tag = f" [{_esc(lab)}]" if lab else ""
            el.append(Paragraph(f"<b>[{mi.id}]</b>{tag} {_emph_pdf(_esc(mi.text))}{src}", S["p"]))
    else:
        el.append(Paragraph("（本轮无采集材料）", S["small"]))

    el.append(Spacer(1, 6))
    el.append(Paragraph(_esc(
        "证券一部：Fundamental & Event-Driven Adversarial Research —— evidence-triggered，"
        "不是每日评论台。要在无新证据时强制复研，用 UNIT_A_FORCE_RESEARCH=1 或 --force；"
        "那属于有意的人工决定（首次建仓、季度复审、论点到期），"
        "报告会明确标注它依据的是既有证据集。"), S["small"]))
    return el


def render_unit_a_pdf(r: UnitAAdvice, path: str):
    """一部 PDF。**必须与 MD 渲染器保持同构**——推给 CEO 的是这一份，
    Markdown 只留在磁盘上。首个真机版本里 MD 已经改成七节新版式，
    而这里还是旧的六节，于是量化面板、反驳轮、论证审计、失效条件在交付物里全都不见了，
    且没有任何报错。与 build59 那个"渲染成空白的脚注"是同一类错误：
    改了渲染就必须两边一起改，并有自检守住。
    """
    from reportlab.platypus import Paragraph, Spacer

    def story(S):
        el = [Paragraph(_esc(f"《{r.resolved} 证券一部观点》 — {r.dt_beijing}（北京）"), S["title"])]
        _warn = f" | 存疑论据 {r.unverified_count} 条" if r.unverified_count else " | 论据核验通过"
        if not r.activated:
            return _pdf_not_activated(r, S)
        _mat = f"采集材料 {r.material_count} 条"
        if r.material_verdict:
            _mat += f"（实质 {r.material_substantive} 条 · {r.material_verdict}）"
        el.append(Paragraph(_esc(
            f"对抗式辩论（独立建案 → 交叉反驳 → 论证审计 → 综合） | {_mat}"
            f"{_warn} | 本地模型调用 {r.llm_calls} 次"), S["small"]))
        el.append(Paragraph(_esc(
            "一部只给方向与论证，不给仓位、止损、目标价或执行方案——那是 CRO 与 "
            "Portfolio Construction 的职权。研究观点，非投资指令。"), S["small"]))
        el.append(Paragraph(_esc(
            "Unit A：解释未来 · Unit B：测量现在 · CIO：发生了什么 · "
            "CRO：风险是什么 · PC：该承担多少 · CEO：做不做"), S["small"]))
        el.append(Spacer(1, 6))

        # 材料实质度横幅置顶。PDF 才是推给 CEO 的那一份——
        # 只加在 MD 上等于没加（build62 的教训）。
        if r.material_banner:
            el.append(Paragraph(_emph_pdf(_esc(r.material_banner)), S["bluf"]))
            el.append(Spacer(1, 4))

        # 历史失效提示置顶：它是唯一会推翻过去结论的信息，比今天的新观点更该先看到
        if r.invalidation_hits:
            el.append(Paragraph("⚠ 历史论点失效提示", S["h"]))
            el.append(Paragraph(_esc("以下过去登记的失效条件被今天的材料命中。这是提示不是判决——"
                                     "是否真的失效由 CEO 看材料决定。"), S["small"]))
            for h in r.invalidation_hits:
                el.append(Paragraph(_esc(f"论点 #{h['thesis_id']}（{h['subject']} {h['direction']}）"
                                         f"失效条件：{h['condition']}"), S["p"]))
                link = (f'　<a href="{_esc(h["url"])}">{_esc(h.get("source") or "来源")}</a>'
                        if h.get("url") else "")
                el.append(Paragraph("触发材料：" + _esc(h["fact"]) + link, S["small"]))
            el.append(Spacer(1, 5))

        el.append(Paragraph("一、一部观点", S["h"]))
        if r.direction_drift:
            _sev = r.direction_drift.get("severity")
            el.append(Paragraph(_esc(
                "⚠ 方向变化：无新证据" if _sev == "no_evidence"
                else "⚠ 方向变化：证据偏薄" if _sev == "thin"
                else "方向变化（有新证据支撑）"), S["h"]))
            el.append(Paragraph(_emph_pdf(_esc(r.direction_drift.get("text", ""))), S["bluf"]))
            if r.direction_drift.get("prev_material_verdict"):
                el.append(Paragraph(_esc(
                    f"既有论点 #{r.direction_drift['prev_id']} 当时的材料判定："
                    f"{r.direction_drift['prev_material_verdict']}。"), S["small"]))

        if r.forced:
            el.append(Paragraph(_emph_pdf(_esc(
                "⚠ Forced review — no new substantive evidence; analysis relies on "
                f"existing evidence set. 本次是人工强制复研，不是自动日常运行：Evidence Gate = "
                f"{r.gate_level}，以下分析依据的是既有证据集，没有新证据。")), S["bluf"]))
        _conv = f"<b>{_esc(r.conviction)}</b>"
        if r.conviction_capped:
            _conv += _esc(f"（原判 {r.conviction_capped}，因 Evidence Gate = THIN 封顶为「弱」）")
        el.append(Paragraph(f"<b>方向：{_esc(r.direction)}</b>　｜　信心：{_conv}", S["bluf"]))
        if r.thesis_id:
            _note = (f"失效条件 {len(r.invalidations)} 条已登记，后续每日自动复检"
                     if r.invalidations else "⚠ 无可核对的失效条件，此论点不参与每日复检")
            el.append(Paragraph(_esc(f"论点台账编号 #{r.thesis_id}（{_note}）"), S["small"]))
        el += _para_lines(r.synthesis, S)

        if r.catalysts:
            el.append(Paragraph("催化剂（什么会证实这个论点）", S["h"]))
            for c in r.catalysts:
                el.append(Paragraph("• " + _emph_pdf(_esc(c)), S["p"]))
        el.append(Paragraph("失效条件（什么一旦发生，论点即应视为失效）", S["h"]))
        if r.invalidations:
            for c in r.invalidations:
                el.append(Paragraph("• " + _emph_pdf(_esc(c)), S["p"]))
            if r.market_only_invalidations:
                el.append(Paragraph(_esc(
                    f"⚠ 其中 {len(r.market_only_invalidations)} 条只引用了股价/风险统计量"
                    f"（{'；'.join(r.market_only_invalidations)}）。"
                    "股价下跌本身不证明论点错——对一个逆向或长期论点，那可能恰恰是它最成立的时候。"
                    "用股价当失效条件，等于把「论点错了」和「暂时亏钱」划等号。"
                    "真正的失效条件应当指向公司事实。"), S["p"]))
            el.append(Paragraph(_esc("这几条已写入论点台账。这是一部唯一可被后续事实证伪的产出——"
                                     "写下来容易，回来检查才让它有价值。"), S["small"]))
        elif r.parse_warnings:
            for w in r.parse_warnings:
                el.append(Paragraph(_emph_pdf(_esc(w)), S["bluf"]))
        else:
            el.append(Paragraph(_esc("（本轮未产出可核对的失效条件）"), S["p"]))
            el.append(Paragraph(_esc("没有失效条件的论点此后无法被证伪。这本身值得注意——"
                                     "要么材料不足以支撑具体条件，要么论证过于笼统。"), S["small"]))

        el.append(Paragraph("二、量化证据面板（固定口径 · 多空双方看到的是同一张完整的表）", S["h"]))
        el.append(Paragraph(_esc("面板预先定死，不由模型挑选。这是不让它从数百个 alpha 里搜出"
                                 "对自己有利那一撮的唯一办法。标「无数据」的项目表示确实没有，不是 0。"),
                            S["small"]))
        for ln in (r.panel_text or "（本轮无面板）").split("\n"):
            if ln.strip():
                st = S["p"] if ln.startswith("【") else S["small"]
                el.append(Paragraph(_emph_pdf(_esc(ln)), st))

        el.append(Paragraph("三、多头论据（Round 1 · 独立建案）", S["h"]))
        el += _para_lines(r.bull_case, S)
        if r.bull_rebuttal:
            el.append(Paragraph("多头 Round 2 · 反驳与直面不利证据", S["h"]))
            el += _para_lines(r.bull_rebuttal, S)
        el.append(Paragraph("四、空头论据（Round 1 · 独立建案）", S["h"]))
        el += _para_lines(r.bear_case, S)
        if r.bear_rebuttal:
            el.append(Paragraph("空头 Round 2 · 反驳与直面不利证据", S["h"]))
            el += _para_lines(r.bear_rebuttal, S)
        el.append(Paragraph(_esc("Round 1 双方互不可见（防锚定）；Round 2 才交换，"
                                 "且各自必须回应面板上对自己最不利的三条——"
                                 "否则挑选行为会从「引用哪几格」这个后门回来。"), S["small"]))

        el.append(Paragraph("五、论证审计（Judge：只审计论证，不重做研究）", S["h"]))
        el += _para_lines(r.audit, S)

        el.append(Paragraph("六、行情 / 回测支撑（yfinance 真值）", S["h"]))
        for q in (r.quant or ["（无）"]):
            el.append(Paragraph(_emph_pdf(_esc(q)), S["p"]))

        el.append(Paragraph("七、采集材料清单（论据引用依据 · 可点链接核对）", S["h"]))
        if r.material_labels:
            el.append(Paragraph(_esc(
                "每条后面的标签是确定性实质度判定，规则写死可审计——判错了当场就能看见。"
                "「实质」需满足条件才能获得，判不出来一律算不实质。"), S["small"]))
        if r.materials:
            for mi in r.materials:
                src = (f'　<a href="{_esc(mi.source_url)}">{_esc(mi.source_name or "来源")}</a>'
                       if mi.source_url else "")
                lab = r.material_labels.get(mi.id) or r.material_labels.get(str(mi.id)) or ""
                tag = f" [{_esc(lab)}]" if lab else ""
                el.append(Paragraph(f"<b>[{mi.id}]</b>{tag} {_emph_pdf(_esc(mi.text))}{src}", S["p"]))
        else:
            el.append(Paragraph("（本轮无采集材料）", S["small"]))

        el.append(Spacer(1, 6))
        el.append(Paragraph(_esc(
            "证券一部：基本面与事件驱动的对抗式研究。独立于 CIO 与证券二部产生判断——"
            "自行采集材料，只调用共享的确定性计算层，不读二部报告（避免锚定）。"
            "允许方向性看法（一部职权），但不涉仓位与执行。非投资指令，须经 CRO 与 CEO 决断。"),
            S["small"]))
        return el

    _build_pdf(path, f"{r.resolved} 证券一部观点", story)


# ============================ 证券二部（量化选股）============================

def _sector_tag(s: str) -> str:
    return f"【{s}】" if s else "—"


_FACTORS_EN = {"动量": "Momentum", "反转": "Reversal", "低波": "LowVol", "趋势": "Trend", "量能": "Volume"}


def _unit_b_md_en(r: UnitBAdvice) -> str:
    order = _FACTORS_ZH
    lab = {f: _FACTORS_EN.get(f, f) for f in order}
    L: list[str] = []
    L.append(f"# Unit B — Quantitative Stock Selection — {r.dt_ny} ET")
    L.append(f"\n> Deterministic multi-factor (zero LLM) · {r.universe_count} in universe / {r.scored_count} scored "
             f"· Benchmark: {r.benchmark} ({r.bench_source} {r.bench_basis}) · **Research view, not an order** (subject to CRO & CEO)")
    L.append(f"\n> {r.tilt_note}")
    if r.universe_src.startswith("fallback"):
        L.append("\n> ⚠ **DEGRADED / TEST** — universe from fallback core list, not the live S&P 500. Not an official Unit B result.")
    L.append(f"\n> PIT: price_pit={r.price_pit} · universe_pit={r.universe_pit} · snapshot={r.universe_snapshot or '—'} · run_id={r.run_id}")
    L.append(f"\n## 1. Model picks (Top {len(r.picks)}) — model weight, **not** final position")
    if r.weighting_method:
        L.append(f"\n*Weighting: {r.weighting_method}*")
    if r.picks:
        L.append("\n| Rank | Ticker | Name | Theme | Model wt | raw | tilt | final | Why |")
        L.append("|---|---|---|---|---|---|---|---|---|")
        for p in r.picks:
            th = ", ".join(p.focus_theme) or "—"
            L.append(f"| {p.rank} | {p.code} | {truncate(p.name, 18)} | {th} | {p.model_weight:.1f}% | "
                     f"{p.raw_quant_score:+.2f} | {p.focus_tilt:+.2f} | {p.final_score:+.2f} | {p.reason} |")
    else:
        L.append("\n(No names with sufficient history this run — stated honestly, nothing padded.)")
    L.append("\n## 2. Factor z-scores (cross-sectional, reproducible)")
    if r.picks:
        L.append("\n| Ticker | " + " | ".join(lab[f] for f in order) + " |")
        L.append("|---|" + "---|" * len(order))
        for p in r.picks:
            L.append(f"| {p.code} | " + " | ".join(f"{p.factors.get(f, 0):+.2f}" for f in order) + " |")
    L.append("\n## 3. Self-validation (walk-forward IC; **raw factors only**)")
    L.append(f"- {r.ic_summary}")
    L.append(f"- Focus-tilt attribution: {r.attribution}")
    L.append("  - IC>0: factor score aligns with forward return; IR=mean/vol (>0.3 fairly stable); spread>0: top quintile beats bottom.")
    L.append("\n## 4. Factors")
    for f in order:
        if f in r.factor_desc:
            L.append(f"- **{lab[f]}**: {r.factor_desc[f]}")
    L.append("\n## 5. Data & independence")
    if r.funnel:
        L.append(f"- Data funnel: {r.funnel}")
    L.append(f"- Universe: {r.universe}  |  snapshot: {r.universe_snapshot or '—'}")
    L.append(f"- Prices: {r.status.structured.get('quant_history', '—')}  |  Benchmark: {r.bench_source} ({r.bench_basis})")
    deg = "; ".join(r.status.degraded) if r.status.degraded else "none"
    L.append(f"- Degraded: {deg}")
    L.append("\n---\n*Unit B (self-built quant · zero LLM): research view, method-independent from Unit A (LLM debate) and CIO. "
             "Backtest/paper only; not an order. Outputs **model weights**, not company-level target positions "
             "(Portfolio Construction owns that). Factor scores reproducible; free-source prices.*")
    return "\n".join(L)


def render_unit_b_md(r: UnitBAdvice) -> str:
    if _rlang() == "en":
        return _unit_b_md_en(r)
    L: list[str] = []
    L.append(f"# 《证券二部 量化选股建议》 — {r.dt_beijing}（北京）/ {r.dt_ny}")
    L.append(f"\n> 沪深300 多因子量化（零 LLM·纯确定性）｜ 池内 {r.universe_count} 只 / 参与打分 {r.scored_count} 只 "
             f"｜ **研究观点，非投资指令**（须经 CRO 风控与 CEO 决断）")
    L.append(f"\n> {r.tilt_note}")
    L.append("\n## 一、今日量化选股（Top " + str(len(r.picks)) + "）")
    if r.picks:
        L.append("\n| 排名 | 代码 | 名称 | 关注池 | 合成分 | 入选主因 |")
        L.append("|---|---|---|---|---|---|")
        for p in r.picks:
            L.append(f"| {p.rank} | {p.code} | {p.name} | {_sector_tag(p.sector)} | "
                     f"{p.composite:+.2f} | {p.reason} |")
    else:
        L.append("\n（本轮无足够历史数据的标的，未出选股——诚实标注，不硬凑。）")
    L.append("\n## 二、因子拆解（每只票的 5 因子 z 分 · 可复算可解释）")
    if r.picks:
        L.append("\n| 代码 | " + " | ".join(_FACTORS_ZH) + " |")
        L.append("|---|" + "---|" * len(_FACTORS_ZH))
        for p in r.picks:
            cells = " | ".join(f"{p.factors.get(f, 0):+.2f}" for f in _FACTORS_ZH)
            L.append(f"| {p.code} | {cells} |")
    L.append("\n## 三、模型自证（历史有效性 · alphalens 式 IC）")
    L.append(f"- {r.ic_summary}")
    L.append("  - IC>0 表示因子打分与未来收益同向；IR=IC均值/波动（>0.3 视为较稳定）；分位差为正=高分组跑赢低分组。")
    L.append("\n## 四、因子说明")
    for f in _FACTORS_ZH:
        if f in r.factor_desc:
            L.append(f"- **{f}**：{r.factor_desc[f]}")
    L.append("\n## 五、数据与独立性")
    L.append(f"- 选股池：{r.universe}")
    deg = "；".join(r.status.degraded) if r.status.degraded else "无"
    L.append(f"- 数据覆盖：成功取到 {r.status.fetched} 只行情；降级：{deg}")
    L.append("\n---\n*证券二部（自研量化·零 LLM）研究观点；与证券一部【方法独立】（一部为 LLM 多空辩论），"
             "与 CIO 情报独立。只回测不实盘；非投资指令，须经 CRO 风控与 CEO 决断。因子分可复算、行情取自免费源真值。*")
    return "\n".join(L)


_FACTORS_ZH = ["动量", "反转", "低波", "趋势", "量能"]


def _unit_b_pdf_en(r: UnitBAdvice, path: str):
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    order = _FACTORS_ZH
    lab = {f: _FACTORS_EN.get(f, f) for f in order}

    def _tbl(data, col_widths, header_bg="#26364a"):
        t = Table(data, colWidths=col_widths, hAlign="LEFT")
        t.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), _CJK_FONT), ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(header_bg)),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f5f8")]),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c8d2de")), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4)]))
        return t

    def story(S):
        el = [Paragraph(_esc(f"Unit B — Quantitative Stock Selection — {r.dt_ny} ET"), S["title"])]
        el.append(Paragraph(_esc(f"Deterministic multi-factor (zero LLM) | {r.universe_count} in universe / {r.scored_count} scored "
                                 f"| Benchmark: {r.benchmark} ({r.bench_source} {r.bench_basis}) | Research view, not an order"), S["small"]))
        el.append(Paragraph(_esc(r.tilt_note), S["small"]))
        el.append(Paragraph(_esc(f"PIT price={r.price_pit} universe={r.universe_pit} | snapshot={r.universe_snapshot or '—'} | run_id={r.run_id}"), S["small"]))
        if r.universe_src.startswith("fallback"):
            el.append(Paragraph("<b>DEGRADED / TEST — fallback universe, not the live S&amp;P 500; not an official result.</b>", S["p"]))
        el.append(Spacer(1, 6))
        el.append(Paragraph("1. Model picks (Top N) — model weight, not final position", S["h"]))
        if r.weighting_method:
            el.append(Paragraph(_esc("Weighting: " + r.weighting_method), S["small"]))
        if r.picks:
            head = ["#", "Ticker", "Name", "Theme", "Wt%", "raw", "tilt", "final", "Why"]
            rows = [head] + [[str(p.rank), p.code, _esc(truncate(p.name, 14)), _esc(", ".join(p.focus_theme) or "—"),
                              f"{p.model_weight:.1f}", f"{p.raw_quant_score:+.2f}", f"{p.focus_tilt:+.2f}",
                              f"{p.final_score:+.2f}", _esc(truncate(p.reason, 26))] for p in r.picks]
            el.append(_tbl(rows, [0.7 * cm, 1.5 * cm, 2.2 * cm, 2.5 * cm, 1.1 * cm, 1.1 * cm, 1.0 * cm, 1.1 * cm, 3.9 * cm]))
        else:
            el.append(Paragraph("(No names with sufficient history — nothing padded.)", S["p"]))
        el.append(Spacer(1, 8))
        el.append(Paragraph("2. Factor z-scores (cross-sectional, reproducible)", S["h"]))
        if r.picks:
            head = ["Ticker"] + [lab[f] for f in order]
            rows = [head] + [[p.code] + [f"{p.factors.get(f, 0):+.2f}" for f in order] for p in r.picks]
            el.append(_tbl(rows, [2.2 * cm] + [2.0 * cm] * len(order)))
            el.append(Paragraph("(z = cross-sectional standardization: +1 ≈ stronger than ~84% of the pool; tilt excluded from these z's.)", S["small"]))
        el.append(Spacer(1, 8))
        el.append(Paragraph("3. Self-validation (walk-forward IC; raw factors only)", S["h"]))
        el.append(Paragraph(_esc(r.ic_summary), S["p"]))
        el.append(Paragraph(_esc("Focus-tilt attribution: " + r.attribution), S["p"]))
        el.append(Paragraph("IC&gt;0: factor aligns with forward return; IR=mean/vol (&gt;0.3 fairly stable); spread&gt;0: top quintile beats bottom.", S["small"]))
        el.append(Spacer(1, 6))
        el.append(Paragraph("4. Factors", S["h"]))
        for f in order:
            if f in r.factor_desc:
                el.append(Paragraph(f"<b>{_esc(lab[f])}</b>: {_esc(r.factor_desc[f])}", S["p"]))
        el.append(Spacer(1, 6))
        el.append(Paragraph("5. Data & independence", S["h"]))
        if r.funnel:
            el.append(Paragraph(_esc("Data funnel: " + r.funnel), S["p"]))
        deg = "; ".join(r.status.degraded) if r.status.degraded else "none"
        el.append(Paragraph(_esc(f"Universe: {r.universe} | snapshot: {r.universe_snapshot or '—'} | "
                                 f"prices: {r.status.structured.get('quant_history', '—')} | "
                                 f"benchmark: {r.bench_source} ({r.bench_basis}) | degraded: {deg}"), S["p"]))
        el.append(Spacer(1, 6))
        el.append(Paragraph("Unit B (self-built quant, zero LLM): research view, method-independent from Unit A and CIO. Backtest/paper only; not an order. "
                            "Outputs model weights, not company-level target positions (Portfolio Construction owns that). Reproducible factor scores; free-source prices.", S["small"]))
        return el

    _build_pdf(path, "Unit B — Quant Selection", story)


def render_unit_b_pdf(r: UnitBAdvice, path: str):
    if _rlang() == "en":
        return _unit_b_pdf_en(r, path)
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle
    from reportlab.lib import colors
    from reportlab.lib.units import cm

    def _tbl(data, S, col_widths=None, header_bg="#26364a"):
        t = Table(data, colWidths=col_widths, hAlign="LEFT")
        t.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), _CJK_FONT),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(header_bg)),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f5f8")]),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c8d2de")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]))
        return t

    def story(S):
        el = [Paragraph(_esc(f"《证券二部 量化选股建议》 — {r.dt_beijing}（北京）"), S["title"])]
        el.append(Paragraph(_esc(f"沪深300 多因子量化（零 LLM·纯确定性）｜ 池内 {r.universe_count} 只 / 打分 {r.scored_count} 只"
                                 " ｜ 研究观点，非投资指令（须经 CRO 与 CEO 决断）"), S["small"]))
        el.append(Paragraph(_esc(r.tilt_note), S["small"]))
        el.append(Spacer(1, 6))

        el.append(Paragraph("一、今日量化选股（Top 3）", S["h"]))
        if r.picks:
            head = ["排名", "代码", "名称", "关注池", "合成分", "入选主因"]
            rows = [head] + [[str(p.rank), p.code, _esc(p.name), _esc(_sector_tag(p.sector)),
                              f"{p.composite:+.2f}", _esc(p.reason)] for p in r.picks]
            el.append(_tbl(rows, S, col_widths=[1.1 * cm, 1.8 * cm, 2.4 * cm, 1.8 * cm, 1.6 * cm, 7.0 * cm]))
        else:
            el.append(Paragraph("（本轮无足够历史数据的标的，未出选股——诚实标注，不硬凑。）", S["p"]))
        el.append(Spacer(1, 8))

        el.append(Paragraph("二、因子拆解（每只票 5 因子 z 分 · 可复算可解释）", S["h"]))
        if r.picks:
            head = ["代码", "名称"] + _FACTORS_ZH
            rows = [head] + [[p.code, _esc(truncate(p.name, 6))] +
                             [f"{p.factors.get(f, 0):+.2f}" for f in _FACTORS_ZH] for p in r.picks]
            el.append(_tbl(rows, S, col_widths=[1.8 * cm, 2.2 * cm] + [1.9 * cm] * 5))
            el.append(Paragraph("（z 分为横截面标准化：+1 表示该因子强于池内约 84% 的股票；关注池加权已计入合成分。）", S["small"]))
        el.append(Spacer(1, 8))

        el.append(Paragraph("三、模型自证（历史有效性 · alphalens 式 IC）", S["h"]))
        el.append(Paragraph(_emph_pdf(_esc(r.ic_summary)), S["p"]))
        el.append(Paragraph("IC>0：因子打分与未来收益同向；IR=IC均值/波动（&gt;0.3 较稳定）；分位差为正=高分组跑赢低分组。", S["small"]))
        el.append(Spacer(1, 6))

        el.append(Paragraph("四、因子说明", S["h"]))
        for f in _FACTORS_ZH:
            if f in r.factor_desc:
                el.append(Paragraph(f"<b>{_esc(f)}</b>：{_esc(r.factor_desc[f])}", S["p"]))
        el.append(Spacer(1, 6))

        el.append(Paragraph("五、数据与独立性", S["h"]))
        deg = "；".join(r.status.degraded) if r.status.degraded else "无"
        el.append(Paragraph(_esc(f"选股池：{r.universe}　｜　成功取到 {r.status.fetched} 只行情　｜　降级：{deg}"), S["p"]))
        el.append(Spacer(1, 6))
        el.append(Paragraph("证券二部（自研量化·零 LLM）研究观点；与证券一部【方法独立】（一部为 LLM 多空辩论），与 CIO 情报独立。"
                            "只回测不实盘；非投资指令，须经 CRO 风控与 CEO 决断。因子分可复算，行情取自免费源真值。", S["small"]))
        return el

    _build_pdf(path, "证券二部 量化选股建议", story)


# ============================ CRO 风控评级 ============================

def _yi(v: float) -> str:
    """成交额（元）→ 亿元字符串。"""
    try:
        return f"{v/1e8:.2f}亿"
    except Exception:
        return "—"


def render_cro_md(r: CRORating) -> str:
    L: list[str] = []
    L.append(f"# 《CRO 风控评级》 — {r.dt_beijing}（北京）/ {r.dt_ny}")
    L.append(f"\n> 独立风控 · 零 LLM 纯确定性 ｜ 否决 {r.vetoed_count} 只 ｜ **研究观点，非投资指令**（须经 CEO 决断）")
    L.append("\n## 一、投资倾向（整体）")
    L.append(f"- **整体倾向：{r.leaning}** ｜ 建议总仓位：**{r.target_position}**")
    L.append(f"- 大盘依据：{r.bench_note}")
    L.append(f"- 两线一致性：{r.consistency_note}")
    L.append("\n## 二、逐只风险评级（五维·可复算）")
    if r.items:
        L.append("\n| 来源 | 代码 | 名称 | 波动 | 回撤 | 成交额 | β | 风险分 | 评级 | 否决 | 主因 |")
        L.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for it in r.items:
            L.append(f"| {it.source} | {it.code} | {it.name} | {it.vol*100:.0f}% | {it.max_dd*100:.0f}% | "
                     f"{_yi(it.liquidity)} | {it.beta:.2f} | {it.risk_score:.2f} | {it.rating} | "
                     f"{'❌' if it.vetoed else '—'} | {it.reason} |")
    L.append("\n## 三、送 CEO 终批清单（过筛后）")
    for a in (r.approved_candidates or ["（全部被否决，无候选）"]):
        L.append(f"- {a}")
    L.append("\n---\n*CRO（独立风控·零 LLM）研究观点，允许风险/方向判断（CRO 职权）；非投资指令，须经 CEO 决断。"
             "与证券一部、二部方法独立。风险指标全部客观可复算，行情取自免费源真值。*")
    return "\n".join(L)


def _risk_tbl(data, S, col_widths, colors, TableStyle, Table, veto_rows=None):
    t = Table(data, colWidths=col_widths, hAlign="LEFT")
    style = [
        ("FONTNAME", (0, 0), (-1, -1), _CJK_FONT), ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#7a2331")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6eef0")]),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d8c4c8")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]
    for ri in (veto_rows or []):
        style.append(("TEXTCOLOR", (0, ri), (-1, ri), colors.HexColor("#b3261e")))
    t.setStyle(TableStyle(style))
    return t


def render_cro_pdf(r: CRORating, path: str):
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle
    from reportlab.lib import colors
    from reportlab.lib.units import cm

    def story(S):
        el = [Paragraph(_esc(f"《CRO 风控评级》 — {r.dt_beijing}（北京）"), S["title"])]
        el.append(Paragraph(_esc(f"独立风控 · 零 LLM 纯确定性 ｜ 否决 {r.vetoed_count} 只 ｜ 研究观点，非投资指令（须经 CEO 决断）"), S["small"]))
        el.append(Spacer(1, 6))

        el.append(Paragraph("一、投资倾向（整体）", S["h"]))
        el.append(Paragraph(f"<b>整体倾向：{_esc(r.leaning)}</b>　｜　建议总仓位：<b>{_esc(r.target_position)}</b>", S["bluf"]))
        el.append(Paragraph(_esc("大盘依据：" + r.bench_note), S["p"]))
        el.append(Paragraph(_esc("两线一致性：" + r.consistency_note), S["p"]))
        el.append(Spacer(1, 7))

        el.append(Paragraph("二、逐只风险评级（五维 · 可复算）", S["h"]))
        head = ["来源", "代码", "名称", "波动", "回撤", "成交额", "β", "风险分", "评级", "否决", "主因"]
        rows = [head]
        veto_rows = []
        for i, it in enumerate(r.items, 1):
            rows.append([it.source, it.code, _esc(truncate(it.name, 5)), f"{it.vol*100:.0f}%",
                         f"{it.max_dd*100:.0f}%", _yi(it.liquidity), f"{it.beta:.2f}",
                         f"{it.risk_score:.2f}", it.rating, "❌" if it.vetoed else "—", _esc(truncate(it.reason, 18))])
            if it.vetoed:
                veto_rows.append(i)
        el.append(_risk_tbl(rows, S, [1.1*cm, 1.5*cm, 1.9*cm, 1.1*cm, 1.1*cm, 1.4*cm, 1.0*cm, 1.2*cm, 1.0*cm, 1.0*cm, 3.4*cm],
                            colors, TableStyle, Table, veto_rows))
        el.append(Spacer(1, 8))

        el.append(Paragraph("三、送 CEO 终批清单（过筛后）", S["h"]))
        for a in (r.approved_candidates or ["（全部被否决，无候选）"]):
            el.append(Paragraph("• " + _esc(a), S["p"]))
        el.append(Spacer(1, 6))
        el.append(Paragraph("CRO（独立风控·零 LLM）研究观点，允许风险/方向判断（CRO 职权）；非投资指令，须经 CEO 决断。"
                            "与证券一部、二部方法独立。风险指标客观可复算，行情取自免费源真值。", S["small"]))
        return el

    _build_pdf(path, "CRO 风控评级", story)


# ============================ CFO 盈亏表 ============================

def _money(v: float) -> str:
    return f"{v:,.0f}"


def render_cfo_md(st: PnLStatement) -> str:
    L: list[str] = []
    L.append(f"# 《财务部盈亏表》 — {st.as_of}（盯市）")
    L.append(f"\n> 纸面验证 · 零 LLM 纯账本 ｜ 持仓模式：{st.mode} ｜ 中立记账，只报事实盈亏")
    L.append("\n## 一、账户层")
    L.append("\n| 账户 | 初始 | 现金 | 持仓市值 | 总净值 | 累计盈亏 | 当日 | 沪深300 | 超额 |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for a in st.accounts:
        L.append(f"| {a.account} | {_money(a.capital)} | {_money(a.cash)} | {_money(a.holdings)} | "
                 f"**{_money(a.net_value)}** | {a.pnl:+,.0f}（{a.pnl_pct:+.2%}） | {a.day_pnl:+,.0f} | "
                 f"{a.bench_pct:+.2%} | {a.excess:+.2%} |")
    L.append("\n## 二、持仓层")
    L.append("\n| 账户 | 代码 | 名称 | 建仓价 | 现价 | 股数 | 市值 | 盈亏 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for p in st.positions:
        px = f"{p.last}" + ("" if p.priced else "⚠缺价")
        L.append(f"| {p.account} | {p.code} | {p.name} | {p.cost} | {px} | {p.shares} | "
                 f"{_money(p.market_value)} | {p.pnl:+,.0f}（{p.pnl_pct:+.2%}） |")
    L.append("\n## 三、对比层")
    L.append(f"- {st.compare_note}")
    if st.missing_prices:
        L.append(f"\n> ⚠ 缺价标的（沿用上一有效价，未估算）：{'、'.join(st.missing_prices)}")
    L.append("\n---\n*财务部（零 LLM·纯账本）中立记账，只纸面不实盘；盯市价取自收盘真值，缺价如实标注不估算。"
             "不选股、不判方向/风险——那分别是两线与 CRO 的职权。*")
    return "\n".join(L)


def render_cfo_pdf(st: PnLStatement, path: str):
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle
    from reportlab.lib import colors
    from reportlab.lib.units import cm

    def _tbl(data, cw, header="#1f4d3a"):
        t = Table(data, colWidths=cw, hAlign="LEFT")
        t.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), _CJK_FONT), ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(header)),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#eef4f0")]),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c6d6cc")), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ]))
        return t

    def story(S):
        el = [Paragraph(_esc(f"《财务部盈亏表》 — {st.as_of}（盯市）"), S["title"])]
        el.append(Paragraph(_esc(f"纸面验证 · 零 LLM 纯账本 ｜ 持仓模式：{st.mode} ｜ 中立记账，只报事实盈亏"), S["small"]))
        el.append(Spacer(1, 6))

        el.append(Paragraph("一、账户层", S["h"]))
        head = ["账户", "初始", "现金", "持仓市值", "总净值", "累计盈亏", "当日", "沪深300", "超额"]
        rows = [head] + [[a.account, _money(a.capital), _money(a.cash), _money(a.holdings), _money(a.net_value),
                          f"{a.pnl_pct:+.2%}", f"{a.day_pnl:+,.0f}", f"{a.bench_pct:+.2%}", f"{a.excess:+.2%}"]
                         for a in st.accounts]
        el.append(_tbl(rows, [1.6*cm, 1.9*cm, 1.9*cm, 2.0*cm, 2.0*cm, 1.8*cm, 1.6*cm, 1.6*cm, 1.5*cm]))
        el.append(Spacer(1, 8))

        el.append(Paragraph("二、持仓层", S["h"]))
        head = ["账户", "代码", "名称", "建仓价", "现价", "股数", "市值", "盈亏", "盈亏率"]
        rows = [head]
        for p in st.positions:
            rows.append([p.account, p.code, _esc(truncate(p.name, 5)), f"{p.cost}",
                         f"{p.last}" + ("" if p.priced else "⚠"), f"{p.shares}", _money(p.market_value),
                         f"{p.pnl:+,.0f}", f"{p.pnl_pct:+.2%}"])
        el.append(_tbl(rows, [1.5*cm, 1.6*cm, 1.9*cm, 1.7*cm, 1.6*cm, 1.5*cm, 2.0*cm, 1.9*cm, 1.6*cm], header="#26364a"))
        el.append(Spacer(1, 8))

        el.append(Paragraph("三、对比层（一部 vs 二部 · 主盘 vs 影子盘）", S["h"]))
        for part in st.compare_note.split(" ｜ "):
            el.append(Paragraph("• " + _esc(part), S["p"]))
        if st.missing_prices:
            el.append(Spacer(1, 3))
            el.append(Paragraph("⚠ 缺价标的（沿用上一有效价，未估算）：" + _esc("、".join(st.missing_prices)), S["small"]))
        el.append(Spacer(1, 6))
        el.append(Paragraph("财务部（零 LLM·纯账本）中立记账，只纸面不实盘；盯市价取自收盘真值，缺价如实标注不估算。"
                            "不选股、不判方向/风险——那分别是两线与 CRO 的职权。", S["small"]))
        return el

    _build_pdf(path, "财务部盈亏表", story)
