"""§6 事件四分卡评分（确定性、零 LLM、非方向判断）。

四个相互独立的客观维度，替代旧的"强/中/弱"单一信号：
  • source_confidence 1–5 —— 证据可信度（按来源域，主源/监管=5）
  • event_type              —— 事件类型（earnings / m&a / regulatory / macro …），规则关键词判定
  • materiality 1–5         —— 客观重要性（由 event_type 基分 + 是否异动）
  • immediacy               —— 时效（Today / This week / Medium-term / Background，按新鲜度）
watchlist_relevance（Direct/Sector/None）已由 §8 classify.match_watchlist 产出。

全部可复算、可解释；无任何买卖/多空判断（那是一部/二部/CRO 的职权）。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

# ---------------- ① 来源可信度 1–5 ----------------
# 记录"原始最高可信源"，而非发现它的聚合器（GNews→Bloomberg 记 Bloomberg=4）。
_SRC5 = ("edgar", "sec.gov", "u.s. securities", "fda", "food and drug", "federal reserve",
         "the fed", "fomc", "treasury", "bls.gov", "bureau of labor", "bea.gov",
         "bureau of economic", "investor relations", " ir ", "nyse", "nasdaq",
         "company release", "press release", "businesswire", "globenewswire", "prnewswire",
         "federal register", "证监会", "上交所", "深交所", "巨潮")
_SRC4 = ("reuters", "bloomberg", "financial times", "wall street journal", "wsj", "cnbc",
         "dow jones", "associated press", "ap news", "新华", "xinhua")
_SRC3 = ("bbc", "scmp", "south china", "guardian", "npr", "france24", "al jazeera",
         "deutsche welle", "nikkei", "caixin", "财新", "人民日报")
_SRC2 = ("yahoo", "google news", "googlenews", "gnews", "marketwatch", "tradingview",
         "forbes", "benzinga", "eastmoney", "东方财富", "marketscreener", "thestreet",
         "fool.com", "motley fool", "bgr", "investing.com", "seeking alpha", "ign")


def source_tier(source) -> int:
    """单个来源的可信度档位 1–5（5=主源/监管，4=一线财经通讯社，3=通用大报，2=聚合器，1=其他）。"""
    blob = f" {getattr(source, 'name', '')} {getattr(source, 'url', '')} ".lower()
    if any(k in blob for k in _SRC5):
        return 5
    if any(k in blob for k in _SRC4):
        return 4
    if any(k in blob for k in _SRC3):
        return 3
    if any(k in blob for k in _SRC2):
        return 2
    return 1


def source_confidence(sources) -> int:
    """事件级可信度 = 成员来源里最高档（记录原始最高可信源，非发现它的聚合器）。"""
    return max((source_tier(s) for s in (sources or [])), default=1)


# ---------------- ② 事件类型（关键词规则，顺序=特异性）----------------
_EVENT_RULES = [
    ("m&a", r"\b(merger|acquisitions?|acquires?|acquired|takeover|buyout|to buy|deal to acquire)\b|并购|收购|要约"),
    ("regulatory", r"\b(fda|ema|pdufa|approv\w+|clearance|reject\w+|crl|recall|entity list|antitrust probe)\b|获批|审批|监管|召回|反垄断"),
    ("clinical", r"\b(phase [123]|topline|clinical (?:trial|results|data)|late-stage (?:trial|study|data|readout)|readout|(?:primary |secondary )?endpoint|progression-free survival|overall survival|extends survival|survival benefit)\b|临床|三期|数据积极"),
    ("sanctions", r"\b(sanctions?|export controls?|embargo|tariffs?|blacklist)\b|制裁|出口管制|关税"),
    ("legal", r"\b(lawsuit|court|ruling|verdict|settlement|indict\w+|plea)\b|诉讼|判决|和解|起诉"),
    ("macro", r"\b(federal reserve|the fed|fomc|interest rates?|rate (?:cut|hike|decision|hold)|(?:cut|hike|rais\w+|lower\w*) (?:interest )?rates?|rates? (?:steady|unchanged|on hold|higher|lower)|cpi|ppi|pce|payrolls|jobs report|gdp|inflation|treasury yield|bond yield|national debt|debt ceiling)\b|美联储|加息|降息|通胀|国债|利率"),
    ("guidance", r"\b(guidance|outlook|forecast|guides?|profit warning)\b|指引|上调预期|下调预期|业绩预告"),
    ("earnings", r"\b(earnings|quarterly results|q[1-4] (?:results|revenue)|beat\w*|miss(?:es|ed)?|revenue|net income|eps|profit surge)\b|财报|季报|营收|净利"),
    ("analyst", r"\b(upgrade[sd]?|downgrade[sd]?|price target|initiated coverage|rating|reiterat\w+)\b|评级|目标价|买入评级|卖出评级"),
    ("financing", r"\b(ipo|listing|bond sale|debt offering|capital raise|funding round|buyback|repurchase|stake)\b|上市|募资|回购|增发|定增"),
    ("management", r"\b(ceo|cfo|chair(?:man|woman)?|resigns?|steps? down|appoints?|names? new)\b|辞职|任命|换帅|离任"),
    ("bankruptcy", r"\b(bankruptcy|chapter 11|insolven\w+|defaults?)\b|破产|违约|资不抵债"),
    ("supply", r"\b(supply chain|production halt|shortage|capacity|shipment|output)\b|供应链|停产|短缺|产能|出货"),
    ("product", r"\b(launch\w*|unveils?|releases?|new (?:product|model|chip|drug)|rolls? out)\b|发布|推出|上新"),
]
_COMPILED = [(name, re.compile(pat, re.I)) for name, pat in _EVENT_RULES]


def classify_event_type(title: str, body: str = "") -> str:
    text = f"{title or ''} {body or ''}"
    for name, rx in _COMPILED:
        if rx.search(text):
            return name
    return "other"


# ---------------- ③ 重要性 1–5（event_type 基分 + 异动加成）----------------
_MAT_BASE = {
    "m&a": 5, "regulatory": 5, "clinical": 5, "earnings": 5, "bankruptcy": 5,
    "sanctions": 4, "guidance": 4, "legal": 4, "financing": 4, "management": 4, "macro": 4,
    "analyst": 3, "supply": 3, "product": 3, "other": 2,
}


def materiality(event_type: str, big_move: bool = False) -> int:
    m = _MAT_BASE.get(event_type, 2)
    return min(5, m + 1) if big_move else m


# ---------------- ④ 时效（按新鲜度）----------------
def immediacy(published_at) -> str:
    if not published_at:
        return "Today"                       # 当日采集、无时间戳 → 视为当日
    try:
        age_h = (datetime.now(timezone.utc) - published_at).total_seconds() / 3600.0
    except Exception:
        return "Today"
    if age_h < 36:
        return "Today"
    if age_h < 24 * 8:
        return "This week"
    if age_h < 24 * 45:
        return "Medium-term"
    return "Background"


# 传闻/泄露/未证实 → 重要性封顶（还没真发生的事，不该占高分）
_SPECULATIVE = re.compile(
    r"\b(leaks?|rumou?rs?|speculat\w+|unconfirmed|allegedly|tipped|hints? at|"
    r"what we know so far|wishlist|wish list)\b|传闻|据传|泄露|爆料|疑似|或将|有望", re.I)


def is_speculative(title: str) -> bool:
    return bool(_SPECULATIVE.search(title or ""))


# ---------------- 就地补齐四分卡（去重后调用，此时 sources 已合并）----------------
def enrich_scores(items) -> None:
    for n in items:
        n.source_confidence = source_confidence(n.sources)
        n.event_type = classify_event_type(n.title_original, n.body)
        big = "异动" in (n.trend_tags or []) or "Anomaly" in (n.trend_tags or [])
        n.materiality = materiality(n.event_type, big_move=big)
        if is_speculative(n.title_original):
            n.materiality = min(n.materiality, 2)   # 传闻封顶 M2
        n.immediacy = immediacy(n.published_at)
