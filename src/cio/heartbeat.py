"""流水线心跳 —— **"今天没有"和"今天没跑"必须长得不一样。**

## 这个模块是被两次真实故障逼出来的

**一、盘前简报静默失踪三天。** launchd 排在 19:30，时间闸每天照常把它挡回去，
日志里留一行"跳过"——而没有任何人会看那个日志。问题从"每天在错的时间收到"
变成"每天什么都收不到"，**而后者更难发现**：错的时间至少还有东西到手上。

**二、全市场大盘超额一起变 null。** 502 张卡片各写一句"该字段是 null"，
一个正确的事实说了 502 遍，**没有变成一个结论**。

两次的形状是同一个：**系统在正常运行的外观下什么也没做，或者做错了。**

## 所以这份报告的规矩

**一、声明式阶段表。** 阶段是**事先声明**的，不是跑到哪算哪。没跑到的阶段
在报告里印成"未运行"——否则一个死在中途的流水线，报告只是短一点，
而"短一点"和"今天本来就没什么事"看起来一样。

**二、0 也要印。** `0 triggers` 是一个结论，不是空白。

**三、每天都发，包括什么都没发生的日子。** 一份"今天全 0"的报告，
和一份根本没来的报告，是两件完全不同的事。

**四、报告落盘。** 磁盘上有没有那一天的报告，就是"那天到底跑没跑"的答案。
`missing_days()` 专门回答这个问题。

**五、一个阶段炸了不拖垮别的。** 异常被记成 `failed` + 异常类型，
继续跑下一个阶段——但**绝不吞掉**：它会出现在报告里，也会进退出码。

**六、告警不是备注**（build122 加）。CRO 否决了一只票、队列和提案库对不上，
这两件事和"今天扫了 502 只"不是同一类信息，不能排在同一串 note 里。

报告有六节，一条重要的话排在第五节的第三行**等于没说**——
半年之内人就会养成只看第一行的习惯。所以 `alert()` 单独收，
**印在报告最上方**，并且让当次推送成为强制的：

    ⚠ **本次有 1 条需要你现在就看**
       CRO 否决 AMD：已实现波动率(60日年化) 92.31% 触及否决线 80.00%

一道从来没否决过的风控闸，和一道否决了但没人看见的风控闸，
**在结果上是同一道闸**。
"""
from __future__ import annotations

import json
import traceback
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from .config import RAW_DIR
from .utils import get_logger

log = get_logger("cio.heartbeat")

REPORT_DIR = Path(RAW_DIR) / "heartbeat"

SCHEMA_VERSION = "heartbeat-1.1.0"
"""build122：阶段多了 `alerts`，而且**推送的含义变了**——
有 alert 时不再是"发一份日常心跳"，是"有事要你现在看"。
字段是加的，语义是改的，所以升版本。
"""

PIPELINE = (
    ("technical_snapshot", "技术快照"),
    ("research_router", "研究路由"),
    ("research_queue", "研究队列"),
    ("unit_a", "证券一部"),
    ("cro_pc", "风控与仓位"),
    ("ceo", "待你批准"),
)
"""**整条流水线的阶段表，事先声明。**

后面每个 build 往里加一行，报告格式不变——这就是为什么它先于那些 build 存在。
Build 1 只有第一行真的会跑；Build 2 接上了路由和队列。
**没接上的照样出现在报告里、标"未运行"**——那不是噪音，
那是"这条链还有几节没接上"这个事实本身。
"""

NOT_RUN, OK, FAILED, SKIPPED = "not_run", "ok", "failed", "skipped"

STATUS_CN = {NOT_RUN: "未运行", OK: "完成", FAILED: "**失败**", SKIPPED: "跳过"}


class Stage:
    """一个阶段的记录。**计数为 0 也照印。**"""

    def __init__(self, key: str, label: str):
        self.key, self.label = key, label
        self.status = NOT_RUN
        self.counts: dict = {}
        self.notes: list = []
        self.alerts: list = []
        self.error = ""

    def count(self, **kw) -> None:
        """记数。`count(scanned=502, triggers=0)` —— **0 是一个结论。**"""
        for k, v in kw.items():
            self.counts[k] = v

    def note(self, text: str) -> None:
        if text:
            self.notes.append(str(text))

    def alert(self, text: str) -> None:
        """**要你现在就看的一条。** 印在报告最上方，且让本次推送成为强制的。

        和 `note` 分开是因为它们的**读者行为**不同：note 是"跑完了，
        这是过程"，alert 是"停一下"。混在一起的下场是人只看第一行。

        理由必填——**一条说不出原因的告警比没有告警更糟**：
        它把人叫过来，然后让人去查一个不存在的问题。
        """
        if not str(text or "").strip():
            raise ValueError("alert 必须写清楚是什么事 —— "
                             "说不出原因的告警会把人叫去查一个不存在的问题")
        self.alerts.append(str(text))

    def skip(self, why: str) -> None:
        """本阶段**有意**不跑（不是没跑到）。理由必填。"""
        if not why:
            raise ValueError("skip 必须写理由 —— 没有理由的跳过和没跑到分不开")
        self.status = SKIPPED
        self.note(why)

    def to_dict(self) -> dict:
        return {"key": self.key, "label": self.label, "status": self.status,
                "counts": dict(self.counts), "notes": list(self.notes),
                "alerts": list(self.alerts), "error": self.error}


class _StageCtx:
    def __init__(self, rep: "Report", st: Stage):
        self.rep, self.st = rep, st

    def __enter__(self) -> Stage:
        return self.st

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None:
            # **一个阶段炸了不拖垮别的，但绝不吞掉。**
            self.st.status = FAILED
            self.st.error = f"{exc_type.__name__}: {exc}"
            log.error("阶段 %s 失败：\n%s", self.st.key,
                      "".join(traceback.format_exception(exc_type, exc, tb)))
            return True                    # 已记录，继续跑后面的阶段
        if self.st.status == NOT_RUN:
            self.st.status = OK
        return False


