"""审批状态机 —— **PC 不能直接改持仓。**

    PC 产生目标   →   CEO 产生授权   →   Execution 产生交易   →   Ledger 产生事实

中间那道"授权"如果没有独立存在，就等于研究结论自动变成持仓。
那样一来"这笔是谁决定要买的"永远只有一个答案：程序。

## 状态

    PROPOSED            刚算出来
    PENDING_APPROVAL    等 CEO 批（**只有真正带指令的行才会到这里**）
    NO_TRADE            本行没有指令（持有不动 / 低于门槛 / 缺价）—— 终态
    APPROVED            批了，等下一个开盘
    REJECTED            否了 —— 终态
    EXECUTED            成交并入账 —— 终态
    EXECUTION_FAILED    该成交而没成交（停牌、退市、取不到开盘价）—— 终态
    EXPIRED             过了有效期还没执行 —— 终态

## 为什么 EXPIRED 必须存在

一次批准是**基于某一天的 NAV 和某一天的价格**算出来的股数。
周五批的、周三才跑执行，拿三天前的股数去成交完全合法：
没有异常、指令格式正确、账本欣然接受。**错的只有结果。**

所以批准带有效期（`rebalance.MAX_SESSION_GAP_DAYS`），
过期自动作废、必须重新提案。

## 为什么不带指令的行也要落库

和 `pc_ledger` 同一条纪律：**一次运行里被跳过的东西必须留痕。**
只记要交易的两只，报告上就看不出另外八只发生了什么——
"持有但今天没人复审"和"根本没纳入评估"都会消失成一片空白。

## 幂等

唯一键 `(run_id, portfolio_id, ticker)`。同一次 PC 决策重跑提案，
命中唯一键 → **返回原来那条，不新增**。这也是执行层不会重复成交的根：
一笔交易永远只能挂在一条提案上。
"""
from __future__ import annotations

import json

from .db import connect
from .utils import get_logger, stamp_utc

log = get_logger("cio.proposal_store")

PROPOSED = "PROPOSED"
PENDING_APPROVAL = "PENDING_APPROVAL"
NO_TRADE = "NO_TRADE"
APPROVED = "APPROVED"
REJECTED = "REJECTED"
EXECUTED = "EXECUTED"
EXECUTION_FAILED = "EXECUTION_FAILED"
EXPIRED = "EXPIRED"

TERMINAL = frozenset({NO_TRADE, REJECTED, EXECUTED, EXECUTION_FAILED, EXPIRED})

TRANSITIONS = {
    PROPOSED: frozenset({PENDING_APPROVAL, NO_TRADE, EXPIRED}),
    PENDING_APPROVAL: frozenset({APPROVED, REJECTED, EXPIRED}),
    APPROVED: frozenset({EXECUTED, EXECUTION_FAILED, EXPIRED}),
    NO_TRADE: frozenset(),
    REJECTED: frozenset(),
    EXECUTED: frozenset(),
    EXECUTION_FAILED: frozenset(),
    EXPIRED: frozenset(),
}
"""合法跃迁。**声明式的，不是散在各处的 if。**

散写的后果：某个入口忘了检查，于是一条 REJECTED 的提案被执行了。
那不会报错——它只是一次多出来的交易，账本照收。
"""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS rebalance_proposal (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                TEXT,      -- **PC 那次运行的 id** → 直连 pc_lineage
    portfolio_id          TEXT,
    ticker                TEXT,
    created_at            TEXT,
    decision_date         TEXT,
    expires_on            TEXT,
    basis                 TEXT,      -- TARGET / EXIT_DECIDED / NO_TARGET
    action                TEXT,
    reason                TEXT,
    target_weight         REAL,
    target_shares         INTEGER,
    current_shares        INTEGER,
    delta_shares          INTEGER,
    decision_price        REAL,
    est_value             REAL,
    nav_at_decision       REAL,
    execution_price_basis TEXT,
    compliance_status     TEXT,
    compliance_json       TEXT,
    days_since_evaluated  INTEGER,
    thesis_id             INTEGER,
    state                 TEXT,
    state_changed_at      TEXT,
    approved_by           TEXT,
    approved_at           TEXT,
    executed_at           TEXT,
    execution_date        TEXT,
    execution_price       REAL,
    trade_id              INTEGER
);
CREATE TABLE IF NOT EXISTS proposal_event (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id  INTEGER,
    at           TEXT,
    from_state   TEXT,
    to_state     TEXT,
    actor        TEXT,
    note         TEXT
);
"""

# **索引与建表分开，在补列之后**（见 db.ensure_columns 的说明）。
_INDEXES = """
CREATE UNIQUE INDEX IF NOT EXISTS ux_rebalance_proposal
    ON rebalance_proposal(run_id, portfolio_id, ticker);
CREATE INDEX IF NOT EXISTS ix_rebalance_proposal_state
    ON rebalance_proposal(portfolio_id, state);
