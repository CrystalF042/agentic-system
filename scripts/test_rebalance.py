#!/usr/bin/env python3
"""Build 1 自测 —— 目标转交易、事前合规、审批状态机。

**每一条断言对应一个真会静默发生的错误**，不是覆盖率。
最重要的一组是"没有目标 ≠ 目标为 0"：那类错误的表现是
一道格式完全正确、账本欣然接受的清仓指令。

    python scripts/test_rebalance.py

断言的是**行为与结构**，不是源码里有没有某句注释——
这个仓库在那上面栽过五次（探针 grep 到的正是解释该修复的那句注释）。
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

# **先把库指到临时文件，再 import 任何会建表的模块。**
_TMP = Path(tempfile.mkdtemp(prefix="cio-b1-")) / "test.db"
db.DB_PATH = _TMP

from cio import book, compliance, proposal_store, rebalance     # noqa: E402

PID = "TEST_US"
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


def _dec(ticker, *, w=None, veto=False, reason="", gate="SUFFICIENT"):
    return {"ticker": ticker, "w_final": w, "veto": veto, "veto_reason": reason,
            "reason": reason, "evidence_gate": gate, "thesis_id": 1,
            "direction": "看多", "conviction": "中"}


# ================================================================ 目标的三种来源
def t_veto_is_exit():
    r = rebalance.target_from_decision(_dec("NVDA", veto=True, reason="波动超限"))
    assert r["basis"] == rebalance.EXIT_DECIDED, r
    assert r["target_weight"] == 0.0, "否决 = 目标 0，要能被执行成清仓"


def t_weight_is_target():
    r = rebalance.target_from_decision(_dec("AVGO", w=0.018))
    assert r["basis"] == rebalance.TARGET and abs(r["target_weight"] - 0.018) < 1e-12, r


def t_no_weight_is_no_target():
    """**本 build 最重要的一条。**

    w_final 为空且未否决 = 本轮没对"该不该持有"做判断。
    target_weight 必须是 None，**不能是 0.0**——0.0 会一路走到"卖出全部"。
    """
    r = rebalance.target_from_decision(
        _dec("AMAT", w=None, gate="INSUFFICIENT", reason="材料不足"))
    assert r["basis"] == rebalance.NO_TARGET, r
    assert r["target_weight"] is None, "无目标必须是 None，不是 0 —— 0 会被执行成清仓"


def t_zero_weight_is_a_real_target():
    r = rebalance.target_from_decision(_dec("QCOM", w=0.0))
    assert r["basis"] == rebalance.TARGET and r["target_weight"] == 0.0


# ================================================================ 权重 → 股数
def t_shares_floor():
    assert rebalance.target_shares(100000, 0.018, 180.0) == 10        # 1800/180
    assert rebalance.target_shares(100000, 0.018, 190.0) == 9         # 向零取整


def t_shares_none_when_unpriced():
    assert rebalance.target_shares(100000, 0.018, None) is None
    assert rebalance.target_shares(None, 0.018, 180.0) is None
    assert rebalance.target_shares(100000, 0.018, 0) is None


def t_lot_size_one_for_us():
    assert book.LOT_SIZE == 1, "美股按 1 股，不是 A 股的 100 股整手"
    assert rebalance.target_shares(100000, 0.018, 180.0, lot=book.LOT_SIZE) == 10


# ================================================================ plan()
def _plan(decisions, holdings, prices, nav=100000.0, cash=100000.0):
    return rebalance.plan(nav=nav, cash=cash, holdings=holdings,
                          decisions=decisions, prices=prices,
                          decision_date="2026-08-31")


def t_held_but_not_evaluated_is_hold():
    """持有 + 本轮没判断 → **持有不动**，绝不是卖出。"""
    p = _plan([], {"NVDA": {"shares": 10, "last_evaluated_on": "2026-08-20"}},
              {"NVDA": 180.0})
    r = p["rows"][0]
    assert r["action"] == rebalance.HOLD_NOT_EVALUATED, r["action"]
    assert r["delta_shares"] == 0, "本轮没复审 ≠ 清仓"
    assert r["days_since_evaluated"] == 11


def t_stale_review_flagged():
    p = _plan([], {"NVDA": {"shares": 10, "last_evaluated_on": "2026-08-01"}},
              {"NVDA": 180.0})
    assert p["rows"][0]["stale_review"] is True
    assert p["summary"]["n_stale_review"] == 1


def t_veto_liquidates():
    p = _plan([_dec("NVDA", veto=True, reason="波动 62% 超上限")],
              {"NVDA": {"shares": 10, "last_evaluated_on": "2026-08-30"}},
              {"NVDA": 180.0})
    r = p["rows"][0]
    assert r["action"] == rebalance.EXIT and r["delta_shares"] == -10, r


def t_delta_only():
    p = _plan([_dec("NVDA", w=0.018)],
              {"NVDA": {"shares": 6, "last_evaluated_on": "2026-08-30"}},
              {"NVDA": 180.0})
    r = p["rows"][0]
    assert r["target_shares"] == 10 and r["delta_shares"] == 4, r
    assert r["action"] == rebalance.BUY


def t_below_band_no_trade():
    """NAV 10 万 → 门槛 max(250, 200) = 250。1 股 ×180 = 180 < 250 → 不动。"""
    p = _plan([_dec("NVDA", w=0.018)],
              {"NVDA": {"shares": 9, "last_evaluated_on": "2026-08-30"}},
              {"NVDA": 180.0})
    r = p["rows"][0]
    assert r["action"] == rebalance.BELOW_BAND and r["delta_shares"] == 0, r


def t_full_exit_ignores_band():
    """清仓例外：只剩 1 股（180 < 门槛 250）也要卖掉。"""
    p = _plan([_dec("NVDA", w=0.0)],
              {"NVDA": {"shares": 1, "last_evaluated_on": "2026-08-30"}},
              {"NVDA": 180.0})
    r = p["rows"][0]
    assert r["action"] == rebalance.EXIT and r["delta_shares"] == -1, r


def t_unpriced_is_not_zero():
    """取不到价 → NOT_PRICED，**不是**目标 0。否则一次取数失败变成清仓指令。"""
    p = _plan([_dec("NVDA", w=0.018)],
              {"NVDA": {"shares": 10, "last_evaluated_on": "2026-08-30"}}, {})
    r = p["rows"][0]
    assert r["action"] == rebalance.NOT_PRICED, r["action"]
    assert r["target_shares"] is None and r["delta_shares"] == 0


def t_sub_lot_does_not_liquidate():
    """目标金额不足一股，且已有持仓 → 维持，不清仓。"""
    p = _plan([_dec("BRK-A", w=0.001)],
              {"BRK-A": {"shares": 1, "last_evaluated_on": "2026-08-30"}},
              {"BRK-A": 700000.0})
    r = p["rows"][0]
    assert r["delta_shares"] == 0, "取整到 0 股不等于判定清仓"
    assert r["action"] == rebalance.HOLD_AT_TARGET, r["action"]


def t_plan_covers_union():
    """清单覆盖决策 ∪ 持仓，不是只有要交易的。"""
    p = _plan([_dec("AVGO", w=0.02)],
              {"NVDA": {"shares": 5, "last_evaluated_on": "2026-08-30"}},
              {"AVGO": 300.0, "NVDA": 180.0})
    assert {r["ticker"] for r in p["rows"]} == {"AVGO", "NVDA"}


def t_cash_required_excludes_same_session_sales():
    """美股 T+1 交收：同场卖出的回款当天没到账，不能拿去抵买入。"""
    p = _plan([_dec("AVGO", w=0.30), _dec("NVDA", w=0.0)],
              {"NVDA": {"shares": 100, "last_evaluated_on": "2026-08-30"}},
              {"AVGO": 300.0, "NVDA": 180.0}, cash=1000.0)
    s = p["summary"]
    assert s["sell_value"] > 0, "确实有卖出"
    assert abs(s["cash_required"] - s["buy_value"]) < 1e-9, \
        "现金需求必须只算买入，不减同场卖出回款"
    assert s["cash_shortfall"] > 0


def t_expiry_window():
    assert rebalance.expires_on("2026-08-31") == "2026-09-04"


# ================================================================ 事前合规
def t_partial_never_reports_pass():
    """六项里四项未评估 → PARTIAL。**绝不能是 PASS。**"""
    p = _plan([_dec("AVGO", w=0.02)], {}, {"AVGO": 300.0})
    res = compliance.check_proforma(nav=100000.0, cash_available=100000.0,
                                    cash_required=p["summary"]["cash_required"],
                                    rows=p["rows"])
    assert res["status"] == compliance.PARTIAL, res["status"]
    assert res["n_not_evaluated"] == 4, res["n_not_evaluated"]
    assert res["status"] != compliance.PASS


def t_cash_breach():
    p = _plan([_dec("AVGO", w=0.50)], {}, {"AVGO": 300.0}, cash=100.0)
    res = compliance.check_proforma(nav=100000.0, cash_available=100.0,
                                    cash_required=p["summary"]["cash_required"],
                                    rows=p["rows"])
    assert res["status"] == compliance.BREACH, res["status"]
    c = [x for x in res["checks"] if x["id"] == "cash_sufficient"][0]
    assert c["status"] == compliance.BREACH


def t_unpriced_holding_blocks_leverage_check():
    """成交后有持仓取不到价 → 总仓位不可计算 → 该项 NOT_EVALUATED，不是通过。"""
    p = _plan([], {"NVDA": {"shares": 10, "last_evaluated_on": "2026-08-30"}}, {})
    res = compliance.check_proforma(nav=100000.0, cash_available=100000.0,
                                    cash_required=0.0, rows=p["rows"])
    c = [x for x in res["checks"] if x["id"] == "no_leverage"][0]
    assert c["status"] == compliance.NOT_EVALUATED, c


def t_all_six_checks_registered():
    ids = {c[0] for c in compliance.CHECKS}
    assert ids == {"cash_sufficient", "no_leverage", "sector_cap", "theme_cap",
                   "portfolio_vol", "liquidity"}, ids


def t_proforma_is_post_trade():
    """合规看的是**成交后**的组合：卖光了就不该再算它的仓位。"""
    p = _plan([_dec("NVDA", w=0.0)],
              {"NVDA": {"shares": 10, "last_evaluated_on": "2026-08-30"}},
              {"NVDA": 180.0})
    pf = compliance.proforma(p["rows"], 100000.0)
    assert "NVDA" not in pf["shares"], pf


# ================================================================ 账本
def t_book_rejects_a_share_code():
    try:
        book.assert_us_ticker("002371")
    except ValueError:
        return
    raise AssertionError("6 位 A 股代码必须被拒，否则它在美股账本里永远取不到价")


def t_book_nav_none_when_unpriced():
    book.open_book(PID, capital=100000.0, opened_on="2026-08-31")
    with db.connect() as con:
        con.execute("INSERT OR IGNORE INTO book_position(portfolio_id,ticker,shares,"
                    "avg_cost,opened_on,opened_run_id,last_evaluated_on,open) "
                    "VALUES(?,?,?,?,?,?,?,1)",
                    (PID, "NVDA", 10, 170.0, "2026-08-20", "pc-x", "2026-08-20"))
    n = book.nav(PID, {})
    assert n["nav"] is None, "有持仓取不到价 → NAV 不可计算，不能按剩下的算"
    assert n["unpriced"] == ["NVDA"]
    n2 = book.nav(PID, {"NVDA": 180.0})
    assert abs(n2["nav"] - (100000.0 + 1800.0)) < 1e-6, n2


def t_open_book_is_idempotent():
    a = book.open_book(PID, capital=999.0)
    assert abs(a["initial_capital"] - 100000.0) < 1e-9, "重开账不能覆盖初始资金"


def t_benchmark_basis_is_total_return():
    assert book.BENCHMARK_BASIS == "TOTAL_RETURN", \
        "价格收益基准会凭空造出每年约 1.5% 的假 alpha"


def t_mark_evaluated():
    n = book.mark_evaluated(PID, ["NVDA"], "2026-08-31", "pc-y")
    assert n == 1
    assert book.holdings_map(PID)["NVDA"]["last_evaluated_on"] == "2026-08-31"


# ================================================================ 状态机
def t_non_actionable_goes_to_no_trade():
    p = _plan([], {"NVDA": {"shares": 10, "last_evaluated_on": "2026-08-30"}},
              {"NVDA": 180.0})
    row = p["rows"][0]
    pr = proposal_store.record(run_id="pc-t1", portfolio_id=PID, row=row,
                               nav=100000.0, decision_date="2026-08-31",
                               expires="2026-09-04", compliance={"status": "PARTIAL"})
    assert pr["state"] == proposal_store.NO_TRADE, pr["state"]


def t_actionable_goes_to_pending():
    p = _plan([_dec("AVGO", w=0.02)], {}, {"AVGO": 300.0})
    pr = proposal_store.record(run_id="pc-t2", portfolio_id=PID, row=p["rows"][0],
                               nav=100000.0, decision_date="2026-08-31",
                               expires="2026-09-04", compliance={"status": "PARTIAL"})
    assert pr["state"] == proposal_store.PENDING_APPROVAL, pr["state"]
    assert pr["execution_price_basis"] == "T+1_OPEN"


def t_record_is_idempotent():
    p = _plan([_dec("AVGO", w=0.02)], {}, {"AVGO": 300.0})
    a = proposal_store.record(run_id="pc-t3", portfolio_id=PID, row=p["rows"][0],
                              nav=100000.0, decision_date="2026-08-31",
                              expires="2026-09-04", compliance={"status": "PARTIAL"})
    b = proposal_store.record(run_id="pc-t3", portfolio_id=PID, row=p["rows"][0],
                              nav=100000.0, decision_date="2026-08-31",
                              expires="2026-09-04", compliance={"status": "PARTIAL"})
    assert a["id"] == b["id"], "同一 (run_id, portfolio, ticker) 不能产生第二条提案"


def t_illegal_transition_raises():
    p = _plan([_dec("QCOM", w=0.02)], {}, {"QCOM": 150.0})
    pr = proposal_store.record(run_id="pc-t4", portfolio_id=PID, row=p["rows"][0],
                               nav=100000.0, decision_date="2026-08-31",
                               expires="2026-09-04", compliance={"status": "PARTIAL"})
    proposal_store.transition(pr["id"], proposal_store.REJECTED, actor="ceo")
    try:
        proposal_store.transition(pr["id"], proposal_store.EXECUTED, actor="exec")
    except ValueError:
        return
    raise AssertionError("被否的提案不能被执行 —— 那不会报错，只会多一笔交易")


def t_no_trade_is_terminal():
    assert proposal_store.TRANSITIONS[proposal_store.NO_TRADE] == frozenset()
    assert proposal_store.EXECUTED in proposal_store.TERMINAL


def t_expire_stale():
    p = _plan([_dec("AMD", w=0.02)], {}, {"AMD": 160.0})
    pr = proposal_store.record(run_id="pc-t5", portfolio_id=PID, row=p["rows"][0],
                               nav=100000.0, decision_date="2026-08-20",
                               expires="2026-08-24", compliance={"status": "PARTIAL"})
    assert pr["state"] == proposal_store.PENDING_APPROVAL
    out = proposal_store.expire_stale(PID, "2026-08-31")
    assert any(o["id"] == pr["id"] for o in out), "过期的批准必须作废"
    assert proposal_store.get(pr["id"])["state"] == proposal_store.EXPIRED


def t_approved_can_expire():
    assert proposal_store.EXPIRED in proposal_store.TRANSITIONS[proposal_store.APPROVED], \
        "批了但没在有效期内执行，也必须能作废"


TESTS = [
    ("否决 → 目标 0（清仓）", t_veto_is_exit),
    ("有权重 → 目标 = 权重", t_weight_is_target),
    ("**无权重且未否决 → 无目标（None，不是 0）**", t_no_weight_is_no_target),
    ("权重 0.0 是一个真实目标", t_zero_weight_is_a_real_target),
    ("权重→股数向零取整", t_shares_floor),
    ("缺价/缺 NAV → None，不是 0", t_shares_none_when_unpriced),
    ("美股按 1 股，不是 100 股整手", t_lot_size_one_for_us),
    ("持有但本轮未复审 → 不动，不卖", t_held_but_not_evaluated_is_hold),
    ("久未复审要标出来", t_stale_review_flagged),
    ("CRO 否决 → 清仓", t_veto_liquidates),
    ("只交易差额", t_delta_only),
    ("低于门槛不交易", t_below_band_no_trade),
    ("清仓不受门槛约束", t_full_exit_ignores_band),
    ("取不到价 ≠ 目标 0", t_unpriced_is_not_zero),
    ("目标不足一股不等于清仓", t_sub_lot_does_not_liquidate),
    ("清单覆盖决策∪持仓", t_plan_covers_union),
    ("现金需求不抵同场卖出回款", t_cash_required_excludes_same_session_sales),
    ("批准有效期 4 天", t_expiry_window),
    ("**有未评估项时绝不报 PASS**", t_partial_never_reports_pass),
    ("现金不足 → BREACH", t_cash_breach),
    ("缺价持仓 → 总仓位不可评估", t_unpriced_holding_blocks_leverage_check),
    ("六项检查全部登记在册", t_all_six_checks_registered),
    ("合规看的是成交后的组合", t_proforma_is_post_trade),
    ("A 股代码不得进美股账本", t_book_rejects_a_share_code),
    ("有持仓缺价 → NAV 不可计算", t_book_nav_none_when_unpriced),
    ("重复开账不覆盖初始资金", t_open_book_is_idempotent),
    ("基准必须是含息总回报", t_benchmark_basis_is_total_return),
    ("复审日期可回写", t_mark_evaluated),
    ("无指令的行 → NO_TRADE 终态", t_non_actionable_goes_to_no_trade),
    ("有指令的行 → 待批准", t_actionable_goes_to_pending),
    ("同一决策不产生第二条提案", t_record_is_idempotent),
    ("被否的提案不能被执行", t_illegal_transition_raises),
    ("NO_TRADE / EXECUTED 是终态", t_no_trade_is_terminal),
    ("过期批准自动作废", t_expire_stale),
    ("批了未执行也能过期", t_approved_can_expire),
]

print("=" * 72)
print("Build 1 自测：目标 → 交易 → 审批（只提案，不成交）")
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
print("临时库：", _TMP)
raise SystemExit(0)
