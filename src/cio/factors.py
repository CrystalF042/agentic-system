"""证券二部 Research Factor Library —— 候选因子库（确定性、零 LLM）。

与 Production Factor Set 严格分开：
  · 本库 = 研究候选。任何因子在通过 Admission Gate 之前，永远只是候选。
  · unit_b._FACTORS = 生产集（UB-US-001 冻结的五因子，现状态 FAIL / 不驱动资金）。
    生产集不因本库新增而改变——避免"加了因子就顺手换模型"。

每个因子声明：fn(ctx, i) → float（方向已对齐：值越大越"好"）、家族、最少历史、说明。
ctx 提供 closes / vols / highs / lows，避免各因子重复切片。

中性化（sector-neutral）在打分层做：横截面 z 之后按 GICS 行业去均值。
这样可以区分"整个半导体在涨"与"MU 在半导体内部特别强"——后者才是选股信息。
"""
from __future__ import annotations

_EPS = 1e-12


class Ctx:
    """单只股票在某个 as-of 位置上的价量上下文（一次切片，多因子复用）。"""

    __slots__ = ("closes", "vols", "highs", "lows", "fund", "as_of")

    def __init__(self, closes, vols, highs=None, lows=None, fund=None, as_of=None):
        self.closes = closes
        self.vols = vols
        self.highs = highs if highs is not None else closes
        self.lows = lows if lows is not None else closes
        self.fund = fund or {}          # 该公司的紧凑 SEC 记录（PIT 取值用）
        self.as_of = as_of              # 当前横截面日期；基本面只看 filed <= as_of


# ---------------- 生产集五因子（定义与 unit_b 完全一致，供研究复用）----------------
def f_momentum(ctx, i):
    c = ctx.closes[:i + 1]
    if c[-250] <= 0 or c[-21] <= 0:
        return None
    return c[-21] / c[-250] - 1.0


def f_reversal(ctx, i):
    c = ctx.closes[:i + 1]
    if c[-21] <= 0:
        return None
    return -(c[-1] / c[-21] - 1.0)


def f_lowvol(ctx, i):
    import numpy as np
    c = ctx.closes[:i + 1]
    r = np.diff(np.log(np.maximum(c[-61:], _EPS)))
    return -float(np.std(r)) if len(r) else None


def f_trend(ctx, i):
    import numpy as np
    c = ctx.closes[:i + 1]
    ma = float(np.mean(c[-120:]))
    return c[-1] / ma - 1.0 if ma > 0 else None


def f_volume(ctx, i):
    import numpy as np
    v = ctx.vols[:i + 1]
    a20, a120 = float(np.mean(v[-20:])), float(np.mean(v[-120:]))
    return float(np.log((a20 + 1e-9) / (a120 + 1e-9)))


# ---------------- Build 2 新增候选（价量 / 相对）----------------
def f_mom_voladj(ctx, i):
    """波动调整动量：12-1 动量 / 近60日波动。同样的涨幅，波动更小者信号更强。
    假设：裸动量把'涨得多'和'波动大'混在一起，风险调整后才是纯趋势信息。"""
    import numpy as np
    m = f_momentum(ctx, i)
    if m is None:
        return None
    c = ctx.closes[:i + 1]
    r = np.diff(np.log(np.maximum(c[-61:], _EPS)))
    s = float(np.std(r))
    return m / s if s > _EPS else None


def f_high52(ctx, i):
    """52周高点邻近度：现价 / 近250日最高价。
    假设（George & Hwang 锚定效应）：越接近52周高，越容易继续创新高。"""
    import numpy as np
    c = ctx.closes[:i + 1]
    hi = float(np.max(c[-250:]))
    return c[-1] / hi if hi > 0 else None


