"""波动度量 —— ATR、ATR 分位、区间收缩。

**这个模块是价区算法的量纲来源。** 0.5×ATR20 那个聚类容差要是从这里
取错了数，价区会静默地变宽或变窄——不报错、图上看着也像模像样。
所以 ATR 只在这里实现一次，别处一律调它。

## 为什么不用 TA-Lib

ATR 是 Wilder 平滑，十行以内。自己写的好处是**边界情况由我们决定**：
第一根 K 线没有前收，`true_range` 只能退化成 high-low；
数据不足 n+1 根时返回 `None` 而不是一个用三根 K 线算出来的"ATR14"。
库会给你后者，而且不会说。

## 分位数说的是"和它自己比"

`atr_percentile_252` 回答的是**这只票现在的波动，在它自己过去一年里
排第几**——不是和别的票比。跨票比较要看全市场分布，那是
`scripts/technical_distribution.py` 的事。
"""
from __future__ import annotations

from typing import Optional

ATR_N = 14
"""ATR 的标准窗口。"""

ATR_ZONE_N = 20
"""**价区聚类专用的 ATR 窗口。** 和 ATR_N 分开命名不是啰嗦：
CEO 冻结的参数是「0.5×ATR20」，如果这里跟着 ATR_N 走，
哪天有人把 ATR_N 从 14 调成 10，价区算法就被静默改了参数。
冻结的东西要有自己的名字。"""

PCT_LOOKBACK = 252
"""分位数的回看窗口（约一年交易日）。"""

RANGE_N = 20
NR_N = 7


def _series(df, col):
    return [float(x) for x in df[col].tolist()]


def true_range(df) -> list:
    """逐根 K 线的真实波幅。**第一根没有前收，只能退化成 high-low**——
    这是定义上的缺口，不是缺陷，但要写出来，否则以后有人会以为是 bug。"""
    high, low, close = _series(df, "high"), _series(df, "low"), _series(df, "close")
    out = []
    for i in range(len(df)):
        if i == 0:
            out.append(high[i] - low[i])
            continue
        pc = close[i - 1]
        out.append(max(high[i] - low[i], abs(high[i] - pc), abs(low[i] - pc)))
    return out


def atr(df, n: int = ATR_N) -> Optional[float]:
    """Wilder 平滑的 ATR。**数据不足 n+1 根返回 None，不返回一个凑出来的数。**"""
    if df is None or len(df) < n + 1:
        return None
    tr = true_range(df)
    val = sum(tr[1:n + 1]) / n            # 第一根的 TR 是退化值，不进种子
    for x in tr[n + 1:]:
        val = (val * (n - 1) + x) / n
    return float(val)


def atr_series(df, n: int = ATR_N) -> list:
    """逐日 ATR（前 n 根为 None）。分位数要用整条序列。"""
    if df is None or len(df) < n + 1:
        return [None] * (0 if df is None else len(df))
    tr = true_range(df)
    out = [None] * len(df)
    val = sum(tr[1:n + 1]) / n
    out[n] = float(val)
    for i in range(n + 1, len(df)):
        val = (val * (n - 1) + tr[i]) / n
        out[i] = float(val)
    return out


def percentile_of_last(values: list, lookback: int) -> Optional[float]:
    """最后一个值在最近 `lookback` 个值里的分位（0~1）。

    **不足 lookback 就返回 None。** 用 60 天的数据算"一年分位"，
    算得出来、看着也正常，但它回答的是另一个问题。
    """
    vals = [v for v in values if v is not None]
    if len(vals) < lookback:
        return None
    win = vals[-lookback:]
    cur = win[-1]
    return float(sum(1 for v in win if v <= cur) / len(win))


def range_pct(df, n: int = RANGE_N) -> Optional[float]:
    """最近 n 根的高低区间宽度 ÷ 最新收盘。**"横盘"就是这个数变小。**"""
    if df is None or len(df) < n:
        return None
    hi = max(_series(df, "high")[-n:])
    lo = min(_series(df, "low")[-n:])
    close = _series(df, "close")[-1]
    if close <= 0:
        return None
    return float((hi - lo) / close)


def range_pct_series(df, n: int = RANGE_N) -> list:
    if df is None or len(df) < n:
        return [None] * (0 if df is None else len(df))
    high, low, close = _series(df, "high"), _series(df, "low"), _series(df, "close")
    out = [None] * len(df)
    for i in range(n - 1, len(df)):
        hi, lo, c = max(high[i - n + 1:i + 1]), min(low[i - n + 1:i + 1]), close[i]
        out[i] = float((hi - lo) / c) if c > 0 else None
    return out


def is_nr(df, n: int = NR_N) -> Optional[bool]:
    """今天的日内振幅是不是最近 n 根里最窄的（NR7）。

    这是一个**离散事实**，不是预测。它经常出现在大幅波动之前，
    也经常什么都不出现——v1 只负责说"今天是 NR7"。
    """
    if df is None or len(df) < n:
        return None
    high, low = _series(df, "high")[-n:], _series(df, "low")[-n:]
    rng = [h - l for h, l in zip(high, low)]
    return bool(rng[-1] <= min(rng))


def measure(df) -> tuple[dict, dict]:
    """整块波动度量。返回 (值, 说不出来的原因)。

    **每一个 None 都要在 reasons 里有一条。** 下游看到 null 时能知道
    是"数据不够"还是"这只票真的没波动"——这两件事在一个 0 上分不出来。
    """
    vals: dict = {}
    why: dict = {}
    n = 0 if df is None else len(df)

    a = atr(df, ATR_N)
    vals["atr_14"] = round(a, 6) if a is not None else None
    if a is None:
        why["atr_14"] = f"需要至少 {ATR_N + 1} 根 K 线，只有 {n} 根"

    az = atr(df, ATR_ZONE_N)
    vals["atr_20"] = round(az, 6) if az is not None else None
    if az is None:
        why["atr_20"] = f"需要至少 {ATR_ZONE_N + 1} 根 K 线，只有 {n} 根"

    close = _series(df, "close")[-1] if n else None
    vals["atr_pct_14"] = round(a / close, 5) if (a is not None and close) else None
    if vals["atr_pct_14"] is None:
        why["atr_pct_14"] = why.get("atr_14", "最新收盘价缺失或为 0")

    p = percentile_of_last(atr_series(df, ATR_N), PCT_LOOKBACK)
    vals["atr_percentile_252"] = round(p, 4) if p is not None else None
    if p is None:
        why["atr_percentile_252"] = (
            f"需要至少 {PCT_LOOKBACK} 个有效 ATR 值（约 {PCT_LOOKBACK + ATR_N} 根 K 线），"
            f"只有 {n} 根")

    r = range_pct(df, RANGE_N)
    vals["range_pct_20"] = round(r, 5) if r is not None else None
    if r is None:
        why["range_pct_20"] = f"需要至少 {RANGE_N} 根 K 线，只有 {n} 根"

    rp = percentile_of_last(range_pct_series(df, RANGE_N), PCT_LOOKBACK)
    vals["range_pct_20_percentile_252"] = round(rp, 4) if rp is not None else None
    if rp is None:
        why["range_pct_20_percentile_252"] = (
            f"需要至少 {PCT_LOOKBACK} 个有效区间值（约 {PCT_LOOKBACK + RANGE_N} 根 K 线），"
            f"只有 {n} 根")

    nr = is_nr(df, NR_N)
    vals[f"is_nr{NR_N}"] = nr
    if nr is None:
        why[f"is_nr{NR_N}"] = f"需要至少 {NR_N} 根 K 线，只有 {n} 根"
    return vals, why
