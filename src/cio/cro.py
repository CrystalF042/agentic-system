"""风控部（CRO）—— 独立风险闸门（零 LLM，纯确定性）。

定位（项目书铁律）：CIO/CRO 是"两个点"。CRO 对一部、二部的每日选股做独立风险把关——
出①投资倾向、②逐只五维风险评级、③一票否决，把关后交 CEO 终批（本期：CRO 先筛 → CEO 终批）。
红线：允许方向/风险判断（CRO 职权，区别于 CIO）；但仅研究观点，须 CEO 决断；只回测不实盘。
独立性：不读两线的推理过程，只对既有选股用行情真值独立评估。
"""
from __future__ import annotations

import os

from . import db, quant_data
from .models import CollectionStatus, CRORating, DailyPick, RiskItem
from .quant_data import Stock
from .utils import get_logger, stamp_beijing, stamp_ny

log = get_logger("cio.cro")

# 否决硬线（.env 可覆盖）——数值口径全部客观可复算
VOL_MAX = float(os.environ.get("CIO_CRO_VOL_MAX", "0.60"))    # 年化波动否决线
DD_MAX = float(os.environ.get("CIO_CRO_DD_MAX", "0.50"))      # 最大回撤否决线（绝对值）
LIQ_MIN = float(os.environ.get("CIO_CRO_LIQ_MIN", "5e7"))    # 日均成交额下限（元）
SECTOR_MAX = int(os.environ.get("CIO_CRO_SECTOR_MAX", "2"))  # 同板块入选上限
_MIN_HIST = 130                                              # 算 Beta/回撤所需最少交易日


def _metrics(df, bench_close) -> dict | None:
    """从日线面板算五维风险指标 + 跌停/停牌判据。严格只用已发生数据。"""
    import numpy as np
    c = df["close"].values.astype(float)
    v = df["volume"].values.astype(float)
    if len(c) < _MIN_HIST or c[-1] <= 0:
        return None
    rets = np.diff(np.log(c[-61:]))
    vol = float(np.std(rets) * np.sqrt(252)) if len(rets) else 0.0       # 年化波动
    w = c[-250:] if len(c) >= 250 else c
    peak = np.maximum.accumulate(w)
    max_dd = float(((w - peak) / peak).min())                            # 最大回撤（负）
    liq = float(np.mean(c[-20:] * v[-20:]))                              # 20日日均成交额
    ma120 = float(np.mean(c[-120:]))
    trend = c[-1] / ma120 - 1.0 if ma120 > 0 else 0.0                    # 相对120日线
    beta = 1.0
    if bench_close is not None and len(bench_close) >= 30:
        sr = np.diff(np.log(c[-121:]))
        br = np.diff(np.log(np.asarray(bench_close, float)[-121:]))
        n = min(len(sr), len(br))
        if n >= 20 and np.var(br[-n:]) > 0:
            beta = float(np.cov(sr[-n:], br[-n:])[0, 1] / np.var(br[-n:]))
    last_move = c[-1] / c[-2] - 1.0 if len(c) >= 2 and c[-2] > 0 else 0.0
    halt = bool(v[-1] == 0)                                              # 末日零成交≈停牌
    return dict(vol=vol, max_dd=max_dd, liquidity=liq, beta=beta, trend=trend,
                last_move=last_move, halt=halt)


