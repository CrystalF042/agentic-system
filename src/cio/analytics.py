"""证券二部 — Systematic Analytics（确定性 · 零 LLM · 只测量，不判断）。

定位（2026-08 定稿）：
    二部不再宣称任何 alpha。UB-US-001 / 002 / 008 全部未通过准入闸，
    Production Factor Set = ∅，研究职能转入 dormant。
    二部现在只做一件事：**把关注池与组合的客观状态量出来**，交给 CRO 判断。

        Unit B: Measure  →  CRO: Assess  →  Portfolio Construction: Size  →  CEO: Decide

    二部可以说："NVDA 60 日年化波动率 48%，处于 S&P 500 第 91 百分位。"
    二部不可以说："NVDA 风险太高，所以只能配 2%。"——那是 CRO 与 PC 的职责。

为什么"失败的因子研究"留下的数学在这里仍然有效：
    波动率没有横截面 alpha，不等于波动率没有价值。作为**风险测量**，
    它本来就不需要预测未来收益。同一份计算，在研究阶段是 candidate predictor，
    在这里只是 descriptive state variable。这没有任何逻辑冲突——
    但为避免旧语义从词汇后门溜回来，本模块**一律不使用因子术语**：
    没有 "momentum exposure"，只有 "trailing 12-1 return"。

铁律：
  · 所有窗口长度写进字段名（vol_60d / beta_250d），不写脚注。
  · 不做任何 shrinkage / 平滑 —— 一平滑就从测量变成模型。
  · 缺失就是缺失，绝不用中位数之类的方式填补（填出来的数字会被下游当成事实）。
  · Exceptions 的阈值全部来自 config/analytics_thresholds.yaml，并在报告里原样印出。
"""
from __future__ import annotations

import os
from datetime import datetime

from .config import CONFIG_DIR, MARKET, market, market_date
from .models import (AnalyticsException, AnalyticsReport, AnalyticsRow,
                     CollectionStatus, PortfolioBlock, Pctile)
from .utils import date_only, get_logger, stamp_utc

log = get_logger("cio.analytics")

_TRADING_DAYS = 252.0


# ---------------- 配置 ----------------
def load_cfg() -> dict:
    """读阈值配置。文件缺失时用内置缺省，并在日志里说清楚——绝不静默。"""
    import yaml
    p = CONFIG_DIR / "analytics_thresholds.yaml"
    if not p.exists():
        log.warning("缺少 %s，使用内置缺省阈值（报告会标注）", p.name)
        return {"version": "builtin-default", "windows": {}, "percentile": {},
                "fundamentals": {}, "exceptions": {}}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def _w(cfg: dict, k: str, d):
    return (cfg.get("windows") or {}).get(k, d)


def _x(cfg: dict, k: str, d):
    return (cfg.get("exceptions") or {}).get(k, d)


# ---------------- 纯计算一律走共享层 ----------------
# 这些函数曾经就定义在本文件里。抽出去是因为证券一部也要用它们，
# 而一部不该 import 二部的模块——依赖方向错了，早晚会有人顺手把二部的结论也带过去。
# 现在两部各自 import measures，谁都不依赖谁。
from .measures import (_MIN_COVER, _TRADING_DAYS, _clean, _fin, _logret, _rank_pct,  # noqa: F401
                       ann_vol, beta_corr, downside_vol, max_drawdown,
                       pair_corr, px_vs_ma, trailing_return, vol_concentration)



# ---------------- 百分位（双口径 + 最小样本回退）----------------
_PCT_FIELDS = ["vol_60d", "downside_60d", "beta_250d", "max_dd_250d", "px_vs_ma120",
               "trail_12_1", "liab_assets", "gross_margin", "op_margin",
               "fcf_margin", "fcf_assets", "rev_growth"]




