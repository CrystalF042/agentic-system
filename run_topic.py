#!/usr/bin/env python3
"""专题报告入口（CEO 指令触发）。

用法：
  python run_topic.py "帮我分析苹果 AAPL 最近动向"
  python run_topic.py "创新药 医保谈判最近怎么样"
  python run_topic.py "/report 601398"
离线自测：
  CIO_MOCK_LLM=1 CIO_TG_DRYRUN=1 python run_topic.py "苹果 AAPL"
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from cio import deliver, topic          # noqa: E402
from cio.utils import get_logger, truncate  # noqa: E402

log = get_logger("cio.topic.run")


def main(argv: list[str]) -> int:
    # 主题来源：命令行参数优先；否则读环境变量 CIO_TOPIC_SUBJECT（供 OpenClaw skill 调用）
    if len(argv) >= 2 and argv[1].strip():
        text = " ".join(argv[1:]).strip()
    else:
        text = os.environ.get("CIO_TOPIC_SUBJECT", "").strip()
    if not text:
        print('用法：python run_topic.py "你的专题指令，例如：分析苹果 AAPL 最近动向"')
        return 2

    no_ack = os.environ.get("CIO_NO_ACK") == "1"   # 被 skill 触发时静默（由 agent 回确认）

    if topic.is_directional(text):
        log.info("检测到方向性问题 → 礼貌拒答方向、仍给事实。")

    log.info("收到专题指令：%s（正在编撰，约数分钟）", truncate(text, 60))
    if not no_ack:
        deliver.send_text(f"（@CIO）收到，正在为「{truncate(text, 40)}」编撰专题，约几分钟后回传 PDF。")

    r = topic.build_topic_report(text)
    md_path, pdf_path = topic.archive_and_render(r)

    cap = f"CIO 专题：{r.resolved}（{r.dt_beijing}）"
    if pdf_path:
        deliver.send_document(pdf_path, cap)
    else:
        deliver.send_text("PDF 渲染失败，Markdown 已存档：" + md_path)

    log.info("专题完成：%s\n  md=%s\n  pdf=%s", r.resolved, md_path, pdf_path)
    print(md_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
