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
    """**筛子的主 KPI 要有地方记。**

    "推出来的值不值得研究"今天就能测，但前提是有人把判断写下来。
    在复核台账出现之前，筛子好不好用只能靠印象——而印象会被最近一次的
    成败带着走。
    """
    import tempfile
    from pathlib import Path as _P
    from cio.technical import review as rv
    with tempfile.TemporaryDirectory() as tmp:
        old = rv.REVIEW_PATH
        try:
            rv.REVIEW_PATH = _P(tmp) / "reviews.jsonl"
            rv.mark("2026-09-01", "A", "worth", "财报后放量")
            rv.mark("2026-09-02", "B", "skip", "指数调仓")
            # **三档都要有**：逼人二选一会把犹豫记成假的确定
            rv.mark("2026-09-03", "C", "unclear")
            assert set(rv.VERDICTS) == {"worth", "skip", "unclear"}
            try:
                rv.mark("2026-09-04", "D", "会涨")
                raise AssertionError("非法判定被收下了")
            except ValueError:
                pass
            st = rv.stats()["deduped"]
            box = st[rv.SETUP_VERSION]
            assert box == {"worth": 1, "skip": 1, "unclear": 1}, box
            # 改主意：追加一条，两条都在，而且改过要看得见
            rv.mark("2026-09-02", "B", "worth", "回头看错了")
            assert rv.latest()[("2026-09-02", "B")]["verdict"] == "worth"
            assert rv.revisions() == [(("2026-09-02", "B"), "skip", "worth")]
            assert rv.stats()["deduped"][rv.SETUP_VERSION]["skip"] == 0, "去重后旧判定还在计数"
            assert rv.stats()["all_records"][rv.SETUP_VERSION]["skip"] == 1, "原始记录被抹了"
            # 待复核
            hits = [("2026-09-01", "A"), ("2026-09-05", "E")]
            assert rv.pending(hits) == [("2026-09-05", "E")]
        finally:
            rv.REVIEW_PATH = old


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
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "main")
    gate = work = None
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            nm = getattr(n.func, "attr", getattr(n.func, "id", ""))
            if nm == "is_snapshot_time" and gate is None:
                gate = n.lineno
            if nm in ("get_universe", "get_history") and work is None:
                work = n.lineno
    assert gate and work and gate < work, "收盘闸没跑在取数之前"


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
