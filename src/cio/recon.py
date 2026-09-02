"""每日对账 —— **唯一能在下游发现上游静默错误的机制**。

真实机构里运营每天拿内部账和托管行对一遍。这里没有托管行，
但内部恒等式一样能查，而且能查出同一类问题：
**某个环节少写了一笔、多写了一笔、或者写反了。**

三条恒等式，任何一条不成立就红字停报：

    1  现金 + Σ(股数 × 盯市价) == NAV
    2  初始资金 + Σ(交易现金流) + Σ(公司行为现金) == 当前现金
    3  每个持仓的股数，能被 trades 加公司行为解释出来

## 为什么"停报"而不是"标个警告"

一份带着警告的盈亏表还是会被读、被相信、被拿去做决定。
对账失败的含义不是"某个数字可能有点问题"，而是**这本账现在自相矛盾**——
在矛盾解决之前，任何由它算出来的收益率都没有意义。

## 第 2 条为什么把公司行为单列

分红是**现金增加但没有交易**的唯一合法来源。不把它算进恒等式，
每收一次分红对账就报一次假警；把它混进 trades 又会让"我们买卖了什么"
这个问题的答案变脏。所以它单独一项，看得见。
"""
from __future__ import annotations

from . import book, corp_actions
from .db import connect
from .utils import get_logger

log = get_logger("cio.recon")

TOL = 0.01                  # 一分钱。浮点误差远小于它；真实错账远大于它。

PASS = "PASS"
FAIL = "FAIL"
SKIPPED = "SKIPPED"


def _nav_row(pid: str, day: str) -> dict:
    with connect() as con:
        r = con.execute("SELECT * FROM book_nav WHERE portfolio_id=? AND date=?",
                        (pid, str(day)[:10])).fetchone()
    return dict(r) if r else {}


def check(portfolio_id: str, day: str) -> dict:
    """跑三条恒等式。返回 {status, checks:[...]}。"""
    book.init()
    pid, day = portfolio_id, str(day)[:10]
    p = book.portfolio_row(pid)
    out = []

    # ---------------------------------------------------------- 1
    nav_row = _nav_row(pid, day)
    if not nav_row:
        out.append({"id": "nav_identity", "status": SKIPPED,
                    "detail": f"{day} 还没有盯市记录"})
    elif not nav_row.get("complete"):
        out.append({"id": "nav_identity", "status": SKIPPED,
                    "detail": f"{day} 有 {nav_row.get('n_unpriced')} 只缺价，"
                              f"NAV 本就不可计算 —— 这条不适用，"
                              f"**不是通过**"})
    else:
        with connect() as con:
            mv = float(con.execute(
                "SELECT COALESCE(SUM(market_value),0) FROM book_mark "
                "WHERE portfolio_id=? AND date=? AND priced=1",
                (pid, day)).fetchone()[0] or 0.0)
        lhs = float(nav_row["cash"] or 0.0) + mv
        rhs = float(nav_row["nav"] or 0.0)
        ok = abs(lhs - rhs) <= TOL
        out.append({"id": "nav_identity", "status": PASS if ok else FAIL,
                    "detail": f"现金 {nav_row['cash']:,.2f} + 持仓 {mv:,.2f} "
                              f"= {lhs:,.2f}　NAV {rhs:,.2f}"
                              + ("" if ok else f"　**差 {lhs - rhs:,.2f}**")})

    # ---------------------------------------------------------- 2
    with connect() as con:
        flows = float(con.execute(
            "SELECT COALESCE(SUM(cash_flow),0) FROM book_trade WHERE portfolio_id=?",
            (pid,)).fetchone()[0] or 0.0)
    ca = corp_actions.cash_from_actions(pid)
    cap = float(p.get("initial_capital") or 0.0)
    cash = float(p.get("cash") or 0.0)
    lhs = cap + flows + ca
    ok = abs(lhs - cash) <= TOL
    out.append({"id": "cash_identity", "status": PASS if ok else FAIL,
                "detail": f"初始 {cap:,.2f} + 交易现金流 {flows:,.2f} + "
                          f"公司行为 {ca:,.2f} = {lhs:,.2f}　账上现金 {cash:,.2f}"
                          + ("" if ok else f"　**差 {lhs - cash:,.2f}**")})

    # ---------------------------------------------------------- 3
    bad = []
    with connect() as con:
        trades = {}
        for tk, side, sh in con.execute(
                "SELECT ticker, side, COALESCE(SUM(shares),0) FROM book_trade "
                "WHERE portfolio_id=? GROUP BY ticker, side", (pid,)):
            d = trades.setdefault(tk, {"BUY": 0, "SELL": 0})
            d[side] = int(sh or 0)
        splits = {}
        for tk, before, after in con.execute(
                "SELECT ticker, shares_before, shares_after FROM book_corp_action "
                "WHERE portfolio_id=? AND kind='SPLIT' ORDER BY ex_date", (pid,)):
            splits.setdefault(tk, []).append((int(before or 0), int(after or 0)))
    for h in book.holdings(pid):
        tk = h["ticker"]
        t = trades.get(tk, {"BUY": 0, "SELL": 0})
        expect = t["BUY"] - t["SELL"]
        for before, after in splits.get(tk, []):
            expect += (after - before)         # 拆股对股数的净影响
        if expect != int(h["shares"]):
            bad.append(f"{tk}：账上 {h['shares']} 股，"
                       f"交易({t['BUY']}买−{t['SELL']}卖)加公司行为推出 {expect} 股")
    out.append({"id": "position_identity", "status": FAIL if bad else PASS,
                "detail": ("；".join(bad) if bad else
                           f"{len(book.holdings(pid))} 个持仓的股数都能被交易与"
                           f"公司行为解释")})

    n_fail = sum(1 for c in out if c["status"] == FAIL)
    n_skip = sum(1 for c in out if c["status"] == SKIPPED)
    status = FAIL if n_fail else (SKIPPED if n_skip else PASS)
    if n_fail:
        log.error("%s %s 对账失败 %d 条 —— 账本自相矛盾，本轮不出盈亏表",
                  pid, day, n_fail)
    return {"portfolio_id": pid, "date": day, "status": status, "checks": out,
            "n_fail": n_fail, "n_skipped": n_skip}


_LABEL = {
    "nav_identity": "现金 + 持仓市值 == NAV",
    "cash_identity": "初始资金 + 交易现金流 + 公司行为 == 现金",
    "position_identity": "持仓股数能被交易与公司行为解释",
}


def render(res: dict) -> str:
    head = {PASS: "三条恒等式全部成立",
            FAIL: "**账本自相矛盾 —— 在修好之前，任何由它算出的收益率都没有意义**",
            SKIPPED: "部分不适用（见下）"}
    L = [f"每日对账　{res['date']}：{res['status']}　{head.get(res['status'], '')}"]
    mark = {PASS: "通过", FAIL: "失败", SKIPPED: "不适用"}
    for c in res["checks"]:
        L.append(f"  [{mark.get(c['status'], c['status']):<4}] "
                 f"{_LABEL.get(c['id'], c['id'])}")
        L.append(f"         {c['detail']}")
    return "\n".join(L)
