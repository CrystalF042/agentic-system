#!/usr/bin/env python3
"""Portfolio Construction 自检 —— 确定性、不联网、不调模型。

用法：  python scripts/test_sizing.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _no_network                                            # noqa: E402,F401
"""测试期间禁止联网 —— 靠真实行情才通过的断言，换台机器就是另一个结果。"""

from cio import sizing as S                                   # noqa: E402

FAIL = []


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))
    if not cond:
        FAIL.append(name)


def close(a, b, tol=1e-9):
    return a is not None and b is not None and abs(a - b) < tol


print("=" * 60)
print("Portfolio Construction 自检")
print("=" * 60)

# ============================================================ 1. σ_eff 的三个性质
print("\n[1] σ_eff = max(σ60, 0.75σ60+0.25σ252, floor)")

# 近期波动升高 → σ60 立即接管
a = S.effective_sigma(0.60, 0.30)
check("近期升高时 σ60 接管", close(a["sigma_effective"], 0.60), str(a["sigma_effective"]))
check("并记下是哪一项绑定", "sigma_60" in a["sigma_binding_component"],
      str(a["sigma_binding_component"]))

# 近期异常平静但长期危险 → 252 日记忆不消失（但被阻尼，不等于 σ252）
b = S.effective_sigma(0.20, 0.60)
check("近期平静时长期记忆接管", close(b["sigma_effective"], 0.75 * 0.20 + 0.25 * 0.60),
      str(b["sigma_effective"]))
check("长期记忆被阻尼而非全额采用", b["sigma_effective"] < 0.60)
check("绑定项是 blend", b["sigma_binding_component"] == ["sigma_blend"])

# 两者都低 → floor 接管
c = S.effective_sigma(0.05, 0.06)
check("两者都低时 floor 接管", close(c["sigma_effective"], S.SIGMA_FLOOR))
check("绑定项是 floor", c["sigma_binding_component"] == ["sigma_floor"])

# **单向性**：max() 只抬高不压低 —— 只会缩仓，不会放大
for s60, s252 in [(0.40, 0.20), (0.20, 0.40), (0.05, 0.05), (0.9, 0.1)]:
    e = S.effective_sigma(s60, s252)["sigma_effective"]
    check(f"σ_eff 不低于 σ60 与 floor（{s60},{s252}）",
          e >= max(s60, S.SIGMA_FLOOR) - 1e-12, str(e))

# 缺数据时不假装
d = S.effective_sigma(None, None)
check("两个窗口都缺 → 不给 σ，并说明原因",
      d["sigma_effective"] is None and "无法定仓位" in d["reason"])
e1 = S.effective_sigma(0.40, None)
check("缺 σ252 时不假装能混合", close(e1["sigma_blend"], 0.40), str(e1["sigma_blend"]))

# ============================================================ 2. risk budget
print("\n[2] RB = base × regime × conviction")
r = S.risk_budget(0.015, "强", "risk_on")
check("强信心 + risk_on 不打折", close(r["adjusted_risk_budget"], 0.015))
r2 = S.risk_budget(0.015, "弱", "risk_off")
check("弱信心 + risk_off = 0.50 × 0.50", close(r2["adjusted_risk_budget"], 0.015 * 0.25))
check("信心与 regime 的乘数分别落库",
      close(r2["conviction_multiplier"], 0.5) and close(r2["regime_multiplier"], 0.5))
r3 = S.risk_budget(0.015, "非常强", "neutral")
check("认不出的信心按最保守处理", close(r3["conviction_multiplier"], 0.5))
check("并且说出来，不静默降级", "无法识别" in r3["note"], r3["note"])

# 信心只在 RB 上乘一次 —— 乘两次会让影响被平方，且说不清哪次生效
import inspect as _i                                          # noqa: E402
check("信心不在最终权重上再乘一次",
      "conviction" not in _i.getsource(S.raw_weight))

# ============================================================ 3. 逐票 cap
print("\n[3] 第一趟：w_final = min(w_raw, 各逐票 cap)")
w = S.apply_caps(0.0800, {"single_name": 0.05, "sector": 0.032, "theme": 0.06})
check("取最小值", close(w["w_final"], 0.032), str(w["w_final"]))
check("绑定项正确", w["binding_position_constraint"] == ["sector"])

# 并列 binding 必须全部记下来
w2 = S.apply_caps(0.08, {"sector": 0.032, "liquidity": 0.032, "single_name": 0.05})
check("两个 cap 同时绑定 → 都记下来",
      w2["binding_position_constraint"] == ["liquidity", "sector"],
      str(w2["binding_position_constraint"]))

# w_raw 自己可能就是最小的
w3 = S.apply_caps(0.01, {"single_name": 0.05, "sector": 0.032})
check("风险预算本身最小时记为 risk_budget",
      w3["binding_position_constraint"] == ["risk_budget"])

# **算不出的 cap ≠ 无上限**
w4 = S.apply_caps(0.08, {"single_name": 0.05, "sector": None, "liquidity": None})
check("未评估的 cap 不参与 min", close(w4["w_final"], 0.05))
check("但必须单独列出（静默当成无穷大是最危险的）",
      w4["caps_not_evaluated"] == ["liquidity", "sector"], str(w4["caps_not_evaluated"]))
check("已评估的 cap 也落库", w4["caps_evaluated"] == {"single_name": 0.05})

w5 = S.apply_caps(None, {"single_name": 0.05})
check("w_raw 不可得时不给仓位", w5["w_final"] is None and "不给仓位" in w5["reason"])

# ============================================================ 4. 组合层是第二趟
print("\n[4] 第二趟：组合层总量约束")
ws = {"NVDA": 0.04, "AMD": 0.03, "AVGO": 0.03}
p = S.portfolio_scale(ws, portfolio_risk_cap=0.10, portfolio_risk=0.20)
check("超限时按比例缩放", close(p["scale_factor"], 0.5) and close(p["weights"]["NVDA"], 0.02))
check("缩放事实单独落库（否则会被误认为逐票 cap 绑定）", p["scaled"] is True)
check("缩放原因可读", "超过上限" in p["reason"])
p2 = S.portfolio_scale(ws, 0.30, 0.20)
check("未超限时不动", p2["scaled"] is False and close(p2["weights"]["NVDA"], 0.04))
p3 = S.portfolio_scale(ws, None, 0.20)
check("组合层数据缺失 → 明说未评估，不等于未超限",
      "未评估" in p3["reason"] and "不等于未超限" in p3["reason"])
check("组合层上限【不】出现在逐票 min 里",
      "portfolio_risk_cap" not in _i.getsource(S.apply_caps))

# ============================================================ 5. 不归一化
print("\n[5] Σw 不归一化 —— 残差就是现金")
check("残差 = 1 − Σw", close(S.cash_residual({"A": 0.03, "B": 0.02}), 0.95))
check("Σw > 1 时返回负数（真杠杆必须看得见，不能 clip）",
      S.cash_residual({"A": 0.7, "B": 0.5}) < 0)
src = _i.getsource(S)
check("代码里没有归一化操作", "/ total" not in src and "normalize" not in src.lower())
check("并写明为什么不归一化", "整套风险预算白做" in src)

# ============================================================ 6. 端到端 lineage
print("\n[6] size_one：一条完整可归因的 lineage")
one = S.size_one(ticker="NVDA", conviction="中", evidence_gate="SUFFICIENT",
                 sigma_60=0.4074, sigma_252=0.35,
                 caps={"single_name": 0.05, "sector": 0.032, "liquidity": None},
                 base_rb=0.015, regime="neutral")
need = ["base_risk_budget", "conviction_multiplier", "regime_multiplier",
        "adjusted_risk_budget", "sigma_60", "sigma_252", "sigma_blend",
        "sigma_floor", "sigma_effective", "sigma_binding_component",
        "w_raw", "w_final", "binding_position_constraint",
        "caps_evaluated", "caps_not_evaluated"]
for k in need:
    check(f"lineage 含 {k}", k in one)
check("σ_eff 走 σ60（近期高于长期）", close(one["sigma_effective"], 0.4074))
# RB_adj = 0.015 × 0.75(中) × 0.80(neutral) = 0.009 —— regime 乘数不能漏
check("w_raw = RB_adj / σ_eff",
      close(one["w_raw"], (0.015 * 0.75 * 0.80) / 0.4074), str(one["w_raw"]))
check("此例中风险预算本身最小 → 绑定项是 risk_budget（健康情形：cap 未触发）",
      one["binding_position_constraint"] == ["risk_budget"],
      str(one["binding_position_constraint"]))

# 让 sector cap 真的绑定：把 σ 压到 floor，w_raw 才会大过 cap
tight = S.size_one(ticker="X", conviction="强", evidence_gate="SUFFICIENT",
                   sigma_60=0.05, sigma_252=0.05,
                   caps={"single_name": 0.05, "sector": 0.032}, base_rb=0.015,
                   regime="risk_on")
check("σ 触到 floor 时 w_raw = 0.015/0.15 = 10%", close(tight["w_raw"], 0.10),
      str(tight["w_raw"]))
check("此时 sector cap 绑定", tight["binding_position_constraint"] == ["sector"])
check("并且 σ 的绑定项记为 floor", tight["sigma_binding_component"] == ["sigma_floor"])

# Evidence Gate 决定"是不是候选"，不是 min() 里的一项
ins = S.size_one(ticker="NVDA", conviction="中", evidence_gate="INSUFFICIENT",
                 sigma_60=0.40, sigma_252=0.35, caps={"single_name": 0.05},
                 base_rb=0.015)
check("INSUFFICIENT → 不进候选池，无仓位", ins["w_final"] is None)
check("并说明是闸门的原因", "未产出观点" in ins["reason"])
check("闸门不作为 cap 出现在 min 里", "gate" not in _i.getsource(S.apply_caps).lower())

thin = S.size_one(ticker="NVDA", conviction="弱", evidence_gate="THIN",
                  sigma_60=0.40, sigma_252=0.35, caps={"single_name": 0.05},
                  base_rb=0.015)
check("THIN 通过信心封顶自然传导成较低 RB",
      close(thin["adjusted_risk_budget"], 0.015 * 0.5 * 0.8), str(thin["adjusted_risk_budget"]))

# ============================================================ 7. 政策参数不是拟合参数
print("\n[7] 政策参数")
check("σ_floor = 15% 年化", close(S.SIGMA_FLOOR, 0.15))
check("信心乘数 弱/中/强 = 0.50/0.75/1.00",
      S.CONVICTION_MULT == {"强": 1.00, "中": 0.75, "弱": 0.50})
check("regime 乘数 risk_on/neutral/risk_off = 1.0/0.8/0.5",
      S.REGIME_MULT == {"risk_on": 1.00, "neutral": 0.80, "risk_off": 0.50})
check("注释写明 floor 是风险政策、不得按收益优化",
      "哪个值历史收益最好" in src)
check("PC 不重新判断投资论点（模块内无方向/论点逻辑）",
      "direction" not in src and "thesis" not in src.lower())

print("\n" + "=" * 60)
if FAIL:
    print(f"FAILED {len(FAIL)}: " + "; ".join(FAIL))
    raise SystemExit(1)
print("全部通过。")
