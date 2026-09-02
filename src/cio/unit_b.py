"""证券二部（Trading Unit B）—— 自研量化线（零 LLM，纯确定性因子模型）。

定位（项目书铁律）：与证券一部【方法独立】——一部是 LLM 多空辩论，二部是纯量化打分；
两条线互不读对方结论/信号，独立出结果，供 CEO 横向比对（一致=增强信心，分歧=风险信号）。
红线：零 LLM、零付费数据、只回测不实盘、只报客观事实与可复算的因子分。

v1（先C）：透明多因子打分（动量/反转/低波/趋势/量能）→ 横截面 z-score → 等权合成
          → 关注池行业加权 → 沪深300 内取 Top3；附 IC/分位收益自证模型有历史 edge。
          全程可解释：每只票为何入选，看得到每个因子的贡献。
后续（后B）：可在此骨架上叠 LightGBM 学习层 + alphalens/backtrader（Path B，待纸面验证有效再深化）。
"""
from __future__ import annotations

import math
import os

from . import db, quant_data
from .models import CollectionStatus, UnitBAdvice, UnitBPick
from .utils import file_stamp, get_logger, safe_filename, stamp_beijing, stamp_ny

log = get_logger("cio.unit_b")

# 因子定义（方向已对齐：值越大越"好"）。权重可用 .env 覆盖（CIO_UB_W_动量 等，一般不用改）。
_FACTORS = ["动量", "反转", "低波", "趋势", "量能"]
_FACTOR_DESC = {
    "动量": "12-1 月动量（剔除最近1月，学术标准动量）",
    "反转": "短期反转（近1月跌得多的反而占优，取负）",
    "低波": "低波动（近60日已实现波动率，取负）",
    "趋势": "中期趋势（现价相对120日均线）",
    "量能": "量能变化（近20日均量 / 近120日均量）",
}
_MIN_HISTORY = 250          # 计因子所需最少交易日（12月动量要回看约250日）
_TILT = float(os.environ.get("CIO_UB_TILT", "0.40"))   # 关注池加权（加到合成 z 上的额度）
# 【测量参数，非模型参数】拉长它只是把同一个冻结模型放到更长历史上量，不改变模型本身。
# 2 年窗只有 ~22 期，仅能检出 IR≥0.43 的信号（IR 0.2–0.3 这种像样的 alpha 根本测不出来）；
# 5 年窗 ~98 期，可检出下限降到 ~0.20。先定后测：跑出什么报什么。
_DAYS = int(os.environ.get("CIO_UB_DAYS", "1250"))       # 取数交易日（≈5年）
_LOOKBACK = int(os.environ.get("CIO_UB_LOOKBACK", "1000"))  # IC 滚动验证回看窗


def _weights() -> dict:
    return {f: float(os.environ.get(f"CIO_UB_W_{f}", "1")) for f in _FACTORS}


def _factor_row(close, vol, i: int):
    """用【截至第 i 行（含）】的数据算因子——严格无未来函数。返回 dict 或 None。"""
    if i + 1 < _MIN_HISTORY:
        return None
    import numpy as np
    c = close[:i + 1]
    v = vol[:i + 1]
    if c[-250] <= 0 or c[-21] <= 0:
        return None
    mom = c[-21] / c[-250] - 1.0                         # 12-1 月动量
    rev = -(c[-1] / c[-21] - 1.0)                        # 短期反转（取负）
    rets = np.diff(np.log(c[-61:]))                      # 近60日对数收益
    lowvol = -float(np.std(rets)) if len(rets) else 0.0  # 低波（取负）
    ma120 = float(np.mean(c[-120:]))
    trend = c[-1] / ma120 - 1.0 if ma120 > 0 else 0.0    # 相对120日均线
    a20, a120 = float(np.mean(v[-20:])), float(np.mean(v[-120:]))
    vt = float(np.log((a20 + 1e-9) / (a120 + 1e-9)))     # 量能变化
    return {"动量": mom, "反转": rev, "低波": lowvol, "趋势": trend, "量能": vt}


