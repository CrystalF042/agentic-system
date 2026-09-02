"""轻量回测（证券一部用）。只用 yfinance + pandas，不引入 backtrader/qlib（那是二部的重装备）。
纪律：① 只报真值、不吹 alpha；② 严防未来函数（信号一律滞后一日）；③ 取不到数就如实标注。"""
from __future__ import annotations

import time
import traceback

from .utils import get_logger

log = get_logger("cio.backtest")

_FETCH_ATTEMPTS = 3          # Yahoo 限流时的取数重试次数（含首次）
_FETCH_BACKOFF = 1.5         # 退避基数：1.5s → 3.0s


def quant_support(symbol: str) -> tuple[list[str], dict]:
    """对给定标的用近一年真实行情算：买入持有(年化/波动/Sharpe/最大回撤) + 20/60 均线策略回测。
    返回 (可读事实列表, 指标dict)。仅作量化背景，非选股 alpha 主张。

    失败时不静默：日志打出异常消息 + 出错阶段 + 完整栈，返回值也带上阶段名，
    便于从 Telegram/PDF 直接看出是取数挂了还是算的时候挂了。"""
    facts: list[str] = []
    try:
        import pandas as pd
        import yfinance as yf
    except Exception:
        return ["回测依赖缺失（yfinance/pandas）"], {}

    stage = "启动"
    try:
        # ---------- 取数（带退避重试）+ 行情源降级防御 ----------
        # Yahoo 限流时会返回空响应，而 yfinance 内部 history.py 直接对 None 下标
        # （data['chart']），抛 TypeError: 'NoneType' object is not subscriptable。
        # 这是 yfinance 的缺陷，不是本地逻辑问题；连续调用（日频多只 + 建仓取价）极易触发。
        # 对策：退避重试，把"间歇性硬失败"降级成"偶尔慢几秒"。
        stage = "取数"
        hist, last_err = None, None
        for i in range(_FETCH_ATTEMPTS):
            try:
                hist = yf.Ticker(symbol).history(period="1y")
                if hist is not None and not getattr(hist, "empty", True):
                    last_err = None
                    break
                last_err = "行情源返回空表"
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
                hist = None
            if i < _FETCH_ATTEMPTS - 1:
                wait = _FETCH_BACKOFF * (2 ** i)
                log.info("回测取数失败(%s) %s —— %.1fs 后第 %d/%d 次重试",
                         last_err, symbol, wait, i + 2, _FETCH_ATTEMPTS)
                time.sleep(wait)
        if hist is None or getattr(hist, "empty", True):
            log.warning("回测取数 %s 连续 %d 次失败：%s（多为 Yahoo 限流，稍后自愈）",
                        symbol, _FETCH_ATTEMPTS, last_err)
            return [f"{symbol} 行情源连续 {_FETCH_ATTEMPTS} 次未返回数据"
                    f"（Yahoo 限流或代码无效），本轮无量化支撑"], {}
        if isinstance(hist.columns, pd.MultiIndex):
            # yfinance 1.x 某些路径返回 Price×Ticker 两层列；不压平的话 hist["Close"]
            # 拿到的是 DataFrame 而非 Series，后面 f-string 格式化会抛 TypeError。
            hist = hist.copy()
            hist.columns = hist.columns.droplevel(-1)
        if "Close" not in hist.columns:
            return [f"{symbol} 行情表缺 Close 列（实际列：{list(hist.columns)[:6]}），本轮无回测"], {}

        stage = "清洗"
        close = hist["Close"]
        if isinstance(close, pd.DataFrame):                      # 同名多列 → 取第一列
            close = close.iloc[:, 0]
        close = pd.to_numeric(close, errors="coerce").dropna()   # 治 object dtype（源降级时会混入非数值）
        if len(close) < 60:
            return [f"{symbol} 行情数据不足（{len(close)} 日 < 60），无法回测"], {}
        ret = close.pct_change().dropna()

        # ---------- 1) 买入持有基准 ----------
        stage = "买入持有"
        ann_ret = float((close.iloc[-1] / close.iloc[0]) ** (252.0 / len(close)) - 1)
        ann_vol = float(ret.std()) * (252 ** 0.5)
        sharpe = ann_ret / ann_vol if ann_vol else 0.0
        mdd = float((close / close.cummax() - 1).min())
        facts.append(f"买入持有(近一年)：年化 {ann_ret * 100:+.1f}%，年化波动 {ann_vol * 100:.1f}%，"
                     f"Sharpe {sharpe:.2f}，最大回撤 {mdd * 100:.1f}%")

        # ---------- 2) 20/60 均线策略（信号滞后一日，防未来函数）----------
        stage = "均线策略"
        ma_f = close.rolling(20).mean()
        ma_s = close.rolling(60).mean()
        pos = (ma_f > ma_s).astype(int).shift(1).fillna(0)       # 用"昨日"信号定"今日"仓位
        sret = (pos * ret).dropna()
        s_ann = float(sret.mean()) * 252
        s_vol = float(sret.std()) * (252 ** 0.5)
        s_sharpe = s_ann / s_vol if s_vol else 0.0
        scum = (1 + sret).cumprod()
        s_mdd = float((scum / scum.cummax() - 1).min()) if len(scum) else 0.0
        facts.append(f"20/60 均线策略(信号滞后1日·防未来函数)：年化 {s_ann * 100:+.1f}%，"
                     f"Sharpe {s_sharpe:.2f}，最大回撤 {s_mdd * 100:.1f}%")

        # ---------- 3) 当前均线状态（客观信号，非建议）----------
        stage = "均线状态"
        f_last, s_last = float(ma_f.iloc[-1]), float(ma_s.iloc[-1])
        state = "多头排列(20日在60日上方)" if f_last > s_last else "空头排列(20日在60日下方)"
        facts.append(f"当前均线：{state}（20日 {f_last:.2f} / 60日 {s_last:.2f}）")

        return facts, {"sharpe": round(sharpe, 2), "ann_ret": round(ann_ret, 3),
                       "mdd": round(mdd, 3), "ma_state": state}
    except Exception as e:
        log.warning("回测失败(%s) %s @%s阶段：%s", type(e).__name__, symbol, stage, e)
        log.warning("回测异常栈 %s：\n%s", symbol, traceback.format_exc())
        return [f"{symbol} 回测失败({type(e).__name__} @{stage})——详见日志，本轮该标的无量化支撑"], {}
