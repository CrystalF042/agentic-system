"""CRO —— 风险审查（确定性，零 LLM）。

**架构冻结 v1.0 的职责边界，这个模块必须守住两条：**

    CRO 可以生成 risk budget 与 hard constraints，**不能输出 target weight**。
    PC 可以计算 target weight，**不能重新判断 investment thesis**。

不这么钉死，职责链会慢慢长回去——旧版 `build_cro` 就是活例子：
它输出 `target_position="中仓"`，同时对一部的论点、信心、失效条件一无所知
（grep 零命中）。**既没读到一部，又越权做了 PC 的事。**

---

**CRO 只拿结构化接口，不看一部的多空论述原文。**

    ticker / direction / conviction / evidence_gate / thesis_id
    / invalidation_conditions / direction_drift

一旦让它读到完整 Bull/Bear argument，它会忍不住重新判断"这个观点到底对不对"——
那就成了第三个投资委员会，而不是独立的风险审查。
这套结构里最有价值的就是 separation of duties：
**一部判断观点；CRO 假设观点成立，然后研究后果。**

---

**为什么 CRO 可以完全确定性、不需要 LLM。**

它的判断力体现在两处：**约束**与**否决**。两者都由政策阈值编码。
而 risk_budget 的基数是政策常数、只受 regime 调制——
一个可以逐票自由设定的 RB，就是伪装成风险预算的 target weight。
"""
from __future__ import annotations

from . import sizing
from .utils import get_logger

log = get_logger("cio.cro")

# ---------------------------------------------------------------- 风险政策
# 全部是【人选的红线】，不是算出来的。改这里会改变风险判断，改动需要政策理由。
POLICY = {
    "base_risk_budget": 0.015,      # 每只候选标的的基准风险预算（年化波动贡献）
    "single_name_cap": 0.05,        # 单票权重上限
    "sector_cap": 0.20,             # 单一 GICS 行业合计上限
    "theme_cap": 0.25,              # 单一主题（AI / 半导体等）合计上限
    "portfolio_risk_cap": 0.12,     # 组合年化波动上限（第二趟总量约束）
    "veto_beta": 3.5,               # Beta 超过此值直接否决
    "veto_vol": 1.50,               # 已实现波动率超过 150% 直接否决
    "veto_maxdd": -0.60,            # 近一年最大回撤超过 -60% 直接否决
    "warn_beta": 2.0,
    "warn_vol": 0.60,
    "warn_maxdd": -0.35,
}


# ---------------------------------------------------------------- 单位闸门
# **POLICY 的阈值全部是小数：veto_vol = 1.50 表示年化波动率 150%。**
#
# 这道闸门是被一次真实的假否决教出来的：`measures.ann_vol()` 返回百分数
# （40.74 = 40.74%），run_pc 未换算就送进来，40.74 > 1.50 成立，
# NVDA 被否决，理由行印的是"已实现波动率(60日年化) 40.74 触及否决线 1.50"——
# **每一个字都对，唯独单位错了，而报告上看不出任何异常。**
#
# 所以这里不"容错"、不自动 /100 猜口径：**猜错的方向同样静默。**
# 超出物理合理区间就抛错，把口径不符变成一次必须处理的失败。
_UNIT_BOUNDS = {
    "sigma_60":  (0.0, 5.0, "年化波动率"),      # 500% 年化已是极端值，再高就是单位错
    "sigma_252": (0.0, 5.0, "年化波动率"),
    "maxdd":     (-1.0, 0.0, "最大回撤"),       # 回撤是负数且不可能小于 −100%
    "beta":      (-10.0, 10.0, "Beta"),
}


