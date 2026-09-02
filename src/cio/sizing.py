"""Portfolio Construction —— 确定性仓位计算（零 LLM）。

**这个模块存在的理由：仓位非常适合数学规则，不适合让模型说"综合考虑，建议中仓"。**

职责边界（架构冻结 v1.0，不得越界）：

    一部   产生观点          方向 / 信心 / Evidence Gate / 失效条件
    二部   测量现实          波动率 / Beta / 流动性 / 分位 / 风格暴露
    CRO   假设观点成立，研究后果   risk_budget + 约束 + 否决（**不给 target weight**）
    PC    决定承担多少        本模块（**不重新判断投资论点**）
    CEO   做不做

计算链：

    RB_i      = RB_base × regime_mult × conviction_mult
    σ_eff     = max(σ60, 0.75·σ60 + 0.25·σ252, σ_floor)
    w_raw     = RB_i / σ_eff
    w_final   = min(w_raw, 逐票各 cap)          ← 第一趟：逐票
    （组合层总量约束是第二趟，见 portfolio_scale）

**三条刻意的设计，每条都防一种"看起来合理"的错误：**

一、**σ 只被抬高，不被压低。** max() 是单向的——近期波动升高立刻接管，
    近期异常平静但长期危险时长期记忆不消失，两者都低时 floor 接管。
    作为风险政策这是对的：它只会缩仓，不会放大。

二、**σ_floor 是风险政策，不是回测优化出来的参数。** w ∝ 1/σ 在 σ→0 时发散；
    安静期的股票会在波动扩张【之前】拿到最大权重，而波动率均值回复且自相关，
    所以这是系统性偏差不是偶发。以后要改 floor，依据必须是风险政策，
    不能是"哪个值历史收益最好"——那就把风险参数变成了 alpha 参数。

三、**Σw 不归一化到 100%。** 残差就是现金。归一化等于把风险规则刚压下去的仓位
    重新吹回来，整套风险预算白做。"权重加起来不到 100% 看起来不对"是个很强的
    心理诱惑，所以这条写死在代码里，并且有自检守着。
"""
from __future__ import annotations

from .utils import get_logger

log = get_logger("cio.sizing")

# ---------------------------------------------------------------- 政策常数
# 全部是【风险政策】，不是拟合出来的。改动它们需要的是政策理由，不是回测结果。

# 信心缩放 risk_budget，**不在最终权重上另乘一次**——
# 乘两次会让信心的影响被平方，而且哪一次生效说不清。
CONVICTION_MULT = {"强": 1.00, "中": 0.75, "弱": 0.50}

# Evidence Gate 已把 THIN 的信心封顶为「弱」、INSUFFICIENT 封顶为「中」，
# 于是闸门自然传导成较低的 risk budget。**闸门不再单独作为一项 cap**——
# 它决定"这只标的是不是候选"，不是 min() 里的一个数。
# 档位的规范换算点在 material_gate.level_from_verdict()，这里只消费档位字符串。
# **UNRECORDED 同样不给仓位，但理由必须与 INSUFFICIENT 分开写**：
# 前者是"闸门没跑过，不知道"，后者是"闸门跑过了，判定没有实质材料"。
# 印同一句话，就等于把"不知道"报告成了一次主动的弃权判断。
GATE_NOT_CANDIDATE = ("INSUFFICIENT", "UNRECORDED")

_GATE_REASON = {
    "INSUFFICIENT": "Evidence Gate = INSUFFICIENT：一部未产出观点，不进候选池",
    "UNRECORDED": "Evidence Gate 未记录：该论点没有材料判定字段"
                  "——**不等于「一部未产出观点」**，重跑一次一部才能定档",
}

REGIME_MULT = {"risk_on": 1.00, "neutral": 0.80, "risk_off": 0.50}

SIGMA_FLOOR = 0.15          # 年化 15%
BLEND_W60, BLEND_W252 = 0.75, 0.25


def effective_sigma(sigma_60, sigma_252, floor: float = SIGMA_FLOOR) -> dict:
    """σ_eff 及其全部中间量。**每一步都返回，因为半年后要能回答"为什么用了这个 σ"。**

    只知道"当时用了 40% 波动率"不够；要知道是近期接管、长期记忆接管，还是 floor 接管——
    这三种情况指向完全不同的解释。
    """
    s60 = None if sigma_60 is None else float(sigma_60)
    s252 = None if sigma_252 is None else float(sigma_252)
    if s60 is None and s252 is None:
        return {"sigma_60": None, "sigma_252": None, "sigma_blend": None,
                "sigma_floor": floor, "sigma_effective": None,
                "sigma_binding_component": None,
                "reason": "两个窗口的波动率都取不到——无法定仓位"}

    parts = {"sigma_60": s60, "sigma_252": s252, "sigma_floor": floor}
    if s60 is not None and s252 is not None:
        blend = BLEND_W60 * s60 + BLEND_W252 * s252
    else:                       # 缺一个窗口时不假装能混合，用现有的那个
        blend = s60 if s60 is not None else s252
    parts["sigma_blend"] = blend

    cands = {"sigma_60": s60, "sigma_blend": blend, "sigma_floor": floor}
    cands = {k: v for k, v in cands.items() if v is not None}
    eff = max(cands.values())
    # 并列时全部记下来——"哪一项决定的"可能不止一项
    binding = sorted(k for k, v in cands.items() if v == eff)
    parts["sigma_effective"] = eff
    parts["sigma_binding_component"] = binding
    parts["reason"] = ""
    return parts


