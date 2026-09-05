"""证券二部数据层：沪深300 成分 + 日线行情 + 关注池行业打标 + 磁盘缓存。

红线：全部走【免费本地可得】数据源（akshare 主，机器在国内正常联网即可），
零付费数据、零云端依赖。本模块只取【客观行情事实】，不含任何观点/LLM。

数据可达性说明：akshare 的行情/成分接口是国内可达的公共源；本工程运行在 CEO 的
Mac（本地、国内 IP），akshare 正常。若 akshare 某接口暂不可达，成分退到内置核心
名单、行情按标的逐个降级（诚实标注覆盖度，绝不静默造数）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import timedelta

from .config import MARKET, RAW_DIR, market, watchlist
from .utils import get_logger, now_beijing

log = get_logger("cio.quant_data")

CACHE_DIR = RAW_DIR / "quant_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
SNAP_DIR = RAW_DIR / "quant_snapshots"          # §Data Contract：universe last-known-good 快照
SNAP_DIR.mkdir(parents=True, exist_ok=True)
_MIN_LEN_OK = 250                                # 判 complete 的最少交易日（12月动量需回看约250）


# ---------------- 关注池行业打标（沪深300 内做加权倾斜用）----------------
# 银行：直接取 watchlist 里的六大行 a_shares；创新药/硬科技赛道成分动态，内置核心权重股映射。
_INNOVDRUG = {  # 创新药 / CXO / 医疗器械龙头（沪深300 内核心）
    "600276": "恒瑞医药", "603259": "药明康德", "300760": "迈瑞医疗", "300759": "康龙化成",
    "300122": "智飞生物", "600196": "复星医药", "000661": "长春高新", "300015": "爱尔眼科",
    "600436": "片仔癀", "000538": "云南白药", "002821": "凯莱英", "603127": "昭衍新药",
}
_HARDTECH = {  # 硬科技：半导体/AI/算力/光模块/先进制造（沪深300 内核心）
    "688981": "中芯国际", "002415": "海康威视", "000725": "京东方A", "002475": "立讯精密",
    "601012": "隆基绿能", "300750": "宁德时代", "002230": "科大讯飞", "300124": "汇川技术",
    "603501": "韦尔股份", "002371": "北方华创", "300308": "中际旭创", "688111": "金山办公",
    "688012": "中微公司", "002049": "紫光国微",
}


def _bank_codes() -> dict:
    wl = watchlist().get("watchlist", {})
    names = wl.get("银行", {}).get("names_cn", []) or []
    codes = wl.get("银行", {}).get("a_shares", []) or []
    m = {str(c): (names[i] if i < len(names) else "") for i, c in enumerate(codes)}
    # 补充沪深300 内其它权重银行
    m.update({"600036": "招商银行", "601166": "兴业银行", "600000": "浦发银行",
              "600016": "民生银行", "601818": "光大银行", "601998": "中信银行"})
    return m


def sector_map() -> dict:
    """代码 → 关注池行业（银行/创新药/硬科技）；不在池内的返回空。"""
    m = {}
    for code in _bank_codes():
        m[code] = "银行"
    for code in _INNOVDRUG:
        m[code] = "创新药"
    for code in _HARDTECH:
        m[code] = "硬科技"
    return m


# ---------------- 沪深300 成分（akshare 主 + 内置核心名单兜底）----------------
# 兜底名单：大盘蓝筹核心权重股（含六大行 + 关注池赛道龙头 + 各行业压舱石）。
# 诚实说明：这是 akshare 不可达时的【子集兜底】，非全量 300 只；机器国内联网时用 akshare 全量成分。
def _fallback_universe() -> list[tuple[str, str]]:
    core = [
        ("600519", "贵州茅台"), ("000858", "五粮液"), ("601318", "中国平安"),
        ("600900", "长江电力"), ("601899", "紫金矿业"), ("600030", "中信证券"),
        ("000333", "美的集团"), ("000651", "格力电器"), ("600887", "伊利股份"),
        ("002594", "比亚迪"), ("600585", "海螺水泥"), ("601088", "中国神华"),
        ("600028", "中国石化"), ("601857", "中国石油"), ("600048", "保利发展"),
        ("601390", "中国中铁"), ("600031", "三一重工"), ("000002", "万科A"),
    ]
    banks = list(_bank_codes().items())
    drug = list(_INNOVDRUG.items())
    tech = list(_HARDTECH.items())
    seen, out = set(), []
    for code, name in banks + drug + tech + core:
        if code not in seen:
            seen.add(code)
            out.append((code, name))
    return out


@dataclass
class Stock:
    code: str            # A股 6 位代码 / 美股 ticker
    name: str = ""
    sector: str = ""     # 兼容旧字段：cn=关注池行业；us=focus 主题串（展示用）
    yahoo: str = ""      # 行情取数标的（.SS/.SZ 或美股 ticker）
    # §Data Contract：GICS（官方行业，集中度用）与 focus_theme（CEO 主题，tilt 用）分开
    gics_sector: str = ""
    gics_subindustry: str = ""
    focus_theme: list = field(default_factory=list)
    preferred_source: str = ""      # 该股本次回测窗口用的单一源（yfinance/stooq/akshare）
    source_status: str = ""         # complete / partial / miss
    # §公司行为：合并/分拆/更名后价格连续但经济主体断裂 → 本轮不参与选股（如实计数）
    cik: str = ""                   # SEC CIK（基本面取数用；来自成分表）
    identity_flag: str = ""         # corp-action:merger / identity:new / identity:renamed / ""
    identity_effective: str = ""    # 断点生效日


def _yahoo(code: str) -> str:
    """行情取数标的。us：ticker 即标的（不加后缀）；cn：沪.SS / 深.SZ。"""
    if MARKET == "us":
        return code
    return f"{code}.SS" if code and code[0] == "6" else f"{code}.SZ"


# ---------------- 美股：S&P 500 universe + focus theme（§Data Contract）----------------
def _us_theme_map() -> dict:
    """ticker → [focus themes]（来自 watchlist_us.companies；一个票可多主题，如 NVDA=[AI,Semiconductors]）。"""
    m: dict[str, list] = {}
    for sector, sec in (watchlist().get("watchlist", {}) or {}).items():
        comp = sec.get("companies") or {}
        if isinstance(comp, dict):
            for _name, tk in comp.items():
                tk = str(tk or "").strip().upper()
                if tk:
                    m.setdefault(tk, [])
                    if sector not in m[tk]:
                        m[tk].append(sector)
    return m


def _us_fallback_universe() -> list[tuple[str, str]]:
    """冷启动兜底（仅 smoke/TEST，绝不作正式结果）：关注池四赛道龙头 + 各板块压舱石。"""
    core = [
        ("AAPL", "Apple"), ("MSFT", "Microsoft"), ("AMZN", "Amazon"), ("GOOGL", "Alphabet"),
        ("META", "Meta"), ("NVDA", "Nvidia"), ("AVGO", "Broadcom"), ("AMD", "AMD"),
        ("TSM", "TSMC"), ("MU", "Micron"), ("INTC", "Intel"), ("QCOM", "Qualcomm"),
        ("LLY", "Eli Lilly"), ("MRK", "Merck"), ("PFE", "Pfizer"), ("ABBV", "AbbVie"),
        ("JNJ", "Johnson & Johnson"), ("AMGN", "Amgen"), ("MRNA", "Moderna"), ("VRTX", "Vertex"),
        ("ORCL", "Oracle"), ("CRM", "Salesforce"), ("ADBE", "Adobe"), ("NFLX", "Netflix"),
        ("PLTR", "Palantir"), ("ASML", "ASML"), ("AMAT", "Applied Materials"), ("LRCX", "Lam Research"),
        ("JPM", "JPMorgan"), ("V", "Visa"), ("XOM", "Exxon Mobil"), ("WMT", "Walmart"),
    ]
    seen, out = set(), []
    for tk, name in core:
        if tk not in seen:
            seen.add(tk)
            out.append((tk, name))
    return out


def _us_snapshot_save(rows: list[dict]) -> str:
    """存 last-known-good 快照 sp500_YYYY-MM-DD.csv（effective_date/ticker/company/gics_sector/gics_subindustry）。
    返回快照名。同日覆盖。"""
    import csv
    from .config import market_date
    # **用市场时区的日期，不用机器本地/北京时区。**
    # 纽约 18:28 收盘后跑，北京已是次日 —— 用北京日期会把一份描述 8/24 收盘的报告
    # 标成 sp500_8/25，看起来成分来自一个还没发生的交易日。
    day = market_date()
    name = f"sp500_{day}"
    p = SNAP_DIR / f"{name}.csv"
    try:
        with open(p, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["effective_date", "ticker", "company",
                                              "gics_sector", "gics_subindustry", "cik"])
            w.writeheader()
            for r in rows:
                w.writerow({"effective_date": day, **{k: r.get(k, "") for k in
                            ("ticker", "company", "gics_sector", "gics_subindustry", "cik")}})
    except Exception as e:
        log.warning("universe 快照写入失败：%s", e)
    return name


def _us_snapshot_load_latest():
    """读最近一次成功保存的 S&P 500 快照 → (rows, snapshot_name)；无快照返回 (None, '')。"""
    try:
        import csv
        snaps = sorted(SNAP_DIR.glob("sp500_*.csv"))
        if not snaps:
            return None, ""
        p = snaps[-1]
        with open(p, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        return (rows, p.stem) if rows else (None, "")
    except Exception:
        return None, ""


# 维基要求可识别的 User-Agent（默认 urllib UA 会被 403）。两个源都是免费公开数据，零付费。
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) CIO-Agent/1.0 (local quant research)"


def _http_get(url: str, timeout: int = 25) -> "bytes | None":
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "text/html,text/csv,*/*"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception as e:
        log.warning("取数失败 %s：%s", url.split("/")[2], e)
        return None


def _norm_rows(df, c_sym, c_sec, c_gics, c_sub) -> list:
    rows = []
    for _, r in df.iterrows():
        tk = str(r[c_sym]).strip().upper().replace(".", "-")   # BRK.B → BRK-B（yfinance 口径）
        if not tk or tk == "NAN":
            continue
        cik = ""
        for _ck in ("cik", "CIK", "Cik"):
            if _ck in r.index if hasattr(r, "index") else False:
                try:
                    cik = str(int(float(r[_ck])))
                except Exception:
                    cik = ""
                break
        rows.append({"ticker": tk,
                     "company": str(r[c_sec]) if c_sec else "",
                     "gics_sector": str(r[c_gics]) if c_gics else "",
                     "gics_subindustry": str(r[c_sub]) if c_sub else "",
                     "cik": cik})
    return rows


def _us_from_wikipedia() -> "list[dict] | None":
    """维基百科 S&P 500 成分表（带合规 UA，避免 403）。"""
    raw = _http_get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
    if not raw:
        return None
    try:
        import io
        import pandas as pd
        for df in pd.read_html(io.BytesIO(raw)):
            cols = {str(c).strip().lower(): c for c in df.columns}
            c_sym = cols.get("symbol") or cols.get("ticker")
            c_sec = cols.get("security") or cols.get("company")
            if not c_sym or not c_sec:
                continue                      # 页面上还有别的表，认字段挑出成分表
            rows = _norm_rows(df, c_sym, c_sec, cols.get("gics sector"),
                              cols.get("gics sub-industry") or cols.get("gics sub industry"))
            if len(rows) >= 400:              # 成分表应有约 500 行
                return rows
        return None
    except Exception as e:
        log.warning("维基成分表解析失败：%s", e)
        return None


def _us_from_datahub() -> "list[dict] | None":
    """免费备源（GitHub datasets/s-and-p-500-companies），字段与维基一致：Symbol/Security/GICS。"""
    raw = _http_get("https://raw.githubusercontent.com/datasets/s-and-p-500-companies/master/data/constituents.csv")
    if not raw:
        return None
    try:
        import io
        import pandas as pd
        df = pd.read_csv(io.BytesIO(raw))
        cols = {str(c).strip().lower(): c for c in df.columns}
        c_sym = cols.get("symbol")
        if not c_sym:
            return None
        rows = _norm_rows(df, c_sym, cols.get("security"), cols.get("gics sector"),
                          cols.get("gics sub-industry"))
        return rows if len(rows) >= 400 else None
    except Exception as e:
        log.warning("备源成分表解析失败：%s", e)
        return None


def _us_universe_fetch() -> tuple:
    """成分抓取链：维基(最新) → 免费 CSV 备源。返回 (rows, source) 或 (None, '')。"""
    rows = _us_from_wikipedia()
    if rows:
        return rows, "wikipedia"
    rows = _us_from_datahub()
    if rows:
        return rows, "datahub-csv"
    return None, ""


IDENTITY_PATH = SNAP_DIR / "identity_registry.csv"


def _load_corp_actions() -> tuple[dict, int]:
    """读 config/corporate_actions.yaml → ({ticker: {...}}, lookback_days)。缺文件返回空表。"""
    try:
        import yaml
        from .config import CONFIG_DIR
        p = CONFIG_DIR / "corporate_actions.yaml"
        if not p.exists():
            return {}, 365
        cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        acts = {str(k).upper(): (v or {}) for k, v in (cfg.get("actions") or {}).items()}
        return acts, int(cfg.get("lookback_days", 365))
    except Exception as e:
        log.warning("公司行为例外表读取失败：%s", e)
        return {}, 365


def _update_identity_registry(rows: list) -> dict:
    """身份登记簿：记录每个 ticker 首次出现的日期与公司名，用【快照差分】发现身份断点。

    首次运行时全部标 seeded（我们只是刚开始跟踪，不代表它们是新股）；
    此后新出现的 ticker 标 new、同 ticker 换名标 renamed —— 这两类才是真正的身份变更。
    返回 {ticker: {"first_seen":.., "status":.., "prev_name":..}}。
    """
    import csv
    from .config import market_date
    today = market_date()          # 身份登记是业务凭证，日期走市场时区
    prev: dict = {}
    seeded_before = IDENTITY_PATH.exists()
    if seeded_before:
        try:
            with open(IDENTITY_PATH, encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    prev[str(r.get("ticker", "")).upper()] = r
        except Exception:
            prev = {}
    out: dict = {}
    for r in rows:
        tk = str(r.get("ticker", "")).upper()
        name = str(r.get("company", ""))
        old = prev.get(tk)
        if old is None:
            status = "new" if seeded_before else "seeded"      # 首轮全部 seeded，避免整池误判为新
            out[tk] = {"first_seen": today, "status": status, "prev_name": ""}
        else:
            old_name = str(old.get("name", ""))
            renamed = bool(old_name and name and old_name.strip() != name.strip())
            out[tk] = {"first_seen": old.get("first_seen", today),
                       "status": "renamed" if renamed else old.get("status", "seeded"),
                       "prev_name": old_name if renamed else ""}
            if renamed:
                out[tk]["first_seen"] = today                  # 换名=新身份，重置身份起算日
    try:
        with open(IDENTITY_PATH, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["ticker", "first_seen", "status", "name", "prev_name"])
            w.writeheader()
            for r in rows:
                tk = str(r.get("ticker", "")).upper()
                w.writerow({"ticker": tk, "first_seen": out[tk]["first_seen"], "status": out[tk]["status"],
                            "name": r.get("company", ""), "prev_name": out[tk]["prev_name"]})
    except Exception as e:
        log.warning("身份登记簿写入失败：%s", e)
    return out


def _identity_flags(rows: list) -> dict:
    """合并【已知公司行为表】与【身份登记簿】→ {ticker: (flag, effective_date)}。
    flag 非空 = 该标的的价量历史可能描述前身主体，本轮不参与选股（如实计数、不删数据）。"""
    from datetime import date
    acts, lookback = _load_corp_actions()
    reg = _update_identity_registry(rows)
    from .config import market_date
    from datetime import datetime as _dt
    today = _dt.strptime(market_date(), "%Y-%m-%d").date()      # 同上，走市场时区
    flags: dict = {}
    for r in rows:
        tk = str(r.get("ticker", "")).upper()
        a = acts.get(tk)
        if a and a.get("effective"):
            try:
                y, m, d = (int(x) for x in str(a["effective"]).split("-"))
                age = (today - date(y, m, d)).days
                if age < lookback:
                    flags[tk] = (f"corp-action:{a.get('type', 'change')}", str(a["effective"]))
                    continue
            except Exception:
                pass
        st = (reg.get(tk) or {}).get("status", "")
        if st in ("new", "renamed"):
            fs = (reg.get(tk) or {}).get("first_seen", "")
            try:
                y, m, d = (int(x) for x in fs.split("-"))
                age = (today - date(y, m, d)).days
            except Exception:
                age = 0
            if age < lookback:
                flags[tk] = (f"identity:{st}", fs)
    return flags


def _us_get_universe(limit: int) -> tuple[list, str, str, bool]:
    """§Data Contract universe 语义：wiki 成功→存快照；失败→用最近快照；从无快照→兜底且 DEGRADED/TEST。
    返回 (stocks, src, snapshot_name, universe_pit)。"""
    theme = _us_theme_map()
    rows, fetch_src = _us_universe_fetch()
    snap_name, degraded = "", False
    if rows:
        snap_name = _us_snapshot_save(rows)
        src = fetch_src
    else:
        rows, snap_name = _us_snapshot_load_latest()
        if rows:
            src = f"snapshot:{snap_name}"
        else:
            rows = [{"ticker": tk, "company": nm, "gics_sector": "", "gics_subindustry": ""}
                    for tk, nm in _us_fallback_universe()]
            src = "fallback-DEGRADED/TEST"
            degraded = True
    flags = _identity_flags(rows)          # 公司行为 / 身份断点（快照差分 + 已知事件表）
    stocks = []
    for r in rows:
        tk = str(r.get("ticker", "")).upper()
        fl, eff = flags.get(tk, ("", ""))
        stocks.append(Stock(code=tk, name=r.get("company", ""), yahoo=tk,
                            gics_sector=r.get("gics_sector", ""), gics_subindustry=r.get("gics_subindustry", ""),
                            focus_theme=theme.get(tk, []),
                            sector=", ".join(theme.get(tk, [])) or "",   # sector=focus 主题串（展示/tilt 兼容）
                            cik=str(r.get("cik", "") or ""),
                            identity_flag=fl, identity_effective=eff))
    if flags:
        log.info("公司行为/身份断点：%d 只本轮不参与选股 —— %s",
                 len(flags), ", ".join(f"{k}({v[0]})" for k, v in list(flags.items())[:6]))
    if limit and limit > 0:
        inpool = [s for s in stocks if s.focus_theme]
        rest = [s for s in stocks if not s.focus_theme]
        stocks = (inpool + rest)[:max(limit, len(inpool))]
    log.info("S&P 500 universe：%d 只（源=%s，关注池主题命中=%d）",
             len(stocks), src, sum(1 for s in stocks if s.focus_theme))
    # universe_pit：当前成分回测历史暂为 False；wiki/snapshot 都是"当前"成分。累积快照后由回测区间判定升级。
    return stocks, src, snap_name, False


def snapshot_coverage() -> tuple[str, str, int]:
    """已有的成分快照覆盖了哪一段。返回 (最早, 最晚, 份数)。

    这是 `universe_pit` 能不能为真的**唯一依据**：快照就是我们对
    "那一天成分表长什么样"的全部记录。
    """
    days = sorted(p.stem.replace("sp500_", "") for p in SNAP_DIR.glob("sp500_*.csv"))
    return (days[0] if days else "", days[-1] if days else "", len(days))


def universe_pit_for(start: str, end: str) -> tuple[bool, str]:
    """**这段区间的成分是不是 point-in-time 的。** 返回 (是否, 说明)。

    原来只有一个全局的 `universe_pit = False`，而真相是**它取决于区间**：
    快照覆盖到的那几天是 PIT 的，之前的不是。写死成 False 会让人以为
    这件事永远做不了；写死成 True 则是撒谎。所以按区间判。

    这个函数不改 `_LAST_UNIVERSE_META` 里那个全局值——那个仍然是保守的
    False，因为大多数调用方问的是"整段历史"，而整段历史确实不是 PIT。
    """
    lo, hi, n = snapshot_coverage()
    if not n:
        return False, "一份成分快照都没有 —— 成分表只有「今天」这一个版本"
    s, e = str(start)[:10], str(end)[:10]
    if lo <= s and e <= hi:
        return True, f"区间 {s}…{e} 落在快照覆盖内（{n} 份，{lo}…{hi}）"
    return False, (f"区间 {s}…{e} 超出快照覆盖（{n} 份，{lo}…{hi}）"
                   f"—— 超出的那段用的是「今天」的成分，带幸存者偏差")


# 最近一次 get_universe 的元信息（供 build_unit_b 写 manifest；不改 get_universe 签名，避免波及其它调用方）
_LAST_UNIVERSE_META: dict = {"snapshot": "", "universe_pit": False, "degraded": False}


def get_universe(limit: int = 0) -> tuple[list[Stock], str]:
    """取成分股并打标。us=S&P 500（wiki + last-known-good 快照 + 兜底 DEGRADED）；cn=沪深300（akshare + 兜底）。
    返回 (股票列表, 数据源状态)。US 的快照/PIT 元信息落在 _LAST_UNIVERSE_META。"""
    if MARKET == "us":
        stocks, src, snap, upit = _us_get_universe(limit)
        _LAST_UNIVERSE_META.update({"snapshot": snap, "universe_pit": upit,
                                    "degraded": src.startswith("fallback")})
        return stocks, src
    _LAST_UNIVERSE_META.update({"snapshot": "", "universe_pit": False, "degraded": False})
    smap = sector_map()
    pairs: list[tuple[str, str]] = []
    src = "akshare:csindex"
    try:
        import akshare as ak
        df = None
        for fn_name in ("index_stock_cons_csindex", "index_stock_cons"):
            fn = getattr(ak, fn_name, None)
            if not callable(fn):
                continue
            try:
                df = fn(symbol="000300")
                src = f"akshare:{fn_name}"
                break
            except Exception:
                continue
        if df is not None and len(df):
            ccol = next((c for c in df.columns if str(c) in ("成分券代码", "品种代码", "证券代码", "成份券代码", "con_code", "股票代码")), None)
            ncol = next((c for c in df.columns if str(c) in ("成分券名称", "品种名称", "证券名称", "成份券名称", "股票名称")), None)
            if ccol:
                for _, row in df.iterrows():
                    code = str(row[ccol]).zfill(6)
                    name = str(row[ncol]) if ncol else ""
                    pairs.append((code, name))
    except Exception as e:
        log.warning("akshare 成分获取失败，用内置兜底名单：%s", e)

    if not pairs:
        pairs = _fallback_universe()
        src = "fallback:核心名单(子集)"

    stocks = []
    for code, name in pairs:
        stocks.append(Stock(code=code, name=name, sector=smap.get(code, ""), yahoo=_yahoo(code)))
    if limit and limit > 0:
        # 优先保留关注池标的 + 前 limit（保证赛道有代表）
        inpool = [s for s in stocks if s.sector]
        rest = [s for s in stocks if not s.sector]
        stocks = (inpool + rest)[:max(limit, len(inpool))]
    log.info("沪深300 成分：%d 只（源=%s，关注池命中=%d）",
             len(stocks), src, sum(1 for s in stocks if s.sector))
    return stocks, src


# ---------------- 日线行情（akshare 主 + yfinance 兜底 + 磁盘缓存）----------------
def _cache_path(code: str, days: int = 500):
    """缓存按【窗口档位】分开存：否则拉长回看窗后，旧的短历史缓存会静默顶替，
    看起来"有缓存"，实际期数还是老样子（这类静默降级最难发现）。"""
    return CACHE_DIR / f"{code}__{_yf_period(days)}.csv"


_PERIOD_RANK = {"2y": 0, "5y": 1, "10y": 2}      # 由短到长


def _read_cache_file(p, max_age_days: int):
    if not p.exists():
        return None
    try:
        import time
        import pandas as pd
        if (time.time() - p.stat().st_mtime) > max_age_days * 86400:   # 缓存过期→重拉
            return None
        df = pd.read_csv(p, parse_dates=["date"])
        return df if len(df) else None
    except Exception:
        return None


def _load_cache(code: str, max_age_days: int = 1, days: int = 500):
    """缓存读取。命中顺序：本档位 → 更长档位（截尾使用）。

    为什么"更长顶替更短"是安全的、反过来不行：
      · 长顶短：10 年缓存里包含 400 日请求所需的全部 K 线，取尾部即可，信息完整。
      · 短顶长：2 年缓存无法满足 5 年请求，若允许就会【静默降级】——
        看起来有缓存、实际期数还是老样子，这类问题最难发现，所以必须禁止。
    没有这一条，二部日报（400 日窗）与准入闸（2500 日窗）会各存一份、各拉一次，
    每天多下载一次全池 507 只，白等 80 秒。
    """
    df = _read_cache_file(_cache_path(code, days), max_age_days)
    if df is not None:
        return df
    want = _yf_period(days)
    for period, rank in sorted(_PERIOD_RANK.items(), key=lambda kv: kv[1]):
        if rank <= _PERIOD_RANK.get(want, 0):
            continue                       # 只往【更长】的档位找
        longer = _read_cache_file(CACHE_DIR / f"{code}__{period}.csv", max_age_days)
        if longer is not None and len(longer) >= 60:
            return longer.tail(days + 5).reset_index(drop=True)
    return None


def _akshare_hist(code: str, start: str, end: str):
    """akshare 前复权日线。返回标准列 DataFrame(date,open,high,low,close,volume) 或 None。"""
    try:
        import akshare as ak
        import pandas as pd
    except Exception:
        return None
    fn = getattr(ak, "stock_zh_a_hist", None)
    if not callable(fn):
        return None
    try:
        df = fn(symbol=code, period="daily", start_date=start, end_date=end, adjust="qfq")
        if df is None or not len(df):
            return None
        ren = {"日期": "date", "开盘": "open", "最高": "high", "最低": "low", "收盘": "close", "成交量": "volume"}
        df = df.rename(columns=ren)
        keep = [c for c in ("date", "open", "high", "low", "close", "volume") if c in df.columns]
        df = df[keep].copy()
        df["date"] = pd.to_datetime(df["date"])
        return df.sort_values("date").reset_index(drop=True)
    except Exception:
        return None


def _yf_period(days: int) -> str:
    """交易日数 → yfinance 合法 period。用文档内的档位（2y/5y/10y），
    避免 '{n}d' 被当【自然日】解释而少给约 30% 的 K 线（250 交易日≈365 自然日）。"""
    if days <= 350:
        return "2y"
    if days <= 1150:
        return "5y"
    return "10y"


def _yf_hist(yahoo: str, days: int):
    try:
        import yfinance as yf
        import pandas as pd
    except Exception:
        return None
    try:
        # auto_adjust=True：拆股/分红调整后价格（与基准 SPY adjusted 同口径，§Data Contract return basis）
        h = yf.Ticker(yahoo).history(period=_yf_period(days), auto_adjust=True)
        if h is None or not len(h):
            return None
        h = h.reset_index().rename(columns={"Date": "date", "Open": "open", "High": "high",
                                            "Low": "low", "Close": "close", "Volume": "volume"})
        h["date"] = pd.to_datetime(h["date"]).dt.tz_localize(None)
        return h[["date", "open", "high", "low", "close", "volume"]].sort_values("date").reset_index(drop=True)
    except Exception:
        return None


def _stooq_hist(ticker: str, days: int):
    """Stooq 免费日线兜底（美股 {ticker}.us）。整段取用，不与 yfinance 拼接（§Data Contract 单源规则）。
    注意：stooq 为未复权价——仅作 yfinance 完全取不到时的整段兜底，preferred_source 会标明。"""
    try:
        import pandas as pd
        url = f"https://stooq.com/q/d/l/?s={ticker.lower()}.us&i=d"
        df = pd.read_csv(url)
        if df is None or not len(df) or "Close" not in df.columns:
            return None
        df = df.rename(columns={"Date": "date", "Open": "open", "High": "high",
                                "Low": "low", "Close": "close", "Volume": "volume"})
        df["date"] = pd.to_datetime(df["date"])
        keep = [c for c in ("date", "open", "high", "low", "close", "volume") if c in df.columns]
        return df[keep].sort_values("date").tail(days + 40).reset_index(drop=True)
    except Exception:
        return None


def _fetch_one(s: "Stock", start: str, end: str, days: int) -> tuple:
    """按市场取单只股票整段日线（§Data Contract：一只股票一个源，绝不跨源拼接）。返回 (df, source)。"""
    if MARKET == "us":
        df = _yf_hist(s.yahoo, days)
        if df is not None and len(df) >= 60:
            return df, "yfinance"
        df = _stooq_hist(s.code, days)          # 整段改 stooq，不 patch 个别日
        if df is not None and len(df) >= 60:
            return df, "stooq"
        return None, "miss"
    # cn：akshare 主 → yfinance 兜底（各自整段）
    df = _akshare_hist(s.code, start, end)
    if df is not None and len(df) >= 60:
        return df, "akshare"
    df = _yf_hist(s.yahoo, days)
    if df is not None and len(df) >= 60:
        return df, "yfinance"
    return None, "miss"


def get_history(stocks: list[Stock], days: int = 500, status: dict | None = None) -> dict:
    """批量取日线（缓存优先 → akshare → yfinance）。返回 {code: DataFrame}。
    诚实：取不到的标的直接略过并计入 status，不造数。"""
    status = status if status is not None else {}
    if os.environ.get("CIO_QUANT_MOCK") == "1":         # 离线自测：合成确定性行情
        return _mock_panels(stocks, days)
    end = now_beijing().strftime("%Y%m%d")
    start = (now_beijing() - timedelta(days=int(days * 1.7) + 60)).strftime("%Y%m%d")
    out, ok, miss = {}, 0, 0
    src_count: dict = {}
    for s in stocks:
        df = _load_cache(s.code, days=days)
        source = "cache" if df is not None else ""
        if df is None:
            df, source = _fetch_one(s, start, end, days)     # 单源整段，不拼接
            if df is not None and len(df):
                try:
                    df.to_csv(_cache_path(s.code, days), index=False)
                except Exception:
                    pass
        if df is not None and len(df) >= 60:
            out[s.code] = df.tail(days + 5).reset_index(drop=True)
            s.preferred_source = source
            s.source_status = "complete" if len(df) >= _MIN_LEN_OK else "partial"
            src_count[source] = src_count.get(source, 0) + 1
            ok += 1
        else:
            s.source_status = "miss"
            miss += 1
    # 行数中位数：直接暴露"取到的历史有多长"。缓存/周期出问题时，这个数会明显小于 days，
    # 一眼可见，不必等 PDF 里的期数才发现（静默降级最怕看不见）。
    lens = sorted(len(v) for v in out.values())
    med = lens[len(lens) // 2] if lens else 0
    status["quant_history"] = f"ok={ok} miss={miss} sources={src_count} rows_median={med}/{days}"
    log.info("二部行情：成功 %d，缺失 %d，源=%s，行数中位数=%d（目标 %d）", ok, miss, src_count, med, days)
    if med and med < days * 0.6:
        log.warning("历史长度明显不足（%d < 目标 %d 的 60%%）：可能命中了旧窗口缓存或取数受限，"
                    "IC 期数会随之变少", med, days)
    return out


def latest_prices(codes: list[str], names: dict | None = None) -> dict:
    """取一批标的的【最新收盘价真值】{code: close}——CFO 盯市、CRO 取数用。缺的自动略过（不造价）。"""
    names = names or {}
    stocks = [Stock(code=c, name=names.get(c, ""), yahoo=_yahoo(c)) for c in codes]
    panels = get_history(stocks, days=60)
    out = {}
    for c in codes:
        df = panels.get(c)
        if df is not None and len(df):
            out[c] = float(df["close"].iloc[-1])
    return out


def get_benchmark(days: int = 500, status: dict | None = None):
    """沪深300 日线（CRO 算 Beta、CFO 算超额用）。返回 DataFrame(date,close) 或 None。
    akshare 主 / yfinance(000300.SS) 兜底；CIO_QUANT_MOCK=1 时给确定性合成序列。"""
    status = status if status is not None else {}
    if os.environ.get("CIO_QUANT_MOCK") == "1":
        import hashlib
        import numpy as np
        import pandas as pd
        # 合成行情的日期也走市场时区：否则离线冒烟会出现 as_of=8/25 配 snapshot=8/24，
        # 那是测试夹具自己制造的日期分歧，会掩盖（或伪造）真正的日期问题。
        from .config import market_date as _md
        base = pd.Timestamp(_md())
        dates = pd.bdate_range(end=base, periods=days + 5)
        seed = market().get("bench_source", "000300")
        rng = np.random.default_rng(int(hashlib.md5(seed.encode()).hexdigest()[:8], 16))
        close = 4000 * np.exp(np.cumsum(rng.normal(0.0002, 0.011, len(dates))))
        return pd.DataFrame({"date": dates, "close": close})
    # us：基准用 SPY adjusted（免费 total-return 代理，与个股 adjusted 同口径）
    if MARKET == "us":
        sym = market().get("bench_source", "SPY")
        df = _yf_hist(sym, days)
        if df is not None and len(df):
            # **"取到了"和"取全了"是两回事。** 一个 30 行的 SPY 面板照样
            # 通过 `len(df)`，然后让全市场每一只票的大盘超额同时变成 null——
            # 不报错、日志正常，只在 502 张卡片的"缺："行里各留一句话。
            status["benchmark"] = f"yfinance:{sym}(adjusted)"
            status["benchmark_rows"] = int(len(df))
            status["benchmark_want"] = int(days)
            status["benchmark_span"] = (str(df["date"].iloc[0])[:10],
                                        str(df["date"].iloc[-1])[:10])
            if len(df) < days * 0.5:
                status["benchmark_short"] = (
                    f"**基准只取到 {len(df)} 行（要 {days} 行）** —— "
                    f"这会让全市场每一只票的大盘超额同时变 null，"
                    f"不是个别票缺数据")
            # **行数够不等于能用。** 2026-09-04 真实发生过：SPY 405 行、
            # 只有最后一根收盘是 NaN（yfinance 尾行），于是全市场 502 只票的
            # excess_mkt_21/63/126 同时变 null——因为三个窗口共用 `series[-1]`。
            # 行数检查完全看不见这件事，所以这里单独数 NaN，**并且单独看最后一根**。
            bad = int(df["close"].isna().sum())
            status["benchmark_nan"] = bad
            last_bad = bool(len(df) and df["close"].isna().iloc[-1])
            status["benchmark_last_bad"] = last_bad
            # **说现在是什么，不要说修复之前会怎样。**
            # 第一版这里写的是"会让全市场的大盘超额同时变 null"——那是
            # `align()` 修好之前的后果。而 yfinance 那根未落定的尾行**每天都有**，
            # 于是这盏灯天天亮，报一个不会发生的故障。
            # **常亮的灯和不亮的灯是同一种缺陷。**
            if last_bad:
                status["benchmark_last_note"] = (
                    "基准最后一根收盘是 NaN（yfinance 未落定的尾行，常见）—— "
                    "**已按不可用样本丢掉**，超额照算；代价是这一路的超额"
                    "截止到上一个交易日，比板块那一路晚一天（卡片上的 "
                    "rs_mkt_as_of / rs_sector_as_of 会显示出来）")
            elif bad:
                status["benchmark_last_note"] = (
                    f"基准中间有 {bad} 根收盘是 NaN（最后一根正常）—— "
                    f"已按不可用样本丢掉，截止日不受影响")
            return df[["date", "close"]]
        status["benchmark"] = "缺"
        status["benchmark_rows"] = 0
        return None
    # akshare 指数
    try:
        import akshare as ak
        import pandas as pd
        for fn_name in ("stock_zh_index_daily_em", "stock_zh_index_daily"):
            fn = getattr(ak, fn_name, None)
            if not callable(fn):
                continue
            try:
                df = fn(symbol="sh000300")
                ccol = next((c for c in ("close", "收盘", "收盘价") if c in df.columns), None)
                dcol = next((c for c in ("date", "日期", "trade_date") if c in df.columns), None)
                if ccol and dcol:
                    df = df.rename(columns={ccol: "close", dcol: "date"})[["date", "close"]]
                    df["date"] = pd.to_datetime(df["date"])
                    status["benchmark"] = f"akshare:{fn_name}"
                    return df.sort_values("date").tail(days + 5).reset_index(drop=True)
            except Exception:
                continue
    except Exception:
        pass
    df = _yf_hist("000300.SS", days)          # yfinance 兜底
    if df is not None and len(df):
        status["benchmark"] = "yfinance:000300.SS"
        return df[["date", "close"]]
    status["benchmark"] = "缺"
    return None


def _mock_panels(stocks: list[Stock], days: int) -> dict:
    """离线自测用：为每只股票生成【确定性】随机游走行情（种子=代码哈希，可复现）。
    仅用于验证因子/打分/无未来函数逻辑，绝不进入真实报告。"""
    import hashlib
    import numpy as np
    import pandas as pd
    out = {}
    from .config import market_date as _md
    base = pd.Timestamp(_md())        # 合成行情日期同样走市场时区，避免夹具自造日期分歧
    dates = pd.bdate_range(end=base, periods=days + 5)
    for s in stocks:
        seed = int(hashlib.md5(s.code.encode()).hexdigest()[:8], 16)   # 稳定哈希，跨进程可复现
        rng = np.random.default_rng(seed)
        drift = (rng.random() - 0.45) * 0.0012            # 每只不同的漂移
        rets = rng.normal(drift, 0.02, len(dates))
        close = 10 * np.exp(np.cumsum(rets))
        vol = rng.integers(5_000_000, 50_000_000, len(dates)).astype(float)
        df = pd.DataFrame({"date": dates, "open": close * (1 + rng.normal(0, 0.003, len(dates))),
                           "high": close * (1 + abs(rng.normal(0, 0.006, len(dates)))),
                           "low": close * (1 - abs(rng.normal(0, 0.006, len(dates)))),
                           "close": close, "volume": vol})
        out[s.code] = df
    return out
