"""盘前早报编撰（CIO v2）。

- BLUF：只讲事件，【排除纯行情复述】——行情数字只出现在数据锚定（真值）。
- 数据锚定：8+A股指数（yfinance 真值）+ 北向资金（akshare）。
- 国际十大要闻：world 桶，按"跨源频次 + 源权威度 + 时效"排序（不限财经）。
- 中国财经：cn 桶 + 关注池。
- 相对信号分级、单主标签、源多样性上限、提示词回声/数字过滤。
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone

from . import leakage, process
from .classify import assign_signals
from .config import settings, watchlist
from .models import Brief, CollectionStatus, FundFlow, IndexQuote, NewsItem, Source, WatchlistHit
from .utils import get_logger, stamp_beijing, stamp_ny, truncate

log = get_logger("cio.brief")

# 纯大盘行情复述识别（这类不进 BLUF/趋势，行情归数据锚定）
_MKT_RECAP = re.compile(
    r"(沪指|深证|创业板|上证|北证|道指|道琼斯|标普|纳指|纳斯达克|恒生|日经|KOSPI|费城半导体|三大指数|大盘|股指)"
    r".{0,8}(收报|收盘|收涨|收跌|上涨|下跌|涨|跌|点|%|％)")


def _is_market_recap(n: NewsItem) -> bool:
    return bool(_MKT_RECAP.search(f"{n.title_original} {n.title_zh}"))


def _recency(n: NewsItem):
    return n.published_at or datetime(2000, 1, 1, tzinfo=timezone.utc)


# §事件时效闸：源发布超过 N 天 → 旧闻（不进页首 BLUF；事件卡上标注 stale，供 CEO 判断）。
STALE_DAYS = int(os.environ.get("CIO_STALE_DAYS", "7"))


def _is_stale(published_at, max_days: int = STALE_DAYS) -> bool:
    """源发布时间已知且早于 max_days → 旧闻。时间未知(None)一律不判旧（避免误杀无日期条目）。"""
    if not published_at:
        return False
    try:
        now = datetime.now(timezone.utc)
        dt = published_at if published_at.tzinfo else published_at.replace(tzinfo=timezone.utc)
        return (now - dt).days > max_days
    except Exception:
        return False


# §16 全球要闻的"市场传导路径"过滤：只保留对市场/关注池/宏观有可信传导路径的事件，
# 剔除体育、明星、学术八卦等无金融传导机制的一般新闻（船难、板球、抄袭之类）。
# §16 收紧：把"强传导词"（本身就有明确金融/经济机制）与"纯地缘词"（战事/选举/抗议…）分开。
# 强传导词 → 直接算投资相关；纯地缘词 → 必须同时出现市场/经济通道词才算，否则剔除
# （砍掉"乌克兰选举""也门战事"这类没有清晰金融传导路径的新闻——对齐 CEO：拿掉它 PM 会不会漏重要信息）。
_NEXUS_STRONG = re.compile(
    r"\b(oil|crude|opec|natural gas|energy price|gasoline|sanctions?|tariffs?|trade war|trade deal|"
    r"export controls?|central bank|federal reserve|the fed|fomc|interest rates?|rate cut|rate hike|"
    r"inflation|deflation|cpi|ppi|pce|payrolls|jobs report|unemployment|gdp|recession|stimulus|subsid\w+|"
    r"currenc\w+|forex|yuan|renminbi|yen|euro|dollar|treasur\w+|bond yield|10-year|sovereign|"
    r"supply chain|semiconductors?|chips?|dram|nand|hbm|foundry|bank\w*|default|bankrupt\w+|"
    r"ipo|mergers?|acquisitions?|buyout|earnings|guidance|revenue|profit|layoffs?|"
    r"government shutdown|debt ceiling|national debt|commodit\w+|gold price|copper|lithium|pipeline|"
    r"blockade|strait)\b", re.I)
# 纯地缘/政治词（本身无直接金融机制）
_GEO = re.compile(
    r"\b(war|warfare|conflict|coup|missile|nuclear|drones?|troops?|arm(?:y|ies)|milit(?:ary|ia)|"
    r"airstrikes?|ceasefire|invasion|border clash|protests?|riot|elections?|referendum|"
    r"assassinat\w+|terror\w+|hostages?|insurgen\w+)\b", re.I)
# 市场/经济通道词（地缘新闻要"落到"市场上，才算投资相关）
_MARKET_CO = re.compile(
    r"\b(markets?|stocks?|shares?|equit\w+|bonds?|yields?|prices?|oil|gas|energy|currenc\w+|dollar|euro|"
    r"yen|export|supply|econom\w+|gdp|inflation|investors?|central bank|the fed|rates?|commodit\w+|"
    r"chips?|semiconductors?|opec|sanction\w*|tariffs?)\b", re.I)

# 中文传导词（CJK 无 \b 词界，走子串）
_TRANSMISSION_CJK = ("股", "债市", "汇率", "油价", "通胀", "加息", "降息", "关税", "制裁", "财报",
                     "并购", "违约", "破产", "出口管制", "半导体", "芯片", "美联储", "央行", "国债", "大宗商品")

# §V 今日待观察 = 前瞻性催化（不复述过去的新闻）：只挑显式提到"将发生/已排期"的条目。
# 没有付费财经日历（红线：只用免费源），故从当日采集文本里识别明确的未来事件语言；
# 一条都识别不到时，如实标注"未检出"，绝不拿旧闻硬凑。
_FWD_EN = re.compile(
    r"\b(will report|is scheduled|scheduled (?:for|to)|expected (?:on|in|to report|to release|later)|"
    r"upcoming|next week|next month|due (?:on|in|next|out|to report)|slated (?:for|to)|to be held|"
    r"pdufa|fda (?:decision|action|advisory)|adcom|advisory committee|fomc|fed meeting|rate decision|"
    r"earnings (?:on|date|call|are due|next)|to announce|to release|to unveil|set to|"
    r"投产|plans to (?:report|release|announce)|investor day|analyst day|ex-dividend)\b", re.I)
_FWD_CJK = ("将于", "定于", "拟于", "预计将", "下周", "本周晚", "即将", "召开", "披露在即",
            "发布会", "财报日", "分红除权", "除权除息", "将公布", "将发布")


def _is_investment_relevant(n: NewsItem) -> bool:
    """有市场传导路径才算'投资相关'（§16 收紧）：
      ① 关注池命中；② 含强传导词（本身有金融/经济机制）；
      ③ 纯地缘/政治词 → 必须同时含市场/经济通道词才算，否则剔除；④ 中文传导词。"""
    if n.is_watchlist_hit:
        return True
    hay = f"{n.title_original} {n.title_zh} {n.summary_zh}"
    if _NEXUS_STRONG.search(hay):
        return True
    if _GEO.search(hay):
        return bool(_MARKET_CO.search(hay))   # 战事/选举/抗议：无市场通道 → 不算投资相关
    return any(k in hay for k in _TRANSMISSION_CJK)


def _detect_anomalies(anchor: list[IndexQuote], big_move: float = 3.0, diverge: float = 2.0) -> list[str]:
    """§7 客观异动检测（纯算术，零解读）：从锚定指数算出①单指数异常大幅波动 ②板块 vs 大盘背离。
    只陈述事实（'X 下跌 Y%'、'半导体与大盘背离 Zpp'），绝不说明其投资含义——那是一部/二部/CRO 的职权。"""
    import math
    from .config import market
    en = market().get("lang", "zh") == "en"

    def ok(q: IndexQuote) -> bool:
        return q.change_pct is not None and not (isinstance(q.change_pct, float) and math.isnan(q.change_pct))

    valid = [q for q in anchor if ok(q)]
    out: list[str] = []

    # ① 单指数异常大幅波动（|涨跌| ≥ big_move）
    for q in sorted(valid, key=lambda x: abs(x.change_pct), reverse=True):
        if abs(q.change_pct) >= big_move:
            out.append(f"{q.name} {q.change_pct:+.2f}% — outsized daily move (>{big_move:.0f}%)." if en
                       else f"{q.name} {q.change_pct:+.2f}% —— 单日异常大幅波动（>{big_move:.0f}%）。")

    # ② 半导体（费半/SOX）与大盘（标普500）背离（≥ diverge 个百分点）
    def find(*keys):
        for q in valid:
            nm = (q.name or "").lower()
            if any(k in nm for k in keys):
                return q
        return None
    sox = find("semi", "sox", "费半", "费城半导体")
    spx = find("s&p", "标普", "500")
    if sox and spx:
        d = sox.change_pct - spx.change_pct
        if abs(d) >= diverge:
            out.append(
                f"Semiconductors vs broad market: {sox.name} {sox.change_pct:+.2f}% vs {spx.name} "
                f"{spx.change_pct:+.2f}% ({d:+.2f}pp divergence)." if en
                else f"半导体与大盘背离：{sox.name} {sox.change_pct:+.2f}% vs {spx.name} "
                f"{spx.change_pct:+.2f}%（相差 {d:+.2f} 个百分点）。")

    return out[:5]


def _pick_bluf(items: list[NewsItem], n: int = 6) -> list[NewsItem]:
    """多取候选（默认6），hydrate 后再在摘要层二次筛掉行情复述，选出干净的前3。"""
    cand = [x for x in items if not x.is_noise and not _is_market_recap(x)]
    cand.sort(key=lambda x: (x.is_watchlist_hit, x.score), reverse=True)
    return cand[:n]


def _pick_trend(items: list[NewsItem], exclude: set[int], limit: int) -> list[NewsItem]:
    out = []
    for x in items:
        if id(x) in exclude:
            continue
        if (x.trend_tags or x.is_watchlist_hit) and not x.is_noise and not _is_market_recap(x):
            out.append(x)
        if len(out) >= limit:
            break
    return out


def _pick_world_top(items: list[NewsItem], limit: int = 10, per_source_cap: int = 2) -> list[NewsItem]:
    """全球投资相关要闻（§16：须有市场传导路径）：跨源频次 + 源权威度 + 时效。"""
    world = [x for x in items if x.region != "china" and not x.is_noise and _is_investment_relevant(x)]
    world.sort(key=lambda x: (len(x.sources), x.weight, _recency(x)), reverse=True)
    out, seen = [], {}
    for x in world:
        s = x.sources[0].name if x.sources else "?"
        if seen.get(s, 0) >= per_source_cap:
            continue
        seen[s] = seen.get(s, 0) + 1
        out.append(x)
        if len(out) >= limit:
            break
    return out


def _pick_china(items: list[NewsItem], limit: int = 8, per_source_cap: int = 3) -> list[NewsItem]:
    """中国财经/关注池：cn 桶按材料度排序。"""
    cn = [x for x in items if x.region == "china" and not x.is_noise]
    cn.sort(key=lambda x: x.score, reverse=True)
    out, seen = [], {}
    for x in cn:
        s = x.sources[0].name if x.sources else "?"
        if seen.get(s, 0) >= per_source_cap:
            continue
        seen[s] = seen.get(s, 0) + 1
        out.append(x)
        if len(out) >= limit:
            break
    return out


def _watchlist_hits(items: list[NewsItem], limit: int = 10) -> list[WatchlistHit]:
    hits = []
    for n in items:
        if not n.is_watchlist_hit or n.is_noise:
            continue
        src = n.sources[0] if n.sources else Source(name="", url="")
        hits.append(WatchlistHit(sector=n.watchlist_sector or "关注池",
                                 target=", ".join(n.tickers) or "—",
                                 fact=n.summary_zh or n.title_zh or n.title_original,
                                 signal=n.signal, source=src,
                                 confidence=n.source_confidence, materiality=n.materiality,
                                 relevance=n.watchlist_relevance, immediacy=n.immediacy))
        if len(hits) >= limit:
            break
    return hits


def build_premarket(items: list[NewsItem], anchor: list[IndexQuote],
                    fund_flows: list[FundFlow], status: CollectionStatus) -> Brief:
    items = sorted(items, key=lambda n: n.score, reverse=True)
    assign_signals(items)

    # CEO 动态指令：本期焦点。命中焦点关键词的条目加权上浮，并抽出置顶"焦点栏"。
    from . import focus as _focus
    fkws = _focus.focus_keywords()
    focus_label = _focus.active_label()
    focus_items: list[NewsItem] = []
    if fkws:
        fw = _focus.focus_weight()
        low_kws = [k.lower() for k in fkws]
        for n in items:
            hay = f"{n.title_original} {n.title_zh} {n.summary_zh} {n.body}"
            low = hay.lower()
            if any(k in hay for k in fkws) or any(k in low for k in low_kws):
                n.score += fw
                if not n.is_noise:
                    focus_items.append(n)
        items = sorted(items, key=lambda n: n.score, reverse=True)
        focus_items = focus_items[:6]

    world_top = _pick_world_top(items, 10)
    china = _pick_china(items, 8)

    # BLUF：多取候选 → gpt-oss 精修 → 摘要层二次筛（数字铁律兜底：指数点位/涨跌幅只归数据锚定）
    bluf_cand = _pick_bluf(items, 6)
    cand_ids = {id(n) for n in bluf_cand}
    process.hydrate(bluf_cand, model=settings.MODEL_BRIEF)
    bluf_items: list[NewsItem] = []
    for n in bluf_cand:
        if _is_stale(n.published_at):   # 页首 30 秒速读只放新鲜事件，旧闻不上 BLUF
            continue
        fact = n.summary_zh or n.title_zh or n.title_original
        # 摘要里若混进了大盘点位/涨跌幅（模型没守住数字铁律）→ 丢弃，行情只由数据锚定负责
        if _MKT_RECAP.search(fact or "") or len((fact or "").strip()) < 8:
            continue
        bluf_items.append(n)
        if len(bluf_items) >= 3:
            break
    bluf_ids = {id(n) for n in bluf_items}

    trend = _pick_trend(items, bluf_ids, settings.TREND_MAX)
    hits = _watchlist_hits(items)
    wl_items = [n for n in items if n.is_watchlist_hit and not n.is_noise][:40]

    # 其余条目用轻量模型（含焦点栏 + 关注池条目——供事件卡摘要；排除已重模型精修过的 BLUF 候选）
    rest = list({id(n): n for n in (world_top + china + trend + focus_items + wl_items)}.values())
    rest = [n for n in rest if id(n) not in cand_ids]
    process.hydrate(rest, model=settings.MODEL_LIGHT)

    # §4 关注池事件聚类：把讲同一件事的多篇报道去重成一条四分卡事件（如两条 Moderna → 一条）
    from . import cluster
    from .config import market as _mkt
    _diag: dict = {}
    all_events = cluster.cluster_events(wl_items, diag=_diag)
    watchlist_events = all_events[:12]
    _r, _e = len(wl_items), len(all_events)
    _m = max(0, _r - _e)   # 合并掉的篇数 = 报道数 − 事件数
    # 诊断：向量是否真跑起来 + 最接近合并阈值但没合并的余弦值 → 一眼看出 0 合并是阈值高还是向量没跑
    _emb = _diag.get("embed_ok")
    _cos = _diag.get("max_unmerged_cos")
    _tau = _diag.get("tau", 0.0)
    if _mkt().get("lang", "zh") == "en":
        cluster_stat = f"Clustering: {_r} watchlist reports → {_e} events ({_m} merged)"
        if _diag:
            cluster_stat += f" · embed {'OK' if _emb else 'off'}"
            if _cos is not None:
                cluster_stat += f" · closest unmerged cos {_cos:.2f}/tau {_tau:.2f}"
    else:
        cluster_stat = f"事件聚类：{_r} 篇关注池报道 → {_e} 个事件（合并 {_m} 篇）"
        if _diag:
            cluster_stat += f" · 向量{'已跑' if _emb else '未跑'}"
            if _cos is not None:
                cluster_stat += f" · 最近未合并余弦 {_cos:.2f}/阈 {_tau:.2f}"

    bluf_lines = []
    for n in bluf_items:
        tag = n.watchlist_sector or n.primary_tag or ""
        fact = n.summary_zh or n.title_zh or n.title_original
        bluf_lines.append((f"[{tag}] " if tag else "") + fact)

    from .config import market
    is_en = market().get("lang", "zh") == "en"
    # §V 今日待观察：只收"前瞻性催化"（显式未来事件语言），不再复述过去的公告/预期修正旧闻。
    # 关注池条目优先；识别不到就留空 → 渲染层如实显示"未检出"。
    watch_ahead = []
    seen_wa: set[str] = set()
    for n in sorted(items, key=lambda x: (x.is_watchlist_hit, x.score), reverse=True):
        if n.is_noise:
            continue
        hay = f"{n.title_original} {n.title_zh} {n.summary_zh}"
        if _FWD_EN.search(hay) or any(k in hay for k in _FWD_CJK):
            txt = truncate(n.summary_zh or n.title_en or n.title_zh or n.title_original, 60)
            key = txt[:28]
            if key in seen_wa:
                continue
            seen_wa.add(key)
            watch_ahead.append(txt)
        if len(watch_ahead) >= 4:
            break

    if is_en:
        decisions = [f"Watchlist hit ({h.sector}): {h.fact}  source: {h.source.url or h.source.name}"
                     for h in hits if h.signal == "强"][:5]
    else:
        decisions = [f"关注池命中（{h.sector}）：{h.fact} 来源：{h.source.url or h.source.name}"
                     for h in hits if h.signal == "强"][:5]

    anomalies = _detect_anomalies(anchor)
    # §21 只审计 CIO 自撰文本（BLUF/异动/待观察/待决断），不审转述的新闻标题
    leakage_flags = leakage.scan(bluf_lines + anomalies + watch_ahead + decisions)
    if leakage_flags:
        log.warning("§21 方向性泄漏审计命中 %d 处（供 CEO 复核）：%s", len(leakage_flags), " / ".join(leakage_flags))

    # §零幻觉数字核验：汇总所有展示条目里"原文之外的数字/年份"标记（hydrate 阶段已算好，供 CEO 复核）
    fact_flags: list[str] = []
    _seen_ff: set[int] = set()
    for n in (bluf_items + trend + world_top + china + wl_items):
        if id(n) in _seen_ff:
            continue
        _seen_ff.add(id(n))
        if getattr(n, "fact_suspect", None):
            head = (n.title_en or n.title_original or n.title_zh or "")[:36]
            fact_flags.append(f"{head}… [{', '.join(n.fact_suspect)}]")
    fact_flags = fact_flags[:6]
    if fact_flags:
        log.warning("§零幻觉数字核验命中 %d 处（供 CEO 复核）：%s", len(fact_flags), " / ".join(fact_flags))

    # 盘前市场快照（期货 + 宏观 + 海外收盘）。**整段失败也不能拖垮简报**——
    # 这里包一层 try：市场快照取不到时简报照常出，只是少一节并在状态里降级标注。
    ticks, mnote = [], ""
    try:
        from . import market_now as _mn
        ticks = _mn.snapshot()
        mnote = _mn.render_note(ticks)
        n_bad = sum(1 for t in ticks if t.stale)
        if n_bad:
            status.degraded.append(f"市场快照:{n_bad}项异常")
    except Exception as e:                       # noqa: BLE001
        log.warning("市场快照取不到（简报照常产出，本节标为降级）：%s", e)
        status.degraded.append(f"市场快照:{type(e).__name__}")

    return Brief(
        dt_beijing=stamp_beijing(), dt_ny=stamp_ny(),
        market_snapshot=ticks, market_note=mnote,
        bluf=bluf_lines, anchor=anchor, anomalies=anomalies, leakage_flags=leakage_flags,
        fact_flags=fact_flags,
        world_top=world_top, top_news_china=china,
        watchlist_hits=hits, watchlist_events=watchlist_events, cluster_stat=cluster_stat,
        trend_signals=trend,
        macro_policy=[n for n in items if n.primary_tag == "政策" and not n.is_noise][:5],
        fund_flows=fund_flows, watch_ahead=watch_ahead, decisions=decisions,
        focus_label=focus_label, focus_items=focus_items, status=status,
    )