def _score_and_veto(m: dict) -> tuple[float, str, bool, str]:
    """五维→风险分(0-1)→低/中/高；分两档：硬否决 vs 高风险警示。
    硬否决只留【不可交易/已破位】(停牌/跌停/流动性枯竭)——真拦死；
    波动/回撤高只标【⚠高风险】、评级打高，但不否决，交 CEO 慎重终批（人在环路）。"""
    # 各维风险贡献（0≈无风险，1≈触线）
    vol_r = min(m["vol"] / VOL_MAX, 1.5)
    dd_r = min(abs(m["max_dd"]) / DD_MAX, 1.5)
    liq_r = 1.0 if m["liquidity"] < LIQ_MIN else max(0.0, min(1.0, (3 * LIQ_MIN - m["liquidity"]) / (3 * LIQ_MIN)))
    beta_r = max(0.0, min(1.2, (m["beta"] - 0.8) / 0.8))
    trend_r = 0.9 if m["trend"] < -0.05 else (0.5 if m["trend"] < 0 else 0.2)
    score = min(1.0, 0.30 * vol_r + 0.28 * dd_r + 0.18 * liq_r + 0.12 * beta_r + 0.12 * trend_r)
    rating = "高" if score >= 0.70 else ("中" if score >= 0.40 else "低")

    # 第一档·硬否决：仅限"不可交易/已破位"——这些是死线，直接拦
    if m["halt"]:
        return score, "高", True, "停牌（末日零成交）"
    if m["last_move"] <= -0.098:
        return score, "高", True, f"当日跌停（{m['last_move']*100:.1f}%）"
    if m["liquidity"] < LIQ_MIN:
        return score, "高", True, f"流动性枯竭：日均成交额 {m['liquidity']/1e8:.2f}亿 < 下限 {LIQ_MIN/1e8:.2f}亿"
    # 第二档·高风险警示：波动/回撤超线只警示、打高评级，不否决（交 CEO 慎重决断）
    warn = []
    if m["vol"] > VOL_MAX:
        warn.append(f"高波动 {m['vol']*100:.0f}%")
    if abs(m["max_dd"]) > DD_MAX:
        warn.append(f"深回撤 {m['max_dd']*100:.0f}%")
    if warn:
        return score, "高", False, "⚠高风险（须CEO慎重）：" + "、".join(warn)
    top = max([("波动", vol_r), ("回撤", dd_r), ("流动性", liq_r), ("Beta", beta_r), ("趋势", trend_r)],
              key=lambda x: x[1])
    return score, rating, False, f"主要风险维度：{top[0]}"


def _consistency_note(overlap: int, n_a: int, n_b: int) -> tuple[str, bool]:
    """两线一致性。返回 (说明, 是否构成有效一致性信号)。

    **"没有输入"必须与"存在分歧"严格区分。**
    二部转入 ABSTAIN（无已验证因子）之后，两线可能同时为空。此时若沿用
    "两线无重叠 → 分歧偏大 → 风险信号"，就是从一个空集合里凭空造出一个风险信号，
    并据此下调仓位——这是无中生有，比不报更糟。
    只有当【两条线都真的给了选股】时，重叠与否才携带信息。
    """
    if n_a == 0 and n_b == 0:
        # 只陈述观察到的事实。为什么两线都没输出（弃权？取数失败？辩论异常？）
        # 本函数并不知道，也不该猜——一个专门用来防止无中生有的函数，
        # 自己更不能编一个原因出来。
        return "两线本日均无选股输入 —— 无一致性信息，不构成信号", False
    if n_a == 0 or n_b == 0:
        only = "一部" if n_b == 0 else "二部"
        return f"仅{only}提供选股，另一线无输出 —— 无法判断一致性，不构成信号", False
    if overlap >= 1:
        return f"两线重叠 {overlap} 只（信心增强）", True
    return "两线各有选股但无重叠、分歧偏大（风险信号，建议谨慎）", True


def _leaning(bench_close, overlap: int, n_pairs: int,
             n_a: int = 0, n_b: int = 0) -> tuple[str, str, str, str]:
    """投资倾向：大盘趋势/波动 + 两线一致性 → 整体看多/中性/看空 + 建议总仓位。"""
    import numpy as np
    from .config import market
    cons_txt, cons_valid = _consistency_note(overlap, n_a, n_b)
    bench_name = market().get("bench_name", "沪深300")
    if bench_close is None or len(bench_close) < 60:
        return "中性", "中仓", "大盘数据不足，倾向取中性", cons_txt
    bc = np.asarray(bench_close, float)
    ma60 = float(np.mean(bc[-60:]))
    btrend = bc[-1] / ma60 - 1.0
    bvol = float(np.std(np.diff(np.log(bc[-21:]))) * np.sqrt(252))
    leaning = "看多" if btrend > 0.01 else ("看空" if btrend < -0.01 else "中性")
    # 仓位：趋势为基，波动降档，一致性升档
    pos = 2  # 1轻/2中/3重
    if leaning == "看多":
        pos += 1
    if leaning == "看空":
        pos -= 1
    if bvol > 0.30:
        pos -= 1
    if cons_valid and overlap >= 1:
        pos += 1                       # 两线一致→信心增强（仅当一致性确实有信息时才动仓位）
    pos = max(1, min(3, pos))
    posname = {1: "轻仓", 2: "中仓", 3: "重仓"}[pos]
    bench_note = f"{bench_name} 相对60日线 {btrend*100:+.1f}%、年化波动 {bvol*100:.0f}% → 倾向{leaning}"
    return leaning, posname, bench_note, cons_txt