def attach_percentiles(rows: list, cfg: dict) -> None:
    """给每一行挂上双口径百分位。

    行业口径样本不足时回退全域，并把 basis 一起带出去。
    为什么必须回退并标注：GICS 11 个行业分 500 只，小行业只有二十几只名字；
    再叠加基本面字段缺失，可能只剩个位数。7 只样本里的 "90th" 没有信息量，
    但看起来和 500 只里的 "90th" 一模一样——不标 basis 就是在误导 CRO。

    分布本身也必须只收【有限数】：一个 NaN 混进去不会报错，但它永远不满足
    "v < value"，于是既不计入分子又照样撑大分母——所有干净标的的百分位被一起压低。
    """
    import math as _m
    min_n = int((cfg.get("percentile") or {}).get("min_sector_n", 15))
    min_uni = int((cfg.get("percentile") or {}).get("min_universe_n", 10))

    def _vals(rs, f):
        out = []
        for r in rs:
            v = getattr(r, f)
            if v is not None and _m.isfinite(v):
                out.append(v)
        return out

    for f in _PCT_FIELDS:
        uni = _vals(rows, f)
        by_sec: dict = {}
        for r in rows:
            v = getattr(r, f)
            if v is not None and _m.isfinite(v):
                by_sec.setdefault(r.gics_sector or "_NA", []).append(v)
        for r in rows:
            v = getattr(r, f)
            if v is None or not _m.isfinite(v):
                continue
            sec = by_sec.get(r.gics_sector or "_NA", [])
            if r.gics_sector and len(sec) >= min_n:
                p = _rank_pct(v, sec)
                if p is not None:
                    r.pctile[f] = Pctile(value=round(p, 1), basis="sector", n=len(sec))
            elif len(uni) >= min_uni:
                # 全域样本也太小时不给百分位。一个 n=1 的 "50th percentile" 是纯噪声，
                # 而它印出来和 n=500 的 50th 长得一模一样。
                p = _rank_pct(v, uni)
                if p is not None:
                    r.pctile[f] = Pctile(value=round(p, 1), basis="universe", n=len(uni))


# ---------------- 关注池 ----------------
def watchlist_codes() -> dict:
    """从 config/watchlist_<market>.yaml 抽出关注池标的 {ticker: (name, [themes])}。
    只取 companies（锚点公司），不取 keywords —— 后者是新闻主题词，不是标的。"""
    from .config import watchlist as _wl
    out: dict = {}
    for theme, blk in ((_wl() or {}).get("watchlist") or {}).items():
        for name, tk in ((blk or {}).get("companies") or {}).items():
            tk = str(tk).strip().upper()
            if not tk:
                continue
            nm, th = out.get(tk, ("", []))
            out[tk] = (nm or name, sorted(set(th + [theme])))
    return out


# ---------------- 组装 ----------------
def _row_for(s, df, bench_df, fund_snap: dict, cfg: dict, bench_dates=None) -> AnalyticsRow:
    import pandas as pd
    closes = df["close"].values
    beta, corr, _n = beta_corr(df, bench_df, int(_w(cfg, "beta_days", 250)),
                               int(_w(cfg, "corr_days", 60)))
    # 价格陈旧度。基本面有 stale 检查，价格却没有——这不对称：
    # 一条停更两周的价格序列会让波动率、回撤、Beta 全部停在两周前，
    # 而 px_last 还会被组合市值直接乘上去。用【基准的交易日】数落后了几个 session，
    # 而不是自然日：自然日会把周末算进去，长假后天天报"陈旧"。
    last_bar, stale_sessions = "", 0
    try:
        d = pd.to_datetime(df["date"]).max()
        last_bar = date_only(d.date())
        if bench_dates is not None and len(bench_dates):
            after = [x for x in bench_dates if x > d]
            stale_sessions = len(after)
    except Exception:
        pass
    mv, share, mi = vol_concentration(closes, int(_w(cfg, "vol_days", 60)))
    mv_date = ""
    if mi is not None:
        try:
            # _logret 的第 i 个收益对应窗口内第 i+1 根 K 线
            wd = list(pd.to_datetime(df["date"]))[-(int(_w(cfg, "vol_days", 60)) + 1):]
            mv_date = date_only(wd[mi + 1].date()) if mi + 1 < len(wd) else ""
        except Exception:
            mv_date = ""

    r = AnalyticsRow(
        code=s.code, name=s.name, gics_sector=s.gics_sector,
        focus_theme=list(s.focus_theme or []),
        member=bool(getattr(s, "_member", True)),
        identity_flag=getattr(s, "identity_flag", "") or "",
        vol_60d=ann_vol(closes, int(_w(cfg, "vol_days", 60))),
        downside_60d=downside_vol(closes, int(_w(cfg, "downside_days", 60))),
        beta_250d=beta, corr_bench_60d=corr,
        max_dd_250d=max_drawdown(closes, int(_w(cfg, "maxdd_days", 250))),
        px_vs_ma120=px_vs_ma(closes, int(_w(cfg, "ma_days", 120))),
        trail_12_1=trailing_return(closes, int(_w(cfg, "trail_lookback", 250)),
                                   int(_w(cfg, "trail_skip", 21))),
        px_last=_fin(closes[-1]) if len(closes) else None,
        max_1d_move=mv, max_1d_share=share, max_1d_date=mv_date,
        last_bar_date=last_bar,
        price_stale_days=(stale_sessions if stale_sessions > int(_x(cfg, "price_stale_days", 3)) else 0),
    )
    for k in ("liab_assets", "gross_margin", "op_margin", "fcf_margin", "fcf_assets",
              "rev_growth", "current_ratio", "interest_cover", "derived_fields",
              "filing_accepted_date", "filing_age_days", "filing_stale",
              "filing_cadence_days", "filing_stale_threshold_days"):
        if k in fund_snap:
            setattr(r, k, fund_snap[k])
    return r


