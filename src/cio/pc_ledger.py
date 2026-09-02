"""Attribution 落库 —— 每一次定仓的完整 lineage。

**这不是"半年后做的分析"，是现在的落库要求。**

半年后要能把收益拆成三块：

    选股        一部方向对不对
    风险倾斜    CRO 约束造成的风格偏移
    仓位配置    PC 的 sizing

**而这个分解只有在决策当时把输入存下来才做得到。** 事后从持仓表反推不出来：
一个 3.2% 的仓位，可能是波动率决定的、可能是行业上限绑定的、
也可能是组合层缩放后的结果——**三种指向完全不同的改进方向**，
而它们在持仓表里长得一模一样。

风险层会从后门产生 alpha：长期一致地"高波动高 Beta 就降仓"，
构建出来的就是一个低波动策略。这不是错，是正当的风险管理，
但**它意味着风险指标对收益有系统性影响**，评估业绩时必须能把它拆出来，
否则会把风格倾斜误认为选股能力。

`binding_position_constraint` 存**数组**不存字符串：
w_sector 与 w_liquidity 同时等于最小值是会发生的，只存一个会丢掉一半信息。
"""
from __future__ import annotations

import json

from .db import connect
from .utils import get_logger

log = get_logger("cio.pc_ledger")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pc_lineage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    created_at TEXT, as_of_date TEXT, portfolio_id TEXT, ticker TEXT,

    -- Unit A
    thesis_id INTEGER, direction TEXT, conviction TEXT,
    evidence_gate TEXT, direction_drift TEXT,

    -- CRO
    regime TEXT,
    base_risk_budget REAL, conviction_multiplier REAL, regime_multiplier REAL,
    adjusted_risk_budget REAL,
    risk_constraints TEXT, binding_risk_constraint TEXT,
    veto INTEGER DEFAULT 0, veto_reason TEXT,

    -- PC
    sigma_60 REAL, sigma_252 REAL, sigma_blend REAL, sigma_floor REAL,
    sigma_effective REAL, sigma_binding_component TEXT,
    w_raw REAL,
    caps_evaluated TEXT, caps_not_evaluated TEXT,
    w_pre_scale REAL, portfolio_scale_factor REAL,
    w_final REAL, binding_position_constraint TEXT,
    reason TEXT
);
"""

# **索引单独放，在补列之后建。** 放进上面那段就会出现这条死路：
# 旧库的 pc_lineage 没有 run_id → CREATE TABLE IF NOT EXISTS 不动它 →
# 紧接着的 CREATE INDEX ON pc_lineage(run_id,...) 报 `no such column: run_id`
# → 整段 executescript 抛异常 → **后面那段专门补 run_id 的迁移永远跑不到**。
# 一段修复代码被它要修的问题挡在门外，而报错信息指向的是索引，不是迁移。
_INDEXES = """
CREATE INDEX IF NOT EXISTS ix_pc_lineage ON pc_lineage(as_of_date, portfolio_id, ticker);
CREATE UNIQUE INDEX IF NOT EXISTS ux_pc_lineage_run
    ON pc_lineage(run_id, portfolio_id, ticker);