def build_cro(picks: list[DailyPick]) -> CRORating:
    """对两线选股做独立风控评级。picks 为一部+二部合并的当日选股。"""
    status: dict = {}
    stocks = [Stock(code=p.code, name=p.name, sector=p.sector,
                    yahoo=p.yahoo or (f"{p.code}.SS" if p.code[:1] == "6" else f"{p.code}.SZ")) for p in picks]
    panels = quant_data.get_history(stocks, days=300, status=status)
    bench_df = quant_data.get_benchmark(300, status=status)
    bench_close = bench_df["close"].values if bench_df is not None else None

    # 集中度：同板块超过上限，超出的（按分数低者先）标记⚠集中度警示（不否决，交 CEO 掂量）
    from collections import defaultdict
    by_sector: dict = defaultdict(list)
    for p in picks:
        if p.sector:
            by_sector[p.sector].append(p)
    conc_flag: set = set()
    for sec, ps in by_sector.items():
        if len(ps) > SECTOR_MAX:
            for p in sorted(ps, key=lambda x: x.score)[:len(ps) - SECTOR_MAX]:
                conc_flag.add((p.source, p.code))

    items: list[RiskItem] = []
    for p in picks:
        df = panels.get(p.code)
        m = _metrics(df, bench_close) if df is not None else None
        if m is None:
            items.append(RiskItem(source=p.source, code=p.code, name=p.name, sector=p.sector,
                                  rating="中", vetoed=False, reason="行情数据不足，暂无法评级（不猜）"))
            continue
        score, rating, vetoed, reason = _score_and_veto(m)
        if (p.source, p.code) in conc_flag and not vetoed:      # 集中度只警示、不否决
            rating = "高"
            reason = (reason + "；" if reason and "主要风险" not in reason else "") + f"⚠同板块扎堆（>{SECTOR_MAX}只）"
        items.append(RiskItem(
            source=p.source, code=p.code, name=p.name, sector=p.sector,
            vol=round(m["vol"], 4), max_dd=round(m["max_dd"], 4), liquidity=round(m["liquidity"], 1),
            beta=round(m["beta"], 3), trend=round(m["trend"], 4),
            risk_score=round(score, 3), rating=rating, vetoed=vetoed, reason=reason))

    # 两线一致性：一部与二部选股代码的重叠
    a_codes = {p.code for p in picks if p.source == "一部"}
    b_codes = {p.code for p in picks if p.source == "二部"}
    overlap = len(a_codes & b_codes)
    leaning, pos, bench_note, cons = _leaning(bench_close, overlap,
                                              min(len(a_codes), len(b_codes)),
                                              n_a=len(a_codes), n_b=len(b_codes))

    approved = [f"{it.source}·{it.code} {it.name}" for it in items if not it.vetoed]
    vetoed_n = sum(1 for it in items if it.vetoed)
    cov = CollectionStatus(structured=status, fetched=len(panels),
                           degraded=[k for k, v in status.items() if "缺" in str(v)])
    return CRORating(
        dt_beijing=stamp_beijing(), dt_ny=stamp_ny(),
        leaning=leaning, target_position=pos, bench_note=bench_note, consistency_note=cons,
        items=items, approved_candidates=approved, vetoed_count=vetoed_n, status=cov)


def archive_and_render(r: CRORating) -> tuple[str, str]:
    from .config import TOPIC_DIR
    from .render import render_cro_md, render_cro_pdf
    from .utils import file_stamp, safe_filename
    stamp = file_stamp()
    base = f"{safe_filename('CRO风控评级')}+{stamp}"
    md_path = TOPIC_DIR / f"{base}.md"
    pdf_path = TOPIC_DIR / f"{base}.pdf"
    md_path.write_text(render_cro_md(r), encoding="utf-8")
    try:
        render_cro_pdf(r, str(pdf_path))
    except Exception as e:
        log.error("CRO 风控评级 PDF 渲染失败: %s", e)
        pdf_path = None
    db.init_db()
    db.insert_brief("cro", "《CRO 风控评级》", str(md_path), str(pdf_path or ""))
    return str(md_path), str(pdf_path or "")
