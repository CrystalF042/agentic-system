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
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from ..config import RAW_DIR
from ..utils import get_logger
from .setups import SETUP_ID, SETUP_VERSION

log = get_logger("cio.technical.review")

REVIEW_PATH = Path(RAW_DIR) / "technical_reviews" / "reviews.jsonl"
"""**台账不和卡片同目录。**

原来它是 `technical_cards/reviews.jsonl`，而 `store.dates()` 是
`glob("*.jsonl")` 取文件名——于是 `reviews` 被当成了一个交易日，
`events()` / `version_drift()` / `hit_series()` 全都在遍历它，
**复核台账的行一直在被当作信号卡片读**。

今天没出事，只是因为台账的行里没有 `symbol` 字段、恰好被跳过了。
**"恰好没出事"和"不会出事"是两回事。**

两条一起修，只做一条都不够：这里把台账挪出卡片目录（**不再有可污染的东西**），
`store.dates()` 那边只认 `YYYY-MM-DD` 形状的文件名（**万一又有人往里放东西**）。
"""

LEGACY_REVIEW_PATH = Path(RAW_DIR) / "technical_cards" / "reviews.jsonl"
"""旧位置。**只读、只搬一次，不双写**——两个地方各存一份迟早对不上。"""
VERDICTS = ("worth", "skip", "unclear", "excluded")
"""前三档是**对标的的判断**，`excluded` 是**对这次复核本身的判断**。

`excluded` 的意思是"这一条已经没法干净地复核了"——最典型的是信号过了
好几天才回头看，那时后续走势已经知道了。它**既不算 worth 也不算 skip，
更不进分母**：把它记成 skip 等于凭空造出一个"当时不值得研究"的结论。
"""

JUDGEMENTS = ("worth", "skip", "unclear")
"""进值得率分母的三档。`excluded` 不在里面。"""

VERDICT_CN = {"worth": "值得研究", "skip": "不值得", "unclear": "看不出来",
              "excluded": "排除（这次复核已不干净）"}

RETROSPECTIVE_CONTAMINATION = "retrospective_contamination"
"""`excluded` 最常见的理由：**看过后续走势之后才回头判的**。"""

CLEAN_MAX_LAG = 0
"""主 KPI 只收**信号当天**判的。

为什么是 0 而不是 1：隔一个交易日，那一天的走势已经看得见了。
T+1 单独展示、可以作为次要口径，但**不许混进主值得率**。
这个数是一个判断，不是拟合出来的——改它要同时改文档和用例。
"""


def migrate_if_needed() -> str:
    """把旧位置的台账搬到新位置。**搬一次，之后旧文件改名让位。**

    不双写、不合并：两个地方各存一份，迟早对不上，而对不上的那天
    没有任何东西会告诉你哪一份是真的。
    """
    if not LEGACY_REVIEW_PATH.exists():
        return ""
    if REVIEW_PATH.exists():
        return (f"**两个位置都有台账**：新的 {REVIEW_PATH}、旧的 {LEGACY_REVIEW_PATH}。"
                "没有自动合并——请你自己看过再决定留哪一份。")
    REVIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
    REVIEW_PATH.write_bytes(LEGACY_REVIEW_PATH.read_bytes())
    moved = LEGACY_REVIEW_PATH.with_suffix(".jsonl.moved")
    LEGACY_REVIEW_PATH.rename(moved)
    note = (f"复核台账已从卡片目录搬出：{LEGACY_REVIEW_PATH.name} → "
            f"{REVIEW_PATH.parent.name}/{REVIEW_PATH.name}"
            f"（旧文件改名为 {moved.name}，没有删除）")
    log.info(note)
    return note


def _load() -> list:
    migrate_if_needed()
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


def market_stamp() -> str:
    """现在的**市场时区**时间戳，带偏移量的 ISO：`2026-09-04T20:54:09-04:00`。

    **不用机器本地时间。** 机器搬个时区，同一批复核的时间戳口径就变了，
    而台账上看不出来。市场时区跟着 `CIO_MARKET` 走，和窗口判定同源。

    **也不让 CLI 传。** 让人自己填时间就是多开一个人为错误入口——
    而这个字段的全部价值在于它是自动的、没法事后凑的。
    """
    from ..schedule import market_now
    return market_now().replace(microsecond=0).isoformat()


def trading_days_between(as_of: str, reviewed_iso: str) -> Optional[int]:
    """信号日 → 复核日之间隔了几个**交易日**（不是日历天）。

    周五的信号、周一复核，答案是 `1` 不是 `3`。

    **不含节假日。** 这一层刻意做成保守的：感恩节那种情况会把真实的
    lag=1 算成 2，于是那条复核被推进**更严格**的桶。宁可把干净的算成脏的，
    不能反过来——这个数存在的唯一理由就是防止污染混进主 KPI。
    """
    if not as_of or not reviewed_iso:
        return None
    try:
        d0 = date.fromisoformat(str(as_of)[:10])
        d1 = date.fromisoformat(str(reviewed_iso)[:10])
    except ValueError:
        return None
    if d1 < d0:
        return None                        # 复核早于信号，说不通，不猜
    n, cur = 0, d0
    while cur < d1:
        cur += timedelta(days=1)
        if cur.weekday() in (0, 1, 2, 3, 4):
            n += 1
    return n