def _winsor_z(vals):
    """横截面 z-score + 缩尾到 ±3（抗离群）。返回等长 list。"""
    import numpy as np
    a = np.array(vals, dtype=float)
    mu, sd = np.nanmean(a), np.nanstd(a)
    if not sd or np.isnan(sd):
        return [0.0] * len(a)
    z = (a - mu) / sd
    return list(np.clip(z, -3, 3))


def _rank_ic(factor, fwd):
    """横截面 Spearman 秩相关（factor 与未来收益）。numpy 手算，避免 scipy 依赖。"""
    import numpy as np
    a, b = np.array(factor, float), np.array(fwd, float)
    m = ~(np.isnan(a) | np.isnan(b))
    if m.sum() < 5:
        return None
    ra = np.argsort(np.argsort(a[m]))
    rb = np.argsort(np.argsort(b[m]))
    ra = ra - ra.mean()
    rb = rb - rb.mean()
    denom = np.sqrt((ra**2).sum() * (rb**2).sum())
    return float((ra * rb).sum() / denom) if denom else None


def composite_from_rows(rows: list) -> list:
    """【实盘与自证共用的唯一合成口径】：各因子横截面 z（缩尾±3）→ 按权重加权平均。

    纪律：live 打分与 IC 自证必须走这同一个函数。否则会出现"自证测的不是所跑的模型"——
    因子原始量纲差异极大（动量 std≈0.34 vs 低波 std≈0.002，相差百倍），
    若直接平均原始值，合成分会被动量单因子支配，IC 就变成了动量的 IC。
    """
    if not rows:
        return []
    zmat = {f: _winsor_z([r[f] for r in rows]) for f in _FACTORS}
    w = _weights()
    sw = sum(w.values()) or 1.0
    return [sum(w[f] * zmat[f][k] for f in _FACTORS) / sw for k in range(len(rows))]


def compute_scores(panels: dict, stocks: list):
    """LIVE 选股：每只票在其【最后一根K线】上算因子 → 横截面 z → 加权合成 → 关注池加权。
    返回 (排序后的 [(stock, composite, {factor:z})], 参与只数)。"""
    import numpy as np
    rows, used = [], []
    # 数据漏斗：每一只被剔除的标的都要有原因，让 universe→scored 完全闭合（可审计）
    excl = {"missing_price": 0, "insufficient_history": 0, "invalid_factor": 0, "corporate_action": 0}
    excl_names: dict = {}
    for s in stocks:
        if getattr(s, "identity_flag", ""):        # 合并/更名：价量历史描述前身主体，不参与选股
            excl["corporate_action"] += 1
            excl_names.setdefault("corporate_action", []).append(f"{s.code}({s.identity_flag})")
            continue
        df = panels.get(s.code)
        if df is None:
            excl["missing_price"] += 1
            excl_names.setdefault("missing_price", []).append(s.code)
            continue
        if len(df) < _MIN_HISTORY:
            excl["insufficient_history"] += 1
            excl_names.setdefault("insufficient_history", []).append(s.code)
            continue
        fr = _factor_row(df["close"].values, df["volume"].values, len(df) - 1)
        if fr is None or any(np.isnan(v) for v in fr.values()):
            excl["invalid_factor"] += 1
            excl_names.setdefault("invalid_factor", []).append(s.code)
            continue
        rows.append(fr)
        used.append(s)
    compute_scores.last_exclusions = (excl, excl_names)     # 供 build_unit_b 组装漏斗
    if not rows:
        return [], 0
    # 各因子横截面 z（与 IC 自证共用 composite_from_rows，保证"测的就是所跑的模型"）
    zmat = {f: _winsor_z([r[f] for r in rows]) for f in _FACTORS}
    comps = composite_from_rows(rows)
    out = []
    for idx, s in enumerate(used):
        zbreak = {f: zmat[f][idx] for f in _FACTORS}
        raw = comps[idx]                                                  # 纯因子合成（IC 自证只看这个）
        tilt = _TILT if s.sector else 0.0                                 # 关注池加权（单独归因）
        out.append((s, raw, tilt, raw + tilt, zbreak))                    # (股票, raw, tilt, final, z拆解)
    out.sort(key=lambda t: t[3], reverse=True)                            # 按 final 排序
    return out, len(used)


