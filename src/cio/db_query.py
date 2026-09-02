"""资料库检索（个股情报档案用）：从 SQLite(documents/watchlist_hits) + LanceDB 里
把某标的的历史沉淀调出来，组装成"历史脉络时间线"。这是个股情报分析【资料库驱动】的核心。"""
from __future__ import annotations

from datetime import datetime, timezone

from . import db
from .models import TimelineEvent
from .utils import get_logger

log = get_logger("cio.dbq")


def _terms(subject: str, extra: list[str] | None = None) -> list[str]:
    ts = [subject] + (extra or [])
    return [t for t in dict.fromkeys(t.strip() for t in ts if t and len(str(t).strip()) >= 2)]


def query_documents(terms: list[str], limit: int = 80) -> list[dict]:
    """按标的相关词在 documents 表里检索（标题匹配），按时间倒序。返回存量库命中。"""
    if not terms:
        return []
    db.init_db()
    where = " OR ".join(["title LIKE ?"] * len(terms))
    params = [f"%{t}%" for t in terms] + [limit]
    sql = (f"SELECT title, source_name, source_url, region, layer, published_at, ingested_at "
           f"FROM documents WHERE {where} "
           f"ORDER BY COALESCE(published_at, ingested_at) DESC LIMIT ?")
    try:
        with db.connect() as con:
            return [dict(r) for r in con.execute(sql, params).fetchall()]
    except Exception as e:
        log.warning("documents 检索失败(%s)", type(e).__name__)
        return []


def query_hits(terms: list[str], limit: int = 40) -> list[dict]:
    """关注池命中回顾：watchlist_hits 里该标的/板块历史上触发过的信号。"""
    if not terms:
        return []
    db.init_db()
    where = " OR ".join(["(target LIKE ? OR sector LIKE ? OR fact LIKE ?)"] * len(terms))
    params: list = []
    for t in terms:
        params += [f"%{t}%", f"%{t}%", f"%{t}%"]
    params.append(limit)
    sql = (f"SELECT sector, target, signal, fact, source_url, hit_at "
           f"FROM watchlist_hits WHERE {where} ORDER BY hit_at DESC LIMIT ?")
    try:
        with db.connect() as con:
            return [dict(r) for r in con.execute(sql, params).fetchall()]
    except Exception as e:
        log.warning("watchlist_hits 检索失败(%s)", type(e).__name__)
        return []


def vector_context(subject: str, k: int = 8) -> list[dict]:
    """LanceDB 语义检索：即使标题没直接命中，也能捞出语义相关的历史沉淀。"""
    try:
        from .vectorstore import get_store
        return get_store().search(subject, k=k)
    except Exception as e:
        log.warning("向量检索失败(%s)", type(e).__name__)
        return []


def _date_of(doc: dict) -> str:
    raw = doc.get("published_at") or doc.get("ingested_at") or ""
    return str(raw)[:10] if raw else ""


def build_timeline(docs: list[dict], limit: int = 20) -> list[TimelineEvent]:
    """把存量库命中去重、按日期倒序，组装成历史脉络时间线。"""
    seen: set[str] = set()
    events: list[TimelineEvent] = []
    for d in docs:
        title = (d.get("title") or "").strip()
        key = title[:40]
        if not title or key in seen:
            continue
        seen.add(key)
        events.append(TimelineEvent(
            date=_date_of(d), title=title[:120],
            source_name=d.get("source_name") or "", source_url=d.get("source_url") or "",
            layer=d.get("layer") or "",
        ))
        if len(events) >= limit:
            break
    return events


def freshness(docs: list[dict], stale_days: int = 3) -> tuple[bool, int]:
    """评估存量库新鲜度。返回 (是否需要增量补齐, 距最新一条的天数)。
    存量薄(<8) 或 最新一条超过 stale_days 天 → 需要增量补齐。"""
    if len(docs) < 8:
        return True, 999
    latest = ""
    for d in docs:
        dt = _date_of(d)
        if dt > latest:
            latest = dt
    if not latest:
        return True, 999
    try:
        last = datetime.strptime(latest, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        days = (datetime.now(timezone.utc) - last).days
        return days > stale_days, days
    except Exception:
        return True, 999
