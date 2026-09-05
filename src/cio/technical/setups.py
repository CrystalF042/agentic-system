"""Setup 定义与事件切分 —— **在看任何远期收益之前冻结。**

## 为什么这个文件必须先于事件研究存在

先跑收益、再回来调阈值，是这个项目已经吃过两次亏的做法：
证券二部的因子搜索空间越放越大、结果越来越漂亮、样本外全废；
材料闸门在自己的调参集上 67/67，换两只没见过的票只剩 3/8。

所以顺序是：**先把定义写死并说明每个数字的来历，再去看它有没有用。**
定义一旦冻结，改任何一个阈值都必须升版本号——`params_fingerprint()`
和 `FROZEN_FINGERPRINT` 绑在一起，只改数不改版本，测试会红。

## 三个条件为什么是这三个

CEO 转述的炒股逻辑，翻成三个互相独立的信息面：

    A  持续放量      participation      有没有人在参与
    B  量能代理改善   accumulation proxy 参与的方向偏哪一边
    C  贴近上方价区   location           价格站在什么位置

**它们彼此接近独立，这是设计意图，不是缺陷。** 如果三者高度相关，
那就是把同一个信息数了三遍。真正要问的不是"它们是不是比随机更常同时出现"，
而是"同时出现之后发生了什么"——后者是事件研究的事，本文件只负责定义。

## 每个数字的来历（都取自基础率表，且都在看收益之前定下）

    A ≥5 天        近20日放量天数的中位数是 2、p90 是 4；≥5 落在 90 分位以外。
                   选它的理由是"它在全市场是多罕见"，不是"它带来多少收益"。
    B CMF > 0.10   CMF20 的 p75 是 0.095，>0.10 约等于取上四分位。
      OBV 斜率 > 0  p50 是 0.0055，取 >0 只是"在改善"，不是"很强"。
    C ≤ 0.5 ATR    **这个数不是新参数。** 它就是价区算法里的聚类容差
                   `CLUSTER_ATR_MULT`——"距离价区不到一个聚类容差"，
                   等价于"已经贴在这个价区上"。不引入新的自由度。

真机 6 天 × 120 只的实测：A 8%、B 23%、C 22%，三条同时约每天 1–2 只
（换算到全市场 500 只）。**这个频率是这条 setup 唯一已知的性质。**

## 一次事件 ≠ 一个 stock-day

    周一  A∧B∧C = True     ← 事件开始
    周二  A∧B∧C = True     ← 同一个事件，不是新样本
    周三  A∧B∧C = True     ← 同上
    周四  False            ← 复位
    周五  True             ← 冷却期内，仍不算新事件

不切事件的话，250 天回放会造出大量高度重叠的"样本"：
5/10/20 日收益互相覆盖，看着几百个样本，独立信息远没有那么多。

**这正是 build100 在材料闸门上修过的同一个缺陷**（同一件事被转载三次
就顶开了闸门），换了个模块又出现了一次。所以这次写在定义里，不等它复发。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Optional

from .numbers import finite
from .price_structure import CLUSTER_ATR_MULT

SETUP_ID = "SETUP_VOLUME_ACCUMULATION_AT_ZONE_V1"
SETUP_VERSION = "setup-1.0.1"
"""**1.0.0 → 1.0.1：三个阈值一个都没改，但行为变了。**

1.0.0 下，面板里一根缺失的 K 线会让 `cmf_20` / `obv_slope_20` 变成 NaN，
而 `NaN > 0.10` 静默返回 False —— 于是"算不出来"被记成了"不成立"，
`unknown` 里还是空的。任何一只票只要有一根坏 K 线，就被无声地排除在命中之外。