def find_exceptions(rows: list, panels: dict, cfg: dict) -> list:
    """异常状态。只陈述"发生了什么"，绝不给"因此应该怎么做"。

    阈值全部来自配置，并把触发它的那条红线原样带在每条记录上——
    半年后读报告的人必须能一眼分清"这是测量"和"这是我们定的判断标准"。
    """
    out: list = []
    thr_vol = float(_x(cfg, "vol_pctile", 95))
    thr_dn = float(_x(cfg, "downside_pctile", 95))
    thr_dd = float(_x(cfg, "maxdd_pct", -30.0))
    thr_beta = float(_x(cfg, "beta", 1.80))
    # 配置键随指标一起改名；旧键仍然认，避免没更新 yaml 的机器静默失去这条红线。
    thr_lev = float(_x(cfg, "liab_assets_pctile", _x(cfg, "leverage_pctile", 90)))
    thr_corr = float(_x(cfg, "corr_pair", 0.85))
    thr_ext = float(_x(cfg, "trend_extended_pct", 25.0))

    def _p(r, f):
        return r.pctile.get(f)

    for r in rows:
        pv = _p(r, "vol_60d")
        # 一律以【值本身存在】为前提再格式化。以百分位存在为前提是错的：
        # 二者由不同代码路径写入，一旦将来有人在 attach 之后清空某个值，
        # 这里就会抛 TypeError 并整份日报崩掉。
        if pv and r.vol_60d is not None and pv.value > thr_vol:
            out.append(AnalyticsException(code=r.code, kind="volatility",
                       message=f"{r.code} realized vol {r.vol_60d:.0f}% — {pv.value:.0f}th pctile ({pv.basis}, n={pv.n})",
                       threshold=f"vol percentile > {thr_vol:.0f}", extremity=float(r.vol_60d)))
        pd_ = _p(r, "downside_60d")
        if pd_ and r.downside_60d is not None and pd_.value > thr_dn:
            out.append(AnalyticsException(code=r.code, kind="volatility",
                       message=f"{r.code} downside vol {r.downside_60d:.0f}% — {pd_.value:.0f}th pctile ({pd_.basis}, n={pd_.n})",
                       threshold=f"downside vol percentile > {thr_dn:.0f}", extremity=float(r.downside_60d)))
        if r.max_dd_250d is not None and r.max_dd_250d < thr_dd:
            out.append(AnalyticsException(code=r.code, kind="drawdown",
                       message=f"{r.code} 1Y max drawdown {r.max_dd_250d:.0f}%",
                       threshold=f"1Y max drawdown worse than {thr_dd:.0f}%",
                       extremity=abs(float(r.max_dd_250d))))
        if r.beta_250d is not None and r.beta_250d > thr_beta:
            out.append(AnalyticsException(code=r.code, kind="beta",
                       message=f"{r.code} Beta_250d {r.beta_250d:.2f}",
                       threshold=f"Beta_250d > {thr_beta:.2f}", extremity=float(r.beta_250d)))
        pl = _p(r, "liab_assets")
        if pl and r.liab_assets is not None and pl.value > thr_lev:
            out.append(AnalyticsException(code=r.code, kind="liab_assets",
                       message=f"{r.code} total liabilities/assets {r.liab_assets:.0f}% — "
                               f"{pl.value:.0f}th pctile ({pl.basis}, n={pl.n})",
                       threshold=f"liabilities/assets percentile > {thr_lev:.0f}",
                       extremity=float(r.liab_assets)))
        if r.px_vs_ma120 is not None and abs(r.px_vs_ma120) > thr_ext:
            side = "above" if r.px_vs_ma120 > 0 else "below"
            out.append(AnalyticsException(code=r.code, kind="extended",
                       message=f"{r.code} price {abs(r.px_vs_ma120):.0f}% {side} its 120-day average",
                       threshold=f"|price vs MA120| > {thr_ext:.0f}%",
                       extremity=abs(float(r.px_vs_ma120))))
        if r.filing_stale and r.filing_age_days is not None:
            out.append(AnalyticsException(code=r.code, kind="stale",
                       message=f"{r.code} no new SEC filing for {r.filing_age_days} days "
                               f"(last accepted {r.filing_accepted_date or 'n/a'}) — fundamentals in this row "
                               f"are that old"
                               + (f"; this filer's own cadence is ~{r.filing_cadence_days}d"
                                  if r.filing_cadence_days else ""),
                       threshold=(f"older than 1.5x this filer's own cadence "
                                  f"(> {r.filing_stale_threshold_days}d)"
                                  if r.filing_stale_threshold_days else
                                  f"no filing for > {int((cfg.get('fundamentals') or {}).get('stale_days', 100))} days"),
                       extremity=float(r.filing_age_days or 0)))
        if (r.max_1d_share is not None and r.max_1d_move is not None
                and r.max_1d_share > float(_x(cfg, "vol_single_day_share", 0.5))):
            out.append(AnalyticsException(code=r.code, kind="price_anomaly",
                       message=f"{r.code} volatility is dominated by one session: "
                               f"{r.max_1d_move:+.0f}% on {r.max_1d_date or 'n/a'} accounts for "
                               f"{r.max_1d_share*100:.0f}% of the {int(_w(cfg,'vol_days',60))}-day variance"
                               + (f" (Vol_60d reads {r.vol_60d:.0f}%)" if r.vol_60d is not None else "")
                               + " — check that day: a real event, or an unadjusted split / bad print",
                       threshold=f"single session > {float(_x(cfg,'vol_single_day_share',0.5))*100:.0f}% of window variance",
                       extremity=float(r.max_1d_share)))
        if r.price_stale_days:
            out.append(AnalyticsException(code=r.code, kind="price_stale",
                       message=f"{r.code} last price is {r.price_stale_days} trading-session days behind "
                               f"the as-of date (last bar {r.last_bar_date or 'n/a'}) — every risk number "
                               f"in this row ends there, not at the as-of date",
                       threshold=f"price series more than {int(_x(cfg, 'price_stale_days', 3))} sessions stale"))
        if r.identity_flag:
            out.append(AnalyticsException(code=r.code, kind="corp_action",
                       message=f"{r.code} corporate-action discontinuity ({r.identity_flag}) — "
                               f"price history spans two different economic entities",
                       threshold="identity registry / corporate_actions.yaml"))

    # 两两相关性：只在展示集内比（O(n²)，关注池几十只完全可接受）
    codes = [r.code for r in rows if r.code in panels]
    n_corr = int(_w(cfg, "corr_days", 60))
    for i in range(len(codes)):
        for j in range(i + 1, len(codes)):
            c = pair_corr(panels[codes[i]], panels[codes[j]], n_corr)
            if c is not None and c > thr_corr:
                out.append(AnalyticsException(
                    code=f"{codes[i]}/{codes[j]}", kind="correlation",
                    message=f"{codes[i]} and {codes[j]} daily-return correlation {c:.2f} over {n_corr}d",
                    threshold=f"pairwise correlation > {thr_corr:.2f}", extremity=float(c)))
    return out


