#!/usr/bin/env python3
"""市场快照符号自检 —— **接进简报之前，先在真机上验一遍哪些符号真能取到数。**

    python scripts/check_market_now.py

为什么单独做一个脚本：一个静默失效的行情符号，表现形式恰好是
**"这项今天没有异动"**——和真的没异动长得一模一样。
Yahoo 会随时下架、改名、或对某些符号返回空，而这些都不会抛异常。

输出里要看三样：

    价格取到没有        取不到就换符号，不要留在表里当摆设
    as-of 时间戳        期货应该是分钟级的"刚刚"，指数应该是收盘时刻
    实测年龄标签        07:00 ET 跑时，ES=F 该显示"实时"，日经该显示"今日 HH:MM"

**如果某个符号的年龄标签和你的预期不符，那就是它真的不新鲜**——
标签是按时间戳算出来的，不是按品种猜的。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cio import market_now                       # noqa: E402
from cio.config import market_now as _now        # noqa: E402

now = _now()
print("=" * 78)
print(f"市场快照符号自检　当前市场时间 {now.strftime('%Y-%m-%d %H:%M %Z')}")
print("=" * 78)
print()

ticks = market_now.snapshot(now=now)

cur = ""
ok = bad = 0
for t in ticks:
    if t.group != cur:
        cur = t.group
        print(f"\n【{cur}】")
    if t.last is None:
        bad += 1
        print(f"  ✗ {t.name:16} {t.symbol:12} 取不到 —— {t.note}")
        continue
    ok += 1
    pct = "—" if t.change_pct is None else f"{t.change_pct:+.2f}%"
    flag = "  ⚠ 过期或异常" if t.stale else ""
    print(f"  ✓ {t.name:16} {t.symbol:12} {t.last:>12,.2f}  {pct:>8}  "
          f"[{t.as_of}]  {t.age_label}{flag}")

print("\n" + "-" * 78)
print(f"{ok} 项取到，{bad} 项取不到。")
print(market_now.render_note(ticks, now))

if bad:
    print("\n取不到的符号请从 src/cio/market_now.py 的 SYMBOLS 里换掉或删掉——")
    print("**留在表里会让报告每天多出一行「未取到」，久了就没人看了。**")
    raise SystemExit(1)
raise SystemExit(0)