1.0.1 起 NaN 统一收成 `None`（见 `numbers.py`），走 `unknown` 那条路。
**同一天同一只票，两个版本可能给出不同结果**，所以两版的事件不能混在一起统计
——这正是血统四元组存在的理由。参数指纹不变（阈值确实没动），
变的是 `SETUP_VERSION`。"""

A_MIN_SPIKE_DAYS = 5
B_MIN_CMF = 0.10
B_MIN_OBV_SLOPE = 0.0
C_MAX_ATR_TO_ZONE = CLUSTER_ATR_MULT
"""**刻意等于价区聚类容差，不是另取一个 0.5。** 见模块开头。"""

COOLDOWN_DAYS = 5
"""复位之后多少个交易日内再次成立仍算同一个事件。

取 5 与 `MIN_TOUCH_GAP` 同源：都是"隔得太近的两次算一次"。
量能类指标用的是 20 日重叠窗口，相隔一两天的两次触发共享 18–19 天输入，
当成两个独立样本是自欺。
"""

CONDITIONS = ("A_participation", "B_accumulation_proxy", "C_location")


def params_fingerprint() -> str:
    raw = (f"{A_MIN_SPIKE_DAYS}|{B_MIN_CMF}|{B_MIN_OBV_SLOPE}|"
           f"{C_MAX_ATR_TO_ZONE}|{COOLDOWN_DAYS}")
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


FROZEN_FINGERPRINT = "3b61f7d65bc7d9b2"
"""它红了不是让你改这个常量，是让你回答：阈值动了，`SETUP_VERSION` 跟着动了吗？"""


def evaluate(card) -> dict:
    """一张 Signal Card → 三个条件各自成立与否，以及交集。

    **缺值一律判 False，并记进 `unknown`。** 把"算不出来"当成"不成立"
    在计数上是对的（它确实没触发），但如果不把它单独记下来，
    "这只票不满足"和"这只票数据不够"就又合成了一个数——
    整份 v1 都在防这件事。
    """
    v, ps = card.volume, card.price_structure
    # **每一个都过 `finite()`。** 卡片理应已经洗过，但这一层是判定层——
    # 它不该假设上游没漏。NaN 在这里静默判 False 的代价是一次看不见的漏判。
    spike = finite(v.get("days_rvol_over_1_5_of_20"))
    cmf = finite(v.get("cmf_20"))
    obv = finite(v.get("obv_slope_20"))
    dist = finite(ps.get("atr_to_nearest_zone_above"))

    unknown = [name for name, val in (("A_participation", spike),
                                      ("B_accumulation_proxy", cmf),
                                      ("C_location", dist)) if val is None]
    if cmf is not None and obv is None:
        unknown.append("B_accumulation_proxy")

    a = spike is not None and spike >= A_MIN_SPIKE_DAYS
    b = (cmf is not None and obv is not None
         and cmf > B_MIN_CMF and obv > B_MIN_OBV_SLOPE)
    c = dist is not None and dist <= C_MAX_ATR_TO_ZONE
    return {
        "setup_id": SETUP_ID, "setup_version": SETUP_VERSION,
        "A_participation": a, "B_accumulation_proxy": b, "C_location": c,
        "hit": bool(a and b and c),
        "unknown": sorted(set(unknown)),
    }


@dataclass
class Event:
    """一次 setup 事件。**起点是唯一有意义的样本时刻。**"""

    symbol: str = ""
    start: str = ""
    """第一次 False → True 的那一天。事件研究的 t=0。"""
    end: str = ""
    """最后一天仍然成立的日期（含）。"""
    days: int = 0
    setup_id: str = SETUP_ID
    lineage: tuple = ()
    """**完整血统，不只是 setup_version。**

    条件 C 是"距上方价区 ≤0.5 ATR"，而"价区"是 `sr-1.0.0` 这套算法算出来的。
    所以 `sr-1.0.0 → sr-1.1.0` 之后，即使 setup 的三个阈值一个都没改，
    **这条 setup 的实际含义已经变了**——它筛的是另一批东西。

    只按 `setup_version` 分组做事件研究，会把两套定义下的事件混成一堆，
    而且混得毫无痕迹。所以血统是 (setup_version, setup_fingerprint,
    zone_algo_version, card_schema_version) 四元组，分组必须按整个四元组。
    """
    merged_repeats: list = field(default_factory=list)
    """冷却期内被并进来的重新触发日。**并了什么要留痕**，
    否则"这段时间只触发一次"和"触发五次被合成一次"看不出区别。"""
    ended_by_version_change: bool = False
    """True 表示这个事件是被**定义变更**截断的，不是被行情截断的。

    一个事件不能横跨两套定义：前半段按旧价区算法成立、后半段按新的，
    那它就不是一个事件。截断并标记，比缝在一起诚实。"""

    @property
    def setup_version(self) -> str:
        return self.lineage[0] if self.lineage else SETUP_VERSION


def current_lineage() -> tuple:
    """当前代码的完整血统四元组。存卡片时抄一份，事件从卡片里读。"""
    from . import SCHEMA_VERSION
    from .price_structure import ALGO_VERSION
    return (SETUP_VERSION, params_fingerprint(), ALGO_VERSION, SCHEMA_VERSION)


def derive_events(symbol: str, series: list,
                  cooldown: int = COOLDOWN_DAYS) -> list:
    """把一只票的逐日结果切成事件。

    `series` 是按日期升序的 `[(date, hit_bool)]` 或
    `[(date, hit_bool, lineage_tuple)]`。**必须是连续交易日**——
    中间跳掉的日期会让冷却期按"条数"而不是"天数"计算。调用方负责这一点。

    规则：False→True 开始一个事件；持续 True 属于同一事件；
    转 False 复位；复位后 `cooldown` 个交易日内再次 True 仍并入上一个事件。
    **血统一变就截断**——见 `Event.ended_by_version_change`。
    """
    events: list = []
    cur: Optional[Event] = None
    off_streak = 0
    for row in series:
        d, hit = str(row[0])[:10], bool(row[1])
        lin = tuple(row[2]) if len(row) > 2 and row[2] else current_lineage()
        if hit:
            if cur is not None and cur.lineage != lin:
                # **定义变了 → 上一个事件到此为止。** 不能横跨两套定义。
                cur.ended_by_version_change = True
                events.append(cur)
                cur, off_streak = None, 0
            if cur is None:
                cur = Event(symbol=symbol, start=d, end=d, days=1, lineage=lin)
            elif off_streak == 0:
                cur.end, cur.days = d, cur.days + 1
            elif off_streak <= cooldown:
                cur.merged_repeats.append(d)      # 冷却期内重新触发 → 并入
                cur.end, cur.days = d, cur.days + 1
            else:
                events.append(cur)
                cur = Event(symbol=symbol, start=d, end=d, days=1, lineage=lin)
            off_streak = 0
        else:
            if cur is not None:
                off_streak += 1
                if off_streak > cooldown:
                    events.append(cur)
                    cur, off_streak = None, 0
    if cur is not None:
        events.append(cur)
    return events


def describe() -> list:
    """定义的人话版本。**放进每一份用到它的报告里**，
    这样读的人不必去翻代码就知道自己在看什么。"""
    return [
        f"{SETUP_ID}（{SETUP_VERSION}，参数指纹 {params_fingerprint()}）",
        f"  A 参与度    近 20 日量比≥1.5 的天数 ≥ {A_MIN_SPIKE_DAYS}"
        f"      （全市场基础率约 8%；中位数 2、p90 4）",
        f"  B 量能代理   CMF20 > {B_MIN_CMF} 且 OBV 斜率 > {B_MIN_OBV_SLOPE}"
        f"   （约 23%；CMF 的 p75 是 0.095）",
        f"  C 位置      距上方价区 ≤ {C_MAX_ATR_TO_ZONE} 个 ATR20"
        f"        （约 22%；这个数等于价区聚类容差，不是新参数）",
        f"  事件切分    False→True 开始；持续 True 同一事件；"
        f"复位后 {COOLDOWN_DAYS} 个交易日内重触发仍并入",
        "  三条同时：真机 6 天 × 120 只实测约每天 1–2 只（折全市场 500 只）",
        "  **以上全部在看任何远期收益之前定下。改任何一个数都要升版本号。**",
    ]
