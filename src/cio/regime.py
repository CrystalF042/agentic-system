"""市场 regime —— risk-on / neutral / risk-off（确定性，零 LLM）。

**架构冻结 v1.0：regime 是 CRO 自己的 market-level 输入，不进二部的股票横截面表。**

理由是对象错位：二部测量的对象是**股票**，regime 的对象是**市场**。
把 RSP/SPY 塞进一张每行一只股票的表里，那一列对每一行都是同一个值——
它不是那只股票的属性。

三个信号，全部可从免费行情算出，每一个都把原始比值印出来：

    RSP / SPY      等权 vs 市值加权 = **市场宽度**。比值走低 = 涨势集中在少数大票，
                   宽度恶化。这是最有信息量的一个，因为它不看点位、只看结构。
    GLD / SPY      黄金 vs 股票 = **风险偏好**。比值走高 = 资金在避险。
    SPY vs 200 日均线   趋势状态。最粗但也最不容易被单日噪声推翻。

**为什么不用点位或涨跌幅**：那是水平量，需要一个"正常值"才有意义，
而"正常值"就是个自由参数。比值的**变化方向**不需要基准。

判定是投票制而不是打分：三个信号各投一票，多数决。
打分要设权重，权重就是拟合空间；投票只需要每个信号自己方向明确。
"""
from __future__ import annotations

from .utils import get_logger

log = get_logger("cio.regime")

RISK_ON, NEUTRAL, RISK_OFF = "risk_on", "neutral", "risk_off"

# 比值的回看窗口。**写进字段名的纪律同样适用**：报告里印的是 rsp_spy_60d，不是 rsp_spy。
LOOKBACK = 60
MA_DAYS = 200
# 比值变化超过这个幅度才算一票，避免把噪声读成信号。这是风险政策，不是拟合参数。
_BAND = 0.02


def _series_ratio(a, b, n: int):
    """a/b 最近 n 日的变化率。任一序列不足则返回 None——不外推、不填补。"""
    try:
        import pandas as pd
        if a is None or b is None or len(a) < n + 1 or len(b) < n + 1:
            return None
        ca = pd.to_numeric(a["close"], errors="coerce").dropna()
        cb = pd.to_numeric(b["close"], errors="coerce").dropna()
        m = min(len(ca), len(cb))
        if m < n + 1:
            return None
        ra = ca.values[-m:]
        rb = cb.values[-m:]
        now = ra[-1] / rb[-1]
        then = ra[-(n + 1)] / rb[-(n + 1)]
        if not then:
            return None
        return float(now / then - 1.0)
    except Exception:
        return None


def _above_ma(df, n: int):
    try:
        import pandas as pd
        c = pd.to_numeric(df["close"], errors="coerce").dropna()
        if len(c) < n:
            return None
        return float(c.values[-1] / c.values[-n:].mean() - 1.0)
    except Exception:
        return None


def assess(fetch=None) -> dict:
    """算出 regime。fetch(ticker, days) -> DataFrame|None，缺省用 quant_data。

    **任何一个信号取不到就少一票，不猜、不用剩下的补。** 三票全缺时返回
    neutral 并写明"未评估"——neutral 是保守的默认（风险预算 ×0.8），
    而 risk_on 会放大仓位，绝不能作为数据缺失时的兜底。
    """
    if fetch is None:
        def fetch(t, days):
            try:
                from . import quant_data
                return quant_data._yf_hist(t, days)
            except Exception:
                return None

    days = max(LOOKBACK, MA_DAYS) + 40
    spy, rsp, gld = fetch("SPY", days), fetch("RSP", days), fetch("GLD", days)

    breadth = _series_ratio(rsp, spy, LOOKBACK)      # 宽度：>0 好
    haven = _series_ratio(gld, spy, LOOKBACK)        # 避险：>0 差
    trend = _above_ma(spy, MA_DAYS)                  # 趋势：>0 好

    votes, detail = [], []
    for name, val, good_when_positive in (
            (f"rsp_spy_{LOOKBACK}d", breadth, True),
            (f"gld_spy_{LOOKBACK}d", haven, False),
            (f"spy_vs_ma{MA_DAYS}", trend, True)):
        if val is None:
            detail.append({"signal": name, "value": None, "vote": None,
                           "note": "取不到——本信号不投票"})
            continue
        pos = val > _BAND
        neg = val < -_BAND
        if not pos and not neg:
            v = 0
        else:
            up = pos if good_when_positive else neg
            v = 1 if up else -1
        votes.append(v)
        detail.append({"signal": name, "value": round(val, 4), "vote": v, "note": ""})

    if not votes:
        out = {"regime": NEUTRAL, "score": None, "signals": detail,
               "note": "三个信号全部取不到——按 neutral 处理（保守默认；"
                       "risk_on 会放大仓位，绝不能作为缺数据的兜底）"}
        log.warning("市场 regime：%s", out["note"])
        return out

    score = sum(votes)
    regime = RISK_ON if score >= 2 else RISK_OFF if score <= -2 else NEUTRAL
    out = {"regime": regime, "score": score, "signals": detail,
           "note": f"{len(votes)} 个信号投票，净票 {score:+d}"
                   + ("（有信号缺失）" if len(votes) < 3 else "")}
    log.info("市场 regime = %s（%s）", regime, out["note"])
    return out


def render(r: dict) -> str:
    """人读的一段。**把原始比值印出来**——不然 regime 就是个不可复核的标签。"""
    L = [f"市场 regime = **{r.get('regime', '?')}**（{r.get('note', '')}）"]
    for s in r.get("signals") or []:
        v = s.get("value")
        L.append(f"  · {s['signal']}: " + ("无数据" if v is None else f"{v:+.2%}")
                 + (f"　票 {s['vote']:+d}" if s.get("vote") is not None else "")
                 + (f"　{s['note']}" if s.get("note") else ""))
    return "\n".join(L)