def check_units(measures: dict) -> None:
    """量级校验。**不合理就抛错，不静默改口径。**

    抛出的信息必须让人一眼看出是单位问题而不是"这只票真的很危险"。
    """
    for key, (lo, hi, label) in _UNIT_BOUNDS.items():
        v = (measures or {}).get(key)
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            raise ValueError(f"{key} 不是数字：{v!r}")
        if not (lo - 1e-9 <= f <= hi + 1e-9):
            hint = ""
            if key != "maxdd" and abs(f) > hi:
                hint = f"（{f} 很可能是百分数，小数应为 {f / 100.0:g}——调用 measures.as_ratio()）"
            elif key == "maxdd" and f < lo:
                hint = f"（{f} 很可能是百分数，小数应为 {f / 100.0:g}——调用 measures.as_ratio()）"
            raise ValueError(
                f"{label} {key}={f} 超出合理区间 [{lo}, {hi}]，判定为**单位不符**而非极端行情。"
                f"CRO 的阈值是小数口径。{hint}")


def _cap_from_headroom(cap_total: float, used: float) -> float:
    """行业/主题上限换算成这只标的还能占多少。已用满则返回 0——
    **返回 0 而不是 None**：0 是"确实没有余量"，None 是"算不出来"，
    两者在下游的含义完全不同（见 sizing.apply_caps 对 None 的处理）。
    """
    return max(0.0, float(cap_total) - float(used or 0.0))


def assess_one(*, ticker: str, direction: str, conviction: str, evidence_gate: str,
               thesis_id: int = 0, invalidation_conditions=None, direction_drift=None,
               measures: dict, sector_used: float = 0.0, theme_used: float = 0.0,
               regime: str = "neutral", policy: dict = None) -> dict:
    """对单只标的做风险审查。

    measures：二部口径的确定性测量
        {sigma_60, sigma_252, beta, maxdd, corr_bench, liquidity_cap}
        取不到的项传 None，**不要传 0**——0 会被当成真实测量值。

    返回 CRO → PC 的数字契约：risk_budget / caps / binding_flags / veto / regime。
    **不含 target weight，也不含「轻仓/中仓/重仓」。**
    """
    P = dict(POLICY)
    P.update(policy or {})
    m = measures or {}
    check_units(m)              # 先校口径再比阈值——口径不符时任何比较都是无效的
    flags, notes = [], []

    # ---- 硬否决。理由必须具体到指标与阈值，否则否决就是不可复核的判断 ----
    veto, veto_reason = False, ""
    for key, val, thr, cmp_gt, label in (
            ("beta", m.get("beta"), P["veto_beta"], True, "Beta"),
            ("vol", m.get("sigma_60"), P["veto_vol"], True, "已实现波动率(60日年化)"),
            ("maxdd", m.get("maxdd"), P["veto_maxdd"], False, "近一年最大回撤")):
        if val is None:
            continue
        hit = val > thr if cmp_gt else val < thr
        if hit:
            # 阈值与数值都按小数口径印，并同时给出百分数——单位错误在报告上要看得见
            veto, veto_reason = True, (
                f"{label} {val:.2%} 触及否决线 {thr:.2%}" if key != "beta"
                else f"{label} {val:.2f} 触及否决线 {thr:.2f}")
            break

    # ---- 警戒（不否决，但要出现在报告里）----
    for key, val, thr, cmp_gt, label in (
            ("beta", m.get("beta"), P["warn_beta"], True, "Beta"),
            ("vol", m.get("sigma_60"), P["warn_vol"], True, "波动率"),
            ("maxdd", m.get("maxdd"), P["warn_maxdd"], False, "最大回撤")):
        if val is None:
            notes.append(f"{label}：无数据——本项未评估（不等于安全）")
            continue
        if (val > thr) if cmp_gt else (val < thr):
            flags.append(f"{label} {val:.2%} 超过警戒线 {thr:.2%}" if key != "beta"
                         else f"{label} {val:.2f} 超过警戒线 {thr:.2f}")

    # ---- 失效条件与方向漂移是风险信息，不是观点评价 ----
    inval = list(invalidation_conditions or [])
    if not inval:
        flags.append("该论点没有可核对的失效条件——无法被后续事实证伪")
    drift = (direction_drift or {}).get("severity")
    if drift == "no_evidence":
        flags.append("方向在无新证据的情况下发生了改变")
    elif drift == "thin":
        flags.append("方向改变时证据偏薄")

    # ---- 风险预算：政策常数 × regime × 信心。CRO 不逐票自由设定这个数 ----
    rb = sizing.risk_budget(P["base_risk_budget"], conviction, regime)

    caps = {
        "single_name": P["single_name_cap"],
        "sector": _cap_from_headroom(P["sector_cap"], sector_used),
        "theme": _cap_from_headroom(P["theme_cap"], theme_used),
        # 流动性上限由调用方按成交额算；算不出就是 None（未评估），不是无上限
        "liquidity": m.get("liquidity_cap"),
    }

    out = {
        "ticker": ticker, "direction": direction, "conviction": conviction,
        "evidence_gate": evidence_gate, "thesis_id": thesis_id,
        "regime": regime,
        "base_risk_budget": rb["base_risk_budget"],
        "conviction_multiplier": rb["conviction_multiplier"],
        "regime_multiplier": rb["regime_multiplier"],
        "adjusted_risk_budget": rb["adjusted_risk_budget"],
        "caps": caps,
        # 把用于判断的测量原值带出来。**否决行只印阈值不印口径，就是这次假否决
        # 能活下来的原因**——报告上看得见 σ60 = 40.74%，40.74 > 1.50 这种比较
        # 一眼就知道不成立。
        "measures": {k: m.get(k) for k in
                     ("sigma_60", "sigma_252", "beta", "maxdd", "corr_bench")},
        "risk_constraints": flags,
        "binding_risk_constraint": flags[0] if flags else "",
        "veto": veto, "veto_reason": veto_reason,
        "portfolio_risk_cap": P["portfolio_risk_cap"],
        "notes": notes + ([rb["note"]] if rb["note"] else []),
    }
    if veto:
        log.warning("CRO 否决 %s：%s", ticker, veto_reason)
    return out


