"""第一部分：信息采集。RSS / Google News / Yahoo 个股 / yfinance 行情 / EDGAR 公告。
全程降级容错：任一源失败只记状态、不拖垮整条流水线。"""
from __future__ import annotations

import math
import re
import urllib.parse
from datetime import datetime, timezone
from typing import Optional

import feedparser
import httpx

from .config import RAW_DIR, settings, sources
from .models import IndexQuote, RawItem
from .utils import clean_text, detect_lang, file_stamp, get_logger, now_beijing, safe_filename, sha256_text

log = get_logger("cio.collect")


def _parsed_dt(entry) -> Optional[datetime]:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            try:
                return datetime(*t[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    return None


def _mk_raw(*, title, url, body, source_name, category, region, weight) -> RawItem:
    title = clean_text(title)[:400]
    body = clean_text(body)
    lang = detect_lang(f"{title} {body[:200]}")
    return RawItem(
        source_name=source_name, source_category=category, region=region,
        source_url=url or "", title=title or "(无标题)", lang=lang,
        fetched_at=now_beijing(), body=body, weight=weight,
        sha256=sha256_text(title, url or body[:120]),
    )


def _fetch_feed(url: str, timeout: int) -> list:
    """用 httpx 取 feed 字节再交给 feedparser，便于统一超时与 UA。"""
    headers = {"User-Agent": "Mozilla/5.0 (CIO-Agent research)"}
    r = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
    r.raise_for_status()
    return feedparser.parse(r.content).entries


# 本次进程内连续失败的源。**批量扫描时同一个死源会被每只标的各撞一次**：
# 10 只标的 × 2 个死源 = 20 次超时 + 20 行告警，既拖慢扫描又淹没真正的信号。
# 连续失败 _SKIP_AFTER 次后本次运行不再重试。
#
# **跳过必须能被查出来，不能静默。** 一个悄悄消失的信息源，
# 表现形式恰好就是"今天这只票没有新材料"——和真的没有新闻长得一模一样。
# 所以 dead_feeds() 把跳过的源和原因暴露出来，扫描结束时一定打印。
_FEED_FAILS: dict = {}
_SKIP_AFTER = 2


def dead_feeds() -> dict:
    """本次运行被跳过的源 → (连续失败次数, 异常类型)。"""
    return {k: v for k, v in _FEED_FAILS.items() if v[0] >= _SKIP_AFTER}


def reset_feed_health() -> None:
    _FEED_FAILS.clear()


def fetch_rss(feed: dict, status: dict) -> list[RawItem]:
    items: list[RawItem] = []
    name = feed.get("name", "?")
    prev = _FEED_FAILS.get(name)
    if prev and prev[0] >= _SKIP_AFTER:
        status[name] = f"跳过（本次运行已连续失败 {prev[0]} 次：{prev[1]}）"
        return items
    try:
        # 配置读取也放进 try：配置缺失/损坏原本会直接抛出去，
        # 而它在语义上就是"这个源取不到"，应当和网络失败走同一条路径
        # ——被计数、被记录、被报告，而不是把整轮采集打断。
        cfg = sources()
        limit = cfg["limits"]["per_feed_items"]
        timeout = cfg["limits"]["http_timeout_seconds"]
        entries = _fetch_feed(feed["url"], timeout)
        for e in entries[:limit]:
            body = e.get("summary") or e.get("description") or ""
            it = _mk_raw(title=e.get("title", ""), url=e.get("link", ""), body=body,
                         source_name=feed["name"], category=feed.get("bucket") or feed.get("category", "rss"),
                         region=feed.get("region", "international"), weight=feed.get("weight", 2))
            it.published_at = _parsed_dt(e)
            items.append(it)
        status[name] = "ok" if items else "空"
        _FEED_FAILS.pop(name, None)          # 恢复了就清零，不留旧账
    except Exception as ex:
        kind = type(ex).__name__
        n = (_FEED_FAILS.get(name, (0, kind))[0]) + 1
        _FEED_FAILS[name] = (n, kind)
        status[name] = f"失败({kind})"
        if n <= _SKIP_AFTER:
            log.warning("RSS 失败 %s: %s%s", name, kind,
                        f"——连续第 {n} 次，本次运行后续跳过该源" if n >= _SKIP_AFTER else "")
    return items


def fetch_google_news(query: str, region: str, status: dict) -> list[RawItem]:
    cfg = sources()
    tmpl = cfg["google_news"]["template"]
    url = tmpl.format(q=urllib.parse.quote(query))
    name = f"GoogleNews:{query}"
    items: list[RawItem] = []
    try:
        entries = _fetch_feed(url, cfg["limits"]["http_timeout_seconds"])
        for e in entries[:cfg["limits"]["per_feed_items"]]:
            it = _mk_raw(title=e.get("title", ""), url=e.get("link", ""),
                         body=e.get("summary", ""), source_name=name,
                         category="google_news", region=region, weight=2)
            it.published_at = _parsed_dt(e)
            items.append(it)
        status[name] = "ok" if items else "空"
    except Exception as ex:
        status[name] = f"失败({type(ex).__name__})"
    return items


def fetch_yahoo_ticker(symbol: str, status: dict) -> list[RawItem]:
    cfg = sources()
    url = cfg["yahoo_ticker_rss"]["template"].format(symbol=symbol)
    name = f"Yahoo:{symbol}"
    items: list[RawItem] = []
    try:
        entries = _fetch_feed(url, cfg["limits"]["http_timeout_seconds"])
        for e in entries[:15]:
            it = _mk_raw(title=e.get("title", ""), url=e.get("link", ""),
                         body=e.get("summary", ""), source_name=name,
                         category="yahoo_ticker", region="international", weight=3)
            it.published_at = _parsed_dt(e)
            it.tickers = [symbol]
            items.append(it)
        status[name] = "ok" if items else "空"
    except Exception as ex:
        status[name] = f"失败({type(ex).__name__})"
    return items


def scan_rss_for_subject(keywords: list[str], status: dict, limit: int = 40) -> list[RawItem]:
    """扫描配置的三类 RSS（国际/海外看中国/中国本土），保留与主题相关的条目。
    用于给专题报告接上权威源 + 交叉对照，避免只依赖 Google News/自媒体。

    **这里刻意取全量桶（不按市场过滤）。** 盘前简报按市场收桶是对的——
    美股早报里不该混进 A 股外资流入。但专题报告是 CEO 点名要的：
    她问一个中国主题，系统却静默摘掉了中文源，是同一类缺陷，只是方向相反。
    """
    cfg = sources(all_buckets=True)
    kws = [k for k in keywords if k and len(str(k)) >= 2]
    low = [str(k).lower() for k in kws]
    out: list[RawItem] = []
    for feed in cfg["rss"]:
        try:
            items = fetch_rss(feed, status)
        except Exception:
            continue
        for it in items:
            text = f"{it.title} {it.body}"
            tl = text.lower()
            if any(k in text for k in kws) or any(k in tl for k in low):
                out.append(it)
                if len(out) >= limit:
                    return out
    return out


def enrich_fulltext(items: list[RawItem], top_n: int) -> None:
    """对排序靠前的 N 条抓全文正文（trafilatura），失败保留原摘要。"""
    try:
        import trafilatura
    except Exception:
        return
    for it in items[:top_n]:
        if not it.source_url or len(it.body) > 600:
            continue
        try:
            downloaded = trafilatura.fetch_url(it.source_url)
            if downloaded:
                txt = trafilatura.extract(downloaded, include_comments=False, include_tables=False)
                if txt and len(txt) > len(it.body):
                    it.body = clean_text(txt)[:6000]
        except Exception:
            continue


def enrich_news_fulltext(news: list, top_n: int) -> int:
    """给已去重的 NewsItem 补正文。返回真正补上的条数。

    和 `enrich_fulltext` 抓的是同一件事，区别只在**取 URL 的方式**：
    `RawItem` 有 `.source_url`，`NewsItem` 把来源放在 `.sources[0].url`。

    为什么需要在去重之后再补一次：实质度判定看的是「标题 + 正文」，
    而标题常常只是"某公司本周受关注"，正文头几百字里才有已发生的动作
    和硬锚点。原来的 `enrich_fulltext(raws, top_n=10)` 跑在**去重和相关性
    清洗之前**，那 10 个名额有相当一部分花在了后面会被丢掉的条目上——
    等到真正要判定的时候，手里只剩标题。
    """
    try:
        import trafilatura
    except Exception:                                    # noqa: BLE001
        return 0
    n_ok = 0
    for it in (news or [])[:top_n]:
        url = ""
        srcs = getattr(it, "sources", None) or []
        if srcs:
            url = getattr(srcs[0], "url", "") or ""
        if not url or len(getattr(it, "body", "") or "") > 600:
            continue
        try:
            downloaded = trafilatura.fetch_url(url)
            if not downloaded:
                continue
            txt = trafilatura.extract(downloaded, include_comments=False,
                                      include_tables=False)
            if txt and len(txt) > len(it.body or ""):
                it.body = clean_text(txt)[:6000]
                n_ok += 1
        except Exception:                                # noqa: BLE001
            continue
    return n_ok


def _finite(x) -> bool:
    """有效数值：非 None、非 NaN、非 0（指数收盘不会是 0，0 视为脏数据）。"""
    try:
        f = float(x)
        return not math.isnan(f) and f != 0.0
    except Exception:
        return False


def _pick_completed(hist, now=None) -> list[float]:
    """从 yfinance 日线里取"已收盘交易日"的收盘序列，剔除当前盘中未完成的实时K线。
    盘前早报要的是"上一个完整收盘"，不是正在交易的日经/韩股半小时实时价。
    用每个指数自带的交易所时区判断：本地日期为今天且尚未收盘（<16:00）→ 该根为实时价，剔除。"""
    try:
        import pandas as pd
        idx = list(hist.index)
        vals = list(hist["Close"])
        tz = getattr(hist.index, "tz", None)
        if tz is None:
            return [float(c) for c in vals if _finite(c)]
        now_ex = now if now is not None else pd.Timestamp.now(tz=tz)
        today = now_ex.date()
        trading_now = now_ex.hour < 16          # 保守：交易所本地16点前，当日K线可能仍在盘中
        closes: list[float] = []
        for ts, c in zip(idx, vals):
            if not _finite(c):
                continue
            if trading_now and ts.date() == today:
                continue                        # 当日实时未完成K线 → 剔除
            closes.append(float(c))
        return closes
    except Exception:
        try:
            return [float(c) for c in hist["Close"].tolist() if _finite(c)]
        except Exception:
            return []


def _akshare_index_daily(sym: str):
    """A股指数 yfinance 缺数时用 akshare 兜底。按日期升序取最新两根收盘（防接口默认降序取错），
    返回 (last, change_pct) 或 None。"""
    code = {"000001.SS": "sh000001", "000300.SS": "sh000300"}.get(sym)
    if not code:
        return None
    try:
        import akshare as ak
    except Exception:
        return None
    for fn_name in ("stock_zh_index_daily_em", "stock_zh_index_daily"):
        fn = getattr(ak, fn_name, None)
        if not callable(fn):
            continue
        try:
            df = fn(symbol=code)
            ccol = next((c for c in ("close", "收盘", "收盘价") if c in df.columns), None)
            dcol = next((c for c in ("date", "日期", "trade_date") if c in df.columns), None)
            if not ccol:
                continue
            if dcol:
                df = df.sort_values(by=dcol)     # 升序 → 最后一行=最新交易日
            closes = [float(v) for v in df[ccol].tolist() if _finite(v)]
            if len(closes) >= 2 and closes[-2]:
                return closes[-1], (closes[-1] - closes[-2]) / closes[-2] * 100
        except Exception:
            continue
    return None


def _akshare_hk_index(sym: str):
    """恒生科技等港股指数 yfinance 取不到（HSTECH.HK/^HSTECH 在 Yahoo 都无历史）时用 akshare 港股指数兜底。
    返回 (last, change_pct) 或 None。收盘后快照=当日收盘价+涨跌幅。"""
    name_key = {"HSTECH.HK": "恒生科技", "^HSTECH": "恒生科技", "^HSI": "恒生指数"}.get(sym)
    if not name_key:
        return None
    try:
        import akshare as ak
    except Exception:
        return None
    fn = getattr(ak, "stock_hk_index_spot_em", None)
    if callable(fn):
        try:
            df = fn()
            ncol = next((c for c in df.columns if "名称" in str(c)), None)
            pcol = next((c for c in df.columns if "最新价" in str(c)), df.columns[3] if len(df.columns) > 3 else None)
            chcol = next((c for c in df.columns if "涨跌幅" in str(c)), None)
            if ncol and pcol is not None:
                m = df[df[ncol].astype(str).str.contains(name_key)]
                if len(m):
                    row = m.iloc[0]
                    if _finite(row[pcol]):
                        chg = float(row[chcol]) if chcol and _finite(row[chcol]) else None
                        return float(row[pcol]), chg
        except Exception:
            pass
    return None


def fetch_index_quotes(symbols: dict, status: dict, group: str = "") -> list[IndexQuote]:
    """取指数"上一完整收盘"与涨跌幅（权威真值，数据锚定用）。yfinance 为主、A股/港股缺数时 akshare 兜底。
    - 剔除盘中实时K线（避免日经/韩股开盘半小时的实时价被当成收盘）。
    - 离谱涨跌幅（>±25%）视为脏数据剔除。
    - 绝不写 NaN：取不到就标注'今日未取到'，渲染层显示占位符。"""
    out: list[IndexQuote] = []
    from .config import market as _mkt
    _en = _mkt().get("lang", "zh") == "en"
    _na = "n/a today" if _en else "今日未取到"          # §5 语言：US 模式下占位符也用英文
    _noyf = "yfinance not installed" if _en else "yfinance 未安装"
    try:
        import yfinance as yf
    except Exception:
        status["yfinance"] = "未安装"
        return [IndexQuote(name=n, symbol=s, note=_noyf, group=group) for n, s in symbols.items()]
    ok = 0
    for name, sym in symbols.items():
        last = pct = None
        try:
            hist = yf.Ticker(sym).history(period="10d")
            closes = _pick_completed(hist)
            if len(closes) >= 2 and closes[-2]:
                last = closes[-1]
                pct = (last - closes[-2]) / closes[-2] * 100
        except Exception as ex:
            status.setdefault("yfinance_err", type(ex).__name__)
        # A股指数（.SS）yfinance 常返回 NaN → akshare 兜底（自带官方涨跌幅口径）
        if last is None and sym.endswith(".SS"):
            fb = _akshare_index_daily(sym)
            if fb:
                last, pct = fb
                status["akshare_index"] = "ok"
        # 港股指数（恒生科技 HSTECH.HK）yfinance 无历史 → akshare 港股指数兜底
        if last is None and (sym.endswith(".HK") or sym == "^HSTECH"):
            fb = _akshare_hk_index(sym)
            if fb:
                last, pct = fb
                status["akshare_hk"] = "ok"
        # 脏数据护栏：主要指数单日不可能涨跌超 25%
        if last is not None and pct is not None and abs(pct) > 25:
            last = pct = None
        if last is not None:
            out.append(IndexQuote(name=name, symbol=sym, last=round(last, 2),
                                  change_pct=round(pct, 2) if pct is not None else None, group=group))
            ok += 1
        else:
            out.append(IndexQuote(name=name, symbol=sym, note=_na, group=group))
    status["yfinance"] = "ok" if ok else "降级"
    return out


EDGAR_BODY_N = 5
"""取多少份公告的正文。SEC 限速 10 req/s，这里是个位数请求，很宽裕。"""


def _edgar_body(url: str) -> str:
    """抓一份公告的正文。**必须带 SEC 要求的 User-Agent**，否则 403。

    这也是为什么不能交给 `trafilatura.fetch_url` 去抓：它用自己的 UA，
    sec.gov 会直接拒绝——而拒绝的表现是"这份公告没有正文"，
    和"这份公告确实没内容"长得一模一样。
    """
    try:
        r = httpx.get(url, headers={"User-Agent": settings.SEC_USER_AGENT},
                      timeout=20, follow_redirects=True)
        r.raise_for_status()
        html = r.text[:400_000]
    except Exception as e:                                   # noqa: BLE001
        log.info("EDGAR 正文取不到 %s：%s", url[:70], type(e).__name__)
        return ""
    try:
        import trafilatura
        txt = trafilatura.extract(html, include_comments=False,
                                  include_tables=False)
        if txt:
            return clean_text(txt)[:6000]
    except Exception:                                        # noqa: BLE001
        pass
    txt = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html,
                 flags=re.S | re.I)
    txt = re.sub(r"<[^>]+>", " ", txt)
    return clean_text(txt)[:6000]


def fetch_edgar_recent(cik: str, status: dict, with_body: bool = True,
                       within_days: int = 0) -> list[RawItem]:
    """SEC EDGAR 最近公告。cik 为 10 位数字字符串。

    ## `within_days` —— 不加这个的话，闸门等于被拆了

    这个接口返回的是"最近 N 份公告"，**和它们什么时候提交的无关**。
    一家公司只要历史上提交过 8 份文件，就永远能取到 8 份。

    对个股档案（dossier / topic）这是对的：你要的是这家公司的近况全貌。
    **但对证券一部是致命的**——一部的闸门问的是"今天有没有增量事实"，
    而一份六周前的 10-Q 不是今天的新闻。

    真机上的表现：接入 EDGAR 后 10 只票**全部**变成 SUFFICIENT，
    实质材料从 4% 跳到 57%。看起来像大成功，实际是
    **闸门从此每天对每只票都说"材料充分"**——
    evidence-triggered 的研究退化成了每日评论台，
    而这正是这套闸门当初要防的东西。**没有一处报错。**

    所以调用方要按用途选：
        dossier / topic  → `within_days=0`（不筛，要全貌）
        证券一部          → `within_days=7`（只要"新发生的"）

    `with_body=True` 时会去抓前几份公告的**正文**。这一步不是锦上添花：

    公告条目的标题就是 `NVIDIA CORP 8-K (2026-08-28)` —— 一个表单号加日期。
    只有它的话，材料闸门即使认定"一手披露"，辩论手里拿到的也是一条
    **什么都没说的存根**。那会造出闸门开了、论据却是空的这种局面，
    比闸门不开更糟——报告会声称有基本面依据，而它没有。
    所以正文取不到时，闸门会把这条降为「背景」（见 material_gate.tier_of）。
    """
    import time
    cfg = sources()
    url = cfg["edgar"]["submissions_api"].format(cik=str(cik).zfill(10))
    items: list[RawItem] = []
    try:
        r = httpx.get(url, headers={"User-Agent": settings.SEC_USER_AGENT}, timeout=20)
        r.raise_for_status()
        data = r.json()
        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accns = recent.get("accessionNumber", [])
        docs = recent.get("primaryDocument", [])
        name = data.get("name", cik)
        cutoff = ""
        if within_days and within_days > 0:
            from datetime import timedelta
            cutoff = (now_beijing().date() - timedelta(days=int(within_days))).isoformat()
        n_seen = n_old = 0
        for i in range(min(cfg["edgar"]["recent_limit"], len(forms))):
            n_seen += 1
            fdate = str(dates[i])[:10] if i < len(dates) else ""
            if cutoff and fdate and fdate < cutoff:
                n_old += 1
                continue                 # 太旧 —— 不是"今天的增量事实"
            accn = accns[i].replace("-", "") if i < len(accns) else ""
            doc = docs[i] if i < len(docs) else ""
            link = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn}/{doc}" if accn else url
            body = f"SEC filing {forms[i]} filed {dates[i]}."
            if with_body and accn and doc and i < EDGAR_BODY_N:
                full = _edgar_body(link)
                if full:
                    body = f"{body}\n{full}"
                time.sleep(0.12)             # SEC 限速 10 req/s，留足余量
            it = _mk_raw(title=f"{name} {forms[i]} ({dates[i]})", url=link,
                         body=body,
                         source_name="EDGAR", category="edgar", region="international", weight=3)
            items.append(it)
        n_body = sum(1 for it in items if len(it.body) > 300)
        # **筛掉几份要报出来。** 不报的话，"这家公司这周没动静"和
        # "我们把它的公告都筛掉了"在日志里长得一样。
        win = f"，{within_days} 天内 {len(items)} 份（滤掉 {n_old} 份更早的）" if cutoff else ""
        status["EDGAR"] = (f"ok（查到 {n_seen} 份{win}，其中 {n_body} 份取到正文）"
                           if n_seen else "空")
    except Exception as ex:
        status["EDGAR"] = f"失败({type(ex).__name__})"
    return items


