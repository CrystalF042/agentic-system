#!/usr/bin/env python3
"""CRO → PC 自检 —— 确定性、不联网、不调模型。

用法：  python scripts/test_cro_pc.py
"""
from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _no_network                                            # noqa: E402,F401
"""测试期间禁止联网 —— 靠真实行情才通过的断言，换台机器就是另一个结果。"""

from cio import pc_ledger, portfolio, regime, risk_officer, sizing    # noqa: E402

# ---------------------------------------------------------------- 测试隔离
# **自检绝不能往真实的 cio.db 里写 lineage。** pc_lineage 是"每一次定仓当时
# 的输入"的记录，是归因分析的唯一依据；混进 TESTPC / TESTVETO 这种假行，
# 半年后的收益拆解就建立在被污染的样本上，而且这些行看起来和真行一模一样。
import tempfile                                                      # noqa: E402

from cio import db as _db                                            # noqa: E402

_TMPDB = Path(tempfile.mkdtemp(prefix="cio-selftest-")) / "test.db"
_db.DB_PATH = _TMPDB

FAIL = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))
    if not cond:
        FAIL.append(name)


print("=" * 60)
print("CRO → PC 自检")
print("=" * 60)

# ============================================================ 1. 持仓归属
print("\n[1] 持仓：唯一真源 + 显式归属")
for c in ("688012", "002371", "300308"):
    check(f"A 股 {c} → LEGACY_A_SHARE_PAPER",
          portfolio.infer_portfolio_id(c) == portfolio.LEGACY_A_SHARE)
for c in ("NVDA", "AMD", "AVGO"):
    check(f"美股 {c} → US_PAPER", portfolio.infer_portfolio_id(c) == portfolio.US_PAPER)

_psrc = inspect.getsource(portfolio)
# 检查【行为】，不是注释里的字眼：任何输入都必须落到一个非空的显式 id
check("任何代码都得到非空的显式归属（不产生 NULL/UNKNOWN）",
      all(portfolio.infer_portfolio_id(x) in
          (portfolio.LEGACY_A_SHARE, portfolio.US_PAPER, portfolio.CN_PAPER)
          for x in ("", None, "  ", "NVDA", "688012", "???")))
check("并写明为什么不用 NULL", "意外扫进风险计算" in _psrc)
try:
    portfolio.open_positions("")
    check("拒绝『读全部持仓』", False)
except ValueError as e:
    check("拒绝『读全部持仓』", "没有『读全部』" in str(e))
check("回填是幂等的（只处理空值行）",
      "portfolio_id IS NULL OR portfolio_id=''" in _psrc)

# ============================================================ 2. 市场 regime
print("\n[2] regime：CRO 自己的 market-level 输入")
_rsrc = inspect.getsource(regime)
check("数据全缺时按 neutral，不按 risk_on 兜底",
      regime.assess(fetch=lambda t, d: None)["regime"] == regime.NEUTRAL)
check("并写明理由（risk_on 会放大仓位）", "绝不能作为缺数据的兜底" in _rsrc)


def _fake(t, d):
    import pandas as pd
    base = {"SPY": 100, "RSP": 100, "GLD": 100}[t]
    tr = {"SPY": 1.0008, "RSP": 1.0011, "GLD": 0.9995}[t]
    return pd.DataFrame({"close": [base * (tr ** i) for i in range(260)]})


r = regime.assess(fetch=_fake)
check("宽度改善 + 避险回落 + 站上均线 → risk_on", r["regime"] == regime.RISK_ON, r["note"])
check("每个信号的原始比值都印出来（regime 必须可复核）",
      all("value" in s for s in r["signals"]) and "rsp_spy_60d" in regime.render(r))
check("窗口写进字段名（rsp_spy_60d 不是 rsp_spy）", "rsp_spy_60d" in regime.render(r))
check("投票制而非打分（打分要设权重，权重就是拟合空间）", "投票" in _rsrc and "权重就是拟合空间" in _rsrc)

