"""Mockup 风格 HTML 简报 + HTML→PDF（让 Telegram 附件长得像 mockup）。

同一份 Brief 数据，换一套"外壳"：机构终端风格、四分卡带圆点评分条、Market Snapshot 网格、
页首 30 秒速读色块。HTML→PDF 引擎按稳健度优先：Playwright/Chromium（首选，macOS 上最稳）
→ weasyprint（备选，需系统原生库）；两者都不可用时上层自动回退 reportlab。全程本地、免费。
标签沿用 render._BRIEF_L，与 PDF 版语言一致。
"""
from __future__ import annotations

import html as _html

from .render import _BRIEF_L, _one_link, _rlang, _stale_marker, best_source, source_label

_ACCENT = "#1F3A5F"


def _e(s) -> str:
    return _html.escape(str(s if s is not None else ""))


def _pct(q) -> str:
    import math
    if q.change_pct is None or (isinstance(q.change_pct, float) and math.isnan(q.change_pct)):
        return '<span class="q-flat">—</span>'
    cls = "q-up" if q.change_pct >= 0 else "q-down"
    return f'<span class="{cls}">{q.change_pct:+.2f}%</span>'


def _val(q) -> str:
    import math
    import re
    if q.last is None or (isinstance(q.last, float) and math.isnan(q.last)):
        note = q.note or ""
        if _rlang() == "en" and re.search(r"[一-鿿]", note):   # §5 兜底：en 模式不显示中文降级串
            note = "n/a today"
        return _e(note or "—")
    return f"{q.last:,.2f}"


def _dots(n: int, total: int = 5) -> str:
    n = max(0, min(total, int(n or 0)))
    return "".join(f'<span class="dot{" on" if i < n else ""}"></span>' for i in range(total))


_CSS = """
@page { size: A4; margin: 1.3cm 1.2cm; }
* { box-sizing: border-box; }
body { font-family: -apple-system, "Helvetica Neue", Arial, "PingFang SC", "Noto Sans CJK SC", "Microsoft YaHei", sans-serif;
       color: #151a22; font-size: 10.5px; line-height: 1.5; margin: 0; }
a { color: #2f6db0; text-decoration: none; }
.mast { border-bottom: 2px solid ACCENT; padding-bottom: 8px; margin-bottom: 12px; }
.mast h1 { font-size: 19px; font-weight: 700; margin: 0; }
.mast h1 span { color: ACCENT; }
.dateline { margin-top: 4px; font-size: 9.5px; color: #46536b; }
.status { margin-top: 3px; font-size: 8.5px; color: #7a8699; }
.clu { color: ACCENT; font-weight: 600; }
.band { background: #eef3f9; border: 1px solid #d7dee8; border-radius: 8px; padding: 4px 12px 10px; margin-bottom: 12px; }
.band-tag { display: inline-block; background: ACCENT; color: #fff; font-size: 8.5px; font-weight: 700;
            letter-spacing: .1em; padding: 3px 9px; border-radius: 5px; transform: translateY(-9px); }
h2 { font-size: 12px; font-weight: 600; color: ACCENT; margin: 12px 0 5px; border-bottom: 1px solid #e2e8f1; padding-bottom: 3px; }
.band h2:first-of-type { margin-top: 2px; }
.snap { display: flex; gap: 10px; }
.snap-col { flex: 1; background: #fff; border: 1px solid #dde4ee; border-radius: 6px; padding: 7px 9px; }
.snap-col h3 { font-size: 8.5px; letter-spacing: .06em; text-transform: uppercase; color: #7a8699; margin: 0 0 5px; }
.qrow { display: flex; justify-content: space-between; font-size: 9.5px; padding: 1.5px 0; }
.qrow .qn { color: #46536b; }
.q-up { color: #137a55; } .q-down { color: #b23a3a; } .q-flat { color: #7a8699; }
.anom { background: #fff; border: 1px solid #c3ccda; border-left: 3px solid #9c6716; border-radius: 6px;
        padding: 6px 10px; margin: 5px 0; font-size: 10px; }
.bluf { margin: 4px 0; padding-left: 4px; }
.bluf .n { color: ACCENT; font-weight: 700; }
.ev { background: #fff; border: 1px solid #dde4ee; border-left: 3px solid ACCENT; border-radius: 6px;
      padding: 7px 10px; margin: 6px 0; }
.ev.t1 { border-left-color: #9c6716; }
.ev-title { font-weight: 600; font-size: 11px; }
.ev .sec { color: #7a8699; font-size: 9px; }
.ev .tks { font-family: "SF Mono", Menlo, monospace; font-size: 9px; background: #eef2f8; color: #3a465c;
           padding: 1px 5px; border-radius: 3px; margin-left: 4px; }
.ev-sum { color: #46536b; font-size: 9.5px; margin: 3px 0; }
.ev-meta { display: flex; flex-wrap: wrap; gap: 4px 12px; align-items: center; font-size: 9px; margin-top: 3px; }
.score b { color: #7a8699; font-weight: 600; margin-right: 3px; }
.legend { font-size: 8.6pt; color: #6b7684; line-height: 1.5; margin: 4px 0 10px; padding: 5px 8px; background: #f4f6f8; border-left: 2px solid #c8d0d8; }
.age { font-size: 8.2pt; color: #6b7684; }
.stale { font-size: 8.2pt; color: #8d5a1e; font-weight: 600; }
.dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: #cdd6e2; margin-right: 1.5px; }
.dot.on { background: ACCENT; }
.pill { border: 1px solid #c3ccda; border-radius: 10px; padding: 1px 7px; color: #46536b; }
.pill.rel { border-color: #2f6db0; color: #2f6db0; }
.ev-src { border-top: 1px dashed #e2e8f1; margin-top: 5px; padding-top: 4px; font-size: 8.5px; color: #7a8699; }
.ev-src .mono { font-family: "SF Mono", Menlo, monospace; }
.nl { font-size: 9.5px; margin: 3px 0; }
.nl .t { font-weight: 600; } .nl .s { color: #7a8699; font-size: 8.5px; }
ul.plain { margin: 3px 0; padding-left: 16px; } ul.plain li { margin: 2px 0; font-size: 9.5px; }
.lint { font-size: 8.5px; margin-top: 8px; }
.lint.ok { color: #137a55; } .lint.flag { color: #9c6716; }
.foot { margin-top: 8px; padding-top: 6px; border-top: 1px solid #e2e8f1; font-size: 8px; color: #7a8699; }
""".replace("ACCENT", _ACCENT)


