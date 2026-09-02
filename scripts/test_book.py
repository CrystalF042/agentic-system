#!/usr/bin/env python3
"""Build 3 自测 —— 公司行为、盯市、净值曲线、对账。

    python scripts/test_book.py

价格全部注入，不联网。最重要的一条是 4:1 拆股**不能**产生 −75%。
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

_TMP = Path(tempfile.mkdtemp(prefix="cio-b3-")) / "test.db"
db.DB_PATH = _TMP

from cio import book, corp_actions, recon, valuation            # noqa: E402
from cio.db import connect                                      # noqa: E402

PID = "TEST_BK"
OK, BAD = [], []


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


def reset(cash=100000.0, opened="2026-08-31"):
    book.init()
    with connect() as con:
        for t in ("book_portfolio", "book_position", "book_trade",
                  "book_corp_action", "book_mark", "book_nav"):
            con.execute(f"DELETE FROM {t} WHERE portfolio_id=?", (PID,))
    book.open_book(PID, capital=cash, opened_on=opened)


def hold(ticker, shares, cost, opened="2026-08-20"):
    with connect() as con:
        con.execute("INSERT INTO book_position(portfolio_id,ticker,shares,avg_cost,"
                    "opened_on,opened_run_id,last_run_id,last_evaluated_on,"
                    "realized_pnl,open) VALUES(?,?,?,?,?,?,?,?,0,1)",
                    (PID, ticker, shares, cost, opened, "r0", "r0", opened))


def trade(ticker, side, shares, price, run="r0", date="2026-08-20"):
    flow = -(shares * price) if side == "BUY" else (shares * price)
    with connect() as con:
        con.execute("INSERT INTO book_trade(run_id,portfolio_id,ticker,side,shares,"
                    "decision_date,decision_price,execution_date,execution_price,"
                    "execution_price_basis,commission,slippage,cash_flow,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,0,0,?,?)",
                    (run, PID, ticker, side, shares, date, price, date, price,
                     "T+1_OPEN", flow, date))
        con.execute("UPDATE book_portfolio SET cash=cash+? WHERE portfolio_id=?",
                    (flow, PID))


# ================================================================ 公司行为
def t_split_does_not_show_minus_75():
    """**本 build 的核心。** 4:1 拆股：100 股 @800 → 400 股 @200，市值不变。

    不处理的话账本说 100 股、市场说 200 元 → 持仓当天显示 −75%，
    没有任何报错。
    """
    reset()
    hold("SPL", 100, 800.0)
    before = 100 * 800.0
    pos = book.holdings_map(PID)["SPL"]
    r = corp_actions.apply_one(PID, pos,
                              {"kind": corp_actions.SPLIT, "ex_date": "2026-09-01",
                               "ratio": 4.0, "amount_per_share": None},
                              ex_close=200.0)
    assert r["applied"], r
    h = book.holdings_map(PID)["SPL"]
    assert h["shares"] == 400, h
    assert abs(h["avg_cost"] - 200.0) < 1e-9, h
    m = valuation.mark(PID, on="2026-09-01", prices={"SPL": 200.0}, use_bench=False)
    assert abs(m["holdings_value"] - before) < 1e-6, \
        f"拆股后市值变了：{m['holdings_value']} vs {before}"


def t_split_fraction_to_cash():
    """3:2 拆 5 股 = 7.5 股 → 7 股 + 零股折现（券商就是这么做的）。"""
    reset()
    hold("FRC", 5, 100.0)
    cash0 = book.cash(PID)
    r = corp_actions.apply_one(PID, book.holdings_map(PID)["FRC"],
                              {"kind": corp_actions.SPLIT, "ex_date": "2026-09-01",
                               "ratio": 1.5, "amount_per_share": None},
                              ex_close=66.67)
    assert book.holdings_map(PID)["FRC"]["shares"] == 7, book.holdings_map(PID)
    assert abs(book.cash(PID) - cash0 - 0.5 * 66.67) < 1e-6, book.cash(PID)


def t_split_without_price_refuses():
    """**取不到除权日价就不应用。** 硬取整会静默吞掉零股价值。"""
    reset()
    hold("NOP", 5, 100.0)
    r = corp_actions.apply_one(PID, book.holdings_map(PID)["NOP"],
                              {"kind": corp_actions.SPLIT, "ex_date": "2026-09-01",
                               "ratio": 1.5, "amount_per_share": None},
                              ex_close=None)
    assert not r["applied"] and "不应用" in r["note"], r
    assert book.holdings_map(PID)["NOP"]["shares"] == 5


def t_dividend_into_cash():
    reset()
    hold("DIV", 100, 50.0)
    cash0 = book.cash(PID)
    r = corp_actions.apply_one(PID, book.holdings_map(PID)["DIV"],
                              {"kind": corp_actions.DIVIDEND, "ex_date": "2026-09-01",
                               "ratio": None, "amount_per_share": 0.75})
    assert r["applied"] and abs(book.cash(PID) - cash0 - 75.0) < 1e-9, book.cash(PID)
    assert abs(corp_actions.cash_from_actions(PID) - 75.0) < 1e-9


def t_action_idempotent():
    reset()
    hold("IDM", 100, 50.0)
    a = {"kind": corp_actions.DIVIDEND, "ex_date": "2026-09-01",
         "ratio": None, "amount_per_share": 0.5}
    corp_actions.apply_one(PID, book.holdings_map(PID)["IDM"], a)
    cash1 = book.cash(PID)
    r2 = corp_actions.apply_one(PID, book.holdings_map(PID)["IDM"], a)
    assert not r2["applied"] and abs(book.cash(PID) - cash1) < 1e-9, r2


def t_action_before_purchase_not_applied():
    """除权日不晚于建仓日 → 那天还没持有，拿不到。"""
    reset()
    hold("OLD", 100, 50.0, opened="2026-09-05")
    r = corp_actions.apply_one(PID, book.holdings_map(PID)["OLD"],
                              {"kind": corp_actions.DIVIDEND, "ex_date": "2026-09-01",
                               "ratio": None, "amount_per_share": 1.0})
    assert not r["applied"] and "不适用" in r["note"], r


# ================================================================ 盯市
def t_nav_none_when_unpriced():
    reset()
    hold("AAA", 10, 100.0)
    hold("BBB", 10, 100.0)
    m = valuation.mark(PID, on="2026-09-01", prices={"AAA": 110.0}, use_bench=False)
    assert m["nav"] is None and m["unpriced"] == ["BBB"], m
    with connect() as con:
        row = con.execute("SELECT nav, complete, n_unpriced FROM book_nav "
                          "WHERE portfolio_id=? AND date='2026-09-01'",
                          (PID,)).fetchone()
    assert row[0] is None and row[1] == 0 and row[2] == 1, tuple(row)


def t_day_pnl_none_when_prev_incomplete():
    """前一日不完整 → 当日盈亏**不计算**，不能把多日变动挂在一天上。"""
    reset()
    hold("CCC", 10, 100.0)
    hold("DDD", 10, 100.0)
    valuation.mark(PID, on="2026-09-01", prices={"CCC": 100.0}, use_bench=False)
    m = valuation.mark(PID, on="2026-09-02",
                       prices={"CCC": 110.0, "DDD": 110.0}, use_bench=False)
    assert m["nav"] is not None, m
    assert m["day_pnl"] is None, f"前一日缺价，当日盈亏应为 None，实际 {m['day_pnl']}"
    assert "不计算" in (m["note"] or "")


def t_day_pnl_normal():
    reset()
    hold("EEE", 10, 100.0)
    a = valuation.mark(PID, on="2026-09-01", prices={"EEE": 100.0}, use_bench=False)
    b = valuation.mark(PID, on="2026-09-02", prices={"EEE": 110.0}, use_bench=False)
    assert abs(b["day_pnl"] - 100.0) < 1e-6, b["day_pnl"]
    assert abs(b["nav"] - (a["nav"] + 100.0)) < 1e-6


def t_backfill_recomputes_day_pnl():
    """**补一个更早的日子之后，后面几行的"前一日"变了，day_pnl 必须重算。**

    真机上见过：先写的那行显示"当日 +69"，其实算的是相对初始资金——
    数字完全正常，只是答非所问。
    """
    reset(opened="2026-08-25")
    hold("XXX", 10, 100.0, opened="2026-08-25")
    hold("YYY", 10, 100.0, opened="2026-08-25")
    m = valuation.mark(PID, on="2026-08-31",
                       prices={"XXX": 110.0, "YYY": 110.0}, use_bench=False)
    assert abs(m["day_pnl"] - 2200.0) < 1e-6, m["day_pnl"]   # 还没有更早的行
    valuation.mark(PID, on="2026-08-28",
                   prices={"XXX": 100.0, "YYY": 100.0}, use_bench=False)
    valuation.mark(PID, on="2026-08-30", prices={"XXX": 105.0}, use_bench=False)
    rows = {r["date"]: r for r in valuation.series(PID)}
    assert rows["2026-08-31"]["day_pnl"] is None, \
        f"前一日(08-30)缺价，08-31 的当日盈亏应重算成 None，实际 {rows['2026-08-31']['day_pnl']}"
    assert abs(rows["2026-08-28"]["day_pnl"] - 2000.0) < 1e-6


def t_refuse_mark_before_open():
    """盯市日早于开账日 → 拒绝写入。账本还不存在的日子不该有净值。"""
    reset(opened="2026-08-20")
    hold("ZZZ", 10, 100.0, opened="2026-08-20")
    m = valuation.mark(PID, on="2026-08-10", prices={"ZZZ": 100.0}, use_bench=False)
    assert not m.get("ok") and "早于开账日" in m["note"], m
    assert not valuation.series(PID)


def t_mark_is_idempotent():
    reset()
    hold("FFF", 10, 100.0)
    valuation.mark(PID, on="2026-09-01", prices={"FFF": 100.0}, use_bench=False)
    valuation.mark(PID, on="2026-09-01", prices={"FFF": 100.0}, use_bench=False)
    with connect() as con:
        n = con.execute("SELECT COUNT(*) FROM book_mark WHERE portfolio_id=? "
                        "AND date='2026-09-01'", (PID,)).fetchone()[0]
        k = con.execute("SELECT COUNT(*) FROM book_nav WHERE portfolio_id=? "
                        "AND date='2026-09-01'", (PID,)).fetchone()[0]
    assert n == 1 and k == 1, (n, k)


def t_invested_pct_reported():
    reset()
    hold("GGG", 10, 100.0)
    m = valuation.mark(PID, on="2026-09-01", prices={"GGG": 100.0}, use_bench=False)
    assert m["invested_pct"] is not None and 0 < m["invested_pct"] < 0.02, m


def t_no_excess_without_benchmark():
    """基准取不到 → 超额**不计算**，不用价格收益顶替含息总回报。"""
    reset()
    hold("HHH", 10, 100.0)
    m = valuation.mark(PID, on="2026-09-01", prices={"HHH": 100.0}, use_bench=False)
    st = valuation.statement(PID, mark_result=m)
    assert st["bench_cum_return"] is None and st["excess"] is None, st
    txt = __import__("cio.render_book", fromlist=["x"]).render_text(st)
    assert "不计算" in txt


def t_bench_basis_is_total_return():
    assert "TOTAL_RETURN" in valuation.BENCH_BASIS, valuation.BENCH_BASIS


# ================================================================ 对账
def t_recon_all_pass():
    reset()
    trade("III", "BUY", 10, 100.0)
    hold("III", 10, 100.0)
    valuation.mark(PID, on="2026-09-01", prices={"III": 100.0}, use_bench=False)
    r = recon.check(PID, "2026-09-01")
    assert r["status"] == recon.PASS, r


def t_recon_catches_cash_drift():
    """有人动了现金却没有对应交易 → 恒等式 2 必须报失败。"""
    reset()
    trade("JJJ", "BUY", 10, 100.0)
    hold("JJJ", 10, 100.0)
    with connect() as con:
        con.execute("UPDATE book_portfolio SET cash=cash-500 WHERE portfolio_id=?",
                    (PID,))
    valuation.mark(PID, on="2026-09-01", prices={"JJJ": 100.0}, use_bench=False)
    r = recon.check(PID, "2026-09-01")
    assert r["status"] == recon.FAIL
    c = [x for x in r["checks"] if x["id"] == "cash_identity"][0]
    assert c["status"] == recon.FAIL, c


def t_recon_catches_orphan_position():
    """持仓的股数解释不了 → 恒等式 3 必须报失败。"""
    reset()
    hold("KKK", 10, 100.0)                       # 没有对应 trade
    valuation.mark(PID, on="2026-09-01", prices={"KKK": 100.0}, use_bench=False)
    r = recon.check(PID, "2026-09-01")
    c = [x for x in r["checks"] if x["id"] == "position_identity"][0]
    assert c["status"] == recon.FAIL and "KKK" in c["detail"], c


def t_recon_counts_split_in_position_identity():
    """拆股改了股数，恒等式 3 必须把它算进去，否则每次拆股都误报。"""
    reset()
    trade("LLL", "BUY", 100, 800.0)
    hold("LLL", 100, 800.0)
    corp_actions.apply_one(PID, book.holdings_map(PID)["LLL"],
                           {"kind": corp_actions.SPLIT, "ex_date": "2026-09-01",
                            "ratio": 4.0, "amount_per_share": None}, ex_close=200.0)
    valuation.mark(PID, on="2026-09-01", prices={"LLL": 200.0}, use_bench=False)
    r = recon.check(PID, "2026-09-01")
    c = [x for x in r["checks"] if x["id"] == "position_identity"][0]
    assert c["status"] == recon.PASS, c


def t_recon_counts_dividend_in_cash_identity():
    reset()
    trade("MMM", "BUY", 100, 50.0)
    hold("MMM", 100, 50.0)
    corp_actions.apply_one(PID, book.holdings_map(PID)["MMM"],
                           {"kind": corp_actions.DIVIDEND, "ex_date": "2026-09-01",
                            "ratio": None, "amount_per_share": 0.4})
    valuation.mark(PID, on="2026-09-01", prices={"MMM": 50.0}, use_bench=False)
    r = recon.check(PID, "2026-09-01")
    c = [x for x in r["checks"] if x["id"] == "cash_identity"][0]
    assert c["status"] == recon.PASS, c


def t_recon_skips_not_passes_when_unpriced():
    """缺价当天恒等式 1 是**不适用**，不能算通过。"""
    reset()
    trade("NNN", "BUY", 10, 100.0)
    hold("NNN", 10, 100.0)
    valuation.mark(PID, on="2026-09-01", prices={}, use_bench=False)
    r = recon.check(PID, "2026-09-01")
    c = [x for x in r["checks"] if x["id"] == "nav_identity"][0]
    assert c["status"] == recon.SKIPPED and "不是通过" in c["detail"], c
    assert r["status"] != recon.PASS


# ================================================================ 报表
def t_statement_shows_review_gap_and_invalidations():
    reset()
    trade("OOO", "BUY", 10, 100.0)
    hold("OOO", 10, 100.0, opened="2026-08-01")
    m = valuation.mark(PID, on="2026-09-01", prices={"OOO": 120.0}, use_bench=False)
    st = valuation.statement(PID, mark_result=m)
    r = st["positions"][0]
    assert r["days_held"] == 31 and r["days_since_review"] == 31, r
    assert abs(r["unrealized_pnl"] - 200.0) < 1e-6, r
    from cio import render_book
    txt = render_book.render_text(st)
    assert "未复审" in txt and "失效条件" in txt, txt[:400]
    html = render_book.render_html(st)
    assert "OOO" in html and "失效条件" in html


def t_three_renderers_agree():
    """文本 / Markdown / HTML 三处都要有关键内容 —— 这个仓库栽过一次。"""
    reset()
    trade("PPP", "BUY", 10, 100.0)
    hold("PPP", 10, 100.0)
    m = valuation.mark(PID, on="2026-09-01", prices={"PPP": 110.0}, use_bench=False)
    st = valuation.statement(PID, mark_result=m)
    from cio import render_book
    t = render_book.render_text(st)
    d = render_book.render_md(st)
    h = render_book.render_html(st)
    for name, s in (("text", t), ("md", d), ("html", h)):
        assert "PPP" in s, name
        assert ("距复审" in s or "复审" in s), f"{name} 缺少复审信息"


TESTS = [
    ("**4:1 拆股不产生 −75%**", t_split_does_not_show_minus_75),
    ("拆股零头折现金", t_split_fraction_to_cash),
    ("取不到除权日价 → 拒绝应用拆股", t_split_without_price_refuses),
    ("分红入现金", t_dividend_into_cash),
    ("公司行为幂等", t_action_idempotent),
    ("建仓前的行为不适用", t_action_before_purchase_not_applied),
    ("有持仓缺价 → NAV 记为不可计算", t_nav_none_when_unpriced),
    ("**前一日不完整 → 当日盈亏不计算**", t_day_pnl_none_when_prev_incomplete),
    ("正常两日的当日盈亏", t_day_pnl_normal),
    ("**回填更早的日子后重算当日盈亏**", t_backfill_recomputes_day_pnl),
    ("盯市日早于开账日 → 拒绝", t_refuse_mark_before_open),
    ("盯市幂等", t_mark_is_idempotent),
    ("报出平均仓位", t_invested_pct_reported),
    ("**基准取不到 → 超额不计算**", t_no_excess_without_benchmark),
    ("基准口径是含息总回报", t_bench_basis_is_total_return),
    ("对账三条全过", t_recon_all_pass),
    ("对账抓出现金漂移", t_recon_catches_cash_drift),
    ("对账抓出解释不了的持仓", t_recon_catches_orphan_position),
    ("拆股要计入持仓恒等式（否则误报）", t_recon_counts_split_in_position_identity),
    ("分红要计入现金恒等式（否则误报）", t_recon_counts_dividend_in_cash_identity),
    ("缺价当天是不适用，不是通过", t_recon_skips_not_passes_when_unpriced),
    ("盈亏表带复审天数与失效条件", t_statement_shows_review_gap_and_invalidations),
    ("三个渲染器内容一致", t_three_renderers_agree),
]

print("=" * 72)
print("Build 3 自测：公司行为 · 盯市 · 净值 · 对账")
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
