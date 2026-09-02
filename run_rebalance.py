#!/usr/bin/env python3
"""PC 目标 → 调仓提案（Build 1，**只提案不成交**）。

    PC 产生目标   →   CEO 产生授权   →   Execution 产生交易   →   Ledger 产生事实
    ────────────      ─────────────       已在 Build 2          已在 Build 3
      已有             本入口做到这里

本入口做四件事，一件都不多：

    1. 读**已落库**的那一次 PC 决策（不重跑 PC —— 见下）
    2. 按账本当时的 NAV，把目标权重换成**整数股**，再减去现有持仓 → Δ 股数
    3. 对**成交后的假想组合**跑一遍事前合规
    4. 落一条提案，状态 PENDING_APPROVAL，等 CEO

**为什么读台账而不重跑 PC。** 重跑会得到一份新的决策（测量更新了、
regime 变了），于是 CEO 批准的东西和台账里记的东西不是同一件事——
两份都正常、都可读、都对不上，而且没有任何一处报错。

用法：
    CIO_MARKET=us python run_rebalance.py --open-book            首次开账（开账日默认今天）
    CIO_MARKET=us python run_rebalance.py --open-book --capital 100000 --opened-on 2026-08-31
    CIO_MARKET=us python run_rebalance.py --open-book --opened-on 2026-08-31 --reset-open-date
                                                                 改正填错的开账日（仅限空账本）
    CIO_MARKET=us python run_rebalance.py                        用最近一次 PC 运行
    CIO_MARKET=us python run_rebalance.py --run-id pc-20260831-...
    CIO_MARKET=us python run_rebalance.py --json                 给界面
    CIO_MARKET=us python run_rebalance.py --pending              看待批清单
    CIO_MARKET=us python run_rebalance.py --stats                提案状态分布
    CIO_QUANT_MOCK=1 CIO_MARKET=us python run_rebalance.py       离线冒烟（合成价）

**本入口零 LLM。**
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from cio import book, compliance, marks, pc_ledger              # noqa: E402
from cio import portfolio, proposal_store, rebalance, runid     # noqa: E402
from cio.config import market, market_date                      # noqa: E402
from cio.utils import get_logger, stage                         # noqa: E402

log = get_logger("cio.run_rebalance")
RUN_ID = runid.new_run_id("rb")
"""**这次提案动作**的 id。