def validate_ic(panels: dict, stocks: list, fwd: int = 20, step: int = 10, lookback: int = 0,
                tilt_map: dict | None = None):
    """模型自证：过去 lookback 日内，每 step 日取一个横截面，算 因子↔未来 fwd 日收益 的秩相关(IC)。
    对齐1个月验证视角：fwd=20≈1个月，fwd=5≈1周。返回 (平均IC, IR, Top-Bottom分位收益差%, 样本期数)。
    tilt_map（可选）：{code: tilt}——传入则算【含 tilt】的 IC，用于对比"纯因子 vs 加 tilt"的预测力归因。
    无未来函数：每期因子只用截至当日数据，未来收益是"验证真值"。"""
    import numpy as np
    lookback = lookback or _LOOKBACK
    # 同样剔除公司行为断点标的：其历史描述前身主体，放进 IC 会污染自证结果
    codes = [s.code for s in stocks if s.code in panels and not getattr(s, "identity_flag", "")]
    if not codes:
        return None
    # 用【最长】可用历史定回看窗（不是最短）：逐股护栏已在下面按 _MIN_HISTORY 过滤，
    # 若按全池最短(minlen)设闸，池里一只次新股就会让整池算不出 IC —— 这是 A 股全量跑
    # "历史样本不足"的真因，S&P 500 有 IPO 时同样会中招。
    maxlen = max(len(panels[c]) for c in codes)
    ics, spreads = [], []
    j = fwd + step
    while j < min(lookback, maxlen - _MIN_HISTORY):
        rows, ret, cs = [], [], []
        for c in codes:
            df = panels[c]
            i = len(df) - 1 - j                          # as-of（当期）
            if i + fwd >= len(df) or i + 1 < _MIN_HISTORY:
                continue
            fr = _factor_row(df["close"].values, df["volume"].values, i)
            if fr is None or any(np.isnan(v) for v in fr.values()):
                continue
            rows.append(fr)
            cs.append(c)
            ret.append(df["close"].values[i + fwd] / df["close"].values[i] - 1.0)
        # 先建完整横截面，再走【与 live 同一个】合成函数（横截面 z → 加权），口径必须一致
        fac = composite_from_rows(rows)
        if tilt_map:
            fac = [f + tilt_map.get(c, 0.0) for f, c in zip(fac, cs)]   # 归因：同尺度上叠 tilt
        if len(fac) >= 5:
            ic = _rank_ic(fac, ret)
            if ic is not None:
                ics.append(ic)
            order = np.argsort(fac)
            k = max(1, len(fac) // 5)
            bot = np.mean([ret[m] for m in order[:k]])
            top = np.mean([ret[m] for m in order[-k:]])
            spreads.append((top - bot) * 100)
        j += step
    if not ics:
        return None
    ics = np.array(ics)
    ir = float(ics.mean() / ics.std()) if ics.std() else 0.0
    return (float(ics.mean()), ir, float(np.mean(spreads)) if spreads else 0.0, len(ics))


# 因子说明（英文，US 渲染用）；因子 key 仍为中文（内部标识不变），渲染层做标签映射
_FACTOR_DESC_EN = {
    "动量": "12-1 month momentum (skip most recent month; academic standard)",
    "反转": "Short-term reversal (last-month losers favored; sign flipped)",
    "低波": "Low volatility (60-day realized vol; sign flipped)",
    "趋势": "Medium-term trend (price vs 120-day MA)",
    "量能": "Volume trend (20-day avg volume / 120-day avg volume)",
}
_FACTOR_LABEL_EN = {"动量": "Momentum", "反转": "Reversal", "低波": "LowVol", "趋势": "Trend", "量能": "Volume"}
_FACTOR_CONFIG_VERSION = "us-v1-frozen-cn-params"   # 冻结 A 股参数做 transfer test


def _git_commit() -> str:
    try:
        import subprocess
        from .config import BASE
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                           text=True, cwd=str(BASE), timeout=3)
        return r.stdout.strip()
    except Exception:
        return ""


