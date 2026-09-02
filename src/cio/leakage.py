"""§21 方向性泄漏审计（对应 Improvement Spec Test 3）。

CIO 是"事实通道"，绝不做方向判断——买卖/多空/估值判断是证券一部、二部、CRO 的职权。
本模块扫描【CIO 自撰文本】（BLUF、市场异动、待观察、待决断），标记任何越权的方向性用词，
命中即上报供 CEO 复核（只标记、不自动删改，保留人在回路）。

注意：只扫 CIO 自撰内容，不扫转述的新闻标题/来源——转述带来源链接属客观事实通道，允许。
"""
from __future__ import annotations

import re

# 英文：明确的方向/估值意见词（词边界匹配，避免 buyback/buyer 之类误伤）
_EXCL_EN = [
    r"bullish", r"bearish", r"overvalued", r"undervalued",
    r"buying opportunity", r"attractively? valued", r"attractive valuation",
    r"strong buy", r"strong sell", r"should (?:buy|sell|outperform|underperform)",
    r"we recommend", r"our (?:investment )?recommendation", r"price target",
    r"positive catalyst", r"negative catalyst", r"table[- ]pounding",
    r"back up the truck", r"load up on",
]
_EN_RE = re.compile(r"\b(?:" + "|".join(_EXCL_EN) + r")\b", re.I)

# 中文：方向/评级/估值意见词（子串匹配）
_EXCL_ZH = [
    "看多", "看空", "看涨", "看跌", "利好", "利空", "逢低", "抄底", "逃顶",
    "建议买入", "建议卖出", "值得买入", "值得布局", "强烈推荐", "增持", "减持",
    "低估", "高估", "目标价", "买入评级", "卖出评级", "强烈看好",
]


def scan(texts: "list[str]") -> "list[str]":
    """返回 CIO 自撰文本中命中的方向性用词（去重、保序）。空列表 = 通过。"""
    hits: list[str] = []
    for t in texts:
        t = t or ""
        hits += [m.group(0) for m in _EN_RE.finditer(t)]
        hits += [z for z in _EXCL_ZH if z in t]
    return list(dict.fromkeys(h.strip() for h in hits if h and h.strip()))
