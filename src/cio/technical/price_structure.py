"""价格结构 —— swing 点 → 按 ATR 归一聚类 → 价区。

## 这个模块回答的是哪个问题

CEO 给的原话是「四个峰值都在这里高度」。翻成可计算的东西：
**过去有没有若干次，价格到了差不多同一个位置就折返。** 就这一句，
不含任何"所以会怎样"。

## 参数是冻结的

    PIVOT_WINDOW      5          左右各 5 根都不高于/不低于它
    CLUSTER_ATR_MULT  0.5        两个 swing 价差 < 0.5×ATR20 就算同一个价区
    MIN_TOUCH_GAP     5          两次触点至少隔 5 根，否则算同一次
    MIN_TOUCHES       2          少于 2 次不成区

**为什么冻结。** 这四个数各试三种就是 81 套参数，在同一份历史上挑一套
"看起来最准"的，挑出来的是历史噪音的形状。这个项目在证券二部已经吃过
一次这个亏（因子搜索空间越放越大、结果越来越漂亮、样本外全废）。

所以：**改参数必须同时改 `ALGO_VERSION`。** 有一条探针把参数元组的哈希
和版本号绑在一起，只改参数不改版本会红。版本升上去之后，
旧卡片仍然带着旧版本号——历史结论不会被静默改写。

## 未来函数在这里有一个很隐蔽的入口

一个 swing high 要成立，**需要它右边也有 5 根 K 线**。也就是说
最近 5 根 K 线上的高点，今天还不知道是不是 pivot。

如果不管这件事，把整段历史扫一遍找 pivot，然后说"这是截止今天的价区"，
就用到了 as_of 之后的信息。图上完全看不出来，回放测试会一路变绿，
**而实盘那 5 天你根本没有这条线**。

所以 `swings()` 只返回**在 as_of 当天已经被确认**的 pivot。
代价是最近 5 根永远不参与——这是诚实的代价，不是缺陷。
"""
from __future__ import annotations

import hashlib
from typing import Optional

from .numbers import scrub
from .volatility import ATR_ZONE_N, atr

PIVOT_WINDOW = 5
CLUSTER_ATR_MULT = 0.5
MIN_TOUCH_GAP = 5
MIN_TOUCHES = 2
LOOKBACK = 252
"""找 swing 的回看长度（约一年）。"""

ALGO_VERSION = "sr-1.0.0"
"""**改上面五个参数中的任何一个，都必须改这个版本号。**

`params_fingerprint()` 把参数拍成一个哈希，探针拿它和
`FROZEN_FINGERPRINT` 比对——只改参数不改版本，测试会红。
"""

FROZEN_FINGERPRINT = "ccc9ecfca215e9b0"
"""`params_fingerprint()` 在 sr-1.0.0 参数下的值。**它红了不是让你改这个常量**，
是让你回答一个问题：参数动了，版本号跟着动了吗？"""


def params_fingerprint() -> str:
    raw = f"{PIVOT_WINDOW}|{CLUSTER_ATR_MULT}|{MIN_TOUCH_GAP}|{MIN_TOUCHES}|{LOOKBACK}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _col(df, name):
    return [float(x) for x in df[name].tolist()]


def swings(df, window: int = PIVOT_WINDOW) -> tuple[list, list]:
    """返回 (高点 pivot, 低点 pivot)，每个元素是 (下标, 价格)。

    **只返回已确认的 pivot。** 下标 i 的 pivot 要到 i+window 才成立，
    所以最后 `window` 根 K 线上的极值不在结果里——今天还不知道它是不是。

    并列的处理：左边要求**严格**大于、右边允许相等。这样一段平顶
    只会产生一个 pivot（最左那根），不会产生一串。规则是任选的，
    但必须写下来——不写的话，换个人改一次比较符号，价区数量就变了，
    而且没有任何东西会报错。
    """
    if df is None or len(df) < 2 * window + 1:
        return [], []
    high, low = _col(df, "high"), _col(df, "low")
    n = len(df)
    ph, pl = [], []
    for i in range(window, n - window):
        left, right = range(i - window, i), range(i + 1, i + window + 1)
        if all(high[j] < high[i] for j in left) and all(high[j] <= high[i] for j in right):
            ph.append((i, high[i]))
        if all(low[j] > low[i] for j in left) and all(low[j] >= low[i] for j in right):
            pl.append((i, low[i]))
    return ph, pl


def _cluster(points: list, tol: float) -> list:
    """把价格相近的 swing 归成一组。贪心，按价格排序后逐点并入。

    `tol` 是绝对价差（0.5×ATR20），不是百分比——**同一个百分比在
    高波动和低波动的票上完全不是一回事**，ATR 归一就是为了这个。
    """
    if not points or tol <= 0:
        return []
    pts = sorted(points, key=lambda p: p[1])
    groups, cur = [], [pts[0]]
    for p in pts[1:]:
        if p[1] - cur[-1][1] < tol:
            cur.append(p)
        else:
            groups.append(cur)
            cur = [p]
    groups.append(cur)
    return groups


def _count_touches(group: list, min_gap: int = MIN_TOUCH_GAP) -> list:
    """同一组里，间隔小于 min_gap 根的算**一次**触碰。

    连续三天在同一个价位打转是一次触碰，不是三次。不去重的话
    `MIN_TOUCHES` 形同虚设——任何一次盘整都会凑够触点数。
    """
    kept = []
    for idx, price in sorted(group, key=lambda p: p[0]):
        if not kept or idx - kept[-1][0] >= min_gap:
            kept.append((idx, price))
    return kept


