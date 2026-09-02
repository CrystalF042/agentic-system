"""每一次运行的身份。

**为什么 `latest_*()` 不能作为界面的唯一入口。**

单用户串行敲命令时，"最近一次结果"和"我刚才那次结果"永远是同一个东西。
界面一进来这个等式就不成立了：两个浏览器窗口、同一个人连点两次、
定时任务和人工请求撞在一起——`latest` 会把**别人那一次**的结果
交给这一次的页面，而且**长得完全正常**：字段齐全、时间戳新鲜、
没有任何一处报错。用户看到的是一份属于另一只票、或者另一轮的研究。

所以每一次运行在开始时就领一个 id，之后整个页面只读这个 id 的结果。
`latest_advice()` 保留为便利入口（命令行、抽查），但不是身份。

格式：`ua-20260826-231945-a3f2`

    前缀    哪条链（ua=一部 / pc=定仓 / sc=扫描）
    日期时间 可排序、可读，肉眼就能对上日志
    随机尾   同一秒内并发也不会撞

**跟机器时钟而不是市场日期**：这是一次「执行」的身份，不是业务凭证的身份。
业务凭证（快照名、归档文件名）另有规矩，跟市场时区的日期走。
"""
from __future__ import annotations

import uuid
from datetime import datetime

SCHEMA_VERSION = "1.0"
"""所有 machine-facing JSON 的顶层版本号。

**字段改名不会让界面报错，只会让它显示空值**——那是又一次静默失败。
所以界面必须先看这个号：不认识就整体拒绝，而不是逐字段容错。

改动规则：
    加字段          不动版本号（旧界面照常工作）
    改字段含义/改名  **必须**升主版本号
    删字段          **必须**升主版本号
"""


def new_run_id(kind: str = "run") -> str:
    return (f"{kind}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            f"-{uuid.uuid4().hex[:4]}")


def envelope(kind: str, run_id: str, status: str = "completed", **rest) -> dict:
    """所有 --json 输出的统一信封。**三个字段永远在最外层。**

    status 取值：completed / no_candidates / gate_blocked / failed
    界面据此决定画什么，而不是靠猜数组空不空——
    "今天没有候选"和"跑挂了"都表现为 positions 为空，但含义相反。
    """
    out = {"schema_version": SCHEMA_VERSION, "run_id": run_id,
           "kind": kind, "status": status}
    out.update(rest)
    return out
