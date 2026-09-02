#!/usr/bin/env python3
"""CEO 指令统一路由（Telegram 一句话 → 焦点 / 个股情报 / 专题报告）。
钩子把 CEO 整句话喂给本脚本（环境变量 CIO_CMD_TEXT，或命令行参数），本脚本判定走哪条：
  1) 个股情报（情报X / 深挖X / 档案X）           → run_dossier（资料库驱动）
  2) 专题分析（分析X / 研究X / 调研X / …）        → run_topic（专题报告）
  3) 焦点指令（侧重X / 重点看X / 关注X / 取消焦点） → 更新 focus.yaml，回一句确认
其余（普通闲聊）→ 不处理，交给 agent 正常回复。

CLI 自测：
  python run_command.py "侧重香港形势"
  python run_command.py "情报 工商银行"
  python run_command.py "分析创新药"
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from cio import deliver, focus              # noqa: E402
from cio.utils import get_logger, truncate  # noqa: E402

log = get_logger("cio.command")

# 明确的"出报告"指令前缀（放在焦点判断之前，避免"分析…关注…"被误当焦点）
_DOSSIER_PREFIX = re.compile(r"^\s*(帮我|请|麻烦)?\s*(情报档案|个股情报|情报|深挖|档案|dossier|资料库)")
_TOPIC_PREFIX = re.compile(r"^\s*(帮我|请|麻烦)?\s*(分析|研究|专题|情况报告|调研|梳理|盘点|深度)")
# 证券一部·日频3选（关注池→新闻催化→辩论→选3）；放在 _UNIT_A 之前判定
_UNIT_A_DAILY_PREFIX = re.compile(r"^\s*(帮我|请|麻烦)?\s*(一部日频|日频选股|日频|一部选股|一部三选|一部3选)")
# 证券一部（LLM 多空辩论 + 回测 → 一部建议）
_UNIT_A_PREFIX = re.compile(r"^\s*(帮我|请|麻烦)?\s*(证券一部|一部|交易建议|买卖建议|多空辩论|多空)")
# 证券二部（自研量化 → Top3 选股）；"二部/量化/选股/因子"
_UNIT_B_PREFIX = re.compile(r"^\s*(帮我|请|麻烦)?\s*(证券二部|二部|量化选股|量化|选股|因子选股|今日选股)")
# 风控部（CRO 五维风险 + 一票否决 + 投资倾向）
_CRO_PREFIX = re.compile(r"^\s*(帮我|请|麻烦)?\s*(风控|CRO|风险评级|投资倾向|风险)")
# 财务部（CFO 盯市 + 盈亏表）
_CFO_PREFIX = re.compile(r"^\s*(帮我|请|麻烦)?\s*(盈亏表|盈亏|账户|对账|财务|CFO)")
# 纸面验证闭环（二部→CRO→建仓→盯市，一键起跑）
_PILOT_PREFIX = re.compile(r"^\s*(帮我|请|麻烦)?\s*(纸面验证|纸面|起跑|开跑|建仓起跑|双账户|pilot)")
# 生成盘前简报（按需重跑，用于看焦点是否生效）
_BRIEF = re.compile(r"(生成|重新生成|刷新|出|来|发|再来|要).{0,3}(盘前简报|简报|早报|盘前)|^(盘前简报|简报|早报)$")


def _route(text: str) -> str:
    if _BRIEF.search(text):
        return "brief"
    if _PILOT_PREFIX.search(text):
        return "pilot"
    if _CRO_PREFIX.search(text):
        return "cro"
    if _CFO_PREFIX.search(text):
        return "cfo"
    if _UNIT_B_PREFIX.search(text):
        return "unit_b"
    if _UNIT_A_DAILY_PREFIX.search(text):
        return "unit_a_daily"
    if _UNIT_A_PREFIX.search(text):
        return "unit_a"
    if _DOSSIER_PREFIX.search(text):
        return "dossier"
    if _TOPIC_PREFIX.search(text):
        return "topic"
    if focus.parse_command(text).get("action") in ("set", "clear"):
        return "focus"
    # 兜底：像标的/主题就当专题；否则不处理
    return "topic" if len(text) >= 2 else "none"


def main() -> int:
    text = (os.environ.get("CIO_CMD_TEXT") or (" ".join(sys.argv[1:]) if len(sys.argv) > 1 else "")).strip()
    if not text:
        print('用法：python run_command.py "CEO 的一句话指令"   （或设 CIO_CMD_TEXT）')
        return 2

    route = _route(text)
    log.info("CEO 指令路由：%s → %s", truncate(text, 50), route)

    if route == "brief":
        from run_premarket import main as premarket_main
        # **人开口要，就一定给。** 盘前那道时间闸是给 cron 的
        # （防止"每小时采一次全网新闻"），不是给 CEO 的：
        # 她在下午发一句"出份简报"而系统回一句"不在盘前窗口"，
        # 是把一道内部排程规则当成了对人的拒绝。
        return premarket_main(force=True)

    if route == "unit_a_daily":
        from run_unit_a_daily import main as unit_a_daily_main
        return unit_a_daily_main()

    if route == "unit_a":
        os.environ["CIO_UNIT_A_SUBJECT"] = text
        from run_unit_a import main as unit_a_main
        return unit_a_main()

    if route == "unit_b":
        from run_unit_b import main as unit_b_main
        return unit_b_main()

    if route == "pilot":
        from run_pilot import main as pilot_main
        return pilot_main()

    if route == "cro":
        from run_cro import main as cro_main
        return cro_main()

    if route == "cfo":
        from run_cfo import main as cfo_main
        return cfo_main()

    if route == "focus":
        msg = focus.handle_command(text)
        try:
            deliver.send_text(msg)
        except Exception:
            pass
        print(msg)
        return 0

    if route == "dossier":
        os.environ["CIO_DOSSIER_SUBJECT"] = text
        from run_dossier import main as dossier_main
        return dossier_main()

    if route == "topic":
        os.environ["CIO_TOPIC_SUBJECT"] = text
        os.environ.setdefault("CIO_NO_ACK", "1")   # 钩子已回执，避免二次确认
        from run_topic import main as topic_main
        return topic_main(["run_topic", text])

    log.info("非指令消息，忽略。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
