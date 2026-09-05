"""v2 分流打分 —— **闸门决定有没有，家族分决定先看谁。**

## 为什么是"家族"，不是"指标"

上一版（`score-1.0.0`）把四个指标平铺着等权平均：

    participation   volume.days_rvol_over_1_5_of_20
    accumulation    volume.cmf_20
    location        price_structure.atr_to_nearest_zone_above
    relative        relative_strength.excess_mkt_63

**四个里两个来自 volume 块。** 文档写着"等权、不引入自由度"，实际上
量能拿到了 50% 的权重，结构和相对强度各 25%——**一个我没意识到自己在定的权重。**

更糟的是结构：那是一个平铺的元组，代码里没有任何东西说"这两个同族"。
以后谁往里加第五个量能指标，volume 就悄悄变成 60%，而且不会报错。

所以这一版把**家族**做成一等对象：

    Structure          价区距离 · 区间位置              看位置
    Volume             RVOL · 近20日放量天数            看参与度
    Accumulation       CMF · OBV 斜率 · 上下量比        看量价承接
    RelativeStrength   vs SPY · vs 板块 · RS 斜率       看相对强弱
    Volatility         ATR 分位 · 20日区间分位          看收缩/扩张

**家族之间等权（各 20%），家族内部成员等权。** 关键性质是：
**往一个家族里加指标，不改变这个家族的权重**——只稀释族内成员。
有一条用例专门钉这条。

## 等权仍然不是因为它最优

是因为**没有任何东西可以用来定权重**。靠远期收益定，就是在同一份历史上
再拟合一次；靠拍脑袋定，就多了四个自由参数。

而 2026-09-04 那次事件研究（157 个事件、116 个交易日）的结论是
**测不出效应**——所以更没有任何依据去偏袒某一族。**那份结果不许用来定权重。**

## volatility_extremeness：名字里必须带 extremeness

其他四族的方向都是被定义决定的：距价区越近越靠前、放量越多越靠前、
相对强度越高越靠前。**波动没有这种方向**——高波动和低波动谁更值得看，
没有不带假设的答案。

所以这一族用**非方向**的聚合：`|分位 − 0.5| × 2`，即"离典型状态有多远"。
理由是筛子的职责是**把不寻常的东西端上来**，而两个方向的极端都算不寻常
（极度收缩可能是 compression，极度放大可能是 expansion / 有事发生）。
`percentile = 0.05` 和 `0.95` 都得 0.9，**这正是想要的**。

**族名叫 `volatility_extremeness`，不叫 `volatility_strength` / `_quality`。**
她定的：叫强度或质量，早晚有人把 0.9 读成"高波动是利好"。
名字是这里唯一防得住误读的东西——分数本身长得完全一样。

## NR7 为什么不在分里（这条是被单独钉住的，不是碰巧）

NR7 只代表**收缩这一端**。族里其他成员是双边异常，把一个单边证据
`+1` 进去，整族会天然偏向 compression——**混了方向，还看不出来混了。**

干净的做法是把它拆成 `compression evidence` / `expansion evidence`
两个单边量，让本族只表示"异不异常"。v1 不做这个拆分，
所以 NR7 **继续显示在 Signal Card 上，但不进分**。
`EXCLUDED_FROM_SCORE` 把这条连同理由一起写死，有一条用例钉着——
以后谁"顺手"把它加进来，会红。

## 分数不是概率，也不是预期收益

它是**同日横截面上的相对位置**。0.78 的意思是"在今天全市场里，
这只票在这五族的平均分位上排得比较靠前"，不是"78% 概率上涨"，
更不是"配 78% 仓位"。

## 覆盖度必须和分数一起出现

缺的族不进总分、剩下的重新等权——这是对的，但**只做到这里就出事了**：

    A  5/5 族  → 0.78
    B  2/5 族  → 0.82

B 的 0.82 是两族的平均，它对另外三族**什么都没说**。两个数印在一起，
人会当成同质的分数比较，而它们不是。所以 `families_used` / `coverage`
和分数是**一个整体**，输出里它们必须同框——`describe()` 与 `today_line()`
都把覆盖度印在分数旁边。

低于 `MIN_FAMILIES` 时 **`score = None`**，不给一个看起来很精确的数。
这和"缺失 ≠ 0"是同一条原则：**信息不够时的正确输出是"说不出"，
不是一个漂亮的高分。** 这样的票仍然通过闸门、仍然显示，只是没有排名。

## 分档只是标签，不是闸门

`WATCH / REVIEW / HIGH` 挂在**通过闸门之后**。它们不能当进入条件——
家族分是分位平均，中位数恒在 0.5 附近，">0.5" 每天都是半个市场，
**永远说不出"今天没有"**。而"必须能说今天没有"是 v1 的第一条边界。

## 结果不许回流

`backtest.py` 的结论不允许改这里的任何东西。有一条探针钉住：
本模块不许 import backtest。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Optional

from .numbers import finite
from .setups import SETUP_ID, SETUP_VERSION, evaluate

SCORE_VERSION = "score-2.1.0"
"""**1.0.0 → 2.0.0：结构变了。2.0.0 → 2.1.0：语义变了。**

