"""CIO 投研 Agent · 演示界面（Shiny for Python）。

    cd ui && shiny run app.py --port 8000 --reload

**这个界面不做任何投资判断。** 它只做三件事：起进程、读契约、画出来。
所有档位、上限、封顶、绑定项都由引擎算好后原样交出——
页面里没有一句 `if THIN: cap = "弱"`，也不该有。
"""
from __future__ import annotations

import html
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import engine                                    # noqa: E402
from shiny import App, reactive, render, ui      # noqa: E402

DEFAULT_WATCHLIST = "NVDA AVGO AMD MU TSM AMAT LRCX ARM MRVL QCOM"

GATE_STYLE = {
    "SUFFICIENT": ("gate-ok", "材料充分"),
    "THIN": ("gate-thin", "材料偏薄"),
    "INSUFFICIENT": ("gate-no", "无实质材料"),
    "UNRECORDED": ("gate-unk", "闸门未跑过"),
}

CSS = """
:root{--ink:#12202e;--ink2:#516274;--rule:#dbe3ea;--paper:#fff;--ground:#f4f7fa;
--ok:#1a6b4a;--okbg:#e3f2ea;--thin:#8a6410;--thinbg:#fbf1da;
--no:#8d3a22;--nobg:#f8e7e1;--unk:#4a5666;--unkbg:#eaeef2;--accent:#1b4f80;}
body{background:var(--ground);color:var(--ink);
 font-family:"Noto Sans SC","PingFang SC",system-ui,sans-serif;}
/* 自己的容器类。**不要叫 card** —— Bootstrap 的 .card 是
   display:flex;flex-direction:column，直接子元素会被拉成整行，
   行内的 pill 于是变成一条占满宽度的色条。撞类名不会报错，只会长歪。 */
.panel{border:1px solid var(--rule);background:var(--paper);border-radius:4px;}
h5,h6{font-weight:700;}
.mono{font-family:ui-monospace,"SF Mono",Menlo,monospace;font-variant-numeric:tabular-nums;}
.small-dim{color:var(--ink2);font-size:.86rem;}
.pill{display:inline-block;padding:1px 8px;border-radius:3px;font-size:.75rem;
 font-weight:700;font-family:ui-monospace,monospace;}
.gate-ok{background:var(--okbg);color:var(--ok);}
.gate-thin{background:var(--thinbg);color:var(--thin);}
.gate-no{background:var(--nobg);color:var(--no);}
.gate-unk{background:var(--unkbg);color:var(--unk);}
.scan-row{display:flex;align-items:center;gap:.75rem;padding:.55rem .2rem;
 border-bottom:1px solid var(--rule);}
.scan-row:last-child{border-bottom:none;}
.sym{font-family:ui-monospace,monospace;font-weight:700;width:5.5rem;}
.stage{display:flex;align-items:center;gap:.6rem;padding:.22rem 0;}
.dot{width:.85rem;text-align:center;font-family:ui-monospace,monospace;}
.st-done{color:var(--ok);} .st-run{color:var(--accent);font-weight:700;}
.st-wait{color:#a9b4bf;} .st-skip{color:#a9b4bf;text-decoration:line-through;}
.silence{background:var(--paper);border:1px solid var(--rule);border-left:4px solid var(--accent);
 padding:1rem 1.2rem;}
.silence .big{font-size:1.6rem;font-weight:700;font-family:ui-monospace,monospace;}
.warnbox{background:var(--thinbg);border-left:4px solid var(--thin);padding:.7rem 1rem;
 font-size:.88rem;}
.matline{border-bottom:1px dotted var(--rule);padding:.35rem 0;font-size:.88rem;}
table.kv{width:100%;font-size:.9rem;} table.kv td{padding:.3rem .5rem;border-bottom:1px solid var(--rule);}
table.kv td:first-child{color:var(--ink2);width:16rem;}
pre.logbox{background:#0e1620;color:#cfdae6;padding:.8rem;font-size:.76rem;
 max-height:16rem;overflow:auto;}
.bind{background:#e7eef6;color:var(--accent);padding:1px 7px;border-radius:3px;
 font-family:ui-monospace,monospace;font-size:.78rem;font-weight:700;}
"""


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def pct(v, nd=2) -> str:
    return "未评估" if v is None else f"{float(v) * 100:.{nd}f}%"


def num(v, nd=2) -> str:
    return "未评估" if v is None else f"{float(v):.{nd}f}"


