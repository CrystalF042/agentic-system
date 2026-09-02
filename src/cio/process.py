"""第二部分：数据处理与资产沉淀。
去重 → 打分 → (按需)翻译摘要 → 切块向量化 → Company Archive + LanceDB + SQLite。
LLM 仅用于 摘要/翻译/分类/重组，严禁补事实（零幻觉）。"""
from __future__ import annotations

import re

from . import db
from .classify import score_item
from .config import COMPANY_DIR, settings
from .models import NewsItem, RawItem, Source
from .ollama_client import get_ollama
from .utils import clean_text, file_stamp, get_logger, safe_filename

log = get_logger("cio.process")


def _norm_title(t: str) -> str:
    t = re.sub(r"[^0-9a-zA-Z一-鿿]+", "", (t or "").lower())
    return t[:48]


def dedupe_and_score(raw_items: list[RawItem]) -> tuple[list[NewsItem], int]:
    """去重(sha + 近似标题) + 合并多源 + 规则打分。返回 (news_items, 去重条数)。"""
    by_sig: dict[str, NewsItem] = {}
    seen_sha: set[str] = set()
    deduped = 0
    for it in raw_items:
        if it.sha256 in seen_sha:
            deduped += 1
            continue
        seen_sha.add(it.sha256)
        sig = _norm_title(it.title)
        if not sig:
            continue
        sc = score_item(title=it.title, body=it.body, weight=it.weight)
        src = Source(name=it.source_name, url=it.source_url)
        if sig in by_sig:
            # 同一事件多源 → 合并来源（本土源 | 海外源 交叉对照），保留正文更长者
            ni = by_sig[sig]
            if all(s.url != src.url for s in ni.sources):
                ni.sources.append(src)
            if len(it.body) > len(ni.body):
                ni.body = it.body
            ni.score = max(ni.score, sc["score"])
            ni.weight = max(ni.weight, it.weight)
            deduped += 1
            continue
        by_sig[sig] = NewsItem(
            title_original=it.title,
            title_zh=it.title if it.lang == "zh" else "",
            title_en=it.title if it.lang == "en" else "",
            body=it.body,
            region=it.region, weight=it.weight,
            score=sc["score"], primary_tag=sc["primary_tag"], trend_tags=sc["trend_tags"],
            is_noise=sc["is_noise"],
            is_watchlist_hit=sc["is_watchlist_hit"], watchlist_sector=sc["sector"],
            watchlist_relevance=sc.get("watchlist_relevance", "None"),
            tickers=sc["tickers"], sources=[src], published_at=it.published_at,
        )
    items = sorted(by_sig.values(), key=lambda n: n.score, reverse=True)
    from . import scoring
    scoring.enrich_scores(items)   # §6 补齐四分卡（此时 sources 已按去重合并）
    return items, deduped


def hydrate(items: list[NewsItem], *, model: str | None = None) -> None:
    """对将要展示的条目补齐 中英对照标题 + 一句话中文摘要（调 Ollama；就地修改）。
    model 可指定摘要/翻译用的模型（BLUF 用 gpt-oss:20b 精修，长尾用 phi4-mini）。
    生成摘要后立即做【零幻觉数字核验】：摘要里出现但原文查无的年份/数字记入 fact_suspect（供 CEO 复核）。"""
    from . import factlint
    from .config import market
    en = market().get("lang", "zh") == "en"
    oll = get_ollama()
    for ni in items:
        if en:
            # 美股：英文原生，不翻中文（标题留英文原文）；一句话摘要用英文
            if not ni.summary_zh:
                ni.summary_zh = oll.summarize_en(ni.title_original, ni.body, model=model)
        else:
            if not ni.title_zh:
                ni.title_zh = oll.translate_to_zh(ni.title_original, model=model)
            if not ni.title_en and ni.title_original:
                ni.title_en = ni.title_original if not ni.title_zh or ni.title_zh != ni.title_original else ""
            if not ni.summary_zh:
                ni.summary_zh = oll.summarize_zh(ni.title_original, ni.body, model=model)
        # §零幻觉兜底：摘要里出现但原文查无的年份/数字 → fact_suspect（只标记不改写，人在回路）
        if ni.summary_zh and not ni.fact_suspect:
            sus = factlint.added_figures(ni.summary_zh, f"{ni.title_original}\n{ni.body}")
            if sus:
                ni.fact_suspect = sus
                log.warning("数字核验：摘要含原文之外的数字 %s ｜ %s", sus, (ni.title_original or "")[:40])


def _chunk(text: str, size: int = 700, overlap: int = 110) -> list[str]:
    text = clean_text(text)
    if not text:
        return []
    words = text.split()
    if len(text) <= size:
        return [text]
    chunks, i = [], 0
    # 以字符窗口切（中英混排更稳），overlap ~15%
    while i < len(text):
        chunks.append(text[i:i + size])
        i += size - overlap
    return chunks


def ingest_to_archive(raw_items: list[RawItem]) -> int:
    """把原始资料清洗、切块、向量化，沉淀到 Company Archive + LanceDB + SQLite。
    返回本次新写入的向量条数。永久保留、sha256 去重防重复、绝不覆盖。"""
    from .vectorstore import get_store  # 延迟导入，避免无谓加载
    vs = get_store()
    db.init_db()
    stamp = file_stamp()
    total_vecs = 0
    for it in raw_items:
        if db.doc_exists(it.sha256):
            continue
        body = clean_text(it.body)
        if len(body) < 40:  # 太短的跳过入库（仍在 raw-data 留档）
            continue
        # 标准化文件写入 Company Archive（不覆盖）
        fname = f"{safe_filename(it.title)}+{stamp}.md"
        path = COMPANY_DIR / fname
        try:
            if not path.exists():
                path.write_text(
                    f"# {it.title}\n\n- 来源: {it.source_name}\n- 链接: {it.source_url}\n"
                    f"- 分类: {it.source_category}/{it.region}\n\n---\n\n{body}\n",
                    encoding="utf-8")
        except Exception:
            path = COMPANY_DIR / f"doc_{it.sha256[:12]}.md"
            path.write_text(body, encoding="utf-8")

        chunks = _chunk(f"{it.title}\n{body}")
        try:
            n = vs.add_chunks(sha256=it.sha256, title=it.title, source_url=it.source_url,
                              lang=it.lang, region=it.region, chunks=chunks,
                              published_at=it.published_at.isoformat() if it.published_at else "")
            total_vecs += n
        except Exception as ex:
            log.warning("向量入库失败(%s): %s", type(ex).__name__, it.title[:30])
            n = 0
        db.insert_document(title=it.title, source_name=it.source_name, source_url=it.source_url,
                           lang=it.lang, region=it.region, layer="company", filepath=str(path),
                           sha256=it.sha256,
                           published_at=it.published_at.isoformat() if it.published_at else None)
    return total_vecs
