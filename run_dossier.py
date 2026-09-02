#!/usr/bin/env python3
"""个股情报档案（资料库驱动）一键入口。功能①。
先吃资料库存量（历史脉络）→ 缺口才增量补齐 → 数据锚定真值 → 编撰《个股情报档案》→ 归档 → Telegram。

用法：
  source .venv/bin/activate
  python run_dossier.py "工商银行"                 # 正式跑
  python run_dossier.py 601398                      # 代码也行
  CIO_DOSSIER_SUBJECT="AAPL" python run_dossier.py  # 钩子/定时用（读环境变量）
  CIO_MOCK_LLM=1 CIO_TG_DRYRUN=1 python run_dossier.py "创新药"   # 离线冒烟自测
"""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from cio import deliver, dossier          # noqa: E402
from cio.utils import get_logger          # noqa: E402

log = get_logger("cio.run_dossier")


def main() -> int:
    subject = (os.environ.get("CIO_DOSSIER_SUBJECT") or (sys.argv[1] if len(sys.argv) > 1 else "")).strip()
    if not subject:
        print('用法：python run_dossier.py "标的名/代码"   （或设 CIO_DOSSIER_SUBJECT 环境变量）')
        return 2

    try:
        r = dossier.build_dossier(subject)
    except Exception:
        log.error("个股档案编撰异常:\n%s", traceback.format_exc())
        return 1

    md_path, pdf_path = dossier.archive_and_render(r)

    try:
        summary = (f"*CIO 个股情报档案* — {r.resolved}（{r.dt_beijing} 北京）\n"
                   f"命中存量库 {r.archive_docs} 篇 / 本次增量 {r.fresh_docs} 条。\n"
                   f"{r.completeness[:120]}\n完整档案见附件 PDF。")
        deliver.deliver_brief(summary, pdf_path or "", caption=f"CIO 个股情报档案 {r.resolved}")
    except Exception:
        log.error("推送异常:\n%s", traceback.format_exc())

    log.info("个股档案完成：%s 存量%d 增量%d", r.resolved, r.archive_docs, r.fresh_docs)
    print(md_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