app_ui = ui.page_sidebar(
    ui.sidebar(
        ui.h6("观察池"),
        ui.input_text_area("watchlist", None, value=DEFAULT_WATCHLIST, rows=4),
        ui.input_action_button("btn_scan", "① 扫描证据", class_="btn-primary w-100"),
        ui.hr(),
        ui.h6("深度研究"),
        ui.output_ui("research_picker"),
        ui.input_checkbox("force", "强制复研（无新证据时仍启动）", False),
        ui.input_action_button("btn_research", "② 研究选中标的", class_="btn-primary w-100"),
        ui.hr(),
        ui.input_action_button("btn_pc", "③ 重算组合仓位", class_="btn-primary w-100"),
        ui.hr(),
        ui.output_ui("engine_box"),
        width=330, title="CIO 投研 Agent",
    ),
    ui.navset_tab(
        ui.nav_panel("证据扫描", ui.output_ui("scan_view")),
        ui.nav_panel("研究进度", ui.output_ui("progress_view")),
        ui.nav_panel("一部观点", ui.output_ui("tab_research")),
        ui.nav_panel("二部测量", ui.output_ui("tab_quant")),
        ui.nav_panel("CRO 风险", ui.output_ui("tab_risk")),
        ui.nav_panel("PC 仓位", ui.output_ui("tab_position")),
        ui.nav_panel("历史归因", ui.output_ui("tab_stats")),
        id="tabs",
    ),
    ui.tags.style(CSS),
    title="CIO 投研 Agent · 演示",
    fillable=False,
)


