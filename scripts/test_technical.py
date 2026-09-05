#!/usr/bin/env python3
"""技术观察员自测 —— **v1 的边界是被测出来的，不是被写在文档里的。**

    python scripts/test_technical.py

五类探针，每一类对应一条 CEO 冻结的边界：

    禁用词        源码里出现看涨/看跌/买入/超买/机构… 即红
    纯函数        不看时钟、不联网、不改输入、两次调用完全相同
    无未来函数    observe(df[:t]) 必须逐字段等于 observe(df, as_of=t)
    null ≠ 0      算不出来必须是 null，而且必须有原因
    形态回放      造出已知形状的行情，断言度量出来的数

**最贵的一条是"无未来函数"。** 未来函数不会报错、不会让图变难看，
它只会让每一次回测都变绿——等到实盘才发现那条线当天根本不存在。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _no_network                                            # noqa: E402,F401
"""技术观察员**绝不允许联网** —— 它是纯函数，联网就说明有人偷偷加了取数。"""

import pandas as pd                                           # noqa: E402

from cio.technical import observer as ob                      # noqa: E402
from cio.technical import price_structure as ps               # noqa: E402
from cio.technical import relative_strength as rsm            # noqa: E402
from cio.technical import volatility as vol                   # noqa: E402
from cio.technical import volume as vm                        # noqa: E402

OK, BAD = [], []
TECH_DIR = Path(__file__).resolve().parents[1] / "src" / "cio" / "technical"


def check(name, fn):
    try:
        fn()
        OK.append(name)
        print(f"  OK    {name}")
    except AssertionError as e:
        BAD.append((name, str(e)))
        print(f"  FAIL  {name}\n          {e}")
    except Exception as e:                                     # noqa: BLE001
        BAD.append((name, f"{type(e).__name__}: {e}"))
        print(f"  ERR   {name}\n          {type(e).__name__}: {e}")


# ---------------------------------------------------------------- 造行情
def panel(bars: list, start="2024-01-01") -> pd.DataFrame:
    """bars: [(open, high, low, close, volume), ...] → 面板。日期是连续工作日。"""
    dates = pd.bdate_range(start=start, periods=len(bars))
    return pd.DataFrame({
        "date": dates,
        "open": [b[0] for b in bars], "high": [b[1] for b in bars],
        "low": [b[2] for b in bars], "close": [b[3] for b in bars],
        "volume": [float(b[4]) for b in bars],
    })


def flat(n, price=100.0, vol_=1_000_000.0):
    return [(price, price + 0.5, price - 0.5, price, vol_)] * n


def bar(c, h=None, l=None, v=1_000_000.0):
    return (c, h if h is not None else c + 0.5, l if l is not None else c - 0.5, c, v)


def four_peaks() -> pd.DataFrame:
    """**四次冲到同一个高度就回落。** CEO 原话「四个峰值都在这里高度」。

    每个峰之间隔 12 根，保证 pivot 窗口（5）和触点间隔（5）都满足；
    最后再补 6 根，让第四个峰能被确认。
    """
    bars = []
    for _ in range(4):
        bars += [bar(100 + i) for i in range(6)]        # 爬到 105
        bars += [bar(110, h=110.4, l=109.6)]            # 峰：110
        bars += [bar(105 - i) for i in range(5)]        # 回落
    bars += flat(8, 101.0)                              # 让最后一个峰被确认
    return panel(bars)


def trending_up(n=300, step=0.3) -> pd.DataFrame:
    return panel([bar(100 + i * step) for i in range(n)])


def zigzag(n=300, amp=8.0, period=24, drift=0.05) -> pd.DataFrame:
    """带真实摆动的行情。**回放测试必须用它，不能用单调直线。**

    单调上涨的序列里**一个 swing 点都没有**（每根都比左边高、又比右边低），
    于是价区那一整块在任何实现下都是空的——一条本来该抓未来函数的断言，
    在这种数据上两边都是空，永远相等。

    变异测试第一次跑就是这样：我把 `swings()` 改成不要求右侧确认
    （一个货真价实的未来函数），24 条用例**全绿**。
    红不了的原因不在断言，在喂给它的数据。
    """
    import math
    bars = []
    for i in range(n):
        c = 100 + drift * i + amp * math.sin(2 * math.pi * i / period)
        v = 1_000_000 * (1.0 + 0.4 * math.sin(2 * math.pi * i / 7))
        bars.append(bar(round(c, 4), h=round(c + 1.0, 4), l=round(c - 1.0, 4), v=v))
    return panel(bars)


def fresh_extreme_at_the_end() -> pd.DataFrame:
    """四个峰之后，**在倒数第 3 根上插一个新高**。

    那个新高今天绝不能算 pivot —— 它右边只有 2 根，而确认需要 5 根。
    专门造它是因为 `four_peaks()` 的结尾是一段平盘，
    平盘里左侧的严格不等式天然过不了，于是"最后 5 根不算数"这条
    在那份数据上**根本没被考验过**。
    """
    df = four_peaks()
    bars = list(zip(df["open"], df["high"], df["low"], df["close"], df["volume"]))
    bars = bars[:-3] + [(130.0, 131.0, 129.0, 130.0, 3_000_000.0)] + bars[-2:]
    return panel([tuple(b) for b in bars])


# ---------------------------------------------------------------- 禁用词
BANNED = [
    "看涨", "看跌", "bullish", "bearish", "买入", "卖出", "加仓", "减仓",
    "超买", "超卖", "强势", "弱势", "建议", "目标价", "止损", "止盈",
    "抄底", "逃顶", "主力", "机构", "institutional", "突破买",
    "支撑位", "阻力位", "金叉", "死叉", "信号强", "看好", "利好", "利空",
]
"""**v1 只描述，不判断。** 这张表覆盖三类词：方向判断（看涨/强势）、
操作建议（买入/止损）、和不可观测的主体（主力/机构/institutional）。

第三类最重要：日线 OHLCV 看不见谁在交易，写下"机构"两个字就是在
凭空多出一个我们没有的观察。所以那组量能指标叫
`accumulation_pressure_proxy`，proxy 三个字母是它最重要的部分。
"""


def _emitted_text(path: Path) -> list:
    """这个文件里**会跑出去的文字**：非文档字符串的字符串字面量 + 所有标识符。

    刻意**不含文档字符串**。第一版扫了整份源码，于是自己就红了——
    `volume.py` 的说明里必须写出"绝不叫 institutional"才讲得清楚为什么
    那组指标叫 proxy，而那句解释本身触发了禁令。

    禁的是**这个模块说出口的话和它给字段起的名字**，不是它解释自己时
    引用的词。真正会伤人的是 `institutional_flow` 这样一个字段名，
    或者一句写进卡片的"量能强势"——两者都在这个函数的扫描范围里。
    文档字符串是写给人看的说明，越把边界讲清楚越好。
    """
    import ast
    tree = ast.parse(path.read_text("utf-8"))
    docstrings = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(n, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
        # 变量/常量后面那种"裸字符串当注释"也算文档
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) \
                and isinstance(n.value.value, str):
            docstrings.add(id(n.value))
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings:
            out.append(n.value)
        elif isinstance(n, ast.Name):
            out.append(n.id)
        elif isinstance(n, ast.Attribute):
            out.append(n.attr)
        elif isinstance(n, ast.arg):
            out.append(n.arg)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.append(n.name)
    return out


def t_no_banned_words():
    """**字段名和会说出口的字符串里，不许出现这三类词。**"""
    hits = []
    for f in sorted(TECH_DIR.glob("*.py")):
        for text in _emitted_text(f):
            for w in BANNED:
                if w in text:
                    hits.append(f"{f.name}: {w} ←「{text[:36]}」")
    assert not hits, "出现禁用词：" + "；".join(hits[:6])


def t_the_card_itself_carries_no_judgement():
    """**最后一道：跑出来的那张卡片上，字段名和字符串都不许有禁用词。**

    上面那条查的是源码，这条查的是产物。两条都要有——
    源码干净但拼接出一个 `f"{x}强势"` 的实现，只有这条能抓到。
    """
    import json
    df = trending_up(300)
    bench = panel([bar(50 + i * 0.1, v=2_000_000) for i in range(300)])
    blob = json.dumps(ob.observe(df, bench=bench, symbol="X").to_dict(), ensure_ascii=False)
    blob += "\n".join(ob.describe(ob.observe(df, bench=bench, symbol="X")))
    hit = [w for w in BANNED if w in blob]
    assert not hit, f"卡片里出现了禁用词：{hit}"
    assert "institutional" not in blob and "机构" not in blob, \
        "卡片里出现了日线看不见的主体"


def t_proxy_is_named_proxy():
    """那组量能指标必须叫 `accumulation_pressure_proxy`，**而且不是一个分数**。"""
    card = ob.observe(trending_up(), symbol="X")
    p = card.volume.get("accumulation_pressure_proxy")
    assert isinstance(p, dict), "accumulation_pressure_proxy 必须是一组并列事实，不是一个数"
    assert "note" in p and "不构成" in p["note"], "缺少「这不是资金流向」的说明"
    for k in ("cmf_20", "obv_slope_20", "up_down_volume_ratio_20"):
        assert k in p, f"少了成分 {k}"
    assert not any(k in p for k in ("score", "分数", "总分", "signal")), \
        "v1 不许打分：proxy 里出现了分数字段"


# ---------------------------------------------------------------- 纯函数
def t_observe_is_pure():
    """两次调用完全相同；输入面板不被改动。"""
    df = trending_up()
    before = df.to_json()
    a = ob.observe(df, symbol="X").to_json()
    b = ob.observe(df, symbol="X").to_json()
    assert a == b, "同样的输入给出了两个结果"
    assert df.to_json() == before, "observe 改动了传进去的面板"


BANNED_CALLS = {
    "datetime.now", "datetime.utcnow", "date.today", "time.time", "time.localtime",
    "now_beijing", "now_ny", "market_date", "requests.get", "requests.post",
    "httpx.get", "httpx.Client", "open", "random.random", "random.choice",
    "np.random.rand", "pd.read_csv", "pd.read_json",
}


def _called_names(path: Path) -> set:
    """这个文件里**实际发生的调用**的点分名字。走 AST，不是字符串匹配。

    上一版是拿子串扫源码的，结果被自己的文档字符串照亮了：
    `observer.py` 的说明里写着"扫源码里有没有 `datetime.now`"，
    探针就把这句话当成了一次调用。

    **这是本项目第八次踩「断言文本而不是断言结构」。**
    调用是语法结构，AST 认得出来，注释里提到它则不会被误认。
    """
    import ast
    tree = ast.parse(path.read_text("utf-8"))
    out = set()
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        parts = []
        while isinstance(f, ast.Attribute):
            parts.append(f.attr)
            f = f.value
        if isinstance(f, ast.Name):
            parts.append(f.id)
        if parts:
            out.add(".".join(reversed(parts)))
    return out


IO_ALLOWED = {"store.py", "review.py"}
"""**允许碰磁盘的模块，只有这两个。** 它们的职责就是写文件：
`store` 存卡片，`review` 存人工复核。

