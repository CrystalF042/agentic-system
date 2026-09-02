"""证券二部《策略验证报告》v2 —— 把量尺做正确（零 LLM，纯统计）。

与每日选股报告分工（对齐 CEO §9）：
  · 每日 Unit B Selection：今天选了谁、为什么、数据有没有坏 —— 行动前的信息。
  · 本报告（周期性 / 模型版本变更时生成）：模型本身还值不值得相信 —— 证据。

v2 相对 v1 的修正（全部针对"结论会不会被高估"）：
  1) 横截面按【日期】对齐，不再用"从尾部倒数第 j 根"——各股最后交易日不同时，
     倒数索引会把不同日历日拼进同一个横截面。
  2) 重叠收益用 **Newey-West (HAC)** 校正 t 值，而不是粗略地除以 √(fwd/step)。
  3) 额外跑一遍 **非重叠**（step = fwd）稳健性检验，样本少但解释干净。
  4) **多重检验校正**（Holm + BH-FDR）：5 因子 × 多周期是一个检验族，
     10 次检验里出现一个 p≈0.02 属于常见，不校正就会把噪声当发现。
  5) 相关性解释修正：**正相关 = 冗余/重复计票**，不是抵消；
     只有"两个因子各自有实质 IC、且相关性与其 IC 符号相悖"才可能真的互相抵消。
  6) IC 稳定性（逐年）与 IC 衰减（多周期），以及闭合的样本漏斗。

纪律：只测量，不改模型、不调参。
"""
from __future__ import annotations

import math

from .unit_b import (_FACTORS, _LOOKBACK, _MIN_HISTORY, _factor_row, _rank_ic, _weights,
                     _winsor_z, composite_from_rows)
from .utils import get_logger

log = get_logger("cio.validation")

DEFAULT_HORIZONS = (1, 5, 10, 20, 60)


# ---------------- 统计工具 ----------------
def _norm_p(t: float) -> float:
    """双侧 p 值（正态近似）。"""
    return math.erfc(abs(t) / math.sqrt(2.0))


def _nw_tstat(x, lag: int) -> tuple:
    """Newey-West (HAC) 均值 t 值：对重叠/自相关做校正。

    重叠的前向收益会让相邻 IC 正自相关，朴素 t = IR·√n 系统性高估显著性。
    NW 方差 = γ0 + 2·Σ_{k=1..L} (1 − k/(L+1))·γk，L 取重叠阶数。
    返回 (mean, t_hac, se_hac, lag)。
    """
    import numpy as np
    a = np.asarray(x, float)
    n = len(a)
    if n < 3:
        return (float(a.mean()) if n else 0.0), 0.0, 0.0, lag
    mu = float(a.mean())
    e = a - mu
    var = float((e ** 2).sum()) / n                      # γ0
    L = max(0, min(int(lag), n - 2))
    for k in range(1, L + 1):
        gk = float((e[k:] * e[:-k]).sum()) / n           # γk
        var += 2.0 * (1.0 - k / (L + 1.0)) * gk
    var = max(var, 1e-18)                                # NW 方差理论上可能为负，截断
    se = math.sqrt(var / n)
    return mu, (mu / se if se else 0.0), se, L


def _stats(ics: list, fwd: int, step: int) -> "dict | None":
    """IC 序列 → 完整统计量（朴素 + HAC + p 值 + 置信区间）。"""
    import numpy as np
    if not ics:
        return None
    a = np.array(ics, float)
    n = len(a)
    mean = float(a.mean())
    sd = float(a.std(ddof=1)) if n > 1 else 0.0
    ir = mean / sd if sd else 0.0
    lag = max(int(math.ceil(fwd / max(step, 1))) - 1, 0)   # 重叠导致的 MA 阶数
    _mu, t_hac, se_hac, used_lag = _nw_tstat(a, lag)
    return {
        "mean_ic": mean, "ir": ir, "n": n,
        "t_naive": abs(ir) * math.sqrt(n),
        "t_hac": t_hac, "p_hac": _norm_p(t_hac), "lag": used_lag,
        # 置信区间用 HAC 标准误（IC 尺度），比 IR±2/√n 更诚实
        "ci_lo": mean - 1.96 * se_hac, "ci_hi": mean + 1.96 * se_hac,
        "overlapping": fwd > step,
    }