def save_raw(items: list[RawItem]) -> None:
    """原样归档到 raw-data/（标题+YYYY-MM-DD-HHMM.md，含来源可追溯）。"""
    stamp = file_stamp()
    for it in items:
        try:
            fname = f"{safe_filename(it.title)}+{stamp}.md"
            path = RAW_DIR / fname
            if path.exists():
                continue
            path.write_text(
                f"# {it.title}\n\n"
                f"- 来源: {it.source_name}\n- 链接: {it.source_url}\n"
                f"- 分类: {it.source_category} / {it.region}\n- 语言: {it.lang}\n"
                f"- 采集: {it.fetched_at.isoformat()}\n\n---\n\n{it.body}\n",
                encoding="utf-8",
            )
            it.raw_path = str(path)
        except Exception:
            continue


def collect_premarket(status_unstructured: dict, status_structured: dict):
    """盘前采集：world/cn 两桶 RSS + Google News 世界头条 + 数据锚定指数。返回 (items, anchor)。"""
    from .config import watchlist as _wl
    cfg = sources()
    wl = _wl()
    items: list[RawItem] = []

    # **过滤掉了什么，必须印出来。** 看不见的过滤和没有过滤长得一模一样：
    # 少了六个源之后，"今天中国没新闻"和"今天没抓中国"在报告上没有任何区别。
    bf = cfg.get("_bucket_filter") or {}
    if bf.get("dropped_rss") or bf.get("dropped_queries"):
        status_unstructured["源过滤"] = (
            f"{bf.get('market')} 模式只收 {'+'.join(bf.get('kept', []))} 桶；"
            f"跳过 {len(bf.get('dropped_rss', []))} 个 RSS "
            f"（{'、'.join(bf.get('dropped_rss', [])[:3])}…）"
            f"与 {len(bf.get('dropped_queries', []))} 条关键词")

    # RSS 两桶（world + cn）
    for feed in cfg["rss"]:
        items += fetch_rss(feed, status_unstructured)
    # Google News 分区头条（world：世界/财经/科技，Google 已按重要性排序）
    for sf in cfg["google_news"].get("section_feeds", []):
        items += fetch_rss(sf, status_unstructured)
    # Google News 关键词（cn）
    for sq in cfg["google_news"].get("standing_queries", []):
        items += fetch_google_news(sq["q"], sq.get("region", "international"), status_unstructured)

    # 数据锚定：8+A股指数真值（按分组）
    anchor: list[IndexQuote] = []
    for group, syms in (wl.get("data_anchor") or {}).items():
        if isinstance(syms, dict) and syms:
            anchor += fetch_index_quotes(syms, status_structured, group=group)
    return items, anchor