def _open_positions() -> list:
    """读取 CFO 台账里的未平仓持仓。没有台账 / 没有持仓 → 返回空，
    调用方据此【整块不渲染】组合层，绝不印一堆 0（0 会被误读成"组合 Beta 为 0"）。"""
    try:
        from . import cfo
        conn = cfo.connect()
        cfo.init_schema(conn)
        rows = list(conn.execute(
            "SELECT account, code, name, cost, shares FROM positions WHERE open=1"))
        conn.close()
        return [{"account": r[0], "code": str(r[1]).upper(), "name": r[2],
                 "cost": float(r[3] or 0), "shares": int(r[4] or 0)} for r in rows]
    except Exception as e:
        log.info("未读到持仓台账（组合层将不渲染）：%s", e)
        return []


def build_portfolio(rows: list, panels: dict, cfg: dict) -> PortfolioBlock:
    """组合层聚合。条件渲染：只有当持仓与本市场标的确实对得上时才出现。"""
    pos = _open_positions()
    if not pos:
        return PortfolioBlock(present=False, coverage_note="no open positions on file")
    by_code = {r.code: r for r in rows}
    # 账户选择必须【确定性且有理由】。原来取 pos[0] 是 SQLite 的返回顺序，
    # 实际会拿到最早插入的那个账户（通常是一部），于是二部持有的几只美股
    # 被整块判为"本市场无可定价持仓"，组合区块直接消失，还宣称只有 1 笔持仓。
    # 规则：排除影子盘，然后选在本市场能定价的持仓最多的账户；平手取名称排序，保证可复现。
    real = [p for p in pos if not str(p["account"]).endswith("_shadow")] or pos
    by_acct: dict = {}
    for p in real:
        by_acct.setdefault(p["account"], []).append(p)
    def _priced(ps):
        return sum(1 for p in ps if (by_code.get(p["code"]) is not None
                                     and by_code[p["code"]].px_last is not None))
    acct = sorted(by_acct.keys(), key=lambda k: (-_priced(by_acct[k]), k))[0]
    other = sorted(k for k in by_acct if k != acct)
    pos = by_acct[acct]
    matched, mv, missing = [], 0.0, []
    for p in pos:
        r = by_code.get(p["code"])
        if r is None or r.px_last is None:
            missing.append(p["code"])
            continue
        v = r.px_last * p["shares"]
        matched.append((r, v))
        mv += v
    other_note = (f"; other accounts on file not shown: {', '.join(other)}" if other else "")
    if not matched or mv <= 0:
        # 持仓存在但与本市场标的对不上（例如 A 股持仓 + US 报告）——如实说明，不渲染数字
        return PortfolioBlock(present=False, n_positions=len(pos), account=acct,
                              coverage_note=f"account '{acct}': {len(pos)} open position(s) on file, none priced "
                                            f"in this market's universe ({', '.join(missing[:6]) or 'n/a'}) — "
                                            f"portfolio block omitted{other_note}")
    # 仅用【市值为正】的持仓做加权，否则 0 股持仓会让分母为 0 并抛 ZeroDivisionError，
    # 整份日报随之失败——一个记账上的边角情况不该能杀掉测量报告。
    betas = [(r.beta_250d, v) for r, v in matched if r.beta_250d is not None and v > 0]
    wsum = sum(v for _b, v in betas)
    pb = (sum(b * v for b, v in betas) / wsum) if wsum > 0 else None
    sw: dict = {}
    for r, v in matched:
        sw[r.gics_sector or "Unclassified"] = sw.get(r.gics_sector or "Unclassified", 0.0) + v / mv * 100
    top = max(sw.items(), key=lambda kv: kv[1]) if sw else ("", 0.0)
    thr = float(_x(cfg, "corr_pair", 0.85))
    n_corr = int(_w(cfg, "corr_days", 60))
    clusters = []
    cs = [r.code for r, _v in matched if r.code in panels]
    for i in range(len(cs)):
        for j in range(i + 1, len(cs)):
            c = pair_corr(panels[cs[i]], panels[cs[j]], n_corr)
            if c is not None and c > thr:
                clusters.append(f"{cs[i]}–{cs[j]} corr {c:.2f}")
    note = f"account '{acct}': {len(matched)}/{len(pos)} positions priced{other_note}"
    if missing:
        note += f"; not priced: {', '.join(missing[:6])}"
    if not betas:
        note += "; portfolio beta omitted (no constituent beta available)"
    return PortfolioBlock(present=True, account=acct, n_positions=len(matched),
                          market_value=round(mv, 2), beta_250d=(round(pb, 2) if pb is not None else None),
                          sector_weights={k: round(v, 1) for k, v in sorted(sw.items(), key=lambda kv: -kv[1])},
                          top_sector=top[0], top_sector_pct=round(top[1], 1),
                          corr_clusters=clusters, coverage_note=note)


