"""证券二部 Admission Gate —— 因子准入闸（确定性、零 LLM）。

流程（先定后测，顺序不可颠倒）：
    登记假设与通过标准  →  development 检验（已烧毁窗口，可重复）
        →  稳健性（HAC + 非重叠 + 多重检验）
        →  **在纯净窗口上一次性最终确认**  →  PASS / FAIL  →  写台账

硬约束（由代码强制，不靠自律）：
  · 未登记不得检验；登记后假设与标准不可修改。
  · 纯净窗口对每个研究**只能花一次**；已消耗则拒绝重跑（防止反复试到通过为止）。
  · 最终确认必须在 development 通过之后；不允许先看 holdout 再回头改模型。

通过标准（登记时写死，事后不可改）：
  development：HAC p < 0.05，且在其检验族内通过 Holm，且非重叠检验符号一致
  confirmation：纯净窗口上符号与 development 一致，且 HAC p < 0.05
                （单一预注册假设，无多重性问题）
"""
from __future__ import annotations

from . import ledger, validation
from .utils import get_logger

log = get_logger("cio.gate")

DEFAULT_CRITERIA = {
    "dev_p_hac_max": 0.05,
    "dev_require_holm": True,
    "dev_require_sign_stable": True,
    "confirm_p_hac_max": 0.05,
    "confirm_require_same_sign": True,
    "step": 10,
    "sector_neutral": False,     # 登记时可设 True，则该研究全程在行业中性化后的分数上检验
    "require_sign": "",          # "negative"/"positive"：预注册的方向，结果反向即判 FAIL
}


def slice_panels(panels: dict, date_from: str = "", date_to: str = "") -> dict:
    """按日期切片行情面板。用于把同一份缓存切成 development / holdout 两段。"""
    import pandas as pd
    out = {}
    lo = pd.Timestamp(date_from) if date_from else None
    hi = pd.Timestamp(date_to) if date_to else None
    for c, df in panels.items():
        d = df.copy()
        d["date"] = pd.to_datetime(d["date"])
        if lo is not None:
            d = d[d["date"] >= lo]
        if hi is not None:
            d = d[d["date"] <= hi]
        if len(d) >= 60:
            out[c] = d.reset_index(drop=True)
    return out


def _extract(diag: dict, factor: str, horizon: int) -> "dict | None":
    hs = (diag.get("horizons") or {}).get(horizon) or {}
    if factor == "composite":
        return hs.get("composite")
    return (hs.get("factors") or {}).get(factor)


def run_development(study_id: str, panels: dict, stocks: list) -> dict:
    """在【已烧毁】的 development 窗口上检验。可重复跑——这段数据本来就已经看过了。"""
    s = ledger.get(study_id)
    if not s:
        raise ValueError(f"{study_id} 未注册：必须先登记假设与通过标准（先定后测）")
    crit = {**DEFAULT_CRITERIA, **(s.get("criteria") or {})}
    factors, horizons = s["factors"], s["horizons"]
    w = ledger.window("development")
    dev = slice_panels(panels, w.get("from", ""), w.get("to", ""))
    if not dev:
        raise ValueError("development 窗口内无可用数据")

    sneut = bool(crit.get("sector_neutral"))
    diag = validation.factor_diagnostics(dev, stocks, horizons=tuple(horizons), step=crit["step"],
                                         factors=factors, sector_neutral=sneut)
    nonov = {h: validation.factor_diagnostics(dev, stocks, horizons=(h,), step=h,
                                              factors=factors, sector_neutral=sneut)
             for h in horizons}

    # 多重检验族 = 本研究登记的全部 (因子 × 周期)
    pv = {}
    for f in factors:
        for h in horizons:
            v = _extract(diag, f, h)
            if v:
                pv[f"{f}@{h}"] = v["p_hac"]
    mt = validation.holm_bh(pv)

    tests, passed_any = [], False
    for f in factors:
        for h in horizons:
            v = _extract(diag, f, h)
            if not v:
                continue
            nv = _extract(nonov[h], f, h)
            sign_stable = bool(nv and v["mean_ic"] * nv["mean_ic"] > 0)
            holm = bool((mt.get(f"{f}@{h}") or {}).get("holm_reject"))
            want = crit.get("require_sign", "")
            dir_ok = (v["mean_ic"] < 0 if want == "negative"
                      else (v["mean_ic"] > 0 if want == "positive" else True))
            ok = (v["p_hac"] < crit["dev_p_hac_max"]
                  and (holm or not crit["dev_require_holm"])
                  and (sign_stable or not crit["dev_require_sign_stable"])
                  and dir_ok)
            passed_any = passed_any or ok
            tests.append({"factor": f, "horizon": h, "ic": round(v["mean_ic"], 4),
                          "ir": round(v["ir"], 3), "t_hac": round(abs(v["t_hac"]), 2),
                          "p_hac": round(v["p_hac"], 4), "holm": holm,
                          "nonoverlap_ic": (round(nv["mean_ic"], 4) if nv else None),
                          "sign_stable": sign_stable, "n": v["n"], "passed": ok})
    res = {"window": f"{w.get('from','')}→{w.get('to','')}", "tests": tests,
           "passed": passed_any,
           "note": "development 仅为筛选：通过者才有资格动用纯净窗口"}
    ledger.record_development(study_id, res)
    return res


