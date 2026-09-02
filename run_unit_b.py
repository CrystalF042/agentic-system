#!/usr/bin/env python3
"""证券二部 — Systematic Analytics（确定性 · 零 LLM · 只测量，不判断）。

    universe + 关注池 → 行情/SEC基本面 → 风险·风格·基本面测量 → 百分位 → Exceptions → 归档 → Telegram

定位（2026-08 定稿）：二部不再宣称任何 alpha。Production Factor Set = ∅，研究职能 dormant。
它每天只回答一个问题：**这些公司和当前组合，在数字上处于什么状态？**
然后交给 CRO 判断风险、Portfolio Construction 定仓位、CEO 决策。

已停用（刻意的，不是遗漏）：Top N Picks / Model Weight / 正式方向性投票。
验证已证明该模型没有可用预测能力；继续展示"Model Picks"会给系统其他部分错误的信息权重。
研究代码完整保留在 run_validation.py / run_gate.py，但不进入日常生产决策链。

用法：
  CIO_MARKET=us python run_unit_b.py                    # 正式跑
  CIO_MARKET=us CIO_UB_LIMIT=60 python run_unit_b.py    # 小池子快速验（百分位分母同步变小）
  CIO_AN_NO_FUND=1 python run_unit_b.py                 # 跳过 SEC，只出风险测量
  CIO_QUANT_MOCK=1 CIO_TG_DRYRUN=1 python run_unit_b.py # 离线冒烟（合成行情）

相关：
  python run_gate.py status     查看研究台账与窗口状态
  python run_gate.py closeout   幂等应用已决定的研究收尾
"""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from cio import analytics, deliver, ledger   # noqa: E402
from cio.utils import get_logger             # noqa: E402

log = get_logger("cio.analytics.run")

_KIND_ORDER = ["corp_action", "stale", "drawdown", "volatility", "beta", "leverage",
               "correlation", "extended"]


def _summary(r) -> str:
    """Telegram 摘要：只报异常与口径，不报"今天该买什么"——二部没有那个职责。"""
    L = ["*Unit B — Systematic Analytics*", ""]
    L.append(f"as-of trade date {r.as_of_trade_date or 'n/a'} · {r.displayed_count} watchlist names "
             f"measured against {r.universe_count}")
    L.append(f"alpha vote: {r.alpha_vote} · research: {r.research_status} · factor set: "
             f"{', '.join(r.production_factor_set) or 'empty'}")
    L.append("")
    if not r.exceptions:
        L.append("No threshold breaches this run.")
    else:
        by: dict = {}
        for e in r.exceptions:
            by.setdefault(e.kind, []).append(e)
        L.append(f"Exceptions ({len(r.exceptions)}):")
        for k in _KIND_ORDER:
            for e in (by.get(k) or [])[:4]:
                L.append(f"· {e.message}")
            extra = len(by.get(k) or []) - 4
            if extra > 0:
                L.append(f"  … +{extra} more {k}")
    L.append("")
    for d in r.status.degraded:
        L.append(f"⚠ {d}")
    L.append("Measurement only — no directional view, no sizing. See PDF.")
    return "\n".join(L)


def main() -> int:
    limit = int(os.environ.get("CIO_UB_LIMIT", "0"))
    want_fund = os.environ.get("CIO_AN_NO_FUND", "0") != "1"

    # 台账自检：生产集本该是空的。若不空，说明有研究被标成 PASS —— 那是重大状态变化，
    # 必须显式提示，而不是让二部悄悄又开始发方向性信号。
    try:
        rs = ledger.research_status()
        if rs["production_factor_set"]:
            log.warning("Production Factor Set 非空：%s —— 二部当前设计不消费它，请人工确认",
                        rs["production_factor_set"])
        studies = ledger.load().get("studies") or {}
        pending = [sid for sid, _st, _r in ledger.CLOSEOUT
                   if sid in studies and not studies[sid].get("closed")]
        if pending:
            log.info("提示：%s 尚未收尾，可运行  python run_gate.py closeout", "、".join(pending))
    except Exception as e:
        log.info("台账自检跳过：%s", e)

    try:
        r = analytics.build_analytics(universe_limit=limit, want_fundamentals=want_fund)
    except Exception:
        log.error("Analytics 构建异常:\n%s", traceback.format_exc())
        return 1

    md_path, pdf_path = "", ""
    try:
        md_path, pdf_path = analytics.archive_and_render(r)
    except Exception:
        log.error("Analytics 归档/渲染异常:\n%s", traceback.format_exc())

    try:
        cap = f"Unit B — Systematic Analytics · as-of {r.as_of_trade_date or 'n/a'}"
        deliver.deliver_brief(_summary(r), pdf_path or "", caption=cap)
    except Exception:
        log.error("Analytics 推送异常:\n%s", traceback.format_exc())

    log.info("二部完成：measured=%d displayed=%d exceptions=%d as_of=%s run_id=%s pdf=%s",
             r.universe_count, r.displayed_count, len(r.exceptions),
             r.as_of_trade_date, r.run_id, bool(pdf_path))
    print(md_path or "(no md)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