这个白名单是显式的、而且下面会断言它的内容：新加一个会读写文件的模块，
必须来这里改一行——不能靠"它反正也不纯"糊过去。
度量模块（observer / price_structure / volume / relative_strength / volatility）
一个都不在里面，它们必须保持纯函数，否则回放测试就失去意义。
"""


def t_no_clock_no_network_no_io():
    """**度量模块不看时钟、不联网、不读文件、不随机。**

    断的是源码里的调用（AST），不是运行时——这一轮没触发不等于不会触发。
    """
    assert IO_ALLOWED == {"store.py", "review.py"}, \
        f"允许碰磁盘的模块变了：{IO_ALLOWED} —— 这是一次要专门决定的放宽"
    assert "observer.py" not in IO_ALLOWED, "observer 必须保持纯函数"
    hits = []
    for f in sorted(TECH_DIR.glob("*.py")):
        if f.name in IO_ALLOWED:
            continue
        for name in sorted(_called_names(f) & BANNED_CALLS):
            hits.append(f"{f.name}: {name}()")
    assert not hits, "技术观察员里出现了不该有的调用：" + "；".join(hits[:6])
    # 探针自己要能抓到东西 —— 否则它可能只是没在看
    import ast
    probe = ast.parse("import datetime\nx = datetime.datetime.now()\n")
    assert any(isinstance(n, ast.Call) for n in ast.walk(probe))


def t_does_not_import_business_modules():
    """**v1 不接任何东西。** 不许 import 一部/闸门/台账/消息这些业务模块。"""
    forbidden = ["unit_a", "unit_b", "material_gate", "judge", "telegram",
                 "book", "cro", "pc ", "proposal", "collect"]
    hits = []
    for f in sorted(TECH_DIR.glob("*.py")):
        for ln in f.read_text("utf-8").splitlines():
            s = ln.strip()
            if s.startswith(("import ", "from ")):
                for m in forbidden:
                    if m in s:
                        hits.append(f"{f.name}: {s}")
    assert not hits, "技术观察员 import 了业务模块：" + "；".join(hits[:4])


# ---------------------------------------------------------------- 无未来函数
def t_as_of_equals_truncation():
    """**observe(df[:t]) 必须逐字段等于 observe(df, as_of=t)。**

    这一条抓的是未来函数。任何一个"顺手用了后面几根 K 线"的实现
    都会让这两个结果不一样，而在图上、在日志里、在回测收益率上
    都看不出来。

    **必须用带摆动的数据。** 单调直线里一个 swing 点都没有，价区两边都是空，
    这条断言会在任何实现下变绿——包括故意植入未来函数的实现。见 `zigzag`。
    """
    df = zigzag(300)
    bench = panel([bar(50 + i * 0.1, v=2_000_000) for i in range(300)])
    for t in (120, 200, 280):
        cut = ob.observe(df.iloc[:t + 1].reset_index(drop=True),
                         bench=bench.iloc[:t + 1].reset_index(drop=True),
                         symbol="X").to_dict()
        asof = str(df["date"].iloc[t])[:10]
        full = ob.observe(df, as_of=asof, bench=bench, symbol="X").to_dict()
        cut.pop("as_of"), full.pop("as_of")            # 一个是截断后的最后一天，一个是问的那天
        assert cut == full, f"t={t} 截断与 as_of 结果不同 —— 有未来函数"


def t_benchmark_cannot_leak_future():
    """**基准比个股多出来的未来行情，一点都不能进结果。**

    钉的是结果，不管是哪一道机制拦住的：`observe` 里对基准的截断，
    还是 `align()` 的日期取交集。变异测试显示删掉前者不改变任何输出
    （日期对齐已经拦住了），所以断言写在这一层，两道都换掉才会红。
    """
    df = zigzag(200)
    long_bench = panel([bar(50 + i * 0.1, v=2_000_000) for i in range(260)],
                       start=str(df["date"].iloc[0])[:10])
    short_bench = long_bench.iloc[:200].reset_index(drop=True)
    asof = str(df["date"].iloc[-1])[:10]
    a = ob.observe(df, as_of=asof, bench=long_bench, symbol="X").to_dict()
    b = ob.observe(df, as_of=asof, bench=short_bench, symbol="X").to_dict()
    assert a == b, "基准多带了 60 天未来行情，结果却变了"


def t_recent_pivots_are_not_confirmed():
    """**最后 5 根 K 线上的极值今天还不算 pivot。**

    一个 swing high 要成立需要它右边也有 5 根。不管这件事的话，
    "截止今天的价区"里会含着未来 5 天才知道的信息。
    """
    for df in (four_peaks(), fresh_extreme_at_the_end(), zigzag(200)):
        n = len(df)
        assert ps.observed_pivot_cutoff(n) == n - ps.PIVOT_WINDOW - 1
        ph, pl = ps.swings(df)
        idx = [i for i, _p in ph] + [i for i, _p in pl]
        latest = max(idx) if idx else -1
        assert latest <= n - ps.PIVOT_WINDOW - 1, \
            f"返回了下标 {latest} 的 pivot，但它要到 {latest + ps.PIVOT_WINDOW} 才能确认"
    # 那个插在倒数第 3 根的新高：**今天不能出现在任何价区里**
    z, _why = ps.zones(fresh_extreme_at_the_end())
    assert not any(x["high"] >= 129.0 for x in z), \
        f"未确认的新高进了价区：{[x for x in z if x['high'] >= 129.0]}"


def t_frozen_params_are_bound_to_version():
    """**改参数必须改版本号。** 参数指纹和冻结值不符即红。

    这条不是让你去改常量的。它红了要回答的是：
    参数动了，`ALGO_VERSION` 跟着动了吗？旧卡片上的版本号还对得上吗？
    """
    assert ps.params_fingerprint() == ps.FROZEN_FINGERPRINT, (
        f"价区参数变了（现在 {ps.params_fingerprint()}，冻结值 {ps.FROZEN_FINGERPRINT}）"
        f"—— 请同时把 ALGO_VERSION 从 {ps.ALGO_VERSION} 升上去，再更新冻结值")
    assert (ps.PIVOT_WINDOW, ps.CLUSTER_ATR_MULT, ps.MIN_TOUCH_GAP, ps.MIN_TOUCHES) \
        == (5, 0.5, 5, 2), "冻结参数被改了"


# ---------------------------------------------------------------- null ≠ 0
def t_null_is_not_zero():
    """数据不够时必须是 null，**不能是 0**，而且必须有原因。"""
    short = ob.observe(trending_up(40), symbol="X")
    v = short.volatility
    assert v["atr_percentile_252"] is None, \
        f"40 根 K 线算出了一年分位：{v['atr_percentile_252']}"
    # **写成 `is not 0` 是错的**：对 None 恒真，等于没断言，Python 还会警告。
    # 要断的是"没有被写成 0"，那就检查类型：null 不是数。
    assert not isinstance(v["atr_percentile_252"], (int, float)), \
        "一年分位被写成了一个数（多半是 0）——数据不够时必须是 null"
    assert short.price_structure["position_in_range_252"] is None
    for k in ("atr_percentile_252",):
        assert k in short.reasons and str(short.reasons[k]), f"{k} 是 null 但没写原因"


def t_every_null_has_a_reason():
    """**契约：任何 null 字段都要有 reason。** 缺一个就抛异常（strict）。"""
    for n in (3, 15, 40, 120, 300):
        card = ob.observe(trending_up(n), symbol="X")      # strict=True，缺原因直接炸
        assert not ob._check_nulls(card), ob._check_nulls(card)
    # 反过来：故意造一个缺原因的卡片，strict 必须抓住
    bad = ob.SignalCard(symbol="X", rows_used=10, last_close=1.0)
    bad.volume = {"rvol_20": None}
    assert ob._check_nulls(bad) == ["volume.rvol_20"]


def t_missing_benchmark_is_null_not_zero():
    """**没有基准 ≠ 超额为 0。** 后者是一个具体结论，而且可能完全错。"""
    card = ob.observe(trending_up(300), bench=None, symbol="X")
    assert card.relative_strength["excess_mkt_63"] is None
    assert card.relative_strength["rs_mkt_slope_20"] is None
    assert "没有基准面板" in card.reasons["excess_mkt_63"]


def t_no_down_day_ratio_is_null_not_inf():
    """一段只涨不跌的行情里，上下量比**无定义**（分母为 0）——是 null，不是 0、不是 inf。"""
    v, why = vm.measure(trending_up(60))
    assert v["up_down_volume_ratio_20"] is None
    assert "没有下跌日" in why["up_down_volume_ratio_20"]


def t_cmf_skips_are_counted():
    """一字板（high == low）那天 CMF 无定义。**被跳过几天必须报出来**，
    而不是当成"那天净流入为 0"。"""
    bars = [bar(100 + i * 0.2) for i in range(20)]
    bars[5] = (100.0, 100.0, 100.0, 100.0, 500_000.0)      # 一字板
    bars[9] = (100.0, 100.0, 100.0, 100.0, 500_000.0)
    v, why = vm.measure(panel(bars))
    assert v["cmf_20_skipped_days"] == 2, v["cmf_20_skipped_days"]
    assert "最高价=最低价" in why["cmf_20_skipped_days"]


# ---------------------------------------------------------------- 形态回放
def t_four_peaks_make_one_zone():
    """**四次触到同一高度 → 上方一个价区，触碰 4 次。**

    这是 CEO 那张图的算法版本。数错触碰次数是这个模块最可能出的错，
    所以断言的是精确值，不是"大于等于 2"。
    """
    z, why = ps.zones(four_peaks())
    above = [x for x in z if x["side"] == "above" and x["kind"] == "high"]
    assert above, f"没找到上方价区：{why}"
    top = above[0]
    assert top["touches"] == 4, f"触碰次数应为 4，实得 {top['touches']}（{top}）"
    assert 109.5 <= top["mid"] <= 110.5, top


def t_clustered_touches_count_once():
    """连着三天在同一价位打转是**一次**触碰，不是三次。"""
    g = [(10, 100.0), (11, 100.1), (12, 100.2), (40, 100.1), (41, 100.0)]
    kept = ps._count_touches(g, min_gap=5)
    assert [i for i, _p in kept] == [10, 40], kept


def t_position_in_range_is_a_fact():
    """单调上涨 → 现价处在区间顶部；单调下跌 → 底部。"""
    up = ob.observe(trending_up(300), symbol="X").price_structure
    assert up["position_in_range_252"] > 0.97, up["position_in_range_252"]
    down = ob.observe(panel([bar(200 - i * 0.3) for i in range(300)]),
                      symbol="X").price_structure
    assert down["position_in_range_252"] < 0.03, down["position_in_range_252"]


def t_rvol_sees_a_volume_spike():
    """今天放量 → rvol；**分母不含今天**。

    "分母不含今天"这句话需要一份能分辨的数据。原来那份是 30 天等量 + 今天 3 倍：
    中位数对单根异常本来就不敏感，含不含今天都是同一个中位数，
    于是把分母改成含今天的实现**照样通过**（变异测试抓到的）。

    这里前 20 天一半 1M、一半 2M（中位数 1.5M），今天 3M：
        不含今天 → 1.5M → rvol 2.0
        含今天   → 挤掉最老的一天，中位数变 2.0M → rvol 1.5
    两个数不一样，这条断言才真的在断言。
    """
    priors = ([bar(100.0, v=1_000_000.0)] * 10 + [bar(100.0, v=2_000_000.0)] * 10)
    v, _ = vm.measure(panel(priors + [bar(100.0, v=3_000_000.0)]))
    assert abs(v["rvol_20"] - 2.0) < 1e-6, \
        f"rvol={v['rvol_20']}（2.0 才是分母不含今天；1.5 说明含了）"
    # 再来一个直觉版：常量量能下放量 3 倍就是 3 倍
    v2, _ = vm.measure(panel(flat(30) + [bar(100.0, v=3_000_000.0)]))
    assert 2.9 <= v2["rvol_20"] <= 3.1, v2["rvol_20"]


def t_obv_slope_direction():
    """涨日放量、跌日缩量 → OBV 斜率为正；镜像 → 为负；**没方向 → 接近 0**。"""
    up = [bar(100 + i * 0.5, v=2_000_000.0) if i % 2 == 0 else bar(100 + i * 0.5 - 0.2, v=400_000.0)
          for i in range(40)]
    assert vm.obv_slope(panel(up)) > 0.2, vm.obv_slope(panel(up))
    down = [bar(120 - i * 0.5, v=2_000_000.0) if i % 2 == 0 else bar(120 - i * 0.5 + 0.2, v=400_000.0)
            for i in range(40)]
    assert vm.obv_slope(panel(down)) < -0.2, vm.obv_slope(panel(down))
    # 完全横盘：OBV 不动（平盘不计），斜率 0 —— **"量增但没方向"必须看得出来**
    assert abs(vm.obv_slope(panel(flat(40, vol_=5_000_000.0)))) < 1e-9


def t_range_contraction_is_visible():
    """先大幅震荡再窄幅横盘 → 20 日区间明显收缩，且今天是 NR7。"""
    wide = [bar(100 + (5 if i % 2 else -5), h=106, l=94) for i in range(40)]
    tight = [bar(100.0, h=100.1, l=99.9) for _ in range(25)]
    v, _ = vol.measure(panel(wide + tight))
    assert v["range_pct_20"] < 0.01, v["range_pct_20"]
    assert v["is_nr7"] is True
    v2, _ = vol.measure(panel(wide))
    assert v2["range_pct_20"] > 0.1, v2["range_pct_20"]


def t_relative_strength_aligns_by_date():
    """**按日期对齐，不按行数。**

    只断言"样本数变少"是不够的——把 60 行和 57 行按位置截齐，
    结果也是 57 行。变异测试证明了这一点：我把 `align` 换成按位置取
    `min(len)` 的实现，这条用例照样绿。

    所以断言的是**配对的值**：基准比个股多三个更早的日期时，
    按位置配会把每一天错配到三天前，超额收益随之改变。
    """
    df = trending_up(60)
    b_dates = pd.bdate_range(end=df["date"].iloc[-1], periods=63)   # 基准多三个更早的日子
    b = pd.DataFrame({"date": b_dates,
                      "open": [50.0] * 63, "high": [50.5] * 63, "low": [49.5] * 63,
                      "close": [50 + i * 0.05 for i in range(63)],
                      "volume": [2_000_000.0] * 63})
    a, bb = rsm.align(df, b)
    assert len(a) == len(bb) == 60, (len(a), len(bb))
    # 按日期配对：df 的最后一天要配到 b 的最后一天
    assert bb[-1] == b["close"].iloc[-1], "最后一天没配到最后一天"
    assert bb[0] == b["close"].iloc[3], "起点错配了三天 —— 这是按位置对齐的症状"
    v, _ = rsm.measure(df, b)
    assert v["rs_mkt_samples"] == 60
    # 基准中间缺三天 → 样本数如实变少
    b2 = b.drop(index=[30, 31, 32]).reset_index(drop=True)
    assert rsm.measure(df, b2)[0]["rs_mkt_samples"] == 57


def t_outperformance_is_measured_not_judged():
    """个股涨得比基准快 → 超额为正；反之为负。**只出数，不出词。**"""
    fast = trending_up(200, step=0.5)
    slow = panel([bar(100 + i * 0.1, v=2_000_000) for i in range(200)])
    v, _ = rsm.measure(fast, slow)
    assert v["excess_mkt_63"] > 0
    v2, _ = rsm.measure(slow, fast)
    assert v2["excess_mkt_63"] < 0


def t_card_schema_is_stable():
    """卡片顶层字段是契约。**加字段可以，改名要升 schema_version。**"""
    d = ob.observe(trending_up(300), symbol="X").to_dict()
    must = {"symbol", "as_of", "as_of_effective", "rows_used", "last_close",
            "schema_version", "algo_version", "price_structure", "volume",
            "relative_strength", "volatility", "reasons"}
    assert must <= set(d), f"缺字段：{sorted(must - set(d))}"
    assert d["schema_version"] and d["algo_version"]
    import json
    json.loads(ob.observe(trending_up(300), symbol="X").to_json())    # 必须可序列化


def t_as_of_falls_back_to_last_trading_day():
    """as_of 落在非交易日 → 取它之前最后一个交易日，**两个日期都写在卡片上**。"""
    df = trending_up(60)
    last = str(df["date"].iloc[-1])[:10]
    card = ob.observe(df, as_of="2099-12-31", symbol="X")
    assert card.as_of == "2099-12-31" and card.as_of_effective == last
    early = ob.observe(df, as_of="1990-01-01", symbol="X")
    assert early.rows_used == 0 and "不足" in early.reasons["last_close"]


def t_setup_thresholds_are_frozen_and_explained():
    """**阈值冻结，而且每个数字的来历写在定义里。**

    先跑收益、再回来调阈值，是这个项目吃过两次亏的做法。
    所以顺序反过来：定义写死 + 说明来历 → 才去看有没有用。
    """
    from cio.technical import setups as st
    assert st.params_fingerprint() == st.FROZEN_FINGERPRINT, (
        f"setup 阈值变了（现在 {st.params_fingerprint()}，冻结值 {st.FROZEN_FINGERPRINT}）"
        f"—— 请同时把 SETUP_VERSION 从 {st.SETUP_VERSION} 升上去")
    assert (st.A_MIN_SPIKE_DAYS, st.B_MIN_CMF, st.B_MIN_OBV_SLOPE) == (5, 0.10, 0.0)
    # C 不是新参数：它就是价区的聚类容差，不引入新的自由度。
    # **断结构，不断值** —— 抄一个 0.5 过去，断值照样通过，
    # 等价区容差改成 0.4 时两边就悄悄分家了。（第九次踩这个坑。）
    assert st.C_MAX_ATR_TO_ZONE == ps.CLUSTER_ATR_MULT
    import ast as _ast
    src = (TECH_DIR / "setups.py").read_text("utf-8")
    linked = False
    for node in _ast.walk(_ast.parse(src)):
        if isinstance(node, _ast.Assign) and any(
                getattr(t, "id", "") == "C_MAX_ATR_TO_ZONE" for t in node.targets):
            linked = isinstance(node.value, _ast.Name) and node.value.id == "CLUSTER_ATR_MULT"
    assert linked, "C_MAX_ATR_TO_ZONE 是抄过去的字面量，不是引用 —— 多了一个自由参数"
    text = "\n".join(st.describe())
    for must in ("基础率", "p90", "不是新参数", "之前定下"):
        assert must in text, f"定义说明里少了「{must}」"


def t_an_event_is_not_a_stock_day():
    """**连着三天成立是一个事件，不是三个样本。**

    不切事件的话，回放会造出大量高度重叠的"样本"：5/10/20 日收益互相覆盖，
    看着几百个，独立信息远没那么多。这正是 build100 在材料闸门上修过的
    同一个缺陷（同一件事被转载三次就顶开闸门），换了个模块又出现一次。
    """
    from cio.technical import setups as st
    d = [f"2026-01-{i:02d}" for i in range(1, 31)]
    # 连续三天成立 → 一个事件
    ser = list(zip(d, [False, True, True, True] + [False] * 26))
    ev = st.derive_events("X", ser)
    assert len(ev) == 1 and ev[0].start == d[1] and ev[0].days == 3, ev
    # 冷却期内重新触发 → 并入同一个事件，且留痕
    ser = list(zip(d, [False, True, False, False, True] + [False] * 25))
    ev = st.derive_events("X", ser)
    assert len(ev) == 1 and ev[0].merged_repeats == [d[4]], ev
    # 冷却期之外 → 两个事件
    ser = list(zip(d, [False, True] + [False] * 8 + [True] + [False] * 20))
    ev = st.derive_events("X", ser)
    assert len(ev) == 2, [(e.start, e.end) for e in ev]


def t_unknown_is_not_the_same_as_false():
    """**"算不出来"和"不成立"必须分得开。** 计数上都不触发，但含义相反。"""
    from cio.technical import setups as st
    short = ob.observe(trending_up(40), symbol="X")
    r = st.evaluate(short)
    assert r["hit"] is False
    assert r["unknown"], "数据不够时 unknown 是空的 —— 那就和'不成立'混了"
    full = st.evaluate(ob.observe(zigzag(300), symbol="X"))
    assert isinstance(full["hit"], bool)


def t_store_never_silently_rewrites_history():
    """**写过的一天不再重写。**

    参数改了之后重跑历史，会把过去每一天按新参数改写——而新参数下的历史
    当然更好看，因为它本来就是拿这段历史调出来的。
    """
    import tempfile
    from pathlib import Path as _P
    from cio.technical import store as sto
    cards = [ob.observe(zigzag(300), symbol="X")]
    with tempfile.TemporaryDirectory() as tmp:
        old = sto.CARD_DIR
        try:
            sto.CARD_DIR = _P(tmp) / "cards"
            n, note = sto.write_day("2026-01-05", cards)
            assert n == 1, note
            n2, note2 = sto.write_day("2026-01-05", cards)
            assert n2 == 0 and "已存在" in note2, note2
            n3, _ = sto.write_day("2026-01-05", cards, force=True)
            assert n3 == 1, "显式 force 时必须能覆盖"
            rows = sto.load_day("2026-01-05")
            assert rows and rows[0]["stamps"]["setup_version"], "没盖版本号"
            assert "setup" in rows[0] and "hit" in rows[0]["setup"]
        finally:
            sto.CARD_DIR = old


def t_review_is_the_screen_kpi_and_it_is_recorded():
    """**筛子的主 KPI 要有地方记，而且要记得住"什么时候判的"。**

    "推出来的值不值得研究"今天就能测，但只有在**判的时候还看不见后续走势**
    的前提下才算数。她定的规格：

        reviewed_at 自动填市场时区 → review_lag_trading_days（交易日，不是日历天）
        → clean / t1 / retrospective 分开统计 → 主 KPI 只看 clean
        → excluded 独立一档，不进分母
        → 同判定重复 mark 幂等，不再靠 stats() 事后去重
    """
    import json
    import tempfile
    from pathlib import Path as _P
    from cio.technical import review as rv
    with tempfile.TemporaryDirectory() as tmp:
        old_p, old_l = rv.REVIEW_PATH, rv.LEGACY_REVIEW_PATH
        try:
            rv.REVIEW_PATH = _P(tmp) / "reviews.jsonl"
            rv.LEGACY_REVIEW_PATH = _P(tmp) / "nonexistent.jsonl"

            # 一、三档判断 + 非法判定被拒
            r = rv.mark("2026-09-04", "A", "worth", "财报后放量")
            assert r["action"] == "written", r
            rv.mark("2026-09-04", "B", "skip", "指数调仓")
            rv.mark("2026-09-04", "C", "unclear")
            assert set(rv.JUDGEMENTS) == {"worth", "skip", "unclear"}
            try:
                rv.mark("2026-09-04", "D", "会涨")
                raise AssertionError("非法判定被收下了")
            except ValueError:
                pass

            # 二、**reviewed_at 自动填，且是市场时区的 offset-aware ISO**
            assert r["reviewed_at"], "没有自动记录复核时间"
            assert ("+" in r["reviewed_at"][10:] or "-" in r["reviewed_at"][10:]), \
                f"不是带偏移量的 ISO：{r['reviewed_at']}"
            assert "T" in r["reviewed_at"], r["reviewed_at"]

            # **必须跟市场时区，不跟机器时区。**
            # 这条得把机器时区**掰到和市场不一样**才有判别力：跑测试的机器
            # 本来就在美东时，两者偏移量相同，用机器时间的实现照样绿。
            #
            # 掰去哪儿**不能写死**。写死 Asia/Shanghai，`CIO_MARKET=cn`
            # （不设这个变量时的默认值）下市场时区本来就是 +08:00 ——
            # 那句"不等于 +08:00"会把正确实现判成错的，而且那种情形下
            # 它对"跟着机器走"根本没有判别力。所以挑一个**此刻偏移量确实
            # 和市场不同**的时区，再和机器实际偏移量比。
            import datetime as _dt
            import os as _os
            import time as _time
            from zoneinfo import ZoneInfo as _ZI
            from cio.schedule import market_now
            _mkt_off = market_now().utcoffset()
            _other = next((z for z in ("UTC", "Asia/Shanghai",
                                       "America/New_York", "Asia/Kolkata",
                                       "Pacific/Kiritimati")
                           if _dt.datetime.now(_ZI(z)).utcoffset() != _mkt_off),
                          None)
            assert _other, "找不到一个和市场偏移量不同的时区，这条断言无从判别"
            _keep = _os.environ.get("TZ")
            try:
                _os.environ["TZ"] = _other
                if hasattr(_time, "tzset"):
                    _time.tzset()
                    machine = _dt.datetime.now().astimezone().isoformat()[-6:]
                    want = market_now().isoformat()[-6:]
                    got = rv.market_stamp()[-6:]
                    assert got == want, \
                        f"复核时间戳跟着机器时区走了（机器 {machine}，市场 {want}，拿到 {got}）"
                    assert got != machine, got
            finally:
                if _keep is None:
                    _os.environ.pop("TZ", None)
                else:
                    _os.environ["TZ"] = _keep
                if hasattr(_time, "tzset"):
                    _time.tzset()

            # 三、**延迟按交易日算：周五信号、周一复核 = 1，不是 3**
            assert rv.trading_days_between(
                "2026-09-04", "2026-09-07T06:00:00-04:00") == 1
            assert rv.trading_days_between(
                "2026-09-04", "2026-09-04T20:00:00-04:00") == 0
            assert rv.trading_days_between(
                "2026-09-03", "2026-09-04T09:00:00-04:00") == 1
            assert rv.trading_days_between("2026-09-04", "") is None
            # 复核早于信号说不通 —— **不猜，返回 None**
            assert rv.trading_days_between(
                "2026-09-04", "2026-09-01T09:00:00-04:00") is None

            # 四、**同判定重复 mark 不写第二行**（不再靠 stats 事后去重）
            n_before = len(rv.REVIEW_PATH.read_text("utf-8").splitlines())
            again = rv.mark("2026-09-04", "A", "worth", "再来一次")
            assert again["action"] == "unchanged", again
            n_after = len(rv.REVIEW_PATH.read_text("utf-8").splitlines())
            assert n_after == n_before, "同判定被重复写进了台账"

            # 改判定才追加，并且带上原判定
            rev = rv.mark("2026-09-04", "B", "worth", "回头看错了")
            assert rev["action"] == "revised", rev
            assert rev["previous_verdict"] == "skip", rev
            assert rv.latest()[("2026-09-04", "B")]["verdict"] == "worth"
            assert rv.revisions() == [(("2026-09-04", "B"), "skip", "worth")]

            # 五、**excluded 独立一档，必须写理由，且不进分母**
            try:
                rv.mark("2026-09-01", "E", "excluded", "")
                raise AssertionError("没写理由的 excluded 被收下了")
            except ValueError as e:
                assert "理由" in str(e), str(e)
            rv.mark("2026-09-01", "E", "excluded", rv.RETROSPECTIVE_CONTAMINATION)

            # 六、分桶：只有当天判的进主 KPI
            rv.mark("2026-09-03", "F", "worth", "隔天才看",
                    reviewed_at="2026-09-04T09:00:00-04:00")
            by_lag = rv.stats()["by_lag"][rv.SETUP_VERSION]
            assert by_lag["clean"]["worth"] == 2, by_lag        # A, B(改判后)
            assert by_lag["t1"]["worth"] == 1, by_lag           # F
            assert by_lag["retrospective"]["excluded"] == 1, by_lag  # E
            rate, n = rv.worth_rate(by_lag["clean"])
            assert n == 3 and abs(rate - 2 / 3) < 1e-9, (rate, n)
            # **excluded 不进分母**
            r2, n2 = rv.worth_rate(by_lag["retrospective"])
            assert n2 == 0 and r2 is None, (r2, n2)

            # 七、**没有分母时返回 None，不是 0%**
            assert rv.worth_rate({"worth": 0, "skip": 0, "unclear": 0}) == (None, 0)

            # 八、老记录没有 reviewed_at → unknown，**不许并进 clean**
            rows = [json.loads(x) for x in
                    rv.REVIEW_PATH.read_text("utf-8").splitlines() if x.strip()]
            rows.append({"as_of": "2026-08-20", "symbol": "OLD", "verdict": "worth",
                         "note": "老台账", "setup_id": rv.SETUP_ID,
                         "setup_version": rv.SETUP_VERSION, "reviewed_at": ""})
            rv.REVIEW_PATH.write_text(
                "\n".join(json.dumps(x, ensure_ascii=False) for x in rows) + "\n",
                "utf-8")
            by_lag = rv.stats()["by_lag"][rv.SETUP_VERSION]
            assert by_lag["unknown"]["worth"] == 1, by_lag
            assert by_lag["clean"]["worth"] == 2, \
                "没有时间戳的老记录被并进了主 KPI —— 我们没有证据说它是当天判的"

            # 九、excluded 之后就不在待复核队列里了
            assert ("2026-09-01", "E") not in rv.pending(
                [("2026-09-01", "E"), ("2026-09-04", "Z")])
            assert ("2026-09-04", "Z") in rv.pending(
                [("2026-09-01", "E"), ("2026-09-04", "Z")])
        finally:
            rv.REVIEW_PATH, rv.LEGACY_REVIEW_PATH = old_p, old_l


def t_snapshot_runs_after_the_close():
    """**收盘之后才存卡片。**

    盘中跑会把一根没走完的 K 线当成当天收盘：量比、CMF、ATR 全算在半天数据上，
    而卡片上写的日期是今天。不报错、图上也看不出来。
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from cio import schedule as S
    ny = ZoneInfo("America/New_York")
    if S.MARKET == "us":
        assert S.is_snapshot_time(datetime(2026, 9, 2, 11, 0, tzinfo=ny))[0] is False
        assert S.is_snapshot_time(datetime(2026, 9, 2, 17, 0, tzinfo=ny))[0] is True
        assert S.is_snapshot_time(datetime(2026, 9, 5, 17, 0, tzinfo=ny))[0] is False
    assert S.SNAPSHOT_WINDOW["us"] != S.PREMARKET_WINDOW["us"], \
        "快照窗口和盘前窗口不该是同一个 —— 一个在收盘后，一个在开盘前"
    import ast
    src = (Path(__file__).resolve().parents[1] / "scripts"
           / "technical_snapshot.py").read_text("utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "main")

    def _calls(node):
        out = {}
        for n in ast.walk(node):
            if isinstance(n, ast.Call):
                nm = getattr(n.func, "attr", getattr(n.func, "id", ""))
                out.setdefault(nm, n.lineno)
        return out

    # build119 之后取数搬进了 `_snapshot_body`，闸门留在 `main`。
    # 所以这条断言跨两个函数，而且**比原来更强**：
    # 不只是"闸在取数之前"，而是"main 里根本够不到取数，
    # 唯一的入口是那个被闸门守着的函数"。
    mc = _calls(fn)
    assert "is_snapshot_time" in mc, "main 里没有收盘闸"
    assert "_snapshot_body" in mc, "main 不再调用取数那一段？结构变了，这条要重写"
    assert mc["is_snapshot_time"] < mc["_snapshot_body"], "收盘闸没跑在取数之前"
    for banned in ("get_universe", "get_history"):
        assert banned not in mc, \
            f"main 里直接取数（{banned}）—— 那条路绕开了收盘闸"
    body_fn = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "_snapshot_body")
    bc = _calls(body_fn)
    assert "get_universe" in bc, "取数不在被守着的那个函数里"


