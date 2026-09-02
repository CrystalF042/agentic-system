"""§4 事件聚类（确定性、零 LLM）：把讲同一件事的多篇报道聚成一条【事件】。

信号：标题内容词 Jaccard 相似 + 共享标的(ticker) + （新闻本就同期，时间窗天然收紧）。
纪律（deterministic-first）：核心用确定性规则聚类，不让小模型"从零聚类"；
每个事件保留 member_count 与来源并集，任何误合都可审计。

四分卡在事件层取聚合值：confidence/materiality 取 max，relevance/immediacy 取最强，
来源取并集——这正是"20 篇同一事件 → 一条多源事件"的去噪效果。
"""
from __future__ import annotations

import hashlib
import os
import re
from functools import lru_cache

from .config import watchlist as _wl
from .models import Event, Source


@lru_cache(maxsize=1)
def _canon_map() -> dict:
    """公司名/代码 → 规范代码（Merck→MRK、Moderna→MRNA），让"名"与"码"能对上。"""
    m: dict[str, str] = {}
    for sec in (_wl().get("watchlist", {}) or {}).values():
        comp = sec.get("companies") or {}
        if isinstance(comp, dict):
            for name, tk in comp.items():
                canon = (str(tk) or str(name)).upper()
                m[str(name).lower()] = canon
                if tk:
                    m[str(tk).lower()] = canon
    return m


def _canon(tickers) -> set:
    cm = _canon_map()
    return {cm.get(str(t).lower(), str(t).upper()) for t in (tickers or []) if t}

# 停用词（英文）：结构词/泛动词，不参与事件判别
_STOP = {
    "the", "a", "an", "of", "to", "in", "on", "and", "or", "for", "as", "is", "are", "be",
    "at", "by", "from", "with", "that", "this", "it", "its", "was", "were", "has", "have",
    "after", "amid", "over", "into", "out", "up", "down", "new", "could", "will", "would",
    "may", "says", "say", "said", "report", "reports", "reported", "than", "more", "less",
    "not", "no", "but", "so", "if", "how", "why", "what", "who", "when", "amp", "live",
    "update", "updates", "today", "week", "year", "day", "com", "www", "https", "http",
}


def _tokens(title: str) -> set:
    """内容词集合：英文单词（长度≥2、去停用词）+ 单个 CJK 字。"""
    t = (title or "").lower()
    words = re.findall(r"[a-z0-9]{2,}", t)
    cjk = re.findall(r"[一-鿿]", t)
    return {w for w in words if w not in _STOP} | set(cjk)


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / len(a | b)


_REL_RANK = {"Direct": 2, "Sector": 1, "None": 0, "": 0}
_WHEN_RANK = {"Today": 3, "This week": 2, "Medium-term": 1, "Background": 0, "": 0}
_SIG_RANK = {"强": 2, "中": 1, "弱": 0}


def _dedupe_sources(members) -> list:
    seen, out = set(), []
    for n in members:
        for s in (n.sources or []):
            key = (getattr(s, "name", ""), getattr(s, "url", ""))
            if key in seen:
                continue
            seen.add(key)
            out.append(s)
    return out


def _to_event(members: list) -> Event:
    # 代表作：可信度→重要性→标题长度 最高者，作为 headline / summary 出处
    rep = max(members, key=lambda n: (n.source_confidence, n.materiality, len(n.title_original or "")))
    tickers: list[str] = []
    for n in members:
        for tk in (n.tickers or []):
            if tk and tk not in tickers:
                tickers.append(tk)
    relevance = max((n.watchlist_relevance for n in members), key=lambda r: _REL_RANK.get(r, 0))
    immediacy = max((n.immediacy for n in members if n.immediacy), key=lambda w: _WHEN_RANK.get(w, 0), default="Today")
    signal = max((n.signal for n in members), key=lambda s: _SIG_RANK.get(s, 0))
    pubs = [n.published_at for n in members if n.published_at]   # 事件时效取成员里最新一篇
    published_at = max(pubs) if pubs else None
    headline = rep.title_en or rep.title_original or rep.title_zh
    key = ",".join(sorted(_tokens(headline)))
    eid = "EVT-" + hashlib.md5(key.encode("utf-8")).hexdigest()[:6].upper()
    return Event(
        event_id=eid, headline=headline,
        summary=rep.summary_zh or rep.title_zh or rep.title_original,
        sector=rep.watchlist_sector or "", tickers=tickers[:5],
        event_type=rep.event_type, primary_tag=rep.primary_tag, signal=signal,
        confidence=max(n.source_confidence for n in members),
        materiality=max(n.materiality for n in members),
        relevance=relevance, immediacy=immediacy, published_at=published_at,
        sources=_dedupe_sources(members), member_count=len(members),
    )


