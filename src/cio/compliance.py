"""事前合规 —— **检查的是成交后的假想组合，不是现在的组合**。

真实机构里这一步叫 pre-trade compliance，它问的是一句话：

    **如果这批单子全部成交，组合会不会破限？**

所以它必须发生在**批准之前**，不是批准之后。批完再查，只剩两个选择：
要么撤单（那批准是什么意思），要么带着破限过下去。

## 为什么现在就要有这个模块，哪怕四项检查还没实现

行业上限、主题集中度、组合波动、流动性要等 Build 4 才有真实输入。
但**状态机的形状现在必须定下来**：合规检查是提案 → 待批之间的一步。
等 Build 4 再插进去，就要重开一条已经在用的审批链路——
届时已经批过、执行过的历史提案，是"合规通过"还是"未检查"？没法回答。

所以现在就放进去，六项检查全部登记在册，能算的算，不能算的
**明明白白写 NOT_EVALUATED**。

## 一条不能松的规则：有未评估项时，整体结论不能是 PASS

    全部通过且全部评估过   →  PASS
    任何一项破限          →  BREACH
    有项目没评估          →  **PARTIAL**（绝不是 PASS）

差别不是措辞。`PASS` 会被读成"风控查过了，没问题"，
而真相是六项里只查了两项。**一个只做了三分之一的检查报告"通过"，
比它直接不存在更危险**——不存在时人还会自己去看，
报告通过时人就不看了。
"""
from __future__ import annotations

from .utils import get_logger

log = get_logger("cio.compliance")

PASS = "PASS"
BREACH = "BREACH"
NOT_EVALUATED = "NOT_EVALUATED"
PARTIAL = "PARTIAL"

# 每一项：id、人话、限额、实现批次。
# **限额写在这里只是为了报告可读**，真值在 risk_officer.POLICY，
# Build 4 接线时从那里读，不要在这里复制一份阈值（两份一定会漂移）。
CHECKS = [
    ("cash_sufficient", "买入所需现金不超过可用现金", "", "Build 1"),
    ("no_leverage", "成交后总仓位不超过 100%（不加杠杆）", "≤100%", "Build 1"),
    ("sector_cap", "单一行业合计不超过上限", "≤20%", "Build 4"),
    ("theme_cap", "单一主题合计不超过上限", "≤25%", "Build 4"),
    ("portfolio_vol", "组合年化波动不超过上限（需相关矩阵）", "≤12%", "Build 4"),
    ("liquidity", "单票不超过 20 日均额的一定比例", "", "Build 4"),
]

_DEFERRED_NOTE = {
    "sector_cap": "行业标签与真实行业暴露尚未接入 —— 未评估，不是通过",
    "theme_cap": "主题标签与真实主题暴露尚未接入 —— 未评估，不是通过",
    "portfolio_vol": "组合波动需要相关矩阵，尚未实现 —— 未评估，不是通过",
    "liquidity": "20 日均额尚未接入 CRO —— 未评估，不是通过",
}


def proforma(rows: list, nav) -> dict:
    """成交后的假想持仓与权重。

    **取不到价的持仓会让总仓位变成不可计算**，而不是"按剩下的算"。
    按剩下的算会得到一个偏小的总仓位，于是"不加杠杆"这项检查
    在一个真的破限的组合上给出通过——一次完全正常的误判。
    """
    from .rebalance import TRADING_ACTIONS
    weights, shares, unpriced = {}, {}, []
    total = 0.0
    for r in rows:
        post = (r.get("target_shares") if r.get("action") in TRADING_ACTIONS
                else r.get("current_shares")) or 0
        if post <= 0:
            continue
        shares[r["ticker"]] = post
        p = r.get("decision_price")
        if p is None or float(p) <= 0:
            unpriced.append(r["ticker"])
            continue
        v = post * float(p)
        total += v
        weights[r["ticker"]] = (None if not nav else v / float(nav))
    gross = None if (unpriced or not nav) else total / float(nav)
    return {"shares": shares, "weights": weights, "unpriced": unpriced,
            "gross_exposure": gross, "holdings_value": (None if unpriced else total)}


