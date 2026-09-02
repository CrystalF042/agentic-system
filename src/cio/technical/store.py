"""Signal Card 落盘 —— **一次写定，永不重算。**

## 为什么"积累"必须有一个存储层

`observe()` 是纯函数，跑完什么都不留。要"在真实运行里积累"，
就得有个地方按天存下来。存储的规矩比存储本身更重要：

**一、按 as_of 日期一个文件，写过就不再写。**
参数改了之后重跑一遍历史，会把过去每一天的卡片按新参数改写。
新参数下的历史当然更"好看"——因为它本来就是拿这段历史调出来的。
所以默认拒绝覆盖已存在的日期；真要重写必须显式 `force=True`，
并且会在日志里说清楚覆盖了哪一天。

**二、每一行都带三个版本号。**
`schema_version`（字段契约）、`algo_version`（价区算法）、
`setup_version`（阈值定义）。混着不同版本的历史不是不能用，
但**必须看得出来它是混的**——`version_drift()` 就是干这个的。

**三、事件从卡片流里推导，不单独存事件。**
筛子的 KPI（每天推几条、值不值得看）和 setup 的 KPI（命中之后怎么样）
要的是同一份数据的两种切法。只存事件，筛子那半边的数据就没了；
两边各存一份，迟早对不上。
"""
from __future__ import annotations

import json
from pathlib import Path

from ..config import RAW_DIR
from ..utils import get_logger
from . import SCHEMA_VERSION
from .price_structure import ALGO_VERSION
from .setups import SETUP_VERSION, derive_events, evaluate
from .setups import params_fingerprint as setup_fingerprint

log = get_logger("cio.technical.store")

CARD_DIR = Path(RAW_DIR) / "technical_cards"


def _path(as_of: str) -> Path:
    return CARD_DIR / f"{str(as_of)[:10]}.jsonl"


def write_day(as_of: str, cards: list, force: bool = False) -> tuple[int, str]:
    """把一天的卡片写成一个 jsonl。返回 (写入条数, 说明)。

    **已存在就不写**，除非 `force=True`——见模块开头第一条。
    """
    p = _path(as_of)
    if p.exists() and not force:
        return 0, f"{p.name} 已存在，跳过（要覆盖请显式 force=True）"
    CARD_DIR.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(p, "w", encoding="utf-8") as f:
        for c in cards:
            row = c.to_dict()
            row["setup"] = evaluate(c)
            row["stamps"] = {"schema_version": SCHEMA_VERSION,
                             "algo_version": ALGO_VERSION,
                             "setup_version": SETUP_VERSION,
                             "setup_fingerprint": setup_fingerprint()}
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
    note = f"写入 {p.name}：{n} 张"
    if force and p.exists():
        note += "（**覆盖了已有的一天**）"
    log.info(note)
    return n, note


def dates() -> list:
    if not CARD_DIR.exists():
        return []
    return sorted(p.stem for p in CARD_DIR.glob("*.jsonl"))


def load_day(as_of: str) -> list:
    p = _path(as_of)
    if not p.exists():
        return []
    out = []
    for line in p.read_text("utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:                                  # noqa: BLE001
                log.warning("%s 里有一行读不动，跳过", p.name)
    return out


def version_drift() -> dict:
    """存下来的历史里出现过哪些版本号。**混版本不是错，看不出来才是。**"""
    seen: dict = {"schema_version": {}, "algo_version": {}, "setup_version": {}}
    for d in dates():
        for row in load_day(d):
            for k, box in seen.items():
                v = (row.get("stamps") or {}).get(k, "?")
                box.setdefault(v, []).append(d)
    return {k: {v: [min(ds), max(ds)] for v, ds in box.items()} for k, box in seen.items()}


def hit_series(symbol: str) -> list:
    """一只票在所有已存日期上的 `[(date, hit, lineage)]`，按日期升序。

    **血统从卡片里读，不用当前代码的。** 半年前存的卡片是按当时的算法
    算出来的；用今天的版本号给它盖章，等于把历史改写成"一直都是这套定义"。
    """
    out = []
    for d in dates():
        for row in load_day(d):
            if row.get("symbol") == symbol:
                st = row.get("stamps") or {}
                lin = (st.get("setup_version", "?"), st.get("setup_fingerprint", "?"),
                       st.get("algo_version", "?"), st.get("schema_version", "?"))
                out.append((d, bool((row.get("setup") or {}).get("hit")), lin))
                break
    return out


def events(symbols: list | None = None) -> list:
    """从卡片流里推导事件。**不存事件，只推导**——见模块开头第三条。"""
    if symbols is None:
        symbols = sorted({row.get("symbol", "") for d in dates()
                          for row in load_day(d)} - {""})
    out = []
    for s in symbols:
        out += derive_events(s, hit_series(s))
    return sorted(out, key=lambda e: (e.start, e.symbol))