def holm_bh(pvals: dict, alpha: float = 0.05) -> dict:
    """多重检验校正：Holm（控制族错误率）+ BH（控制 FDR）。
    返回 {key: {"p":.., "holm_reject":bool, "bh_reject":bool, "holm_adj":..}}。"""
    items = sorted(((k, v) for k, v in pvals.items() if v is not None), key=lambda kv: kv[1])
    m = len(items)
    out: dict = {}
    # Holm：逐步下降，一旦不通过，其后全部不通过
    still = True
    for i, (k, p) in enumerate(items):
        adj = min(1.0, p * (m - i))
        rej = still and (p <= alpha / (m - i))
        if not rej:
            still = False
        out[k] = {"p": p, "holm_adj": adj, "holm_reject": rej}
    # BH-FDR：找最大的 i 使 p_(i) <= (i/m)·alpha，其及之前全部拒绝
    kmax = -1
    for i, (_k, p) in enumerate(items, start=1):
        if p <= (i / m) * alpha:
            kmax = i
    for i, (k, _p) in enumerate(items, start=1):
        out[k]["bh_reject"] = i <= kmax
    return out


# ---------------- 核心：按日期对齐的多周期诊断 ----------------
def _aligned_index(panels: dict, codes: list) -> tuple:
    """构造【按日期对齐】的索引：ref_dates（参考交易日序列）+ {code: {date: 行号}}。
    这样同一个横截面上，每只股票取的都是同一个日历日，而不是"各自倒数第 j 根"。"""
    import pandas as pd
    ref = max(codes, key=lambda c: len(panels[c]))
    ref_dates = list(pd.to_datetime(panels[ref]["date"]))
    pos: dict = {}
    for c in codes:
        d = pd.to_datetime(panels[c]["date"])
        pos[c] = {ts: i for i, ts in enumerate(d)}
    return ref_dates, pos