class Report:
    """一天的流水线报告。**阶段表是声明的，不是跑出来的。**"""

    def __init__(self, as_of: str, pipeline: tuple = PIPELINE):
        self.as_of = str(as_of)[:10]
        self.pipeline = pipeline
        self.stages = {k: Stage(k, lab) for k, lab in pipeline}

    def stage(self, key: str) -> _StageCtx:
        if key not in self.stages:
            raise KeyError(
                f"{key!r} 不在声明的阶段表里：{[k for k, _ in self.pipeline]}。"
                "加阶段要改 PIPELINE —— **不许临时冒出一个没声明的阶段**，"
                "那样它不跑的时候就不会出现在报告里")
        return _StageCtx(self, self.stages[key])

    # ---- 判断 ----

    def failed(self) -> list:
        return [s for s in self.stages.values() if s.status == FAILED]

    def never_ran(self) -> list:
        return [s for s in self.stages.values() if s.status == NOT_RUN]

    def alerts(self) -> list:
        """`[(阶段标签, 那句话)]`，按流水线顺序。**空列表是正常状态。**"""
        out = []
        for key, _lab in self.pipeline:
            s = self.stages[key]
            out.extend((s.label, a) for a in s.alerts)
        return out

    def exit_code(self) -> int:
        """有阶段失败就非 0。**未运行不算失败**——Build 1 时后五节本来就没接。"""
        return 1 if self.failed() else 0

    # ---- 渲染 ----

    def render(self) -> str:
        out = [f"CIO 流水线心跳　{self.as_of}"]
        # **告警排在最上面。** 排在第五节第三行的一句话等于没说 ——
        # 报告有六节，人很快就会只看第一行。
        al = self.alerts()
        if al:
            out.append("")
            out.append(f"⚠ **本次有 {len(al)} 条需要你现在就看**")
            for label, text in al:
                out.append(f"   · [{label}] {text}")
            out.append("")
        for key, _lab in self.pipeline:
            s = self.stages[key]
            head = f"[{s.label}] {STATUS_CN[s.status]}"
            if s.counts:
                head += "　" + "　".join(f"{k} {v}" for k, v in s.counts.items())
            out.append(head)
            if s.error:
                out.append(f"    {s.error}")
            for a in s.alerts:
                out.append(f"    ⚠ {a}")
            for n in s.notes:
                out.append(f"    {n}")
        nr = self.never_ran()
        if nr:
            out.append("")
            out.append("**未运行的阶段**：" + "、".join(s.label for s in nr))
            out.append("（未运行 ≠ 今天没事。这几节现在还没接上流水线。）")
        if self.failed():
            out.append("")
            out.append("**本次有阶段失败** —— 见上面标了失败的那几行。")
        return "\n".join(out)

    # ---- 落盘 ----

    def to_dict(self) -> dict:
        return {"as_of": self.as_of, "schema_version": SCHEMA_VERSION,
                "stages": [self.stages[k].to_dict() for k, _ in self.pipeline]}

    def save(self) -> Path:
        """一天一个文件，**同一天重跑会覆盖**。

        和卡片不同：卡片是观察，覆盖会改写历史；心跳是"今天跑过没有"，
        一天里跑两次，我们要的是最后那次的状态。
        """
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        p = REPORT_DIR / f"{self.as_of}.json"
        p.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
                     encoding="utf-8")
        return p

    def push(self) -> bool:
        """推给人。**每天都推，包括全 0 的日子。**

        有 alert 的那次推送失败要**大声报**：日常心跳掉一次，第二天就补上了；
        一条"CRO 否决了 AMD"的推送掉了，那件事就再也不会主动出现在她面前。
        """
        from . import deliver
        try:
            ok = deliver.send_text(self.render())
        except Exception:                                      # noqa: BLE001
            log.error("心跳推送失败：\n%s", traceback.format_exc())
            ok = False
        if not ok and self.alerts():
            log.error("**本次有 %d 条告警而推送没成功** —— "
                      "日常心跳掉一次第二天会补上，告警掉了就再也不会自己出现：\n%s",
                      len(self.alerts()),
                      "\n".join(f"  [{l}] {t}" for l, t in self.alerts()))
        return ok


# ---- 读回来 ----

def dates() -> list:
    if not REPORT_DIR.exists():
        return []
    out = []
    for p in sorted(REPORT_DIR.glob("*.json")):
        if len(p.stem) == 10 and p.stem[4] == "-" and p.stem[7] == "-":
            out.append(p.stem)
    return out


def load(as_of: str) -> Optional[dict]:
    p = REPORT_DIR / f"{str(as_of)[:10]}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text("utf-8"))
    except Exception:                                          # noqa: BLE001
        log.warning("%s 读不动", p.name)
        return None


def missing_days(back: int = 14, weekdays_only: bool = True) -> list:
    """最近 `back` 天里**没有报告**的日子。

    **这是"今天到底跑没跑"的答案。** 一份全 0 的报告说"跑了，没事"；
    没有报告说"根本没跑"——而在这个函数存在之前，这两件事从磁盘上分不出来，
    也从你的收件箱里分不出来（都是什么都没有）。
    """
    have = set(dates())
    today = date.today()
    out = []
    for i in range(back):
        d = today - timedelta(days=i)
        if weekdays_only and d.weekday() not in (0, 1, 2, 3, 4):
            continue
        if d.isoformat() not in have:
            out.append(d.isoformat())
    return sorted(out)