# ============================================================ 3. CRO 边界
print("\n[3] CRO：给约束与否决，不给权重")
_csrc = inspect.getsource(risk_officer)
# 检查【返回值】，不是源码文本
_probe = risk_officer.assess_one(
    ticker="X", direction="看多", conviction="中", evidence_gate="SUFFICIENT",
    invalidation_conditions=["x 条件"], measures={"sigma_60": 0.3, "beta": 1.0})
check("CRO 返回值里没有 target weight",
      not any(k in _probe for k in ("target_weight", "target_position", "weight", "w_final")))
check("CRO 返回值里没有轻仓/中仓/重仓",
      not any(isinstance(v, str) and v in ("轻仓", "中仓", "重仓") for v in _probe.values()))
check("CRO 返回的是 risk_budget + caps + veto 这套数字契约",
      all(k in _probe for k in ("adjusted_risk_budget", "caps", "veto", "regime")))
check("CRO 不调模型", "ollama" not in _csrc.lower() and "MODEL_" not in _csrc)
# 检查【签名】：没有接收论述原文的参数
_params = set(inspect.signature(risk_officer.assess_one).parameters)
check("CRO 签名里没有接收多空论述的参数",
      not (_params & {"bull", "bear", "bull_case", "bear_case", "synthesis", "thesis"}))
check("CRO 签名只收结构化字段",
      {"direction", "conviction", "evidence_gate", "invalidation_conditions"} <= _params)
check("并写明理由（否则成了第三个投资委员会）", "第三个投资委员会" in _csrc)

hi = risk_officer.assess_one(
    ticker="X", direction="看多", conviction="强", evidence_gate="SUFFICIENT",
    invalidation_conditions=["毛利率跌破60%"],
    measures={"sigma_60": 1.8, "sigma_252": 1.2, "beta": 2.0, "maxdd": -0.3},
    regime="risk_on")
check("波动率触及否决线 → veto", hi["veto"] is True)
check("否决理由具体到指标与阈值", "触及否决线" in hi["veto_reason"], hi["veto_reason"])

warn = risk_officer.assess_one(
    ticker="X", direction="看多", conviction="中", evidence_gate="SUFFICIENT",
    invalidation_conditions=[],
    measures={"sigma_60": 0.70, "sigma_252": 0.5, "beta": 2.5, "maxdd": -0.40},
    regime="neutral")
check("未否决但触警戒 → 进 risk_constraints", len(warn["risk_constraints"]) >= 3,
      str(warn["risk_constraints"]))
check("没有失效条件本身算一条风险",
      any("无法被后续事实证伪" in f for f in warn["risk_constraints"]))

drift = risk_officer.assess_one(
    ticker="X", direction="看多", conviction="中", evidence_gate="SUFFICIENT",
    invalidation_conditions=["x 条件"], measures={"sigma_60": 0.3, "beta": 1.0},
    direction_drift={"severity": "no_evidence"}, regime="neutral")
check("无证据的方向漂移进入风险清单",
      any("无新证据" in f for f in drift["risk_constraints"]))

miss = risk_officer.assess_one(
    ticker="X", direction="看多", conviction="中", evidence_gate="SUFFICIENT",
    invalidation_conditions=["x 条件"],
    measures={"sigma_60": None, "beta": None, "maxdd": None}, regime="neutral")
check("测量缺失时明说『未评估』，不当成安全",
      any("不等于安全" in n for n in miss["notes"]), str(miss["notes"]))
check("行业/主题上限用余量而非总量",
      risk_officer.assess_one(
          ticker="X", direction="看多", conviction="中", evidence_gate="SUFFICIENT",
          invalidation_conditions=["x"], measures={"sigma_60": 0.3},
          sector_used=0.18)["caps"]["sector"] == risk_officer.POLICY["sector_cap"] - 0.18)
check("已用满时余量为 0（0 是『确实没余量』，None 是『算不出』）",
      risk_officer.assess_one(
          ticker="X", direction="看多", conviction="中", evidence_gate="SUFFICIENT",
          invalidation_conditions=["x"], measures={"sigma_60": 0.3},
          sector_used=0.30)["caps"]["sector"] == 0.0)

