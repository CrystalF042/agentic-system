#!/usr/bin/env python3
"""Telegram 控制台入口 —— 没有网页之前，手机就是界面。

    CIO_MARKET=us python run_tgbot.py

启动后在手机上发 /help。它会一直挂着等指令，Ctrl-C 退出。
想让它常驻，见 BUILD88_README 里的 launchd 段。

**先建一个专用 bot。** @BotFather → /newbot → 拿到 token →
写进 .env：

    CIO_CTRL_BOT_TOKEN=1234567:AA...

为什么不能和 OpenClaw 共用：Telegram 的一条更新**只投递给一个**
getUpdates 消费者。共用的话两边互相抢，**指令会随机丢失且没有任何提示**——
你以为点了批准，其实那条更新被另一边收走了。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from cio import tgbot                                             # noqa: E402


def main() -> int:
    try:
        return tgbot.serve(once="--once" in sys.argv[1:])
    except KeyboardInterrupt:
        print("\n控制台已退出。")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