CREATE INDEX IF NOT EXISTS ix_proposal_event ON proposal_event(proposal_id, at);
"""

_MIGRATIONS = {"rebalance_proposal": {
    "executed_at": "TEXT", "execution_date": "TEXT",
    "execution_price": "REAL", "trade_id": "INTEGER"}}


def init() -> None:
    """**建表 → 补列 → 建索引**。"""
    from .db import ensure_columns
    with connect() as con:
        con.executescript(_SCHEMA)
        for table, cols in _MIGRATIONS.items():
            added = ensure_columns(con, table, cols)
            if added:
                log.info("%s 已补列：%s", table, "、".join(added))
        con.executescript(_INDEXES)


def _j(v) -> str:
    return json.dumps(v, ensure_ascii=False, default=str) if v is not None else ""


def record(*, run_id: str, portfolio_id: str, row: dict, nav, decision_date: str,
           expires: str, compliance: dict, actor: str = "system") -> dict:
    """落一条提案。**幂等：同一 (run_id, portfolio_id, ticker) 返回原记录。**

    初始状态由**有没有指令**决定，不由人挑：
    带 BUY/SELL/EXIT 的进 PENDING_APPROVAL，其余直接终态 NO_TRADE。
    """
    from .rebalance import EXECUTION_PRICE_BASIS, TRADING_ACTIONS
    init()
    with connect() as con:
        old = con.execute(
            "SELECT * FROM rebalance_proposal WHERE run_id=? AND portfolio_id=? AND ticker=?",
            (run_id, portfolio_id, row["ticker"])).fetchone()
        if old:
            log.info("提案已存在 %s/%s/%s → 返回原记录 #%s（重跑不产生第二条）",
                     run_id, portfolio_id, row["ticker"], old["id"])
            return dict(old)
    actionable = row.get("action") in TRADING_ACTIONS
    state = PENDING_APPROVAL if actionable else NO_TRADE
    now = stamp_utc()
    with connect() as con:
        cur = con.execute(
            "INSERT INTO rebalance_proposal(run_id,portfolio_id,ticker,created_at,"
            "decision_date,expires_on,basis,action,reason,target_weight,target_shares,"
            "current_shares,delta_shares,decision_price,est_value,nav_at_decision,"
            "execution_price_basis,compliance_status,compliance_json,"
            "days_since_evaluated,thesis_id,state,state_changed_at) "
            "VALUES(" + ",".join(["?"] * 23) + ")",
            (run_id, portfolio_id, row["ticker"], now, decision_date, expires,
             row.get("basis"), row.get("action"), row.get("reason", ""),
             row.get("target_weight"), row.get("target_shares"),
             row.get("current_shares"), row.get("delta_shares"),
             row.get("decision_price"), row.get("est_value"), nav,
             EXECUTION_PRICE_BASIS,
             (compliance or {}).get("status", ""), _j(compliance),
             row.get("days_since_evaluated"), row.get("thesis_id"),
             state, now))
        pid = cur.lastrowid
        con.execute("INSERT INTO proposal_event(proposal_id,at,from_state,to_state,"
                    "actor,note) VALUES(?,?,?,?,?,?)",
                    (pid, now, PROPOSED, state, actor,
                     row.get("reason", "")[:400]))
    return get(pid)


def get(proposal_id: int) -> dict:
    init()
    with connect() as con:
        r = con.execute("SELECT * FROM rebalance_proposal WHERE id=?",
                        (proposal_id,)).fetchone()
    return dict(r) if r else {}


_SETTABLE = frozenset({"executed_at", "execution_date", "execution_price", "trade_id"})


def transition(proposal_id: int, to_state: str, *, actor: str,
               note: str = "", fields: dict = None) -> dict:
    """按 TRANSITIONS 校验后改状态。**非法跃迁抛异常，不静默忽略。**

    静默忽略的表现是：点了「批准」，页面没报错，状态还是 PENDING——
    人会以为自己没点到，再点一次。
    """
    p = get(proposal_id)
    if not p:
        raise ValueError(f"提案 #{proposal_id} 不存在")
    frm = p["state"]
    allowed = TRANSITIONS.get(frm, frozenset())
    if to_state not in allowed:
        raise ValueError(
            f"提案 #{proposal_id}（{p['ticker']}）不能从 {frm} 变到 {to_state}。"
            f"允许的下一步：{'、'.join(sorted(allowed)) or '（终态，无）'}")
    now = stamp_utc()
    with connect() as con:
        if to_state in (APPROVED, REJECTED):
            con.execute("UPDATE rebalance_proposal SET state=?,state_changed_at=?,"
                        "approved_by=?,approved_at=? WHERE id=?",
                        (to_state, now, actor, now, proposal_id))
        else:
            con.execute("UPDATE rebalance_proposal SET state=?,state_changed_at=? "
                        "WHERE id=?", (to_state, now, proposal_id))
        # 成交明细（成交日、成交价、交易行 id）随状态一起写。
        # **白名单，不接受任意列名** —— 否则调用方一个笔误就能改掉
        # target_shares 这类已经被批准过的字段，而那不会报错。
        for k, v in (fields or {}).items():
            if k not in _SETTABLE:
                raise ValueError(f"{k} 不在可写字段白名单里：{'、'.join(sorted(_SETTABLE))}")
            con.execute(f"UPDATE rebalance_proposal SET {k}=? WHERE id=?", (v, proposal_id))
        con.execute("INSERT INTO proposal_event(proposal_id,at,from_state,to_state,"
                    "actor,note) VALUES(?,?,?,?,?,?)",
                    (proposal_id, now, frm, to_state, actor, note))
    log.info("提案 #%s %s：%s → %s（%s）", proposal_id, p["ticker"], frm, to_state, actor)
    return get(proposal_id)


def by_run(run_id: str, portfolio_id: str = "") -> list:
    init()
    q = "SELECT * FROM rebalance_proposal WHERE run_id=?"
    args = [run_id]
    if portfolio_id:
        q += " AND portfolio_id=?"
        args.append(portfolio_id)
    with connect() as con:
        return [dict(r) for r in con.execute(q + " ORDER BY ticker", args)]


def pending(portfolio_id: str) -> list:
    """等批准的提案。"""
    init()
    with connect() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM rebalance_proposal WHERE portfolio_id=? AND state=? "
            "ORDER BY decision_date DESC, ticker", (portfolio_id, PENDING_APPROVAL))]


def approved(portfolio_id: str) -> list:
    """已批准、等待成交的提案。按决策日排序，**先批的先成交**。"""
    init()
    with connect() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM rebalance_proposal WHERE portfolio_id=? AND state=? "
            "ORDER BY decision_date, ticker", (portfolio_id, APPROVED))]


def get_by_ref(ref: str, portfolio_id: str = "") -> list:
    """按 id 或 ticker 找提案。给命令行和 Telegram 用。

    Telegram 上人会打 `/approve NVDA` 而不是 `/approve 47`，
    所以两种都得认。**同一 ticker 有多条待批时全部返回，由调用方拒绝执行**——
    自动挑一条就是替人做了一个他没做的决定。
    """
    init()
    ref = str(ref or "").strip()
    if not ref:
        return []
    q = "SELECT * FROM rebalance_proposal WHERE "
    args = []
    if ref.isdigit():
        q += "id=?"
        args.append(int(ref))
    else:
        q += "ticker=? AND state IN (?,?)"
        args += [ref.upper(), PENDING_APPROVAL, APPROVED]
    if portfolio_id:
        q += " AND portfolio_id=?"
        args.append(portfolio_id)
    with connect() as con:
        return [dict(r) for r in con.execute(q + " ORDER BY id DESC", args)]


def expire_stale(portfolio_id: str, today: str, actor: str = "system") -> list:
    """把过了有效期还没执行的提案标成 EXPIRED。

    **每次提案前先跑一次。** 否则昨天那批未处理的 PENDING 会一直挂着，
    某天被批准并按当日开盘成交——用的却是好几天前的股数。
    """
    init()
    with connect() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM rebalance_proposal WHERE portfolio_id=? "
            "AND state IN (?,?) AND expires_on < ?",
            (portfolio_id, PENDING_APPROVAL, APPROVED, str(today)[:10]))]
    out = []
    for r in rows:
        out.append(transition(r["id"], EXPIRED, actor=actor,
                              note=f"有效期至 {r['expires_on']}，今天 {today} —— "
                                   f"股数基于 {r['decision_date']} 的 NAV 与价格，已不可用"))
    if out:
        log.warning("%s：%d 条提案过期作废（需重新提案）", portfolio_id, len(out))
    return out


def stats(portfolio_id: str = "") -> dict:
    init()
    q = "SELECT state, COUNT(*) FROM rebalance_proposal WHERE 1=1"
    args = []
    if portfolio_id:
        q += " AND portfolio_id=?"
        args.append(portfolio_id)
    with connect() as con:
        rows = list(con.execute(q + " GROUP BY state", args))
    return {r[0]: r[1] for r in rows}


def render_pending(portfolio_id: str) -> str:
    rows = pending(portfolio_id)
    if not rows:
        return f"{portfolio_id}：没有待批准的提案。"
    L = [f"{portfolio_id}：待批准 {len(rows)} 条"]
    for r in rows:
        L.append(f"  #{r['id']:<5} {r['ticker']:<6} {r['action']:<5} "
                 f"{r['current_shares']:>6} → {r['target_shares']:<6} 股"
                 f"　Δ {r['delta_shares']:+d}　@ {r['decision_price']:,.2f}"
                 f"　决策日 {r['decision_date']}　有效至 {r['expires_on']}"
                 f"　合规 {r['compliance_status']}")
    return "\n".join(L)
