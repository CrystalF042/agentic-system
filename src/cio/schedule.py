"""发车时间 —— **按市场时区判断"现在是不是盘前"，不按机器时区。**

## 这个文件是为了修一个真实故障

2026-09-02，盘前简报在**纽约时间 09-01 晚上 19:49** 送达。
简报本身是对的：英文抬头、ET 时间戳、美股期货与指数、当天真实新闻。
错的只有一件事——**它在收盘之后四个小时才发出来**。

原因是 README 里那行：

    0 7 * * 1-5   cd ... && .venv/bin/python run_premarket.py

那句话写的是"机器本地时间 7 点"，而这台 Mac 在北京时区。
**北京 07:00 = 纽约前一天 19:00。** 分毫不差。

这是一个典型的静默失败：cron 成功、采集成功、PDF 成功、推送成功、
日志全绿，只有"这份东西是给哪个市场的开盘用的"没有人问。

## 为什么不能靠改 cron 的小时数解决

把 `0 7` 改成 `0 19`，今天对，**十一月第一个周日之后就错一小时**——
美国退出夏令时，北京不动，两地时差从 12 小时变成 13 小时。
一年两次，每次错一小时，而且错的那天不会有任何提示。

crontab 与 launchd 都只认机器本地时间，跟不了另一个国家的夏令时。
**能跟得了的是 Python 的 zoneinfo。** 所以时区判断放在这里，
cron 只负责"多敲几次门"。

## 用法

    cron:    0 * * * 1-5   ...  run_premarket.py
    程序里:  ok, why = is_premarket()
             if not ok: 记一行日志，退出 0

一天敲 24 次门，其中 23 次在**发出任何网络请求之前**就退出。
换时区、换机器、夏令时切换，全都不用改任何配置。
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from .config import MARKET, market

PREMARKET_WINDOW = {
    "us": (time(6, 0), time(9, 15)),
    "cn": (time(7, 0), time(9, 15)),
}
"""**市场本地时间**的盘前窗口。终点定在开盘前 15 分钟：
美股 09:30 开、A股 09:30 开，留出发送和阅读的时间。

窗口而不是一个时刻，是因为采集加编撰要跑几十分钟——真机上这份简报
从启动到出 PDF 花了约 49 分钟。卡在一个时刻上，跑慢一点就整天不发。
"""

SNAPSHOT_WINDOW = {
    "us": (time(16, 30), time(23, 59)),
    "cn": (time(15, 30), time(23, 59)),
}
"""**收盘之后**的市场本地时间窗口 —— 每日 Signal Card 快照跑在这里。

盘中跑会把一根**没走完的 K 线**当成当天的收盘：量比、CMF、ATR 全都算在
半天的数据上，而卡片上写的日期是今天。这类错误不报错、图上也看不出来，
只有把当天和第二天的卡片摆在一起才会发现数对不上。

