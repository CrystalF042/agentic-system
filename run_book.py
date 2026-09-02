#!/usr/bin/env python3
"""账本日结 —— 公司行为 → 盯市 → 对账 → 盈亏表。

    PC 产生目标   →   CEO 产生授权   →   Execution 产生交易   →   Ledger 产生事实
                                                                    ↑ 本入口收尾

顺序是硬的，不能换：

    1. 公司行为    拆股改股数与成本价，分红入现金
    2. 盯市        未复权收盘价 × 股数 → NAV，写进净值曲线
    3. 对账        三条恒等式，任一不成立就**不出盈亏表**
    4. 盈亏表      文本 / Markdown / PDF / Telegram

**第 1 步必须在第 2 步之前。** 反了的话，盯市会拿**拆后价格**乘**拆前股数**，
那一天的市值凭空翻几倍或掉四分之三——一个完全正常的数字，没有任何报错。

**第 3 步失败就停。** 一份带警告的盈亏表还是会被读、被相信、被拿去做决定。
对账失败的含义不是"某个数字可能有点问题"，是**这本账现在自相矛盾**。

用法：
    CIO_MARKET=us python run_book.py                 日结并打印
    CIO_MARKET=us python run_book.py --pdf           另出 PDF
    CIO_MARKET=us python run_book.py --tg            推 Telegram
    CIO_MARKET=us python run_book.py --recon         只对账
    CIO_MARKET=us python run_book.py --actions       只查公司行为
    CIO_MARKET=us python run_book.py --curve         打印净值曲线
    CIO_MARKET=us python run_book.py --json
    CIO_MARKET=us python run_book.py --date 2026-09-01

**本入口零 LLM。**
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from cio import book, corp_actions, portfolio, recon                  # noqa: E402
from cio import render_book, runid, valuation                         # noqa: E402
from cio.config import OUT_DIR, market, market_date                   # noqa: E402
from cio.utils import get_logger, stage                               # noqa: E402

log = get_logger("cio.run_book")
RUN_ID = runid.new_run_id("bk")


def _arg(argv, name, default=""):
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return default


def _pid(argv) -> str:
    return _arg(argv, "--portfolio") or portfolio.MARKET_PORTFOLIO.get(
        market().get("news_region", "us"), portfolio.US_PAPER)


def _curve(pid: str) -> str:
    rows = valuation.series(pid, 60)
    if not rows:
        return f"{pid}：净值曲线还没有数据（先跑一次 run_book.py）。"
    L = [f"净值曲线 {pid}（最近 {len(rows)} 个记录日）",
         f"{'日期':<12}{'NAV':>14}{'当日':>12}{'累计':>10}{'基准':>10}"
         f"{'仓位':>8}  说明"]
    for r in rows:
        L.append(f"{r['date']:<12}"
                 f"{('不可计算' if r['nav'] is None else format(r['nav'], ',.2f')):>14}"
                 f"{('—' if r['day_pnl'] is None else format(r['day_pnl'], ',.2f')):>12}"
                 f"{('—' if r['cum_return'] is None else format(r['cum_return'], '.2%')):>10}"
                 f"{('—' if r['bench_cum_return'] is None else format(r['bench_cum_return'], '.2%')):>10}"
                 f"{('—' if r['invested_pct'] is None else format(r['invested_pct'], '.1%')):>8}"
                 + ("  ⚠ 缺价 %d 只" % r["n_unpriced"] if not r["complete"] else ""))
    return "\n".join(L)


def main() -> int:                                                    # noqa: C901
    argv = sys.argv[1:]
    as_json = "--json" in argv
    say = (lambda *a, **k: None) if as_json else print
    pid = _pid(argv)
    day = _arg(argv, "--date") or str(market_date())

    stage("run_id", RUN_ID)
    stage("start", f"portfolio={pid} date={day}")

    if not book.is_book_portfolio(pid):
        msg = f"{pid} 还没开账：python run_rebalance.py --open-book"
        say(msg)
        if as_json:
            import json as _json
            print(_json.dumps(runid.envelope("book", RUN_ID, status="book_not_open",
                                             portfolio_id=pid, note=msg),
                              ensure_ascii=False, default=str))
        return 0

    if "--curve" in argv:
        print(_curve(pid))
        return 0

    # ---------------------------------------------------------- 1. 公司行为
    ca = corp_actions.sync(pid)
    stage("corp_actions", f"applied={len(ca['applied'])} blocked={len(ca['blocked'])}")
    say(corp_actions.render(ca))
    if "--actions" in argv:
        for h in corp_actions.history(pid, 20):
            say(f"  {h['ex_date']}　{h['ticker']:<6} {h['kind']:<9} {h['note']}")
        return 0

    # ---------------------------------------------------------- 2. 盯市
    m = valuation.mark(pid, on=day)
    if not m.get("ok"):
        # **盯市没跑成就到此为止**，不要拿一个空壳继续算对账和盈亏表——
        # 那些数字会正常打印出来，而它们描述的是一次没发生的盯市。
        say("\n" + "=" * 72)
        say(f"盯市未执行：{m.get('note', '（未说明原因）')}")
        say("=" * 72)
        if as_json:
            import json as _json
            print(_json.dumps(runid.envelope(
                "book", RUN_ID, status="mark_failed", portfolio_id=pid,
                as_of=day, note=m.get("note", ""), corp_actions=ca),
                ensure_ascii=False, default=str))
        return 1
    stage("marked", f"nav={'None' if m['nav'] is None else round(m['nav'], 2)} "
                    f"unpriced={len(m['unpriced'])}")

    # ---------------------------------------------------------- 3. 对账
    rc = recon.check(pid, day)
    stage("recon", f"{rc['status']} fail={rc['n_fail']}")
    if "--recon" in argv:
        print(recon.render(rc))
        return 1 if rc["n_fail"] else 0

    if rc["n_fail"]:
        # **不出盈亏表。** 带警告的报表还是会被相信。
        say("\n" + recon.render(rc))
        say("\n" + "=" * 72)
        say("**本轮不出盈亏表** —— 账本自相矛盾，在修好之前由它算出的任何")
        say("收益率都没有意义。上面每条失败都写了两边的数字，从差额去找。")
        say("=" * 72)
        if as_json:
            import json as _json
            print(_json.dumps(runid.envelope(
                "book", RUN_ID, status="recon_failed", portfolio_id=pid,
                as_of=day, recon=rc, corp_actions=ca), ensure_ascii=False,
                default=str))
        return 1

    # ---------------------------------------------------------- 4. 盈亏表
    st = valuation.statement(pid, mark_result=m)
    text = render_book.render_text(st, rc, ca)
    say("\n" + "=" * 72)
    say(text)
    say("=" * 72)

    md_path = pdf_path = ""
    if "--pdf" in argv or "--tg" in argv:
        out = Path(OUT_DIR) / "book"
        out.mkdir(parents=True, exist_ok=True)
        md_path = str(out / f"pnl_{pid}_{day}.md")
        Path(md_path).write_text(render_book.render_md(st, rc, ca), encoding="utf-8")
        try:
            pdf_path = str(out / f"pnl_{pid}_{day}.pdf")
            engine = render_book.render_pdf(st, pdf_path, rc, ca)
            say(f"\nPDF：{pdf_path}（{engine}）")
        except Exception as e:                                # noqa: BLE001
            pdf_path = ""
            say(f"\nPDF 引擎不可用，只留 Markdown：{md_path}\n  {e}")

    if "--tg" in argv:
        from cio import deliver, tgbot
        from cio.config import settings
        try:
            sent = tgbot.send(text)
            if pdf_path and not settings.TG_DRYRUN:
                deliver.send_document(pdf_path, caption=f"盈亏表 {pid} {day}")
            say("\nTelegram：" + ("DRYRUN，只打印未真发。" if settings.TG_DRYRUN
                                  else "已推送盈亏表。" if sent
                                  else "未推送（token / chat_id 未配置或发送失败）。"))
        except Exception as e:                                # noqa: BLE001
            say(f"\nTelegram 推送失败（账本已入库，不受影响）：{e}")

    if as_json:
        import json as _json
        print(_json.dumps(runid.envelope(
            "book", RUN_ID, status="completed", portfolio_id=pid, as_of=day,
            statement=st, recon=rc, corp_actions=ca,
            md_path=md_path, pdf_path=pdf_path), ensure_ascii=False, default=str))
        return 0

    if not m["complete"]:
        say(f"\n⚠ {len(m['unpriced'])} 只持仓取不到价，当天 NAV 记为不可计算。"
            f"曲线上这一天是空白 —— **空白就是空白**，不拿剩下的凑一个数。")
    if ca["blocked"]:
        say(f"\n⚠ {len(ca['blocked'])} 项公司行为无法应用（见上）。"
            f"在应用之前，这些标的的盯市结果都不可信。")
    say(f"\n净值曲线：python run_book.py --curve")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
