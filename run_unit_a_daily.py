#!/usr/bin/env python3
"""证券一部·日频3选 一键入口。
关注池 → 新闻活跃度缩池 → 逐只多空辩论 → 按看多程度选3只 → 归档每只辩论 → Telegram 汇总。
研究观点、非投资指令；与二部/CIO 独立。较慢（要跑数只完整辩论），适合盘前批量。

用法：
  source .venv/bin/activate
  python run_unit_a_daily.py
  CIO_MOCK_LLM=1 CIO_QUANT_MOCK=1 CIO_TG_DRYRUN=1 python run_unit_a_daily.py   # 离线冒烟
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from cio import deliver, unit_a               # noqa: E402
from cio.utils import get_logger, stamp_beijing  # noqa: E402

log = get_logger("cio.run_unit_a_daily")


def main() -> int:
    try:
        picks, advices = unit_a.build_unit_a_daily(top_n=3)
    except Exception:
        log.error("一部日频选股异常:\n%s", traceback.format_exc())
        return 1

    pdfs = []
    for adv in advices:
        try:
            _, pdf = unit_a.archive_and_render(adv)    # 每只辩论各自归档留痕
            if pdf:
                pdfs.append((adv.resolved, pdf))
        except Exception:
            pass

    try:
        lines = "\n".join(f"{i}. {p.name}（{p.code}）方向：{p.direction}" for i, p in enumerate(picks, 1)) or "（本轮无选股）"
        summary = (f"*证券一部 · 日频3选*（{stamp_beijing()} 北京）\n"
                   f"关注池 → 新闻催化缩池 → 多空辩论 → 选看多前3\n{lines}\n"
                   f"（研究观点，非投资指令，须经 CRO 与 CEO 决断；每只辩论全文见附件）")
        deliver.send_text(summary)
        for name, pdf in pdfs:
            deliver.send_document(pdf, f"一部建议 {name}")
    except Exception:
        log.error("推送异常:\n%s", traceback.format_exc())

    log.info("一部日频3选完成：%s", "、".join(f"{p.name}:{p.direction}" for p in picks))
    print("\n".join(f"{p.source} {p.code} {p.name} {p.direction}" for p in picks))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
