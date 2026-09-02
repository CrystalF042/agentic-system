"""退役模块的推送闸门。

**架构冻结 v1.0 把 `cro.build_cro()` 判为退役**：它输出
`target_position="中仓"`，同时对一部的论点、信心、失效条件一无所知
（grep 零命中）——既没读到一部，又越权做了 PC 的事。

但它现在仍然存在，仍然被 `run_cro.py` 与 `run_pilot.py` 调用，
**仍然往同一个 Telegram 频道推送「建议总仓位：中仓」**。

这在演示时是最糟糕的一种失败：新链路在群里说"CRO 给约束，PC 给权重，
两者都不判断论点对错"，往上翻两条是老 CRO 的"建议总仓位：中仓"。
**两条都不报错，看起来都像正式结论，只是互相矛盾。**

所以：退役模块可以继续在本地跑（历史数据、对照、调试都还需要它），
但**默认不再对外推送**。要推送必须显式打开：

    CIO_ALLOW_LEGACY_CRO=1 python run_cro.py

显式打开是一个人的决定，且在命令行上留痕；默认推送则谁都不知道它发过。
"""
from __future__ import annotations

import os

from .utils import get_logger

log = get_logger("cio.legacy")

ENV = "CIO_ALLOW_LEGACY_CRO"

_NOTE = (
    "【退役模块】cro.build_cro 已被架构冻结 v1.0 判为退役："
    "它输出「轻仓/中仓/重仓」，而仓位是 PC 的职权；"
    "它也读不到一部的论点/信心/失效条件。\n"
    "本次运行**不会推送到 Telegram**，避免与新链路（run_pc.py）的结论在同一频道上打架。\n"
    f"确需推送请显式打开：{ENV}=1")


def legacy_push_allowed(what: str = "老 CRO") -> bool:
    """退役模块能不能推送。**默认不能，且必须说出来。**"""
    if os.environ.get(ENV, "") == "1":
        log.warning("%s：%s=1，本次允许推送退役模块的结论——"
                    "请确认接收方知道这不是新链路的输出", what, ENV)
        return True
    log.warning("%s：%s", what, _NOTE)
    print("\n" + _NOTE + "\n")
    return False
