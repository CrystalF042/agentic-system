"""账本用价 —— **未复权**（raw / unadjusted）。

## 为什么账本不能用复权价

系统里别处取行情走 `quant_data.get_history`，它是 `auto_adjust=True`——
**对测量是对的**：波动率、Beta、回撤都要在复权收益序列上算，
否则一次拆股会被读成 −75% 的暴跌。

**但对账本是错的**，而且错得很安静：复权价会**回溯改变**。
下个月发一次股息，今天这根 K 线的复权价会随之下调。
于是：

    今天    按复权开盘价 182.40 记一笔买入
    下月    同一天的复权开盘价变成 181.95
    结果    账本里的成本价和数据源对不上了 —— 而两边都没有错

再叠加一次拆股就彻底散架：账本说 100 股 @ 800，数据源说 200。
持仓瞬间显示 −75%，**没有任何一处报错**。

所以账本这一侧统一用未复权价：成交价、盯市价、成本价同一口径。
拆股与分红改为 Build 3 里**显式的公司行为事件**——
拆股记一笔股数调整，分红记一笔现金入账，两者都在账本上看得见。

## next_session_after：下一个交易日是**查出来**的，不是算出来的

"T+1" 不等于"明天"。周五的下一个交易日是周一，感恩节前一天的下一个
交易日要跳过假日。用日历规则算需要一份美股节假日表，而那份表一旦过期
就会安静地算错一天。

这里改成：**去数据里找第一根晚于 T 的 K 线**。数据没有那根，
就说明还没到（或取数出了问题），返回 None 让上层显式处理——
而不是给出一个凭空生成的日期。
"""
from __future__ import annotations

import os
from datetime import datetime

from .utils import get_logger

log = get_logger("cio.marks")

PRICE_BASIS = "RAW_UNADJUSTED"
"""账本一侧的价格口径。与 `quant_data`（复权，用于测量）**刻意不同**。"""


def _mock_bar(ticker: str, day: str) -> dict:
    """离线自测用的确定性价格。CIO_QUANT_MOCK=1 时启用。

    **确定性**很重要：同一个 ticker 永远得到同一个价格，
    所以测试断言的是逻辑，不是运气。
    """
    h = sum(ord(c) * (i + 7) for i, c in enumerate(ticker.upper()))
    base = 20.0 + (h % 380)
    return {"date": day, "open": round(base * 1.002, 2), "close": round(base, 2),
            "high": round(base * 1.01, 2), "low": round(base * 0.99, 2)}


def _raw_hist(ticker: str, days: int = 30):
    """未复权日线。取不到返回 None（**不造价**）。"""
    try:
        import pandas as pd
        import yfinance as yf
    except Exception as e:                                   # noqa: BLE001
        log.warning("yfinance/pandas 不可用：%s", e)
        return None
    try:
        period = f"{max(int(days) + 10, 15)}d"
        h = yf.Ticker(ticker).history(period=period, auto_adjust=False)
        if h is None or not len(h):
            return None
        h = h.reset_index().rename(columns={"Date": "date", "Open": "open",
                                            "High": "high", "Low": "low",
                                            "Close": "close"})
        h["date"] = pd.to_datetime(h["date"]).dt.tz_localize(None)
        return h[["date", "open", "high", "low", "close"]].sort_values("date")\
                .reset_index(drop=True)
    except Exception as e:                                   # noqa: BLE001
        log.warning("%s 未复权行情取不到：%s", ticker, e)
        return None


def close_prices(tickers, days: int = 30) -> dict:
    """{ticker: {"price", "date", "basis"}}。

    **取不到的一律留一行，写明取不到**，不静默省略。
    省略的后果是上层看到"这只票不在字典里"，很容易被 `.get(t, 0)` 折成 0。
    """
    out = {}
    ts = [str(t).upper() for t in (tickers or [])]
    if not ts:
        return out
    if os.environ.get("CIO_QUANT_MOCK") == "1":
        from .config import market_date
        day = market_date()
        for t in ts:
            b = _mock_bar(t, day)
            out[t] = {"price": b["close"], "date": day, "basis": PRICE_BASIS,
                      "ok": True, "note": "CIO_QUANT_MOCK=1 合成价"}
        return out
    for t in ts:
        df = _raw_hist(t, days)
        if df is None or not len(df):
            out[t] = {"price": None, "date": "", "basis": PRICE_BASIS, "ok": False,
                      "note": "取不到未复权行情 —— 目标股数不可计算（**不按 0 处理**）"}
            continue
        last = df.iloc[-1]
        out[t] = {"price": float(last["close"]),
                  "date": last["date"].strftime("%Y-%m-%d"),
                  "basis": PRICE_BASIS, "ok": True, "note": ""}
    return out


def price_map(tickers, days: int = 30) -> dict:
    """只要 {ticker: price} 的简版。**取不到的键不存在**，
    这样 `plan()` 里会走 NOT_PRICED 分支，而不是拿到一个 None 当数用。"""
    return {t: d["price"] for t, d in close_prices(tickers, days).items()
            if d.get("price") is not None}


def next_session_after(ticker: str, day: str, days: int = 30) -> str:
    """**从数据里查**第一根晚于 `day` 的 K 线日期。查不到返回空串。

    Build 2 用它把 "T+1_OPEN" 落成一个真实存在的交易日，
    并据此判断批准是否已经过期。
    """
    if os.environ.get("CIO_QUANT_MOCK") == "1":
        return ""            # 离线时没有"下一个交易日"这个事实，**不编一个**
    df = _raw_hist(str(ticker).upper(), days)
    if df is None or not len(df):
        return ""
    d0 = str(day)[:10]
    for _, r in df.iterrows():
        d = r["date"].strftime("%Y-%m-%d")
        if d > d0:
            return d
    return ""


def render_note(prices: dict) -> str:
    ok = [t for t, d in prices.items() if d.get("ok")]
    bad = [t for t, d in prices.items() if not d.get("ok")]
    dates = sorted({d["date"] for d in prices.values() if d.get("date")})
    s = (f"决策价：{len(ok)}/{len(prices)} 取到，口径 {PRICE_BASIS}（未复权，"
         f"与成交价、盯市价同口径；测量另走复权序列）")
    if dates:
        s += f"　价格所属交易日 {'、'.join(dates)}"
    if bad:
        s += f"　⚠ 取不到：{'、'.join(bad)} —— 这几只本轮不可定量，不按 0 处理"
    return s