提案行上存的 `run_id` 是 **PC 那次运行的 id**，不是这个——
提案必须挂在它所依据的那次决策上，否则 (run_id, portfolio_id, ticker)
的幂等键就防不住"同一次决策被提案两次"。
本 id 只进事件日志的 actor，用来回答"是哪次提案动作产生了这条记录"。
"""


def _arg(argv, name, default=""):
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return default


def _pid(argv) -> str:
    p = _arg(argv, "--portfolio")
    return p or portfolio.MARKET_PORTFOLIO.get(
        market().get("news_region", "us"), portfolio.US_PAPER)


def _emit(payload) -> int:
    import json as _json
    print(_json.dumps(payload, ensure_ascii=False, default=str))
    return 0


def main() -> int:                                              # noqa: C901
    argv = sys.argv[1:]
    as_json = "--json" in argv
    say = (lambda *a, **k: None) if as_json else print
    pid = _pid(argv)

    stage("run_id", RUN_ID)
    stage("start", f"portfolio={pid}")

    # ---------------------------------------------------------- 开账
    if "--open-book" in argv:
        cap = _arg(argv, "--capital")
        try:
            row = book.open_book(pid, capital=(float(cap) if cap else None),
                                 opened_on=_arg(argv, "--opened-on"),
                                 note=_arg(argv, "--note"),
                                 reset_open_date="--reset-open-date" in argv)
        except ValueError as e:
            say(str(e))
            if as_json:
                return _emit(runid.envelope("rebalance", RUN_ID, status="failed",
                                            portfolio_id=pid, action="open_book",
                                            note=str(e)))
            return 1
        book.assert_single_source(pid)
        say(book.render(pid))
        if as_json:
            return _emit(runid.envelope("rebalance", RUN_ID, status="completed",
                                        portfolio_id=pid, action="open_book",
                                        portfolio=row))
        return 0

    if "--stats" in argv:
        s = proposal_store.stats(pid)
        print(f"提案状态分布（{pid}）：" + ("　".join(f"{k} {v}" for k, v in s.items())
                                            or "（无记录）"))
        return 0

    if "--pending" in argv:
        print(proposal_store.render_pending(pid))
        return 0

    as_of = str(market_date())

    # ---------------------------------------------------------- 账本必须先开
    if not book.is_book_portfolio(pid):
        msg = (f"{pid} 还没开账。先跑一次：\n"
               f"    CIO_MARKET=us python run_rebalance.py --open-book "
               f"--capital 100000 --opened-on {as_of}\n"
               f"**开账日和初始资金是写进去的事实，不从最早一笔交易倒推。**")
        say(msg)
        if as_json:
            return _emit(runid.envelope("rebalance", RUN_ID, status="book_not_open",
                                        portfolio_id=pid, as_of=as_of, note=msg,
                                        rows=[], summary={}))
        return 0
    book.assert_single_source(pid)

    # ---------------------------------------------------------- 先作废过期提案
    # **必须在提案之前。** 否则昨天那批 PENDING 会一直挂着，
    # 某天被批准并按当日开盘成交 —— 用的却是好几天前算出的股数。
    expired = proposal_store.expire_stale(pid, as_of, actor=RUN_ID)
    if expired:
        say(f"⚠ {len(expired)} 条旧提案已过期作废（股数基于更早的 NAV 与价格，"
            f"不能拿来今天成交）：" + "、".join(e["ticker"] for e in expired))

    # ---------------------------------------------------------- 取那一次决策
    run_id = _arg(argv, "--run-id") or pc_ledger.latest_run_id(pid)
    if not run_id:
        msg = (f"{pid} 在 pc_lineage 里没有任何一次 PC 运行 —— 先跑 run_pc.py。"
               f"**这不是「今天没有候选」**：那种情况下 PC 会留下一次运行记录，"
               f"只是每一行都没有仓位。")
        say(msg)
        if as_json:
            return _emit(runid.envelope("rebalance", RUN_ID, status="no_pc_run",
                                        portfolio_id=pid, as_of=as_of, note=msg,
                                        rows=[], summary={}))
        return 0
    decisions = pc_ledger.decisions_for_run(run_id, pid)
    stage("decisions", f"run={run_id} rows={len(decisions)}")

    held = book.holdings_map(pid)
    say("=" * 72)
    say(f"调仓提案　portfolio={pid}　as-of {as_of}")
    say(f"依据 PC 运行 {run_id}（{len(decisions)} 条决策，含被否决与无仓位的）")
    say(f"现有持仓 {len(held)} 笔" + ("：" + "、".join(sorted(held)) if held else "（无）"))
    say("=" * 72)

    # ---------------------------------------------------------- 价格与 NAV
    tickers = sorted({d["ticker"] for d in decisions if d.get("ticker")} | set(held))
    px_detail = marks.close_prices(tickers)
    prices = {t: d["price"] for t, d in px_detail.items() if d.get("price") is not None}
    say("\n" + marks.render_note(px_detail))

    nv = book.nav(pid, prices)
    say("\n" + book.render(pid, prices))

    # ---------------------------------------------------------- 目标 → 指令
    pl = rebalance.plan(nav=nv["nav"], cash=nv["cash"], holdings=held,
                        decisions=decisions, prices=prices, decision_date=as_of,
                        lot=int(book.portfolio_row(pid).get("lot_size") or 1))
    say("\n" + "-" * 72)
    say(rebalance.render(pl))

    # ---------------------------------------------------------- 事前合规
    cmp_res = compliance.check_proforma(
        nav=nv["nav"], cash_available=nv["cash"],
        cash_required=pl["summary"]["cash_required"], rows=pl["rows"])
    say("\n" + "-" * 72)
    say(compliance.render(cmp_res))

    # **先序列化，后落库。** 与 run_pc 同一条纪律：反过来的话，
    # 提案已经写进库 → 序列化炸了 → 界面判定失败 → 用户重试 → 库里两条。
    payload = None
    if as_json:
        payload = runid.envelope(
            "rebalance", RUN_ID, status="completed",
            as_of=as_of, portfolio_id=pid, pc_run_id=run_id,
            book=book.portfolio_row(pid), nav=nv,
            price_basis=marks.PRICE_BASIS, prices=px_detail,
            compliance=cmp_res, summary=pl["summary"], rows=pl["rows"],
            expired=[{"id": e["id"], "ticker": e["ticker"]} for e in expired],
            note="Build 1：只提案不成交。执行在 Build 2（T+1 开盘）。")

    # ---------------------------------------------------------- 落库
    # **每一行都落，包括不带指令的。** 只记要交易的两只，
    # 报告上就看不出另外八只发生了什么 —— 而"持有但今天没人复审"
    # 和"根本没纳入评估"都必须能被分辨。
    expires = pl["summary"]["expires_on"]
    saved = []
    for r in pl["rows"]:
        saved.append(proposal_store.record(
            run_id=run_id, portfolio_id=pid, row=r, nav=nv["nav"],
            decision_date=as_of, expires=expires, compliance=cmp_res,
            actor=RUN_ID))

    # 本轮真正做出过判断的持仓 → 刷新复审日期。
    evaluated = [r["ticker"] for r in pl["rows"] if r["basis"] != rebalance.NO_TARGET]
    n_marked = book.mark_evaluated(pid, [t for t in evaluated if t in held],
                                   as_of, run_id)
    stage("proposed", f"rows={len(saved)} marked_evaluated={n_marked}")

    # ---------------------------------------------------------- 推到手机
    # **按钮要有人接才有用。** 控制台（run_tgbot.py）没在跑的时候，
    # 点按钮的表现是转一下圈然后什么都不发生——没有任何提示。
    # 所以推送里同时给出命令行写法，两条路都通。
    if "--tg" in argv:
        from cio import tgbot
        from cio.config import settings
        t, kb = tgbot._pending_msg()
        head = (f"调仓提案　{pid}　{as_of}\n"
                f"依据 PC 运行 {run_id}\n"
                f"事前合规 {cmp_res['status']}"
                f"（已评估 {cmp_res['n_total'] - cmp_res['n_not_evaluated']}"
                f"/{cmp_res['n_total']}）\n")
        tail = ("\n\n按钮需要控制台在跑（python run_tgbot.py）。"
                "没跑的话在电脑上：\n"
                "  python run_approve.py --approve <号>\n"
                "  python run_execute.py")
        try:
            # **照 send 的返回值报告，不照自己的意图报告。**
            # 一句"已推送"配一次没发出去的调用，是这套系统一直在防的那种缺陷。
            sent = tgbot.send(head + t + tail, keyboard=kb)
            say("\nTelegram：" + ("DRYRUN，只打印未真发。" if settings.TG_DRYRUN
                                  else "已推送提案（带批准按钮）。" if sent
                                  else "未推送（token / chat_id 未配置或发送失败）。"))
        except Exception as e:                               # noqa: BLE001
            say(f"\nTelegram 推送失败（提案已落库，不受影响）：{e}")

    if as_json:
        return _emit(payload)

    n_pend = sum(1 for s in saved if s.get("state") == proposal_store.PENDING_APPROVAL)
    say("\n" + "=" * 72)
    say(f"已落 {len(saved)} 条提案：待批准 {n_pend} 条，"
        f"其余 {len(saved) - n_pend} 条无指令（NO_TRADE，终态）")
    say(f"批准有效期至 **{expires}** —— 过期自动作废，必须重新提案。"
        f"一次批准是基于 {as_of} 的 NAV 与价格算出的股数，隔几天再成交完全合法，"
        f"但结果是错的。")
    if cmp_res["status"] == compliance.BREACH:
        say("\n⚠ **事前合规存在破限 —— 不应批准。** 见上面标 [破限] 的那几项。")
    elif cmp_res["status"] == compliance.PARTIAL:
        say(f"\n事前合规 PARTIAL：{cmp_res['n_total'] - cmp_res['n_not_evaluated']}"
            f"/{cmp_res['n_total']} 项已评估。**PARTIAL 不是通过**——"
            f"行业、主题、组合波动、流动性四项要等 Build 4 才有真实输入。")
    say("\nBuild 1 到此为止：**只提案，不成交。**")
    say("下一步（Build 2）：CEO 批准 → 次一交易日开盘按 "
        f"{rebalance.EXECUTION_PRICE_BASIS} 成交 → 记账。")
    say(f"看待批清单：python run_rebalance.py --portfolio {pid} --pending")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
