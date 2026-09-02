"""趋势视角打标 + 关注池命中 + 打分（规则驱动，确定性、零幻觉）。

- 命中趋势关键词(资金面/政策/预期修正/异动/公告) → 加分并打 trend_tag
- 命中关注池(六大行/创新药/科技) → is_watchlist_hit 并大幅加分
- 源权威度 weight 计入
- 分数映射到 强/中/弱 信号
"""
from __future__ import annotations

import re

from .config import watchlist as _wl
from .utils import detect_lang


def _wl_cfg() -> dict:
    return _wl()


def _all_terms(section: dict) -> list[str]:
    terms: list[str] = []
    for key in ("keywords", "keywords_en", "names_cn"):
        terms += [t for t in (section.get(key) or []) if t]
    return terms


def _kw_terms(section: dict) -> list[str]:
    """强主题词（sector-defining）。公司名走 _companies 锚定，泛词走 _weak 只佐证。"""
    return [t for t in ((section.get("keywords") or []) + (section.get("keywords_en") or [])) if t]


def _weak_terms(section: dict) -> list[str]:
    """弱/泛词（FDA、chip、AI、GPU…）：只佐证，不可单独触发命中。"""
    return [t for t in (section.get("weak") or []) if t]


def _companies(section: dict) -> list[tuple[str, str]]:
    """公司锚定 [(name, ticker)]：US 用 companies 映射，A股用 names_cn（代码另算）。"""
    out: list[tuple[str, str]] = []
    comp = section.get("companies")
    if isinstance(comp, dict):
        out += [(str(n), str(t or "")) for n, t in comp.items() if n]
    elif isinstance(comp, list):
        out += [(str(n), "") for n in comp if n]
    out += [(str(n), "") for n in (section.get("names_cn") or []) if n]
    return out


# 既是公司名又是普通英文词：需大写词形 + 同板块主题词共现才算锚定（避免 apple 水果 / arm 手臂）
_AMBIG_ANCHORS = {"arm", "apple", "meta", "intel"}


def _has(term: str, text: str, ci: bool = False) -> bool:
    r"""词边界匹配。中文（无空格词界）走子串；英文走 \b（可选大小写不敏感）。"""
    if not term:
        return False
    if re.search(r"[一-鿿]", term):
        return term in text
    return re.search(rf"\b{re.escape(term)}\b", text, re.I if ci else 0) is not None


# §3 否定/对比语境：公司名前紧跟 not/without/vs/unlike/instead of/非/而非… → 是"排除/对比"而非主体
_NEG_CUE = re.compile(
    r"(?:\bnot\b|\bno\b|\bwithout\b|\bunlike\b|\bvs\.?\b|\bversus\b|\bbeyond\b|\bexcept\b|"
    r"\binstead of\b|\brather than\b|\bother than\b|非|而非|不是|除了)\s*[,:：\-–—\"'“”]*\s*$", re.I)


def _negated_all(term: str, text: str) -> bool:
    """term 在 text 中每一次出现都紧跟否定/对比词 → 视为未真正命中（如 'Not Microsoft'）。
    只要有一次出现在非否定语境，即返回 False（仍算命中）。"""
    if not term or not text:
        return False
    if re.search(r"[一-鿿]", term):                    # CJK：无词界，子串定位
        occ, i = [], text.find(term)
        while i >= 0:
            occ.append(i); i = text.find(term, i + 1)
    else:
        occ = [m.start() for m in re.finditer(rf"\b{re.escape(term)}\b", text, re.I)]
    if not occ:
        return False
    for i in occ:
        if not _NEG_CUE.search(text[max(0, i - 20):i]):
            return False                              # 存在非否定语境的出现 → 真命中
    return True