# ============================================================ 4. lineage 落库
print("\n[4] attribution：每一次定仓的完整 lineage")
_lsrc = inspect.getsource(pc_ledger)
for col in ("thesis_id", "direction", "conviction", "evidence_gate", "direction_drift",
            "base_risk_budget", "conviction_multiplier", "regime_multiplier",
            "adjusted_risk_budget", "risk_constraints", "binding_risk_constraint",
            "sigma_60", "sigma_252", "sigma_blend", "sigma_effective",
            "sigma_binding_component", "w_raw", "caps_evaluated", "caps_not_evaluated",
            "w_pre_scale", "portfolio_scale_factor", "w_final",
            "binding_position_constraint"):
    check(f"schema 含 {col}", col in _lsrc)
check("binding 存数组不存字符串（并列 binding 会发生）",
      "_j(size.get(\"binding_position_constraint\"))" in _lsrc)
check("写明为什么现在落库而不是半年后分析", "事后从持仓表反推不出来" in _lsrc)

cro = risk_officer.assess_one(
    ticker="TESTPC", direction="看多", conviction="中", evidence_gate="SUFFICIENT",
    thesis_id=999, invalidation_conditions=["毛利率跌破60%"],
    measures={"sigma_60": 0.4074, "sigma_252": 0.35, "beta": 1.9, "maxdd": -0.20,
              "liquidity_cap": None}, regime="neutral")
sz = sizing.size_one(ticker="TESTPC", conviction=cro["conviction"],
                     evidence_gate=cro["evidence_gate"], sigma_60=0.4074, sigma_252=0.35,
                     caps=cro["caps"], base_rb=cro["base_risk_budget"], regime=cro["regime"])
rid = pc_ledger.record(as_of_date="2026-08-26", portfolio_id="US_PAPER", cro=cro, size=sz)
check("落库成功", isinstance(rid, int) and rid > 0)
st = pc_ledger.binding_stats()
check("能回答『仓位是被谁决定的』", st["n"] >= 1 and bool(st["position_binding"]),
      str(st["position_binding"]))
check("能回答『σ 是被哪一项决定的』", bool(st["sigma_binding"]), str(st["sigma_binding"]))

# ============================================================ 5. 职责链不越界
print("\n[5] 职责边界")
_ssrc = inspect.getsource(sizing)
check("PC 不重新判断投资论点",
      "direction" not in _ssrc and "thesis" not in _ssrc.lower())
check("PC 不调模型", "ollama" not in _ssrc.lower())
check("Evidence Gate 决定是否候选，不是 min() 里的一项",
      "gate" not in inspect.getsource(sizing.apply_caps).lower())
check("组合层约束是第二趟，不在逐票 min 里",
      "portfolio_risk_cap" not in inspect.getsource(sizing.apply_caps))
check("不归一化到 100%", "normalize" not in _ssrc.lower() and "整套风险预算白做" in _ssrc)

# ============================================================ 6. 单位契约
print("\n[6] 单位：measures 是百分数，CRO 是小数")
from cio import measures                                             # noqa: E402

check("as_ratio 把百分数换成小数", measures.as_ratio(40.74) == 40.74 / 100.0)
check("as_ratio 对 None 透传（缺失不是 0）", measures.as_ratio(None) is None)
check("measures 声明了哪些函数返回百分数",
      {"ann_vol", "max_drawdown"} <= measures.PERCENT_RETURNING
      and "beta_corr" in measures.RATIO_RETURNING)
check("beta_corr 返回三元组（beta, corr, n_aligned）",
      len(measures.beta_corr(None, None, 250, 60)) == 3)

_ok = risk_officer.assess_one(
    ticker="NVDA", direction="看多", conviction="中", evidence_gate="SUFFICIENT",
    invalidation_conditions=["x"], regime="risk_on",
    measures={"sigma_60": 0.4074, "sigma_252": 0.50, "beta": 1.9, "maxdd": -0.2021})
check("σ60=40.74%（小数 0.4074）**不被**否决线 1.50 否决", _ok["veto"] is False,
      _ok.get("veto_reason", ""))
check("40.74% 也不触 60% 警戒线",
      not any("波动率" in f for f in _ok["risk_constraints"]), str(_ok["risk_constraints"]))