def run_confirmation(study_id: str, panels: dict, stocks: list, force: bool = False) -> dict:
    """在【纯净】窗口上做一次性最终确认。已消耗则拒绝——这是本闸最重要的一条。"""
    s = ledger.get(study_id)
    if not s:
        raise ValueError(f"{study_id} 未注册")
    dev = s.get("development")
    if not dev:
        raise ValueError(f"{study_id} 尚未做 development 检验：不允许先看 holdout 再回头改模型")
    if not dev.get("passed") and not force:
        raise ValueError(f"{study_id} 未通过 development：不得动用纯净窗口"
                         "（纯净数据是稀缺资源，只留给有资格的假设）")
    if ledger.holdout_consumed_by(study_id):
        raise ValueError(f"{study_id} 已经消耗过纯净窗口，拒绝重跑。"
                         "反复在同一段 holdout 上试到通过为止，等于把它变成第二个训练集。")

    crit = {**DEFAULT_CRITERIA, **(s.get("criteria") or {})}
    w = ledger.window("holdout")
    hold = slice_panels(panels, w.get("from", ""), w.get("to", ""))
    if not hold:
        raise ValueError("纯净窗口内无可用数据（本地缓存历史可能不够长）")

    # 只确认 development 里通过的那些 (因子 × 周期)——单一预注册假设，不做多重性惩罚
    cand = [(t["factor"], t["horizon"], t["ic"]) for t in dev["tests"] if t["passed"]]
    if not cand and force:
        cand = [(t["factor"], t["horizon"], t["ic"]) for t in dev["tests"]]
    horizons = sorted({h for _f, h, _ic in cand})
    diag = validation.factor_diagnostics(hold, stocks, horizons=tuple(horizons), step=crit["step"],
                                         factors=[f for f, _h, _ic in cand],
                                         sector_neutral=bool(crit.get("sector_neutral")))

    tests, all_ok = [], bool(cand)
    for f, h, dev_ic in cand:
        v = _extract(diag, f, h)
        if not v:
            tests.append({"factor": f, "horizon": h, "passed": False, "reason": "no data"})
            all_ok = False
            continue
        same_sign = (dev_ic * v["mean_ic"]) > 0
        ok = (v["p_hac"] < crit["confirm_p_hac_max"]
              and (same_sign or not crit["confirm_require_same_sign"]))
        all_ok = all_ok and ok
        tests.append({"factor": f, "horizon": h, "dev_ic": dev_ic,
                      "holdout_ic": round(v["mean_ic"], 4), "t_hac": round(abs(v["t_hac"]), 2),
                      "p_hac": round(v["p_hac"], 4), "same_sign": same_sign,
                      "n": v["n"], "passed": ok})
    res = {"window": f"{w.get('from','') or '(起始)'}→{w.get('to','')}",
           "tests": tests, "passed": all_ok}
    ledger.record_confirmation(study_id, res)     # 无论通过与否，窗口都记为已消耗
    return res


