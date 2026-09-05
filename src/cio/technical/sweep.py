"""全市场缺失扫描 —— **一只票缺和全市场缺，不是同一件事。**

## 这个模块是被一次真实的静默失败逼出来的

2026-09-04 的快照，502 张卡片，`--force` 跑完一切正常：闸门推了 2 只、
分数算出来了、覆盖度写着 5/5。翻到卡片末尾才有一行：

    缺：relative_strength 少了 excess_mkt_63（该字段是 null）

两只票上都有。而同一块里的 `excess_sector_63`（板块 63 日超额）和
`rs_mkt_slope_20`（大盘 20 日斜率）都算得出来。

**真正的原因是 SPY 最后一根收盘价是 NaN**（yfinance 未落定的尾行）：
三个超额窗口共用分子 `series[-1]`，它一 NaN 就一起空；而斜率那一支的
推导式里 `NaN > 0` 为 False，把那根顺手滤掉了，所以照算。

（我第一次的诊断是"SPY 面板取短了、对齐后只剩 20–63 天"，**那是错的**——
`rs_mkt_samples` 当时报 405，对齐没有任何问题。我被那个数骗了，
因为它当时把 NaN 也数成了样本。这段留在这里，是因为
**一个错的原因比没有原因更糟**，而它差点被写死成这个模块的立论。）

不管病因是哪一个，系统的表现都一样：**全市场每一只票的大盘超额同时坏掉**，
而每张卡片上只礼貌地写一句"该字段是 null"，一共写 502 次，
没有一处说"这一路数据坏了"。

**每一个 null 都有原因**（build107 就做到了）解决的是"这一格为什么空"。
它解决不了"这一整列为什么空"。**同一个事实重复 502 次，不会自己变成一个结论。**

## 两条判据，都不靠拍一个阈值

**一、成对不对称。** `excess_mkt_63` 与 `excess_sector_63` 是同一段代码
在两个基准上跑出来的。一个全空、另一个基本满，**差异只可能来自基准本身**。
这不需要任何阈值——对比自己就是证据。

**二、缺失率本身。** 单看一个字段缺了多少，只报数、不下结论，
让人自己看。写"某某字段 100% 缺失"和写"某某字段 2% 缺失"，
是同一句话的两个事实，不是两个判断。

## 它不修任何东西

扫描只数不改，和 `panel_health` 一样。**能不能用今天这份数据，是人的决定。**
"""
from __future__ import annotations

BLOCKS = ("price_structure", "volume", "relative_strength", "volatility")

PAIRED = (
    ("excess_mkt_21", "excess_sector_21"),
    ("excess_mkt_63", "excess_sector_63"),
    ("excess_mkt_126", "excess_sector_126"),
    ("rs_mkt_slope_20", "rs_sector_slope_20"),
    ("rs_mkt_samples", "rs_sector_samples"),
)
"""同一段代码在两个基准上的产物。**一边全空、另一边基本满 = 基准坏了。**"""


def null_rates(cards: list) -> dict:
    """{block: {field: (null 数, 总数)}}。**只数，不判断。**"""
    out: dict = {}
    for c in cards:
        for blk in BLOCKS:
            d = getattr(c, blk, None)
            if not isinstance(d, dict):
                continue
            box = out.setdefault(blk, {})
            for k, v in d.items():
                n_null, n_all = box.get(k, (0, 0))
                box[k] = (n_null + (1 if v is None else 0), n_all + 1)
    return out


def benchmark_asymmetry(cards: list) -> list:
    """成对字段的缺失率差。**这一条不需要阈值——它和自己比。**

    返回 [(mkt 字段, sector 字段, mkt 缺失率, sector 缺失率)]，
    只保留两边差得明显的那些。
    """
    rates = null_rates(cards).get("relative_strength", {})
    out = []
    for a, b in PAIRED:
        na, ta = rates.get(a, (0, 0))
        nb, tb = rates.get(b, (0, 0))
        if not ta or not tb:
            continue
        ra, rb = na / ta, nb / tb
        # 一边基本全空、另一边基本不空 —— 差异只可能来自基准本身
        if ra - rb >= 0.5:
            out.append((a, b, round(ra, 4), round(rb, 4)))
        elif rb - ra >= 0.5:
            out.append((b, a, round(rb, 4), round(ra, 4)))
    return out


def asof_divergence(cards: list) -> dict:
    """两个基准的截止日不一样的卡片数。**差一天也要说出来。**

    基准尾行是 NaN 时那一天整对被丢掉，这一路的超额就截止到 T-1，
    而另一路可能截止到 T。两个数进同一个家族分，**as-of 却不是同一天**。

    不是错误，是**必须看得见的事实**——尤其它每天都会因为 yfinance
    的未落定尾行而发生，而且从分数上完全看不出来。
    """
    box: dict = {}
    for c in cards:
        d = getattr(c, "relative_strength", None)
        if not isinstance(d, dict):
            continue
        m, s = d.get("rs_mkt_as_of"), d.get("rs_sector_as_of")
        if m and s and m != s:
            box[(m, s)] = box.get((m, s), 0) + 1
    return box


def report(cards: list, top: int = 8) -> list:
    """给人看的几行。**先报不对称，因为那一条是有结论的。**"""
    n = len(cards)
    if not n:
        return ["没有卡片，不扫描"]
    out = [f"全市场缺失扫描（{n} 张卡片）"]

    asym = benchmark_asymmetry(cards)
    if asym:
        out.append("  **成对基准不对称 —— 这不是个别票缺数据，是一路基准坏了：**")
        for bad, good, rbad, rgood in asym:
            out.append(f"    {bad} 缺 {rbad:.0%}　而同源的 {good} 只缺 {rgood:.0%}"
                       f"　→ 差异只可能来自基准本身")
    else:
        out.append("  成对基准对称，两个基准都正常")

    div = asof_divergence(cards)
    if div:
        out.append("  **两个基准的截止日不一样** —— 超额进的是同一个家族分，"
                   "as-of 却不同（多半是某一路的尾行不可用、被丢掉了）：")
        for (m, s), cnt in sorted(div.items(), key=lambda kv: -kv[1])[:3]:
            out.append(f"    大盘截止 {m}　板块截止 {s}　{cnt} 张卡片")

    rows = []
    for blk, box in null_rates(cards).items():
        for k, (nn, tt) in box.items():
            if nn:
                rows.append((nn / tt, blk, k, nn, tt))
    rows.sort(reverse=True)
    if rows:
        out.append(f"  缺失率最高的 {min(top, len(rows))} 个字段（**只报数，不下结论**）：")
        for rate, blk, k, nn, tt in rows[:top]:
            out.append(f"    {rate:6.1%}  {blk}.{k}　（{nn}/{tt}）")
    else:
        out.append("  没有任何字段为 null")
    return out


def closing_line() -> str:
    """收尾那句。**它必须印在最后**——快照后面还要接基准面板那几行，
    把"扫描只数不修"卡在中间，读起来像扫描已经结束了。"""
    return "  **扫描只数不修。今天这份数据能不能用，是人的决定。**"