def risk_budget(base: float, conviction: str, regime: str = "neutral") -> dict:
    """RB_i = base × regime × conviction。

    **base 是政策常数，不是 CRO 逐票判断的数字。** 一个自由的 per-name RB
    等于伪装成风险预算的 target weight——CRO 的判断力应该体现在
    约束与否决上，而不是一个能直接换算成仓位的自由量。
    """
    cm = CONVICTION_MULT.get(conviction)
    noted = ""
    if cm is None:                          # 认不出的信心一律按最保守处理，并说出来
        cm, noted = CONVICTION_MULT["弱"], f"信心「{conviction}」无法识别，按「弱」处理"
    rm = REGIME_MULT.get(regime)
    if rm is None:
        rm, noted = REGIME_MULT["neutral"], (noted + f"；regime「{regime}」无法识别，按 neutral 处理").lstrip("；")
    return {"base_risk_budget": float(base), "conviction": conviction,
            "conviction_multiplier": cm, "regime": regime, "regime_multiplier": rm,
            "adjusted_risk_budget": float(base) * cm * rm, "note": noted}


def raw_weight(adjusted_rb: float, sigma_effective) -> "float | None":
    if sigma_effective is None or sigma_effective <= 0:
        return None
    return float(adjusted_rb) / float(sigma_effective)


def apply_caps(w_raw, caps: dict) -> dict:
    """第一趟：逐票 cap 取 min。

    caps 形如 {"single_name": 0.05, "sector": 0.032, "liquidity": None, ...}

    **值为 None 的 cap 表示"算不出来"，不表示"无上限"。**
    静默地把算不出的上限当成无穷大，等于在没有行业约束的情况下按无行业约束建仓，
    而报告上看不出任何异常——这正是本项目一直在防的静默失败。
    所以未评估的 cap 单独列出来，落库、上报告。

    binding 用**列表**：w_sector 与 w_liquidity 同时等于最小值是会发生的，
    只存一个字符串会丢掉一半信息。
    """
    if w_raw is None:
        return {"w_raw": None, "w_final": None, "binding_position_constraint": [],
                "caps_evaluated": {}, "caps_not_evaluated": sorted(caps or {}),
                "reason": "w_raw 不可得（波动率缺失）——不给仓位"}
    evaluated = {k: float(v) for k, v in (caps or {}).items() if v is not None}
    missing = sorted(k for k, v in (caps or {}).items() if v is None)
    pool = dict(evaluated)
    pool["risk_budget"] = float(w_raw)      # w_raw 自己也参与 min，它可能就是最小的
    w_final = min(pool.values())
    binding = sorted(k for k, v in pool.items() if v == w_final)
    return {"w_raw": float(w_raw), "w_final": w_final,
            "binding_position_constraint": binding,
            "caps_evaluated": evaluated, "caps_not_evaluated": missing,
            "reason": ""}


def portfolio_scale(weights: dict, portfolio_risk_cap, portfolio_risk) -> dict:
    """第二趟：组合层总量约束。

    组合层上限不是逐票的量，**不能放进第一趟的 min()**——它没有天然的逐票值。
    超限时按比例统一缩放，并把缩放系数与触发原因单独落库：
    不这么做的话，半年后看到一个被缩过的仓位，会误以为是某个逐票 cap 绑定的。
    """
    out = {"scale_factor": 1.0, "portfolio_risk": portfolio_risk,
           "portfolio_risk_cap": portfolio_risk_cap, "scaled": False, "reason": ""}
    if portfolio_risk_cap is None or portfolio_risk is None:
        out["reason"] = "组合层风险或上限不可得——本趟未评估（不等于未超限）"
        out["weights"] = dict(weights or {})
        return out
    if portfolio_risk <= portfolio_risk_cap or portfolio_risk <= 0:
        out["weights"] = dict(weights or {})
        return out
    k = float(portfolio_risk_cap) / float(portfolio_risk)
    out.update(scale_factor=k, scaled=True,
               reason=f"组合层风险 {portfolio_risk:.4f} 超过上限 {portfolio_risk_cap:.4f}，全体按 {k:.4f} 缩放",
               weights={t: w * k for t, w in (weights or {}).items()})
    return out


def cash_residual(weights: dict) -> float:
    """1 − Σw。**这就是现金，不要归一化。**

    归一化会把风险规则刚压下去的仓位重新吹回来，整套风险预算白做。
    Σw > 1 时返回负数——那是真的杠杆，必须看得见，不能被 clip 成 0。
    """
    return 1.0 - sum((weights or {}).values())


def size_one(*, ticker: str, conviction: str, evidence_gate: str,
             sigma_60, sigma_252, caps: dict, base_rb: float,
             regime: str = "neutral") -> dict:
    """单只标的的完整 lineage。返回的每个字段都是要落库的——
    **不记"哪一项绑定"，就永远无法回答"这个仓位是被谁决定的"。**
    """
    if evidence_gate in GATE_NOT_CANDIDATE:
        return {"ticker": ticker, "evidence_gate": evidence_gate, "w_final": None,
                "reason": _GATE_REASON[evidence_gate]}
    sig = effective_sigma(sigma_60, sigma_252)
    rb = risk_budget(base_rb, conviction, regime)
    w = apply_caps(raw_weight(rb["adjusted_risk_budget"], sig["sigma_effective"]), caps)
    out = {"ticker": ticker, "evidence_gate": evidence_gate}
    out.update(rb)
    out.update(sig)
    out.update(w)
    out["reason"] = w["reason"] or sig["reason"]
    return out
