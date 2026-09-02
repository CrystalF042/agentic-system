"""盯市与净值 —— 把账本变成一条**可以对得上的曲线**。

    公司行为  →  盯市  →  对账  →  盈亏表
    ↑ 必须在前

## 三条不能让步的规则

一、**有持仓取不到价 → 当天 NAV 是 NULL，不是"按剩下的算"。**
    漏掉一只票的市值，NAV 会小一截。曲线上不会有缺口，只会出现
    一个凭空的亏损日，第二天又"涨回来"。两个假的极端值，
    而每一个数字看起来都正常。所以缺价的那天写 `complete=0`、`nav=NULL`，
    **让空白是空白**。

二、**前一日不完整 → 当日 day_pnl 是 None。**
    拿今天的 NAV 减三天前的 NAV，得到的是三天的变动，却挂在今天头上。
    这个数字不会错到离谱，只会让某一天看起来特别好或特别糟——
    最难发现的那种错。

三、**基准必须含息。** 组合通过 `corp_actions` 把分红记成现金收入，
    所以对比的基准也必须含息，否则超额收益每年凭空多出约 1.5%。
    `yfinance` 的 `auto_adjust=True` 复权收盘价**就是**总回报序列——
    这是全系统唯一一处**账本侧刻意使用复权价**的地方，
    因为这里要的正是"含息的累计回报"，不是"某天的成交价"。

## 还有一件必须一起报的事

组合早期大部分是现金。拿一个 3% 仓位的组合去比 100% 仓位的基准，
"跑输 20 个点"这句话没有信息量。所以每一行都同时给出**平均仓位**，
超额收益永远和它一起出现。
"""
from __future__ import annotations

from . import book, marks
from .db import connect
from .utils import get_logger, stamp_utc

log = get_logger("cio.valuation")

BENCH_BASIS = "TOTAL_RETURN(adjusted close)"


def _bench_series(symbol: str, days: int = 400):
    """基准的**复权**收盘序列 = 总回报。取不到返回 None。"""
    import os
    if os.environ.get("CIO_QUANT_MOCK") == "1":
        return None
    try:
        import pandas as pd
        import yfinance as yf
        h = yf.Ticker(symbol).history(period=f"{days}d", auto_adjust=True)
        if h is None or not len(h):
            return None
        h = h.reset_index().rename(columns={"Date": "date", "Close": "close"})
        h["date"] = pd.to_datetime(h["date"]).dt.tz_localize(None)
        return h[["date", "close"]].sort_values("date").reset_index(drop=True)
    except Exception as e:                                   # noqa: BLE001
        log.warning("基准 %s 取不到：%s", symbol, e)
        return None


def _on_or_before(df, day: str):
    """取 `day` 当天或之前最近一根。返回 (close, date) 或 (None, "")。"""
    if df is None or not len(df):
        return None, ""
    d0 = str(day)[:10]
    hit = None
    for _, r in df.iterrows():
        if r["date"].strftime("%Y-%m-%d") <= d0:
            hit = r
        else:
            break
    if hit is None:
        return None, ""
    return float(hit["close"]), hit["date"].strftime("%Y-%m-%d")


def _prev_nav(pid: str, day: str) -> dict:
    with connect() as con:
        r = con.execute("SELECT date, nav, complete FROM book_nav WHERE portfolio_id=? "
                        "AND date<? ORDER BY date DESC LIMIT 1",
                        (pid, str(day)[:10])).fetchone()
    return dict(r) if r else {}


_SHAPE = ("ok", "date", "portfolio_id", "cash", "holdings_value", "nav",
          "day_pnl", "cum_return", "bench_close", "bench_date",
          "bench_cum_return", "bench_basis", "bench_open_close", "invested_pct",
          "complete", "unpriced", "positions", "note", "initial_capital", "prev")
"""`mark()` 的返回形状 —— **每一条路径都返回全部这些键。**

真机上炸过一次：早退路径只返回 `{"ok": False, "note": ...}`，
调用方拿 `m['nav']` 直接 KeyError。

一个函数的返回形状随执行路径变化，就是给每个调用方都埋了一个坑：
正常路径下测得好好的，某个边界一走到就崩——而崩在调用方那一行，
看起来像调用方写错了。所以形状固定，缺的值用 None，**不是缺键**。
"""


