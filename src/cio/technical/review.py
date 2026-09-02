"""人工复核台账 —— **筛子的主 KPI 在这里，不在收益里。**

## 为什么必须有这个文件

技术筛子有两个完全不同的评价，不能混：

    作为筛子（近期主 KPI）   每天推几条？推出来的值不值得研究？省了盯盘时间吗？
    作为交易 setup（额外研究）  5/10/20 日超额、MFE/MAE、胜率、对照组

第二个要等样本——每天 1–2 只，攒到几百个事件要大半年。
**第一个今天就能测，但前提是有人把判断记下来。**

在这个文件出现之前，第一个 KPI 是测不了的：系统每天推一个名字，
没有任何地方记录"我看了，值/不值"。于是筛子好不好用，
只能靠印象——而印象会被最近一次的成败带着走。

## 三个判定，刻意不含收益

    worth     值得研究（推给一部之前，人愿意花时间看）
    skip      不值得（噪音、指数调仓、财报日的机械放量……）
    unclear   看不出来（**这一档必须存在**：逼人二选一会把犹豫记成假的确定）

**注意 `worth` 不等于"会涨"。** 它的意思是"这条线索值得占用研究时间"。
筛子的职责是省时间，不是预测方向；用涨跌去回填这一栏，
就把两个 KPI 又混成了一个。

## 一条记录钉死在一个 setup 版本上

阈值改了，历史复核不能跟着搬家：`setup-1.0.0` 上判过的 worth，
说的是那套阈值筛出来的东西。换版本就重新开始计数——
`stats()` 按版本分开报，就是为了这个。
"""
from __future__ import annotations

import json
from pathlib import Path

from ..config import RAW_DIR
from ..utils import get_logger
from .setups import SETUP_ID, SETUP_VERSION

log = get_logger("cio.technical.review")

REVIEW_PATH = Path(RAW_DIR) / "technical_cards" / "reviews.jsonl"
VERDICTS = ("worth", "skip", "unclear")
VERDICT_CN = {"worth": "值得研究", "skip": "不值得", "unclear": "看不出来"}


def _load() -> list:
    if not REVIEW_PATH.exists():
        return []
    out = []
    for line in REVIEW_PATH.read_text("utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:                                  # noqa: BLE001
                log.warning("复核台账里有一行读不动，跳过")
    return out


def mark(as_of: str, symbol: str, verdict: str, note: str = "",
         reviewed_at: str = "") -> dict:
    """记一条复核。**追加，不覆盖。**

    改主意就再记一条：`latest()` 取最后一条，但**前面那条留着**。
    复核意见变过，本身就是要看得见的信息——尤其是在事后知道结果之后改的。
    """
    v = str(verdict or "").strip().lower()
    if v not in VERDICTS:
        raise ValueError(f"判定只能是 {'/'.join(VERDICTS)}，收到 {verdict!r}")
    row = {"as_of": str(as_of)[:10], "symbol": str(symbol).upper(),
           "verdict": v, "note": str(note or "")[:300],
           "setup_id": SETUP_ID, "setup_version": SETUP_VERSION,
           "reviewed_at": reviewed_at or ""}
    REVIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(REVIEW_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def latest() -> dict:
    """{(as_of, symbol): 最后一条复核}。"""
    out: dict = {}
    for r in _load():
        out[(r.get("as_of", ""), r.get("symbol", ""))] = r
    return out


def revisions() -> list:
    """同一个 (日期, 标的) 上被改过的复核。**改过就要能看见。**"""
    seen: dict = {}
    changed = []
    for r in _load():
        k = (r.get("as_of", ""), r.get("symbol", ""))
        if k in seen and seen[k].get("verdict") != r.get("verdict"):
            changed.append((k, seen[k].get("verdict"), r.get("verdict")))
        seen[k] = r
    return changed


def stats() -> dict:
    """按 setup 版本分开统计。**换了阈值就重新计数**，见模块开头。"""
    box: dict = {}
    for r in _load():
        ver = r.get("setup_version", "?")
        b = box.setdefault(ver, {v: 0 for v in VERDICTS})
        v = r.get("verdict")
        if v in b:
            b[v] += 1
    # 同一 (日期,标的) 只算最后一条，避免改主意被重复计数
    box2: dict = {}
    for (_d, _s), r in latest().items():
        ver = r.get("setup_version", "?")
        b = box2.setdefault(ver, {v: 0 for v in VERDICTS})
        if r.get("verdict") in b:
            b[r["verdict"]] += 1
    return {"all_records": box, "deduped": box2}


def pending(hits: list) -> list:
    """还没被复核的命中。`hits` 是 [(as_of, symbol), ...]。"""
    done = set(latest())
    return [h for h in hits if h not in done]