def collect_candidates() -> list:
    """收集所有【development 已通过】、且尚未确认过的候选，供打包成一个确认批次。"""
    d = ledger.load()
    out = []
    for sid, s in (d.get("studies") or {}).items():
        if s.get("confirmation"):
            continue                                  # 已确认过的不再入批
        dev = s.get("development") or {}
        for t in dev.get("tests", []):
            if t.get("passed"):
                out.append({"study": sid, "factor": t["factor"], "horizon": t["horizon"],
                            "dev_ic": t.get("ic")})
    return out


def run_batch_confirmation(batch_id: str, panels: dict, stocks: list) -> dict:
    """在纯净窗口上【一次性】确认一整批候选，批内做 Holm 多重检验校正。

    为什么必须成批：若每个候选各自去 confirm 一次，等于在 holdout 上做了 N 次独立检验，
    多重检验从后门回来，而且发生在最不该发生的地方。
    重复使用惩罚：纯净窗口每被使用一次，alpha 再除以使用次数（Bonferroni over uses），
    第二批就要比第一批严一倍——用得越多越难通过，这是应该的。
    """
    b = ledger.get_batch(batch_id)
    if not b:
        raise ValueError(f"批次 {batch_id} 未注册：必须先登记候选清单（先定后测）")
    if b.get("confirmation"):
        raise ValueError(f"批次 {batch_id} 已确认过，拒绝重跑。"
                         "在同一批上反复试到通过为止，等于把 holdout 变成第二个训练集。")
    cands = b["candidates"]
    if not cands:
        raise ValueError("批次内无候选")
    crit = {**DEFAULT_CRITERIA, **(b.get("criteria") or {})}
    use_idx = int(b.get("holdout_use_index", 1))
    alpha = crit["confirm_p_hac_max"] / max(use_idx, 1)     # 重复使用惩罚

    w = ledger.window("holdout")
    hold = slice_panels(panels, w.get("from", ""), w.get("to", ""))
    if not hold:
        raise ValueError("纯净窗口内无可用数据（本地缓存历史可能不够长）")

    horizons = sorted({int(c["horizon"]) for c in cands})
    factors = sorted({c["factor"] for c in cands})
    diag = validation.factor_diagnostics(hold, stocks, horizons=tuple(horizons),
                                         step=crit["step"], factors=factors,
                                         sector_neutral=bool(crit.get("sector_neutral")))
    # 批内多重检验：所有候选放在同一个族里做 Holm
    pv = {}
    for c in cands:
        v = _extract(diag, c["factor"], int(c["horizon"]))
        if v:
            pv[f"{c['study']}|{c['factor']}@{c['horizon']}"] = v["p_hac"]
    mt = validation.holm_bh(pv, alpha=alpha)

    tests = []
    for c in cands:
        key = f"{c['study']}|{c['factor']}@{c['horizon']}"
        v = _extract(diag, c["factor"], int(c["horizon"]))
        if not v:
            tests.append({**c, "passed": False, "reason": "no data in holdout"})
            continue
        same_sign = (float(c.get("dev_ic") or 0) * v["mean_ic"]) > 0
        holm = bool((mt.get(key) or {}).get("holm_reject"))
        want = crit.get("require_sign", "")
        dir_ok = (v["mean_ic"] < 0 if want == "negative"
                  else (v["mean_ic"] > 0 if want == "positive" else True))
        ok = holm and dir_ok and (same_sign or not crit["confirm_require_same_sign"])
        tests.append({**c, "holdout_ic": round(v["mean_ic"], 4),
                      "t_hac": round(abs(v["t_hac"]), 2), "p_hac": round(v["p_hac"], 4),
                      "holm": holm, "same_sign": same_sign, "n": v["n"], "passed": ok})
    res = {"window": f"{w.get('from','') or '(起始)'}→{w.get('to','')}",
           "holdout_use_index": use_idx, "alpha_used": round(alpha, 5),
           "batch_size": len(cands), "tests": tests,
           "passed": any(t.get("passed") for t in tests)}
    ledger.record_batch_confirmation(batch_id, res)
    return res


