"""事实增补审计（零幻觉红线的兜底 lint）。

CIO 摘要必须"基于且仅基于原文"。本地小模型偶尔会脑补原文没有的数字/年份
（例如给 2026 的新闻硬安一句 "in 2021"，或凭空写出一个金额）。本模块做一件事：
把【摘要里出现、但原文里查无】的年份/数字挑出来，命中即标记，供 CEO 复核。

纪律：只标记、不改写（保留人在回路）；不碰方向性判断（那是 leakage.py 的职责）。
偏保守（宁可漏报，不可错杀）：数字比对容忍千分位/小数格式差异，个位数噪音一律跳过。
"""
from __future__ import annotations

import re

_YEAR = re.compile(r"\b(?:18|19|20)\d{2}\b")
# 数字：可带 $ 前缀、千分位逗号、小数点
_NUM = re.compile(r"\$?\d[\d,]*(?:\.\d+)?")


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def added_figures(summary: str, source: str, *, max_out: int = 4) -> list[str]:
    """返回【摘要中出现、但原文中查无】的年份/数字（去重、保序）。空列表 = 干净。

    - 年份（四位 18/19/20xx）：原文必须字面出现，否则判为增补（专抓 "in 2021" 这类脑补）。
    - 其它数字：把核心数字串（去掉逗号/小数点/货币符）拿去和"原文所有数字连成的串"做子串比对，
      容忍 "45%" vs "45 per cent"、"27.3billion" vs "27.3 billion" 这类格式差异；
      个位数（如 "two weeks" 的隐含 2、"$4" 的 4）噪音大，跳过不判。
    """
    summary = summary or ""
    source = source or ""
    src_digits = _digits(source)          # 原文数字连成串，做子串比对（容忍格式差异）
    out: list[str] = []
    seen: set[str] = set()
    # 1) 年份：原文必须字面出现该四位年份
    for m in _YEAR.finditer(summary):
        y = m.group(0)
        if y not in source and y not in seen:
            seen.add(y)
            out.append(y)
    # 2) 其它数字：核心数字串不在原文数字串里 → 判增补
    for m in _NUM.finditer(summary):
        tok = m.group(0)
        core = _digits(tok)
        if len(core) < 2:                 # 个位数噪音大，跳过
            continue
        if core in seen:
            continue
        if core not in src_digits:
            seen.add(core)
            out.append(tok.strip())
    return out[:max_out]