def check_proforma(*, nav, cash_available, cash_required, rows: list) -> dict:
    """跑六项检查。**只有全部评估过且全部通过，才可能返回 PASS。**"""
    pf = proforma(rows, nav)
    out = []

    # ---- 现在就能算的两项 ----
    if cash_available is None or cash_required is None:
        out.append({"id": "cash_sufficient", "status": NOT_EVALUATED,
                    "detail": "账本未开或 NAV 不可计算，现金充足性无法判断"})
    elif cash_required > cash_available + 1e-9:
        out.append({"id": "cash_sufficient", "status": BREACH,
                    "detail": f"买入需 {cash_required:,.2f}，可用现金 "
                              f"{cash_available:,.2f}，缺口 "
                              f"{cash_required - cash_available:,.2f}"})
    else:
        out.append({"id": "cash_sufficient", "status": PASS,
                    "detail": f"买入需 {cash_required:,.2f} ≤ 可用 "
                              f"{cash_available:,.2f}"})

    if pf["gross_exposure"] is None:
        out.append({"id": "no_leverage", "status": NOT_EVALUATED,
                    "detail": ("成交后有持仓取不到价（"
                               + "、".join(pf["unpriced"]) + "），总仓位不可计算"
                               if pf["unpriced"] else "NAV 不可计算，总仓位无法判断")})
    elif pf["gross_exposure"] > 1.0 + 1e-9:
        out.append({"id": "no_leverage", "status": BREACH,
                    "detail": f"成交后总仓位 {pf['gross_exposure']:.2%} > 100%"})
    else:
        out.append({"id": "no_leverage", "status": PASS,
                    "detail": f"成交后总仓位 {pf['gross_exposure']:.2%}"
                              f"，现金残差 {1 - pf['gross_exposure']:.2%}"})

    # ---- 还没接线的四项：登记在册，明写未评估 ----
    for cid, note in _DEFERRED_NOTE.items():
        out.append({"id": cid, "status": NOT_EVALUATED, "detail": note})

    order = [c[0] for c in CHECKS]
    out.sort(key=lambda c: order.index(c["id"]) if c["id"] in order else 99)

    n_breach = sum(1 for c in out if c["status"] == BREACH)
    n_unknown = sum(1 for c in out if c["status"] == NOT_EVALUATED)
    if n_breach:
        status = BREACH
    elif n_unknown:
        # **这里绝不能是 PASS。** 见模块开头。
        status = PARTIAL
    else:
        status = PASS
    return {"status": status, "checks": out, "n_breach": n_breach,
            "n_not_evaluated": n_unknown, "n_total": len(out),
            "proforma": pf}


def render(res: dict) -> str:
    meaning = {PASS: "全部检查通过",
               BREACH: "**存在破限** —— 不应批准",
               PARTIAL: f"部分未评估 —— **不等于通过**",
               NOT_EVALUATED: "全部未评估"}
    lbl = {c[0]: (c[1], c[2], c[3]) for c in CHECKS}
    L = [f"事前合规（对**成交后**的假想组合）：{res['status']}　"
         f"{meaning.get(res['status'], '')}",
         f"  已评估 {res['n_total'] - res['n_not_evaluated']}/{res['n_total']}　"
         f"破限 {res['n_breach']}"]
    mark = {PASS: "通过", BREACH: "破限", NOT_EVALUATED: "未评估"}
    for c in res["checks"]:
        name, cap, when = lbl.get(c["id"], (c["id"], "", ""))
        L.append(f"  [{mark.get(c['status'], c['status']):<4}] {name}"
                 + (f"（{cap}）" if cap else "")
                 + (f"　← {when} 实现" if c["status"] == NOT_EVALUATED else ""))
        L.append(f"         {c['detail']}")
    return "\n".join(L)
