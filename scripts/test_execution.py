#!/usr/bin/env python3
"""Build 2 自测 —— 批准 → 成交 → 入账。

    python scripts/test_execution.py

行情用注入的 session（`{"date":..., "open":...}`），**不联网**，
所以每条断言测的是逻辑，不是运气。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _no_network                                            # noqa: E402,F401
"""测试期间禁止联网 —— 靠真实行情才通过的断言，换台机器就是另一个结果。"""

from cio import db                                              # noqa: E402

_TMP = Path(tempfile.mkdtemp(prefix="cio-b2-")) / "test.db"
db.DB_PATH = _TMP

from cio import book, execution, proposal_store, rebalance      # noqa: E402
from cio.db import connect                                      # noqa: E402

PID = "TEST_EX"
OK, BAD = [], []
_N = [0]


def check(name, fn):
    try:
        fn()
        OK.append(name)
        print(f"  OK    {name}")
    except AssertionError as e:
        BAD.append((name, str(e)))
        print(f"  FAIL  {name}\n          {e}")
    except Exception as e:                                       # noqa: BLE001
        BAD.append((name, f"{type(e).__name__}: {e}"))
        print(f"  ERR   {name}\n          {type(e).__name__}: {e}")


def reset_book(cash=100000.0):
    book.init()
    proposal_store.init()
    with connect() as con:
        con.execute("DELETE FROM book_portfolio WHERE portfolio_id=?", (PID,))
        con.execute("DELETE FROM book_position WHERE portfolio_id=?", (PID,))
        con.execute("DELETE FROM book_trade WHERE portfolio_id=?", (PID,))
    book.open_book(PID, capital=cash, opened_on="2026-08-31")


def hold(ticker, shares, cost, run_id="pc-old"):
    with connect() as con:
        con.execute("INSERT INTO book_position(portfolio_id,ticker,shares,avg_cost,"
                    "opened_on,opened_run_id,last_run_id,last_evaluated_on,"
                    "realized_pnl,open) VALUES(?,?,?,?,?,?,?,?,0,1)",
                    (PID, ticker, shares, cost, "2026-08-20", run_id, run_id,
                     "2026-08-20"))


def propose(ticker, *, delta, target, current=0, price=180.0, weight=0.02,
            comp="PARTIAL", decision_date="2026-08-31", approve=True):
    """造一条提案并（默认）批准它。返回提案行。"""
    _N[0] += 1
    row = {"ticker": ticker, "basis": rebalance.TARGET, "reason": "",
           "target_weight": weight, "target_shares": target,
           "current_shares": current, "delta_shares": delta,
           "decision_price": price, "est_value": target * price,
           "action": (rebalance.BUY if delta > 0 else
                      (rebalance.EXIT if target == 0 else rebalance.SELL)),
           "days_since_evaluated": 1, "thesis_id": 1}
    p = proposal_store.record(run_id=f"pc-{_N[0]}", portfolio_id=PID, row=row,
                              nav=100000.0, decision_date=decision_date,
                              expires=rebalance.expires_on(decision_date),
                              compliance={"status": comp})
    if approve:
        p = proposal_store.transition(p["id"], proposal_store.APPROVED, actor="test")
    return p


SESS = {"date": "2026-09-01", "open": 182.0}


# ================================================================ 状态前置
def t_only_approved_executes():
    reset_book()
    p = propose("AAA", delta=5, target=5, approve=False)
    r = execution.execute_one(p, session=SESS)
    assert r["status"] == execution.FAILED, r
    assert "APPROVED" in r["reason"]


def t_no_session_waits():
    """下一个交易日还没到 → 等，**不拿今天的价硬成交**，状态保持已批准。"""
    reset_book()
    p = propose("BBB", delta=5, target=5)
    r = execution.execute_one(p, session={})
    assert r["status"] == execution.WAITING, r
    assert proposal_store.get(p["id"])["state"] == proposal_store.APPROVED


def t_session_without_open_fails():
    reset_book()
    p = propose("CCC", delta=5, target=5)
    r = execution.execute_one(p, session={"date": "2026-09-01", "open": None})
    assert r["status"] == execution.FAILED and "开盘价" in r["reason"], r


# ================================================================ 买入
def t_buy_fills_and_books():
    reset_book()
    p = propose("NVDA", delta=10, target=10, price=180.0)
    r = execution.execute_one(p, session=SESS)
    assert r["status"] == execution.FILLED, r
    h = book.holdings_map(PID)["NVDA"]
    assert h["shares"] == 10 and abs(h["avg_cost"] - 182.0) < 1e-9, h
    assert abs(book.cash(PID) - (100000.0 - 1820.0)) < 1e-9, book.cash(PID)
    got = proposal_store.get(p["id"])
    assert got["state"] == proposal_store.EXECUTED
    assert got["execution_date"] == "2026-09-01" and got["execution_price"] == 182.0
    assert got["trade_id"] == r["trade_id"]


def t_fill_price_is_open_not_decision():
    """成交价必须是**开盘价**，不是决策日收盘价——后者是用当天信息买当天。"""
    reset_book()
    p = propose("DDD", delta=10, target=10, price=180.0)
    r = execution.execute_one(p, session={"date": "2026-09-01", "open": 171.0})
    assert r["execution_price"] == 171.0 and r["execution_price"] != 180.0
    assert abs(r["gap_pct"] - (171.0 - 180.0) / 180.0) < 1e-12, r["gap_pct"]


def t_execute_is_idempotent():
    reset_book()
    p = propose("EEE", delta=4, target=4)
    a = execution.execute_one(p, session=SESS)
    p2 = proposal_store.get(p["id"])
    b = execution.execute_one({**p2, "state": proposal_store.APPROVED}, session=SESS)
    assert a["status"] == execution.FILLED and b["status"] == execution.SKIPPED, (a, b)
    with connect() as con:
        n = con.execute("SELECT COUNT(*) FROM book_trade WHERE portfolio_id=? "
                        "AND ticker='EEE'", (PID,)).fetchone()[0]
    assert n == 1, f"重跑产生了 {n} 笔成交"


def t_insufficient_cash_does_not_partial_fill():
    """钱不够就整条不成交。**少买一半是一个没人批准过的仓位。**"""
    reset_book(cash=1000.0)
    p = propose("FFF", delta=10, target=10, price=180.0)
    r = execution.execute_one(p, session=SESS)
    assert r["status"] == execution.FAILED and "现金不足" in r["reason"], r
    assert "FFF" not in book.holdings_map(PID)
    assert abs(book.cash(PID) - 1000.0) < 1e-9, "失败不能动现金"


def t_avg_cost_on_add():
    reset_book()
    hold("GGG", 10, 100.0)
    p = propose("GGG", delta=10, target=20, current=10, price=200.0)
    execution.execute_one(p, session={"date": "2026-09-01", "open": 200.0})
    h = book.holdings_map(PID)["GGG"]
    assert h["shares"] == 20 and abs(h["avg_cost"] - 150.0) < 1e-9, h


# ================================================================ 卖出
def t_partial_sell_records_realized():
    reset_book()
    hold("HHH", 10, 100.0)
    p = propose("HHH", delta=-4, target=6, current=10, price=120.0)
    r = execution.execute_one(p, session={"date": "2026-09-01", "open": 120.0})
    assert r["status"] == execution.FILLED
    h = book.holdings_map(PID)["HHH"]
    assert h["shares"] == 6, h
    assert abs(h["avg_cost"] - 100.0) < 1e-9, "减持不改成本价（否则盈亏算两次）"
    assert abs(r["realized_pnl"] - (120.0 - 100.0) * 4) < 1e-9, r["realized_pnl"]
    assert abs(book.cash(PID) - (100000.0 + 480.0)) < 1e-9


def t_exit_keeps_the_row():
    """**平仓是置 open=0 并留行，不是删行。** 删掉归因的分母就没了。"""
    reset_book()
    hold("III", 10, 100.0)
    p = propose("III", delta=-10, target=0, current=10, price=90.0)
    execution.execute_one(p, session={"date": "2026-09-01", "open": 90.0})
    assert "III" not in book.holdings_map(PID)
    with connect() as con:
        row = con.execute("SELECT shares, open, closed_on, realized_pnl FROM "
                          "book_position WHERE portfolio_id=? AND ticker='III'",
                          (PID,)).fetchone()
    assert row is not None, "持仓行被删掉了"
    assert row[1] == 0 and row[2] == "2026-09-01"
    assert abs(row[3] - (-100.0)) < 1e-9, row[3]


def t_no_short_selling():
    reset_book()
    hold("JJJ", 3, 100.0)
    p = propose("JJJ", delta=-10, target=0, current=10, price=100.0)
    r = execution.execute_one(p, session={"date": "2026-09-01", "open": 100.0})
    assert r["status"] == execution.FAILED and "卖空" in r["reason"], r
    assert book.holdings_map(PID)["JJJ"]["shares"] == 3


# ================================================================ run()
def t_run_sells_before_buys():
    reset_book()
    hold("KKK", 10, 100.0)
    propose("KKK", delta=-10, target=0, current=10, price=100.0)
    propose("LLL", delta=5, target=5, price=100.0)
    res = execution.run(PID, today="2026-09-02",
                        sessions={"KKK": {"date": "2026-09-01", "open": 100.0},
                                  "LLL": {"date": "2026-09-01", "open": 100.0}})
    order = [r["ticker"] for r in res["rows"]]
    assert order.index("KKK") < order.index("LLL"), order


def t_run_buy_cannot_use_same_session_proceeds():
    """美股 T+1 交收：同一场卖出的回款当天没到账，不能拿来买。"""
    reset_book(cash=100.0)
    hold("MMM", 100, 10.0)
    propose("MMM", delta=-100, target=0, current=100, price=50.0)
    propose("NNN", delta=10, target=10, price=50.0)
    res = execution.run(PID, today="2026-09-02",
                        sessions={"MMM": {"date": "2026-09-01", "open": 50.0},
                                  "NNN": {"date": "2026-09-01", "open": 50.0}})
    by = {r["ticker"]: r for r in res["rows"]}
    assert by["MMM"]["status"] == execution.FILLED
    assert by["NNN"]["status"] == execution.FAILED, by["NNN"]
    assert "T+1" in by["NNN"]["reason"] or "回款" in by["NNN"]["reason"]


def t_failed_is_terminal():
    assert proposal_store.TRANSITIONS[proposal_store.EXECUTION_FAILED] == frozenset()


def t_expired_cannot_execute():
    reset_book()
    p = propose("OOO", delta=5, target=5, decision_date="2026-08-01")
    res = execution.run(PID, today="2026-09-02",
                        sessions={"OOO": {"date": "2026-09-01", "open": 100.0}})
    assert proposal_store.get(p["id"])["state"] == proposal_store.EXPIRED
    assert not any(r["ticker"] == "OOO" and r["status"] == execution.FILLED
                   for r in res["rows"])


# ================================================================ 现金恒等式
def t_cash_identity():
    """现金 = 初始 + Σ(交易现金流)。对账的第一条恒等式，现在就要成立。"""
    reset_book()
    propose("PPP", delta=10, target=10, price=100.0)
    execution.run(PID, today="2026-09-02",
                  sessions={"PPP": {"date": "2026-09-01", "open": 100.0}})
    with connect() as con:
        flows = con.execute("SELECT COALESCE(SUM(cash_flow),0) FROM book_trade "
                            "WHERE portfolio_id=?", (PID,)).fetchone()[0]
    assert abs(book.cash(PID) - (100000.0 + flows)) < 1e-6, (book.cash(PID), flows)


def t_trade_carries_lineage():
    """每笔交易都能回到那次 PC 运行 —— 否则账本和台账是两本对不上的账。"""
    reset_book()
    p = propose("QQQ", delta=3, target=3, price=100.0)
    execution.execute_one(p, session={"date": "2026-09-01", "open": 100.0})
    with connect() as con:
        row = con.execute("SELECT run_id, proposal_id, execution_price_basis "
                          "FROM book_trade WHERE portfolio_id=? AND ticker='QQQ'",
                          (PID,)).fetchone()
    assert row[0] == p["run_id"] and row[1] == p["id"], row
    assert row[2] == "T+1_OPEN", row[2]


# ================================================================ 迁移
def t_old_schema_migrates():
    """旧库的 pc_lineage 没有 run_id 列时，init() 必须能补上而不是炸。

    真实事故：迁移写在 executescript 之后，而 executescript 里的唯一索引
    正好用到那一列 —— 脚本先炸，迁移永远跑不到。
    """
    import sqlite3
    tmp = Path(tempfile.mkdtemp(prefix="cio-old-")) / "old.db"
    con = sqlite3.connect(tmp)
    con.executescript("CREATE TABLE pc_lineage (id INTEGER PRIMARY KEY, "
                      "as_of_date TEXT, portfolio_id TEXT, ticker TEXT);")
    con.commit()
    con.close()
    old = db.DB_PATH
    db.DB_PATH = tmp
    try:
        from cio import pc_ledger
        pc_ledger.init()
        with connect() as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(pc_lineage)")}
        assert "run_id" in cols, cols
    finally:
        db.DB_PATH = old


TESTS = [
    ("只有已批准的才能成交", t_only_approved_executes),
    ("**下一个交易日没到 → 等，不硬成交**", t_no_session_waits),
    ("有 K 线但无开盘价 → 未成交（不静默跳过）", t_session_without_open_fails),
    ("买入成交并入账（持仓/现金/提案状态）", t_buy_fills_and_books),
    ("**成交价用开盘价，不是决策日收盘价**", t_fill_price_is_open_not_decision),
    ("重跑执行不产生第二笔成交", t_execute_is_idempotent),
    ("现金不足整条不成交，不部分成交", t_insufficient_cash_does_not_partial_fill),
    ("加仓后成本价按加权平均", t_avg_cost_on_add),
    ("减持记已实现盈亏，不改成本价", t_partial_sell_records_realized),
    ("**平仓留行、置 open=0**", t_exit_keeps_the_row),
    ("不做卖空", t_no_short_selling),
    ("先卖后买", t_run_sells_before_buys),
    ("**同场卖出回款不能当天用来买**", t_run_buy_cannot_use_same_session_proceeds),
    ("成交失败是终态，需重新提案", t_failed_is_terminal),
    ("过期的批准不会成交", t_expired_cannot_execute),
    ("现金恒等式：现金 = 初始 + Σ交易现金流", t_cash_identity),
    ("每笔交易带 run_id 与提案号", t_trade_carries_lineage),
    ("**旧库缺列时迁移能跑到**", t_old_schema_migrates),
]

print("=" * 72)
print("Build 2 自测：批准 → T+1 开盘成交 → 入账")
print("=" * 72)
for _n, _f in TESTS:
    check(_n, _f)

print("\n" + "=" * 72)
if BAD:
    print(f"{len(BAD)} 项失败 / 共 {len(TESTS)}")
    for n, e in BAD:
        print(f"  · {n}\n      {e}")
    raise SystemExit(1)
print(f"全部 {len(OK)} 项通过。")
raise SystemExit(0)