def _cos(a, b) -> float:
    """余弦相似度（纯 Python，避免依赖）。"""
    if not a or not b:
        return 0.0
    import math
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _embed_titles(items) -> dict:
    """给每条标题算 nomic 向量（语义聚类用）。失败/离线自动回退（返回 None，退化为词聚类）。"""
    try:
        from .ollama_client import get_ollama
        oll = get_ollama()
    except Exception:
        return {}
    out = {}
    for n in items:
        try:
            out[id(n)] = oll.embed(n.title_original or n.title_zh or "")
        except Exception:
            out[id(n)] = None
    return out


def _cluster_diag(items: list, vecs: dict, clusters: list, tau: float, embed_ok: bool) -> dict:
    """聚类诊断：向量是否真跑 + "最接近但没合并"的余弦值。
    让 CEO 一眼判断 0 合并到底是①向量没跑（nomic 没 pull）②阈值太高 还是 ③本就无重复。"""
    cluster_of: dict = {}
    for ci, c in enumerate(clusters):
        for n in c["members"]:
            cluster_of[id(n)] = ci
    max_cos = None
    for i in range(len(items)):
        vi = vecs.get(id(items[i]))
        if not vi:
            continue
        for j in range(i + 1, len(items)):
            if cluster_of.get(id(items[i])) == cluster_of.get(id(items[j])):
                continue                      # 同簇=已合并，跳过
            vj = vecs.get(id(items[j]))
            if not vj:
                continue
            c = _cos(vi, vj)
            if max_cos is None or c > max_cos:
                max_cos = c
    return {"embed_ok": bool(embed_ok), "tau": tau, "max_unmerged_cos": max_cos,
            "n_reports": len(items), "n_events": len(clusters)}


def cluster_events(items: list, jac: float = 0.5, jac_ticker: float = 0.3,
                   embed: bool = True, emb_tau: float = 0.0,
                   diag: "dict | None" = None) -> list[Event]:
    """贪心聚类 → 事件。合并信号（任一成立即并入）：
      ① 标题词高度重叠(Jaccard≥jac)；② 同提两家公司；③ 共享1家公司且中等重叠；
      ④ 语义向量相似(cosine≥emb_tau) —— 补上"改写型同一事件"（如 OpenAI 两条）。
    护栏（保 purity）：两条都点名公司且完全不相交 → 判为不同事件，绝不合并（防 Pfizer×Merck 误合）。
    emb_tau 默认读环境变量 CIO_EMB_TAU（缺省 0.82），便于不改代码现场校准。
    diag（可选）：传入一个 dict，函数会回填 embed_ok / max_unmerged_cos / tau 等诊断值。
    返回按（重要性→可信度→相关度→篇数）降序。"""
    tau = emb_tau or float(os.environ.get("CIO_EMB_TAU", "0.82"))
    vecs = _embed_titles(items) if embed else {}
    embed_ok = any(v for v in vecs.values())   # 向量真跑起来了吗（nomic 没 pull → 全 None/空）
    clusters: list[dict] = []
    for n in items:
        toks = _tokens(n.title_original or n.title_zh)
        tks = _canon(n.tickers)
        nvec = vecs.get(id(n))
        placed = False
        for c in clusters:
            # 公司冲突护栏：不同公司 = 不同事件，任何路径都不合并
            if tks and c["tickers"] and not (tks & c["tickers"]):
                continue
            shared = len(tks & c["tickers"])
            s = _jaccard(toks, c["tokens"])
            emb = _cos(nvec, c.get("vec")) if (nvec and c.get("vec")) else 0.0
            if s >= jac or shared >= 2 or (shared >= 1 and s >= jac_ticker) or emb >= tau:
                c["members"].append(n)
                c["tokens"] |= toks
                c["tickers"] |= tks
                placed = True
                break
        if not placed:
            clusters.append({"tokens": toks, "tickers": tks, "members": [n], "vec": nvec})
    events = [_to_event(c["members"]) for c in clusters]
    events.sort(key=lambda e: (e.materiality, e.confidence, _REL_RANK.get(e.relevance, 0), e.member_count),
                reverse=True)
    if diag is not None:
        diag.update(_cluster_diag(items, vecs, clusters, tau, embed_ok))
    return events
