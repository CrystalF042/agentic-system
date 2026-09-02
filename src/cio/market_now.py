"""盘前市场快照 —— 期货与宏观，**每个数字带自己的 as-of 时间戳**（零 LLM）。

## 为什么需要这个模块

07:00 ET 那一屏里，不同数字的新鲜度差了十几个小时：

    美股收盘价       昨天 16:00 ET —— 隔了 15 小时，那是昨天的结论
    股指期货         几乎全天交易 —— **此刻**，这才是"盘前"
    亚洲市场         今天已经收盘 —— 今天的完整信息
    欧洲市场         正在盘中 —— 今天的，但未定局

**而它们在报告里长得一模一样。** 读的人会默认同屏的数字同样新鲜，
这个误解不会有任何提示——既不报错，也没有一处显示异常。

## 一条核心纪律：新鲜度是**测**出来的，不是按品种**假设**的

最省事的写法是给每个符号打死一个标签："ES=F 是期货，所以标实时"。
但那样一来，**当行情源挂掉、返回的是三天前的陈数据时，它照样显示"实时"**——
标签来自类别，而不是来自数据本身。

所以这里只做一件事：拿到最后一根 K 线的时间戳，和当前市场时间相减，
**按实测年龄归档**。数据陈了，标签自己就会变，不需要任何人发现。

    ≤ 60 分钟      实时
    ≤ 6 小时       {n} 小时前
    同一日历日      今日 HH:MM
    其余           {n} 天前 —— 并标 ⚠

## 与 collect.fetch_index_quotes 的分工

那个函数刻意**剔除盘中 K 线**（`_pick_completed`），取"上一完整收盘"——
对指数锚定是对的，防的是"日经开盘半小时的实时价被当成收盘"。
**对期货正好相反**：07:00 ET 时我们要的就是那根盘中 K 线。
两者口径相反，所以是两个函数，不是给旧函数加参数。
"""
from __future__ import annotations

from .utils import get_logger

log = get_logger("cio.market_now")

# ---------------------------------------------------------------- 符号表
# 全部来自免费源（yfinance），不破"零付费"红线。
# 顺序即报告里的呈现顺序：先看盘前在定价什么，再看已经发生了什么。
#
# **符号是否可取必须在真机上先验**（scripts/check_market_now.py），
# 不要假设它一定返回数据——一个静默失效的符号会表现成"这项没有异动"。
SYMBOLS = [
    ("股指期货", "标普500期货", "ES=F"),
    ("股指期货", "纳指100期货", "NQ=F"),
    ("股指期货", "道指期货", "YM=F"),
    ("股指期货", "罗素2000期货", "RTY=F"),
    ("宏观", "VIX 波动率指数", "^VIX"),
    ("宏观", "美债10年收益率", "^TNX"),
    ("宏观", "美元指数 DXY", "DX-Y.NYB"),
    ("海外市场", "日经225", "^N225"),
    ("海外市场", "恒生指数", "^HSI"),
    ("海外市场", "欧洲斯托克50", "^STOXX50E"),
]

FRESH_LIVE = "实时"
_LIVE_MINUTES = 60
_HOURS_CUTOFF = 6


def classify_age(as_of, now) -> tuple:
    """按**实测年龄**归档。返回 (标签, 分钟数, 是否需要标⚠)。

    纯函数、不联网、不看品种——所以它可以被完整测试，
    也因此不会出现"数据陈了但标签还写着实时"这种情况。
    """
    if as_of is None or now is None:
        return ("时间未知", None, True)
    mins = (now - as_of).total_seconds() / 60.0
    if mins < -5:                      # 时间戳在未来 = 时区搞错了，必须看得见
        return ("时间戳异常（晚于当前）", mins, True)
    mins = max(mins, 0.0)
    if mins <= _LIVE_MINUTES:
        return (FRESH_LIVE, mins, False)
    if mins <= _HOURS_CUTOFF * 60:
        return (f"{mins / 60:.1f} 小时前", mins, False)
    if as_of.date() == now.date():
        return (f"今日 {as_of.strftime('%H:%M')}", mins, False)
    days = (now.date() - as_of.date()).days
    if days == 1:
        return (f"昨日收盘 {as_of.strftime('%H:%M')}", mins, False)
    return (f"{days} 天前", mins, True)      # 超过一天 = 大概率取数出了问题