def t_universe_pit_is_judged_per_window():
    """**PIT 取决于区间，不是一个全局布尔。**

    写死 False 会让人以为这件事永远做不了；写死 True 是撒谎。
    快照覆盖到的那几天就是 PIT 的，之前的不是。
    """
    from cio import quant_data as q
    lo, hi, n = q.snapshot_coverage()
    ok, why = q.universe_pit_for("2000-01-01", "2030-01-01")
    assert ok is False and "超出" in why or "一份" in why, why
    if n:
        ok2, why2 = q.universe_pit_for(lo, hi)
        assert ok2 is True, why2


def t_event_carries_full_lineage_not_just_setup_version():
    """**setup 的身份不只是 setup_version。**

    条件 C 是"距上方价区 ≤0.5 ATR"，而价区是 `sr-1.0.0` 算出来的。
    `sr-1.0.0 → sr-1.1.0` 之后，即使三个阈值一个都没改，
    **这条 setup 筛的已经是另一批东西**。只按 setup_version 分组，
    会把两套定义下的事件混成一堆，而且混得毫无痕迹。
    """
    from cio.technical import setups as st
    lin = st.current_lineage()
    assert len(lin) == 4, lin
    assert lin[0] == st.SETUP_VERSION and lin[1] == st.params_fingerprint()
    assert lin[2] == ps.ALGO_VERSION, "血统里没有价区算法版本"
    d = [f"2026-01-{i:02d}" for i in range(1, 21)]
    old = ("setup-1.0.0", "fp", "sr-1.0.0", "signal-card-1.0.0")
    new_ = ("setup-1.0.0", "fp", "sr-1.1.0", "signal-card-1.0.0")   # 只有价区算法变了
    ser = [(d[0], True, old), (d[1], True, old), (d[2], True, new_), (d[3], True, new_)]
    evs = st.derive_events("X", ser)
    assert len(evs) == 2, [(e.start, e.end, e.lineage) for e in evs]
    assert evs[0].ended_by_version_change is True, "定义变了却把事件缝在了一起"
    assert evs[0].lineage[2] == "sr-1.0.0" and evs[1].lineage[2] == "sr-1.1.0"


