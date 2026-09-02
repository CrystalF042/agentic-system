"""个股情报分析【资料库驱动】。CEO 一句话 → 先吃资料库存量（历史脉络）→ 缺口才增量补齐
→ 数据锚定真值 → 交叉验证 → 编撰《个股情报档案》→ 归档喂飞轮。只报事实，无方向判断。

与 topic.py（专题报告，增量优先）的区别：本模块【资料库优先】，突出历史脉络与数据完备度，
库越厚这份档案越有价值——这是本地私有库的护城河。"""
from __future__ import annotations

from . import collect, db, db_query, process, topic
from .classify import assign_signals
from .config import TOPIC_DIR
from .models import CollectionStatus, DossierReport
from .render import render_dossier_md, render_dossier_pdf
from .utils import file_stamp, get_logger, safe_filename, stamp_beijing, stamp_ny

log = get_logger("cio.dossier")


def build_dossier(text: str) -> DossierReport:
    info = topic.parse_subject(text)
    is_en = topic._lang() == "en"
    status_u: dict = {}
    status_s: dict = {}
    terms = db_query._terms(info["resolved"], info.get("queries"))

    # 1) 资料库检索（存量优先，飞轮变现）
    docs = db_query.query_documents(terms)
    hits = db_query.query_hits(terms)
    vecs = db_query.vector_context(info["resolved"])
    timeline = db_query.build_timeline(docs)
    archive_docs = len(docs)
    need_fresh, gap_days = db_query.freshness(docs)

    # 2) 近期增量：个股档案【每次都轻采一轮】，保证"三、近期增量"总有当期带摘要内容，
    #    不因存量新鲜就跳过（否则再跑一次会空）。need_fresh 仅用于"数据完备度"措辞。
    raws: list = []
    region = "china" if info.get("a_share") else "international"
    for q in (info.get("queries") or [])[:2]:
        raws += collect.fetch_google_news(q, region, status_u)
    en_q = topic.THEME_EN.get(info["resolved"]) or (
        info["symbol"] if info.get("symbol") and not info.get("a_share") else "")
    if en_q:
        raws += collect.fetch_google_news(en_q, "international", status_u)
    raws += collect.scan_rss_for_subject(terms, status_u, limit=20)
    if info["type"] == "stock" and not info.get("a_share") and info.get("symbol"):
        raws += collect.fetch_yahoo_ticker(info["symbol"], status_u)
        cik = topic._get_cik(info["symbol"])
        if cik:
            raws += collect.fetch_edgar_recent(cik, status_u)
    try:
        collect.save_raw(raws)
        collect.enrich_fulltext(raws, top_n=15)
        process.ingest_to_archive(raws)     # 增量入库，喂飞轮（去重，不会重复膨胀）
    except Exception as e:
        log.warning("增量补齐异常(%s)", type(e).__name__)

    news, _ = process.dedupe_and_score(raws)
    news = sorted(news, key=lambda n: n.score, reverse=True)
    assign_signals(news)
    show = topic._cap_sources(news, cap=3)[:10]
    process.hydrate(show)
    fresh_docs = len(news)

    # 3) 数据锚定（行情真值，绝不让模型编数字）
    quote_facts = topic._quote_facts(info["symbol"], status_s) if info.get("symbol") else []

    # 4) 编撰各栏
    recent = show[:8]
    past_hits = [
        f"{(h.get('hit_at') or '')[:10]}｜[{h.get('sector','')}] {h.get('target','')}·{h.get('signal','')}："
        f"{(h.get('fact') or '')[:60]}" for h in hits[:8]
    ]
    _fil = "Filings" if is_en else "公告"
    filings = [n for n in show if n.primary_tag == _fil or _fil in (n.trend_tags or [])][:6]

    if is_en:
        completeness = (
            f"This dossier draws on {archive_docs} archived docs, {len(hits)} past signals, "
            f"{len(vecs)} semantically-related passages; {fresh_docs} recent items pulled this run.")
        if archive_docs == 0 and fresh_docs == 0:
            completeness = ("[Data note] Neither the archive nor the free sources hold anything on this name yet "
                            "— it may be an obscure ticker, or not yet in the daily collection scope. "
                            "Add it to the watchlist so the flywheel starts accruing coverage.")
    else:
        completeness = (
            f"本档案命中存量库 {archive_docs} 篇、历史信号 {len(hits)} 次、语义相关 {len(vecs)} 段；"
            f"本次采集近期增量 {fresh_docs} 条。")
        if archive_docs == 0 and fresh_docs == 0:
            completeness = ("【数据提示】资料库与免费源目前都没有该标的的沉淀——可能是冷门标的，"
                            "或该名称尚未进入日常采集范围。建议先将其纳入关注池，让飞轮开始为它沉淀资产。")

    cross: list[str] = []
    src_names = {n.sources[0].name for n in show if n.sources}
    if len(src_names) >= 2:
        cross.append(
            f"Cross-checked across {len(src_names)} sources; verify any discrepancy against each source link."
            if is_en else
            f"本档案交叉了 {len(src_names)} 个来源；本土源与海外源的差异请对照各条来源链接核验。")
    if topic.is_directional(text):
        cross.insert(0, topic.REFUSAL_EN if is_en else topic.REFUSAL)

    status = CollectionStatus(
        structured=status_s, unstructured=status_u,
        fetched=len(raws), deduped=0, ingested_vectors=0,
        degraded=[f"{k}:{v}" for k, v in {**status_s, **status_u}.items() if v not in ("ok",)])

    return DossierReport(
        subject=text, subject_type=info["type"], resolved=info["resolved"],
        title=(f"{info['resolved']} — Stock Intelligence Dossier" if is_en
               else f"《{info['resolved']} 个股情报档案》"),
        dt_beijing=stamp_beijing(), dt_ny=stamp_ny(),
        quote_facts=quote_facts, timeline=timeline, recent=recent,
        past_hits=past_hits, filings=filings, cross_check=cross,
        completeness=completeness, decisions=[],
        status=status, archive_docs=archive_docs, fresh_docs=fresh_docs,
    )


def archive_and_render(r: DossierReport) -> tuple[str, str]:
    """写 md + pdf 到 Topic Archive（永久沉淀，成未来语料），返回 (md_path, pdf_path)。"""
    stamp = file_stamp()
    base = f"{safe_filename(r.resolved)}个股情报档案+{stamp}"
    md_path = TOPIC_DIR / f"{base}.md"
    pdf_path = TOPIC_DIR / f"{base}.pdf"
    md_path.write_text(render_dossier_md(r), encoding="utf-8")
    try:
        render_dossier_pdf(r, str(pdf_path))
    except Exception as e:
        log.error("个股档案 PDF 渲染失败: %s", e)
        pdf_path = None
    db.init_db()
    db.insert_brief("dossier", r.title, str(md_path), str(pdf_path or ""))
    return str(md_path), str(pdf_path or "")