def zones(df, lookback: int = LOOKBACK) -> tuple[list, dict]:
    """价区。返回 (zones, reasons)。

    每个 zone：
        low / high      价区上下沿（组内最低价、最高价）
        mid             中值
        touches         去重后的触碰次数
        first_idx/last_idx   第一次、最后一次触碰的下标（相对传入面板）
        kind            "high" 由高点构成 / "low" 由低点构成
        side            "above" / "below" —— 相对最新收盘的位置，**只是位置**

    `side` 刻意不叫别的名字。价区在价格上方还是下方是一个可核对的事实；
    它会不会挡住价格是一个观点，v1 不出观点。
    """
    why: dict = {}
    n = 0 if df is None else len(df)
    need = 2 * PIVOT_WINDOW + 1
    if n < need:
        why["zones"] = f"需要至少 {need} 根 K 线才能确认一个 pivot，只有 {n} 根"
        return [], why
    win = df.tail(lookback).reset_index(drop=True) if n > lookback else df
    tol_atr = atr(win, ATR_ZONE_N)
    if tol_atr is None:
        why["zones"] = f"算不出 ATR{ATR_ZONE_N}（需要 {ATR_ZONE_N + 1} 根，只有 {len(win)} 根）"
        return [], why
    tol = CLUSTER_ATR_MULT * tol_atr
    close = _col(win, "close")[-1]
    ph, pl = swings(win, PIVOT_WINDOW)
    if not ph and not pl:
        why["zones"] = (f"回看窗口内没有已确认的 swing 点"
                        f"（最后 {PIVOT_WINDOW} 根还不能确认）")
        return [], why

    out = []
    for kind, pts in (("high", ph), ("low", pl)):
        for g in _cluster(pts, tol):
            touches = _count_touches(g)
            if len(touches) < MIN_TOUCHES:
                continue
            prices = [p for _i, p in touches]
            lo, hi = min(prices), max(prices)
            mid = (lo + hi) / 2
            out.append({
                "low": round(lo, 6), "high": round(hi, 6), "mid": round(mid, 6),
                "touches": len(touches), "kind": kind,
                "first_idx": touches[0][0], "last_idx": touches[-1][0],
                "side": "above" if mid > close else "below",
            })
    out.sort(key=lambda z: (-z["touches"], abs(z["mid"] - close)))
    if not out:
        why["zones"] = (f"有 swing 点但没有任何一组凑够 {MIN_TOUCHES} 次触碰"
                        f"（触碰间隔需 ≥{MIN_TOUCH_GAP} 根）")
    return out, why


def measure(df) -> tuple[dict, dict]:
    """整块价格结构度量。返回 (值, 说不出来的原因)。"""
    vals: dict = {}
    why: dict = {}
    n = 0 if df is None else len(df)

    z, zwhy = zones(df)
    why.update(zwhy)
    vals["algo_version"] = ALGO_VERSION
    vals["zones"] = z
    vals["zones_above"] = [x for x in z if x["side"] == "above"]
    vals["zones_below"] = [x for x in z if x["side"] == "below"]

    close = float(df["close"].iloc[-1]) if n else None
    a20 = atr(df, ATR_ZONE_N)

    def _dist(zs):
        """到最近价区的距离，**以 ATR 为单位**。没有价区就是 None，不是 0。

        0 的含义是"价格正好在价区上"，和"没有价区"是完全相反的两件事。
        """
        if not zs or close is None or not a20:
            return None
        return round(min(abs(x["mid"] - close) for x in zs) / a20, 3)

    vals["atr_to_nearest_zone_above"] = _dist(vals["zones_above"])
    if vals["atr_to_nearest_zone_above"] is None:
        why["atr_to_nearest_zone_above"] = (
            "上方没有成立的价区" if a20 and close is not None
            else why.get("zones", "ATR20 或收盘价缺失"))
    vals["atr_to_nearest_zone_below"] = _dist(vals["zones_below"])
    if vals["atr_to_nearest_zone_below"] is None:
        why["atr_to_nearest_zone_below"] = (
            "下方没有成立的价区" if a20 and close is not None
            else why.get("zones", "ATR20 或收盘价缺失"))

    # 位置：现价在最近 252 根的高低区间里处于什么位置（0~1）。
    # 这是「四个峰值都在这里高度」的另一半——**现在离那个高度多远**。
    for k, lb in (("position_in_range_252", 252), ("position_in_range_63", 63)):
        if n < lb:
            vals[k] = None
            why[k] = f"需要 {lb} 根 K 线，只有 {n} 根"
            continue
        hi = max(_col(df, "high")[-lb:])
        lo = min(_col(df, "low")[-lb:])
        vals[k] = round((close - lo) / (hi - lo), 4) if hi > lo else None
        if vals[k] is None:
            why[k] = "区间内最高价等于最低价（无波动），位置无定义"
    return scrub(vals, why)


def observed_pivot_cutoff(n_rows: int) -> Optional[int]:
    """as_of 当天，最后一个**可能已确认**的 pivot 的下标。

    单独暴露出来是为了让"最后 5 根不算数"这件事可以被断言，
    而不是藏在 `swings()` 的循环边界里。
    """
    if n_rows < 2 * PIVOT_WINDOW + 1:
        return None
    return n_rows - PIVOT_WINDOW - 1
