"""公司行为 —— **所有自建账本最常见的死法**。

## 不做会怎样

4:1 拆股当天：

    账本说   100 股 @ $800
    市场说   $200

持仓瞬间显示 **−75%**。没有异常、没有告警、没有一处数字看起来是坏的——
净值曲线上只是多了一个巨亏日。第二天照常盯市，那个坑永远留在曲线里。

分红更隐蔽：不记的话组合白白少掉一笔收入，而基准（复权序列）是含息的，
于是超额收益被系统性低估，**每年差一两个百分点，长期就是全部的结论**。

## 为什么这里必须用未复权价

复权序列会把这两件事"抹平"——那正是它的用途，对**测量**是对的。
但账本记的是"我们真实持有多少股、成本多少"，那是未复权的事实。
所以账本这一侧必须自己把公司行为记成**显式事件**：

    拆股   股数 × ratio，成本价 ÷ ratio，零头折现金
    分红   现金 += 股数 × 每股金额

两者都写进 `book_corp_action`，看得见、可回溯、可对账。

## 拆股的零头

3:2 拆股遇上 5 股 = 7.5 股。真实券商的做法是给**零股现金**（cash in lieu）。
这里照做：向下取整，零头按**除权日的实际成交价**折成现金。

**取不到除权日的价就不应用这次拆股**，并让该标的在盯市时标缺价。
硬着头皮取整会静默吞掉一部分市值；而"拆了但账本没拆"比不拆更危险——
下一次盯市会拿**拆后价格**乘**拆前股数**。

## 资格判定

只对当前持有、且 `ex_date > opened_on` 的仓位应用——
除权日当天才买进的，拿不到这次分红。这条判定是近似的（没有逐日持仓快照），
局限写在这里：**中途清仓又买回的仓位可能被误判**，等有了逐日持仓表再收紧。
"""
from __future__ import annotations

from . import book, marks
from .db import connect
from .utils import get_logger, stamp_utc

log = get_logger("cio.corp_actions")

SPLIT = "SPLIT"
DIVIDEND = "DIVIDEND"
LOOKBACK_DAYS = 120


def fetch_actions(ticker: str, days: int = LOOKBACK_DAYS) -> list:
    """从行情源取拆股与分红。返回 [{kind, ex_date, ratio|amount_per_share}]。

    **取不到就返回空列表并记日志**，不抛——一只票取不到不该让整轮日结失败。
    但"取不到"和"确实没有"在报告上必须能分辨，所以调用方会把它记进 note。
    """
    import os
    if os.environ.get("CIO_QUANT_MOCK") == "1":
        return []                       # 离线自测不编造公司行为
    try:
        import yfinance as yf
        t = yf.Ticker(str(ticker).upper())
        out = []
        sp = getattr(t, "splits", None)
        if sp is not None and len(sp):
            for ts, ratio in sp.items():
                out.append({"kind": SPLIT, "ex_date": ts.strftime("%Y-%m-%d"),
                            "ratio": float(ratio), "amount_per_share": None})
        dv = getattr(t, "dividends", None)
        if dv is not None and len(dv):
            for ts, amt in dv.items():
                out.append({"kind": DIVIDEND, "ex_date": ts.strftime("%Y-%m-%d"),
                            "ratio": None, "amount_per_share": float(amt)})
        return sorted(out, key=lambda a: a["ex_date"])
    except Exception as e:                                   # noqa: BLE001
        log.warning("%s 的公司行为取不到：%s", ticker, e)
        return []


def _already(pid: str, ticker: str, kind: str, ex_date: str) -> bool:
    with connect() as con:
        r = con.execute("SELECT 1 FROM book_corp_action WHERE portfolio_id=? AND "
                        "ticker=? AND kind=? AND ex_date=?",
                        (pid, ticker, kind, ex_date)).fetchone()
    return bool(r)


def _ex_close(ticker: str, ex_date: str, bars=None):
    """除权日的成交价。取不到返回 None（**不猜**）。"""
    df = bars if bars is not None else marks._raw_hist(ticker, LOOKBACK_DAYS + 20)
    if df is None or not len(df):
        return None
    for _, r in df.iterrows():
        if r["date"].strftime("%Y-%m-%d") == str(ex_date)[:10]:
            v = float(r["close"])
            return v if v > 0 else None
    return None


