"""盈亏表的三种呈现：终端文本 / Markdown 归档 / HTML→PDF。

**三处内容必须一致。** 这个仓库栽过：图例只加进了 md 和 reportlab，
偏偏漏了真正生成 PDF 的那个渲染器，于是她拿到的报告读不懂四分卡。
所以这里只有一份取数与组织逻辑（`_blocks`），三个渲染器都从它出发。

## 每个持仓旁边一定要有的两样

**距上次复审多少天**，和**当初写下的失效条件**。

人工做投资最经常的失败不是算错，是忘了当初为什么买、说好什么时候认错。
这两行几乎零成本，却是整套论点台账真正产生约束力的地方——
其余所有东西都在算数，只有这一条在管人。
"""
from __future__ import annotations

from .utils import get_logger

log = get_logger("cio.render_book")

STALE_REVIEW_DAYS = 10


def _invalidations(symbol: str) -> list:
    try:
        from . import thesis_store
        rows = thesis_store.open_brief(symbol, limit=1)
        return (rows[0]["invalidations"] if rows else [])
    except Exception as e:                                   # noqa: BLE001
        log.info("%s 的失效条件读不到：%s", symbol, e)
        return []


def _pct(v, digits=2):
    return "—" if v is None else f"{v:.{digits}%}"


def _num(v, digits=2):
    return "—" if v is None else f"{v:,.{digits}f}"


def _blocks(st: dict, recon_res: dict = None, ca_res: dict = None) -> dict:
    """三个渲染器共用的**唯一一份**内容组织。"""
    pos = []
    for r in st["positions"]:
        conds = _invalidations(r["ticker"])
        pos.append({**r, "invalidations": conds,
                    "stale": bool(r.get("days_since_review") is not None
                                  and r["days_since_review"] > STALE_REVIEW_DAYS),
                    "date_anomaly": bool((r.get("days_held") or 0) < 0)})
    head = {
        "as_of": st["as_of"], "portfolio_id": st["portfolio_id"],
        "currency": st.get("currency") or "USD",
        "opened_on": st.get("opened_on", ""),
        "initial_capital": st["initial_capital"],
        "cash": st["cash"], "holdings_value": st["holdings_value"],
        "nav": st["nav"], "day_pnl": st["day_pnl"], "cum_return": st["cum_return"],
        "realized_pnl": st["realized_pnl"], "n_trades": st["n_trades"],
        "bench_symbol": st.get("bench_symbol"), "bench_basis": st.get("bench_basis"),
        "bench_cum_return": st.get("bench_cum_return"), "excess": st.get("excess"),
        "invested_pct": st.get("invested_pct"), "complete": st.get("complete"),
        "unpriced": st.get("unpriced") or [], "note": st.get("note", ""),
        "dividends_cash": st.get("dividends_cash", 0.0),
    }
    return {"head": head, "positions": pos,
            "recon": recon_res or {}, "actions": ca_res or {},
            "recent_actions": st.get("recent_actions") or []}


# ---------------------------------------------------------------- 文本
def render_text(st: dict, recon_res: dict = None, ca_res: dict = None) -> str:
    b = _blocks(st, recon_res, ca_res)
    h = b["head"]
    L = [f"盈亏表　{h['portfolio_id']}　{h['as_of']}　{h['currency']}",
         f"{h['opened_on']} 开账　初始 {_num(h['initial_capital'])}"]

    if h["nav"] is None:
        L.append(f"NAV **不可计算** —— {h['note']}")
        L.append(f"现金 {_num(h['cash'])}　缺价 {len(h['unpriced'])} 只："
                 + "、".join(h["unpriced"]))
    else:
        L.append(f"现金 {_num(h['cash'])}　持仓市值 {_num(h['holdings_value'])}　"
                 f"NAV **{_num(h['nav'])}**")
        L.append(f"累计收益 {_pct(h['cum_return'])}　"
                 + (f"当日 {_num(h['day_pnl'])}" if h["day_pnl"] is not None
                    else "当日盈亏 不计算")
                 + f"　已实现 {_num(h['realized_pnl'])}　成交 {h['n_trades']} 笔")
        if h["note"]:
            L.append(f"  （{h['note']}）")

    # **超额收益永远和平均仓位一起出现。**
    if h["bench_cum_return"] is not None:
        L.append(f"基准 {h['bench_symbol']}（{h['bench_basis']}）"
                 f"累计 {_pct(h['bench_cum_return'])}　"
                 f"超额 {_pct(h['excess'])}"
                 f"　← 当前仓位 {_pct(h['invested_pct'])}")
        L.append("  （仓位远低于 100% 时，和满仓基准比出来的超额不说明选股能力，"
                 "只说明没投出去。）")
    else:
        L.append(f"基准 {h['bench_symbol']}：本轮取不到 —— 超额**不计算**"
                 f"（不用价格收益顶替含息总回报）")
    if h["dividends_cash"]:
        L.append(f"公司行为累计入账现金 {_num(h['dividends_cash'])}")

    L.append("")
    if not b["positions"]:
        L.append("持仓：无。")
    for r in b["positions"]:
        L.append(f"{r['ticker']:<6} {r['shares']:>6} 股　成本 {_num(r['avg_cost'])}　"
                 + (f"现价 {_num(r['close'])}　市值 {_num(r['market_value'])}　"
                    f"浮盈 {_num(r['unrealized_pnl'])}（{_pct(r['unrealized_pct'])}）　"
                    f"权重 {_pct(r['weight'])}"
                    if r["priced"] else "**取不到价**"))
        # 负天数 = 建仓日晚于盯市日。**印成"持有 -1 天"就是把一处数据矛盾
        # 伪装成一个普通数字**；要么说清楚，要么别印。
        held = (f"持有 {r['days_held']} 天" if (r["days_held"] or 0) >= 0
                else f"⚠ 建仓日 {r['opened_on']} 晚于盯市日 —— 数据矛盾")
        since = ""
        if r["days_since_review"] is not None and r["days_since_review"] >= 0:
            since = f"（{r['days_since_review']} 天前）"
        L.append(f"       {held}　"
                 f"上次复审 {r['last_evaluated_on'] or '（无记录）'}{since}")
        if r["stale"]:
            L.append(f"       ⚠ 已 {r['days_since_review']} 天未复审")
        if r["invalidations"]:
            L.append("       当初写下的失效条件：")
            for c in r["invalidations"][:4]:
                L.append(f"         · {c}")
        else:
            L.append("       ⚠ 没有记录失效条件 —— 无法判断该在什么情况下认错")

    if b["actions"] and (b["actions"].get("applied") or b["actions"].get("blocked")):
        from . import corp_actions
        L += ["", corp_actions.render(b["actions"])]
    if b["recon"]:
        from . import recon
        L += ["", recon.render(b["recon"])]
    return "\n".join(L)