def f_illiq(ctx, i):
    """Amihud 非流动性：近60日 mean(|日收益| / 成交额)。
    假设：流动性差的标的要求补偿，非流动性越高预期收益越高（故取正号）。"""
    import numpy as np
    c = ctx.closes[:i + 1]
    v = ctx.vols[:i + 1]
    r = np.abs(np.diff(np.log(np.maximum(c[-61:], _EPS))))
    dv = (c[-60:] * v[-60:])
    dv = np.maximum(dv[:len(r)], 1.0)
    x = float(np.mean(r / dv))
    return float(np.log1p(x * 1e9))          # 量纲压缩，避免极端值主导


def f_vol_regime(ctx, i):
    """波动率变化：近60日波动 / 近250日波动，取负。
    假设：波动率相对自身抬升是风险上行信号（波动率聚集），故短期相对放大者不利。"""
    import numpy as np
    c = ctx.closes[:i + 1]
    r = np.diff(np.log(np.maximum(c[-251:], _EPS)))
    if len(r) < 200:
        return None
    s_short = float(np.std(r[-60:]))
    s_long = float(np.std(r))
    return -(s_short / s_long) if s_long > _EPS else None


def f_downside(ctx, i):
    """下行波动（近60日只计负收益的标准差），取负。
    假设：投资者厌恶下行风险；分离上/下行波动后，下行部分才是被定价的那半。"""
    import numpy as np
    c = ctx.closes[:i + 1]
    r = np.diff(np.log(np.maximum(c[-61:], _EPS)))
    neg = r[r < 0]
    if len(neg) < 5:
        return None
    return -float(np.std(neg))


def f_skew(ctx, i):
    """近60日收益偏度，取负。
    假设（Bali et al. 彩票效应）：右偏（偶尔暴涨）的标的被追捧、预期收益反而低。"""
    import numpy as np
    c = ctx.closes[:i + 1]
    r = np.diff(np.log(np.maximum(c[-61:], _EPS)))
    if len(r) < 30:
        return None
    m, s = float(np.mean(r)), float(np.std(r))
    if s <= _EPS:
        return None
    return -float(np.mean(((r - m) / s) ** 3))


LIBRARY = {
    # 生产集（同时也是研究候选）
    "动量":     {"fn": f_momentum,   "family": "price", "min_hist": 250, "desc": "12-1 月动量（剔除最近1月）"},
    "反转":     {"fn": f_reversal,   "family": "price", "min_hist": 25,  "desc": "短期反转（近1月，取负）"},
    "低波":     {"fn": f_lowvol,     "family": "price", "min_hist": 65,  "desc": "低波动（近60日已实现波动，取负）"},
    "趋势":     {"fn": f_trend,      "family": "price", "min_hist": 125, "desc": "中期趋势（现价 / 120日均线）"},
    "量能":     {"fn": f_volume,     "family": "price", "min_hist": 125, "desc": "量能变化（20日均量 / 120日均量）"},
    # Build 2 新增
    "动量_波调": {"fn": f_mom_voladj, "family": "price", "min_hist": 250, "desc": "波动调整动量（动量 / 60日波动）"},
    "52周高":   {"fn": f_high52,     "family": "price", "min_hist": 250, "desc": "52周高点邻近度（现价 / 250日最高）"},
    "非流动性":  {"fn": f_illiq,      "family": "price", "min_hist": 65,  "desc": "Amihud 非流动性（|收益|/成交额，60日）"},
    "波动抬升":  {"fn": f_vol_regime, "family": "price", "min_hist": 251, "desc": "波动率相对抬升（60日波动/250日波动，取负）"},
    "下行波动":  {"fn": f_downside,   "family": "price", "min_hist": 65,  "desc": "下行波动（负收益标准差，取负）"},
    "偏度":     {"fn": f_skew,       "family": "price", "min_hist": 65,  "desc": "收益偏度（60日，取负；彩票效应）"},
}

# 并入 SEC 基本面因子（family="fundamental"；需要 ctx.fund 与 ctx.as_of）
try:
    from .fundamentals import FUNDAMENTAL_FACTORS as _FF
    for _k, _v in _FF.items():
        LIBRARY[_k] = {"fn": _v["fn"], "family": "fundamental", "min_hist": 0, "desc": _v["desc"]}
