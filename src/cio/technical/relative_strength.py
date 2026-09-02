"""相对强度 —— 相对大盘、相对板块。

## 为什么要两个基准

一只半导体股票涨 8%，可能是三件不同的事：

    大盘涨了 8%          它什么也没做
    大盘平、板块涨 8%    是板块的事，不是这家公司的事
    大盘平、板块平       才是这只票自己的事

只对 SPY 做超额，第二种和第三种分不开。所以板块基准是必需的，
不是锦上添花。

## 日期必须对齐，不能靠位置

两个面板的行数相同**不等于**日期相同：停牌、半日市、数据源缺一天，
都会让第 i 行不是同一天。按位置相减，算出来的"超额收益"里混着
日期错位——不报错、量级也正常，只是不再是超额收益。

所以这里一律先按 date 取交集再算，并且把**对齐后的样本数**写进卡片。
样本数明显小于窗口，就是这只票或基准缺数据。

## 基准缺失一律 null

取不到基准时**不退化成"相对自己"**，也不填 0。填 0 的意思是
"和大盘涨得一样多"，那是一个具体的、可能完全错误的结论；
null 的意思是"这一栏我们不知道"。这两件事必须在卡片上长得不一样。
"""
from __future__ import annotations

from typing import Optional

RS_SLOPE_N = 20
EXCESS_WINDOWS = (21, 63, 126)
RS_HIGH_N = 63

SECTOR_ETF = {
    "Information Technology": "XLK", "Health Care": "XLV", "Financials": "XLF",
    "Consumer Discretionary": "XLY", "Consumer Staples": "XLP", "Energy": "XLE",
    "Industrials": "XLI", "Materials": "XLB", "Utilities": "XLU",
    "Real Estate": "XLRE", "Communication Services": "XLC",
}
"""GICS 板块 → 板块 ETF。**只在取数脚本里用**，纯函数不碰它——
`measure()` 收的是已经取好的面板，这样它才能保持纯。"""


def _pairs(df):
    """(date 字符串, close) 列表。date 统一成字符串前 10 位，避开 tz 与类型差异。"""
    dates = [str(d)[:10] for d in df["date"].tolist()]
    close = [float(x) for x in df["close"].tolist()]
    return list(zip(dates, close))


def align(df, bench) -> tuple[list, list]:
    """按日期取交集。返回 (个股收盘序列, 基准收盘序列)，**等长且逐位同日**。"""
    if df is None or bench is None or not len(df) or not len(bench):
        return [], []
    b = dict(_pairs(bench))
    a_dates, a_close, b_close = [], [], []
    for d, c in _pairs(df):
        if d in b:
            a_dates.append(d)
            a_close.append(c)
            b_close.append(b[d])
    return a_close, b_close


def _ret(series: list, n: int) -> Optional[float]:
    if len(series) < n + 1 or series[-n - 1] <= 0:
        return None
    return series[-1] / series[-n - 1] - 1.0


def _slope_norm(series: list, n: int) -> Optional[float]:
    """最近 n 个点的最小二乘斜率 ÷ 窗口均值（→ 每天百分之几）。"""
    if len(series) < n:
        return None
    ys = series[-n:]
    my = sum(ys) / n
    if my == 0:
        return None
    xs = list(range(n))
    mx = sum(xs) / n
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
    return float(slope / my)


def _rs_block(df, bench, tag: str) -> tuple[dict, dict]:
    vals: dict = {}
    why: dict = {}
    a, b = align(df, bench)
    vals[f"rs_{tag}_samples"] = len(a)
    if len(a) < 2:
        for k in ([f"rs_{tag}_slope_20", f"rs_{tag}_new_high_{RS_HIGH_N}"]
                  + [f"excess_{tag}_{w}" for w in EXCESS_WINDOWS]):
            vals[k] = None
            why[k] = ("没有基准面板" if bench is None or not len(bench)
                      else f"与基准按日期对齐后只剩 {len(a)} 个交易日")
        return vals, why

    rs = [x / y for x, y in zip(a, b) if y > 0]
    s = _slope_norm(rs, RS_SLOPE_N)
    vals[f"rs_{tag}_slope_20"] = round(s, 5) if s is not None else None
    if s is None:
        why[f"rs_{tag}_slope_20"] = f"对齐后不足 {RS_SLOPE_N} 个交易日（有 {len(rs)} 个）"

    if len(rs) >= RS_HIGH_N:
        vals[f"rs_{tag}_new_high_{RS_HIGH_N}"] = bool(rs[-1] >= max(rs[-RS_HIGH_N:]))
    else:
        vals[f"rs_{tag}_new_high_{RS_HIGH_N}"] = None
        why[f"rs_{tag}_new_high_{RS_HIGH_N}"] = \
            f"对齐后不足 {RS_HIGH_N} 个交易日（有 {len(rs)} 个）"

    for w in EXCESS_WINDOWS:
        ra, rb = _ret(a, w), _ret(b, w)
        k = f"excess_{tag}_{w}"
        vals[k] = round(ra - rb, 5) if (ra is not None and rb is not None) else None
        if vals[k] is None:
            why[k] = f"对齐后不足 {w + 1} 个交易日（有 {len(a)} 个）"
    return vals, why


def measure(df, bench=None, sector_bench=None,
            sector_symbol: str = "") -> tuple[dict, dict]:
    """相对强度整块。`bench` 是大盘（SPY），`sector_bench` 是板块 ETF。

    两个都可以是 None —— 那一整组字段就是 null，**并且每个 null 都带原因**。
    """
    vals: dict = {"sector_benchmark_symbol": sector_symbol or None}
    why: dict = {}
    if not sector_symbol:
        why["sector_benchmark_symbol"] = "没有给这只票指定板块 ETF（GICS 板块缺失或不在映射表里）"
    for tag, bm in (("mkt", bench), ("sector", sector_bench)):
        v, w = _rs_block(df, bm, tag)
        vals.update(v)
        why.update(w)
    return vals, why