def server(input, output, session):
    job_scan = reactive.value("")
    job_res = reactive.value("")
    job_pc = reactive.value("")
    stats_txt = reactive.value("")

    # ---------------------------------------------------------------- 触发
    @reactive.effect
    @reactive.event(input.btn_scan)
    def _scan():
        syms = [s for s in input.watchlist().replace(",", " ").split() if s]
        if syms:
            job_scan.set(engine.scan(syms))
            ui.update_navset("tabs", selected="证据扫描")

    @reactive.effect
    @reactive.event(input.btn_research)
    def _research():
        sym = (input.pick() or "").strip() if hasattr(input, "pick") else ""
        if sym:
            job_res.set(engine.research(sym, force=bool(input.force())))
            ui.update_navset("tabs", selected="研究进度")

    @reactive.effect
    @reactive.event(input.btn_pc)
    def _pc():
        job_pc.set(engine.portfolio())
        stats_txt.set("")
        ui.update_navset("tabs", selected="PC 仓位")

    # ---------------------------------------------------------------- 侧栏
    @render.ui
    def engine_box():
        i = engine.engine_info()
        cls = "gate-ok" if i["ok"] else "gate-no"
        return ui.HTML(
            f'<div class="small-dim">引擎 <span class="pill {cls}">'
            f'{"已连接" if i["ok"] else "找不到"}</span><br>'
            f'<span class="mono">{esc(i["home"])}</span><br>'
            f'<span class="mono">{esc(i["python"])}</span></div>'
            '<div class="small-dim" style="margin-top:.6rem">'
            '本界面不做任何投资判断：只起进程、读契约、画结果。</div>')

    @render.ui
    def research_picker():
        # **必须跟着任务状态刷新。** job_scan 的值在任务【开始】时就定了，
        # 若不轮询，这个下拉框会永远停在"扫描前"的样子——
        # 页面上表现为"扫过了但选不到那几只"，而不会有任何报错。
        j = engine.get(job_scan())
        if j.get("status") == "running":
            reactive.invalidate_later(0.8)
        res = (j.get("result") or {}) if j else {}
        rows = res.get("rows") or []
        good = [r["symbol"] for r in rows if r.get("activate")]
        allsym = [r["symbol"] for r in rows]
        wl = [s for s in input.watchlist().replace(",", " ").split() if s]
        # **勾了强制复研，候选池必须放宽到全部。** 否则闸门拦下的那几只根本
        # 选不到——而 --force 存在的意义恰恰就是研究被拦下的标的
        # （首次建仓、季度复审、论点到期）。只给"闸门放行的"是把这个功能锁死了。
        if input.force():
            pool = allsym or wl
            note = ('<div class="small-dim">强制复研：候选池已放宽到全部扫描标的。'
                    '<b>报告会标明依据的是既有证据集，不是新证据</b>，'
                    '且 Evidence Gate 仍为 INSUFFICIENT——PC 依然不会给仓位。</div>')
        else:
            pool = good or wl
            note = ("" if good else
                    '<div class="small-dim">（尚无扫描结果，或今天没有一只有实质材料）</div>')
        return ui.TagList(ui.input_select("pick", None, {s: s for s in pool} or {"": "—"}),
                          ui.HTML(note))

    # ---------------------------------------------------------------- 扫描
    @render.ui
    def scan_view():
        jid = job_scan()
        if not jid:
            return ui.HTML('<p class="small-dim">点左侧「扫描证据」开始。'
                           '这一步一次模型都不调，只做采集 + 实质度判定。</p>')
        j = engine.get(jid)
        if j.get("status") == "running":
            reactive.invalidate_later(0.8)
            return ui.HTML(f'<p>扫描中… <span class="mono">{engine.elapsed(jid):.0f}s</span>'
                           f'</p><p class="small-dim">正在逐只采集新闻并过闸门。</p>')
        if j.get("status") == "failed":
            return _fail_box(j)

        d = j["result"]
        rows = d.get("rows") or []
        n_mat, n_sub = d.get("n_materials", 0), d.get("n_substantive", 0)
        sil = len([r for r in rows if not r.get("activate")])

        parts = [
            '<div class="silence"><div class="big">'
            f'{n_sub} / {n_mat}</div>'
            f'<div>今天扫了 <b>{len(rows)}</b> 只，'
            f'共 {n_mat} 条材料，其中<b>实质 {n_sub} 条</b>'
            + (f"（{n_sub / n_mat:.0%}）" if n_mat else "") + '。<br>'
            f'<b>{sil}</b> 只系统选择不研究——'
            '<b>没有新的可解释信息，就不制造新的观点。</b></div></div>']

        dead = d.get("dead_feeds") or {}
        if dead:
            items = "、".join(f'{esc(k)}（{esc(v["error"])}）' for k, v in dead.items())
            parts.append(f'<div class="warnbox" style="margin-top:.8rem">'
                         f'⚠ 本次跳过的信息源：{items}。'
                         f'<b>它们的缺席会伪装成「今天没新闻」</b>，所以必须报出来。</div>')

        parts.append('<div class="panel" style="margin-top:1rem;padding:.6rem 1rem">')
        for r in rows:
            cls, _lbl = GATE_STYLE.get(r.get("level"), ("gate-unk", "?"))
            cap = (f'<span class="small-dim">信心将封顶为「{esc(r["conviction_cap"])}」</span>'
                   if r.get("conviction_cap") else "")
            err = (f'<span class="small-dim">采集失败：{esc(r["error"])}</span>'
                   if r.get("error") else "")
            parts.append(
                f'<div class="scan-row"><span class="sym">{esc(r["symbol"])}</span>'
                f'<span class="pill {cls}">{esc(r.get("level"))}</span>'
                f'<span>{esc(r.get("verdict"))}</span>'
                f'<span class="small-dim mono">实质 {r.get("n_sub")}/{r.get("n")}</span>'
                f'{cap}{err}</div>')
        parts.append("</div>")

        with_items = [r for r in rows if r.get("items")]
        if with_items:
            acc = []
            for r in with_items:
                lines = "".join(
                    f'<div class="matline"><span class="pill gate-unk">{esc(t)}</span> '
                    f'<span class="small-dim">{esc(why)}</span><br>{esc(txt)}</div>'
                    for _i, t, why, txt in r["items"])
                acc.append(ui.accordion_panel(
                    f'{r["symbol"]}　{r["verdict"]}（{r["n_sub"]}/{r["n"]}）',
                    ui.HTML(lines)))
            return ui.TagList(ui.HTML("".join(parts)),
                              ui.h6("逐条材料判定（判错了当场就能看见）",
                                    style="margin-top:1.2rem"),
                              ui.accordion(*acc, open=False))
        return ui.HTML("".join(parts))

    # ---------------------------------------------------------------- 进度
    @render.ui
    def progress_view():
        jid = job_res()
        if not jid:
            return ui.HTML('<p class="small-dim">在左侧选一只标的，点「研究选中标的」。'
                           '完整一部约 3–4 分钟，六次本地模型调用。</p>')
        j = engine.get(jid)
        seen = j.get("seen") or set()
        blocked = "gate_blocked" in seen
        expected = engine.UNIT_A_BLOCKED_STAGES if blocked else engine.UNIT_A_STAGES
        detail = {s["name"]: s["detail"] for s in j.get("stages") or []}
        done_all = j.get("status") != "running"

        rows = []
        cur_found = False
        for name, label in expected:
            if name in seen:
                mark, cls = "✓", "st-done"
            elif not cur_found and not done_all:
                mark, cls, cur_found = "●", "st-run", True
            else:
                mark, cls = "○", "st-wait" if not done_all else "st-skip"
            d = detail.get(name, "")
            if d.strip() == label:           # 明细和标签一样就不重复印
                d = ""
            rows.append(f'<div class="stage"><span class="dot {cls}">{mark}</span>'
                        f'<span class="{cls}">{esc(label)}</span>'
                        + (f'<span class="small-dim mono">{esc(d)}</span>' if d else "")
                        + "</div>")

        head = (f'<div class="small-dim">{esc(j.get("label"))}　'
                f'run_id <span class="mono">{esc(j.get("run_id") or "…")}</span>　'
                f'<span class="mono">{engine.elapsed(jid):.0f}s</span></div>')
        body = f'<div class="panel" style="padding:.9rem 1.2rem;margin-top:.6rem">{"".join(rows)}</div>'

        tail = ""
        if blocked and done_all:
            tail = ('<div class="warnbox" style="margin-top:1rem">'
                    '<b>一部主动弃权，未产生新观点。</b>本轮采集到的材料没有一条含增量事实——'
                    '这不是故障，是设计：没有新的可解释信息，就不制造新的观点。'
                    '量化面板照常产出，见「二部测量」。</div>')
        elif j.get("status") == "failed":
            return ui.TagList(ui.HTML(head + body), _fail_box(j))
        if j.get("status") == "running":
            reactive.invalidate_later(0.8)
        return ui.HTML(head + body + tail)

    # ---------------------------------------------------------------- 四个 Tab
    def _advice():
        j = engine.get(job_res())
        return (j.get("result") or {}) if j else {}

    @render.ui
    def tab_research():
        d = _advice()
        if not d:
            return _empty("还没有研究结果。")
        if d.get("status") == "gate_blocked" or not d.get("activated", True):
            return ui.HTML(
                '<div class="warnbox"><b>Unit A not activated — no substantive new evidence.</b>'
                '<br>Formal vote: ABSTAIN　'
                f'（{esc(d.get("material_verdict"))}：{d.get("material_count", 0)} 条材料，'
                f'实质 {d.get("material_substantive", 0)} 条）</div>'
                f'<p class="small-dim" style="margin-top:1rem">{esc(d.get("material_banner"))}</p>')
        kv = [("方向", d.get("direction")), ("信心", d.get("conviction")),
              ("Evidence Gate", d.get("gate_level")),
              ("材料判定", f'{d.get("material_verdict")}（实质 '
                            f'{d.get("material_substantive")}/{d.get("material_count")}）'),
              ("本地模型调用", f'{d.get("llm_calls")} 次'),
              ("未核实论据", f'{d.get("unverified_count")} 条'),
              ("论点台账 ID", f'#{d.get("thesis_id")}')]
        if d.get("conviction_capped"):
            kv.append(("信心封顶", f'由「{d["conviction_capped"]}」封顶为「{d.get("conviction")}」'))
        if d.get("forced"):
            kv.append(("强制复研", "是——依据的是既有证据集，不是新证据"))
        table = "".join(f"<tr><td>{esc(k)}</td><td><b>{esc(v)}</b></td></tr>" for k, v in kv)
        secs = [("多头论据", d.get("bull_case")), ("空头论据", d.get("bear_case")),
                ("多头反驳", d.get("bull_rebuttal")), ("空头反驳", d.get("bear_rebuttal")),
                ("裁判论证审计", d.get("audit")), ("一部综合观点", d.get("synthesis"))]
        acc = [ui.accordion_panel(t, ui.HTML(f'<div style="white-space:pre-wrap">{esc(v)}</div>'))
               for t, v in secs if v]
        lists = ""
        for t, key in (("催化剂（什么会证实它）", "catalysts"),
                       ("失效条件（什么一旦发生论点即失效）", "invalidations")):
            it = d.get(key) or []
            lists += (f'<h6 style="margin-top:1rem">{t}</h6>'
                      + ("".join(f'<div class="matline">{esc(x)}</div>' for x in it)
                         if it else '<div class="small-dim">（无）</div>'))
        if d.get("market_only_invalidations"):
            lists += ('<div class="warnbox" style="margin-top:.8rem">'
                      '以下失效条件只引用了股价/风险统计量——<b>股价下跌不证明论点错</b>，'
                      '对逆向或长期论点，那可能恰恰是它最成立的时候：<br>'
                      + "；".join(esc(x) for x in d["market_only_invalidations"]) + '</div>')
        return ui.TagList(ui.HTML(f'<table class="kv">{table}</table>{lists}'),
                          ui.h6("辩论全文", style="margin-top:1.2rem"),
                          ui.accordion(*acc, open=False) if acc else ui.HTML(""))

    @render.ui
    def tab_quant():
        d = _advice()
        if not d:
            return _empty("还没有研究结果。量化面板随一部运行一起产出。")
        txt = d.get("panel_text") or ""
        if not txt:
            return _empty("这次运行没有面板文本。")
        return ui.TagList(
            ui.HTML('<p class="small-dim">这是<b>确定性生成</b>的固定口径面板，'
                    '多空双方拿到的是同一张表的全部内容——'
                    '这是不让模型从几百个指标里挑对自己有利那几个的唯一办法。'
                    '标「无数据」的项目表示确实没有，不是 0。</p>'),
            ui.HTML(f'<div class="panel" style="padding:1rem;white-space:pre-wrap;'
                    f'font-size:.87rem">{esc(txt)}</div>'))

    def _pc_result():
        j = engine.get(job_pc())
        return (j, (j.get("result") or {}) if j else {})

    @render.ui
    def tab_risk():
        j, d = _pc_result()
        if not j:
            return _empty("点左侧「重算组合仓位」。CRO 的判断全部由政策阈值编码，零模型调用。")
        if j.get("status") == "running":
            reactive.invalidate_later(0.8)
            return ui.HTML("<p>CRO 审查中…</p>")
        if j.get("status") == "failed":
            return _fail_box(j)
        pos = d.get("positions") or []
        if not pos:
            return _empty(f'本轮无候选（{esc(d.get("note") or d.get("status"))}）。')
        blocks = []
        for p in pos:
            m = p.get("measures") or {}
            head = (f'<h6>{esc(p["ticker"])}　'
                    f'<span class="small-dim">{esc(p.get("direction"))}｜'
                    f'{esc(p.get("conviction"))}｜Gate {esc(p.get("evidence_gate"))}</span></h6>')
            meas = (f'<table class="kv"><tr><td>已实现波动率 σ60</td><td class="mono">'
                    f'{pct(m.get("sigma_60"))}</td></tr>'
                    f'<tr><td>年化波动率 σ252</td><td class="mono">{pct(m.get("sigma_252"))}</td></tr>'
                    f'<tr><td>Beta（250 日）</td><td class="mono">{num(m.get("beta"))}</td></tr>'
                    f'<tr><td>近一年最大回撤</td><td class="mono">{pct(m.get("maxdd"))}</td></tr>'
                    f'<tr><td>风险预算（已调）</td><td class="mono">'
                    f'{pct(p.get("adjusted_risk_budget"), 3)}</td></tr></table>')
            flags = "".join(f'<div class="matline">⚠ {esc(x)}</div>'
                            for x in (p.get("risk_constraints") or []))
            notes = "".join(f'<div class="matline small-dim">{esc(x)}</div>'
                            for x in (p.get("notes") or []))
            veto = (f'<div class="warnbox" style="margin:.6rem 0">⛔ <b>CRO 否决</b>：'
                    f'{esc(p.get("veto_reason"))}</div>' if p.get("veto") else "")
            ne = p.get("caps_not_evaluated") or []
            neb = (f'<div class="warnbox" style="margin-top:.6rem">⚠ 未评估的上限：'
                   f'{"、".join(esc(x) for x in ne)}——<b>未评估不等于无上限</b></div>' if ne else "")
            blocks.append(f'<div class="panel" style="padding:1rem;margin-bottom:1rem">'
                          f'{head}{veto}{meas}{flags}{notes}{neb}</div>')
        return ui.TagList(
            ui.HTML('<p class="small-dim">CRO <b>拿不到一部的多空论述原文</b>——'
                    '函数签名里就没有这个参数。它假设观点成立，然后研究后果。'
                    '它给约束与否决，<b>不给仓位</b>。</p>'),
            ui.HTML("".join(blocks)))

    @render.ui
    def tab_position():
        j, d = _pc_result()
        if not j:
            return _empty("点左侧「重算组合仓位」。")
        if j.get("status") == "running":
            reactive.invalidate_later(0.8)
            return ui.HTML("<p>PC 计算中…</p>")
        if j.get("status") == "failed":
            return _fail_box(j)
        pos = d.get("positions") or []
        rg = d.get("regime") or {}
        head = (f'<div class="small-dim">as-of <span class="mono">{esc(d.get("as_of"))}</span>　'
                f'组合 <span class="mono">{esc(d.get("portfolio_id"))}</span>　'
                f'regime <b>{esc(rg.get("regime"))}</b>（{esc(rg.get("note"))}）　'
                f'run_id <span class="mono">{esc(d.get("run_id"))}</span></div>')
        if not pos:
            return ui.TagList(ui.HTML(head), _empty(
                f'本轮无候选：{esc(d.get("note") or "论点台账里没有仍 OPEN 的观点")}。'
                '这是系统最常见的正常状态，不是故障。'))
        rows = []
        for p in pos:
            if p.get("w_final") is None:
                rows.append(
                    f'<div class="panel" style="padding:.9rem 1rem;margin-bottom:.7rem">'
                    f'<b class="mono">{esc(p["ticker"])}</b>　'
                    f'<span class="pill gate-no">无仓位</span><br>'
                    f'<span class="small-dim">{esc(p.get("reason"))}</span></div>')
                continue
            bind = "＋".join(esc(b) for b in (p.get("binding_position_constraint") or []))
            sb = "＋".join(esc(b) for b in (p.get("sigma_binding_component") or []))
            rows.append(
                f'<div class="panel" style="padding:.9rem 1rem;margin-bottom:.7rem">'
                f'<b class="mono">{esc(p["ticker"])}</b>　'
                f'<span class="pill gate-ok mono">{pct(p.get("w_final"))}</span>　'
                f'<span class="small-dim">σ_eff {pct(p.get("sigma_effective"))}'
                f'（绑定 {sb}）　w_raw {pct(p.get("w_raw"))}</span><br>'
                f'<span class="small-dim">仓位由谁决定：</span> <span class="bind">{bind}</span>'
                f'</div>')
        tot = d.get("total_weight") or 0.0
        foot = (f'<div class="silence" style="margin-top:1rem">'
                f'<div class="big">{tot * 100:.2f}%　/　现金 {(d.get("cash_residual") or 0) * 100:.2f}%</div>'
                f'<div><b>不归一化到 100%。</b>残差就是现金——'
                f'归一化会把风险规则刚压下去的仓位重新吹回来，整套风险预算白做。</div></div>'
                f'<p class="small-dim" style="margin-top:.8rem">'
                f'CRO 给约束，PC 给权重，两者都不判断论点对错。执行与否由 CEO 决定。</p>')
        return ui.HTML(head + "".join(rows) + foot)

    # ---------------------------------------------------------------- 归因
    @reactive.effect
    @reactive.event(input.tabs)
    def _load_stats():
        if input.tabs() == "历史归因" and not stats_txt():
            stats_txt.set(engine.stats().get("text", ""))

    @render.ui
    def tab_stats():
        t = stats_txt()
        if not t:
            return _empty("读取中…（这一步只查库，毫秒级）")
        return ui.TagList(
            ui.HTML('<p class="small-dim">'
                    '<b>「这个仓位是被谁决定的？」</b>——这个问题在任何持仓表里都答不出来，'
                    '因为那些仓位长得一模一样。lineage 在决策当时把输入存下来，才有得可查。</p>'),
            ui.HTML(f'<pre class="logbox">{esc(t)}</pre>'))

    # ---------------------------------------------------------------- 公共件
    def _empty(msg):
        return ui.HTML(f'<p class="small-dim">{msg}</p>')

    def _fail_box(j):
        log = "\n".join((j.get("log") or [])[-40:])
        raw = ((j.get("result") or {}).get("_raw_stdout") or "")[:1500]
        return ui.TagList(
            ui.HTML(f'<div class="warnbox"><b>这一步失败了。</b><br>{esc(j.get("error"))}</div>'),
            ui.HTML(f'<h6 style="margin-top:1rem">引擎日志（stderr 末尾）</h6>'
                    f'<pre class="logbox">{esc(log) or "（空）"}</pre>'),
            ui.HTML(f'<h6>stdout 原文</h6><pre class="logbox">{esc(raw)}</pre>' if raw else ""))


app = App(app_ui, server)