check("否决/警戒理由按百分号印，单位在报告上看得见",
      "%" in risk_officer.render_one(_ok) and "σ60 40.74%" in risk_officer.render_one(_ok),
      risk_officer.render_one(_ok).splitlines()[1])

for _bad, _lbl in (({"sigma_60": 40.74}, "σ 传成百分数"),
                   ({"maxdd": -20.21}, "回撤传成百分数"),
                   ({"sigma_60": 0.4, "sigma_252": 55.0}, "σ252 传成百分数")):
    try:
        risk_officer.assess_one(
            ticker="X", direction="看多", conviction="中", evidence_gate="SUFFICIENT",
            invalidation_conditions=["x"], measures=_bad)
        check(f"{_lbl} → 抛错而不是照常否决", False)
    except ValueError as e:
        check(f"{_lbl} → 抛错而不是照常否决",
              "单位不符" in str(e) and "as_ratio" in str(e))
_edge = risk_officer.assess_one(
    ticker="X", direction="看多", conviction="中", evidence_gate="SUFFICIENT",
    invalidation_conditions=["x"], measures={"sigma_60": 4.9}, regime="neutral")
check("合理区间内的数按原值用，**不猜口径也不偷偷 /100**",
      _edge["veto"] is True and _edge["measures"]["sigma_60"] == 4.9,
      _edge.get("veto_reason", ""))

# 真正跑一遍 run_pc 的测量层：单位换算 + 三元组 + 单项失败不污染其它项
import importlib.util                                                # noqa: E402
import pandas as pd                                                  # noqa: E402
from cio import quant_data                                           # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "run_pc_mod", Path(__file__).resolve().parents[1] / "run_pc.py")
_rp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_rp)


def _panel(n=400, drift=1.0006, amp=0.02):
    import math
    d = pd.date_range("2024-01-02", periods=n, freq="B")
    px = [100 * (drift ** i) * (1 + amp * math.sin(i / 3.0)) for i in range(n)]
    return pd.DataFrame({"date": d, "close": px})


quant_data.get_history = lambda stocks, days=500, status=None: {stocks[0].code: _panel()}
quant_data.get_benchmark = lambda days=500: _panel(amp=0.008)
_m = _rp._measures_for("FAKE")
check("run_pc 交出的 σ 已是小数（0 < σ < 5）",
      _m["sigma_60"] is not None and 0 < _m["sigma_60"] < 5.0, str(_m["sigma_60"]))
check("run_pc 交出的回撤已是小数（−1 ≤ dd ≤ 0）",
      _m["maxdd"] is not None and -1.0 <= _m["maxdd"] <= 0.0, str(_m["maxdd"]))
check("beta 被正确解包（不再 too many values to unpack）", _m["beta"] is not None,
      str(_m["beta"]))
check("对齐天数一并带出（区分『算不出』与『样本不够』）", _m["beta_n_aligned"], str(_m))
check("这组测量能直接通过 CRO 的单位闸门",
      risk_officer.check_units(_m) is None)

quant_data.get_benchmark = lambda days=500: (_ for _ in ()).throw(RuntimeError("基准挂了"))
_m2 = _rp._measures_for("FAKE")
check("基准取不到时 σ/回撤仍算得出（单项失败不污染整组）",
      _m2["sigma_60"] is not None and _m2["maxdd"] is not None and _m2["beta"] is None,
      str({k: _m2[k] for k in ("sigma_60", "maxdd", "beta")}))

# ============================================================ 7. 否决也要落库
print("\n[7] 被否决的标的同样进 lineage")
_before = pc_ledger.binding_stats()["vetoed"]
_vetoed = risk_officer.assess_one(
    ticker="TESTVETO", direction="看多", conviction="中", evidence_gate="SUFFICIENT",
    invalidation_conditions=["x"], measures={"sigma_60": 1.8, "beta": 2.0}, regime="neutral")
check("先确认它确实被否决", _vetoed["veto"] is True)
pc_ledger.record(as_of_date="2026-08-26", portfolio_id="US_PAPER", cro=_vetoed,
                 size={"w_final": None, "reason": f"CRO 否决：{_vetoed['veto_reason']}"})
