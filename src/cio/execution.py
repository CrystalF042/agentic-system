"""Execution —— 把**已批准的股数**在下一个交易日开盘变成一笔交易。

    PC 产生目标   →   CEO 产生授权   →   Execution 产生交易   →   Ledger 产生事实
                                        ↑ 本模块

本模块**不做任何投资判断**。它只回答一个问题：那个被批准的整数，
在哪一天、按什么价、成交了没有。

## 三件事只能这样做

一、**下一个交易日是查出来的，不是算出来的。**
    "T+1" 不等于"明天"：周五的下一个交易日是周一，假日要跳过。
    用日历规则算需要一份美股节假日表，那份表一旦过期就会安静地错一天。
    这里去数据里找**第一根晚于决策日的 K 线**（`marks.next_session_after`）。
    找不到 = 那一天还没发生（或取数出了问题），**保持 APPROVED 等下次**，
    不是失败，也不是拿今天的价硬成交。

二、**成交价是那根 K 线的开盘价，未复权。**
    与账本成本价、盯市价同口径。用收盘价成交就是用当天的信息买当天。

三、**买入的现金检查用"这一场之前"的现金，不含同场卖出的回款。**
    美股 T+1 交收，同一开盘卖出的钱当天没到账。提案阶段就是按这个口径
    算的现金需求，执行阶段必须一致——否则会出现"提案说钱不够、
    执行却成功了"这种两边都不报错的矛盾。

## 幂等

`book_trade` 的唯一键是 `(run_id, portfolio_id, ticker)`，和提案同一把键。
重跑执行 → 命中唯一键 → 跳过，不产生第二笔成交。
**这是"重试安全"的全部依据**，不能靠"我记得跑过了"。
"""
from __future__ import annotations

from . import book, marks, proposal_store
from .db import connect
from .utils import get_logger, stamp_utc

log = get_logger("cio.execution")

FILLED = "FILLED"
WAITING = "WAITING_FOR_SESSION"
FAILED = "FAILED"
SKIPPED = "ALREADY_FILLED"


def _session_bar(ticker: str, after: str) -> dict:
    """决策日之后的第一根 K 线。返回 {} 表示还没有。"""
    day = marks.next_session_after(ticker, after)
    if not day:
        return {}
    df = marks._raw_hist(ticker, 30)
    if df is None or not len(df):
        return {}
    for _, r in df.iterrows():
        if r["date"].strftime("%Y-%m-%d") == day:
            o = float(r["open"])
            return {"date": day, "open": (o if o > 0 else None)}
    return {}