def render_brief_html(b) -> str:
    en = _rlang() == "en"
    T = _BRIEF_L[_rlang()]
    title = T["title"] if en else b.title
    P: list[str] = []
    P.append(f'<div class="mast"><h1><span>CIO</span> {_e(title.replace("CIO ", "").replace("CIO", ""))}</h1>')
    P.append(f'<div class="dateline">{_e(T["stamp"].format(bj=b.dt_beijing, ny=b.dt_ny))}</div>')
    st = b.status
    deg = ("; ".join(st.degraded) if en else "；".join(st.degraded)) if st.degraded else T["none_deg"]
    P.append(f'<div class="status">{_e(T["status"].format(f=st.fetched, d=st.deduped, v=st.ingested_vectors, deg=deg))}')
    if b.cluster_stat:
        P.append(f' · <span class="clu">{_e(b.cluster_stat)}</span>')
    P.append("</div></div>")

    # ---- page-one band: snapshot + anomalies + key points ----
    P.append('<div class="band"><span class="band-tag">' + ("PAGE ONE · 30-SECOND READ" if en else "页首 · 30 秒速读") + "</span>")
    # 盘前市场快照。**三个渲染器必须同时有这一节**——
    # md 与 reportlab 有、这里没有，就会出现"同一天两份报告内容不同且都不报错"。
    if getattr(b, "market_snapshot", None):
        P.append('<h2>' + _e("盘前市场快照（此刻 vs 已收盘）" if not en
                             else "Pre-market Snapshot (live vs closed)") + '</h2>')
        if getattr(b, "market_note", ""):
            P.append(f'<div class="legend">{_e(b.market_note)}</div>')
        _mg: dict = {}
        for _t in b.market_snapshot:
            _mg.setdefault(_t.group or "市场", []).append(_t)
        P.append('<div class="snap">')
        for _grp, _ts in _mg.items():
            P.append(f'<div class="snap-col"><h3>{_e(_grp)}</h3>')
            for _t in _ts:
                if _t.last is None:
                    P.append(f'<div class="qrow"><span class="qn">{_e(_t.name)}</span>'
                             f'<span class="stale">{_e(_t.note or "未取到")}</span></div>')
                    continue
                _pc = "—" if _t.change_pct is None else f"{_t.change_pct:+.2f}%"
                _cls = "stale" if _t.stale else ""
                # **原始时间戳必须一起印。** 只印"实时"是一个【结论】，
                # 印上 [08-31 11:02] 才是可核对的【事实】——
                # 读者能自己判断这个"实时"到底有多实时。md 版一直有，HTML 版漏了。
                _ts = f' [{_e(_t.as_of)}]' if _t.as_of else ""
                P.append(f'<div class="qrow"><span class="qn">{_e(_t.name)}</span>'
                         f'<span>{_t.last:,.2f} <b>{_e(_pc)}</b>'
                         f'<span class="age {_cls}">{_ts} {_e(_t.age_label)}'
                         f'{" ⚠" if _t.stale else ""}</span></span></div>')
            P.append('</div>')
        P.append('</div>')

    P.append(f'<h2>{_e(T["h_anchor"])}</h2>')
    groups: dict = {}
    for q in b.anchor:
        groups.setdefault(q.group or ("Indices" if en else "指数"), []).append(q)
    P.append('<div class="snap">')
    for grp, qs in list(groups.items())[:3] or [("", [])]:
        P.append(f'<div class="snap-col"><h3>{_e(grp)}</h3>')
        for q in qs:
            P.append(f'<div class="qrow"><span class="qn">{_e(q.name)}</span><span>{_val(q)} {_pct(q)}</span></div>')
        P.append("</div>")
    P.append("</div>")
    if b.anomalies:
        P.append(f'<h2>{_e(T["h_anom"])}</h2>')
        for a in b.anomalies:
            P.append(f'<div class="anom">{_e(a)}</div>')
    P.append(f'<h2>{_e(T["h_bluf"])}</h2>')
    if b.bluf:
        for i, s in enumerate(b.bluf, 1):
            P.append(f'<div class="bluf"><span class="n">{i}.</span> {_e(s)}</div>')
    else:
        P.append(f"<div class='bluf'>{_e(T['no_bluf'])}</div>")
    P.append("</div>")  # band

    # ---- III. watchlist event cards ----
    P.append(f'<h2>{_e(T["h_watch_sec"])}</h2>')
    if b.watchlist_events:
        for e in b.watchlist_events:
            t1 = " t1" if e.materiality >= 5 else ""
            sec = f'<span class="sec">[{_e(e.sector)}]</span> ' if e.sector else ""
            tks = f'<span class="tks">{_e(", ".join(e.tickers))}</span>' if e.tickers else ""
            P.append(f'<div class="ev{t1}"><div><span class="ev-title">{sec}{_e(e.headline)}</span>{tks}</div>')
            if e.summary and e.summary.strip() and e.summary.strip() != (e.headline or "").strip():
                P.append(f'<div class="ev-sum">{_e(e.summary)}</div>')
            relab = {"Direct": "Direct", "Sector": "Sector"}.get(e.relevance, e.relevance or "—")
            P.append('<div class="ev-meta">'
                     f'<span class="score"><b>C</b>{_dots(e.confidence)}</span>'
                     f'<span class="score"><b>M</b>{_dots(e.materiality)}</span>'
                     f'<span class="pill rel">{_e(relab)}</span>'
                     f'<span class="pill">{_e(e.immediacy or "—")}</span></div>')
            _dfl = "source" if en else "来源"
            link = _one_link(
                e.sources,
                lambda s: (f'<a href="{_e(s.url)}">{_e(source_label(s, e.headline, _dfl))}</a>'
                           if s.url else _e(source_label(s, e.headline, _dfl))),
                member_count=e.member_count)
            sm = _stale_marker(e.published_at)
            sm_html = f'<span style="color:#9c6716;font-weight:700">⚠ {_e(sm)}</span> · ' if sm else ''
            P.append(f'<div class="ev-src"><span class="mono">{_e(e.event_id)}</span> · {sm_html}{link}</div></div>')
        # **四分卡图例必须印在报告里。** md 与 reportlab 早就有，这个渲染器一直没有——
        # 而它才是实际生成 PDF 的那个（run_premarket 优先用它，reportlab 只是兜底）。
        # 结果就是：收到的报告上印着 C●●●●○ M●●●○○ Direct，却没有任何一处解释它们是什么。
        P.append('<div class="legend">' + _e(
            "四分卡图例　C1–5 来源可信度（5=一手来源）｜M1–5 事件重要性｜"
            "Direct 直接命中关注池标的 · Sector 命中所属行业 · — 无关｜"
            "Td 今日 · Wk 本周 · Med 中期 · Bg 背景。均为客观事实，非方向判断。"
            if not en else
            "Scorecard　C1–5 source confidence (5=primary) ｜ M1–5 materiality ｜ "
            "Direct = watchlist name · Sector = its industry · — none ｜ "
            "Td today · Wk this week · Med medium-term · Bg background. Factual, not directional."
        ) + '</div>')
    else:
        P.append(f"<p>{_e(T['none'])}</p>")

    # ---- IV trend / V watch / VI decisions / VII global ----
    def _news_line(n):
        title = n.title_en or n.title_original or n.title_zh
        s = f' — {_e(n.summary_zh)}' if n.summary_zh else ""
        bs = best_source(n.sources)   # 只给一条最佳原文链接
        lbl = source_label(bs, title, "src") if bs else ""
        src = (f'<a href="{_e(bs.url)}">{_e(lbl)}</a>' if (bs and bs.url) else _e(lbl))
        return f'<div class="nl"><span class="t">{_e(title)}</span>{s} <span class="s">{src}</span></div>'

    P.append(f'<h2>{_e(T["h_trend"])}</h2>')
    if b.trend_signals:
        for n in b.trend_signals[:12]:
            P.append(_news_line(n))
    else:
        P.append(f"<p>{_e(T['no_trend'])}</p>")

    P.append(f'<h2>{_e(T["h_ahead"])}</h2><ul class="plain">')
    for w in (b.watch_ahead or [T["no_ahead"]]):
        P.append(f"<li>{_e(w)}</li>")
    P.append("</ul>")

    P.append(f'<h2>{_e(T["h_dec"])}</h2><ul class="plain">')
    for d in (b.decisions or [T["none"]]):
        P.append(f"<li>{_e(d)}</li>")
    P.append("</ul>")

    P.append(f'<h2>{_e(T["h_world"])}</h2>')
    if b.world_top:
        for n in b.world_top[:10]:
            P.append(_news_line(n))
    else:
        P.append(f"<p>{_e(T['no_world'])}</p>")

    # ---- leakage + footer ----
    if b.leakage_flags:
        isep = ", " if en else "、"
        P.append(f'<div class="lint flag">{_e(T["lint_flag"].format(n=len(b.leakage_flags), items=isep.join(b.leakage_flags)))}</div>')
    else:
        P.append(f'<div class="lint ok">{_e(T["lint_ok"])}</div>')
    isep2 = ", " if en else "、"
    if b.fact_flags:
        P.append(f'<div class="lint flag">{_e(T["fact_flag"].format(n=len(b.fact_flags), items=isep2.join(b.fact_flags)))}</div>')
    else:
        P.append(f'<div class="lint ok">{_e(T["fact_ok"])}</div>')
    P.append(f'<div class="foot">{_e(T["footer"])}</div>')

    return f"<!doctype html><html><head><meta charset='utf-8'><style>{_CSS}</style></head><body>{''.join(P)}</body></html>"