def apply_one(pid: str, pos: dict, action: dict, *, ex_close=None,
              actor: str = "system") -> dict:
    """把一次公司行为应用到一个持仓。**幂等**：已应用过就直接返回。"""
    tk = pos["ticker"]
    kind, ex = action["kind"], str(action["ex_date"])[:10]
    out = {"ticker": tk, "kind": kind, "ex_date": ex, "applied": False, "note": ""}

    if _already(pid, tk, kind, ex):
        out["note"] = "已应用过（幂等跳过）"
        return out
    if ex <= str(pos.get("opened_on") or "")[:10]:
        # 除权日不晚于建仓日 → 那天还没持有（或当天才买），不参与。
        out["note"] = f"除权日 {ex} 不晚于建仓日 {pos.get('opened_on')}，不适用"
        return out

    shares0 = int(pos["shares"])
    cost0 = float(pos["avg_cost"])
    now = stamp_utc()

    if kind == DIVIDEND:
        amt = float(action["amount_per_share"] or 0.0)
        if amt <= 0:
            out["note"] = "每股金额为 0，跳过"
            return out
        cash = shares0 * amt
        with connect() as con:
            con.execute(
                "INSERT INTO book_corp_action(portfolio_id,ticker,kind,ex_date,ratio,"
                "amount_per_share,shares_before,shares_after,avg_cost_before,"
                "avg_cost_after,cash_delta,ex_close,applied_at,note) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pid, tk, DIVIDEND, ex, None, amt, shares0, shares0, cost0, cost0,
                 cash, ex_close, now,
                 f"{shares0} 股 × {amt:.4f}/股 = {cash:.2f} 入现金"))
            con.execute("UPDATE book_portfolio SET cash=cash+? WHERE portfolio_id=?",
                        (cash, pid))
        out.update(applied=True, cash_delta=cash,
                   note=f"分红 {amt:.4f}/股 × {shares0} 股 = {cash:,.2f} 入现金")
        log.info("%s %s 分红入账 %.2f（除权日 %s）", pid, tk, cash, ex)
        return out

    # ---- 拆股 ----
    ratio = float(action["ratio"] or 0.0)
    if ratio <= 0 or abs(ratio - 1.0) < 1e-12:
        out["note"] = f"比例 {ratio} 无意义，跳过"
        return out
    if ex_close is None:
        # **拆了但账本没拆，比不拆更危险**：下一次盯市会拿拆后价乘拆前股数。
        out["note"] = (f"取不到除权日 {ex} 的价格 —— **本次拆股不应用**，"
                       f"{tk} 在盯市时会标缺价。硬取整会静默吞掉零股价值。")
        log.error("%s %s 拆股 %.4f 无法应用：缺除权日价格", pid, tk, ratio)
        return out

    exact = shares0 * ratio
    shares1 = int(exact + 1e-9)
    frac = exact - shares1
    cash = frac * float(ex_close)
    cost1 = cost0 / ratio
    with connect() as con:
        con.execute(
            "INSERT INTO book_corp_action(portfolio_id,ticker,kind,ex_date,ratio,"
            "amount_per_share,shares_before,shares_after,avg_cost_before,"
            "avg_cost_after,cash_delta,ex_close,applied_at,note) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, tk, SPLIT, ex, ratio, None, shares0, shares1, cost0, cost1,
             cash, float(ex_close), now,
             f"{shares0} → {shares1} 股，成本价 {cost0:.4f} → {cost1:.4f}"
             + (f"，零股 {frac:.4f} 折现 {cash:.2f}" if frac > 1e-9 else "")))
        con.execute("UPDATE book_position SET shares=?, avg_cost=? "
                    "WHERE portfolio_id=? AND ticker=? AND open=1",
                    (shares1, cost1, pid, tk))
        if cash > 0:
            con.execute("UPDATE book_portfolio SET cash=cash+? WHERE portfolio_id=?",
                        (cash, pid))
    out.update(applied=True, cash_delta=cash, shares_before=shares0,
               shares_after=shares1, ratio=ratio,
               note=f"拆股 {ratio:g}:1　{shares0} → {shares1} 股，"
                    f"成本价 {cost0:,.2f} → {cost1:,.2f}"
                    + (f"，零股折现 {cash:,.2f}" if cash > 0 else ""))
    log.info("%s %s 拆股 %.4f：%d → %d 股（除权日 %s）", pid, tk, ratio,
             shares0, shares1, ex)
    return out


def sync(portfolio_id: str, *, days: int = LOOKBACK_DAYS, actor: str = "system") -> dict:
    """检查全部持仓的公司行为并应用。**必须在盯市之前跑。**

    顺序反了的后果：盯市用的是**拆后价格**，而持仓还是**拆前股数**，
    于是那一天的市值凭空翻几倍或掉四分之三——一个完全正常的数字。
    """
    rows, blocked = [], []
    for pos in book.holdings(portfolio_id):
        tk = pos["ticker"]
        acts = fetch_actions(tk, days)
        if not acts:
            continue
        bars = marks._raw_hist(tk, days + 20)
        for a in acts:
            ex = str(a["ex_date"])[:10]
            if ex <= str(pos.get("opened_on") or "")[:10]:
                continue
            exc = _ex_close(tk, ex, bars) if a["kind"] == SPLIT else None
            # 持仓可能已被前一个行为改过，重新读一次。
            cur = book.holdings_map(portfolio_id).get(tk) or pos
            r = apply_one(portfolio_id, cur, a, ex_close=exc, actor=actor)
            if r["applied"]:
                rows.append(r)
            elif a["kind"] == SPLIT and exc is None and not _already(
                    portfolio_id, tk, SPLIT, ex):
                blocked.append(r)
    if rows:
        log.info("%s：应用了 %d 项公司行为", portfolio_id, len(rows))
    return {"portfolio_id": portfolio_id, "applied": rows, "blocked": blocked,
            "blocked_tickers": sorted({b["ticker"] for b in blocked})}


def history(portfolio_id: str, limit: int = 50) -> list:
    book.init()
    with connect() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM book_corp_action WHERE portfolio_id=? "
            "ORDER BY ex_date DESC, id DESC LIMIT ?", (portfolio_id, limit))]


def cash_from_actions(portfolio_id: str) -> float:
    """公司行为带来的现金合计 —— 对账恒等式 2 要用。"""
    book.init()
    with connect() as con:
        r = con.execute("SELECT COALESCE(SUM(cash_delta),0) FROM book_corp_action "
                        "WHERE portfolio_id=?", (portfolio_id,)).fetchone()
    return float(r[0] or 0.0)


def render(res: dict) -> str:
    if not res["applied"] and not res["blocked"]:
        return "公司行为：本轮无新增（拆股 / 分红）。"
    L = [f"公司行为：应用 {len(res['applied'])} 项"]
    for r in res["applied"]:
        L.append(f"  {r['ticker']:<6} {r['ex_date']}　{r['note']}")
    if res["blocked"]:
        L.append(f"  ⚠ {len(res['blocked'])} 项**无法应用** —— 这些标的本轮不可盯市：")
        for r in res["blocked"]:
            L.append(f"     {r['ticker']:<6} {r['note']}")
    return "\n".join(L)