"""

_MIGRATIONS = {"run_id": "TEXT"}


def init() -> None:
    """**建表 → 补列 → 建索引**，三步顺序不能换。老库缺 run_id 列时补上。

    唯一键是 **(run_id, portfolio_id, ticker)**：

    · 同一次运行重试 → 同一个 run_id → 命中唯一键 → **取回原来那条，不新增**
    · 重新跑一次     → 新的 run_id   → 正常新增

    这个区分很重要：**一次 PC 运行不是"一个投资决策"，是"在这批输入下算出来
    的结果"。** 输入变了（regime 变了、测量更新了）就该有新的一条，
    那不是重复。要防的只有一种情况——同一次执行因为下游报错被重试，
    结果在台账里留下两条一模一样的决策。

    老库里 run_id 为 NULL 的历史行不受影响：SQLite 的唯一索引把 NULL 视为互不相同。
    """
    from .db import ensure_columns
    with connect() as con:
        con.executescript(_SCHEMA)                       # 1. 表
        added = ensure_columns(con, "pc_lineage", _MIGRATIONS)   # 2. 列
        con.executescript(_INDEXES)                      # 3. 索引
        if added:
            log.info("pc_lineage 已补列 %s 并建唯一键（历史行该列为空，"
                     "SQLite 视 NULL 互不相同，不影响旧数据）", "、".join(added))


def _j(v) -> str:
    return json.dumps(v, ensure_ascii=False) if v is not None else ""


def record(*, as_of_date: str, portfolio_id: str, cro: dict, size: dict,
           scale_factor: float = 1.0, direction_drift: dict = None,
           run_id: str = "") -> int:
    """存一条完整 lineage。**任何一个字段缺了，对应的那个归因问题就永远答不了。**

    传了 run_id 且该 (run_id, portfolio_id, ticker) 已存在 → **返回原来那条的 id，
    不写第二条**。这样"重试"拿回的是同一次决策，而不是产生第二次决策。

    没传 run_id 的调用方（命令行抽查、自检）自动领一个一次性 id，
    行为和以前完全一样——**不能因为省略了参数就悄悄开始去重**，
    那会让两次真实的运行被合并成一条。
    """
    from .runid import new_run_id
    from .utils import stamp_utc
    init()
    rid_run = run_id or new_run_id("adhoc")
    ticker = cro.get("ticker", "")
    with connect() as con:
        row = con.execute(
            "SELECT id FROM pc_lineage WHERE run_id=? AND portfolio_id=? AND ticker=?",
            (rid_run, portfolio_id, ticker)).fetchone()
        if row:
            log.info("lineage 已有 %s/%s/%s，返回原记录 #%s（重试不产生第二次决策）",
                     rid_run, portfolio_id, ticker, row[0])
            return int(row[0])
    w_pre = size.get("w_final")
    w_final = None if w_pre is None else w_pre * float(scale_factor or 1.0)
    with connect() as con:
        cur = con.execute(
            "INSERT INTO pc_lineage(run_id,created_at,as_of_date,portfolio_id,ticker,"
            "thesis_id,direction,conviction,evidence_gate,direction_drift,"
            "regime,base_risk_budget,conviction_multiplier,regime_multiplier,"
            "adjusted_risk_budget,risk_constraints,binding_risk_constraint,veto,veto_reason,"
            "sigma_60,sigma_252,sigma_blend,sigma_floor,sigma_effective,"
            "sigma_binding_component,w_raw,caps_evaluated,caps_not_evaluated,"
            "w_pre_scale,portfolio_scale_factor,w_final,binding_position_constraint,reason) "
            "VALUES(" + ",".join(["?"] * 33) + ")",
            (rid_run, stamp_utc(), as_of_date, portfolio_id, ticker,
             int(cro.get("thesis_id") or 0), cro.get("direction", ""),
             cro.get("conviction", ""), cro.get("evidence_gate", ""),
             _j(direction_drift or {}),
             cro.get("regime", ""), cro.get("base_risk_budget"),
             cro.get("conviction_multiplier"), cro.get("regime_multiplier"),
             cro.get("adjusted_risk_budget"), _j(cro.get("risk_constraints")),
             cro.get("binding_risk_constraint", ""), 1 if cro.get("veto") else 0,
             cro.get("veto_reason", ""),
             size.get("sigma_60"), size.get("sigma_252"), size.get("sigma_blend"),
             size.get("sigma_floor"), size.get("sigma_effective"),
             _j(size.get("sigma_binding_component")), size.get("w_raw"),
             _j(size.get("caps_evaluated")), _j(size.get("caps_not_evaluated")),
             w_pre, float(scale_factor or 1.0), w_final,
             _j(size.get("binding_position_constraint")), size.get("reason", "")))
        rid = cur.lastrowid
    return rid


def latest_run_id(portfolio_id: str) -> str:
    """该组合最近一次 PC 运行的 id。没有则返回空串。

    **不返回"最近一条决策"，返回一次运行。** 一次运行是一批同时成立的决策；
    按条取会把两次运行的决策混在一起，得到一个从未真实存在过的组合目标。
    """
    init()
    with connect() as con:
        r = con.execute(
            "SELECT run_id FROM pc_lineage WHERE portfolio_id=? AND run_id IS NOT NULL "
            "AND run_id<>'' ORDER BY id DESC LIMIT 1", (portfolio_id,)).fetchone()
    return str(r[0]) if r else ""


def decisions_for_run(run_id: str, portfolio_id: str = "") -> list:
    """取出一次运行的**全部**决策行（含被否决、无仓位的）。

    调仓提案必须**从已落库的决策派生，不能重跑一次 PC**。
    重跑会得到一份新的决策（测量更新了、regime 变了），
    于是 CEO 批准的东西和台账里记的东西不是同一件——
    两份都正常、都可读、都对不上。
    """
    init()
    q = "SELECT * FROM pc_lineage WHERE run_id=?"
    args = [run_id]
    if portfolio_id:
        q += " AND portfolio_id=?"
        args.append(portfolio_id)
    with connect() as con:
        rows = [dict(r) for r in con.execute(q + " ORDER BY ticker", args)]
    for r in rows:
        r["veto"] = bool(r.get("veto"))
        r["ticker"] = str(r.get("ticker") or "").upper()
    return rows


def binding_stats(portfolio_id: str = "", limit_days: int = 90) -> dict:
    """回答：**过去这些仓位分别是被谁决定的？**

    这是整个 lineage 表存在的理由——不落库就永远问不出来。
    """
    init()
    with connect() as con:
        q = ("SELECT binding_position_constraint, sigma_binding_component, veto, "
             "w_final, reason FROM pc_lineage WHERE 1=1")
        args = []
        if portfolio_id:
            q += " AND portfolio_id=?"
            args.append(portfolio_id)
        q += " ORDER BY id DESC LIMIT 5000"
        rows = list(con.execute(q, args))
    pos, sig, why, vetoed, no_pos = {}, {}, {}, 0, 0
    for bpc, sbc, veto, w_final, reason in rows:
        vetoed += 1 if veto else 0
        if w_final is None:
            no_pos += 1
            why[reason or "（未记原因）"] = why.get(reason or "（未记原因）", 0) + 1
        for k in (json.loads(bpc) if bpc else []):
            pos[k] = pos.get(k, 0) + 1
        for k in (json.loads(sbc) if sbc else []):
            sig[k] = sig.get(k, 0) + 1
    return {"n": len(rows), "vetoed": vetoed, "no_position": no_pos,
            "no_position_reason": dict(sorted(why.items(), key=lambda kv: -kv[1])),
            "position_binding": dict(sorted(pos.items(), key=lambda kv: -kv[1])),
            "sigma_binding": dict(sorted(sig.items(), key=lambda kv: -kv[1]))}