def t_stored_cards_keep_their_own_versions():
    """**血统从卡片里读，不用当前代码的。**

    半年前的卡片是按当时的算法算的；用今天的版本号给它盖章，
    等于把历史改写成"一直都是这套定义"。
    """
    import json
    import tempfile
    from pathlib import Path as _P
    from cio.technical import store as sto
    with tempfile.TemporaryDirectory() as tmp:
        old = sto.CARD_DIR
        try:
            sto.CARD_DIR = _P(tmp) / "cards"
            sto.write_day("2026-01-05", [ob.observe(zigzag(300), symbol="X")])
            row = sto.load_day("2026-01-05")[0]
            for k in ("schema_version", "algo_version", "setup_version",
                      "setup_fingerprint"):
                assert row["stamps"].get(k), f"卡片上没盖 {k}"
            # 手改成一个古早版本，读回来必须是古早的那个
            p = sto.CARD_DIR / "2026-01-05.jsonl"
            row["stamps"]["algo_version"] = "sr-0.9.0"
            p.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
            ser = sto.hit_series("X")
            assert ser and ser[0][2][2] == "sr-0.9.0", ser
        finally:
            sto.CARD_DIR = old


def t_nan_is_the_third_state():
    """**NaN 既不是 None 也不是数 —— v1 漏掉的第三种状态。**

    2026-09-02 全市场 502 只那一跑暴露：一根缺量的 K 线让 `cmf_20` /
    `obv_slope_20` / `up_down_volume_ratio_20` 全变 NaN，而

        reasons 里什么都不写      NaN 不是 None，`is None` 漏过
        evaluate() 判 False       `NaN > 0.10` 静默返回 False
        unknown 是空的            于是"算不出来"被记成了"不成立"

    任何一只票只要有一根坏 K 线，就被无声地排除在命中之外。
    """
    from cio.technical import numbers as num
    from cio.technical import setups as st
    assert num.finite(float("nan")) is None
    assert num.finite(float("inf")) is None
    assert num.finite(None) is None
    assert num.finite(0.0) == 0.0, "0 是一个真实的数，不能被当成算不出来"
    assert num.finite(True) is True, "布尔字段不该被当成数处理"

    df = zigzag(300)
    bad = df.copy()
    bad.loc[290, "volume"] = float("nan")
    card = ob.observe(bad, symbol="X")           # strict=True：缺原因会直接炸
    for k in ("cmf_20", "obv_slope_20", "up_down_volume_ratio_20"):
        assert card.volume[k] is None, f"{k} 还是 NaN：{card.volume[k]}"
        assert k in card.reasons, f"{k} 是 null 但没写原因"
    r = st.evaluate(card)
    assert r["hit"] is False
    assert "B_accumulation_proxy" in r["unknown"], \
        "NaN 被静默判成'不成立'，没有进 unknown"


