#!/usr/bin/env python3
"""CRO → PC 一键入口（确定性，零 LLM）。

    一部产生观点  →  二部测量现实  →  CRO 测量承担这个观点的风险
                  →  PC 决定承担多少  →  CEO 决定是否执行

用法：
    CIO_MARKET=us python run_pc.py
    CIO_MARKET=us python run_pc.py --portfolio US_PAPER
    CIO_MARKET=us python run_pc.py --tg     推送到 Telegram（CIO_TG_DRYRUN=1 只打印）
    CIO_MARKET=us python run_pc.py --json   结构化输出（给界面用；仍会落 lineage）
    python run_pc.py --stats            # 过去的仓位分别是被谁决定的

**本模块一次模型调用都没有。** 一部的观点从论点台账读结构化字段，
二部的测量确定性算出，CRO 的判断由政策阈值编码，PC 的仓位是公式。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from cio import deliver, material_gate, pc_ledger, portfolio         # noqa: E402
from cio import regime, risk_officer, runid, sizing                  # noqa: E402
from cio import quant_data, thesis_store                            # noqa: E402
from cio.config import market, market_date                          # noqa: E402
from cio.utils import get_logger, stage                             # noqa: E402

log = get_logger("cio.run_pc")
RUN_ID = runid.new_run_id("pc")


def pid_hint(argv) -> str:
    if "--portfolio" in argv:
        i = argv.index("--portfolio")
        if i + 1 < len(argv):
            return argv[i + 1]
    return "（按市场默认）"


def _measures_for(symbol: str) -> dict:
    """二部口径的确定性测量，**换算成 CRO 的小数口径后交出**。

    两条纪律，都是被真实缺陷教出来的：

    一、**单位在边界上显式换算。** `measures.*` 返回百分数（40.74 = 40.74%），
        CRO 的 POLICY 阈值是小数（veto_vol = 1.50 = 150%）。直接对比会得到
        "40.74 > 1.50 → 否决"，理由行印得一字不差，结论却是反的。
        换算写在这里，接收端 `risk_officer.check_units` 再校一次量级。
        Beta 与相关系数本来就是无量纲，**不换算**。

    二、**每一项测量各自 try。** 原来整段共用一个 try：`beta_corr` 抛错时
        前面已经算出的 σ 留下、后面的全成 None，日志只说一句"测量取不到"，
        报告上看不出哪一项失败。一次异常污染整组测量，这就是静默失败。

    取不到的一律 None，**绝不填 0**——`beta=0` 读起来是"完全不随市场波动"，
    真实含义却是"我们不知道"，这两件事在风险判断里的后果相反。
    """
    from cio import measures
    out = {"sigma_60": None, "sigma_252": None, "beta": None,
           "maxdd": None, "corr_bench": None, "liquidity_cap": None,
           "beta_n_aligned": None}
    try:
        st = quant_data.Stock(code=symbol, name=symbol, yahoo=symbol)
        df = quant_data.get_history([st], days=400).get(symbol)
    except Exception as e:
        log.warning("%s 取不到行情，全部测量标为未评估（不填 0）：%s", symbol, e)
        return out
    if df is None or not len(df):
        log.warning("%s 行情为空，全部测量标为未评估（不填 0）", symbol)
        return out
    closes = df["close"].tolist()
    # 基准单独取。**它只被 beta/corr 用到**——放进上面那个 try 的话，
    # 基准挂掉会连带把 σ 和回撤一起清成 None，而这两项本来算得出来。
    try:
        bench = quant_data.get_benchmark(days=400)
    except Exception as e:                           # noqa: BLE001
        log.warning("%s 的基准取不到，仅 beta/corr 标为未评估：%s", symbol, e)
        bench = None

    def _one(field, fn, ratio=True):
        try:
            v = fn()
        except Exception as e:                       # noqa: BLE001
            log.warning("%s 的 %s 算不出，仅该项标为未评估：%s", symbol, field, e)
            return
        out[field] = measures.as_ratio(v) if ratio else v

    _one("sigma_60", lambda: measures.ann_vol(closes, 60))
    _one("sigma_252", lambda: measures.ann_vol(closes, 252))
    _one("maxdd", lambda: measures.max_drawdown(closes, 250))
    try:
        b, c, n_aligned = measures.beta_corr(df, bench, 250, 60)   # 返回三元组
        out["beta"], out["corr_bench"], out["beta_n_aligned"] = b, c, n_aligned
        if b is None:
            log.info("%s 的 Beta 未评估：与基准对齐 %s 个交易日，不足 250×0.8",
                     symbol, n_aligned)
    except Exception as e:                           # noqa: BLE001
        log.warning("%s 的 beta/corr 算不出，仅该两项标为未评估：%s", symbol, e)
    return out


def _tg_summary(as_of: str, pid: str, rg: dict, rows: list, ps: dict) -> str:
    """Telegram 摘要。**纯文本，不用 markdown 强调符**——
    Telegram 的 Markdown 解析对成对的 `*` 很挑剔，报告里的 `**` 会让整条消息
    发不出去或者排版错乱。deliver.send_text 有纯文本兜底，但不该依赖兜底。

    内容上只放**决策**与**理由**：谁拿到仓位、被什么绑定、谁没拿到、为什么。
    中间计算过程留在终端和 lineage 里。
    """
    def _plain(s: str) -> str:
        # 报告里的 **强调** 是给终端和 PDF 用的；带进 Telegram 会让 Markdown
        # 解析失败、整条消息退回纯文本（甚至丢排版）。在出口处一次剥掉。
        return str(s or "").replace("**", "")

    L = [f"PC 定仓 · {pid}（{as_of}）",
         f"市场 regime：{rg.get('regime', '?')}（{rg.get('note', '')}）", ""]
    sized = notsized = 0
    for cro, sz in rows:
        head = (f"{cro.get('ticker', '?')}　{cro.get('direction', '')}|"
                f"{cro.get('conviction', '')}　Gate {cro.get('evidence_gate', '')}")
        if sz.get("w_final") is None:
            notsized += 1
            L.append(f"· {head}\n    无仓位：{_plain(sz.get('reason', ''))}")
        else:
            sized += 1
            w = sz["w_final"] * float(ps.get("scale_factor") or 1.0)
            L.append(f"· {head}\n    仓位 {w:.2%}　σ_eff {sz['sigma_effective']:.2%}"
                     f"　绑定 {'+'.join(sz['binding_position_constraint'])}")
        if cro.get("veto"):
            L[-1] += f"\n    CRO 否决：{_plain(cro.get('veto_reason', ''))}"
    tot = sum((ps.get("weights") or {}).values())
    L += ["", f"合计权重 {tot:.2%}　现金残差 {sizing.cash_residual(ps.get('weights') or {}):.2%}"
              f"（不归一化到 100%）",
          f"候选 {len(rows)} 只：定仓 {sized}，无仓位 {notsized}",
          "CRO 给约束，PC 给权重，两者都不判断论点对错。执行与否由 CEO 决定。"]
    return "\n".join(L)


def main() -> int:
    argv = sys.argv[1:]
    if "--stats" in argv:
        s = pc_ledger.binding_stats()
        print(f"lineage 记录 {s['n']} 条，其中 CRO 否决 {s['vetoed']} 条、"
              f"无仓位 {s['no_position']} 条")
        if s["no_position_reason"]:
            print("\n无仓位的原因：")
            for k, v in s["no_position_reason"].items():
                print(f"  {k:40} {v}")
        print("\n仓位由谁决定（binding constraint 计数）：")
        for k, v in (s["position_binding"] or {"（无记录）": 0}).items():
            print(f"  {k:28} {v}")
        print("\nσ 由哪一项决定：")
        for k, v in (s["sigma_binding"] or {"（无记录）": 0}).items():
            print(f"  {k:28} {v}")
        return 0

    # --json：**stdout 只留 JSON**。但绝不能因此提前 return——
    # 中间那段 pc_ledger.record 必须照跑，否则界面触发的每一次定仓
    # 都不进 lineage，而 `--stats` 仍然一切正常地少算。
    # 所以关掉的是【打印】，不是【流程】。
    as_json = "--json" in argv
    say = (lambda *a, **k: None) if as_json else print

    stage("run_id", RUN_ID)
    stage("start", f"portfolio={pid_hint(argv)}")

    pid = ""
    if "--portfolio" in argv:
        i = argv.index("--portfolio")
        pid = argv[i + 1] if i + 1 < len(argv) else ""
    pid = pid or portfolio.MARKET_PORTFOLIO.get(market().get("news_region", "us"), portfolio.US_PAPER)

    as_of = str(market_date())
    say("=" * 66)
    say(f"CRO → PC　portfolio={pid}　as-of {as_of}")
    say("=" * 66)

    # ---- 持仓：唯一真源，且必须显式指定 portfolio ----
    held = portfolio.open_positions(pid)
    say(f"\n持仓（{pid}）：{len(held)} 笔"
          + ("　" + "、".join(h["code"] for h in held) if held else "　（无）"))
    allp = portfolio.summary()
    for p in allp:
        tag = "（本次风险计算的口径）" if p["portfolio_id"] == pid else "（**不参与本次风险计算**）"
        say(f"  {p['portfolio_id']}: {p['n']} 笔（实盘口径 {p['n_real']} 笔）{tag}")
        for a in p["accounts"]:                 # 按账户拆开：同票多账户 ≠ 台账重复
            mark = "　← 影子账户，纸面镜像，不计入风险聚合" if a["is_shadow"] else ""
            say(f"    账户 {a['account']}: {a['n']} 笔　{'、'.join(a['codes'])}{mark}")
    dups = portfolio.duplicates()
    if dups:
        say("\n  ⚠ **台账重复**（同一账户同一代码有多条 open 记录）——"
              "任何按持仓聚合的口径都会成倍虚增，请先修台账：")
        for d in dups:
            say(f"    {d['portfolio_id']} / {d['account']} / {d['code']}：{d['n']} 条")

    # ---- 市场 regime：CRO 自己的 market-level 输入 ----
    rg = regime.assess()
    say("\n" + regime.render(rg))

    # ---- 一部观点：只取结构化字段，不读多空论述 ----
    theses = thesis_store.open_brief("", limit=50)
    if not theses:
        say("\n论点台账里没有仍 OPEN 的一部观点——无候选，本轮不定仓位。")
        if as_json:
            # **空 stdout 不是合法输出。** 界面拿到空串只能显示"出错了"，
            # 而"今天没有候选"是这套系统最常见的正常状态，必须能表达。
            import json as _json
            print(_json.dumps(runid.envelope(
                "pc", RUN_ID, status="no_candidates",
                as_of=as_of, portfolio_id=pid, regime=rg,
                positions=[], total_weight=0.0, cash_residual=1.0,
                note="论点台账里没有仍 OPEN 的一部观点"),
                ensure_ascii=False, default=str))
        return 0
    say(f"\n一部 OPEN 论点 {len(theses)} 条：" + "、".join(
        f"{t['subject']}({t['direction']}|{t['conviction']})" for t in theses))

    rows, weights = [], {}
    for t in theses:
        sym = (t.get("subject") or "").upper()
        m = _measures_for(sym)
        # 换算点只有一个：material_gate.level_from_verdict()。
        # **不要在这里手写 if/else**——就地展开的映射表会漏掉"字段为空"这一档，
        # 把"闸门没跑过"静默折成"闸门判了没有实质材料"。
        gate = material_gate.level_from_verdict(t.get("material_verdict"))
        if gate == material_gate.UNRECORDED:
            log.warning("论点 #%s（%s）没有材料判定字段——按未定档处理，不给仓位。"
                        "这**不是**『一部未产出观点』，重跑一次 run_unit_a.py 才能定档。",
                        t.get("id", "?"), sym)
        try:
            cro = risk_officer.assess_one(
                ticker=sym, direction=t.get("direction", ""),
                conviction=t.get("conviction", ""), evidence_gate=gate,
                thesis_id=t.get("id", 0), invalidation_conditions=t.get("invalidations"),
                measures=m, regime=rg["regime"])
        except ValueError as e:
            # 口径不符不是"风险高"，是**测量不可用**。按未评估处理并印在报告上，
            # 绝不能落成一次否决——否决会被当作真实风险判断读。
            log.error("%s 的测量口径不符，本轮不定仓位：%s", sym, e)
            rows.append(({"ticker": sym, "direction": t.get("direction", ""),
                          "conviction": t.get("conviction", ""), "evidence_gate": gate,
                          "thesis_id": t.get("id", 0), "regime": rg["regime"],
                          "caps": {}, "risk_constraints": [str(e)], "notes": [],
                          "veto": False, "veto_reason": "",
                          "base_risk_budget": None, "conviction_multiplier": None,
                          "regime_multiplier": None, "adjusted_risk_budget": None},
                         # reason 是 `--stats` 的分组键，必须**短且稳定**；
                         # 完整信息落在 risk_constraints 列里，不靠这一列携带。
                         {"w_final": None, "reason": "测量口径不符（未完成风险审查）"}))
            continue
        if cro["veto"]:
            rows.append((cro, {"w_final": None,
                               "reason": f"CRO 否决：{cro['veto_reason']}"}))
            continue
        sz = sizing.size_one(
            ticker=sym, conviction=cro["conviction"], evidence_gate=cro["evidence_gate"],
            sigma_60=m["sigma_60"], sigma_252=m["sigma_252"], caps=cro["caps"],
            base_rb=cro["base_risk_budget"], regime=cro["regime"])
        rows.append((cro, sz))
        if sz.get("w_final") is not None:
            weights[sym] = sz["w_final"]

    # ---- 第二趟：组合层总量约束（不是逐票 min 里的一项）----
    port_risk = None                     # 组合波动需要相关矩阵，尚未实现 → 显式未评估
    ps = sizing.portfolio_scale(weights, risk_officer.POLICY["portfolio_risk_cap"], port_risk)

    # **先序列化，后落库。** 顺序反过来的话，存在这样一条路径：
    # ledger 已经记了一次 → JSON 序列化炸了 → 界面判定"失败" → 用户点重试
    # → 台账里出现两条同样的决策。先把 payload 造出来（会炸就在这里炸），
    # 此时一个字都还没写库，整次运行干净地失败，重试是安全的。
    payload = None
    if as_json:
        import json as _json
        payload = _json.dumps(runid.envelope(
            "pc", RUN_ID, status="completed",
            as_of=as_of, portfolio_id=pid, regime=rg,
            scale_factor=ps.get("scale_factor"), scale_reason=ps.get("reason", ""),
            total_weight=sum((ps.get("weights") or {}).values()),
            cash_residual=sizing.cash_residual(ps.get("weights") or {}),
            positions=[{
                "ticker": c.get("ticker"), "direction": c.get("direction"),
                "conviction": c.get("conviction"), "evidence_gate": c.get("evidence_gate"),
                "thesis_id": c.get("thesis_id"), "measures": c.get("measures"),
                "adjusted_risk_budget": c.get("adjusted_risk_budget"),
                "caps": c.get("caps"), "risk_constraints": c.get("risk_constraints"),
                "veto": bool(c.get("veto")), "veto_reason": c.get("veto_reason", ""),
                "notes": c.get("notes"),
                "sigma_effective": z.get("sigma_effective"),
                "sigma_binding_component": z.get("sigma_binding_component"),
                "w_raw": z.get("w_raw"), "w_final": z.get("w_final"),
                "binding_position_constraint": z.get("binding_position_constraint"),
                "caps_not_evaluated": z.get("caps_not_evaluated"),
                "reason": z.get("reason", ""),
            } for c, z in rows]), ensure_ascii=False, default=str)

    say("\n" + "-" * 66)
    for cro, sz in rows:
        say("\n" + risk_officer.render_one(cro))
        if sz.get("w_final") is None:
            say(f"- **无仓位**：{sz.get('reason', '')}")
        else:
            say(f"- σ_eff **{sz['sigma_effective']:.2%}**"
                  f"（σ60 {sz['sigma_60']:.2%} / σ252 "
                  + ("无" if sz['sigma_252'] is None else f"{sz['sigma_252']:.2%}")
                  + f" / floor {sz['sigma_floor']:.0%}"
                  f"　绑定 {'+'.join(sz['sigma_binding_component'])}）")
            say(f"- w_raw {sz['w_raw']:.2%} → **w_final {sz['w_final']:.2%}**"
                  f"　绑定 {'+'.join(sz['binding_position_constraint'])}")
        # **否决与无仓位同样落库。** 上一版在这里 continue，于是被否决的标的
        # 一条 lineage 都没有，`--stats` 却照样印"其中 CRO 否决 0 条"——
        # 一个正常显示的、错误的统计量。而"哪些票被风险层挡掉了"恰恰是
        # 这张表最该回答的问题：不落库，风险层的历史影响就永远不可复盘。
        pc_ledger.record(as_of_date=as_of, portfolio_id=pid, cro=cro, size=sz,
                         scale_factor=ps["scale_factor"], run_id=RUN_ID)

    if "--tg" in argv:
        from cio.config import settings
        ok = deliver.send_text(_tg_summary(as_of, pid, rg, rows, ps))
        # **不要把 DRYRUN 报告成"已推送"。** send_text 在 DRYRUN 下返回 True
        # （表示"这条本来会发出去"），照着它印"已推送"就是在说一件没发生的事。
        say("\n" + ("Telegram：DRYRUN，只打印未真发。" if settings.TG_DRYRUN
                      else "Telegram 已推送。" if ok
                      else "Telegram 未推送（未配置 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID）。"))

    say("\n" + "-" * 66)
    if ps["reason"]:
        say(f"组合层：{ps['reason']}")
    tot = sum(ps["weights"].values()) if ps.get("weights") else 0.0
    say(f"合计权重 **{tot:.2%}**　现金残差 **{sizing.cash_residual(ps.get('weights') or {}):.2%}**")
    say("（**不归一化到 100%**：归一化会把风险规则刚压下去的仓位重新吹回来。）")
    if as_json:
        print(payload)
        return 0
    say("\nCRO 给约束，PC 给权重，两者都不判断论点对错。执行与否由 CEO 决定。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