def execute_one(p: dict, *, session=None, actor: str = "system") -> dict:
    """成交一条已批准的提案。**不判断该不该成交，只判断能不能成交。**

    `session` 供测试注入：`{"date": "2026-09-01", "open": 181.2}`。
    """
    pid, tk = p["portfolio_id"], str(p["ticker"]).upper()
    out = {"proposal_id": p["id"], "ticker": tk, "status": FAILED,
           "reason": "", "shares": p.get("delta_shares") or 0,
           "execution_date": "", "execution_price": None, "trade_id": None}

    if p["state"] != proposal_store.APPROVED:
        out["reason"] = f"状态是 {p['state']}，只有 APPROVED 才能成交"
        return out

    # 已经有成交行 → 直接跳过。**幂等靠唯一键，不靠记性。**
    with connect() as con:
        row = con.execute("SELECT id FROM book_trade WHERE run_id=? AND portfolio_id=? "
                          "AND ticker=?", (p["run_id"], pid, tk)).fetchone()
    if row:
        out.update(status=SKIPPED, trade_id=int(row[0]),
                   reason="该决策已有成交记录，跳过（重试不产生第二笔）")
        return out

    bar = session if session is not None else _session_bar(tk, p["decision_date"])
    if not bar or not bar.get("date"):
        out.update(status=WAITING,
                   reason=f"{p['decision_date']} 之后还没有交易日的行情 —— "
                          f"保持已批准，等下一次执行。**不拿今天的价硬成交。**")
        return out
    if bar.get("open") is None:
        out.update(status=FAILED,
                   reason=f"{bar['date']} 有 K 线但取不到开盘价 —— 无法按 "
                          f"T+1_OPEN 成交。这一条要重新提案，不是静默跳过。")
        return out

    px = float(bar["open"])
    qty = int(p["delta_shares"] or 0)
    if qty == 0:
        out.update(status=FAILED, reason="提案的成交数量是 0，不该进到执行层")
        return out

    side = "BUY" if qty > 0 else "SELL"
    gross = abs(qty) * px
    comm = book.COMMISSION_PER_TRADE
    slip = 0.0
    cash_now = book.cash(pid)
    if cash_now is None:
        out.update(status=FAILED, reason="账本未开，无法成交")
        return out

    if side == "BUY" and gross + comm > cash_now + 1e-9:
        # **不部分成交。** 少买一半是一个没人批准过的仓位。
        out.update(status=FAILED,
                   reason=f"现金不足：需 {gross + comm:,.2f}，可用 {cash_now:,.2f}。"
                          f"**不做部分成交**——少买一半是一个没人批准过的仓位。")
        return out

    held = book.holdings_map(pid).get(tk)
    have = int((held or {}).get("shares") or 0)
    if side == "SELL" and abs(qty) > have:
        out.update(status=FAILED,
                   reason=f"要卖 {abs(qty)} 股，账上只有 {have} 股 —— 不做卖空。"
                          f"持仓在批准之后发生过变化，这一条要重新提案。")
        return out

    now = stamp_utc()
    realized = None
    cash_flow = -(gross + comm) if side == "BUY" else (gross - comm)

    with connect() as con:
        if side == "BUY":
            if held:
                new_shares = have + qty
                new_cost = ((have * float(held["avg_cost"])) + gross) / new_shares
                con.execute("UPDATE book_position SET shares=?, avg_cost=?, "
                            "last_run_id=?, last_evaluated_on=? "
                            "WHERE portfolio_id=? AND ticker=? AND open=1",
                            (new_shares, new_cost, p["run_id"], bar["date"], pid, tk))
            else:
                con.execute(
                    "INSERT INTO book_position(portfolio_id,ticker,shares,avg_cost,"
                    "opened_on,opened_run_id,last_run_id,last_evaluated_on,"
                    "realized_pnl,open) VALUES(?,?,?,?,?,?,?,?,0,1)",
                    (pid, book.assert_us_ticker(tk), qty, px, bar["date"],
                     p["run_id"], p["run_id"], bar["date"]))
        else:
            sold = abs(qty)
            cost = float(held["avg_cost"])
            realized = (px - cost) * sold - comm
            left = have - sold
            if left > 0:
                # 减持不动成本价：均价法下已实现盈亏已经单独记账，
                # 再改成本价会把同一笔盈亏算两次。
                con.execute("UPDATE book_position SET shares=?, "
                            "realized_pnl=COALESCE(realized_pnl,0)+?, last_run_id=?, "
                            "last_evaluated_on=? WHERE portfolio_id=? AND ticker=? AND open=1",
                            (left, realized, p["run_id"], bar["date"], pid, tk))
            else:
                # **平仓是置 open=0 并留行，不是删行。** 删掉的那一刻，
                # 业绩归因的分母就没了。
                con.execute("UPDATE book_position SET shares=0, open=0, closed_on=?, "
                            "realized_pnl=COALESCE(realized_pnl,0)+?, last_run_id=? "
                            "WHERE portfolio_id=? AND ticker=? AND open=1",
                            (bar["date"], realized, p["run_id"], pid, tk))
        con.execute("UPDATE book_portfolio SET cash=cash+? WHERE portfolio_id=?",
                    (cash_flow, pid))
        cur = con.execute(
            "INSERT INTO book_trade(run_id,portfolio_id,ticker,side,shares,"
            "decision_date,decision_time,decision_price,execution_date,execution_time,"
            "execution_price,execution_price_basis,commission,slippage,cash_flow,"
            "proposal_id,realized_pnl,created_at) VALUES(" + ",".join(["?"] * 18) + ")",
            (p["run_id"], pid, tk, side, abs(qty), p["decision_date"], p["created_at"],
             p["decision_price"], bar["date"], now, px, p["execution_price_basis"],
             comm, slip, cash_flow, p["id"], realized, now))
        trade_id = cur.lastrowid

    proposal_store.transition(
        p["id"], proposal_store.EXECUTED, actor=actor,
        note=f"{bar['date']} 开盘 {px:,.2f} 成交 {side} {abs(qty)} 股"
             f"（决策日 {p['decision_date']} 收盘价 {p['decision_price']:,.2f}）",
        fields={"executed_at": now, "execution_date": bar["date"],
                "execution_price": px, "trade_id": trade_id})

    gap = None
    if p["decision_price"]:
        gap = (px - float(p["decision_price"])) / float(p["decision_price"])
    out.update(status=FILLED, execution_date=bar["date"], execution_price=px,
               trade_id=trade_id, realized_pnl=realized, cash_flow=cash_flow,
               gap_pct=gap, side=side,
               reason=f"{bar['date']} 开盘成交")
    log.info("成交 #%s %s %s %d 股 @ %.2f（%s）", p["id"], tk, side, abs(qty),
             px, bar["date"])
    return out