def t_panel_health_counts_but_does_not_repair():
    """**面板体检只数，不修。**

    补一根插值出来的 K 线会让所有度量都算得出来、而且看不出是补的——
    那比留一个 null 糟得多。
    """
    from cio.technical import numbers as num
    df = zigzag(60)
    counts, problems = num.panel_health(df)
    assert counts["rows"] == 60 and not problems, (counts, problems)

    dirty = df.copy()
    dirty.loc[10, "volume"] = float("nan")
    dirty.loc[20, "volume"] = 0.0
    dirty.loc[30, "close"] = -1.0
    dirty.loc[40, ["high", "low"]] = [90.0, 110.0]     # high < low
    counts, problems = num.panel_health(dirty)
    assert counts["nan_rows"] == 1, counts
    assert counts["nonpositive_volume"] == 1, counts
    assert counts["nonpositive_close"] == 1, counts
    assert counts["inverted_bars"] == 1, counts
    assert len(problems) == 4, problems
    # 体检结果要出现在卡片上，否则等于没查
    card = ob.observe(dirty, symbol="X")
    assert card.panel_health["nan_rows"] == 1
    assert "panel_health" in card.reasons


def t_scrub_is_exhaustive_including_nested():
    """**兜底必须是穷举的。** 逐个函数去包，新加一个度量就会漏一次，
    而漏了不报错。所以 `scrub` 递归洗整块，`observe` 再兜一道。"""
    from cio.technical import numbers as num
    vals = {"a": float("nan"), "b": 1.0, "c": None,
            "nested": {"x": float("inf"), "y": 2.0}}
    why: dict = {}
    num.scrub(vals, why)
    assert vals["a"] is None and vals["nested"]["x"] is None
    assert vals["b"] == 1.0 and vals["nested"]["y"] == 2.0
    assert "a" in why and "nested.x" in why, why
    # observe 那一道兜底真的跑了（源码结构）
    src = (TECH_DIR / "observer.py").read_text("utf-8")
    assert "scrub(getattr(card, name), card.reasons)" in src, "observe 没有兜底 scrub"


def t_setup_version_moved_even_though_thresholds_did_not():
    """**数字没变，行为变了，版本也必须变。**

    这是她自己那条血统论证的第一次实际应用：`setup_version` 的身份
    不只是三个阈值。NaN 处理改了之后，同一天同一只票可能给出不同结果，
    两版的事件不能混在一起统计。
    """
    from cio.technical import setups as st
    assert st.SETUP_VERSION == "setup-1.0.1", st.SETUP_VERSION
    assert st.params_fingerprint() == st.FROZEN_FINGERPRINT, \
        "阈值本来就不该变 —— 指纹动了说明改错了东西"
    src = (TECH_DIR / "setups.py").read_text("utf-8")
    assert "1.0.0 → 1.0.1" in src and "NaN" in src, \
        "升版本没写清楚为什么 —— 以后没人知道两版差在哪"


def t_v2_gate_decides_whether_rank_decides_who():
    """**闸门决定有没有，家族分决定先看谁。顺序不能反。**

    家族分是分位平均，中位数恒在 0.5 附近——只用它，">0.5" 每天都是
    半个市场，系统永远说不出"今天没有"，而那是 v1 的第一条边界。
    """
    from cio.technical import score as sc
    quiet = [ob.observe(zigzag(300, amp=1.0), symbol=f"Q{i}") for i in range(8)]
    ranked = sc.rank_day(quiet)
    assert all(not r.passed_gate for r in ranked), "这批不该有通过闸门的"
    assert all(r.rank is None for r in ranked), "没通过闸门的不该有排名"
    assert "今天没有" in sc.today_line(ranked), sc.today_line(ranked)
    assert any(r.score is not None for r in ranked), "分数应当仍然算得出（只是不开门）"
    # 分档只是标签：拿到 band 不等于进入队列
    assert any(r.band for r in ranked), "band 应当照常算出来"
    assert all(r.rank is None for r in ranked if not r.passed_gate)


def t_adding_an_indicator_does_not_change_its_family_weight():
    """**这条是 family 重构存在的全部理由。**

    上一版四个平铺指标里有两个来自 volume 块 —— 量能实际拿了 50% 权重，
    而文档写着"等权"。再加一个量能指标就变 60%，不报错、看不出来。

    家族化之后：族间等权固定，**往一族里加成员只稀释族内成员**。
    """
    from cio.technical import score as sc
    cards = [ob.observe(zigzag(300, drift=0.02 * (i + 1)), symbol=f"S{i}")
             for i in range(6)]
    base = sc.rank_day(cards)
    in_score = [f for f in sc.FAMILIES if f.in_score]
    assert len(in_score) >= 4, "进总分的族太少，等权就没意义了"

    # 往 volume 族里塞一个重复成员，族数不变 → 族间权重不变
    fam = next(f for f in sc.FAMILIES if f.name == "volume")
    bigger = sc.Family(fam.name, fam.members + (
        sc.Member("rvol_again", "volume", "rvol_20", sc.HIGHER),), fam.in_score)
    orig = sc.FAMILIES
    try:
        sc.FAMILIES = tuple(bigger if f.name == "volume" else f for f in orig)
        after = sc.rank_day(cards)
        assert len([f for f in sc.FAMILIES if f.in_score]) == len(in_score), \
            "族数变了 —— 那就不是「往族里加成员」了"
        for a, b in zip(base, after):
            # 总分可以略变（族内均值被稀释），但**族的个数、因而权重不变**
            assert set(a.families) == set(b.families), "家族集合变了"
        # 明确验证权重：总分就是进总分那几族的等权平均
        r = after[0]
        got = [r.families[f.name] for f in sc.FAMILIES
               if f.in_score and r.families.get(f.name) is not None]
        assert r.score == round(sum(got) / len(got), 4), (r.score, got)
    finally:
        sc.FAMILIES = orig


def t_directions_and_the_one_judgement_call():
    """方向：距离越近越靠前、其余越高越靠前；**波动没有方向**。

    `UNUSUAL` 是全文唯一一个不是被定义逼出来的选择——两端的极端都算
    不寻常，中间靠后。这条写错不会报错，只会让整族悄悄反过来。
    """
    from cio.technical import score as sc
    vals = [0.1, 0.5, 2.0, 10.0]
    assert sc._percentile_ranks(vals, sc.LOWER)[0] == 1.0
    assert sc._percentile_ranks(vals, sc.HIGHER)[0] == 0.0
    un = sc._percentile_ranks([0.0, 0.25, 0.5, 0.75, 1.0], sc.UNUSUAL)
    assert un[0] == 1.0 and un[-1] == 1.0, f"两端都该靠前：{un}"
    assert un[2] == 0.0, f"中间该靠后：{un}"
    # 并列取平均秩
    tie = sc._percentile_ranks([1.0, 1.0, 2.0], sc.HIGHER)
    assert tie[0] == tie[1], tie
    assert sc._percentile_ranks([None, None], sc.HIGHER) == [None, None]
    # 结构上确认 volatility 用的是非方向聚合
    volf = next(f for f in sc.FAMILIES if f.name == "volatility_extremeness")
    assert all(m.direction == sc.UNUSUAL for m in volf.members), \
        "波动族被赋了方向 —— 那是一个没有依据的假设"
    zone = next(m for f in sc.FAMILIES for m in f.members
                if m.field == "atr_to_nearest_zone_above")
    assert zone.direction == sc.LOWER, "距离的方向写反了"


def t_missing_member_is_dropped_not_filled():
    """**缺的成员直接不算，不补 0、不补 0.5。**

    补 0 把"没数据"变成"这一维很差"，补 0.5 变成"中性"——两个都是
    凭空造出来的结论。族内全缺时，整族是 None 且不进总分。
    """
    from cio.technical import score as sc
    # 单调上涨 = 没有 swing 点 = 没有价区 → structure 的 zone_distance 必缺
    cards = [ob.observe(trending_up(300, step=0.1 * (i + 1)), symbol=f"T{i}")
             for i in range(3)]
    assert all(c.price_structure.get("atr_to_nearest_zone_above") is None
               for c in cards), "前提没成立"
    ranked = sc.rank_day(cards)
    for r in ranked:
        assert "zone_distance" in r.missing.get("structure", []), r.missing
        assert "zone_distance" not in r.members, "缺的成员被填了值"
        # 没有基准 → relative_strength 整族算不出来，且不进总分
        assert r.families.get("relative_strength") is None
        got = [r.families[f.name] for f in sc.FAMILIES
               if f.in_score and r.families.get(f.name) is not None]
        assert r.score == round(sum(got) / len(got), 4), (r.score, got)


def t_bands_are_labels_not_a_gate():
    """**分档不能当进入条件。** 中位数恒在 0.5 附近，">0.5" 每天都是半个市场。"""
    from cio.technical import score as sc
    assert sc.band_of(0.9) == "HIGH" and sc.band_of(0.75) == "REVIEW"
    assert sc.band_of(0.55) == "WATCH" and sc.band_of(0.2) == "LOW"
    assert sc.band_of(None) == ""
    # 高分但没通过闸门 → 仍然没有排名、不进队列
    import ast
    src = (TECH_DIR / "score.py").read_text("utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "rank_day")
    body = ast.get_source_segment(src, fn) or ""
    assert "r.passed_gate and r.score is not None" in body, \
        "排名的入选条件里没有 passed_gate —— 分档就变成闸门了"
    assert "band" not in body.split("passed = ")[1][:200], \
        "band 参与了入选判断"


def t_score_params_are_frozen_and_equal_weighted():
    """**等权不是因为它最优，是因为没有任何东西可以用来定权重。**"""
    from cio.technical import score as sc
    assert sc.params_fingerprint() == sc.FROZEN_FINGERPRINT, (
        f"家族/成员/方向变了（{sc.params_fingerprint()}），"
        f"请同时升 SCORE_VERSION（现在 {sc.SCORE_VERSION}）")
    assert sc.SCORE_VERSION.startswith("score-2."), sc.SCORE_VERSION
    src = (TECH_DIR / "score.py").read_text("utf-8")
    assert "没有任何东西可以用来定权重" in src, "没写清楚为什么等权"
    assert "不许用来定权重" in src, "没写明回测结果不能用来定权重"
    import ast
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Assign) and any(
                "weight" in getattr(t, "id", "").lower() for t in node.targets):
            raise AssertionError("出现了权重参数 —— 那是一个没有依据的自由度")


def t_nr7_stays_out_of_the_score_and_says_why():
    """**NR7 只代表收缩这一端，不许混进双边异常族。**

    她复核时提的：族里其他成员是"离典型状态多远"（双边），
    NR7 是单边证据。加进去整族会天然偏向 compression——**混了方向，
    而且从分数上完全看不出来混了。**

    要用它就得先拆成 compression / expansion 两个单边量。v1 不做，
    所以它继续显示在卡片上、不进分。这条必须写死，否则下一个人
    "顺手补全一下波动族"就把它加回来了，而且不会有任何东西拦住。
    """
    from cio.technical import score as sc
    assert any(b == "volatility" and f == "is_nr7"
               for b, f, _ in sc.EXCLUDED_FROM_SCORE), "排除名单里没有 NR7"
    for block, fld, why in sc.EXCLUDED_FROM_SCORE:
        assert len(why) > 30, f"{block}.{fld} 排除了但没写理由"
        # 排除名单里的字段不许出现在任何一个族里
        for fam in sc.FAMILIES:
            for m in fam.members:
                assert not (m.block == block and m.field == fld), \
                    f"{block}.{fld} 被排除了，却出现在 {fam.name} 族里"
    # 它必须仍然在卡片上（排除的是"进分"，不是"不测量"）
    card = ob.observe(zigzag(300), symbol="NR")
    assert "is_nr7" in card.volatility, "NR7 从卡片上消失了 —— 那不是排除，是删掉了"
    # 族名必须自带 extremeness，否则 0.9 会被读成"高波动是利好"
    names = [f.name for f in sc.FAMILIES]
    assert "volatility_extremeness" in names, names
    assert not any(n in names for n in ("volatility_strength", "volatility_quality"))


