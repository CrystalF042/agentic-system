#!/usr/bin/env python3
"""财务部（CFO）一键入口——每日盯市 + 日结 + 出盈亏表。
读账本里的持仓 → 拉当日收盘价真值盯市 → 日结净值/盈亏/超额 → 出《盈亏表》→ 归档 → Telegram。
零 LLM、纯账本、只纸面不实盘。建仓由 run_pilot.py / 审批流负责，本入口专管盯市与出表。

用法：
  source .venv/bin/activate
  python run_cfo.py                 # 对现有持仓盯市并出当日盈亏表
  CIO_QUANT_MOCK=1 CIO_TG_DRYRUN=1 python run_cfo.py   # 离线冒烟
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from cio import cfo, deliver, quant_data       # noqa: E402
from cio.utils import get_logger, stamp_beijing  # noqa: E402

log = get_logger("cio.run_cfo")


def main() -> int:
    try:
        conn = cfo.connect()
        cfo.init_schema(conn)
        cfo.init_accounts(conn)
        # 取所有在册持仓的代码 → 拉当日收盘真值 → 盯市日结
        rows = conn.execute("SELECT DISTINCT code,name FROM positions WHERE open=1").fetchall()
        if not rows:
            print("账本暂无持仓——请先经审批流/ run_pilot.py 建仓，再盯市。")
            return 0
        codes = [r["code"] for r in rows]
        names = {r["code"]: r["name"] for r in rows}
        prices = quant_data.latest_prices(codes, names)
        bench = quant_data.get_benchmark(60)
        bclose = float(bench["close"].iloc[-1]) if bench is not None and len(bench) else None
        today = stamp_beijing()[:10]
        cfo.mark_and_settle(conn, today, prices, bench_close=bclose)
        st = cfo.build_statement(conn, as_of=today)
    except Exception:
        log.error("CFO 盯市/出表异常:\n%s", traceback.format_exc())
        return 1

    md_path, pdf_path = cfo.archive_and_render(st)
    try:
        lines = "\n".join(f"· {a.account}：净值 ¥{a.net_value:,.0f}（{a.pnl_pct:+.2%}）超额 {a.excess:+.2%}"
                          for a in st.accounts) or "（无账户）"
        summary = (f"*财务部盈亏表*（盯市 {st.as_of}）\n{lines}\n{st.compare_note}\n"
                   f"（零 LLM 纯账本，只纸面不实盘）\n完整盈亏表见附件 PDF。")
        deliver.deliver_brief(summary, pdf_path or "", caption=f"财务部盈亏表 {st.as_of}")
    except Exception:
        log.error("推送异常:\n%s", traceback.format_exc())

    log.info("CFO 盈亏表完成：as_of=%s 账户=%d 持仓=%d", st.as_of, len(st.accounts), len(st.positions))
    print(md_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