def _ic_se(v) -> float:
    """IC 的标准误 = std/√n（std 由 mean/IR 反解）。用于判断差异是不是噪声。"""
    if not v:
        return 0.0
    ic, ir, _sp, n = v
    if not ir or not n:
        return 0.0
    return abs(ic / ir) / math.sqrt(n)


def _fmt_ic(v20, v5, en: bool) -> str:
    def one(v, tag, fwd, step=10):
        if not v:
            return f"{tag}: n/a" if en else f"{tag}: 样本不足"
        ic, ir, sp, n = v
        ov = max(1.0, fwd / step)                       # 前向窗重叠 → t 需打折
        t = abs(ir) * math.sqrt(n) / math.sqrt(ov)
        sig = ("significant" if t > 2 else "not significant") if en else ("显著" if t > 2 else "不显著")
        return (f"{tag} IC={ic:+.3f} | IR={ir:+.2f} | Top-Bottom={sp:+.1f}% | t={t:.2f} ({sig}) | {n} periods" if en
                else f"{tag} IC={ic:+.3f}｜IR={ir:+.2f}｜Top-Bottom={sp:+.1f}%｜t={t:.2f}（{sig}）｜{n}期")
    if not v20 and not v5:
        return ("Insufficient history for IC this run; re-assess after ~1 month of paper tracking."
                if en else "历史样本不足，本轮不出 IC；纸面跟踪约1个月后再评估。")
    sep = "  |  " if en else "　｜　"
    out = sep.join([one(v20, "fwd20≈1M", 20), one(v5, "fwd5≈1W", 5)])
    # 统计功效：这个样本量最小能检出多大的 IR —— 防止把"测不出"误读成"没有"
    n = (v20 or v5)[3]
    mdi = 2.0 / math.sqrt(max(n, 1))
    out += (f"  |  Power: with {n} periods only |IR| >= {mdi:.2f} is detectable (t>2); "
            f"a weaker-but-real edge would not show up here."
            if en else
            f"　｜　统计功效：{n} 期样本仅能检出 |IR| ≥ {mdi:.2f}；更弱但真实的边际在此测不出。")
    return out


def _tilt_attribution(panels: dict, stocks: list, raw_val, en: bool) -> str:
    """§Data Contract 归因：纯因子 IC vs 叠加 focus tilt 后的 IC（tilt 到底有没有加分）。"""
    tmap = {s.code: _TILT for s in stocks if s.sector}
    if not tmap:
        return "no focus names in universe" if en else "池内无关注池标的"
    tval = validate_ic(panels, stocks, fwd=20, tilt_map=tmap)
    if not raw_val or not tval:
        return "insufficient sample for tilt attribution" if en else "样本不足，暂不做 tilt 归因"
    raw_ic, tilt_ic = raw_val[0], tval[0]
    d = tilt_ic - raw_ic
    se = _ic_se(raw_val)
    # 只有当差异超过 IC 自身的标准误，才敢说"改善/变差"；否则如实标"无法与噪声区分"。
    if se and abs(d) < se:
        return (f"raw IC={raw_ic:+.3f} vs tilted IC={tilt_ic:+.3f} (delta={d:+.3f}; "
                f"within noise, |delta| < SE {se:.3f} — indistinguishable)" if en else
                f"纯因子 IC={raw_ic:+.3f} vs 含 tilt IC={tilt_ic:+.3f}（差={d:+.3f}；"
                f"在噪声内，|差| < 标准误 {se:.3f} —— 无法区分）")
    if en:
        v = "tilt improves predictive power" if d > 0 else "tilt reduces it"
        return f"raw IC={raw_ic:+.3f} vs tilted IC={tilt_ic:+.3f} (delta={d:+.3f}, SE={se:.3f}; {v})"
    v = "tilt 提升预测力" if d > 0 else "tilt 拉低预测力"
    return f"纯因子 IC={raw_ic:+.3f} vs 含 tilt IC={tilt_ic:+.3f}（差={d:+.3f}，标准误={se:.3f}，{v}）"