1.0.0 是四个平铺指标等权（量能实际占 50%）；2.0.0 是五个家族等权、
族内成员等权。同一天同一只票的分数和排名都会不同，两版不能混着比。

2.1.0 改了两件会改变输出的事，所以版本必须动：
`volatility` 更名 `volatility_extremeness`（族名进指纹），
以及**覆盖度低于 `MIN_FAMILIES` 时分数为 None**——2.0.0 下会给出
一个数的票，2.1.0 下可能没有分数。这不是数变了，是"什么时候敢报分"变了。
"""

HIGHER, LOWER, UNUSUAL = "higher", "lower", "unusual"
"""成员的方向。`UNUSUAL` 表示**没有方向**——离中位数越远越靠前。"""


@dataclass(frozen=True)
class Member:
    name: str
    block: str
    field: str
    direction: str = HIGHER


@dataclass(frozen=True)
class Family:
    name: str
    members: tuple
    in_score: bool = True
    """False 表示算出来、显示给人看，但**不进总分**。"""


FAMILIES = (
    Family("structure", (
        Member("zone_distance", "price_structure", "atr_to_nearest_zone_above", LOWER),
        Member("range_position", "price_structure", "position_in_range_252", HIGHER),
    )),
    Family("volume", (
        Member("rvol", "volume", "rvol_20", HIGHER),
        Member("spike_days", "volume", "days_rvol_over_1_5_of_20", HIGHER),
    )),
    Family("accumulation", (
        Member("cmf", "volume", "cmf_20", HIGHER),
        Member("obv_slope", "volume", "obv_slope_20", HIGHER),
        Member("up_down_volume", "volume", "up_down_volume_ratio_20", HIGHER),
    )),
    Family("relative_strength", (
        Member("excess_mkt_63", "relative_strength", "excess_mkt_63", HIGHER),
        Member("excess_sector_63", "relative_strength", "excess_sector_63", HIGHER),
        Member("rs_slope", "relative_strength", "rs_mkt_slope_20", HIGHER),
    )),
    Family("volatility_extremeness", (
        Member("atr_percentile", "volatility", "atr_percentile_252", UNUSUAL),
        Member("range_percentile", "volatility", "range_pct_20_percentile_252", UNUSUAL),
    )),
)

EXCLUDED_FROM_SCORE = (
    ("volatility", "is_nr7",
     "NR7 只代表收缩这一端。本族是双边异常，加一个单边证据会让整族"
     "天然偏向 compression —— 混了方向，而且看不出来混了。"
     "要用它就得先拆成 compression / expansion 两个单边量，v1 不做。"
     "**它继续显示在 Signal Card 上，只是不进分。**"),
)
"""**明确不进分的字段，连同理由。** 有一条用例钉着：出现在 `FAMILIES` 里即红。

一个字段"碰巧没被加进来"和"经过判断决定不加"，在代码里长得一模一样——
只有写下来，下一个人才知道动它要先回答什么。
"""

MIN_FAMILIES = 3
"""进总分的族至少要有几族算得出来，否则**不报分**。

**这是第二个判断，和 `UNUSUAL` 一样请你复核。** 五族里少于三族，
缺掉的已经不是少数派证据了；那时给出的"0.82"是两族的平均，
它对另外三族什么都没说，却和 5/5 的 0.78 印在同一列里。

低了就 `score = None`：**信息不够时的正确输出是"说不出"。**
这样的票仍然通过闸门、仍然显示、仍然带着各族分——只是没有分数、没有排名。
"""

ATTENTION_BUDGET = 5
"""每天最多推几条。**一个关于人的注意力的决定，不是拟合出来的参数。**

