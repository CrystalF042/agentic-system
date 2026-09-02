#!/usr/bin/env python3
"""证券二部《策略验证报告》v2 入口 —— 先把量尺做正确，再谈因子。

只做测量，不改模型、不调参。

用法：
  CIO_MARKET=us python run_validation.py                  # 用已有缓存，较快
  CIO_MARKET=us CIO_UB_LIMIT=120 python run_validation.py # 小池子先试
  CIO_MARKET=cn python run_validation.py                  # A 股同样适用
"""
from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from cio import quant_data, validation                              # noqa: E402
from cio.config import TOPIC_DIR, market                             # noqa: E402
from cio.unit_b import _DAYS, _FACTOR_LABEL_EN, _FACTORS             # noqa: E402
from cio.utils import file_stamp, get_logger                         # noqa: E402

log = get_logger("cio.validation.run")

PRIMARY = (20, 5)          # 主结论周期
DECAY = (1, 5, 10, 20, 60)  # IC 衰减曲线


def _fmt(v, key, digits=3):
    return "—" if not v else f"{v[key]:+.{digits}f}"


def _md(diag, nonov, mt, meta, en) -> str:
    names = diag.get("names") or list(_FACTORS)
    lab = {f: (_FACTOR_LABEL_EN.get(f, f) if en else f) for f in names}
    L: list[str] = []
    if en:
        L.append(f"# Unit B — Strategy Validation Report (v2) — {meta['generated_at_utc']} UTC")
        L.append(f"\n> **Measurement only** — no model change, no parameter tuning.")
        L.append(f"> Universe: {meta['universe']} · window {meta['days']} trading days · "
                 f"data {meta['date_from']} → {meta['date_to']} (trade dates) · run_id `{meta['run_id']}`")
        L.append(f"> Funnel: {meta['funnel']}")
        L.append("\n> Reading rule: a factor counts only if it survives **HAC** (overlapping "
                 "forward returns) **and** **multiple-testing correction**, and holds up in the "
                 "**non-overlapping** check. Anything less is a candidate, not a finding.")
    else:
        L.append(f"# 证券二部《策略验证报告》v2 — {meta['generated_at_utc']} UTC")
        L.append("\n> **只做测量**——不改模型、不调参。")
        L.append(f"> 股票池：{meta['universe']} · 窗口 {meta['days']} 交易日 · "
                 f"数据 {meta['date_from']} → {meta['date_to']}（交易日） · run_id `{meta['run_id']}`")
        L.append(f"> 样本漏斗：{meta['funnel']}")
        L.append("\n> 判读规则：一个因子必须同时通过 **HAC**（重叠收益校正）与**多重检验校正**，"
                 "并在**非重叠**检验里方向稳定，才算数。达不到的一律只是候选。")

    for h in PRIMARY:
        hs = (diag.get("horizons") or {}).get(h) or {}
        comp = hs.get("composite")
        n = comp["n"] if comp else 0
        L.append((f"\n## Forward {h} trading days — {n} cross-sections"
                  if en else f"\n## 未来 {h} 交易日 — {n} 个横截面"))
        ov = "overlapping (HAC lag=%d)" % (comp["lag"] if comp else 0)
        L.append((f"\n*Sampling every {diag.get('step')} days; forward window {h} days → "
                  f"{'**' + ov + '**' if h > diag.get('step', 10) else 'non-overlapping'}.*"
                  if en else
                  f"\n*每 {diag.get('step')} 交易日取一个横截面，前向窗 {h} 日 → "
                  f"{'**重叠**（HAC 滞后阶=%d）' % (comp['lag'] if comp else 0) if h > diag.get('step', 10) else '不重叠'}。*"))
        if en:
            L.append("\n| Factor | Names | IC | IR | t (naive) | t (HAC) | p (HAC) | Holm | BH-FDR | Top-Bottom | Verdict |")
        else:
            L.append("\n| 因子 | 覆盖只数 | IC | IR | t(朴素) | t(HAC) | p(HAC) | Holm | BH | Top-Bottom | 判定 |")
        L.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for f in list(names) + ["composite"]:
            v = comp if f == "composite" else (hs.get("factors") or {}).get(f)
            key = f"{f}@{h}"
            m = mt.get(key) or {}
            nm = ("**Composite (live model)**" if en else "**合成分（实盘模型）**") if f == "composite" else lab.get(f, f)
            covd = (diag.get("coverage") or {}).get("__composite__" if f == "composite" else f, 0)
            if not v:
                L.append(f"| {nm} | {covd} | — | — | — | — | — | — | — | — | n/a |")
                continue
            sp = (hs.get("spread") or {}).get(f) if f != "composite" else None
            holm = ("PASS" if m.get("holm_reject") else "fail") if en else ("通过" if m.get("holm_reject") else "未过")
            bh = ("PASS" if m.get("bh_reject") else "fail") if en else ("通过" if m.get("bh_reject") else "未过")
            if m.get("holm_reject"):
                vd = "candidate" if en else "候选"
            elif v["p_hac"] < 0.05:
                vd = "nominal only" if en else "仅名义显著"
            else:
                vd = "no edge" if en else "无边际"
            L.append(f"| {nm} | {covd} | {v['mean_ic']:+.3f} | {v['ir']:+.2f} | {v['t_naive']:.2f} | "
                     f"{abs(v['t_hac']):.2f} | {v['p_hac']:.3f} | {holm} | {bh} | "
                     f"{(f'{sp:+.1f}%' if sp is not None else '—')} | {vd} |")
        qq = (hs.get("quintiles") or {}); mm = (hs.get("monotonicity") or {})
        if qq:
            L.append(("\n**Quintile curve** (Q1 low → Q5 high, mean forward return %) · monotonicity = rank corr(bucket, return):"
                      if en else "\n**五分位曲线**（Q1 低 → Q5 高，平均前向收益 %）· 单调性 = 分档序号与收益的秩相关："))
            for f, arr in qq.items():
                L.append(f"- {lab.get(f, f)}: " + " → ".join(f"{x:+.2f}" for x in arr)
                         + f"　(monotonicity {mm.get(f, 0):+.2f})")
        _cov = [c for c in (diag.get("coverage") or {}).values() if c]
        if _cov and min(_cov) < 30:
            L.append(("\n> ⚠ **Thin cross-sections**: the smallest factor covers only "
                      f"{min(_cov)} names per period. Rank IC on so few names is dominated by noise "
                      "and the covered subset is self-selected — do not read these as findings."
                      if en else
                      f"\n> ⚠ **横截面过薄**：覆盖最少的因子每期只有 {min(_cov)} 只标的。"
                      "样本这么小，秩相关基本是噪声，且能凑齐的标的本身有选择偏差——不可当作结论。"))
        L.append(("\n**Verdict:** " if en else "\n**结论：**") + validation.verdict(diag, mt, h, en))

        # 非重叠稳健性
        nv = (nonov.get(h) or {}).get("horizons", {}).get(h) if nonov.get(h) else None
        if nv:
            nc = nv.get("composite")
            L.append(("\n**Non-overlapping robustness** (step = forward window):"
                      if en else "\n**非重叠稳健性检验**（步长 = 前向窗）："))
            for f in list(names) + ["composite"]:
                vv = nc if f == "composite" else (nv.get("factors") or {}).get(f)
                if not vv:
                    continue
                nm = ("composite" if en else "合成分") if f == "composite" else lab.get(f, f)
                same = ""
                base = comp if f == "composite" else (hs.get("factors") or {}).get(f)
                if base:
                    same = (" · sign stable" if (base["mean_ic"] * vv["mean_ic"] > 0) else " · **sign flips**") if en \
                        else ("· 符号一致" if (base["mean_ic"] * vv["mean_ic"] > 0) else "· **符号翻转**")
                L.append(f"- {nm}: IC={vv['mean_ic']:+.3f}, t(HAC)={abs(vv['t_hac']):.2f}, "
                         f"p={vv['p_hac']:.3f}, n={vv['n']}{same}")

    # IC 衰减
    L.append(("\n## IC decay across horizons (composite)" if en else "\n## IC 衰减曲线（合成分）"))
    cells = []
    for h in DECAY:
        c = ((diag.get("horizons") or {}).get(h) or {}).get("composite")
        cells.append(f"{h}d: {c['mean_ic']:+.3f}" if c else f"{h}d: —")
    L.append("- " + "  ·  ".join(cells))

    # 逐年稳定性
    hs20 = (diag.get("horizons") or {}).get(20) or {}
    by_year = hs20.get("by_year") or {}
    if by_year:
        L.append(("\n## IC stability by year (composite, fwd20)" if en else "\n## 逐年 IC 稳定性（合成分，fwd20）"))
        L.append("- " + "  ·  ".join(
            f"{y}: {(v['comp'] if v['comp'] is not None else float('nan')):+.3f} (n={v['n']})"
            for y, v in sorted(by_year.items())))

    # 相关性（正确解读）
    notes = validation.correlation_notes(diag.get("corr") or {}, hs20, en)
    if notes:
        L.append(("\n## Factor correlation — interpreted" if en else "\n## 因子相关性（正确解读）"))
        for f1, f2, r, note in notes:
            L.append(f"- {lab.get(f1,f1)} vs {lab.get(f2,f2)}: {r:+.2f} — {note}")

    L.append(("\n---\n*Unit B validation v2: measurement only. Deterministic, zero LLM, free-source prices. "
              "Cross-sections are date-aligned; overlapping forward returns are HAC-corrected; the factor family "
              "is multiple-testing corrected. Research view, not an order.*"
              if en else
              "\n---\n*证券二部策略验证 v2：只做测量。确定性、零 LLM、免费数据源。横截面按日期对齐；"
              "重叠前向收益做 HAC 校正；因子族做多重检验校正。研究观点，非投资指令。*"))
    return "\n".join(L)


