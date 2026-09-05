"""CRO 的测量输入 —— **换算成小数口径后交出。只有这一份实现。**

原来这段住在 `run_pc.py` 里（私有函数）。build122 让自动流水线也要跑
CRO，于是它必须被两个入口共用。**在流水线里再抄一遍是不行的**：

    手动那份被 test_pc 覆盖着，自动那份没有
    → 改了其中一份，另一份继续用旧口径，而两边都不报错

这正是 build121 刚被咬过的形状（那句"没有新的基本面事实"判了两处）。

## 两条纪律，都是被真实缺陷教出来的

**一、单位在边界上显式换算。** `measures.*` 返回百分数（40.74 = 40.74%），
CRO 的 POLICY 阈值是小数（`veto_vol = 1.50` = 150%）。直接比会得到
"40.74 > 1.50 → 否决"，理由行印得一字不差，**结论却是反的**。
换算写在这里，接收端 `risk_officer.check_units` 再校一次量级。
Beta 与相关系数本来就是无量纲，**不换算**。

**二、每一项测量各自 try。** 原来整段共用一个 try：`beta_corr` 抛错时
前面已经算出的 σ 留下、后面的全成 None，日志只说一句"测量取不到"，
报告上看不出哪一项失败。**一次异常污染整组测量，这就是静默失败。**

取不到的一律 `None`，**绝不填 0**——`beta=0` 读起来是"完全不随市场波动"，
真实含义却是"我们不知道"，这两件事在风险判断里的后果相反。
"""
from __future__ import annotations

from .utils import get_logger

log = get_logger("cio.cro_inputs")

FIELDS = ("sigma_60", "sigma_252", "beta", "maxdd", "corr_bench",
          "liquidity_cap", "beta_n_aligned")


def blank() -> dict:
    """全项未评估。**是 None，不是 0。**"""
    return {k: None for k in FIELDS}


def measures_for(symbol: str) -> dict:
    """二部口径的确定性测量，换算成 CRO 的小数口径。**零 LLM。**"""
    from . import measures, quant_data
    out = blank()
    try:
        st = quant_data.Stock(code=symbol, name=symbol, yahoo=symbol)
        df = quant_data.get_history([st], days=400).get(symbol)
    except Exception as e:                                     # noqa: BLE001
        log.warning("%s 取不到行情，全部测量标为未评估（不填 0）：%s", symbol, e)
        return out
    if df is None or not len(df):
        log.warning("%s 行情为空，全部测量标为未评估（不填 0）", symbol)
        return out
    closes = df["close"].tolist()
    # 基准单独取。**它只被 beta/corr 用到**——放进上面那个 try 的话，
    # 基准挂掉会连带把 σ 和回撤一起清成 None，而这两项本来算得出来。
    try:
        bench = quant_data.get_benchmark(days=400)
    except Exception as e:                                     # noqa: BLE001
        log.warning("%s 的基准取不到，仅 beta/corr 标为未评估：%s", symbol, e)
        bench = None

    def _one(field, fn, ratio=True):
        try:
            v = fn()
        except Exception as e:                                 # noqa: BLE001
            log.warning("%s 的 %s 算不出，仅该项标为未评估：%s", symbol, field, e)
            return
        out[field] = measures.as_ratio(v) if ratio else v

    _one("sigma_60", lambda: measures.ann_vol(closes, 60))
    _one("sigma_252", lambda: measures.ann_vol(closes, 252))
    _one("maxdd", lambda: measures.max_drawdown(closes, 250))
    try:
        b, c, n_aligned = measures.beta_corr(df, bench, 250, 60)   # 三元组
        out["beta"], out["corr_bench"], out["beta_n_aligned"] = b, c, n_aligned
        if b is None:
            log.info("%s 的 Beta 未评估：与基准对齐 %s 个交易日，不足 250×0.8",
                     symbol, n_aligned)
    except Exception as e:                                     # noqa: BLE001
        log.warning("%s 的 beta/corr 算不出，仅该两项标为未评估：%s", symbol, e)
    return out