def _empty(pid: str, day: str, note: str, ok: bool = False) -> dict:
    """形状完整的"没算出来"。缺的是 None，不是缺键。"""
    return {"ok": ok, "date": day, "portfolio_id": pid, "cash": None,
            "holdings_value": None, "nav": None, "day_pnl": None,
            "cum_return": None, "bench_close": None, "bench_date": "",
            "bench_cum_return": None, "bench_basis": BENCH_BASIS,
            "bench_open_close": None, "invested_pct": None, "complete": False,
            "unpriced": [], "positions": [], "note": note,
            "initial_capital": None, "prev": {}}


def mark(portfolio_id: str, *, on: str = "", prices: dict = None,
         bench_close=None, use_bench: bool = True) -> dict:
    """盯市一天：写 `book_mark` 与 `book_nav`。幂等（同一天重跑覆盖当天）。

    `prices` / `bench_close` 供测试注入；不传就去取未复权收盘 / 复权基准。
    """
    from .config import market_date
    book.init()
    pid = portfolio_id
    day = str(on or market_date())[:10]
    p = book.portfolio_row(pid)
    if not p:
        return _empty(pid, day, f"{pid} 未开账")

    # **盯市日不能早于开账日。** 写进去的话曲线上会多出一段账本还不存在时的
    # 净值——它自己看起来完全正常（就是初始资金），却会成为后一天
    # 当日盈亏的基准，把开账那天的建仓也算成"当天的涨跌"。
    opened = str(p.get("opened_on") or "")[:10]
    if opened and day < opened:
        return _empty(pid, day,
                      f"盯市日 {day} 早于开账日 {opened} —— 拒绝写入。"
                      f"账本还不存在的日子不该有净值。"
                      f"（开账日填成了未来的日期？见 run_rebalance.py "
                      f"--open-book --opened-on ... --reset-open-date）")

    hs = book.holdings(pid)
    px = ({str(k).upper(): v for k, v in prices.items()} if prices is not None
          else marks.price_map([h["ticker"] for h in hs]) if hs else {})

    mv, unpriced, lines = 0.0, [], []
    for h in hs:
        v = px.get(h["ticker"])
        ok = v is not None and float(v) > 0
        m = (h["shares"] * float(v)) if ok else None
        if ok:
            mv += m
        else:
            unpriced.append(h["ticker"])
        lines.append({"ticker": h["ticker"], "shares": h["shares"],
                      "close": (float(v) if ok else None), "market_value": m,
                      "priced": 1 if ok else 0, "avg_cost": h["avg_cost"],
                      "opened_on": h["opened_on"],
                      "last_evaluated_on": h["last_evaluated_on"]})

    complete = not unpriced
    cash = float(p["cash"] or 0.0)
    nav = (cash + mv) if complete else None
    cap = float(p["initial_capital"] or 0.0)

    # ---- 基准：复权收盘（= 总回报），并把开账日那根钉住 ----
    # `use_bench=False` 是**显式说"这次不要基准"**，与"基准取不到"分开。
    # 没有这个开关时，`bench_close=None` 同时表示"没传"和"没有"，
    # 于是同一个测试在有网的机器上走取数、在无网的机器上走跳过——
    # **同一份断言在两台机器上测的是两件事**（真机上就这么误报过一次）。
    bopen = p.get("bench_open_close")
    bclose, bdate = (bench_close, day) if bench_close is not None else (None, "")
    bench_cum = None
    if bench_close is None and use_bench:
        s = _bench_series(p["benchmark_symbol"] or book.BENCHMARK_SYMBOL)
        bclose, bdate = _on_or_before(s, day)
        if bopen in (None, 0) and s is not None:
            bopen, _ = _on_or_before(s, p["opened_on"])
            if bopen:
                with connect() as con:
                    con.execute("UPDATE book_portfolio SET bench_open_close=? "
                                "WHERE portfolio_id=?", (bopen, pid))
    if bclose and bopen:
        bench_cum = bclose / float(bopen) - 1.0

    prev = _prev_nav(pid, day)
    day_pnl = None
    pnl_note = ""
    if nav is not None:
        if not prev:
            day_pnl = nav - cap            # 开账后第一天，基准是初始资金
        elif prev.get("complete") and prev.get("nav") is not None:
            day_pnl = nav - float(prev["nav"])
        else:
            # **不拿今天减一个更早的完整日**：那是多日变动挂在一天头上。
            pnl_note = (f"上一有效净值是 {prev.get('date')}（不完整或缺价），"
                        f"当日盈亏**不计算** —— 跨多日的变动挂在一天上，"
                        f"数字不会离谱，只会让某天看起来特别好或特别糟。")

    cum = (nav / cap - 1.0) if (nav is not None and cap) else None
    invested = (mv / nav) if (nav and nav > 0 and complete) else None
    note = pnl_note
    if unpriced:
        note = (f"{len(unpriced)} 只持仓取不到价（{'、'.join(unpriced)}）—— "
                f"NAV 不可计算，**不按剩下的算**。" + (" " + note if note else ""))

    now = stamp_utc()
    with connect() as con:
        con.execute("DELETE FROM book_mark WHERE date=? AND portfolio_id=?", (day, pid))
        for r in lines:
            con.execute("INSERT INTO book_mark(date,portfolio_id,ticker,close,shares,"
                        "market_value,priced) VALUES(?,?,?,?,?,?,?)",
                        (day, pid, r["ticker"], r["close"], r["shares"],
                         r["market_value"], r["priced"]))
        con.execute("INSERT OR REPLACE INTO book_nav(date,portfolio_id,cash,"
                    "holdings_value,nav,day_pnl,cum_return,bench_close,"
                    "bench_cum_return,bench_basis,invested_pct,n_positions,"
                    "n_unpriced,complete,note,created_at) "
                    "VALUES(" + ",".join(["?"] * 16) + ")",
                    (day, pid, cash, (mv if complete else None), nav, day_pnl, cum,
                     bclose, bench_cum, BENCH_BASIS, invested, len(hs),
                     len(unpriced), 1 if complete else 0, note, now))

    # **写完整条重算。** day_pnl 是派生量，见 recompute_pnl 的说明。
    recompute_pnl(pid)
    with connect() as con:
        fresh = con.execute("SELECT day_pnl, note FROM book_nav WHERE portfolio_id=? "
                            "AND date=?", (pid, day)).fetchone()
    if fresh:
        day_pnl, note = fresh[0], (fresh[1] or "")

    log.info("%s 盯市 %s：持仓 %d，缺价 %d，NAV %s", pid, day, len(hs), len(unpriced),
             "不可计算" if nav is None else f"{nav:,.2f}")
    return {"ok": True, "date": day, "portfolio_id": pid, "cash": cash,
            "holdings_value": (mv if complete else None), "nav": nav,
            "day_pnl": day_pnl, "cum_return": cum, "bench_close": bclose,
            "bench_date": bdate, "bench_cum_return": bench_cum,
            "bench_basis": BENCH_BASIS, "bench_open_close": bopen,
            "invested_pct": invested, "complete": complete,
            "unpriced": unpriced, "positions": lines, "note": note,
            "initial_capital": cap, "prev": prev}