def main() -> int:
    en = market().get("lang", "zh") == "en"
    limit = int(os.environ.get("CIO_UB_LIMIT", "0"))
    try:
        stocks, src = quant_data.get_universe(limit=limit)
        status: dict = {}
        panels = quant_data.get_history(stocks, days=_DAYS, status=status)
        log.info("主诊断（多周期，一次遍历）…")
        fsel = os.environ.get("CIO_VAL_FACTORS", "")
        flist = [x for x in fsel.split(",") if x.strip()] or None
        sneut = os.environ.get("CIO_VAL_SECTOR_NEUTRAL", "0") == "1"
        if flist or sneut:
            log.info("因子集=%s｜行业中性=%s", flist or "生产集", sneut)
        diag = validation.factor_diagnostics(panels, stocks, horizons=DECAY, step=10,
                                             factors=flist, sector_neutral=sneut)
        nonov = {}
        for h in PRIMARY:                      # 非重叠稳健性：步长 = 前向窗
            log.info("非重叠稳健性检验 fwd=%d …", h)
            nonov[h] = validation.factor_diagnostics(panels, stocks, horizons=(h,), step=h,
                                                      factors=flist, sector_neutral=sneut)
    except Exception:
        log.error("验证异常:\n%s", traceback.format_exc())
        return 1

    # 多重检验：把"5 因子 × 主结论周期 + 合成分"作为一个检验族
    pv: dict = {}
    fnames = diag.get("names") or list(_FACTORS)     # main() 作用域内取因子名，勿依赖 _md 的局部变量
    for h in PRIMARY:
        hs = (diag.get("horizons") or {}).get(h) or {}
        for f in fnames:
            v = (hs.get("factors") or {}).get(f)
            if v:
                pv[f"{f}@{h}"] = v["p_hac"]
        if hs.get("composite"):
            pv[f"composite@{h}"] = hs["composite"]["p_hac"]
    mt = validation.holm_bh(pv)

    n_uni = len(stocks)
    n_priced = sum(1 for s in stocks if s.code in panels)
    n_ca = sum(1 for s in stocks if getattr(s, "identity_flag", ""))
    cs_med = diag.get("cs_size_median", 0)
    funnel = (f"{n_uni} constituents -> {n_priced} priced -> {n_uni - n_ca} identity-clean -> "
              f"median {cs_med} names per cross-section ({diag.get('n_cross_sections',0)} cross-sections)"
              if en else
              f"{n_uni} 成分 → {n_priced} 有价 → {n_uni - n_ca} 身份干净 → "
              f"每个横截面中位 {cs_med} 只（共 {diag.get('n_cross_sections',0)} 个横截面）")

    meta = {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "universe": src, "days": _DAYS, "funnel": funnel,
        "date_from": diag.get("date_from", ""), "date_to": diag.get("date_to", ""),
        "run_id": f"val-{market().get('name','')}-{file_stamp()}",
    }
    text = _md(diag, nonov, mt, meta, en)
    base = f"{'UnitB_Strategy_Validation' if en else '证券二部策略验证'}+{file_stamp()}"
    md_path = TOPIC_DIR / f"{base}.md"
    md_path.write_text(text, encoding="utf-8")
    print(text)
    print("\n->", md_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