它只影响"忙的日子截断在哪"，不影响"今天有没有"——后者由闸门决定。
"""

BANDS = ((0.85, "HIGH"), (0.70, "REVIEW"), (0.50, "WATCH"), (0.0, "LOW"))
"""**通过闸门之后**的优先级标签。**不是进入条件** —— 见模块开头。"""


def params_fingerprint() -> str:
    raw = ";".join(
        f"{f.name}|{int(f.in_score)}|" + ",".join(
            f"{m.name}:{m.block}.{m.field}:{m.direction}" for m in f.members)
        for f in FAMILIES)
    raw += f"#min_families={MIN_FAMILIES}"
    raw += "#excluded=" + ",".join(f"{b}.{f}" for b, f, _ in EXCLUDED_FROM_SCORE)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


FROZEN_FINGERPRINT = "518a85faa7ed1209"
"""改家族、成员、方向、覆盖度下限或排除名单，都必须同时升 `SCORE_VERSION`。"""


@dataclass
class Ranked:
    """一只票在某一天的分流结果。"""

    symbol: str = ""
    as_of: str = ""
    passed_gate: bool = False
    """通过 v1 绝对闸门没有。**没通过的不进排名。**"""
    score: Optional[float] = None
    """进入总分的家族分的等权平均（0~1）。一族都算不出来时为 None。"""
    band: str = ""
    """WATCH / REVIEW / HIGH —— **标签，不是闸门。**"""
    families: dict = field(default_factory=dict)
    """每一族的分数，**逐族列出**。一个总分说不清是哪一族在动。"""
    members: dict = field(default_factory=dict)
    """每个成员的横截面分位，用于追到底是哪个指标。"""
    missing: dict = field(default_factory=dict)
    """{族名: [算不出来的成员]}。**缺了什么要看得见。**"""
    families_used: int = 0
    """进总分的族里，实际算出来的有几族。**必须和分数一起出现。**"""
    families_possible: int = 0
    coverage: Optional[float] = None
    """`families_used / families_possible`。2/5 的 0.82 和 5/5 的 0.78 不同质。"""
    no_score_reason: str = ""
    """有分不出分的原因。**"没有分数"和"分数很低"必须分得开。**"""
    rank: Optional[int] = None
    within_budget: bool = False
    setup_id: str = SETUP_ID
    lineage: tuple = ()

    def to_dict(self) -> dict:
        return {"symbol": self.symbol, "as_of": self.as_of,
                "passed_gate": self.passed_gate, "score": self.score,
                "band": self.band, "families": self.families,
                "members": self.members, "missing": self.missing,
                "families_used": self.families_used,
                "families_possible": self.families_possible,
                "coverage": self.coverage, "no_score_reason": self.no_score_reason,
                "rank": self.rank, "within_budget": self.within_budget,
                "setup_id": self.setup_id, "lineage": list(self.lineage)}


def _percentile_ranks(values: list, direction: str) -> list:
    """横截面分位（0~1）。`None` 原样返回 `None`。

    并列取平均秩——同分的票必须拿同一个分位，否则字母序会悄悄变成
    一个打分维度。

    `UNUSUAL` 的处理是 `|分位 − 0.5| × 2`：两端都靠前，中间靠后。
    """
    idx = [i for i, v in enumerate(values) if v is not None]
    if not idx:
        return [None] * len(values)
    order = sorted(idx, key=lambda i: (values[i], i))
    out: list = [None] * len(values)
    n = len(order)
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        pct = ((i + j) / 2.0) / (n - 1) if n > 1 else 0.5
        for k in range(i, j + 1):
            if direction == LOWER:
                out[order[k]] = 1.0 - pct
            elif direction == UNUSUAL:
                out[order[k]] = abs(pct - 0.5) * 2.0
            else:
                out[order[k]] = pct
        i = j + 1
    return out


def band_of(score: Optional[float]) -> str:
    if score is None:
        return ""
    for floor, label in BANDS:
        if score >= floor:
            return label
    return "LOW"


def rank_day(cards: list, budget: int = ATTENTION_BUDGET) -> list:
    """一天的全部卡片 → 分流结果（按分数降序）。**纯函数。**

    分位在**当天全部卡片**上算，不是只在通过闸门的票里算——
    "它在全市场排第几"才是可比的量。
    """
    out = [Ranked(symbol=c.symbol, as_of=c.as_of_effective) for c in cards]
    for r, c in zip(out, cards):
        ev = evaluate(c)
        r.passed_gate = bool(ev["hit"])
        r.lineage = (ev.get("setup_version", SETUP_VERSION), SCORE_VERSION,
                     params_fingerprint())

    for fam in FAMILIES:
        for m in fam.members:
            vals = [finite(getattr(c, m.block).get(m.field)) for c in cards]
            for r, p in zip(out, _percentile_ranks(vals, m.direction)):
                if p is None:
                    r.missing.setdefault(fam.name, []).append(m.name)
                else:
                    r.members[m.name] = round(p, 4)

    for r in out:
        for fam in FAMILIES:
            got = [r.members[m.name] for m in fam.members if m.name in r.members]
            # **族内等权，缺的成员不算**（不补 0、不补 0.5）——
            # 补 0 把"没数据"变成"很差"，补 0.5 变成"中性"，都是凭空的结论。
            r.families[fam.name] = round(sum(got) / len(got), 4) if got else None
        # **族间等权。往一族里加指标不改变这一族的权重**，只稀释族内成员。
        fam_scores = [r.families[f.name] for f in FAMILIES
                      if f.in_score and r.families.get(f.name) is not None]
        r.families_possible = len([f for f in FAMILIES if f.in_score])
        r.families_used = len(fam_scores)
        r.coverage = (round(r.families_used / r.families_possible, 4)
                      if r.families_possible else None)
        # **覆盖度不够时不报分**——2/5 的平均对另外三族什么都没说，
        # 而它会和 5/5 的分数印在同一列里被当成同质的数比较。
        if not fam_scores:
            r.score, r.no_score_reason = None, "没有一族算得出来"
        elif r.families_used < MIN_FAMILIES:
            r.score = None
            r.no_score_reason = (f"覆盖度 {r.families_used}/{r.families_possible} "
                                 f"低于下限 {MIN_FAMILIES}/{r.families_possible}")
        else:
            r.score = round(sum(fam_scores) / len(fam_scores), 4)
        r.band = band_of(r.score)

    passed = [r for r in out if r.passed_gate and r.score is not None]
    passed.sort(key=lambda r: -r.score)
    for i, r in enumerate(passed, 1):
        r.rank = i
        r.within_budget = i <= budget
    return sorted(out, key=lambda r: (r.rank is None, r.rank or 0, -(r.score or 0)))


def today_line(ranked: list, budget: int = ATTENTION_BUDGET) -> str:
    """一句话结论。**"今天没有"必须是一个正常的、常见的输出。**

    **覆盖度和分数同框。** 2/5 族的 0.82 和 5/5 族的 0.78 不是同质的数，
    印在一起而不标覆盖度，人一定会横着比。
    """
    hit = [r for r in ranked if r.passed_gate]
    if not hit:
        return "今天没有通过闸门的标的。"
    unscored = [r for r in hit if r.score is None]
    shown = [r for r in hit if r.within_budget]
    # **超预算和没分数是两回事**，不能合成一个数——合起来就说不清
    # "没进队列"是因为今天太忙，还是因为这只票的信息本来就不够。
    overflow = [r for r in hit if r.score is not None and not r.within_budget]
    tail = (f"（另有 {len(overflow)} 只通过但超出今日注意力预算 {budget}）"
            if overflow else "")
    if unscored:
        tail += (f"（另有 {len(unscored)} 只通过闸门但覆盖度不足、不报分不排名："
                 + "、".join(r.symbol for r in unscored) + "）")
    if not shown:
        return f"今天 {len(hit)} 只通过闸门，但没有一只报得出分。" + tail
    return (f"今天 {len(hit)} 只通过闸门，按家族分排前 {len(shown)} 只："
            + "、".join(f"{r.symbol} {r.score:.2f}/{r.band}"
                        f"（{r.families_used}/{r.families_possible} 族）"
                        for r in shown) + tail)


def describe(r: Ranked) -> list:
    """把一条分流结果翻成人话。**分数不是概率，也不是预期收益。**"""
    if not r.passed_gate:
        return [f"{r.symbol}　未通过闸门"]
    if r.score is None:
        cov = f"覆盖度 {r.families_used}/{r.families_possible}"
        why = r.no_score_reason if cov in r.no_score_reason else f"{cov}　{r.no_score_reason}"
        out = [f"{r.symbol}　**没有分数**　{why}",
               "    （**没有分数 ≠ 分数很低。** 它通过了闸门，"
               "只是可用的信息不够，报不出一个横截面位置。）"]
    else:
        # **族覆盖度和项覆盖度是两个数，不能只印一个。**
        # 一族只要有一个成员算得出来就算"这一族有"，所以 5/5 族完全可能
        # 底下缺着成员。只印 5/5（100%）就是卡片自己打自己的脸——
        # 三行之后还写着"缺：relative_strength 少了 excess_mkt_63"。
        nmiss = sum(len(v) for v in r.missing.values())
        mtot = sum(len(f.members) for f in FAMILIES)
        out = [f"{r.symbol}　排名 {r.rank}　分数 {r.score}　{r.band}"
               f"　覆盖度 {r.families_used}/{r.families_possible} 族"
               f" · {mtot - nmiss}/{mtot} 项",
               "    （同日横截面上的相对位置。**不是概率、不是预期收益、不是仓位。**"
               "标签只在通过闸门之后生效，不是进入条件。"
               "**覆盖度不同的分数不能横着比。**）"]
    for fam in FAMILIES:
        v = r.families.get(fam.name)
        mark = "" if fam.in_score else "　（不进总分）"
        if v is None:
            out.append(f"    {fam.name:<24}—　整族算不出来{mark}")
            continue
        parts = "、".join(f"{m.name} {r.members[m.name]:.0%}"
                          for m in fam.members if m.name in r.members)
        out.append(f"    {fam.name:<24}{v:.0%}{mark}　　{parts}")
    for famname, miss in r.missing.items():
        out.append(f"    缺：{famname} 少了 {'、'.join(miss)}（该字段是 null）")
    return out
