"""量能 —— RVOL、OBV 斜率、CMF、上下量比。

## 先把一件事说清楚：日线看不见"资金流入"

CEO 转述的原话是「有没有持续的资金持续流入」。这个问题**用日线 OHLCV
回答不了**，任何声称能回答的指标都是在换一个说法：

    我们有的      每天的总成交量、开高低收
    我们没有      谁在买、单子多大、是不是同一个账户、是不是被动申赎

OBV / CMF / 上下量比全都是**把成交量按当天价格的涨跌分个正负**，
然后累加。它们度量的是"上涨日的量和下跌日的量谁大"，
这和"机构在建仓"之间隔着一整层不可观测。

所以这一组指标的名字叫 `accumulation_pressure_proxy` —— CEO 定的，
**`proxy` 那三个字母是这个模块最重要的部分**。绝不叫 institutional，
因为那会让下游（和人）以为我们看见了持仓。

## 为什么它不是一个分数

v1 的边界是"只描述、不打分"。把 CMF、OBV 斜率、上下量比揉成一个
0~100 的数，会立刻产生三个问题：权重是拍的、阈值是拍的、
而且一个数没法回溯到是哪个成分在动。

所以 `accumulation_pressure_proxy` 返回的是**一组并列的事实**，
不做加权。要不要合成、怎么合成，等有了验证数据再说。

## RVOL 用中位数，不用均值

均值会被自己想找的东西污染：一根 10 倍量的柱子会把 20 日均量抬高
两三成，于是"今天放量了吗"这个问题的分母里含着今天。
中位数对单根异常不敏感，问的才是"和平常比"。
"""
from __future__ import annotations

from typing import Optional

RVOL_N = 20
OBV_SLOPE_N = 20
CMF_N = 20
UPDOWN_N = 20
RVOL_SPIKE = 1.5
"""统计"最近 20 天里有几天放量"时用的门槛。**这是一个计数口径，不是信号。**"""


def _col(df, name):
    return [float(x) for x in df[name].tolist()]


def _median(xs: list) -> float:
    s = sorted(xs)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2


def rvol(df, n: int = RVOL_N) -> Optional[float]:
    """今天的量 ÷ 最近 n 天量的中位数（**不含今天**）。

    分母排除今天，否则放量当天会把自己的分母抬起来。
    """
    if df is None or len(df) < n + 1:
        return None
    vol = _col(df, "volume")
    base = _median(vol[-n - 1:-1])
    return float(vol[-1] / base) if base > 0 else None


def obv(df) -> list:
    """On-Balance Volume：收涨加量、收跌减量、**平盘不动**。

    平盘不动是 Granville 的原始定义。有些实现把平盘也按前一天方向计，
    那样在窄幅震荡里 OBV 会出现纯属人造的趋势。
    """
    close, vol = _col(df, "close"), _col(df, "volume")
    out, run = [0.0], 0.0
    for i in range(1, len(df)):
        if close[i] > close[i - 1]:
            run += vol[i]
        elif close[i] < close[i - 1]:
            run -= vol[i]
        out.append(run)
    return out


def obv_slope(df, n: int = OBV_SLOPE_N) -> Optional[float]:
    """最近 n 根 OBV 的最小二乘斜率，**除以同期平均成交量**。

    归一化之后单位是"每天净增几个平均成交量"，可以跨票比较；
    不归一化的话，一只日均 5000 万股的票和一只 50 万股的票，
    斜率差两个数量级，而那和资金方向无关。
    """
    if df is None or len(df) < n + 1:
        return None
    o = obv(df)[-n:]
    vol = _col(df, "volume")[-n:]
    avg = sum(vol) / len(vol)
    if avg <= 0:
        return None
    xs = list(range(n))
    mx, my = sum(xs) / n, sum(o) / n
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, o)) / den
    return float(slope / avg)


def cmf(df, n: int = CMF_N) -> tuple[Optional[float], int]:
    """Chaikin Money Flow。返回 (值, 被跳过的天数)。

    **被跳过的天数要返回出去。** high == low 的那天（停牌、涨跌停一字板、
    极端流动性缺失）分母是 0，业界实现一般直接记 0 —— 那等于说
    "那天资金净流入为零"，而真相是"那天这个指标没有定义"。
    在 A 股一字板上这不是罕见情况。
    """
    if df is None or len(df) < n:
        return None, 0
    high, low, close = _col(df, "high")[-n:], _col(df, "low")[-n:], _col(df, "close")[-n:]
    vol = _col(df, "volume")[-n:]
    mfv, tot, skipped = 0.0, 0.0, 0
    for h, l, c, v in zip(high, low, close, vol):
        if h <= l:
            skipped += 1
            continue
        mfv += ((c - l) - (h - c)) / (h - l) * v
        tot += v
    if tot <= 0:
        return None, skipped
    return float(mfv / tot), skipped