except Exception as _e:      # 基本面模块不可用时，价量因子仍可独立工作
    pass

# ---------------- 派生因子（由【横截面 z】合成，不是逐股函数）----------------
# UB-US-008 Quality Composite：三个经济维度等权，避免"四个原始指标直接平均"
# 隐含变成 Profitability 50% / FCF 25% / Leverage 25%。
# 方向统一：Quality 越高 = 利润率越高、现金流越强、杠杆越低。
# （杠杆因子在库内已定义为 -(负债/资产)，故此处直接相加即为"低杠杆为好"。）
def _q_quality(z: dict) -> float:
    profitability = (z["毛利margin"] + z["营业利润率"]) / 2.0
    cash_generation = z["自由现金流"]
    balance_sheet = z["杠杆"]
    return (profitability + cash_generation + balance_sheet) / 3.0


DERIVED = {
    "质量": {
        "requires": ["毛利margin", "营业利润率", "自由现金流", "杠杆"],
        "combine": _q_quality,
        "desc": "Quality Composite：(利润率维度 + 现金生成 + 资产负债表)/3，三维度等权",
    },
}


def is_derived(name: str) -> bool:
    return name in DERIVED


def expand(names) -> list:
    """把派生因子展开成它依赖的基础因子（用于逐股取值）。"""
    out = []
    for n in names:
        if n in DERIVED:
            for c in DERIVED[n]["requires"]:
                if c not in out:
                    out.append(c)
        elif n not in out:
            out.append(n)
    return out


PRICE_FACTORS = [k for k, v in LIBRARY.items() if v["family"] == "price"]
FUNDAMENTAL_NAMES = [k for k, v in LIBRARY.items() if v["family"] == "fundamental"]


def needs_fundamentals(names) -> bool:
    return any(LIBRARY.get(n, {}).get("family") == "fundamental" for n in names)


def min_history(names) -> int:
    return max([LIBRARY[n]["min_hist"] for n in names if n in LIBRARY] or [250])


def factor_row(names, ctx: "Ctx", i: int) -> "dict | None":
    """全有或全无：任一因子无效即整行作废。用于【合成分】——合成必须在同一批标的上进行。"""
    out = factor_row_partial(names, ctx, i)
    return out if out is not None and len(out) == len(names) else None


def factor_row_partial(names, ctx: "Ctx", i: int) -> "dict | None":
    """逐因子可缺失：能算出几个就返回几个。

    为什么必须这样：基本面字段各公司披露口径不一（并非所有公司都报 GrossProfit），
    若沿用"全有或全无"，7 个基本面因子取交集会让横截面从 500 只塌到十几只——
    样本一小，IC 噪声就压过信号，还会引入"只有成熟大公司才凑得齐"的选择偏差。
    正确做法是【每个因子在自己可用的标的集合上】各自算 IC。
    """
    import math as _m
    out = {}
    for n in names:
        spec = LIBRARY.get(n)
        if not spec:
            continue
        try:
            v = spec["fn"](ctx, i)
        except Exception:
            continue
        if v is None or (isinstance(v, float) and (_m.isnan(v) or _m.isinf(v))):
            continue
        out[n] = float(v)
    return out or None


def sector_neutralize(zvals: list, sectors: list) -> list:
    """行业中性化：在每个 GICS 行业内部对 z 分去均值。
    作用是把'整个行业在涨'剥掉，只留'该股在行业内部的相对强弱'——
    否则二部会悄悄变成一个行业轮动引擎，而不是选股模型。"""
    import numpy as np
    a = np.array(zvals, float)
    if len(a) != len(sectors):
        return list(a)
    out = a.copy()
    groups: dict = {}
    for idx, s in enumerate(sectors):
        groups.setdefault(s or "_NA", []).append(idx)
    for _s, idxs in groups.items():
        if len(idxs) >= 3:                    # 行业内太少则不中性化（去均值无意义）
            m = float(np.mean(a[idxs]))
            out[idxs] = a[idxs] - m
    return list(out)