def build_unit_b(top_n: int = 3, universe_limit: int = 0) -> UnitBAdvice:
    import hashlib
    import json
    from .config import MARKET, market
    en = market().get("lang", "zh") == "en"
    status: dict = {}
    stocks, src = quant_data.get_universe(limit=universe_limit)
    panels = quant_data.get_history(stocks, days=_DAYS, status=status)
    ranked, used = compute_scores(panels, stocks)

    # model_weight：Top-N 的 final 分平移到正区间后归一 → 线内相对权重（%）。非公司层最终仓位。
    # 口径写进 manifest 与报告，让权重和因子分一样可复算（w_i = (final_i - min(final) + FLOOR) / Σ）。
    _WFLOOR = 0.1
    finals = [t[3] for t in ranked[:top_n]]
    shift = min(finals) if finals else 0.0
    pos = [max(0.0, f - shift) + _WFLOOR for f in finals]
    tot = sum(pos) or 1.0
    weighting_method = (f"model-weight-v1: w_i = (final_i - min(final) + {_WFLOOR}) / sum(...); "
                        f"top_n={top_n}; floor={_WFLOOR}; basis=final(incl. tilt)")

    picks: list[UnitBPick] = []
    for rank, (s, raw, tilt, final, zbreak) in enumerate(ranked[:top_n], 1):
        top_factors = sorted(zbreak.items(), key=lambda kv: kv[1], reverse=True)[:2]
        reason = ("; ".join(f"{_FACTOR_LABEL_EN.get(f, f)} z={z:+.2f}" for f, z in top_factors) if en
                  else "；".join(f"{f} z={z:+.2f}" for f, z in top_factors))
        picks.append(UnitBPick(
            rank=rank, code=s.code, name=s.name, yahoo=s.yahoo, sector=s.sector,
            composite=round(final, 3), factors={k: round(v, 3) for k, v in zbreak.items()},
            reason=reason, raw_quant_score=round(raw, 3), focus_tilt=round(tilt, 3),
            final_score=round(final, 3), model_weight=round(pos[rank - 1] / tot * 100, 1),
            gics_sector=s.gics_sector, focus_theme=list(s.focus_theme)))

    val20 = validate_ic(panels, stocks, fwd=20)
    val5 = validate_ic(panels, stocks, fwd=5)
    ic_txt = _fmt_ic(val20, val5, en)
    attribution = _tilt_attribution(panels, stocks, val20, en)

    # 数据漏斗（闭合）：universe → 有价 → 可打分，每一步剔除原因都写清楚
    excl, excl_names = getattr(compute_scores, "last_exclusions", ({}, {}))
    n_uni = len(stocks)
    n_price = sum(1 for s in stocks if s.code in panels)
    lab = ({"missing_price": "missing price", "insufficient_history": "insufficient history",
            "invalid_factor": "invalid factor", "corporate_action": "corporate action"} if en else
           {"missing_price": "缺价", "insufficient_history": "历史不足",
            "invalid_factor": "因子无效", "corporate_action": "公司行为断点"})
    parts = [f"{lab[k]} {v}" for k, v in excl.items() if v]
    ca = excl_names.get("corporate_action") or []
    ca_txt = ("; excluded: " + ", ".join(ca[:4]) if en else "；剔除：" + ", ".join(ca[:4])) if ca else ""
    if en:
        funnel = (f"{n_uni} universe -> {n_price} priced -> {used} scored"
                  + (f" | excluded: {', '.join(parts)}" if parts else "") + ca_txt)
    else:
        funnel = (f"{n_uni} 成分 → {n_price} 有价 → {used} 参与打分"
                  + (f"｜剔除：{'、'.join(parts)}" if parts else "") + ca_txt)

    meta = quant_data._LAST_UNIVERSE_META
    degraded = bool(meta.get("degraded"))
    tickers = sorted(s.code for s in stocks)
    uhash = hashlib.md5(",".join(tickers).encode()).hexdigest()[:12]
    from .config import market_date, market_now      # 业务凭证身份走市场时区，不走机器时钟
    run_id = f"ub-{MARKET}-{market_date().replace('-', '')}-{market_now().strftime('%H%M')}"
    params = {"factors": _FACTORS, "weights": _weights(), "tilt": _TILT, "min_history": _MIN_HISTORY,
              "days": _DAYS, "ic_fwd": [20, 5], "ic_step": 10, "ic_lookback": _LOOKBACK,
              "rebalance": "weekly", "signal_time": "T close", "fill_time": "T+1 open",
              "calendar": market().get("calendar", ""), "top_n": top_n,
              "weighting_method": weighting_method, "funnel": funnel,
              "composite": "cross-sectional z (winsor ±3) -> weighted mean; shared by live & IC"}
    manifest = {
        "run_id": run_id, "run_at": stamp_beijing(), "kind": "unit_b", "market": MARKET,
        "universe_src": src, "universe_snapshot": meta.get("snapshot", ""), "universe_hash": uhash,
        "price_source": str(status.get("quant_history", "")), "bench_source": market().get("bench_source", ""),
        "bench_basis": market().get("bench_basis", ""), "price_pit": True,
        "universe_pit": bool(meta.get("universe_pit")), "factor_config_version": _FACTOR_CONFIG_VERSION,
        "params_json": json.dumps(params, ensure_ascii=False), "git_commit": _git_commit(),
    }
    try:
        db.init_db()
        db.insert_manifest(manifest)
    except Exception as e:
        log.warning("manifest 写入失败：%s", e)

    deg_list = []
    if degraded:
        deg_list.append("universe=fallback-DEGRADED/TEST（非正式结果，仅冒烟）")
    cov = CollectionStatus(structured={"universe": src, **status}, fetched=len(panels), degraded=deg_list)

    bench_name = market().get("bench_name", "沪深300")
    if en:
        universe = f"S&P 500 ({src})"
        pool = ", ".join(sorted({t for s in stocks for t in s.focus_theme})) or "Pharma/AI/Tech/Semi"
        tilt_note = f"Focus themes ({pool}) up-weighted +{_TILT:.2f} on the composite (attribution kept separate)."
    else:
        universe = f"沪深300（{src}）"
        tilt_note = f"关注池（银行/创新药/硬科技）在沪深300内加权 +{_TILT:.2f}"

    return UnitBAdvice(
        dt_beijing=stamp_beijing(), dt_ny=stamp_ny(),
        universe=universe, universe_count=len(stocks), scored_count=used,
        picks=picks, factor_desc=(_FACTOR_DESC_EN if en else _FACTOR_DESC), ic_summary=ic_txt,
        tilt_note=tilt_note, status=cov,
        market=MARKET, benchmark=bench_name, bench_source=market().get("bench_source", ""),
        bench_basis=market().get("bench_basis", ""), universe_src=src,
        universe_snapshot=meta.get("snapshot", ""), price_pit=True,
        universe_pit=bool(meta.get("universe_pit")), attribution=attribution,
        run_id=run_id, manifest=manifest, funnel=funnel, weighting_method=weighting_method,
    )


def archive_and_render(r: UnitBAdvice) -> tuple[str, str]:
    from .config import TOPIC_DIR, market
    from .render import render_unit_b_md, render_unit_b_pdf
    en = market().get("lang", "zh") == "en"
    stamp = file_stamp()
    label = "UnitB_Quant_Selection" if en else "证券二部量化选股"
    title = "Unit B — Quantitative Stock Selection" if en else "《证券二部 量化选股建议》"
    base = f"{safe_filename(label)}+{stamp}"
    md_path = TOPIC_DIR / f"{base}.md"
    pdf_path = TOPIC_DIR / f"{base}.pdf"
    md_path.write_text(render_unit_b_md(r), encoding="utf-8")
    try:
        render_unit_b_pdf(r, str(pdf_path))
    except Exception as e:
        log.error("二部建议 PDF 渲染失败: %s", e)
        pdf_path = None
    db.init_db()
    db.insert_brief("unit_b", title, str(md_path), str(pdf_path or ""))
    return str(md_path), str(pdf_path or "")