def t_coverage_travels_with_the_score():
    """**2/5 族的 0.82 和 5/5 族的 0.78 不是同质的数。**

    缺的族不进总分、剩下的重新等权是对的，但只做到这里，两个分数会
    印在同一列里被横着比。所以覆盖度和分数**必须同框**，而且低于下限时
    **不报分**——信息不够时的正确输出是"说不出"，不是一个漂亮的高分。
    """
    from cio.technical import score as sc
    cards = [ob.observe(zigzag(300, drift=0.02 * (i + 1)), symbol=f"C{i}")
             for i in range(5)]
    ranked = sc.rank_day(cards)
    poss = len([f for f in sc.FAMILIES if f.in_score])
    for r in ranked:
        assert r.families_possible == poss
        got = [f.name for f in sc.FAMILIES
               if f.in_score and r.families.get(f.name) is not None]
        assert r.families_used == len(got), (r.families_used, got)
        assert r.coverage == round(len(got) / poss, 4)
        if r.families_used < sc.MIN_FAMILIES:
            assert r.score is None, "覆盖度不足却报了分"
            assert r.no_score_reason, "没有分数，却说不出为什么"
            assert r.rank is None, "没有分数却排了名"
        else:
            assert r.score is not None

    # **不是构造出来的极端情形——上市不满一年的票天生就是这样。**
    # 252 日分位算不出来 → structure / volatility / relative_strength 三族全缺，
    # 只剩 2 族。这种票**不该拿到一个自信的分数**。
    short = [ob.observe(trending_up(40, step=0.05 * (i + 1)), symbol=f"NEW{i}")
             for i in range(4)]
    rs = sc.rank_day(short)
    assert all(r.families_used < sc.MIN_FAMILIES for r in rs), \
        [r.families_used for r in rs]
    assert all(r.score is None for r in rs), "历史不足一年却报了分"
    assert all("低于下限" in r.no_score_reason for r in rs), \
        [r.no_score_reason for r in rs]
    assert all(r.rank is None for r in rs)

    # **一族都算不出来的极端**（取数回来是空面板就是这样）：也必须是 None。
    # 这一支在任何正常夹具下都是死代码，所以要单独造出来走一遍——
    # **走不到的分支，等于没有被任何断言保护。**
    blank = ob.observe(zigzag(300), symbol="BLANK")
    for blk in ("price_structure", "volume", "relative_strength", "volatility"):
        setattr(blank, blk, {})
    rb = sc.rank_day([blank])[0]
    assert rb.families_used == 0, rb.families_used
    assert rb.score is None, "一族都没有还给了分"
    assert rb.no_score_reason, "没有分数却说不出为什么"

    # **没有分数 ⟺ 说得出为什么。** 这条不依赖任何夹具凑出特定覆盖度：
    # 它同时拦住"折成 0 分"（有分却带理由）和"静默变 None"（没分却没理由）。
    for r in list(ranked) + list(rs) + [rb]:
        assert (r.score is None) == bool(r.no_score_reason), \
            f"{r.symbol}: score={r.score} reason={r.no_score_reason!r}"

    # 覆盖度必须**印在给人看的出口上**，不只是存在字段里。
    # 直接构造 Ranked 来断言——**不要写成 `if scored is not None:`**，
    # 那样夹具没造出通过闸门的票时，这条断言就白写了。
    synth = sc.Ranked(symbol="COV", as_of="2026-09-05", passed_gate=True,
                      score=0.82, band="REVIEW", families={"structure": 0.8},
                      families_used=2, families_possible=5, coverage=0.4, rank=1,
                      within_budget=True)
    text = "\n".join(sc.describe(synth))
    assert "覆盖度" in text and "2/5" in text, \
        "描述里没印覆盖度 —— 人就会横着比不同覆盖度的分数\n" + text
    assert "族" in sc.today_line([synth]), sc.today_line([synth])
    # **超预算 ≠ 没分数。** 合成一个数就说不清"没进队列"是今天太忙，
    # 还是这只票的信息本来就不够。
    thin_hit = sc.Ranked(symbol="THIN", passed_gate=True, score=None,
                         families_used=2, families_possible=5,
                         no_score_reason="覆盖度 2/5 低于下限 3/5")
    line = sc.today_line([synth, thin_hit])
    assert "超出今日注意力预算" not in line, "没分数的票被算成了超预算\n" + line
    assert "覆盖度不足" in line and "THIN" in line, line
    # 没有分数的那条也要说得出话，且不能被印成"分数很低"
    nos = sc.Ranked(symbol="THIN", as_of="2026-09-05", passed_gate=True,
                    score=None, families_used=2, families_possible=5,
                    coverage=0.4, no_score_reason="覆盖度 2/5 低于下限 3/5")
    t2 = "\n".join(sc.describe(nos))
    assert "没有分数" in t2 and "覆盖度 2/5" in t2, t2
    assert "0.0" not in t2, "没有分数被印成了 0 分"


def t_market_wide_null_is_not_a_per_name_gap():
    """**同一个事实重复 502 次，不会自己变成一个结论。**

    2026-09-04 的真实情形：SPY 面板对齐后只剩 20–63 天，于是全市场
    每一只票的 `excess_mkt_63` 同时变 null，而板块超额完全正常。
    系统当时的表现是——在 502 张卡片上各写一句"该字段是 null"，
    没有一处说"这一路基准坏了"。

    **每个 null 都有原因**解决的是"这一格为什么空"；
    它解决不了"这一整列为什么空"。
    """
    import pandas as pd
    from cio.technical import sweep
    n = 320
    d = pd.bdate_range(start="2024-01-01", periods=n)
    sector = pd.DataFrame({"date": d, "close": [100 * (1.0003 ** i) for i in range(n)]})
    spy_short = pd.DataFrame({"date": d[-40:],
                              "close": [100 * (1.0002 ** i) for i in range(40)]})
    cards = [ob.observe(zigzag(n, drift=0.02 * (k + 1)), bench=spy_short,
                        sector_bench=sector, symbol=f"S{k}", sector_symbol="XLK")
             for k in range(8)]
    # 前提：这正是她那天遇到的形状 —— 大盘超额全空、板块超额全有
    assert all(c.relative_strength.get("excess_mkt_63") is None for c in cards)
    assert all(c.relative_strength.get("excess_sector_63") is not None for c in cards)

    asym = sweep.benchmark_asymmetry(cards)
    fields = [a for a, _, _, _ in asym]
    assert "excess_mkt_63" in fields, f"没认出大盘基准坏了：{asym}"
    text = "\n".join(sweep.report(cards))
    assert "一路基准坏了" in text, text
    assert "excess_mkt_63" in text and "excess_sector_63" in text, text

    # **不对称判据不靠阈值：两个基准都好的时候不许报警。**
    ok = [ob.observe(zigzag(n, drift=0.02 * (k + 1)), bench=sector,
                     sector_bench=sector, symbol=f"OK{k}", sector_symbol="XLK")
          for k in range(8)]
    assert not sweep.benchmark_asymmetry(ok), sweep.benchmark_asymmetry(ok)
    assert "两个基准都正常" in "\n".join(sweep.report(ok))

    # 扫描只数不改（和 panel_health 一样）
    before = [dict(c.relative_strength) for c in cards]
    sweep.report(cards)
    assert [dict(c.relative_strength) for c in cards] == before, "扫描改了卡片"

    # **扫出来没人看 = 没扫。** 快照必须真的调它。
    src = (Path(__file__).resolve().parent / "technical_snapshot.py"
           ).read_text("utf-8")
    assert "sweep.report(" in src, "快照没有调用全市场扫描 —— 那就等于没做"


def t_coverage_shows_families_and_items():
    """**5/5 族不等于信息齐全。** 一族只要有一个成员算得出来就算"这一族有"。

    她那两张卡片写着"覆盖度 5/5（100%）"，三行之后又写着
    "缺：relative_strength 少了 excess_mkt_63"——**卡片自己打自己的脸。**
    """
    from cio.technical import score as sc
    r = sc.Ranked(symbol="BBY", passed_gate=True, score=0.8554, band="HIGH",
                  rank=1, within_budget=True, families_used=5, families_possible=5,
                  coverage=1.0, families={f.name: 0.8 for f in sc.FAMILIES},
                  missing={"relative_strength": ["excess_mkt_63"]})
    text = "\n".join(sc.describe(r))
    mtot = sum(len(f.members) for f in sc.FAMILIES)
    assert f"{mtot - 1}/{mtot} 项" in text, text
    assert "5/5 族" in text, text
    assert "100%" not in text, "缺着成员还印 100% —— 那正是要修的那句\n" + text