def render_one(a: dict) -> str:
    L = [f"**{a['ticker']}**　方向 {a['direction']}｜信心 {a['conviction']}"
         f"｜Gate {a['evidence_gate']}"
         + (f"｜论点 #{a['thesis_id']}" if a.get("thesis_id") else "")]
    mm = a.get("measures") or {}
    if mm:
        def _f(k, pct=True):
            v = mm.get(k)
            if v is None:
                return "未评估"
            return f"{v:.2%}" if pct else f"{v:.2f}"
        L.append(f"- 测量（小数口径）：σ60 {_f('sigma_60')}　σ252 {_f('sigma_252')}"
                 f"　Beta {_f('beta', False)}　近一年最大回撤 {_f('maxdd')}")
    if a.get("veto"):
        L.append(f"- ⛔ **CRO 否决**：{a['veto_reason']}")
    # 风险预算可能整段未算（例如测量口径不符时根本没走到 CRO 的计算）。
    # **这种情况要印"未计算"，不能让渲染层崩掉**——报错会把一整轮组合报告带走。
    if a.get("adjusted_risk_budget") is None:
        L.append("- 风险预算：未计算（本标的未完成风险审查）")
    else:
        L.append(f"- 风险预算：{a['base_risk_budget']:.3%}"
                 f" × 信心 {a['conviction_multiplier']:.2f}"
                 f" × regime {a['regime_multiplier']:.2f}"
                 f" = **{a['adjusted_risk_budget']:.3%}**")
    caps = a.get("caps") or {}
    ev = {k: v for k, v in caps.items() if v is not None}
    ne = sorted(k for k, v in caps.items() if v is None)
    if ev:
        L.append("- 逐票上限：" + "　".join(f"{k} {v:.2%}" for k, v in sorted(ev.items())))
    if ne:
        L.append(f"- ⚠ 未评估的上限：{'、'.join(ne)}——**未评估不等于无上限**")
    for f in a.get("risk_constraints") or []:
        L.append(f"- ⚠ {f}")
    for n in a.get("notes") or []:
        L.append(f"- {n}")
    return "\n".join(L)