def _anchor_hit(name: str, ticker: str, title: str, body: str, theme_present: bool) -> "str | None":
    """公司/代码锚定命中 → 返回展示串，否则 None。§3：否定/对比语境（'Not Microsoft'）不算命中。"""
    T = f"{title}\n{body}"
    if ticker and _has(ticker, T, ci=False) and not _negated_all(ticker, T):   # ticker：大写敏感
        return ticker
    if not name:
        return None
    if name.lower() in _AMBIG_ANCHORS:                # 歧义词：Titlecase 词形 + 主题共现
        return name if (theme_present and _has(name, T, ci=False) and not _negated_all(name, T)) else None
    return name if (_has(name, T, ci=True) and not _negated_all(name, T)) else None   # 明确公司名 / 中文名


# 综合/摘要/复盘类稿件：本身是多主题汇编，不应被归入任何单一板块（否则整篇被一个泛词带偏）
_DIGEST = re.compile(
    r"(四大证券报|头条精华|版面头条|经济晚报|财经晚报|新闻联播|晨报|早报|要闻汇总|快讯汇总|"
    r"资讯汇总|盘点|复盘|收评|午评|早评|一图看懂|每日精选|集体下跌|集体上涨|三大指数)")


def _is_digest(title: str) -> bool:
    return bool(_DIGEST.search(title or ""))


def tag_trends(text: str) -> tuple[list[str], float]:
    """返回 (命中的趋势标签列表[按镜头权重降序,故 tags[0] 为主标签], 累计分)。"""
    cfg = _wl_cfg()
    lenses = cfg.get("trend_lenses", {})
    low = text.lower()
    hits: list[tuple[str, float]] = []
    total = 0.0
    for tag, spec in lenses.items():
        terms = (spec.get("keywords") or []) + (spec.get("keywords_en") or [])
        for t in terms:
            if t and ((t in text) or (t.lower() in low)):
                s = float(spec.get("score", 1))
                hits.append((tag, s))
                total += s
                break
    hits.sort(key=lambda x: x[1], reverse=True)
    return [t for t, _ in hits], total


def is_noise(title: str) -> bool:
    """标题党/自媒体夸张识别：命中则降权、不进 BLUF 与'强'。"""
    cfg = _wl_cfg()
    for k in cfg.get("noise_keywords", []):
        if k and k in title:
            return True
    if (title.count("！") + title.count("!") + title.count("？") + title.count("?")) >= 2:
        return True
    return False