_after = pc_ledger.binding_stats()
check("veto 计数真的变了（旧版 continue 掉了 record，统计恒为 0）",
      _after["vetoed"] == _before + 1, f"{_before} → {_after['vetoed']}")
check("无仓位原因可统计",
      any("CRO 否决" in k for k in _after["no_position_reason"]),
      str(list(_after["no_position_reason"])[:3]))
# 用 AST 查结构，不 grep 文本：解释这个缺陷的注释里本身就有"continue"这个词，
# 文本匹配会永远失败。要断言的是**结构性质**——渲染循环里没有任何提前跳出。
import ast                                                          # noqa: E402
import textwrap                                                     # noqa: E402

_tree = ast.parse(textwrap.dedent(inspect.getsource(_rp.main)))
_render_loops = [n for n in ast.walk(_tree)
                 if isinstance(n, ast.For) and isinstance(n.iter, ast.Name)
                 and n.iter.id == "rows"]
check("找得到渲染循环", len(_render_loops) == 1)
check("渲染循环里没有 continue（每一行都会走到 record）",
      _render_loops and not [c for c in ast.walk(_render_loops[0])
                             if isinstance(c, ast.Continue)])
check("record 在循环体的顶层，不在任何 if 分支里（否决行也要落库）",
      _render_loops and any(
          isinstance(s, ast.Expr) and isinstance(s.value, ast.Call)
          and getattr(s.value.func, "attr", "") == "record"
          for s in _render_loops[0].body))

# ============================================================ 8. 台账重复
print("\n[8] 同票多账户 ≠ 台账重复")
check("summary 按账户拆开", hasattr(portfolio, "duplicates")
      and "accounts" in inspect.getsource(portfolio.summary))
check("重复检测按 (portfolio, account, code) 分组",
      "GROUP BY portfolio_id, account, code HAVING COUNT(*)>1"
      in inspect.getsource(portfolio.duplicates))
check("写明重复为什么危险（聚合口径成倍虚增）", "成倍虚增" in _psrc)

# ============================================================ 9. 档位换算
print("\n[9] 「闸门判了没材料」≠「闸门没跑过」")
from cio import material_gate                                        # noqa: E402

check("材料充分 → SUFFICIENT",
      material_gate.level_from_verdict("材料充分") == material_gate.SUFFICIENT)
check("材料偏薄 → THIN",
      material_gate.level_from_verdict("材料偏薄") == material_gate.THIN)
check("无实质材料 / 无材料 → INSUFFICIENT",
      material_gate.level_from_verdict("无实质材料") == material_gate.INSUFFICIENT
      and material_gate.level_from_verdict("无材料") == material_gate.INSUFFICIENT)
for _v, _lbl in (("", "字段为空"), (None, "字段为 None"), ("未判定", "写着未判定"),
                 ("材料很多", "认不出的字符串")):
    check(f"{_lbl} → UNRECORDED，**不折成 INSUFFICIENT**",
          material_gate.level_from_verdict(_v) == material_gate.UNRECORDED)
check("assess() 产出的 verdict 都能被换算回自己的 level",
      all(material_gate.level_from_verdict(v) == lv
          for v, lv in material_gate._VERDICT_LEVEL.items()))

_un = sizing.size_one(ticker="X", conviction="中",
                      evidence_gate=material_gate.UNRECORDED,
                      sigma_60=0.4, sigma_252=0.35, caps={"single_name": 0.05},
                      base_rb=0.015, regime="neutral")
_ins = sizing.size_one(ticker="X", conviction="中", evidence_gate="INSUFFICIENT",
                       sigma_60=0.4, sigma_252=0.35, caps={"single_name": 0.05},
                       base_rb=0.015, regime="neutral")
check("两档都不给仓位", _un["w_final"] is None and _ins["w_final"] is None)
check("但理由必须不同（否则『不知道』被报告成一次主动弃权）",
      _un["reason"] != _ins["reason"], _un["reason"])
check("UNRECORDED 的理由明说这不等于一部未产出观点",
      "不等于" in _un["reason"] and "重跑" in _un["reason"])
check("run_pc 用换算函数而不是就地手写 if/else",
      "level_from_verdict" in inspect.getsource(_rp.main)
      and "材料充分" not in inspect.getsource(_rp.main))

