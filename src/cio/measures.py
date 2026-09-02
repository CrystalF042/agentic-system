"""共享确定性计算层 —— 一部与二部都调用它，谁都不依赖谁。

**为什么必须单独成模块，而不是留在 analytics.py 里：**
证券一部与证券二部要保持方法独立——一部做因果/前瞻推理，二部做当前状态测量，
两者独立产出，CEO 横向比对。若一部 `import analytics`，它在结构上就依赖了二部的模块，
即便只用函数不读结论，这个依赖方向本身也是错的：将来一定会有人顺手多 import 一个，
把二部的结论带进一部的推理，两线一致/分歧这个风险信号随之失真。

正确形状是：

        shared deterministic tools (本模块)
              ↙                ↘
        Unit A              Unit B
    (解释未来)            (测量现在)

而不是 Unit B report → Unit A。

本模块只放**纯计算**：输入数组/DataFrame，输出数字或 None。
不读配置、不写文件、不含任何部门语义、不做任何判断。
三条铁律与二部一致：窗口名副其实、脏数据丢弃且计数、返回有限数或 None（绝不 NaN）。
"""
from __future__ import annotations

_TRADING_DAYS = 252.0          # 年化换算基数（美股/A股通用近似）

# ---------------------------------------------------------------- 单位契约
# **本模块的比率类测量一律返回百分数（已 ×100）：40.74 表示 40.74%。**
#
# 这条约定必须写在产出这些数字的地方，因为它已经造成过一次真实事故：
# CRO 的政策阈值是小数（veto_vol = 1.50 表示 150%），run_pc 直接把
# `ann_vol()` 的 40.74 拿去和 1.50 比，于是 NVDA 被"波动率 4074%"否决。
# **日志、报告、否决理由全部正常，只是结论是反的**——数据没错，是口径与解释对不上。
#
# 不改成返回小数，是因为二部整张横截面表、所有分位数、所有已生成的报告都按
# 百分数在读；改返回值会让每一个旧调用点静默地差 100 倍，比现在危险得多。
# 正确做法是把转换点显式化：跨模块边界时调用 `as_ratio()`，并在接收端校验量级。
PERCENT_RETURNING = frozenset({
    "ann_vol", "downside_vol", "max_drawdown", "px_vs_ma", "trailing_return"})
RATIO_RETURNING = frozenset({"beta_corr", "pair_corr"})     # 无量纲，不要转换


def as_ratio(v):
    """百分数 → 小数（40.74 → 0.4074）。None 透传——缺失不是 0。"""
    return None if v is None else float(v) / 100.0

# ---------------- 基础统计（全部为描述性测量）----------------
# 三条贯穿全节的纪律，都是被真实缺陷教出来的：
#
# ① 窗口名必须名副其实。字段叫 max_dd_250d，就不能用 60 根 K 线算出来——
#    回撤在短窗口里天然更浅，用 60 根算出的"1年最大回撤"会让次新股
#    显示成全行业最抗跌，还永远触发不了回撤红线。所以每个指标都要求
#    实际可用样本达到名义窗口的 _MIN_COVER，否则返回 None。
# ② 脏价格必须丢弃、并且丢弃要计数。把 NaN 直接压成 1e-12 会造出 −27 的日收益；
#    把 NaN 剔掉再 diff 则会把两日、十日的涨跌当成一日收益，波动率凭空抬高。
#    正确做法：剔除后检查剩余覆盖率，不够就返回 None。
# ③ 返回值必须是有限数或 None，绝不能是 NaN。NaN 会通过 `is not None` 检查混进
#    横截面分布，让自己排到 0 分位，还把所有干净标的的百分位一起压低。
_MIN_COVER = 0.80


def _clean(closes, n: int):
    """取最近 n 个有效收盘价。返回 (数组, 覆盖率)。
    非有限值与非正值一律剔除——它们不是价格，是数据缺陷。"""
    import numpy as np
    c = np.asarray(closes[-n:], dtype=float)
    ok = np.isfinite(c) & (c > 0)
    return c[ok], (float(ok.sum()) / max(len(c), 1))


def _fin(v):
    """把 NaN / inf 统一收敛成 None。测量结果只有两种：有限数，或者没有。"""
    import math as _m
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if _m.isfinite(f) else None