def match_watchlist(title: str, body: str = "") -> "tuple[bool, str, list[str], str]":
    """关注池匹配（§8 实体→经济主体→板块，防误标）。返回 (命中, 板块, 命中项, 相关度)。
    相关度：
      Direct  —— 命中公司/代码锚定（真正的经济主体）。
      Sector  —— 无锚定，但命中"强主题词"（≥2 个，或 1 个在标题）——是板块级事件。
      None    —— 只蹭到泛词（FDA/chip/AI…）→ 不算命中。这正是"FDA 召回蓝莓≠医药"的修复点。
    """
    cfg = _wl_cfg()
    if _is_digest(title):
        return False, "", [], "None"
    title = title or ""
    body = body or ""
    best = None  # (rank, score, sector, matched, relevance)
    all_title_anchors: list[str] = []   # §2 标题里点名的所有关注池公司（多主体聚类用）
    for sector, section in cfg.get("watchlist", {}).items():
        # 强主题词（区分是否在标题）
        strong_title: list[str] = []
        strong_any: list[str] = []
        for kw in _kw_terms(section):
            if _has(kw, title, ci=True):
                strong_title.append(kw); strong_any.append(kw)
            elif _has(kw, body, ci=True):
                strong_any.append(kw)
        strong_distinct = list(dict.fromkeys(strong_any))
        # 弱泛词：不能单独触发命中，但可为"歧义公司名/仅正文锚定"提供上下文佐证
        weak_present = any(_has(w, title, ci=True) or _has(w, body, ci=True) for w in _weak_terms(section))
        context_present = len(strong_distinct) > 0 or weak_present
        codes = (section.get("a_shares") or []) + (section.get("etfs") or [])

        # 锚定：标题命中 = 直接主体(Direct)，无需佐证；仅正文命中必须有同板块上下文，
        # 避免正文偶然提到公司名（如渔民新闻正文蹭到 "Google"）被误判为科技股。
        # §2 标题级锚定收集【所有】点名公司（不只第一个）——让 "Moderna, Merck" 两家都成 ticker，
        #    聚类才能凭"共享公司"把同一多主体事件合并，同时冲突护栏仍拦截真正不同公司的误合。
        title_anchor = None
        for name, tk in _companies(section):
            a = _anchor_hit(name, tk, title, "", context_present)
            if a:
                if a not in all_title_anchors:
                    all_title_anchors.append(a)
                if title_anchor is None:
                    title_anchor = a
        for c in codes:
            if c and c in title:
                if c not in all_title_anchors:
                    all_title_anchors.append(c)
                if title_anchor is None:
                    title_anchor = c

        anchor = title_anchor
        if not anchor and context_present:
            for name, tk in _companies(section):
                a = _anchor_hit(name, tk, "", body, context_present)
                if a:
                    anchor = a; break
            if not anchor:
                anchor = next((c for c in codes if c and c in body), None)

        if anchor:
            # Direct：命中项就是那个公司/代码（干净的名字，不塞主题词）
            relevance, rank, score, matched = "Direct", 2, 6.0 + min(len(strong_distinct), 2) * 0.5, [anchor]
        elif strong_title or len(strong_distinct) >= 2:
            # Sector：无具体公司 → 命中项留空，展示层回退为板块名（不显示主题碎词）
            relevance, rank, score, matched = "Sector", 1, 3.5 + 0.5 * (len(strong_distinct) - 1), []
        else:
            continue  # 只有弱泛词/无强证据 → 不命中

        if best is None or (rank, score) > (best[0], best[1]):
            best = (rank, score, sector, matched, relevance)

    if best:
        sector, matched, relevance = best[2], best[3], best[4]
        if relevance == "Direct":
            # 多主体：并入标题里点名的其它关注池公司（去重、保序）
            allm = list(dict.fromkeys([m for m in (matched + all_title_anchors) if m]))
            return True, sector, allm[:5], relevance
        return True, sector, list(dict.fromkeys([m for m in matched if m]))[:4], relevance
    return False, "", [], "None"


def score_item(*, title: str, body: str, weight: int = 2) -> dict:
    """综合打分。信号强弱不在此定（由 brief 相对排序统一分配），此处只出原始分与主标签。"""
    cfg = _wl_cfg()
    text = f"{title}\n{body}"
    tags, tscore = tag_trends(text)
    hit, sector, matched, relevance = match_watchlist(title, body)
    noise = is_noise(title)

    # Direct（有锚定）比 Sector（仅主题）加更多分 —— 相关度直接影响排序
    wl_bonus = 6.0 if relevance == "Direct" else (3.0 if relevance == "Sector" else 0.0)
    score = float(weight) + tscore + wl_bonus
    if noise:
        score -= float(cfg.get("noise_penalty", 5))

    primary = tags[0] if tags else ("关注池" if hit else "")

    return {
        "score": round(score, 1),
        "primary_tag": primary,
        "trend_tags": tags,
        "is_watchlist_hit": hit,
        "is_noise": noise,
        "sector": sector,
        "tickers": matched,
        "watchlist_relevance": relevance,
        "lang": detect_lang(title or body),
    }


def assign_signals(items) -> None:
    """相对排序分配 强/中/弱（items 须已按 score 降序）。就地修改 .signal。
    早报与专题共用，保证'强'稀缺、有区分度。"""
    r = _wl_cfg().get("signal_ranking", {})
    n = len(items)
    cap = min(int(r.get("strong_cap", 6)), max(1, int(n * float(r.get("strong_ratio", 0.12)))))
    sfloor = float(r.get("strong_floor", 9))
    mfloor = float(r.get("medium_floor", 5))
    strong = 0
    for it in items:
        if strong < cap and it.score >= sfloor and not getattr(it, "is_noise", False):
            it.signal = "强"
            strong += 1
        elif it.score >= mfloor:
            it.signal = "中"
        else:
            it.signal = "弱"