def build_analytics(universe_limit: int = 0, want_fundamentals: bool = True) -> AnalyticsReport:
    """跑一份《Systematic Analytics》日报。

    注意一个容易被误解的成本事实：要给出 S&P 500 百分位，**全 500 只每天照样要算**，
    只是只打印关注池的几十只。降的是报告长度和认知负担，不是运行成本。
    """
    import hashlib
    import json

    from . import db, fundamentals, ledger, quant_data
    from .utils import stamp_ny

    cfg = load_cfg()
    status: dict = {}
    stocks, src = quant_data.get_universe(limit=universe_limit)
    for s in stocks:
        s._member = True

    # 关注池里可能有非指数成分（例如 NVO 是丹麦公司，不在 S&P 500）。
    # 它照常算自己的指标，并放进 500 的分布里定位，但必须标 non-member。
    wl = watchlist_codes()
    have = {s.code for s in stocks}
    extra = []
    for tk, (nm, themes) in sorted(wl.items()):
        if tk in have:
            continue
        s = quant_data.Stock(code=tk, name=nm, yahoo=tk, sector=", ".join(themes),
                             focus_theme=list(themes))
        s._member = False
        extra.append(s)
    all_stocks = list(stocks) + extra

    days = int(os.environ.get("CIO_AN_DAYS", "400"))       # 400 交易日足够 250 日 Beta / 回撤
    panels = quant_data.get_history(all_stocks, days=days, status=status)
    bench = quant_data.get_benchmark(days=days, status=status)

    # as_of：报告数字所依据的【最后一个已完成交易日】。以基准为准（指数每个交易日都有），
    # 基准缺失时退回全池最大日期。这与"生成时刻"是两个不同的日期，绝不混用。
    as_of = ""
    if bench is not None and len(bench):
        import pandas as pd
        as_of = date_only(pd.to_datetime(bench["date"]).max().date())
    elif panels:
        import pandas as pd
        as_of = date_only(max(pd.to_datetime(df["date"]).max() for df in panels.values()).date())

    fund_all: dict = {}
    fund_note = ""
    if not want_fundamentals:
        fund_note = "skipped by CIO_AN_NO_FUND=1"
    else:
        try:
            fund_all = fundamentals.load_universe_cached(all_stocks, status)
        except RuntimeError as e:
            log.warning("跳过基本面：%s", e)
            status["sec_facts"] = "skipped (CIO_SEC_UA not set)"
            fund_note = ("CIO_SEC_UA is not set. SEC fair-access requires an identifiable "
                         "User-Agent with contact info; add it to .env "
                         "(e.g. CIO_SEC_UA=Your Name your@email.com)")
        except Exception as e:
            log.warning("基本面取数异常，本轮只出风险测量：%s", e)
            status["sec_facts"] = f"error: {e}"
            fund_note = f"SEC fetch failed: {e}"

    stale_days = int((cfg.get("fundamentals") or {}).get("stale_days", 100))
    as_of_d = None
    if as_of:
        try:
            as_of_d = datetime.strptime(as_of, "%Y-%m-%d").date()
        except Exception:
            as_of_d = None

    bench_dates = None
    if bench is not None and len(bench):
        import pandas as pd
        bench_dates = list(pd.to_datetime(bench["date"]))

    rows: list = []
    skipped = {"no_price": 0, "short_history": 0}
    for s in all_stocks:
        df = panels.get(s.code)
        if df is None or not len(df):
            skipped["no_price"] += 1
            continue
        if len(df) < 60:
            skipped["short_history"] += 1
            continue
        snap = {}
        f = fund_all.get(s.code)
        if f and as_of_d:
            try:
                snap = fundamentals.snapshot(f, as_of_d, stale_days=stale_days)
            except Exception:
                snap = {}
        row = _row_for(s, df, bench, snap, cfg, bench_dates)
        # 外国发行人（NVO/ASML/ARM/TSM 等）报 20-F，用 IFRS 分类，
        # 在 us-gaap 里一条事实都没有。这不是"取数失败"，是覆盖范围之外——
        # 必须说清楚，否则整行破折号看起来像我们漏了数据。
        if f is not None and not fundamentals.has_us_gaap(f):
            row.no_us_gaap = True
        rows.append(row)

    attach_percentiles(rows, cfg)          # 百分位在【全域】上算，再挑关注池展示

    wl_set = set(wl.keys())
    display = [r for r in rows if r.code in wl_set]
    display.sort(key=lambda r: (r.focus_theme[0] if r.focus_theme else "zz", r.code))
    exceptions = find_exceptions(display, panels, cfg)
    portfolio = build_portfolio(rows, panels, cfg)

    meta = quant_data._LAST_UNIVERSE_META
    prod = ledger.production_factors()
    # 状态位必须【推导】，不能靠模型默认值。报告里印的话和系统实际行为必须一致，
    # 否则最危险的分支（真有因子通过闸门那天）会出现"报告说弃权、CRO 却在投票"。
    if prod:
        alpha_status = f"validated factors admitted: {', '.join(prod)} — human review required before use"
        alpha_vote = "REVIEW REQUIRED"
        research_st = "active"
    else:
        alpha_status = "no validated production model"
        alpha_vote = "ABSTAIN"
        research_st = "dormant"
    gen_utc = stamp_utc()
    # run_id 跟【交易日】，不跟机器时钟。同一个交易日可以跑多次，
    # 所以后面接市场时区的 HHMM 用于区分；UTC 时间戳只留在 manifest 里。
    from .config import market_now
    _rid_day = (as_of or market_date()).replace("-", "")
    run_id = f"an-{MARKET}-{_rid_day}-{market_now().strftime('%H%M')}"
    thresholds_shown = [
        f"volatility percentile > {_x(cfg, 'vol_pctile', 95)}",
        f"downside vol percentile > {_x(cfg, 'downside_pctile', 95)}",
        f"1Y max drawdown worse than {_x(cfg, 'maxdd_pct', -30.0)}%",
        f"Beta_250d > {_x(cfg, 'beta', 1.80)}",
        f"liabilities/assets percentile > {_x(cfg, 'liab_assets_pctile', _x(cfg, 'leverage_pctile', 90))}",
        f"pairwise correlation > {_x(cfg, 'corr_pair', 0.85)}",
        f"|price vs MA120| > {_x(cfg, 'trend_extended_pct', 25.0)}%",
        f"no SEC filing for > {stale_days} days",
    ]
    windows_note = (f"vol {_w(cfg,'vol_days',60)}d · downside {_w(cfg,'downside_days',60)}d · "
                    f"beta {_w(cfg,'beta_days',250)}d · corr {_w(cfg,'corr_days',60)}d · "
                    f"maxDD {_w(cfg,'maxdd_days',250)}d · MA {_w(cfg,'ma_days',120)}d · "
                    f"trailing {_w(cfg,'trail_lookback',250)}d skip {_w(cfg,'trail_skip',21)}d. "
                    f"No shrinkage or smoothing is applied.")
    funnel = (f"{len(stocks)} index constituents + {len(extra)} non-member watchlist names "
              f"-> {len(rows)} measured -> {len(display)} displayed")
    if any(skipped.values()):
        funnel += f" | skipped: no price {skipped['no_price']}, short history {skipped['short_history']}"

    manifest = {
        "run_id": run_id, "kind": "unit_b_analytics", "market": MARKET,
        "as_of_trade_date": as_of, "generated_at_utc": gen_utc,
        "generated_at_market": stamp_ny(),
        "universe_src": src, "universe_snapshot": meta.get("snapshot", ""),
        "universe_hash": hashlib.md5(",".join(sorted(s.code for s in stocks)).encode()).hexdigest()[:12],
        "price_source": str(status.get("quant_history", "")),
        "bench_source": market().get("bench_source", ""), "bench_basis": market().get("bench_basis", ""),
        "sec_facts": status.get("sec_facts", ""),
        "thresholds_version": cfg.get("version", ""), "alpha_vote": "ABSTAIN",
        "production_factor_set": prod,
        "params_json": json.dumps({"windows": cfg.get("windows", {}),
                                   "percentile": cfg.get("percentile", {}),
                                   "exceptions": cfg.get("exceptions", {}),
                                   "days": days, "funnel": funnel}, ensure_ascii=False),
    }
    try:
        db.init_db()
        db.insert_manifest({**manifest, "run_at": manifest["generated_at_market"],
                            "price_pit": True, "universe_pit": bool(meta.get("universe_pit"))})
    except Exception as e:
        log.warning("manifest 写入失败：%s", e)

    deg = []
    if meta.get("degraded"):
        deg.append("universe=fallback-DEGRADED/TEST (smoke only, not an official result)")
    if prod:
        deg.append(f"Production Factor Set is NOT empty ({', '.join(prod)}) — this report's "
                   f"abstain wording no longer describes the system; human review required")
    snap_day = (meta.get("snapshot") or "").replace("sp500_", "")
    if snap_day and as_of and snap_day != as_of:
        deg.append(f"universe snapshot is dated {snap_day} but the as-of trade date is {as_of} — "
                   f"membership comes from a different session (last-known-good fallback, or a "
                   f"pre-market run). Not an error, but the two dates are deliberately not forced equal.")
    if not display:
        # 一份 0 行的报告看起来和"今天一切正常"一模一样，必须显式说明。
        # 最常见的原因：没设 CIO_MARKET=us，而只有 watchlist_us.yaml 里有 companies: 锚点公司，
        # 于是关注池解析为空、展示集为空，报告照常生成、照常推送，全程没有任何报错。
        deg.append(f"NO watchlist names resolved for market '{MARKET}' — the report is empty. "
                   f"Check CIO_MARKET and the `companies:` entries in config/watchlist_{MARKET}.yaml")

    return AnalyticsReport(
        as_of_trade_date=as_of, generated_at_utc=manifest["generated_at_utc"],
        generated_at_market=manifest["generated_at_market"],
        filing_window_note="Fundamentals are point-in-time: only filings accepted on or before "
                           "the as-of trade date are visible. They do not change daily.",
        fundamentals_note=fund_note,
        market=MARKET, benchmark=market().get("bench_name", ""),
        bench_source=market().get("bench_source", ""), bench_basis=market().get("bench_basis", ""),
        universe_src=src, universe_snapshot=meta.get("snapshot", ""),
        universe_count=len(rows), displayed_count=len(display),
        production_factor_set=prod, research_status=research_st,
        alpha_status=alpha_status, alpha_vote=alpha_vote,
        rows=display, exceptions=exceptions, portfolio=portfolio,
        currency=market().get("currency", ""),
        thresholds_version=str(cfg.get("version", "")), thresholds_shown=thresholds_shown,
        windows_note=windows_note, funnel=funnel,
        status=CollectionStatus(structured={"universe": src, **status},
                                fetched=len(panels), degraded=deg),
        run_id=run_id, manifest=manifest,
    )


