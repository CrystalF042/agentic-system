#!/usr/bin/env python3
"""纸面验证一键闭环（爸爸的双账户验证法）。
把整条链跑通并留痕：二部量化选股 → CRO 风控评级（筛）→ 模拟 CEO 终批（默认批准所有未否决）
→ 财务部建仓入账（主盘=批准选股，影子盘=模型原始选股）→ 当日盯市 → 出 CRO评级 + 盈亏表两份 PDF。

首次运行=建仓起跑；之后每天用 run_cfo.py 盯市出盈亏表即可。
（一部日频3选已接入：两线各自选股、各自建仓、各自记影子盘，四本账并行。）

用法：
  source .venv/bin/activate
  python run_pilot.py                # 真实数据起跑（在你机器上）
  CIO_QUANT_MOCK=1 CIO_TG_DRYRUN=1 python run_pilot.py   # 离线冒烟
"""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from cio import cfo, cro, deliver, legacy_guard, ledger, quant_data, unit_a, unit_b   # noqa: E402
from cio.models import DailyPick                          # noqa: E402
from cio.utils import get_logger, stamp_beijing           # noqa: E402

log = get_logger("cio.run_pilot")


def main() -> int:
    # 合成行情不得写进真实账本。文档把 CIO_QUANT_MOCK=1 写成"离线冒烟"，
    # 但价格层认这个开关、记账层不认——冒烟跑会按合成价真的建仓、真的盯市，
    # 而当日幂等保护随后会让【真正的那一次运行】以为今天已经记过账，什么都不写。
    if os.environ.get("CIO_QUANT_MOCK") == "1" and os.environ.get("CIO_PILOT_ALLOW_MOCK_BOOK") != "1":
        print("拒绝执行：CIO_QUANT_MOCK=1 是合成行情，不能写进财务台账。\n"
              "  · 只想验证链路是否跑通：CIO_PILOT_ALLOW_MOCK_BOOK=1（会写入，请用一份可丢弃的 cfo.db）\n"
              "  · 想跑真实建仓：去掉 CIO_QUANT_MOCK")
        return 2
    try:
        today = stamp_beijing()[:10]
        # 1) 各线独立选股（真实数据在你机器上）
        # 二部：Production Factor Set 为空 → ABSTAIN，不提交方向性选股（由台账决定，非硬编码）。
        # 它当天的贡献是 Systematic Analytics 风险测量报告，不进建仓链。
        _ok, _why = ledger.alpha_vote_allowed(unit_b._FACTORS)
        if _ok:
            log.warning("二部恢复方向性选股（%s）—— 请人工确认", _why)
            badv = unit_b.build_unit_b(top_n=3)
            b_picks = [DailyPick(source="二部", code=p.code, name=p.name, yahoo=p.yahoo,
                                 direction="看多", score=p.composite, sector=p.sector) for p in badv.picks]
        else:
            log.info("二部 ABSTAIN：%s。本轮不建二部仓位", _why)
            b_picks = []
        try:
            a_picks, _ = unit_a.build_unit_a_daily(top_n=3)     # 一部日频3选（多空辩论漏斗）
        except Exception as e:
            log.warning("一部日频选股失败，本轮仅二部：%s", e)
            a_picks = []
        raw_picks = a_picks + b_picks
        if not raw_picks:
            print("两线本轮均无选股，闭环中止。")
            return 0

        # 2) CRO 对两条线统一风控评级（筛）
        rating = cro.build_cro(raw_picks)
        cro.archive_and_render(rating)

        # 3) 模拟 CEO 终批：默认批准所有【未硬否决】的（真实使用由你 Telegram 终批替换）
        vetoed = {(it.source, it.code) for it in rating.items if it.vetoed}
        approved = [p for p in raw_picks if (p.source, p.code) not in vetoed]

        # 4) 财务部按【来源分账】建仓：一部账户建一部批准的，二部账户建二部批准的；影子盘各记原始选股
        conn = cfo.connect()
        cfo.init_schema(conn)
        cfo.init_accounts(conn)
        prices = quant_data.latest_prices([p.code for p in raw_picks], {p.code: p.name for p in raw_picks})
        cfo.record_approvals(conn, today, [(p.code, p.source, int((p.source, p.code) in vetoed),
                                            int((p.source, p.code) not in vetoed)) for p in raw_picks])
        # 幂等守卫【按部门各判各的】：某部当日已建过仓则只跳过该部，另一部不受影响。
        # build19 曾把守卫写成全表按日期（无 account 条件），后果是一部辩论失败当天、
        # 二部一旦建了仓，一部当天就再也补不上——且盈亏表上看不出是故障还是真没选到。
        for dept in ("一部", "二部"):
            exist = conn.execute("SELECT COUNT(*) c FROM positions "
                                 "WHERE account IN (?, ?) AND book_date=?",
                                 (dept, f"{dept}_shadow", today)).fetchone()
            if exist["c"]:
                log.info("%s 当日已有持仓 %d 条，跳过重复建仓（另一部不受影响）。", dept, exist["c"])
                continue
            appr = [p for p in approved if p.source == dept]
            rawd = [p for p in raw_picks if p.source == dept]
            if appr:
                cfo.book(conn, dept, appr, today, prices, n_slots=3)
            if rawd:
                cfo.book(conn, f"{dept}_shadow", rawd, today, prices, n_slots=3)

        # 5) 盯市 + 日结 + 出盈亏表
        bench = quant_data.get_benchmark(60)
        bclose = float(bench["close"].iloc[-1]) if bench is not None and len(bench) else None
        cfo.mark_and_settle(conn, today, prices, bench_close=bclose)
        st = cfo.build_statement(conn, as_of=today)
    except Exception:
        log.error("纸面验证闭环异常:\n%s", traceback.format_exc())
        return 1

    _, cro_pdf = cro.archive_and_render(rating)
    _, cfo_pdf = cfo.archive_and_render(st)
    try:
        high = sum(1 for it in rating.items if it.rating == "高" and not it.vetoed)
        warn = f"（含 ⚠高风险 {high} 只，警示不否决交 CEO）" if high else ""
        aline = "、".join(f"{a.account} ¥{a.net_value:,.0f}（{a.pnl_pct:+.2%}）" for a in st.accounts)
        summary = (f"*纸面验证闭环 · 起跑*（{today}）\n"
                   f"一部 {len(a_picks)} 只 + 二部 {len(b_picks)} 只 → CRO 硬否决 {rating.vetoed_count} 只 → 批准 {len(approved)} 只{warn}\n"
                   f"投资倾向：{rating.leaning}｜总仓位：{rating.target_position}\n"
                   f"账户起步：{aline}\n"
                   f"（研究观点，非投资指令；之后每天盯市出盈亏表）")
        if legacy_guard.legacy_push_allowed("run_pilot.py（含老 CRO 的总仓位口径）"):
            deliver.send_text(summary)
            deliver.send_document(cro_pdf or "", "CRO 风控评级（退役模块）")
        deliver.send_document(cfo_pdf or "", f"财务部盈亏表 {st.as_of}")
    except Exception:
        log.error("推送异常:\n%s", traceback.format_exc())

    log.info("闭环完成：一部%d+二部%d 批准%d 只；账户 %s", len(a_picks), len(b_picks), len(approved),
             "、".join(f"{a.account}={a.net_value:.0f}" for a in st.accounts))
    print(f"CRO: {cro_pdf}\nCFO: {cfo_pdf}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