def factor_diagnostics(panels: dict, stocks: list, horizons=DEFAULT_HORIZONS,
                       step: int = 10, lookback: int = 0,
                       factors: "list | None" = None, sector_neutral: bool = False) -> dict:
    """一次遍历算出所有周期的逐因子 / 合成分 IC（因子值与周期无关，只算一次）。

    无未来函数：因子只用截至 as-of 日（含）的数据；未来收益是验证真值。
    与实盘同构：合成分走 composite_from_rows —— 与每日选股用的是同一个函数。
    """
    import numpy as np
    import pandas as pd
    from . import factors as FL
    lookback = lookback or _LOOKBACK
    req = list(factors or _FACTORS)
    derived = [f for f in req if FL.is_derived(f)]
    base = [f for f in FL.expand(req) if f in FL.LIBRARY] or list(_FACTORS)
    names = base + derived            # 报告里同时呈现基础因子与派生因子
    min_hist = max(FL.min_history(base), _MIN_HISTORY)
    funds: dict = {}
    if FL.needs_fundamentals(base):          # 只在真正用到基本面因子时才取 SEC 数据
        from . import fundamentals as FD
        funds = FD.load_universe(stocks)
    codes = [s.code for s in stocks if s.code in panels and not getattr(s, "identity_flag", "")]
    if not codes:
        return {}
    sec_of = {s.code: (getattr(s, "gics_sector", "") or "") for s in stocks}
    ref_dates, pos = _aligned_index(panels, codes)
    nref = len(ref_dates)
    hmax = max(horizons)

    per: dict = {h: {"factors": {f: [] for f in names}, "comp": [], "spread": {f: [] for f in names},
                     "dates": []} for h in horizons}
    corr_acc: dict = {}
    n_cs: list = []           # 每个横截面参与打分的只数（漏斗用）
    cov: dict = {}            # 逐因子覆盖度：每期有多少只标的能算出该因子
    j = hmax + step
    while j < min(lookback, nref - _MIN_HISTORY):
        t_idx = nref - 1 - j
        if t_idx < 0:
            break
        as_of = ref_dates[t_idx]
        vals_by_code: dict = {}          # {code: {factor: 原始值}}，逐因子可缺失
        fwd_by_code: dict = {h: {} for h in horizons}
        for c in codes:
            i = pos[c].get(as_of)
            if i is None or i + 1 < min_hist:
                continue
            df = panels[c]
            closes = df["close"].values
            if i + hmax >= len(closes):
                continue
            fr = FL.factor_row_partial(base, FL.Ctx(closes, df["volume"].values,
                                                    fund=funds.get(c), as_of=as_of), i)
            if not fr:
                continue
            px0 = closes[i]                    # 基期价格（勿用 base：与因子列表同名会互相覆盖）
            if not px0 or px0 <= 0:
                continue
            vals_by_code[c] = fr
            for h in horizons:
                fwd_by_code[h][c] = closes[i + h] / px0 - 1.0

        if len(vals_by_code) >= 5:
            # 逐因子：在【该因子自己可用的标的集合】上做横截面 z（可选行业中性化）
            zmap: dict = {}
            for f in base:
                cs_f = [c for c in vals_by_code if f in vals_by_code[c]]
                if len(cs_f) < 5:
                    continue
                z = _winsor_z([vals_by_code[c][f] for c in cs_f])
                if sector_neutral:
                    z = FL.sector_neutralize(z, [sec_of.get(c, "") for c in cs_f])
                zmap[f] = (cs_f, z)
                cov.setdefault(f, []).append(len(cs_f))
            for dname in derived:
                spec = FL.DERIVED[dname]
                need = spec["requires"]
                cs_d = [c for c in vals_by_code if all(k in vals_by_code[c] for k in need)]
                if len(cs_d) < 5:
                    continue
                zc = {k: _winsor_z([vals_by_code[c][k] for c in cs_d]) for k in need}
                if sector_neutral:      # 去均值是线性的，逐维中性化后再合成 == 合成后再中性化
                    secs_d = [sec_of.get(c, "") for c in cs_d]
                    zc = {k: FL.sector_neutralize(v, secs_d) for k, v in zc.items()}
                zd = [spec["combine"]({k: zc[k][j] for k in need}) for j in range(len(cs_d))]
                zmap[dname] = (cs_d, zd)
                cov.setdefault(dname, []).append(len(cs_d))

            # 合成分：只在【所有基础因子齐备】的标的上算（合成必须同批标的）
            cs_all = [c for c in vals_by_code if len(vals_by_code[c]) == len(base)]
            comp_z = None
            if len(cs_all) >= 5:
                rows_all = [vals_by_code[c] for c in cs_all]
                zc2 = {f: _winsor_z([r[f] for r in rows_all]) for f in base}
                if sector_neutral:
                    secs = [sec_of.get(c, "") for c in cs_all]
                    zc2 = {f: FL.sector_neutralize(v, secs) for f, v in zc2.items()}
                _w = _weights() if list(base) == list(_FACTORS) else {f: 1.0 for f in base}
                _sw = sum(_w.values()) or 1.0
                comp_z = (cs_all, [sum(_w[f] * zc2[f][k] for f in base) / _sw
                                   for k in range(len(cs_all))])
                cov.setdefault("__composite__", []).append(len(cs_all))
            n_cs.append(len(vals_by_code))

            for h in horizons:
                for f, (cs_f, z) in zmap.items():
                    ret = [fwd_by_code[h][c] for c in cs_f]
                    ic = _rank_ic(z, ret)
                    if ic is not None:
                        per[h]["factors"][f].append(ic)
                    order = np.argsort(z)
                    k = max(1, len(ret) // 5)
                    per[h]["spread"][f].append(
                        (float(np.mean([ret[m] for m in order[-k:]]))
                         - float(np.mean([ret[m] for m in order[:k]]))) * 100)
                    if len(order) >= 25:        # 每档至少 5 只才有意义
                        q = [float(np.mean([ret[m] for m in order[j * len(order) // 5:
                                                                  (j + 1) * len(order) // 5]]))
                             for j in range(5)]
                        per[h].setdefault("quint", {}).setdefault(f, []).append(q)
                if comp_z:
                    cs_all2, cz = comp_z
                    cic = _rank_ic(cz, [fwd_by_code[h][c] for c in cs_all2])
                    if cic is not None:
                        per[h]["comp"].append(cic)
                per[h]["dates"].append(as_of)
            # 相关性：在同时具备两个因子的标的上算
            for x in range(len(names)):
                for y in range(x + 1, len(names)):
                    f1, f2 = names[x], names[y]
                    both = [c for c in vals_by_code if f1 in vals_by_code[c] and f2 in vals_by_code[c]]
                    if len(both) >= 5:
                        r = _pearson([vals_by_code[c][f1] for c in both],
                                     [vals_by_code[c][f2] for c in both])
                        if r is not None:
                            corr_acc.setdefault((f1, f2), []).append(r)
        j += step

    out = {"step": step, "names": list(names), "sector_neutral": bool(sector_neutral), "horizons": {}, "corr": {k: sum(v) / len(v) for k, v in corr_acc.items()},
           "weights": _weights(), "n_cross_sections": len(n_cs),
           "coverage": {f: (sorted(v)[len(v)//2] if v else 0) for f, v in cov.items()},
           "cs_size_median": (sorted(n_cs)[len(n_cs) // 2] if n_cs else 0),
           "date_from": (str(min(per[hmax]["dates"]).date()) if per[hmax]["dates"] else ""),
           "date_to": (str(max(per[hmax]["dates"]).date()) if per[hmax]["dates"] else "")}
    for h in horizons:
        d = per[h]
        by_year: dict = {}
        # 逐年 IC（稳定性）：只对合成分与各因子算年度均值
        years = sorted({pd.Timestamp(x).year for x in d["dates"]})
        for y in years:
            idx = [i for i, x in enumerate(d["dates"]) if pd.Timestamp(x).year == y]
            by_year[y] = {"comp": (sum(d["comp"][i] for i in idx if i < len(d["comp"])) / len(idx)) if idx else None,
                          "n": len(idx)}
        # 五分位曲线（逐因子逐期平均）+ 单调性：分档序号与平均收益的秩相关，
        # 单调性接近 ±1 说明信号是渐进的而非只由极端档驱动（对 Top-Bottom 是有力补充）。
        quint, mono = {}, {}
        for f, arr in (d.get("quint") or {}).items():
            if not arr:
                continue
            m5 = [float(np.mean([a[j] for a in arr])) * 100 for j in range(5)]
            quint[f] = [round(x, 3) for x in m5]
            mono[f] = round(_rank_ic([1, 2, 3, 4, 5], m5) or 0.0, 2)
        out["horizons"][h] = {
            "quintiles": quint, "monotonicity": mono,
            "factors": {f: _stats(d["factors"][f], h, step) for f in names},
            "composite": _stats(d["comp"], h, step),
            "spread": {f: (sum(v) / len(v) if v else None) for f, v in d["spread"].items()},
            "by_year": by_year,
        }
    return out


def _pearson(a, b) -> "float | None":
    import numpy as np
    x, y = np.array(a, float), np.array(b, float)
    m = ~(np.isnan(x) | np.isnan(y))
    if m.sum() < 5:
        return None
    x, y = x[m] - x[m].mean(), y[m] - y[m].mean()
    d = math.sqrt(float((x ** 2).sum()) * float((y ** 2).sum()))
    return float((x * y).sum() / d) if d else None


# ---------------- 解释层 ----------------
def correlation_notes(corr: dict, horizon_stats: dict, en: bool = True,
                      ic_floor: float = 0.02) -> list:
    """相关性的【正确】解读：
      · 正相关 = 冗余（重复表达同一暴露、等于多投一票），不是抵消。
      · 负相关 + 两个因子各自都有实质 IC = 才可能真的互相抵消 alpha。
      · 负相关 + 至少一方 IC≈0 = 只是结构相反，抵消不了任何 alpha。
    """
    fac = (horizon_stats or {}).get("factors", {})

    def ic_of(f):
        v = fac.get(f)
        return abs(v["mean_ic"]) if v else 0.0

    out = []
    for (f1, f2), r in sorted(corr.items(), key=lambda kv: -abs(kv[1])):
        if abs(r) < 0.3:
            continue
        both_real = ic_of(f1) >= ic_floor and ic_of(f2) >= ic_floor
        if r > 0:
            note = ("redundancy — both express the same exposure; equal weighting double-counts it"
                    if en else "冗余——两者表达同一种暴露，等权等于重复计票")
        elif both_real:
            note = ("opposed AND both carry material IC — genuine alpha cancellation is possible"
                    if en else "结构相反且两者都有实质 IC——可能真的互相抵消 alpha")
        else:
            note = ("structurally opposed, but at least one has IC ~ 0 — nothing to cancel"
                    if en else "结构相反，但至少一方 IC≈0——没有可抵消的 alpha")
        out.append((f1, f2, r, note))
    return out


def verdict(diag: dict, mt: dict, horizon: int, en: bool = True) -> str:
    """按【HAC + 多重检验】给结论。只有两关都过，才算候选异象；仍不等于可部署因子。"""
    hs = (diag.get("horizons") or {}).get(horizon) or {}
    fac = hs.get("factors") or {}
    comp = hs.get("composite")
    survivors = [f for f in (diag.get("names") or _FACTORS)
                 if (mt.get(f"{f}@{horizon}") or {}).get("holm_reject")]
    comp_ok = (mt.get(f"composite@{horizon}") or {}).get("holm_reject")
    n = comp["n"] if comp else 0
    if not survivors and not comp_ok:
        raw = [f for f, v in fac.items() if v and v["p_hac"] < 0.05]
        extra = ""
        if raw:
            from .unit_b import _FACTOR_LABEL_EN
            nm = ", ".join((_FACTOR_LABEL_EN.get(f, f) if en else f) for f in raw)
            extra = (f" ({nm} is nominally p<0.05 before correction, but does not survive "
                     f"multiple testing — candidate at best, not a finding)"
                     if en else
                     f"（{nm} 校正前名义 p<0.05，但过不了多重检验——至多算候选，不构成发现）")
        return (f"No factor and no composite survives HAC + multiple-testing correction over {n} "
                f"cross-sections{extra}." if en else
                f"{n} 个横截面上，没有任何因子或合成分能通过 HAC + 多重检验校正{extra}。")
    from .unit_b import _FACTOR_LABEL_EN
    nm = ", ".join((_FACTOR_LABEL_EN.get(f, f) if en else f) for f in survivors) or "—"
    return (f"Survives HAC + Holm: {nm}{' + composite' if comp_ok else ''}. "
            f"Treat as CANDIDATE anomaly requiring out-of-sample confirmation — not a production factor."
            if en else
            f"通过 HAC + Holm 的：{nm}{'，以及合成分' if comp_ok else ''}。"
            f"按【候选异象】对待，需样本外确认——尚不构成可投产因子。")