def recompute_pnl(portfolio_id: str) -> int:
    """把整条曲线的 `day_pnl` 与说明按顺序重算一遍。返回改动行数。

    **为什么不能只在写入当天时算一次。** 补一个更早的日子（`--date` 回填、
    或者补跑一天漏掉的盯市）之后，它后面那些行的"前一日"就变了，
    而它们的 `day_pnl` 还是按旧的前一日算出来的。

    真机上就见到过：08-31 那行显示当日 +69，而它算的其实是"相对初始资金"，
    因为写它的时候 08-28~30 还不存在。**数字完全正常，只是答非所问。**

    所以 `day_pnl` 在这里被当作**派生量**：每次盯市后整条重算，
    让"当日盈亏 = 相对上一有效净值"这个定义在任何写入顺序下都成立。
    """
    book.init()
    p = book.portfolio_row(portfolio_id)
    cap = float((p or {}).get("initial_capital") or 0.0)
    with connect() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT date, nav, complete, n_unpriced, day_pnl, note FROM book_nav "
            "WHERE portfolio_id=? ORDER BY date", (portfolio_id,))]
        unpriced = {}
        for d, tk in con.execute(
                "SELECT date, ticker FROM book_mark WHERE portfolio_id=? AND priced=0",
                (portfolio_id,)):
            unpriced.setdefault(d, []).append(tk)

    prev = None
    changed = 0
    for r in rows:
        pnl, pnl_note = None, ""
        if r["nav"] is not None:
            if prev is None:
                pnl = float(r["nav"]) - cap
            elif prev.get("complete") and prev.get("nav") is not None:
                pnl = float(r["nav"]) - float(prev["nav"])
            else:
                pnl_note = (f"上一有效净值是 {prev['date']}（不完整或缺价），"
                            f"当日盈亏**不计算** —— 跨多日的变动挂在一天上，"
                            f"数字不会离谱，只会让某天看起来特别好或特别糟。")
        up = unpriced.get(r["date"], [])
        note = ""
        if up:
            note = (f"{len(up)} 只持仓取不到价（{'、'.join(sorted(up))}）—— "
                    f"NAV 不可计算，**不按剩下的算**。")
        if pnl_note:
            note = (note + " " + pnl_note).strip()
        if (r["day_pnl"] != pnl) or ((r["note"] or "") != note):
            with connect() as con:
                con.execute("UPDATE book_nav SET day_pnl=?, note=? "
                            "WHERE portfolio_id=? AND date=?",
                            (pnl, note, portfolio_id, r["date"]))
            changed += 1
        prev = r
    if changed:
        log.info("%s：重算了 %d 行的当日盈亏（补写更早的日子会让后面几行的"
                 "『前一日』发生变化）", portfolio_id, changed)
    return changed