def up_down_volume_ratio(df, n: int = UPDOWN_N) -> Optional[float]:
    """最近 n 天，上涨日总量 ÷ 下跌日总量。

    **这是"资金流入"能被日线看到的最直白的形式**，也是它的上限：
    它只知道那天收涨还是收跌，不知道量是谁的。
    """
    if df is None or len(df) < n + 1:
        return None
    close, vol = _col(df, "close")[-n - 1:], _col(df, "volume")[-n - 1:]
    up = sum(v for i, v in enumerate(vol[1:], 1) if close[i] > close[i - 1])
    down = sum(v for i, v in enumerate(vol[1:], 1) if close[i] < close[i - 1])
    if down <= 0:
        return None
    return float(up / down)


def measure(df) -> tuple[dict, dict]:
    """整块量能度量。返回 (值, 说不出来的原因)。

    `accumulation_pressure_proxy` 是一组并列的事实，**不是一个分数**，
    也不做加权合成 —— 见模块开头。
    """
    vals: dict = {}
    why: dict = {}
    n = 0 if df is None else len(df)

    r = rvol(df, RVOL_N)
    vals["rvol_20"] = round(r, 3) if r is not None else None
    if r is None:
        why["rvol_20"] = f"需要 {RVOL_N + 1} 根 K 线（分母不含今天），只有 {n} 根"

    if n >= RVOL_N + 1:
        vol = _col(df, "volume")
        base = _median(vol[-RVOL_N - 1:-1])
        vals["days_rvol_over_1_5_of_20"] = (
            int(sum(1 for v in vol[-RVOL_N:] if base > 0 and v / base >= RVOL_SPIKE))
            if base > 0 else None)
        if vals["days_rvol_over_1_5_of_20"] is None:
            why["days_rvol_over_1_5_of_20"] = "近 20 日成交量中位数为 0"
    else:
        vals["days_rvol_over_1_5_of_20"] = None
        why["days_rvol_over_1_5_of_20"] = f"需要 {RVOL_N + 1} 根 K 线，只有 {n} 根"

    s = obv_slope(df, OBV_SLOPE_N)
    vals["obv_slope_20"] = round(s, 4) if s is not None else None
    if s is None:
        why["obv_slope_20"] = (f"需要 {OBV_SLOPE_N + 1} 根 K 线，只有 {n} 根"
                               if n < OBV_SLOPE_N + 1 else "同期平均成交量为 0")

    c, skipped = cmf(df, CMF_N)
    vals["cmf_20"] = round(c, 4) if c is not None else None
    vals["cmf_20_skipped_days"] = skipped
    if c is None:
        why["cmf_20"] = (f"需要 {CMF_N} 根 K 线，只有 {n} 根" if n < CMF_N
                         else f"窗口内有效成交量为 0（跳过 {skipped} 天：最高价=最低价）")
    elif skipped:
        why["cmf_20_skipped_days"] = (
            f"{skipped} 天最高价=最低价（一字板/停牌），这几天 CMF 无定义，已排除")

    u = up_down_volume_ratio(df, UPDOWN_N)
    vals["up_down_volume_ratio_20"] = round(u, 3) if u is not None else None
    if u is None:
        why["up_down_volume_ratio_20"] = (
            f"需要 {UPDOWN_N + 1} 根 K 线，只有 {n} 根" if n < UPDOWN_N + 1
            else "窗口内没有下跌日，比值无定义（**不是无穷大，也不是 0**）")

    # **名字里的 proxy 是这个模块最重要的部分。** 见模块开头。
    vals["accumulation_pressure_proxy"] = {
        "cmf_20": vals["cmf_20"],
        "obv_slope_20": vals["obv_slope_20"],
        "up_down_volume_ratio_20": vals["up_down_volume_ratio_20"],
        "days_rvol_over_1_5_of_20": vals["days_rvol_over_1_5_of_20"],
        "note": "日线 OHLCV 看不见持仓与单子归属；这四个数只说明"
                "上涨日与下跌日的成交量对比，不构成任何关于谁在交易的推断",
    }
    return vals, why