def run(portfolio_id: str, *, today: str, actor: str = "system",
        sessions: dict = None) -> dict:
    """把该组合所有已批准的提案跑一遍。

    **先卖后买**：卖出会让持仓行先更新，避免同一只票先买后卖时读到旧数量。
    但现金检查用的仍是**这一场之前**的余额（见模块开头第三条）——
    卖出回款不能在同一场里被拿去买东西。
    """
    proposal_store.expire_stale(portfolio_id, today, actor=actor)
    rows = proposal_store.approved(portfolio_id)
    rows.sort(key=lambda r: 0 if (r["delta_shares"] or 0) < 0 else 1)
    cash_before = book.cash(portfolio_id)
    spent = 0.0
    out = []
    for p in rows:
        sess = (sessions or {}).get(str(p["ticker"]).upper())
        # 买入的可用现金 = 场前余额 − 本场已花掉的，**不加本场卖出回款**。
        if (p["delta_shares"] or 0) > 0 and cash_before is not None:
            avail = cash_before - spent
            px = float(p["decision_price"] or 0)
            need = abs(int(p["delta_shares"])) * px
            if need > avail + 1e-9:
                out.append({"proposal_id": p["id"], "ticker": p["ticker"],
                            "status": FAILED,
                            "reason": f"按场前现金口径不足（T+1 交收，同场卖出回款"
                                      f"当天未到账）：预估需 {need:,.2f}，"
                                      f"本场可用 {avail:,.2f}"})
                proposal_store.transition(p["id"], proposal_store.EXECUTION_FAILED,
                                          actor=actor, note=out[-1]["reason"])
                continue
        r = execute_one(p, session=sess, actor=actor)
        if r["status"] == FAILED:
            proposal_store.transition(p["id"], proposal_store.EXECUTION_FAILED,
                                      actor=actor, note=r["reason"])
        elif r["status"] == FILLED and r.get("side") == "BUY":
            spent += abs(r["shares"]) * float(r["execution_price"])
        out.append(r)
    n = {}
    for r in out:
        n[r["status"]] = n.get(r["status"], 0) + 1
    return {"portfolio_id": portfolio_id, "today": today, "rows": out,
            "n_by_status": n, "n_approved": len(rows)}


def render(res: dict) -> str:
    if not res["rows"]:
        return (f"{res['portfolio_id']}：没有已批准待成交的提案。"
                f"（先 run_rebalance.py 出提案，再 run_approve.py 批准。）")
    L = [f"执行 {res['portfolio_id']}　{res['today']}　"
         f"已批准 {res['n_approved']} 条"]
    w = {FILLED: "已成交", WAITING: "等待开盘", FAILED: "未成交", SKIPPED: "已成交过"}
    for r in res["rows"]:
        L.append(f"  {r['ticker']:<6} {w.get(r['status'], r['status']):<8}"
                 + (f" {r.get('side', '')} {abs(r['shares'])} 股 @ "
                    f"{r['execution_price']:,.2f}　{r['execution_date']}"
                    + (f"　跳空 {r['gap_pct']:+.2%}" if r.get("gap_pct") is not None else "")
                    if r["status"] == FILLED else ""))
        if r.get("reason") and r["status"] != FILLED:
            L.append(f"         {r['reason']}")
    L.append("　".join(f"{w.get(k, k)} {v}" for k, v in res["n_by_status"].items()))
    return "\n".join(L)