def _logret(closes, n: int):
    """近 n 日对数收益。覆盖率不足则返回空数组（宁可缺失，不可拼接）。"""
    import numpy as np
    c, cover = _clean(closes, n + 1)
    if cover < _MIN_COVER or len(c) < 2:
        return np.array([])
    return np.diff(np.log(c))


def ann_vol(closes, n: int):
    """年化已实现波动率 %（近 n 日对数收益标准差 × √252）。"""
    import numpy as np
    r = _logret(closes, n)
    if len(r) < n * _MIN_COVER:
        return None
    return _fin(np.std(r, ddof=1) * (_TRADING_DAYS ** 0.5) * 100)


def downside_vol(closes, n: int):
    """年化下行波动 %。用【对 0 的半标准差】sqrt(mean(min(r,0)^2))，
    不是"负收益子集的标准差"——后者是条件标准差，衡量的是下跌日彼此差多少，
    而风险管理关心的是下跌本身的幅度。这是 Sortino 分母的标准口径。"""
    import numpy as np
    r = _logret(closes, n)
    if len(r) < n * _MIN_COVER:
        return None
    d = np.minimum(r, 0.0)
    return _fin((np.mean(d ** 2) ** 0.5) * (_TRADING_DAYS ** 0.5) * 100)


def max_drawdown(closes, n: int):
    """近 n 日最大回撤 %（负数）。样本不足名义窗口的 80% 即返回 None——
    否则次新股会拿一个 60 日的浅回撤去和别人的 250 日回撤比百分位。"""
    import numpy as np
    c, _cov = _clean(closes, n)
    if len(c) < n * _MIN_COVER:
        return None
    peak = np.maximum.accumulate(c)
    return _fin(np.min(c / peak - 1.0) * 100)


def px_vs_ma(closes, n: int):
    """现价相对 n 日均线 %。这是【当前价格在中期历史中的位置】，不是趋势因子。"""
    import numpy as np
    c, _cov = _clean(closes, n)
    if len(c) < n * _MIN_COVER:
        return None
    ma = float(np.mean(c))
    return _fin(c[-1] / ma - 1.0) * 100 if ma > 0 else None


def trailing_return(closes, lookback: int, skip: int):
    """尾随 12-1 收益 %：从 lookback 日前，到 skip 日前。纯描述：过去一年涨跌了多少。

    索引用 -(lookback+1) 而不是 -lookback：区间要覆盖完整的 lookback 个交易日，
    起点必须是"lookback 日之前的那个收盘"。差一天听起来无关紧要，
    但如果那天正好是财报跳空，整个字段会平移一个跳空的幅度，百分位跟着错位。
    """
    import numpy as np
    c = np.asarray(closes, dtype=float)
    if len(c) < lookback + 1:
        return None
    a, b = c[-(lookback + 1)], c[-(skip + 1)]
    if not (np.isfinite(a) and np.isfinite(b)) or a <= 0:
        return None
    return _fin((b / a - 1.0) * 100)


def vol_concentration(closes, n: int):
    """波动率有多少来自【单独一天】。返回 (最大单日涨跌幅 %, 该日占窗口平方和的比例, 位置)。

    为什么需要这个：年化波动率是把 60 天的平方和开根号再放大 √252 倍。
    如果其中一天走了 +100%，仅这一天就能把年化波动推到 140% 以上——
    报出来是一个"229% 年化波动"，看着像一只极度动荡的股票，
    实际上是一次事件（生物医药的二元读数、并购），
    或者是一条脏数据（未复权的拆股、错误报价）。

    两种情况都必须让读者知道，但**不能替他判断是哪一种**——所以这里只做测量：
    把最大单日涨跌和它占整个窗口波动的比例摆出来，让人自己去看那天发生了什么。
    """
    import numpy as np
    r = _logret(closes, n)
    if len(r) < 10:
        return None, None, None
    sq = r ** 2
    tot = float(sq.sum())
    if tot <= 0:
        return None, None, None
    i = int(np.argmax(sq))
    move = float(np.expm1(r[i]) * 100)          # 对数收益 → 普通涨跌幅，便于阅读
    return _fin(move), _fin(sq[i] / tot), i