def _pdf_via_playwright(html_str: str, path: str) -> str:
    """用 Playwright/Chromium 把 HTML 渲成 PDF——自带浏览器内核，macOS 上比 weasyprint 稳。
    关键：print_background=True（否则导航蓝、色块、圆点全丢）；prefer_css_page_size 让 @page A4 生效。"""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            page = browser.new_page()
            page.set_content(html_str, wait_until="load")
            page.pdf(path=path, print_background=True, prefer_css_page_size=True)
        finally:
            browser.close()
    return "playwright/chromium"


def render_brief_pdf_styled(b, path: str) -> str:
    """把 mockup 风格 HTML 转成 PDF。引擎按稳健度优先：
      1) Playwright/Chromium —— 自带内核，macOS/Apple Silicon 上最稳（推荐）
      2) weasyprint —— 需系统 pango/cairo 原生库；缺库会 OSError
    两者都不可用时抛异常，交由上层回退 reportlab 版式（保证一定出得了 PDF）。
    返回真正生效的引擎名，便于日志显示到底用了哪套。"""
    html_str = render_brief_html(b)
    errors = []
    # 1) Playwright / Chromium（首选）
    try:
        return _pdf_via_playwright(html_str, path)
    except Exception as e:
        errors.append(f"playwright:{type(e).__name__}")
    # 2) weasyprint（备选）
    try:
        from weasyprint import HTML
        HTML(string=html_str).write_pdf(path)
        return "weasyprint"
    except Exception as e:
        errors.append(f"weasyprint:{type(e).__name__}")
    raise RuntimeError("styled-PDF engines unavailable → " + " | ".join(errors))