# ============================================================ 10. 影子账户
print("\n[10] 影子账户不进风险聚合")
check("识别 _shadow 后缀", portfolio.is_shadow("二部_shadow")
      and not portfolio.is_shadow("二部") and not portfolio.is_shadow(None))
_pos = [{"account": "二部", "code": "688012", "is_shadow": False},
        {"account": "二部_shadow", "code": "688012", "is_shadow": True}]
check("默认排除（analytics 早就排除了，两边口径必须一致）",
      "include_shadow" in inspect.signature(portfolio.open_positions).parameters
      and inspect.signature(portfolio.open_positions)
      .parameters["include_shadow"].default is False)
check("排除会写日志，不静默",
      "排除 %d 笔影子账户持仓" in inspect.getsource(portfolio.open_positions))
check("summary 分出实盘口径笔数", "n_real" in inspect.getsource(portfolio.summary))
check("报告里标出影子账户", "影子账户，纸面镜像" in inspect.getsource(_rp.main))

# ============================================================ 11. 对外推送
print("\n[11] 同一个频道上不能有两套仓位口径")
from cio import legacy_guard                                         # noqa: E402

os.environ.pop(legacy_guard.ENV, None)
check("退役 CRO 默认不推送", legacy_guard.legacy_push_allowed("自检") is False)
os.environ[legacy_guard.ENV] = "1"
check("显式打开才推送（决定要留在命令行上）",
      legacy_guard.legacy_push_allowed("自检") is True)
os.environ.pop(legacy_guard.ENV, None)
_rcro = (Path(__file__).resolve().parents[1] / "run_cro.py").read_text()
_rpilot = (Path(__file__).resolve().parents[1] / "run_pilot.py").read_text()
check("run_cro 的推送走闸门", "legacy_push_allowed" in _rcro)
check("run_pilot 的老 CRO 推送走闸门", "legacy_push_allowed" in _rpilot)
check("财务部盈亏表不受影响（它不是退役模块）",
      _rpilot.index("盈亏表 {st.as_of}") > _rpilot.index("legacy_push_allowed"))

_rua_path = Path(__file__).resolve().parents[1] / "run_unit_a.py"
_rua = _rua_path.read_text()
# 又一次：断言【结构】不断言注释文本。解释这条改动的注释里必然写着"目标仓位"，
# 文本匹配会永远失败——真正要断言的是"这个文件不再读取 target_position 这个字段"。
_rua_tree = ast.parse(_rua)
check("一部不再读取 target_position 字段（PC 是唯一给仓位的地方）",
      not [n for n in ast.walk(_rua_tree)
           if isinstance(n, ast.Attribute) and n.attr == "target_position"])
check("一部改推 Evidence Gate", "Evidence Gate" in _rua and "formal_vote" in _rua)

_tg = _rp._tg_summary(
    "2026-08-26", "US_PAPER", {"regime": "neutral", "note": "n"},
    [({"ticker": "A", "direction": "看多", "conviction": "中",
       "evidence_gate": "SUFFICIENT", "veto": False},
      {"w_final": 0.032, "sigma_effective": 0.35,
       "binding_position_constraint": ["risk_budget"]}),
     ({"ticker": "B", "direction": "看多", "conviction": "弱",
       "evidence_gate": "UNRECORDED", "veto": False},
      {"w_final": None, "reason": sizing._GATE_REASON["UNRECORDED"]})],
    {"scale_factor": 1.0, "weights": {"A": 0.032}})
check("Telegram 摘要不含 markdown 强调符（解析失败会丢排版）", "**" not in _tg, _tg[:60])
check("定仓的票带出绑定项", "绑定 risk_budget" in _tg)
check("没定仓的票带出原因", "Evidence Gate 未记录" in _tg)
check("摘要里有职责边界那句话", "两者都不判断论点对错" in _tg)
check("DRYRUN 不报告成『已推送』",
      "DRYRUN，只打印未真发" in inspect.getsource(_rp.main))

print("\n" + "=" * 60)
if FAIL:
    print(f"FAILED {len(FAIL)}: " + "; ".join(FAIL))
    raise SystemExit(1)
print("全部通过。")