def t_short_benchmark_is_reported_not_swallowed():
    """**"取到了"和"取全了"是两回事。**

    30 行的 SPY 面板照样通过 `len(df)`，然后让全市场的大盘超额同时变 null。
    """
    import ast
    src = (Path(__file__).resolve().parents[1] / "src" / "cio"
           / "quant_data.py").read_text("utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "get_benchmark")
    body = ast.get_source_segment(src, fn) or ""
    assert "benchmark_rows" in body, "基准没记行数 —— 短了看不出来"
    assert "benchmark_short" in body, "基准短了没有任何提示"
    cmps = [n for n in ast.walk(fn) if isinstance(n, ast.Compare)]
    assert any("days" in ast.dump(c) and "len" in ast.dump(c) for c in cmps), \
        "只记了行数却没和请求的天数比 —— 那个数不会自己变成告警"

    # **AST 只能证明那行代码在，不能证明它说的是实话。** 真调一遍：
    # 换掉取数函数（不联网），喂 40 行，看它报的是不是 40。
    import pandas as pd
    from cio import quant_data as qd
    real, real_mkt = qd._yf_hist, qd.MARKET
    try:
        qd.MARKET = "us"
        d = pd.bdate_range(start="2024-01-01", periods=40)
        qd._yf_hist = lambda sym, days: pd.DataFrame(
            {"date": d, "open": [1.0] * 40, "high": [1.0] * 40,
             "low": [1.0] * 40, "close": [1.0] * 40, "volume": [1.0] * 40})
        st: dict = {}
        out = qd.get_benchmark(days=400, status=st)
        assert out is not None and len(out) == 40
        assert st.get("benchmark_rows") == 40, \
            f"报的行数不是真实行数：{st.get('benchmark_rows')}"
        assert st.get("benchmark_want") == 400, st.get("benchmark_want")
        assert st.get("benchmark_short"), "只取到 40/400 行却没有任何提示"

        # 取全了就不该报警，否则这盏灯常亮，等于没有
        full = pd.bdate_range(start="2024-01-01", periods=400)
        qd._yf_hist = lambda sym, days: pd.DataFrame(
            {"date": full, "open": [1.0] * 400, "high": [1.0] * 400,
             "low": [1.0] * 400, "close": [1.0] * 400, "volume": [1.0] * 400})
        st2: dict = {}
        qd.get_benchmark(days=400, status=st2)
        assert st2.get("benchmark_rows") == 400, st2.get("benchmark_rows")
        assert not st2.get("benchmark_short"), "取全了还报警 —— 常亮的灯没有用"
    finally:
        qd._yf_hist, qd.MARKET = real, real_mkt


def t_one_nan_in_the_benchmark_does_not_erase_the_whole_market():
    """**502 张卡片、大盘超额全空、板块超额全好。** 真实发生过（2026-09-04）。

    原因是 SPY 最后一根收盘是 NaN（yfinance 尾行常见）。
    `_ret()` 写的是 `series[-n-1] <= 0`——防了分母 ≤0，**没防 NaN**，
    而 `NaN <= 0` 是 `False`，一路放行返回 NaN，下游 `scrub()` 收成
    null 加一句原因，看起来像"处理过了"。

    形状本身就是指纹：**三个窗口一起空**，因为分子是同一个 `series[-1]`；
    斜率那一支照算，因为它的推导式里 `NaN > 0` 是 False、顺手滤掉了。

    我第一次诊断成"SPY 面板取短了"，是被 `rs_mkt_samples=405` 骗的——
    那个数当时数的是"对齐了几天"，不是"几天能用"。**两个都修。**
    """
    import pandas as pd
    from cio.technical import relative_strength as rsm
    n = 405
    d = pd.bdate_range(start="2024-01-01", periods=n)
    stock = pd.DataFrame({"date": d, "close": [100 * (1.0004 ** i) for i in range(n)]})
    sector = pd.DataFrame({"date": d, "close": [100 * (1.0003 ** i) for i in range(n)]})
    spy = [100 * (1.0002 ** i) for i in range(n)]
    spy[-1] = float("nan")                       # **只有最后一根**
    bench = pd.DataFrame({"date": d, "close": spy})

    v, _ = rsm.measure(stock, bench=bench, sector_bench=sector, sector_symbol="XLK")
    for w in rsm.EXCESS_WINDOWS:
        assert v[f"excess_mkt_{w}"] is not None, \
            f"一根 NaN 就把 excess_mkt_{w} 抹掉了（全市场同时发生）"
        assert v[f"excess_sector_{w}"] is not None
    # 样本数说的必须是**可用天数**，不是对齐天数
    assert v["rs_mkt_samples"] == n - 1, \
        f"样本数把 NaN 也数进去了：{v['rs_mkt_samples']} —— 那是个安慰量，不是诊断量"
    assert v["rs_sector_samples"] == n

    # 端点是 NaN 的直接单测（`NaN <= 0` 为 False，老实现会返回 NaN）
    good = [float(i + 1) for i in range(30)]
    assert rsm._ret(good, 21) is not None
    nan_last = good[:-1] + [float("nan")]
    assert rsm._ret(nan_last, 21) is None, "分子是 NaN 却算出了收益率"
    nan_base = list(good)
    nan_base[-22] = float("nan")
    assert rsm._ret(nan_base, 21) is None, "分母是 NaN 却算出了收益率"
    assert rsm._ret([0.0] + good, 30) is None, "分母 ≤0 的老保护不能丢"

    # **个股侧的 NaN 也要丢。** 只防基准侧，个股停牌那天照样把 NaN 收进来。
    bad_stock = [100 * (1.0004 ** i) for i in range(n)]
    bad_stock[-1] = float("nan")
    v2, _ = rsm.measure(pd.DataFrame({"date": d, "close": bad_stock}),
                        bench=pd.DataFrame({"date": d,
                                            "close": [100 * (1.0002 ** i)
                                                      for i in range(n)]}),
                        sector_bench=sector, sector_symbol="XLK")
    assert v2["rs_mkt_samples"] == n - 1, \
        f"个股侧的 NaN 被当成样本了：{v2['rs_mkt_samples']}"
    for w in rsm.EXCESS_WINDOWS:
        assert v2[f"excess_mkt_{w}"] is not None, "个股侧一根 NaN 就抹掉了超额"

    # 原因不许说错。长度够、只是端点不可用时，不能写成"样本不足"
    short_bench = pd.DataFrame({"date": d[-10:], "close": [100.0] * 10})
    _, w2 = rsm.measure(stock, bench=short_bench, sector_bench=sector,
                        sector_symbol="XLK")
    why = " ".join(str(x) for x in w2.values())
    assert why, "全都算不出来却一句原因都没有"

    # **取数那层要把"最后一根坏"和"中间有几根坏"说成两件事**——
    # 只断言"有一句提示"是不够的：说错话的实现也有一句提示。
    from cio import quant_data as qd
    real, real_mkt = qd._yf_hist, qd.MARKET

    def _bench_status(nan_at):
        cl = [1.0] * 400
        cl[nan_at] = float("nan")
        qd._yf_hist = lambda sym, days: pd.DataFrame(
            {"date": pd.bdate_range(start="2024-01-01", periods=400),
             "open": [1.0] * 400, "high": [1.0] * 400, "low": [1.0] * 400,
             "close": cl, "volume": [1.0] * 400})
        st: dict = {}
        qd.get_benchmark(days=400, status=st)
        return st

    try:
        qd.MARKET = "us"
        last = _bench_status(-1)
        mid = _bench_status(100)
        assert last.get("benchmark_last_bad") is True
        assert mid.get("benchmark_last_bad") is False
        assert last.get("benchmark_last_note") != mid.get("benchmark_last_note"), \
            "最后一根坏和中间坏说的是同一句话 —— 那句话就没有信息"
        assert "最后一根" in str(last.get("benchmark_last_note")), \
            last.get("benchmark_last_note")
        assert not last.get("benchmark_short"), "400 行不短，不该报短"
    finally:
        qd._yf_hist, qd.MARKET = real, real_mkt

    # 报了没人印 = 没报
    snap = (Path(__file__).resolve().parent / "technical_snapshot.py"
            ).read_text("utf-8")
    assert "benchmark_last_note" in snap, "基准的 NaN 提示没有印出来"


def t_a_lamp_that_is_always_on_is_the_same_defect_as_one_that_never_lights():
    """**修完之后的警告必须说"现在是什么"，不是"修之前会怎样"。**

    build114 第一版印的是：

        成对基准对称，两个基准都正常                      ← 判据说没事
        **基准最后一根收盘是 NaN** —— 会让全市场的大盘超额同时变 null  ← 警告说要出事

    第二句在修完之后是**假的**：那根 NaN 会被 `align()` 丢掉，超额照算。
    而 yfinance 的未落定尾行**每天都有**——于是这盏灯天天亮，
    报一个不会发生的故障。**常亮的灯和不亮的灯是同一种缺陷。**

    同时钉住那个真实的代价：两个基准的截止日会差一天，而这件事
    **必须出现在卡片上**，不能只活在我脑子里。
    """
    import pandas as pd
    from cio.technical import observer as tob
    from cio.technical import sweep
    from cio import quant_data as qd

    n = 405
    d = pd.bdate_range(start="2024-01-01", periods=n)
    spy = [100 * (1.0002 ** i) for i in range(n)]
    spy[-1] = float("nan")
    bench = pd.DataFrame({"date": d, "close": spy})
    sector = pd.DataFrame({"date": d, "close": [100 * (1.0003 ** i) for i in range(n)]})
    base = [100 + 0.05 * i for i in range(n)]
    panel = pd.DataFrame({"date": d, "open": base, "high": [x + 0.6 for x in base],
                          "low": [x - 0.6 for x in base], "close": base,
                          "volume": [1e6 + 2e5 * (i % 9) for i in range(n)]})
    cards = [tob.observe(panel, bench=bench, sector_bench=sector,
                         symbol=f"S{k}", sector_symbol="XLK") for k in range(4)]

    # 一、超额照算（说明警告里那句"会变 null"确实不成立了）
    for c in cards:
        assert c.relative_strength.get("excess_mkt_63") is not None

    # 二、截止日差一天，而且**写在卡片上**
    m = cards[0].relative_strength.get("rs_mkt_as_of")
    s = cards[0].relative_strength.get("rs_sector_as_of")
    assert m and s and m != s, (m, s)
    assert m < s, "被丢掉的应该是大盘那一路的最后一天"

    # 三、扫描要把这件事说出来
    text = "\n".join(sweep.report(cards))
    assert "截止日不一样" in text and m in text and s in text, text

    # 四、**警告的措辞**：不许再声称会让超额变 null
    real, real_mkt = qd._yf_hist, qd.MARKET
    try:
        qd.MARKET = "us"
        cl = [1.0] * 400
        cl[-1] = float("nan")
        qd._yf_hist = lambda sym, days: pd.DataFrame(
            {"date": pd.bdate_range(start="2024-01-01", periods=400),
             "open": [1.0] * 400, "high": [1.0] * 400, "low": [1.0] * 400,
             "close": cl, "volume": [1.0] * 400})
        st: dict = {}
        qd.get_benchmark(days=400, status=st)
        note = str(st.get("benchmark_last_note") or "")
        assert note, "尾行 NaN 一句话都不说也不行 —— 它确实有代价"
        assert "变 null" not in note and "同时变" not in note, \
            "警告还在声称一个修完之后不会发生的后果：\n" + note
        assert "丢掉" in note, "没说清楚它被怎么处理了：\n" + note
    finally:
        qd._yf_hist, qd.MARKET = real, real_mkt

    # 五、行数措辞：拿到比要的多不是错（_yf_period 走的是 2y/5y 档）
    snap = (Path(__file__).resolve().parent / "technical_snapshot.py"
            ).read_text("utf-8")
    assert "至少要" in snap, "「要 400 行」会让 1255 看起来像出错"


def t_changing_what_a_field_means_must_move_the_schema_version():
    """**我在自己新加的代码上违反了自己定的血统纪律。**

    build114 改了 `align()`，于是 `rs_mkt_samples` 同一份输入给出不同的数
    （405 → 404），而这个数说的东西也变了：从"对齐了几天"变成"几天能用"。
    **那是语义变了。** 卡片模块开头写着"改字段含义必须升版本"——我没升。

    后果很具体：她的 `2026-09-04.jsonl` 被三个版本的代码各写过一遍，
    三次都盖 `signal-card-1.0.0` 的章，`version_drift()` 会报告"全是同一版"。
    **内容不同，图章相同**，正是 build109/110 那条纪律要防的事。

    语义变了没法自动检测，但**字段集变了可以**——所以有一条字段名指纹。
    """
    from cio.technical import SCHEMA_VERSION

    assert SCHEMA_VERSION == "signal-card-1.1.0", SCHEMA_VERSION
    src = (TECH_DIR / "__init__.py").read_text("utf-8")
    assert "1.0.0 → 1.1.0" in src, "升了版本却没写清楚为什么，以后没人知道两版差在哪"
    assert "rs_mkt_samples" in src, "没写明是哪个字段的语义变了"

    # 指纹必须只跟字段名走，**不跟具体这张卡片算出了什么走**
    fps = set()
    for n, wb in ((405, True), (300, True), (60, False)):
        d = pd.bdate_range(start="2024-01-01", periods=n)
        c = [100 + 0.05 * i for i in range(n)]
        b = (pd.DataFrame({"date": d, "close": [100 * (1.0002 ** i) for i in range(n)]})
             if wb else None)
        card = ob.observe(pd.DataFrame({
            "date": d, "open": c, "high": [x + 0.5 for x in c],
            "low": [x - 0.5 for x in c], "close": c,
            "volume": [1e6 + 3e5 * (i % 7) for i in range(n)]}),
            bench=b, sector_bench=b, symbol="F",
            sector_symbol="XLK" if wb else "")
        fps.add(ob.card_fields_fingerprint(card))
    assert len(fps) == 1, f"指纹跟着数据变了，那它每天都红，等于没有：{fps}"
    assert fps.pop() == ob.FROZEN_FIELDS_FINGERPRINT, (
        "卡片字段集变了 —— 先回答：这次是加字段（可以不升版本），"
        "还是改语义（必须升 SCHEMA_VERSION）？")

    # 新加的两个字段确实在契约里
    d = pd.bdate_range(start="2024-01-01", periods=405)
    c = [100 + 0.05 * i for i in range(405)]
    b = pd.DataFrame({"date": d, "close": [100 * (1.0002 ** i) for i in range(405)]})
    card = ob.observe(pd.DataFrame({
        "date": d, "open": c, "high": [x + 0.5 for x in c],
        "low": [x - 0.5 for x in c], "close": c, "volume": [1e6] * 405}),
        bench=b, sector_bench=b, symbol="F", sector_symbol="XLK")
    for f in ("rs_mkt_as_of", "rs_sector_as_of", "rs_mkt_samples"):
        assert f in card.relative_strength, f


def t_the_review_ledger_is_not_a_trading_day():
    """**复核台账被当成了一个交易日。** 她跑一条诊断命令时掉出来的：

        已存日期 ['2026-09-01', '2026-09-04', 'reviews']

    `reviews.jsonl` 和卡片存在同一个目录，而 `dates()` 是 `glob("*.jsonl")`
    取文件名。`events()` / `version_drift()` / `hit_series()` 全都遍历
    `dates()`——**台账的行一直在被当作信号卡片读。**

    今天没出事，只因为台账的行里没有 `symbol`、恰好被跳过。
    **"恰好没出事"和"不会出事"是两回事。**

    修两条，只修一条都不够：台账搬出卡片目录（不再有可污染的东西）；
    `dates()` 只认日期形状（万一又有人往里放东西）。
    """
    import tempfile
    from cio.technical import review, store
    with tempfile.TemporaryDirectory() as td:
        card_dir = Path(td) / "technical_cards"
        card_dir.mkdir(parents=True)
        old_card, old_rev = store.CARD_DIR, review.REVIEW_PATH
        old_legacy = review.LEGACY_REVIEW_PATH
        try:
            store.CARD_DIR = card_dir
            review.REVIEW_PATH = Path(td) / "technical_reviews" / "reviews.jsonl"
            review.LEGACY_REVIEW_PATH = card_dir / "reviews.jsonl"

            # 一、台账不许再落进卡片目录
            assert review.REVIEW_PATH.parent != store.CARD_DIR, \
                "台账还在卡片目录里 —— 污染源没有搬走"

            # 二、就算有人往卡片目录里放东西，也不许被当成交易日
            (card_dir / "2026-09-04.jsonl").write_text(
                '{"symbol":"AAA","setup":{"hit":true},"stamps":{}}\n', "utf-8")
            (card_dir / "reviews.jsonl").write_text(
                '{"as_of":"2026-09-04","symbol":"AAA","verdict":"worth"}\n', "utf-8")
            (card_dir / "backup-2026.jsonl").write_text("{}\n", "utf-8")
            got = store.dates()
            assert got == ["2026-09-04"], f"非日期文件被当成交易日了：{got}"
            assert "reviews" not in got and "backup-2026" not in got
            # 形状检查必须是**日期形状**，不是"含个数字就行"：
            # `backup-2026` 里有数字，放松了它就又混进来了
            for junk in ("backup-2026", "2026-09", "20260904", "2026-9-4",
                         "2026-09-04-old"):
                (card_dir / f"{junk}.jsonl").write_text("{}\n", "utf-8")
            assert store.dates() == ["2026-09-04"], \
                f"形状检查太松，混进来了：{store.dates()}"
            for junk in ("backup-2026", "2026-09", "20260904", "2026-9-4",
                         "2026-09-04-old"):
                (card_dir / f"{junk}.jsonl").unlink()

            # **跳过了什么要说出来**，否则又是一次静默过滤
            (card_dir / "stray.jsonl").write_text("{}\n", "utf-8")
            import logging
            seen = []

            class _Grab(logging.Handler):
                def emit(self, rec):
                    seen.append(rec.getMessage())

            h = _Grab()
            store.log.addHandler(h)
            try:
                store.dates()
            finally:
                store.log.removeHandler(h)
            assert any("stray" in m for m in seen), \
                f"跳过了非日期文件却一声不吭 —— 静默过滤又来了：{seen}"
            (card_dir / "stray.jsonl").unlink()

            # 三、旧位置的台账要搬过去，而且**不双写**
            review.LEGACY_REVIEW_PATH.write_text(
                '{"as_of":"2026-09-01","symbol":"BBB","verdict":"skip"}\n', "utf-8")
            note = review.migrate_if_needed()
            assert note and "搬出" in note, note
            assert review.REVIEW_PATH.exists(), "没搬过去"
            assert not review.LEGACY_REVIEW_PATH.exists(), \
                "旧文件还在 —— 两个地方各一份，迟早对不上"
            assert (card_dir / "reviews.jsonl.moved").exists(), \
                "旧文件被删了 —— 应该是改名让位，不是删除"
            rows = review._load()
            assert any(r.get("symbol") == "BBB" for r in rows), "搬过去把内容弄丢了"
            # 搬完之后卡片目录里只剩真正的一天
            assert store.dates() == ["2026-09-04"], store.dates()
        finally:
            store.CARD_DIR = old_card
            review.REVIEW_PATH, review.LEGACY_REVIEW_PATH = old_rev, old_legacy


def t_backtest_can_never_feed_back_into_the_definition():
    """**看完收益不许回头调阈值。** 这条写成 import 约束，不是写成承诺。

    `score.py` 和 `setups.py` 都不许 import `backtest` —— 一旦能 import，
    "根据回测结果调一下"就只差一行代码，而那一行不会有任何东西拦住。
    """
    import ast
    for name in ("score.py", "setups.py"):
        tree = ast.parse((TECH_DIR / name).read_text("utf-8"))
        for n in ast.walk(tree):
            mods = []
            if isinstance(n, ast.ImportFrom):
                mods = [n.module or ""] + [a.name for a in n.names]
            elif isinstance(n, ast.Import):
                mods = [a.name for a in n.names]
            assert not any("backtest" in str(m) for m in mods), \
                f"{name} import 了 backtest —— 定义层不许看见结果层"


def t_controls_are_not_matched_on_the_setup_itself():
    """**对照组不能匹配 setup 自己的成分。**

    匹配掉"距上方价区的距离"和"放量天数"，剩下的差异必然接近零，
    而那个零什么都不说明。匹配的应当是混淆项：同日、同板块、相近波动。
    """
    import ast
    src = (TECH_DIR / "backtest.py").read_text("utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "pick_controls")
    body = ast.get_source_segment(src, fn) or ""
    for banned in ("atr_to_nearest_zone_above", "days_rvol_over_1_5_of_20", "cmf_20"):
        assert banned not in body, f"对照组匹配用到了 setup 的成分 {banned}"
    assert "atr_percentile_252" in body and "sectors" in body, "没有匹配混淆项"
    # 通过闸门的票不能当对照
    assert 'evaluate(c)["hit"]' in body, "对照池里可能混进了同样命中的票"


def t_backtest_says_what_it_cannot_claim():
    """**报告必须先说这份结果不能声称什么。**

    一份不带前提的回测数字，读者只会记住那个百分比。
    """
    from cio.technical import backtest as bt
    surv = {"universe": 10, "n_late": 1, "late_entrants": [("X", "2026-01-01")],
            "note": "n"}
    rep = {"events": [], "n_events": 0, "n_event_days": 0,
           "day_diff": {h: [] for h in bt.HORIZONS}, "setup_id": "S",
           "setup_version": "v", "horizons": list(bt.HORIZONS)}
    text = "\n".join(bt.summarize(rep, surv))
    for must in ("不是样本外检验", "幸存者偏差", "不许回头调阈值"):
        assert must in text, f"报告里少了「{must}」"
    assert "估不出任何东西" in text, "样本量不足时没有明说"


TESTS = [
    ("**字段名与输出字符串里没有禁用词**", t_no_banned_words),
    ("**跑出来的卡片上也没有禁用词**", t_the_card_itself_carries_no_judgement),
    ("**量能那组叫 proxy，而且不是一个分数**", t_proxy_is_named_proxy),
    ("observe 是纯函数（两次一致、不改输入）", t_observe_is_pure),
    ("**不看时钟、不联网、不读文件、不随机**", t_no_clock_no_network_no_io),
    ("v1 不 import 任何业务模块", t_does_not_import_business_modules),
    ("**截断 == as_of（无未来函数）**", t_as_of_equals_truncation),
    ("**基准的未来行情进不来**", t_benchmark_cannot_leak_future),
    ("**最近 5 根上的极值还不算 pivot**", t_recent_pivots_are_not_confirmed),
    ("**冻结参数与版本号绑定**", t_frozen_params_are_bound_to_version),
    ("**数据不够是 null，不是 0**", t_null_is_not_zero),
    ("**每个 null 都有原因**", t_every_null_has_a_reason),
    ("没有基准 ≠ 超额为 0", t_missing_benchmark_is_null_not_zero),
    ("没有下跌日时上下量比无定义", t_no_down_day_ratio_is_null_not_inf),
    ("一字板那天 CMF 被跳过，且报出来", t_cmf_skips_are_counted),
    ("**四次触顶 → 一个价区，触碰 4 次**", t_four_peaks_make_one_zone),
    ("连着几天打转只算一次触碰", t_clustered_touches_count_once),
    ("区间位置：单调涨在顶、单调跌在底", t_position_in_range_is_a_fact),
    ("放量 3 倍 → rvol ≈ 3（分母不含今天）", t_rvol_sees_a_volume_spike),
    ("**OBV 斜率的三个方向（正/负/没方向）**", t_obv_slope_direction),
    ("区间收缩看得出来，且认得 NR7", t_range_contraction_is_visible),
    ("**相对强度按日期对齐，不按行数**", t_relative_strength_aligns_by_date),
    ("超额收益只出数、不出词", t_outperformance_is_measured_not_judged),
    ("卡片 schema 稳定且可序列化", t_card_schema_is_stable),
    ("as_of 落在非交易日时退到上一个交易日", t_as_of_falls_back_to_last_trading_day),
    ("**setup 阈值冻结且写明来历**", t_setup_thresholds_are_frozen_and_explained),
    ("**一次事件 ≠ 一个 stock-day**", t_an_event_is_not_a_stock_day),
    ("算不出来 ≠ 不成立", t_unknown_is_not_the_same_as_false),
    ("**存过的一天不静默重写**", t_store_never_silently_rewrites_history),
    ("**人工复核台账（筛子的主 KPI）**", t_review_is_the_screen_kpi_and_it_is_recorded),
    ("**快照跑在收盘之后**", t_snapshot_runs_after_the_close),
    ("**PIT 按区间判，不是全局布尔**", t_universe_pit_is_judged_per_window),
    ("**事件带完整血统（含价区算法版本）**", t_event_carries_full_lineage_not_just_setup_version),
    ("**卡片保留自己当时的版本号**", t_stored_cards_keep_their_own_versions),
    ("**NaN 是第三种状态（不是 None，也不是数）**", t_nan_is_the_third_state),
    ("**面板体检只数不修**", t_panel_health_counts_but_does_not_repair),
    ("兜底 scrub 是穷举的（含嵌套）", t_scrub_is_exhaustive_including_nested),
    ("**阈值没变但版本变了**", t_setup_version_moved_even_though_thresholds_did_not),
    ("**v2：闸门决定有没有，家族分决定先看谁**", t_v2_gate_decides_whether_rank_decides_who),
    ("**加指标不改变这一族的权重**", t_adding_an_indicator_does_not_change_its_family_weight),
    ("方向正确，且波动族无方向", t_directions_and_the_one_judgement_call),
    ("缺的成员不补 0 也不补 0.5", t_missing_member_is_dropped_not_filled),
    ("**分档是标签，不是闸门**", t_bands_are_labels_not_a_gate),
    ("**家族等权且结构冻结**", t_score_params_are_frozen_and_equal_weighted),
    ("**NR7 不进分（单边证据不混进双边异常族）**", t_nr7_stays_out_of_the_score_and_says_why),
    ("**覆盖度和分数同框；不够就不报分**", t_coverage_travels_with_the_score),
    ("**5/5 族 ≠ 信息齐全（族覆盖度和项覆盖度都要印）**", t_coverage_shows_families_and_items),
    ("**全市场缺 ≠ 个别票缺（成对基准不对称）**", t_market_wide_null_is_not_a_per_name_gap),
    ("**基准取短了要报出来，不是吞掉**", t_short_benchmark_is_reported_not_swallowed),
    ("**基准一根 NaN 抹掉全市场大盘超额**", t_one_nan_in_the_benchmark_does_not_erase_the_whole_market),
    ("**常亮的灯 = 不亮的灯（警告要说现在，不说修之前）**",
     t_a_lamp_that_is_always_on_is_the_same_defect_as_one_that_never_lights),
    ("**改字段含义必须升 schema_version（我自己违反了）**",
     t_changing_what_a_field_means_must_move_the_schema_version),
    ("**复核台账不是一个交易日（目录污染）**", t_the_review_ledger_is_not_a_trading_day),
    ("**回测结果不能回流到定义层**", t_backtest_can_never_feed_back_into_the_definition),
    ("**对照组不匹配 setup 自己的成分**", t_controls_are_not_matched_on_the_setup_itself),
    ("回测报告先说它不能声称什么", t_backtest_says_what_it_cannot_claim),
]

print("=" * 72)
print("技术观察员自测 —— v1 只描述，不判断")
print("=" * 72)
for _n, _f in TESTS:
    check(_n, _f)

print("\n" + "=" * 72)
if BAD:
    print(f"{len(BAD)} 项失败 / 共 {len(TESTS)}")
    for n, e in BAD:
        print(f"  · {n}\n      {e}")
    raise SystemExit(1)
print(f"全部 {len(OK)} 项通过。")
raise SystemExit(0)