def render_batch(batch_id: str) -> str:
    b = ledger.get_batch(batch_id) or {}
    r = b.get("confirmation") or {}
    L = [f"# Admission Gate — 批次确认 {batch_id}", "",
         f"- 状态：**{b.get('status','?')}**",
         f"- 登记于：{b.get('registered_at','')}　候选数：{len(b.get('candidates') or [])}",
         f"- 纯净窗口第 **{b.get('holdout_use_index','?')}** 次使用"
         + (f"　→ alpha 收紧为 {r.get('alpha_used')}" if r else "")]
    if not r:
        L.append("\n（尚未执行确认）")
        return "\n".join(L)
    L.append(f"- 窗口：{r.get('window')}　批次结果：**{'PASS' if r.get('passed') else 'FAIL'}**")
    L.append("\n| 研究 | 因子 | 周期 | dev IC | holdout IC | t(HAC) | p(HAC) | 批内Holm | 符号 | n | 判定 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for t in r.get("tests", []):
        L.append(f"| {t.get('study')} | {t.get('factor')} | {t.get('horizon')} | "
                 f"{t.get('dev_ic','—')} | {t.get('holdout_ic','—')} | {t.get('t_hac','—')} | "
                 f"{t.get('p_hac','—')} | {'✓' if t.get('holm') else '—'} | "
                 f"{'一致' if t.get('same_sign') else '不一致'} | {t.get('n','—')} | "
                 f"{'**PASS**' if t.get('passed') else 'fail'} |")
    L.append(f"\n通过者进入 Production Factor Set：{ledger.production_factors() or '（无）'}")
    L.append("\n---\n*批次确认：纯净窗口一次性花掉，批内 Holm 校正，重复使用逐次收紧 alpha。"
             "确定性、零 LLM。研究观点，非投资指令。*")
    return "\n".join(L)


def render(study_id: str, en: bool = False) -> str:
    s = ledger.get(study_id) or {}
    L = [f"# Admission Gate — {study_id}", "",
         f"- 状态：**{s.get('status','?')}**",
         f"- 假设：{s.get('hypothesis','')}",
         f"- 登记于：{s.get('registered_at','')}",
         f"- 预注册标准：{s.get('criteria')}"]
    for phase, title in (("development", "Development（已烧毁窗口，可重复）"),
                         ("confirmation", "Confirmation（纯净窗口，一次性）")):
        r = s.get(phase)
        L.append(f"\n## {title}")
        if not r:
            L.append("- 未执行")
            continue
        L.append(f"- 窗口：{r.get('window')}　结果：**{'PASS' if r.get('passed') else 'FAIL'}**")
        L.append("\n| 因子 | 周期 | IC | t(HAC) | p(HAC) | Holm | 非重叠符号 | n | 判定 |")
        L.append("|---|---|---|---|---|---|---|---|---|")
        for t in r.get("tests", []):
            ic = t.get("ic", t.get("holdout_ic"))
            sign = t.get("sign_stable", t.get("same_sign"))
            L.append(f"| {t.get('factor')} | {t.get('horizon')} | "
                     f"{(f'{ic:+.4f}' if ic is not None else '—')} | {t.get('t_hac','—')} | "
                     f"{t.get('p_hac','—')} | {'✓' if t.get('holm') else '—'} | "
                     f"{'一致' if sign else '不一致'} | {t.get('n','—')} | "
                     f"{'PASS' if t.get('passed') else 'fail'} |")
    L.append("\n---\n*Admission Gate：确定性、零 LLM。纯净窗口每个研究只能消耗一次，"
             "由台账强制。研究观点，非投资指令。*")
    return "\n".join(L)