def mark(as_of: str, symbol: str, verdict: str, note: str = "",
         reviewed_at: str = "") -> dict:
    """记一条复核。**追加，不覆盖；但同判定不重复写。**

    改主意就再记一条：`latest()` 取最后一条，但**前面那条留着**，
    并且新那条带上 `previous_verdict`。复核意见变过本身就是信息——
    尤其是在事后知道结果之后改的。

    **同一个 (日期, 标的, setup 版本) + 同一个判定 → 不写第二行。**
    原来是照写不误、靠 `stats()` 在统计阶段去重——那是一种静默行为：
    台账里躺着两行一样的记录，而"去重了"这件事只发生在读的时候。
    **台账本身应该是干净的，不该靠统计阶段收拾。**

    返回的 dict 里带一个 `action`：`written` / `unchanged` / `revised`。
    这个键**不写进文件**，只用来告诉调用方发生了什么。
    """
    v = str(verdict or "").strip().lower()
    if v not in VERDICTS:
        raise ValueError(f"判定只能是 {'/'.join(VERDICTS)}，收到 {verdict!r}")
    note = str(note or "")[:300]
    if v == "excluded" and not note.strip():
        raise ValueError(
            f"excluded 必须写理由（最常见的是 {RETROSPECTIVE_CONTAMINATION}）——"
            "一条没有理由的排除，和一条被悄悄丢掉的记录没有区别")

    key = (str(as_of)[:10], str(symbol).upper())
    prev = latest().get(key)
    if (prev and prev.get("verdict") == v
            and prev.get("setup_version") == SETUP_VERSION):
        log.info("%s %s 已经复核过且判定未变（%s），不重复写",
                 key[0], key[1], VERDICT_CN.get(v, v))
        return dict(prev, action="unchanged")

    row = {"as_of": key[0], "symbol": key[1],
           "verdict": v, "note": note,
           "setup_id": SETUP_ID, "setup_version": SETUP_VERSION,
           "reviewed_at": reviewed_at or market_stamp()}
    row["review_lag_trading_days"] = trading_days_between(
        row["as_of"], row["reviewed_at"])
    action = "written"
    if prev and prev.get("verdict") != v:
        row["previous_verdict"] = prev.get("verdict")
        action = "revised"
    REVIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(REVIEW_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return dict(row, action=action)


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


def lag_bucket(row: dict) -> str:
    """一条复核落在哪个桶。**桶是按"判的时候能不能看见后续走势"分的。**

        clean          lag == 0        信号当天判的，未来走势还没发生
        t1             lag == 1        隔一个交易日 —— 那一天的走势已经看得见
        retrospective  lag >= 2        更晚，越晚越脏
        unknown        没有 reviewed_at 的老记录 —— **不知道就是不知道**

    `unknown` 不许并进 `clean`。老台账里那些没有时间戳的记录，
    我们**没有任何证据**说它们是当天判的；把它们算进主 KPI，
    等于用一个不知道的东西去撑一个要人相信的数。
    """
    lag = row.get("review_lag_trading_days")
    if lag is None:
        return "unknown"
    if lag <= CLEAN_MAX_LAG:
        return "clean"
    if lag == CLEAN_MAX_LAG + 1:
        return "t1"
    return "retrospective"


BUCKETS = ("clean", "t1", "retrospective", "unknown")


def stats() -> dict:
    """按 setup 版本分开统计。**换了阈值就重新计数**，见模块开头。

    返回三块：

        all_records   每一行都算（包括改主意的历史）
        deduped       同一 (日期,标的) 只算最后一条
        by_lag        再按"判的时候能不能看见后续走势"分桶

    **主 KPI 只看 `by_lag[版本]["clean"]`。** 别的桶照常展示，
    但不许并进那个值得率——见 `lag_bucket` 和 `CLEAN_MAX_LAG`。
    """
    box: dict = {}
    for r in _load():
        ver = r.get("setup_version", "?")
        b = box.setdefault(ver, {v: 0 for v in VERDICTS})
        v = r.get("verdict")
        if v in b:
            b[v] += 1
    # 同一 (日期,标的) 只算最后一条，避免改主意被重复计数
    box2: dict = {}
    by_lag: dict = {}
    for (_d, _s), r in latest().items():
        ver = r.get("setup_version", "?")
        b = box2.setdefault(ver, {v: 0 for v in VERDICTS})
        if r.get("verdict") in b:
            b[r["verdict"]] += 1
        lb = by_lag.setdefault(ver, {k: {v: 0 for v in VERDICTS} for k in BUCKETS})
        bucket = lag_bucket(r)
        if r.get("verdict") in lb[bucket]:
            lb[bucket][r["verdict"]] += 1
    return {"all_records": box, "deduped": box2, "by_lag": by_lag}


def worth_rate(box: dict) -> tuple:
    """(值得率, 分母)。**`excluded` 不进分母。**

    分母是 0 时返回 `(None, 0)`——**不是 0%**。
    没有样本和"一条都不值得"是两件完全不同的事，
    而 `0%` 会把前者说成后者。
    """
    n = sum(box.get(v, 0) for v in JUDGEMENTS)
    if not n:
        return None, 0
    return box.get("worth", 0) / n, n


def pending(hits: list) -> list:
    """还没被复核的命中。`hits` 是 [(as_of, symbol), ...]。"""
    done = set(latest())
    return [h for h in hits if h not in done]