# ---------------------------------------------------------------- Markdown
def render_md(st: dict, recon_res: dict = None, ca_res: dict = None) -> str:
    b = _blocks(st, recon_res, ca_res)
    h = b["head"]
    L = [f"# 盈亏表 {h['portfolio_id']} · {h['as_of']}", "",
         f"- 开账 {h['opened_on']}，初始 {_num(h['initial_capital'])} {h['currency']}",
         f"- 现金 {_num(h['cash'])}｜持仓市值 {_num(h['holdings_value'])}｜"
         f"NAV {_num(h['nav'])}",
         f"- 累计收益 {_pct(h['cum_return'])}｜已实现 {_num(h['realized_pnl'])}｜"
         f"成交 {h['n_trades']} 笔",
         f"- 基准 {h['bench_symbol']}（{h['bench_basis']}）"
         f"{_pct(h['bench_cum_return'])}｜超额 {_pct(h['excess'])}｜"
         f"当前仓位 {_pct(h['invested_pct'])}", ""]
    if h["note"]:
        L += [f"> {h['note']}", ""]
    L += ["| 标的 | 股数 | 成本 | 现价 | 市值 | 浮盈 | 权重 | 持有 | 距复审 |",
          "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for r in b["positions"]:
        L.append(f"| {r['ticker']} | {r['shares']} | {_num(r['avg_cost'])} | "
                 f"{_num(r['close'])} | {_num(r['market_value'])} | "
                 f"{_num(r['unrealized_pnl'])} | {_pct(r['weight'])} | "
                 + (f"{r['days_held']} 天 | " if not r["date_anomaly"]
                    else "⚠日期矛盾 | ")
                 + ("— |" if (r["days_since_review"] is None or r["date_anomaly"])
                    else f"{r['days_since_review']} 天 |"))
    L.append("")
    for r in b["positions"]:
        if r["invalidations"]:
            L.append(f"**{r['ticker']} 的失效条件**")
            L += [f"- {c}" for c in r["invalidations"][:4]]
            L.append("")
    if b["recon"]:
        from . import recon
        L += ["## 对账", "", "```", recon.render(b["recon"]), "```", ""]
    return "\n".join(L)


# ---------------------------------------------------------------- HTML → PDF
_CSS = """
@page { size: A4; margin: 16mm 14mm; }
body { font-family: -apple-system, "PingFang SC", "Helvetica Neue", sans-serif;
       color:#1a1a1a; font-size: 10.5pt; line-height: 1.5; }
h1 { font-size: 16pt; margin: 0 0 2mm; }
.sub { color:#666; font-size: 9pt; margin-bottom: 5mm; }
.kpi { display:flex; gap:6mm; margin-bottom:5mm; flex-wrap:wrap; }
.kpi div { background:#f4f6f8; border-radius:3mm; padding:3mm 4mm; min-width:32mm; }
.kpi b { display:block; font-size:13pt; }
.kpi span { color:#666; font-size:8.5pt; }
table { width:100%; border-collapse:collapse; margin: 3mm 0 5mm; font-size:9.5pt; }
th,td { border-bottom:1px solid #e3e6e9; padding:2mm 1.5mm; text-align:right; }
th:first-child, td:first-child { text-align:left; }
th { background:#f4f6f8; font-weight:600; }
.warn { color:#b3261e; }
.note { background:#fff6e5; border-left:3px solid #e8a33d; padding:3mm 4mm;
        margin:3mm 0; font-size:9.5pt; }
.ok { background:#eef7ee; border-left:3px solid #4a8f4a; padding:3mm 4mm;
      margin:3mm 0; font-size:9.5pt; }
.inv { font-size:9pt; color:#444; margin:1mm 0 3mm 3mm; }
h2 { font-size:12pt; margin:6mm 0 2mm; }
pre { background:#f7f8f9; padding:3mm; font-size:8.5pt; white-space:pre-wrap; }
"""


def _e(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def render_html(st: dict, recon_res: dict = None, ca_res: dict = None) -> str:
    b = _blocks(st, recon_res, ca_res)
    h = b["head"]
    kpi = [("NAV", _num(h["nav"])), ("累计收益", _pct(h["cum_return"])),
           ("当前仓位", _pct(h["invested_pct"])),
           ("现金", _num(h["cash"])), ("已实现", _num(h["realized_pnl"]))]
    P = [f"<html><head><meta charset='utf-8'><style>{_CSS}</style></head><body>",
         f"<h1>盈亏表 · {_e(h['portfolio_id'])}</h1>",
         f"<div class='sub'>{_e(h['as_of'])}　{_e(h['currency'])}　"
         f"{_e(h['opened_on'])} 开账，初始 {_num(h['initial_capital'])}</div>",
         "<div class='kpi'>"
         + "".join(f"<div><b>{v}</b><span>{k}</span></div>" for k, v in kpi)
         + "</div>"]
    if h["note"]:
        P.append(f"<div class='note'>{_e(h['note'])}</div>")
    if h["bench_cum_return"] is not None:
        P.append(f"<div class='note'>基准 {_e(h['bench_symbol'])}"
                 f"（{_e(h['bench_basis'])}）累计 {_pct(h['bench_cum_return'])}，"
                 f"超额 {_pct(h['excess'])}。<br>"
                 f"当前仓位只有 {_pct(h['invested_pct'])} —— "
                 f"和满仓基准比出来的超额不说明选股能力，只说明没投出去。</div>")
    else:
        P.append("<div class='note'>基准本轮取不到，超额<b>不计算</b>"
                 "（不用价格收益顶替含息总回报）。</div>")

    P.append("<table><tr><th>标的</th><th>股数</th><th>成本</th><th>现价</th>"
             "<th>市值</th><th>浮盈</th><th>权重</th><th>持有</th><th>距复审</th></tr>")
    for r in b["positions"]:
        cls = " class='warn'" if r["stale"] else ""
        held = (f"{r['days_held']} 天" if not r["date_anomaly"]
                else "<span class='warn'>日期矛盾</span>")
        rev = ("—" if (r["days_since_review"] is None or r["date_anomaly"])
               else f"{r['days_since_review']} 天")
        P.append(f"<tr><td>{_e(r['ticker'])}</td><td>{r['shares']}</td>"
                 f"<td>{_num(r['avg_cost'])}</td><td>{_num(r['close'])}</td>"
                 f"<td>{_num(r['market_value'])}</td>"
                 f"<td>{_num(r['unrealized_pnl'])}</td>"
                 f"<td>{_pct(r['weight'])}</td><td>{held}</td>"
                 f"<td{cls}>{rev}</td></tr>")
    P.append("</table>")

    for r in b["positions"]:
        if r["invalidations"]:
            P.append(f"<div class='inv'><b>{_e(r['ticker'])} 当初写下的失效条件</b><br>"
                     + "<br>".join("· " + _e(c) for c in r["invalidations"][:4])
                     + "</div>")
        else:
            P.append(f"<div class='inv warn'>{_e(r['ticker'])}：没有记录失效条件 —— "
                     f"无法判断该在什么情况下认错</div>")

    if b["actions"] and (b["actions"].get("applied") or b["actions"].get("blocked")):
        from . import corp_actions
        P.append("<h2>公司行为</h2><pre>"
                 + _e(corp_actions.render(b["actions"])) + "</pre>")
    if b["recon"]:
        from . import recon
        cls = "ok" if b["recon"].get("status") == recon.PASS else "note"
        P.append(f"<h2>每日对账</h2><div class='{cls}'><pre>"
                 + _e(recon.render(b["recon"])) + "</pre></div>")
    P.append("</body></html>")
    return "\n".join(P)


def render_pdf(st: dict, path: str, recon_res: dict = None,
               ca_res: dict = None) -> str:
    """HTML → PDF。引擎不可用时抛，由上层决定是否只留 md。"""
    from .render_html import _pdf_via_playwright
    html = render_html(st, recon_res, ca_res)
    try:
        return _pdf_via_playwright(html, path)
    except Exception as e:                                   # noqa: BLE001
        try:
            from weasyprint import HTML
            HTML(string=html).write_pdf(path)
            return "weasyprint"
        except Exception as e2:                              # noqa: BLE001
            raise RuntimeError(f"PDF 引擎不可用：playwright:{type(e).__name__} | "
                               f"weasyprint:{type(e2).__name__}")