def series(portfolio_id: str, limit: int = 400) -> list:
    book.init()
    with connect() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM book_nav WHERE portfolio_id=? ORDER BY date DESC LIMIT ?",
            (portfolio_id, limit))][::-1]


def statement(portfolio_id: str, *, on: str = "", mark_result: dict = None,
              use_bench: bool = True) -> dict:
    """盈亏表：账户层 + 逐仓位。**超额收益永远和平均仓位一起出现。**"""
    from . import corp_actions
    m = mark_result or mark(portfolio_id, on=on, use_bench=use_bench)
    if not m.get("ok"):
        return {"ok": False, "note": m.get("note", ""), "portfolio_id": portfolio_id,
                "as_of": m.get("date", on), "positions": []}
    p = book.portfolio_row(portfolio_id)
    day = m["date"]

    rows = []
    for r in m["positions"]:
        mvv = r["market_value"]
        cost = r["shares"] * float(r["avg_cost"])
        unreal = None if mvv is None else mvv - cost
        rows.append({
            **r,
            "cost_basis": cost,
            "unrealized_pnl": unreal,
            "unrealized_pct": (None if (unreal is None or cost <= 0)
                               else unreal / cost),
            "weight": (None if (mvv is None or not m["nav"]) else mvv / m["nav"]),
            "days_held": _days(r["opened_on"], day),
            "days_since_review": _days(r["last_evaluated_on"], day),
        })

    with connect() as con:
        realized = float(con.execute(
            "SELECT COALESCE(SUM(realized_pnl),0) FROM book_trade WHERE portfolio_id=?",
            (portfolio_id,)).fetchone()[0] or 0.0)
        n_trades = int(con.execute(
            "SELECT COUNT(*) FROM book_trade WHERE portfolio_id=?",
            (portfolio_id,)).fetchone()[0] or 0)

    excess = None
    if m["cum_return"] is not None and m["bench_cum_return"] is not None:
        excess = m["cum_return"] - m["bench_cum_return"]

    return {"ok": True, "portfolio_id": portfolio_id, "as_of": day,
            "currency": p.get("currency"), "opened_on": p.get("opened_on"),
            "initial_capital": m["initial_capital"], "cash": m["cash"],
            "holdings_value": m["holdings_value"], "nav": m["nav"],
            "day_pnl": m["day_pnl"], "cum_return": m["cum_return"],
            "realized_pnl": realized, "n_trades": n_trades,
            "bench_symbol": p.get("benchmark_symbol"),
            "bench_basis": m["bench_basis"], "bench_cum_return": m["bench_cum_return"],
            "excess": excess, "invested_pct": m["invested_pct"],
            "complete": m["complete"], "unpriced": m["unpriced"],
            "note": m["note"], "positions": rows,
            "dividends_cash": corp_actions.cash_from_actions(portfolio_id),
            "recent_actions": corp_actions.history(portfolio_id, 10)}


def _days(a, b):
    from datetime import datetime
    try:
        d1 = datetime.strptime(str(a)[:10], "%Y-%m-%d").date()
        d2 = datetime.strptime(str(b)[:10], "%Y-%m-%d").date()
    except Exception:                                        # noqa: BLE001
        return None
    return (d2 - d1).days