def _yf_tick(symbol: str, tz):
    """取单个符号的最新价、前收、以及**最后一根 K 线的真实时间戳**。

    先试 5 分钟线（期货/指数盘中都有），拿不到再退日线。
    前收一律从日线取——盘中线的"前一根"是五分钟前，不是上一交易日。
    """
    import yfinance as yf
    t = yf.Ticker(symbol)
    last = prev = as_of = None

    d = t.history(period="7d")
    if d is not None and len(d) >= 1:
        closes = [float(x) for x in d["Close"].tolist()]
        if len(closes) >= 2:
            prev = closes[-2]
        last = closes[-1]
        ts = d.index[-1]
        as_of = ts.tz_convert(tz) if getattr(ts, "tzinfo", None) else ts

    try:
        i = t.history(period="2d", interval="5m")
        if i is not None and len(i):
            last = float(i["Close"].iloc[-1])
            ts = i.index[-1]
            as_of = ts.tz_convert(tz) if getattr(ts, "tzinfo", None) else ts
            # 盘中线存在时，前收要用"最后一根【完整】日线"——
            # 日线的最后一根此刻是今天的进行中 K 线，拿它当前收会算出 0%。
            if d is not None and len(d) >= 2:
                same_day = getattr(d.index[-1], "date", lambda: None)()
                prev = float(d["Close"].iloc[-2]) if same_day == as_of.date() \
                    else float(d["Close"].iloc[-1])
    except Exception as e:                       # noqa: BLE001
        log.info("%s 无 5 分钟线，退用日线：%s", symbol, type(e).__name__)

    if last is None:
        return None
    chg = None
    if prev:
        chg = (last - prev) / prev * 100.0
        if abs(chg) > 25:                        # 主要指数/期货单日不可能这么动
            log.warning("%s 涨跌幅 %.1f%% 超出合理区间，判为脏数据，只报价不报涨跌", symbol, chg)
            chg = None
    return {"last": last, "change_pct": chg, "as_of": as_of}


def snapshot(fetch=None, now=None, symbols=None) -> list:
    """取全部符号。**取不到的一律保留一行并写明原因，不静默省略。**

    省略一行的后果是：报告上看不出这项缺失，读者以为"今天没什么可说的"，
    而实际是数据源挂了——这两件事在页面上必须能分辨。
    """
    from .config import market_now
    from .models import MarketTick

    now = now or market_now()
    tz = getattr(now, "tzinfo", None)
    rows = symbols if symbols is not None else SYMBOLS

    if fetch is None:
        def fetch(sym):
            return _yf_tick(sym, tz)

    out = []
    for group, name, sym in rows:
        try:
            d = fetch(sym)
        except Exception as e:                   # noqa: BLE001
            log.warning("%s 取不到：%s", sym, e)
            out.append(MarketTick(group=group, name=name, symbol=sym,
                                  note=f"取数失败（{type(e).__name__}）", stale=True))
            continue
        if not d or d.get("last") is None:
            out.append(MarketTick(group=group, name=name, symbol=sym,
                                  note="今日未取到", stale=True))
            continue
        label, mins, stale = classify_age(d.get("as_of"), now)
        out.append(MarketTick(
            group=group, name=name, symbol=sym,
            last=round(float(d["last"]), 2),
            change_pct=(None if d.get("change_pct") is None
                        else round(float(d["change_pct"]), 2)),
            as_of=(d["as_of"].strftime("%m-%d %H:%M") if d.get("as_of") else ""),
            age_label=label,
            age_minutes=(None if mins is None else round(mins, 1)),
            stale=stale))
    n_ok = sum(1 for t in out if t.last is not None)
    n_live = sum(1 for t in out if t.age_label == FRESH_LIVE)
    log.info("市场快照：%d/%d 取到，其中实时 %d 项", n_ok, len(out), n_live)
    return out


def render_note(ticks: list, now=None) -> str:
    """报告里那一行时间基准说明。**先写现在几点，读者才有参照系。**"""
    from .config import market_now
    now = now or market_now()
    n_stale = sum(1 for t in ticks if t.stale)
    # **不要用 markdown 强调符。** 这段文字会同时进 md、reportlab PDF 和
    # HTML→PDF 三处，而后两者不解析 markdown——`**` 会原样印在报告上
    # （真机第一份 PDF 上就是"标注了 ** 该数字自己的时间 **——"）。
    # 给三个渲染器共用的文本，只能用纯文本。
    s = (f"报告生成时间 {now.strftime('%Y-%m-%d %H:%M')} 美东。"
         f"下表每一项都标注了该数字自己的时间——"
         f"标「{FRESH_LIVE}」的是此刻仍在交易的，其余是已经发生过的。")
    if n_stale:
        s += f" ⚠ 其中 {n_stale} 项取数异常或明显过期，已单独标出。"
    return s