def beta_corr(df, bench_df, beta_days: int, corr_days: int):
    """Beta 与相关性。**按日期对齐**，不是按位置对齐——
    停牌、上市日不同、数据源缺日都会让"倒数第 k 根"落在不同日历日上，
    位置对齐会算出一个看起来正常、其实错位的 Beta。
    返回 (beta_250d, corr_60d, n_aligned)。"""
    import numpy as np
    import pandas as pd
    if df is None or bench_df is None or not len(df) or not len(bench_df):
        return None, None, 0
    a = pd.DataFrame({"date": pd.to_datetime(df["date"]), "s": df["close"].astype(float)})
    b = pd.DataFrame({"date": pd.to_datetime(bench_df["date"]), "b": bench_df["close"].astype(float)})
    m = a.merge(b, on="date", how="inner").sort_values("date")
    # 脏价格整行剔除，而不是压到 1e-12。压值会造出 log(1e-12/100) ≈ −27 的"日收益"，
    # 一根就足以把 Beta 从 1.2 拉到 2.2，且结果是个正常数字，看不出异常。
    ok = np.isfinite(m["s"].values) & np.isfinite(m["b"].values) & \
        (m["s"].values > 0) & (m["b"].values > 0)
    m = m[ok]
    if len(m) < 30:
        return None, None, len(m)
    rs = np.diff(np.log(m["s"].values))
    rb = np.diff(np.log(m["b"].values))
    beta = None
    if len(rs) >= beta_days * _MIN_COVER:          # 字段叫 beta_250d，就得真有 ~250 根
        x, y = rb[-beta_days:], rs[-beta_days:]
        v = float(np.var(x, ddof=1))
        if v > 1e-18 and float(np.var(y, ddof=1)) > 1e-18:
            beta = _fin(np.cov(y, x, ddof=1)[0, 1] / v)
    corr = None
    if len(rs) >= corr_days * _MIN_COVER:
        x, y = rb[-corr_days:], rs[-corr_days:]
        sx, sy = float(np.std(x, ddof=1)), float(np.std(y, ddof=1))
        if sx > 1e-18 and sy > 1e-18:
            corr = _fin(np.corrcoef(y, x)[0, 1])
    return beta, corr, len(m)


def pair_corr(df_a, df_b, n: int):
    """两只标的日收益相关性（同样按日期对齐）。"""
    import numpy as np
    import pandas as pd
    if df_a is None or df_b is None:
        return None
    a = pd.DataFrame({"date": pd.to_datetime(df_a["date"]), "a": df_a["close"].astype(float)})
    b = pd.DataFrame({"date": pd.to_datetime(df_b["date"]), "b": df_b["close"].astype(float)})
    m = a.merge(b, on="date", how="inner").sort_values("date")
    ok = np.isfinite(m["a"].values) & np.isfinite(m["b"].values) & \
        (m["a"].values > 0) & (m["b"].values > 0)
    m = m[ok]
    if len(m) < n * _MIN_COVER:
        return None
    ra = np.diff(np.log(m["a"].values))[-n:]
    rb = np.diff(np.log(m["b"].values))[-n:]
    if len(ra) < n * _MIN_COVER or np.std(ra) < 1e-18 or np.std(rb) < 1e-18:
        return None
    return _fin(np.corrcoef(ra, rb)[0, 1])



# ---------------- 百分位（纯计算；口径与分母由调用方决定）----------------
def _rank_pct(value: float, dist: list) -> "float | None":
    """升序百分位：pct=90 表示该值高于全体 90%。同分取中点，避免边界跳变。
    **不做方向翻转**——翻转是判断，测量报告不做判断。

    value 非有限或 dist 为空 → None。绝不能返回 0.0：NaN 与任何数比较都是 False，
    落到这里会算出 0.0，然后以"第 0 百分位"的身份印进报告，看起来像一个真实极值。
    """
    import math as _m
    if not dist or value is None or not _m.isfinite(value):
        return None
    below = sum(1 for v in dist if v < value)
    equal = sum(1 for v in dist if v == value)
    return (below + 0.5 * equal) / len(dist) * 100.0
