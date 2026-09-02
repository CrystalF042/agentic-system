#!/usr/bin/env python3
"""发车时间自测 —— **回归 2026-09-02 那次"晚上七点发盘前"。**

    python scripts/test_schedule.py

那次的现场：简报本身完全正确（英文抬头、ET 时间戳、美股期货指数、
当天真实新闻），只有发车时间错了 —— 纽约 09-01 **19:49**，收盘四小时之后。

    cron:      0 7 * * 1-5          机器本地时间 7 点
    机器时区:   Asia/Shanghai
    北京 07:00 = 纽约前一天 19:00    分毫不差

cron、采集、PDF、推送全部成功，日志全绿。**没有任何一层问过
"这份东西是给哪个市场的开盘用的"。**
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

os.environ.setdefault("CIO_MARKET", "us")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _no_network                                            # noqa: E402,F401

from cio import schedule as S                                 # noqa: E402

OK, BAD = [], []
BJ = ZoneInfo("Asia/Shanghai")
NY = ZoneInfo("America/New_York")


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


def t_the_actual_failure_is_rejected():
    """**那一刻必须被判成"不该发"。** 这是本文件存在的唯一理由。"""
    ok, why = S.is_premarket(datetime(2026, 9, 2, 7, 49, tzinfo=BJ))
    assert ok is False, "北京 09-02 07:49（纽约 09-01 19:49）被判成了盘前"
    assert "19:49" in why and "EDT" in why, why


def t_the_right_moment_is_accepted():
    """纽约早上 07:30 该发。"""
    ok, why = S.is_premarket(datetime(2026, 9, 2, 7, 30, tzinfo=NY))
    assert ok is True, why


def t_weekend_is_rejected():
    ok, why = S.is_premarket(datetime(2026, 9, 5, 7, 30, tzinfo=NY))
    assert ok is False and "周末" in why, why


def t_edges_are_closed_at_the_top():
    """窗口是左闭右开：06:00 发，09:15 不发。**开盘前必须已经送到。**"""
    assert S.is_premarket(datetime(2026, 9, 2, 6, 0, tzinfo=NY))[0] is True
    assert S.is_premarket(datetime(2026, 9, 2, 9, 14, tzinfo=NY))[0] is True
    assert S.is_premarket(datetime(2026, 9, 2, 9, 15, tzinfo=NY))[0] is False
    assert S.is_premarket(datetime(2026, 9, 2, 5, 59, tzinfo=NY))[0] is False


def t_dst_shifts_the_local_hour_but_not_the_market_hour():
    """**夏令时是"改 cron 小时数"这条路的死因。**

    同一个市场窗口，在北京时间上夏天和冬天差一小时。手抄进 crontab 的
    数字一年会错两次，每次错一小时，而且不会有任何提示。
    """
    summer = S.local_window("Asia/Shanghai", datetime(2026, 9, 2, 12, tzinfo=ZoneInfo("UTC")))
    winter = S.local_window("Asia/Shanghai", datetime(2026, 12, 2, 12, tzinfo=ZoneInfo("UTC")))
    assert summer != winter, (summer, winter)
    assert summer[0] == "18:00" and winter[0] == "19:00", (summer, winter)
    # 但市场本地时间的窗口纹丝不动 —— 这正是判断该放在市场时区的理由
    lo, _hi = S.window()
    assert lo.strftime("%H:%M") == "06:00"


def t_window_follows_the_market_flag():
    """cn 和 us 的窗口不是同一个。"""
    from cio import config
    lo_us, _ = S.window()
    old = config.MARKET
    try:
        config.MARKET = "cn"
        import importlib
        importlib.reload(S)
        lo_cn, _ = S.window()
    finally:
        config.MARKET = old
        import importlib
        importlib.reload(S)
    assert lo_us != lo_cn, (lo_us, lo_cn)


def t_next_window_skips_weekends():
    nxt = S.next_window_start(datetime(2026, 9, 4, 20, tzinfo=NY))   # 周五晚
    assert nxt.weekday() == 0, nxt        # 下一班是周一
    assert nxt.strftime("%H:%M") == "06:00", nxt


def t_gate_runs_before_any_network_call():
    """**闸门必须在取数之前。**

    放在采集之后，就等于"每小时把全网新闻采一遍再决定要不要发"。
    这里断的是源码结构：`is_premarket` 的调用必须出现在
    `main()` 里第一个 collect/db 调用之前。
    """
    import ast
    src = (Path(__file__).resolve().parents[1] / "run_premarket.py").read_text("utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "main")
    gate_line = work_line = None
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            f = n.func
            name = getattr(f, "attr", getattr(f, "id", ""))
            if name == "is_premarket" and gate_line is None:
                gate_line = n.lineno
            if name in ("collect_premarket", "init_db", "collect_funds") and work_line is None:
                work_line = n.lineno
    assert gate_line is not None, "main() 里没有时间闸"
    assert work_line is not None and gate_line < work_line, \
        f"时间闸在第 {gate_line} 行，而取数在第 {work_line} 行 —— 闸门必须在前"


def t_manual_request_bypasses_the_gate():
    """**人开口要，就一定给。** Telegram 那条"生成盘前简报"必须绕过时间闸。

    时间闸是给 cron 的排程规则，不是对 CEO 的拒绝。
    """
    src = (Path(__file__).resolve().parents[1] / "run_command.py").read_text("utf-8")
    import ast
    tree = ast.parse(src)
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "premarket_main"]
    assert calls, "run_command 里找不到 premarket_main 调用"
    assert any(any(k.arg == "force" and k.value.value is True for k in c.keywords)
               for c in calls), "手动路径没有 force=True，CEO 会被时间闸挡住"


def t_us_mode_does_not_fetch_a_share_flows():
    """**美股模式下不再去取 A 股资金面。**

    那三条常驻降级（两市成交额/北向/板块资金）不是故障，是拿 A 股口径
    去问美股。每天必然失败三次、印在抬头上，把真正的降级淹掉。
    """
    from cio import funds
    st: dict = {}
    assert funds.collect_funds(st) == []
    assert "资金面" in st and "us 模式" in st["资金面"]
    for k in ("两市成交额", "北向资金", "板块资金"):
        assert k not in st, f"us 模式下仍在尝试 {k}"


TESTS = [
    ("**那次真实故障（北京07:49=纽约19:49）被拒**", t_the_actual_failure_is_rejected),
    ("纽约早上 07:30 放行", t_the_right_moment_is_accepted),
    ("周末不发", t_weekend_is_rejected),
    ("窗口左闭右开，开盘前必须送到", t_edges_are_closed_at_the_top),
    ("**夏令时会挪动本地小时数（手改 cron 的死因）**", t_dst_shifts_the_local_hour_but_not_the_market_hour),
    ("窗口跟着市场开关走", t_window_follows_the_market_flag),
    ("下一班跳过周末", t_next_window_skips_weekends),
    ("**闸门跑在任何取数之前**", t_gate_runs_before_any_network_call),
    ("**人手动要简报时绕过闸门**", t_manual_request_bypasses_the_gate),
    ("**us 模式不再取 A 股资金面**", t_us_mode_does_not_fetch_a_share_flows),
]

print("=" * 72)
print("发车时间自测 —— 按市场时区判断盘前，不按机器时区")
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
