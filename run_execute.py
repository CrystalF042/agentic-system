#!/usr/bin/env python3
"""执行入口 —— 已批准的股数，在下一个交易日开盘成交并入账。

    PC 产生目标   →   CEO 产生授权   →   Execution 产生交易   →   Ledger 产生事实
                                        ↑ 本入口              ↑ 同时写

**只成交已经被批准的那个整数。** 不重算、不看行情好坏、不判断该不该做。

什么时候跑：批准之后的**任何时间**都可以跑，早了也没关系——
下一个交易日的行情还没出来时，它会说"等待开盘"并保持已批准状态，
**不会拿今天的价硬成交**。放进定时任务每天跑一次是最省心的用法。

用法：
    CIO_MARKET=us python run_execute.py
    CIO_MARKET=us python run_execute.py --json
    CIO_MARKET=us python run_execute.py --book      只看账本，不成交
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from cio import book, execution, marks, portfolio, runid           # noqa: E402
from cio.config import market, market_date                         # noqa: E402
from cio.utils import get_logger, stage                            # noqa: E402

log = get_logger("cio.run_execute")
RUN_ID = runid.new_run_id("ex")


def _pid(argv) -> str:
    if "--portfolio" in argv:
        i = argv.index("--portfolio")
        if i + 1 < len(argv):
            return argv[i + 1]
    return portfolio.MARKET_PORTFOLIO.get(
        market().get("news_region", "us"), portfolio.US_PAPER)


def _book_view(pid: str) -> str:
    hs = book.holdings(pid)
    px = marks.price_map([h["ticker"] for h in hs]) if hs else {}
    return book.render(pid, px)


def main() -> int:
    argv = sys.argv[1:]
    as_json = "--json" in argv
    say = (lambda *a, **k: None) if as_json else print
    pid = _pid(argv)
    today = str(market_date())

    stage("run_id", RUN_ID)
    stage("start", f"portfolio={pid}")

    if not book.is_book_portfolio(pid):
        msg = f"{pid} 还没开账：python run_rebalance.py --open-book"
        say(msg)
        if as_json:
            import json as _json
            print(_json.dumps(runid.envelope("execute", RUN_ID,
                                             status="book_not_open",
                                             portfolio_id=pid, note=msg),
                              ensure_ascii=False, default=str))
        return 0

    if "--book" in argv:
        print(_book_view(pid))
        return 0

    res = execution.run(pid, today=today, actor=f"exec:{RUN_ID}")
    stage("executed", "　".join(f"{k}={v}" for k, v in res["n_by_status"].items()))

    if as_json:
        import json as _json
        print(_json.dumps(runid.envelope(
            "execute", RUN_ID, status="completed", portfolio_id=pid, as_of=today,
            price_basis=marks.PRICE_BASIS, **res), ensure_ascii=False, default=str))
        return 0

    say("=" * 72)
    say(execution.render(res))
    say("=" * 72)
    say("\n" + _book_view(pid))

    if "--tg" in argv:
        from cio import tgbot
        from cio.config import settings
        try:
            sent = tgbot.send(execution.render(res) + "\n\n" + _book_view(pid))
            say("\nTelegram：" + ("DRYRUN，只打印未真发。" if settings.TG_DRYRUN
                                  else "已推送成交回报。" if sent
                                  else "未推送（token / chat_id 未配置或发送失败）。"))
        except Exception as e:                               # noqa: BLE001
            say(f"\nTelegram 推送失败（成交已入账，不受影响）：{e}")
    n_wait = res["n_by_status"].get(execution.WAITING, 0)
    if n_wait:
        say(f"\n{n_wait} 条在等下一个交易日开盘 —— 明天再跑一次就会成交。"
            f"（下一个交易日是**从行情里查**出来的，不按日历算，"
            f"所以周末和假日不会算错。）")
    if res["n_by_status"].get(execution.FAILED):
        say("\n⚠ 有未成交的条目 —— 上面写了原因。这些**不会自动重试**，"
            "需要重新提案（run_rebalance.py），因为原来的股数是基于当时的 NAV 算的。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
