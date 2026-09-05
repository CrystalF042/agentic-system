"""NaN 是第三种状态 —— **整个 v1 漏掉的那一种。**

## 这个文件修的是什么

v1 建立在一条规矩上：**算不出来必须是 `null`，而且必须有原因**，绝不能写成 0。
我处理了 `None`。**漏了 `NaN`。**

2026-09-02 全市场 502 只那一跑把它暴露了出来：

    cmf_20            p10 -0.031  p25 -0.326  p50 -0.355   ← 分位数不单调
    atr_pct_14        p10 0.0247  p25 0.0166  p50 0.0153   ← 同上
    rs_mkt_slope_20   p50 nan                              ← 直接印出了 nan

分位数在数学上必须单调递增，有三列不是。原因是 `sorted()` 遇到 NaN 会**静默**
给出乱序——NaN 和任何数比较都返回 False，排序的不变量直接失效，不报错：

    sorted([0.5, nan, 0.1, 0.9, 0.3, 0.7, 0.2])
    → [0.5, nan, 0.1, 0.2, 0.3, 0.7, 0.9]

## 更严重的那一半

NaN 会一路穿到冻结的 setup 判定里。验证过：一根缺量的 K 线 →
`cmf_20` / `obv_slope_20` / `up_down_volume_ratio_20` 全变 NaN →

    reasons 里什么都不写            NaN 不是 None，`is None` 检查漏过
    evaluate() 判 False            NaN > 0.1 静默返回 False
    unknown 是空的                 于是"算不出来"被记成了"不成立"

**这正是 v1 全部设计要防的形状，只是换了一种数据类型进来。**
后果很具体：任何一只票的面板里只要有一根 NaN，它就被静默排除在命中之外，
而卡片看起来完整——`1 / 502` 这个数是**下界，不是计数**。

## 为什么放在一个独立文件里

因为它必须是**穷举**的。逐个函数去包 `round(x, 4) if x is not None`，
新加一个度量就会漏掉一次，而漏掉不会报错。所以每个 `measure()` 在
最后统一调 `scrub()`，`observe()` 再兜一次底——加新字段也自动被覆盖。
"""
from __future__ import annotations

import math
from typing import Optional

NAN_REASON = "计算结果不是有限数（NaN/inf）—— 多半是面板里有缺失或异常的 K 线"


def finite(x) -> Optional[float]:
    """能用的数就原样返回，否则返回 `None`。

    **`None` / `NaN` / `±inf` 三者收成同一个出口。** 下游只需要判 `is None`，
    不必每处都记得再判一次 `math.isnan`——记不住是必然的。

    `bool` 刻意不当数处理：`is_nr7` 这类字段是布尔事实，不是数值。
    """
    if x is None or isinstance(x, bool):
        return None if x is None else x
    if isinstance(x, (int, float)):
        return x if math.isfinite(x) else None
    return x


def is_bad(x) -> bool:
    """这个值是不是"本来该是数、但不是有限数"。"""
    return isinstance(x, float) and not math.isfinite(x)


def scrub(vals: dict, why: dict, prefix: str = "") -> tuple[dict, dict]:
    """把一块度量里所有非有限数换成 `None`，并补上原因。

    递归一层：`accumulation_pressure_proxy` 是嵌套 dict，里面的数同样要洗。

    **返回的是同两个 dict（就地改）**，方便各模块 `return scrub(vals, why)`。
    """
    for k, v in list(vals.items()):
        if isinstance(v, dict):
            scrub(v, why, prefix=f"{k}.")
            continue
        if is_bad(v):
            vals[k] = None
            why.setdefault(f"{prefix}{k}", NAN_REASON)
    return vals, why


def panel_health(df) -> tuple[dict, list]:
    """面板进门体检。返回 (计数, 人话说明的问题清单)。

    **不修数据，只如实数。** 补一根插值出来的 K 线，会让所有度量都算得出来、
    而且看不出是补的——那比留一个 null 糟得多。

    体检四项，每一项都对应一种真实见过的脏数据：

        nan_rows        yfinance 偶尔返回缺 volume 或缺 close 的行
        nonpositive_volume   停牌日 volume=0，量比的分母会变成 0
        nonpositive_close    价格为 0 或负（复权异常）
        inverted_bars   high < low，源头数据错位
    """
    counts = {"rows": 0, "nan_rows": 0, "nonpositive_volume": 0,
              "nonpositive_close": 0, "inverted_bars": 0}
    if df is None or not len(df):
        return counts, ["面板为空"]
    counts["rows"] = int(len(df))
    cols = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
    for _i, row in enumerate(df[cols].itertuples(index=False)):
        vals = list(row)
        if any(is_bad(float(v)) if isinstance(v, (int, float)) else v is None for v in vals):
            counts["nan_rows"] += 1
        try:
            d = dict(zip(cols, [float(v) for v in vals]))
        except (TypeError, ValueError):
            continue
        if "volume" in d and math.isfinite(d["volume"]) and d["volume"] <= 0:
            counts["nonpositive_volume"] += 1
        if "close" in d and math.isfinite(d["close"]) and d["close"] <= 0:
            counts["nonpositive_close"] += 1
        if ("high" in d and "low" in d and math.isfinite(d["high"])
                and math.isfinite(d["low"]) and d["high"] < d["low"]):
            counts["inverted_bars"] += 1

    problems = []
    if counts["nan_rows"]:
        problems.append(f"{counts['nan_rows']} 根 K 线含缺失值")
    if counts["nonpositive_volume"]:
        problems.append(f"{counts['nonpositive_volume']} 根成交量 ≤0（停牌？）")
    if counts["nonpositive_close"]:
        problems.append(f"{counts['nonpositive_close']} 根收盘价 ≤0")
    if counts["inverted_bars"]:
        problems.append(f"{counts['inverted_bars']} 根最高价 < 最低价（源数据错位）")
    return counts, problems
