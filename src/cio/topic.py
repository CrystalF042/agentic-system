"""专题报告（CEO 指令触发）：个股 + 主题。
- 自然语言解析标的/主题（斜杠命令备用）
- 方向性问题(能不能买/看多看空/目标价) → 礼貌拒答并转指 CRO/证券部，但仍给事实
- 飞轮：查历史资产 → 增量采集 → 交叉验证 → 编撰(中英对照,只报事实) → 归档
- 趋势视角与早报一致（资金面/政策/预期修正/异动/公告优先）
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import httpx

from . import collect, db, process
from .config import TOPIC_DIR, market, settings, watchlist
from .models import CollectionStatus, FundFlow, NewsItem, Source, TopicReport
from .render import render_report_md, render_report_pdf
from .utils import file_stamp, get_logger, safe_filename, stamp_beijing, stamp_ny

log = get_logger("cio.topic")

# 常见美股中文名 → ticker（可扩充）
US_NAME_MAP = {
    "苹果": "AAPL", "英伟达": "NVDA", "特斯拉": "TSLA", "微软": "MSFT", "谷歌": "GOOGL",
    "亚马逊": "AMZN", "台积电": "TSM", "Meta": "META", "脸书": "META", "奈飞": "NFLX",
    "博通": "AVGO", "AMD": "AMD", "礼来": "LLY", "默沙东": "MRK", "辉瑞": "PFE",
}

# 常见美股英文名 → ticker（大小写不敏感）。覆盖美股关注池四赛道核心公司 + 主要大盘股，
# 让 CEO 直接输 "Nvidia"/"Eli Lilly" 也能解析出个股（否则会被当成主题 → 无行情锚定）。
US_EN_NAME_MAP = {
    # AI / 半导体
    "nvidia": "NVDA", "amd": "AMD", "broadcom": "AVGO", "tsmc": "TSM",
    "taiwan semiconductor": "TSM", "asml": "ASML", "micron": "MU", "intel": "INTC",
    "qualcomm": "QCOM", "arm": "ARM", "marvell": "MRVL", "applied materials": "AMAT",
    "lam research": "LRCX", "kla": "KLAC", "palantir": "PLTR",
    # 大型科技
    "apple": "AAPL", "microsoft": "MSFT", "amazon": "AMZN", "alphabet": "GOOGL",
    "google": "GOOGL", "meta": "META", "facebook": "META", "netflix": "NFLX",
    "tesla": "TSLA", "oracle": "ORCL", "salesforce": "CRM", "adobe": "ADBE",
    # 医药
    "eli lilly": "LLY", "lilly": "LLY", "pfizer": "PFE", "merck": "MRK",
    "abbvie": "ABBV", "johnson & johnson": "JNJ", "amgen": "AMGN", "gilead": "GILD",
    "bristol myers": "BMY", "novo nordisk": "NVO", "vertex": "VRTX",
    "regeneron": "REGN", "moderna": "MRNA",
}


def _lang() -> str:
    """当前市场语言：us→en，cn→zh。用于报表本地化，默认 zh 不影响 A 股。"""
    try:
        return market().get("lang", "zh")
    except Exception:
        return "zh"


def _ticker_from_name(name: str) -> str | None:
    """英文公司名 → ticker：先查内置映射，再用本地缓存的 SEC company_tickers.json 精确核名兜底。
    SEC 源免费、权威、离线可复用；仅做"去公司后缀后的精确匹配"，避免 Apple→Apple Hospitality 这类误配。"""
    tl = (name or "").lower()
    for k in sorted(US_EN_NAME_MAP, key=len, reverse=True):   # 长名优先，避免子串误命中
        if re.search(rf"(?<![a-z]){re.escape(k)}(?![a-z])", tl):
            return US_EN_NAME_MAP[k]
    # SEC 兜底（长尾美股，如 Costco/Walmart 等未列入映射者）
    try:
        _get_cik("AAPL")   # 触发缓存文件下载（若尚不存在）
        if not _CIK_CACHE.exists():
            return None
        q = re.sub(r"[^a-z0-9 ]", " ", tl)
        q = re.sub(r"\s+", " ", q).strip()
        if len(q) < 3:
            return None
        _SUF = re.compile(r"\b(corp|corporation|inc|incorporated|ltd|limited|plc|co|"
                          r"company|holdings|holding|group|the|sa|ag|nv)\b")
        data = json.loads(_CIK_CACHE.read_text(encoding="utf-8"))
        for row in data.values():
            title = re.sub(r"[^a-z0-9 ]", " ", str(row.get("title", "")).lower())
            core = re.sub(r"\s+", " ", _SUF.sub(" ", title)).strip()
            if core and core == q:
                tk = str(row.get("ticker", "")).upper()
                return tk or None
    except Exception as e:
        log.warning("SEC 核名失败(%s)", type(e).__name__)
    return None

# 主题 → 英文检索词（给专题接上国际权威源覆盖）
THEME_EN = {
    "创新药": "China innovative drugs biotech",
    "新药": "China innovative drug new drug biotech pipeline",
    "香港": "Hong Kong market Hang Seng economy",
    "香港形势": "Hong Kong market Hang Seng economy politics",
    "半导体": "China semiconductor chips export control",
    "人工智能": "China artificial intelligence AI",
    "算力": "China AI computing power datacenter",
    "光模块": "optical module transceiver AI",
    "机器人": "China humanoid robot",
    "氢能源": "China hydrogen energy",
    "卫星": "China satellite internet",
    "云计算": "China cloud computing",
    "银行": "China state-owned banks",
    "医药": "China pharma healthcare",
    "医保": "China medical insurance drug pricing",
}


def _cap_sources(items: list, cap: int = 3) -> list:
    """同一来源占比上限，防单一自媒体刷屏、逼出源多样性。"""
    seen: dict = {}
    out = []
    for n in items:
        s = n.sources[0].name if n.sources else "?"
        if seen.get(s, 0) >= cap:
            continue
        seen[s] = seen.get(s, 0) + 1
        out.append(n)
    return out


_DIRECTIONAL = re.compile(
    r"(能不能买|该不该买|要不要买|买入|卖出|加仓|减仓|抄底|逃顶|看多|看空|看涨|看跌|"
    r"目标价|会涨|会跌|涨不涨|跌不跌|值不值得|建议|推荐|前景如何|能到多少|布局)")

REFUSAL = ("（@CIO）方向性判断——能不能买、看多看空、目标价、买卖时点——属于 CRO 与证券部"
           "（CFO 两条线）的职权，我不越权表态。下面只给你关于该标的的客观事实、数据与来源，"
           "供你和 CRO/证券部判断参考。")

REFUSAL_EN = ("(@CIO) Directional calls — whether to buy, bull/bear stance, price targets, entry/exit timing "
              "— are the remit of the CRO and the securities desks, not mine. Below are only the objective "
              "facts, data and sources on this name, for you and the CRO/desks to judge.")


def is_directional(text: str) -> bool:
    return bool(_DIRECTIONAL.search(text or ""))


# ---------------- 标的解析 ----------------

def _theme_keywords() -> list[str]:
    kws: list[str] = []
    for sec in watchlist().get("watchlist", {}).values():
        kws += (sec.get("keywords") or [])
    return kws


def parse_subject(text: str) -> dict:
    """返回 {type: stock/theme, resolved, symbol, a_share, cik, queries[]}。"""
    t = text.strip()
    t = re.sub(r"^/(topic|report|专题|dossier|情报|档案)\s*", "", t)  # 去斜杠命令前缀
    # 去掉指令动词，只留真正的标的/主题（否则会拿"分析新药"当查询词 → 搜不到东西）
    t = re.sub(r"^\s*(帮我|请|麻烦)?\s*"
               r"(分析|研究|专题|情况报告|了解一下|了解|看一下|看看|盯一下|盯|梳理|盘点|"
               r"深度|深挖|调研|情报|档案|查一下|查)\s*", "", t).strip() or text.strip()
    t = re.sub(r"(最近的?|近期的?)?\s*(动向|走势|情况|形势|形式|局势|怎么样|如何|新闻|资讯)\s*$", "", t).strip() or t

    # A 股 6 位代码
    m = re.search(r"\b(\d{6})\b", t)
    if m:
        code = m.group(1)
        sym = f"{code}.SS" if code.startswith("6") else f"{code}.SZ"
        return {"type": "stock", "resolved": code, "symbol": sym, "a_share": True,
                "cik": None, "queries": [code]}

    # 关注池中文名（六大行等）
    for sec in watchlist().get("watchlist", {}).values():
        for name, code in zip(sec.get("names_cn") or [], sec.get("a_shares") or []):
            if name and name in t:
                sym = f"{code}.SS" if code.startswith("6") else f"{code}.SZ"
                return {"type": "stock", "resolved": name, "symbol": sym, "a_share": True,
                        "cik": None, "queries": [name, code]}

    # 美股中文名
    for name, tk in US_NAME_MAP.items():
        if name.lower() in t.lower():
            return {"type": "stock", "resolved": f"{name}({tk})", "symbol": tk, "a_share": False,
                    "cik": None, "queries": [name, tk]}

    # 美股英文名（Nvidia / Eli Lilly / Costco…；映射 + SEC 精确核名）
    en_tk = _ticker_from_name(t)
    if en_tk:
        disp = t.strip()
        return {"type": "stock", "resolved": f"{disp}({en_tk})" if disp.upper() != en_tk else en_tk,
                "symbol": en_tk, "a_share": False, "cik": None, "queries": [disp, en_tk]}

    # 裸大写 ticker（AAPL 等）
    m = re.search(r"\b([A-Z]{2,5})\b", t)
    if m and m.group(1) not in {"CEO", "CIO", "CRO", "CFO", "ETF", "GDP", "FDA", "SEC", "IPO", "PDF"}:
        tk = m.group(1)
        return {"type": "stock", "resolved": tk, "symbol": tk, "a_share": False,
                "cik": None, "queries": [tk]}

    # 主题
    for kw in _theme_keywords():
        if kw and kw in t:
            return {"type": "theme", "resolved": kw, "symbol": None, "a_share": None,
                    "cik": None, "queries": [t, kw]}

    # 兜底：整句当主题
    return {"type": "theme", "resolved": t[:20], "symbol": None, "a_share": None,
            "cik": None, "queries": [t]}


_CIK_CACHE = TOPIC_DIR.parent / ".sec_tickers.json"


def _get_cik(ticker: str) -> str | None:
    try:
        if not _CIK_CACHE.exists():
            r = httpx.get("https://www.sec.gov/files/company_tickers.json",
                          headers={"User-Agent": settings.SEC_USER_AGENT}, timeout=20)
            r.raise_for_status()
            _CIK_CACHE.write_text(r.text, encoding="utf-8")
        data = json.loads(_CIK_CACHE.read_text(encoding="utf-8"))
        for row in data.values():
            if str(row.get("ticker", "")).upper() == ticker.upper():
                return str(row.get("cik_str", "")).zfill(10)
    except Exception as e:
        log.warning("CIK 查询失败(%s)", type(e).__name__)
    return None


def _quote_facts(sym: str, status: dict) -> list[str]:
    facts: list[str] = []
    try:
        import yfinance as yf
        h = yf.Ticker(sym).history(period="1mo")
        if len(h) >= 2:
            last = float(h["Close"].iloc[-1]); prev = float(h["Close"].iloc[-2])
            hi = float(h["High"].max()); lo = float(h["Low"].min())
            chg = (last - prev) / prev * 100 if prev else 0
            first = float(h["Close"].iloc[0])
            mchg = (last - first) / first * 100 if first else 0
            vol = int(h["Volume"].iloc[-1])
            cur = market().get("currency", "")
            if _lang() == "en":
                facts.append(f"Last close {cur}{last:,.2f}, {chg:+.2f}% vs prior day; "
                             f"1-month range {cur}{lo:,.2f}–{cur}{hi:,.2f}, {mchg:+.2f}% over the month; "
                             f"latest volume {vol:,}. (source: yfinance)")
            else:
                facts.append(f"最新收盘 {cur}{last:.2f}，较前一交易日 {chg:+.2f}%；近一月区间 {lo:.2f}–{hi:.2f}，"
                             f"月内累计 {mchg:+.2f}%；最近成交量 {vol:,}。（来源：yfinance）")
            status["yfinance"] = "ok"
        else:
            status["yfinance"] = "insufficient data" if _lang() == "en" else "数据不足"
    except Exception as e:
        status["yfinance"] = f"failed({type(e).__name__})" if _lang() == "en" else f"失败({type(e).__name__})"
    return facts


def _categorize(items: list[NewsItem]) -> dict:
    """把新闻按趋势标签分入 关键消息/研报预期/公告/政策。"""
    key, est, fil, pol = [], [], [], []
    for n in items:
        if "预期修正" in n.trend_tags:
            est.append(n)
        elif "公告" in n.trend_tags:
            fil.append(n)
        elif "政策" in n.trend_tags:
            pol.append(n)
        else:
            key.append(n)
    return {"key": key[:8], "est": est[:5], "fil": fil[:6], "pol": pol[:5]}


def build_topic_report(text: str) -> TopicReport:
    info = parse_subject(text)
    status_u: dict = {}
    status_s: dict = {}
    raws = []

    # 1) 增量采集：Google News（中文角度）
    region = "china" if info.get("a_share") else "international"
    for q in info["queries"]:
        raws += collect.fetch_google_news(q, region, status_u)
    # 英文检索角度（拿国际权威源覆盖）
    en = THEME_EN.get(info["resolved"]) or (
        info["symbol"] if info.get("symbol") and not info.get("a_share") else "")
    if en:
        raws += collect.fetch_google_news(en, "international", status_u)
    # 权威源交叉对照：扫描配置 RSS（BBC/SCMP/日经/财新/新华/东财/CNBC 等）保留相关条目
    kw_list = list(dict.fromkeys([k for k in (info["queries"] + [info["resolved"], en]) if k]))
    raws += collect.scan_rss_for_subject(kw_list, status_u)
    # 个股：行情 + 公告
    if info["type"] == "stock" and not info.get("a_share") and info.get("symbol"):
        raws += collect.fetch_yahoo_ticker(info["symbol"], status_u)
        cik = _get_cik(info["symbol"])
        if cik:
            raws += collect.fetch_edgar_recent(cik, status_u)

    quote_facts = _quote_facts(info["symbol"], status_s) if info.get("symbol") else []

    # 2) 存档原始 + 入库（飞轮沉淀）
    collect.save_raw(raws)
    fetched = len(raws)
    collect.enrich_fulltext(raws, top_n=20)
    news, deduped = process.dedupe_and_score(raws)
    news = sorted(news, key=lambda n: n.score, reverse=True)
    from .classify import assign_signals
    assign_signals(news)          # 相对排序：强/中/弱有区分，不再全"弱"
    vecs = process.ingest_to_archive(raws)

    # 3) 查历史资产（命中数用于"活档案"提示）
    try:
        from .vectorstore import get_store
        hist = get_store().search(info["resolved"], k=6)
    except Exception:
        hist = []

    # 4) 编撰：源多样性上限（防单一自媒体刷屏）+ 只 hydrate 将展示的
    show = _cap_sources(news, cap=3)[:20]
    process.hydrate(show)
    cat = _categorize(show)

    # 摘要（确定性拼装 + 事实）
    top_facts = "；".join((n.summary_zh or n.title_zh or n.title_original)[:60] for n in show[:3]) or "暂无显著增量。"
    summary = (f"围绕「{info['resolved']}」采集 {fetched} 条、去重后 {len(news)} 条，命中历史资产 {len(hist)} 条。"
               f"要点：{top_facts}")
    if len(news) < 5:
        summary = (f"【数据提示】「{info['resolved']}」在公开免费新闻源上可获取的信息很少（去重后仅 {len(news)} 条），"
                   f"本报告覆盖有限。若为冷门产品或细分市场，深度行业/市场研究需要付费数据库，免费源难以支撑；"
                   f"建议换一个更主流的标的或角度。\n\n") + summary
    if is_directional(text):
        summary = REFUSAL + "\n\n" + summary

    status = CollectionStatus(structured=status_s, unstructured=status_u,
                              fetched=fetched, deduped=deduped, ingested_vectors=vecs,
                              degraded=[f"{k}:{v}" for k, v in {**status_s, **status_u}.items()
                                        if v not in ("ok",)])

    r = TopicReport(
        subject=text, subject_type=info["type"], resolved=info["resolved"],
        title=f"《{info['resolved']} 专题情况报告》",
        dt_beijing=stamp_beijing(), dt_ny=stamp_ny(),
        summary=summary, quote_facts=quote_facts,
        fund_facts=[], key_news=cat["key"], filings=cat["fil"],
        estimate_revisions=cat["est"], policy=cat["pol"],
        decisions=[], status=status, archived_from=len(hist),
    )
    return r


def archive_and_render(r: TopicReport) -> tuple[str, str]:
    """写 md + pdf 到 Topic Archive，返回 (md_path, pdf_path)。"""
    stamp = file_stamp()
    base = f"{safe_filename(r.resolved)}专题情况报告+{stamp}"
    md_path = TOPIC_DIR / f"{base}.md"
    pdf_path = TOPIC_DIR / f"{base}.pdf"
    md_path.write_text(render_report_md(r), encoding="utf-8")
    try:
        render_report_pdf(r, str(pdf_path))
    except Exception as e:
        log.error("专题 PDF 渲染失败: %s", e)
        pdf_path = None
    db.init_db()
    db.insert_brief("topic", r.title, str(md_path), str(pdf_path or ""))
    return str(md_path), str(pdf_path or "")