def archive_and_render(r: AnalyticsReport) -> tuple:
    from . import db
    from .config import TOPIC_DIR
    from .render_analytics import render_analytics_md, render_analytics_pdf
    from .utils import safe_filename

    # 文件名用 as_of 交易日（报告内容的日期），不是生成时刻——归档按内容归位才查得到。
    stamp = (r.as_of_trade_date or r.generated_at_utc[:10]).replace("-", "")
    # 文件名必须带市场：同一分钟内先后跑 us 与 cn，两份内容完全不同的报告
    # 会落到同一个文件名上，后者静默覆盖前者。
    # 日期部分与 run_id 同源（都是 as_of 交易日），归档、run_id、报告正文三处再无分歧。
    base = (f"{safe_filename('UnitB_Systematic_Analytics')}+{r.market}"
            f"+asof{stamp}+{r.run_id.split('-')[-1]}")
    md_path = TOPIC_DIR / f"{base}.md"
    pdf_path = TOPIC_DIR / f"{base}.pdf"
    md_path.write_text(render_analytics_md(r), encoding="utf-8")
    try:
        render_analytics_pdf(r, str(pdf_path))
    except Exception as e:
        log.error("Analytics PDF 渲染失败: %s", e)
        pdf_path = None
    try:
        db.init_db()
        db.insert_brief("unit_b_analytics", "Unit B — Systematic Analytics",
                        str(md_path), str(pdf_path or ""))
    except Exception as e:
        log.warning("归档写库失败：%s", e)
    return str(md_path), str(pdf_path or "")
