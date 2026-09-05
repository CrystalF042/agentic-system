"""Signal Card 组装 —— **纯函数，只描述。**

## 契约

    observe(df, as_of=None, bench=None, sector_bench=None, ...) -> SignalCard

`df` 是一只票的 OHLCV 面板（列：date/open/high/low/close/volume），
`as_of` 是"截止到哪一天"。返回一张卡片。

**这个函数不做五件事**：不看时钟、不联网、不读写文件、不改输入面板、
不产生任何随机性。有探针逐条检查——包括扫源码里有没有 `datetime.now`
这类调用，以及跑完之后比对输入面板的哈希。

理由很实际：只有纯函数才能做回放。`observe(df[:t])` 必须逐字段等于
`observe(df, as_of=t)`，这一条断言能抓住**任何**未来函数，
而未来函数是这类系统里最贵、最难看出来的缺陷——它让回测一路变绿。

## `as_of` 是怎么生效的

按 date 列截断，**不是按行数**。传一个不在面板里的日期（周末、停牌日）
就取它之前最后一个交易日，并把真正用到的那天写进 `as_of_effective`。
这两个字段必须都在卡片上：你问的是哪天、我答的是哪天。

## null 不是 0

卡片里任何一个算不出来的字段都是 `null`，并且在 `reasons` 里有一条
说明为什么。构造函数会**强制**这件事：有 null 而没有对应 reason
会直接抛异常，不是警告。

写 0 的后果很具体："这只票近 20 日资金流入为 0" 和
"这只票只有 15 天数据所以算不了" 在下游长得一模一样，
而前者是一个结论、后者是没有结论。

## v1 不接任何东西

不改关注池、不触发一部、不动闸门、不发消息、不打分、不产候选。
这个模块不 import 任何一个业务模块，探针会检查。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Optional

from . import SCHEMA_VERSION
from . import price_structure, relative_strength, volatility, volume
from .numbers import panel_health, scrub

MIN_ROWS = 2
"""低于这个行数连"最新收盘价"都谈不上，直接给一张空卡片（**不是抛异常**）：
全市场扫描时新上市的票就是这样，不该中断整轮。"""


@dataclass
class SignalCard:
    """一只票在某一天的技术面**描述**。

    字段分五块，和五个模块一一对应。加字段不用升 `schema_version`，
    **改字段含义必须升**。
    """

    symbol: str = ""
    as_of: str = ""
    """调用方问的是哪一天。"""
    as_of_effective: str = ""
    """实际用到的最后一个交易日。周末/停牌时它和 `as_of` 不同。"""
    rows_used: int = 0
    last_close: Optional[float] = None
    schema_version: str = SCHEMA_VERSION
    algo_version: str = price_structure.ALGO_VERSION

    price_structure: dict = field(default_factory=dict)
    volume: dict = field(default_factory=dict)
    relative_strength: dict = field(default_factory=dict)
    volatility: dict = field(default_factory=dict)
    panel_health: dict = field(default_factory=dict)
    """面板体检：缺失行、零成交量、负价、上下影错位各有几根。

    **不修数据，只如实数。** 补一根插值出来的 K 线会让所有度量都算得出来、
    而且看不出是补的——那比留一个 null 糟得多。"""
    reasons: dict = field(default_factory=dict)
    """**每一个 null 字段在这里都必须有一条。** 见 `_check_nulls`。"""

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol, "as_of": self.as_of,
            "as_of_effective": self.as_of_effective, "rows_used": self.rows_used,
            "last_close": self.last_close,
            "schema_version": self.schema_version, "algo_version": self.algo_version,
            "price_structure": self.price_structure, "volume": self.volume,
            "relative_strength": self.relative_strength, "volatility": self.volatility,
            "panel_health": self.panel_health, "reasons": self.reasons,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


def card_fields_fingerprint(card: SignalCard) -> str:
    """卡片全部字段名（含四个块内部的字段）的指纹。**只看名字，不看值。**

    语义变了没法自动检测——`rs_mkt_samples` 从"对齐了几天"改成"几天能用"，
    字段名一个字母都没动，而同一份输入会给出不同的数（build114 就是这样，
    **我当时没升 `SCHEMA_VERSION`**）。

    但**字段集变了可以检测**。所以这条指纹钉住的是那一半：
    加或删任何一个字段都会红。红了不是让你改常量，是让你回答一句——
    **这次改动要不要升 `SCHEMA_VERSION`？**
    """
    d = card.to_dict()
    names = []
    for k in sorted(d):
        if k == "reasons":
            # **`reasons` 的键是随卡片变的**（哪个字段算不出来就记哪个），
            # 它是诊断侧信道，不是字段契约的一部分。放进指纹会让同一版代码
            # 在不同的票上给出不同的指纹——那样这条探针每天都红，也就等于没有。
            names.append(k)
            continue
        v = d[k]
        if isinstance(v, dict):
            names += [f"{k}.{kk}" for kk in sorted(v)]
        else:
            names.append(k)
    return hashlib.sha256("|".join(names).encode()).hexdigest()[:16]


FROZEN_FIELDS_FINGERPRINT = "518ee3b0f5107cb2"
"""它红了，先回答：**这次是加字段（可以不升版本），还是改语义（必须升）？**"""


def _nulls(block: dict) -> list:
    return [k for k, v in block.items() if v is None]


def _check_nulls(card: SignalCard) -> list:
    """返回**有 null 却没写原因**的字段名。空列表才算合格。"""
    missing = []
    for name in ("price_structure", "volume", "relative_strength", "volatility"):
        for k in _nulls(getattr(card, name)):
            if k not in card.reasons:
                missing.append(f"{name}.{k}")
    if card.last_close is None and "last_close" not in card.reasons:
        missing.append("last_close")
    return missing


def slice_as_of(df, as_of: Optional[str]):
    """按 date 截断到 as_of（含当天）。返回 (子面板, 实际用到的日期)。

    **按日期，不按行数。** 按行数截断在停牌、半日市、数据源缺一天的时候
    会静默地取到另一天——回放测试照样绿。
    """
    if df is None or not len(df):
        return df, ""
    dates = [str(d)[:10] for d in df["date"].tolist()]
    if not as_of:
        return df, dates[-1]
    keep = [i for i, d in enumerate(dates) if d <= str(as_of)[:10]]
    if not keep:
        return df.iloc[0:0], ""
    end = keep[-1]
    return df.iloc[:end + 1].reset_index(drop=True), dates[end]


def observe(df, as_of: Optional[str] = None, bench=None, sector_bench=None,
            symbol: str = "", sector_symbol: str = "",
            strict: bool = True) -> SignalCard:
    """一只票 → 一张 Signal Card。**纯函数。**

    `strict=True`（默认）时，若有 null 字段缺原因就抛 `AssertionError`。
    这是刻意的：宁可开发期崩，也不要产出一张"看起来完整"的卡片。
    """
    sub, eff = slice_as_of(df, as_of)
    # 基准也截到同一天。**但真正拦住未来数据的不是这两行**，是
    # `relative_strength.align()` 按日期取交集——个股已经截断了，
    # 交集自然不会含有 as_of 之后的日子。
    #
    # 这不是废话：变异测试里把这两行删掉，24 条用例全绿（等价变异）。
    # 它们留着是第二道，而**第一道是日期对齐**——所以那个函数不能改成按位置配对，
    # 那会同时废掉对齐和这层防护。`t_benchmark_cannot_leak_future` 钉的是结果，
    # 不管是哪一道拦住的。
    bsub, _ = slice_as_of(bench, as_of) if bench is not None else (None, "")
    ssub, _ = slice_as_of(sector_bench, as_of) if sector_bench is not None else (None, "")

    card = SignalCard(symbol=symbol, as_of=str(as_of or "")[:10] or eff,
                      as_of_effective=eff, rows_used=0 if sub is None else len(sub))
    if sub is None or len(sub) < MIN_ROWS:
        card.reasons["last_close"] = f"可用 K 线 {card.rows_used} 根，不足 {MIN_ROWS} 根"
        card.reasons["all_blocks"] = "数据不足，本卡片没有任何度量值"
        return card

    card.last_close = float(sub["close"].iloc[-1])

    for name, (vals, why) in (
        ("price_structure", price_structure.measure(sub)),
        ("volume", volume.measure(sub)),
        ("volatility", volatility.measure(sub)),
        ("relative_strength", relative_strength.measure(
            sub, bsub, ssub, sector_symbol=sector_symbol)),
    ):
        setattr(card, name, vals)
        card.reasons.update(why)

    # **面板进门体检。** NaN 不会报错，但它会静默地把度量变成 NaN、
    # 把 setup 条件判成 False，而 `unknown` 里什么都不写——2026-09-02
    # 全市场 502 只那一跑就是这么坏的。见 `numbers.py`。
    card.panel_health, problems = panel_health(sub)
    if problems:
        card.reasons["panel_health"] = "；".join(problems)

    # 兜底再洗一遍：各 measure 已经各自 scrub 过，这一道是为了
    # **以后有人加新度量时不会漏**——漏了不报错，正是要防的。
    for name in ("price_structure", "volume", "relative_strength", "volatility"):
        scrub(getattr(card, name), card.reasons)

    card.algo_version = card.price_structure.get("algo_version", price_structure.ALGO_VERSION)
    bad = _check_nulls(card)
    if bad and strict:
        raise AssertionError(
            "这些字段是 null 但没写原因，卡片契约不允许（null 必须能被解释）：" + ", ".join(bad))
    return card


def describe(card: SignalCard) -> list:
    """把卡片翻成人话。**每一行都是可核对的事实，没有一句预测。**

    这是给人看的，不是给下游程序看的——程序读 `to_dict()`。
    """
    d = card.to_dict()
    out = [f"{card.symbol or '(未命名)'}　截至 {card.as_of_effective}"
           f"（用了 {card.rows_used} 根 K 线，收 {card.last_close:.2f}）"]
    if card.rows_used < MIN_ROWS:
        out.append("  数据不足，没有度量值")
        return out

    ps, vo, vl, rs = (d["price_structure"], d["volume"], d["volatility"],
                      d["relative_strength"])
    za, zb = ps.get("zones_above") or [], ps.get("zones_below") or []
    if za:
        z = za[0]
        out.append(f"  上方价区 {z['low']}~{z['high']}，历史触碰 {z['touches']} 次，"
                   f"距今 {ps.get('atr_to_nearest_zone_above')} 个 ATR")
    if zb:
        z = zb[0]
        out.append(f"  下方价区 {z['low']}~{z['high']}，历史触碰 {z['touches']} 次，"
                   f"距今 {ps.get('atr_to_nearest_zone_below')} 个 ATR")
    if not za and not zb:
        out.append(f"  没有成立的价区（{card.reasons.get('zones', '原因未记录')}）")

    pos = ps.get("position_in_range_252")
    if pos is not None:
        out.append(f"  现价处在近一年高低区间的 {pos:.0%} 位置")
    out.append(f"  今日量比 {vo.get('rvol_20')}，近 20 日有 "
               f"{vo.get('days_rvol_over_1_5_of_20')} 天量比≥{volume.RVOL_SPIKE}")
    out.append(f"  上下量比 {vo.get('up_down_volume_ratio_20')}，"
               f"CMF20 {vo.get('cmf_20')}，OBV 斜率 {vo.get('obv_slope_20')}"
               f"　（量能代理，不是资金流向）")
    out.append(f"  ATR14 占价 {vl.get('atr_pct_14')}，"
               f"处在自身一年波动的 {vl.get('atr_percentile_252')} 分位；"
               f"20 日区间 {vl.get('range_pct_20')}")
    out.append(f"  相对 SPY：63 日超额 {rs.get('excess_mkt_63')}，"
               f"RS 斜率 {rs.get('rs_mkt_slope_20')}")
    out.append(f"  相对板块（{rs.get('sector_benchmark_symbol')}）：63 日超额 "
               f"{rs.get('excess_sector_63')}")
    return out