顺带一提，美股收盘后的窗口换算到北京是**次日上午**——
盘前简报落在她的傍晚、快照落在她的早晨，两个都是按市场时间排的，
不是按机器时间凑的。
"""

WEEKDAYS = (0, 1, 2, 3, 4)


def market_tz() -> ZoneInfo:
    return ZoneInfo(market().get("tz", "Asia/Shanghai"))


def market_now(now: datetime | None = None) -> datetime:
    """当前的**市场本地时间**。`now` 可注入，便于测试（这个模块必须可测）。"""
    return (now or datetime.now(ZoneInfo("UTC"))).astimezone(market_tz())


def window() -> tuple[time, time]:
    return PREMARKET_WINDOW.get(MARKET, PREMARKET_WINDOW["cn"])


def _in_window(win: dict, label: str, now: datetime | None) -> tuple[bool, str]:
    m = market_now(now)
    lo, hi = win.get(MARKET, win["cn"])
    name = market().get("name", MARKET)
    stamp = m.strftime("%Y-%m-%d %H:%M %Z")
    if m.weekday() not in WEEKDAYS:
        return False, f"{stamp}（{name}市场本地时间）是周末，不{label}"
    if not (lo <= m.time() < hi):
        return False, (f"{stamp}（{name}市场本地时间）不在{label}窗口 "
                       f"{lo.strftime('%H:%M')}–{hi.strftime('%H:%M')} 内")
    return True, (f"{stamp}（{name}市场本地时间）在{label}窗口 "
                  f"{lo.strftime('%H:%M')}–{hi.strftime('%H:%M')} 内")


def is_snapshot_time(now: datetime | None = None) -> tuple[bool, str]:
    """现在该不该存当日 Signal Card。**必须在收盘之后**，见 SNAPSHOT_WINDOW。"""
    return _in_window(SNAPSHOT_WINDOW, "快照", now)


def is_premarket(now: datetime | None = None) -> tuple[bool, str]:
    """现在是不是该发盘前简报。返回 (是否, **一句人能看懂的说明**)。

    说明无论真假都要给：退出时日志里必须写清楚"因为什么退出"，
    否则一个不发简报的早晨和一个没跑过的早晨长得一模一样。
    """
    m = market_now(now)
    lo, hi = window()
    name = market().get("name", MARKET)
    stamp = m.strftime("%Y-%m-%d %H:%M %Z")
    if m.weekday() not in WEEKDAYS:
        return False, f"{stamp}（{name}市场本地时间）是周末，不发"
    if not (lo <= m.time() < hi):
        return False, (f"{stamp}（{name}市场本地时间）不在盘前窗口 "
                       f"{lo.strftime('%H:%M')}–{hi.strftime('%H:%M')} 内，不发")
    return True, (f"{stamp}（{name}市场本地时间）在盘前窗口 "
                  f"{lo.strftime('%H:%M')}–{hi.strftime('%H:%M')} 内")


def local_window(machine_tz: str | None = None,
                 now: datetime | None = None) -> tuple[str, str]:
    """把盘前窗口换算成**这台机器本地时间**，用来告诉人"你这儿是几点"。

    换算随夏令时变化，所以它是一个查询函数，不是一个常量——
    把结果抄进 crontab 就又回到了原来那个坑。
    """
    tz = ZoneInfo(machine_tz) if machine_tz else None
    base = (now or datetime.now(ZoneInfo("UTC")))
    m = base.astimezone(market_tz())
    lo, hi = window()
    out = []
    for t in (lo, hi):
        dt = m.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
        out.append(dt.astimezone(tz).strftime("%H:%M") if tz
                   else dt.astimezone().strftime("%H:%M"))
    return out[0], out[1]


def cron_hint(machine_tz: str | None = None, now: datetime | None = None) -> list:
    """给人看的排程建议。**推荐每小时敲一次门，让程序自己判断。**"""
    lo, hi = local_window(machine_tz, now)
    name = market().get("name", MARKET)
    wl, wh = window()
    return [
        f"{name}盘前窗口（市场本地）：{wl.strftime('%H:%M')}–{wh.strftime('%H:%M')}",
        f"换算到这台机器现在的时区：       {lo}–{hi}",
        "",
        "推荐这样排（每小时敲一次，窗口外几毫秒就退出，夏令时切换不用管）：",
        "  0 * * * 1-5  cd ~/.openclaw/workspace/cio-agent && "
        ".venv/bin/python run_premarket.py",
        "",
        f"想少醒几次也行，但**一年两次夏令时切换要手动改**：0 {lo.split(':')[0]} * * 1-5",
    ]


def next_window_start(now: datetime | None = None) -> datetime:
    """下一次窗口开始的时刻（市场时区）。用于日志里写"下一班几点"。"""
    m = market_now(now)
    lo, _hi = window()
    cand = m.replace(hour=lo.hour, minute=lo.minute, second=0, microsecond=0)
    if cand <= m:
        cand += timedelta(days=1)
    while cand.weekday() not in WEEKDAYS:
        cand += timedelta(days=1)
    return cand
