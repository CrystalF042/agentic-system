"""SEC XBRL 基本面数据层（免费、一手、严格 PIT）。

为什么这块值得做：价量因子无论怎么变形，都是【同一份价格序列】的函数；
基本面是真正独立的新信息。数据来自 SEC 官方 XBRL API——免费、一手、可追溯，
完全符合零付费红线。

**本模块最关键的一条铁律：point-in-time 用 filing date，不是 period end date。**
2026 Q1 的财报要到 5 月才公布；拿它去算 3 月的因子就是未来函数，会凭空造出
"基本面因子很有效"的假象。SEC 每条 fact 都带 `filed` 字段，本模块只取
【filed <= as_of】的记录，并在重述（restatement）时自动取当时已知的那一版。

SEC 合理使用要求：请求必须带可识别的 User-Agent（含联系方式），限速 10 次/秒。
请在 .env 里设 CIO_SEC_UA，例如：CIO_SEC_UA=YourName your@email.com
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta

from .config import RAW_DIR
from .utils import get_logger

log = get_logger("cio.fundamentals")

FUND_DIR = RAW_DIR / "sec_facts"
FUND_DIR.mkdir(parents=True, exist_ok=True)

_RATE_SLEEP = 0.12                      # ≈8 次/秒，低于 SEC 的 10/s 上限

# 需要的 us-gaap 概念。存量（instant）与流量（duration）分开处理：
# 流量必须取【年度】口径，否则季度与年度混在一起会造成量纲错乱。
INSTANT = ["Assets", "Liabilities", "StockholdersEquity",
           "AssetsCurrent", "LiabilitiesCurrent",          # 供流动比率（有披露才有）
           "SharesOutstanding"]                            # 供市值 → 估值组（见下）
FLOW = ["Revenues", "GrossProfit", "OperatingIncomeLoss", "NetIncomeLoss",
        "NetCashProvidedByUsedInOperatingActivities",
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "InterestExpense",                                  # 供利息保障倍数（有披露才有）
        "CostOfRevenue"]                                    # 供毛利派生（见下）
# 部分公司用新准则科目名，做同义回退
ALIASES = {
    "Revenues": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                 "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet"],
    "NetCashProvidedByUsedInOperatingActivities": [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "InterestExpense": ["InterestExpense", "InterestExpenseDebt",
                        "InterestExpenseNonoperating"],
    # 资本开支的标签在各行业差别很大；漏掉同义名会让 FCF 静默虚高。
    "PaymentsToAcquirePropertyPlantAndEquipment": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
        "PaymentsToAcquireOtherPropertyPlantAndEquipment"],
    "CostOfRevenue": ["CostOfRevenue", "CostOfGoodsAndServicesSold",
                      "CostOfGoodsSold", "CostOfServices"],
    # 流通股本。**注意它主要住在 dei 命名空间**（10-Q/10-K 封面页那个数），
    # 不在 us-gaap 里——只扫 us-gaap 会让估值组整组算不出来，且没有任何报错。
    # 市值 = 股本 × 现价：股本来自最近一次申报（最多约 90 天前），
    # 期间的回购/增发不会反映，这一点必须在报告里标出来，不能当成实时市值。
    "SharesOutstanding": ["EntityCommonStockSharesOutstanding",        # dei，封面页，最及时
                          "CommonStockSharesOutstanding",              # us-gaap
                          "CommonStockSharesIssued",
                          "WeightedAverageNumberOfDilutedSharesOutstanding"],
    # 资产负债表恒等式是 资产 = 负债 + 【全部】权益（含少数股东权益）。
    # us-gaap 的 StockholdersEquity 是【母公司口径】，不含 NCI；
    # 用它反推负债会把 NCI 算进负债里，杠杆被系统性高估。
    # 因此优先取含 NCI 的那个标签，取不到再退回母公司口径。
    "StockholdersEquity": [
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        "StockholdersEquity"],
}
# 注意：InterestIncomeExpenseNet 曾在别名表里，已移除——它对银行是【净利息收入】，
# 取 abs() 之后算出来的"利息保障倍数"没有任何经济含义，属于用错科目而非缺数据。
CONCEPTS = INSTANT + FLOW

# 紧凑缓存的 schema 版本。**新增 CONCEPTS 时必须 +1**——否则旧缓存里没有新科目，
# 而缓存又在有效期内，新字段会永远是空值，且没有任何报错。这是一个静默失败陷阱。
CACHE_SCHEMA = 5


# ---------------- 取数 ----------------
def sec_ua() -> str:
    """延迟读取，便于 .env 或 shell 在导入后设置也能生效。"""
    return os.environ.get("CIO_SEC_UA", "").strip()


UA_HINT = ("未设置 CIO_SEC_UA。SEC 合理使用政策要求请求带可识别的 User-Agent（含联系方式），"
           "否则会被拒绝。请在 cio-agent/.env 里加一行，例如：\n"
           "  CIO_SEC_UA=Crystal Guo your@email.com")


def _fetch(url: str) -> "dict | None":
    import urllib.request
    ua = sec_ua()
    if not ua:
        return None                    # 前置检查已在 load_universe 做过，这里只做防御
    try:
        req = urllib.request.Request(url, headers={"User-Agent": ua,
                                                   "Accept-Encoding": "gzip, deflate"})
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                import gzip
                raw = gzip.decompress(raw)
        return json.loads(raw)
    except Exception as e:
        log.warning("SEC 取数失败 %s：%s", url.split("/")[-1], e)
        return None


def _extract(facts: dict) -> dict:
    """从 companyfacts JSON 抽出我们需要的概念，压成紧凑结构（原始 JSON 太大，不落盘）。

    每条记录保留 (filed, end, val, start)：
      filed —— PIT 的唯一依据（何时可被公众知晓）
      end   —— 会计期末（仅用于匹配同比的对应期，绝不用作可见性判断）
    """
    out: dict = {}
    allf = (facts or {}).get("facts") or {}
    us = dict(allf.get("us-gaap") or {})
    # dei 命名空间放的是"实体级"事实（封面页股本、财年信息）。
    # 合并进来时 us-gaap 优先：同名概念以会计口径为准，dei 只补 us-gaap 没有的。
    for _k, _v in (allf.get("dei") or {}).items():
        us.setdefault(_k, _v)
    for concept in CONCEPTS:
        rows: list = []
        seen: dict = {}
        # **必须遍历所有同义名，不能命中一个就 break。**
        # 曾经的写法是"哪个同义名先有数据就只用哪个"，后果极其隐蔽：
        # 大量美股公司 2018 年采用 ASC 606 后把 Revenues 换成了
        # RevenueFromContractWithCustomer…，旧标签只剩 2018 年之前的记录。
        # 先命中 Revenues 就 break，等于把营业收入永久冻结在 2017 年，
        # 而毛利/营业利润仍取最新年度 —— 算出来是 180% 的毛利率，
        # 且 filing_accepted_date 显示最新、不触发 stale，完全看不出错。
        for pri, name in enumerate(ALIASES.get(concept, [concept])):
            node = us.get(name)
            if not node:
                continue
            for _unit, arr in (node.get("units") or {}).items():
                for f in arr:
                    filed, end, val = f.get("filed"), f.get("end"), f.get("val")
                    if not filed or not end or val is None:
                        continue
                    key = (filed, end, f.get("start") or "")
                    # 同一期被多个同义名各报一次时，取【别名表中更靠前】的那个口径，
                    # 保证同一家公司在时间轴上口径稳定，不会在某一年突然跳到另一种定义。
                    if key in seen and seen[key] <= pri:
                        continue
                    seen[key] = pri
                    rows.append([filed, end, float(val), f.get("start") or "", pri])
        if rows:
            best: dict = {}
            for r in rows:
                key = (r[0], r[1], r[3])
                if key not in best or r[4] < best[key][4]:
                    best[key] = r
            merged = [r[:4] for r in best.values()]
            merged.sort(key=lambda r: (r[0], r[1]))
            out[concept] = merged
    return out


def cache_path(cik: str):
    return FUND_DIR / f"CIK{str(cik).zfill(10)}.json"


def load_company(cik: str, max_age_days: int = 7) -> "dict | None":
    """取单家公司的紧凑基本面记录（缓存优先；缓存只存抽取后的结果，不存原始大 JSON）。"""
    if not cik:
        return None
    p = cache_path(cik)
    if p.exists():
        try:
            if (time.time() - p.stat().st_mtime) <= max_age_days * 86400:
                d = json.loads(p.read_text(encoding="utf-8"))
                # schema 校验：旧版缓存缺少后加的科目，必须重取，否则新字段静默为空
                if isinstance(d, dict) and d.get("_schema") == CACHE_SCHEMA:
                    return d
        except Exception:
            pass
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{str(cik).zfill(10)}.json"
    time.sleep(_RATE_SLEEP)
    facts = _fetch(url)
    if not facts:
        return None
    ex = _extract(facts)
    ex["_schema"] = CACHE_SCHEMA
    try:
        p.write_text(json.dumps(ex), encoding="utf-8")
    except Exception:
        pass
    return ex


_MEM: dict = {}


def load_universe_cached(stocks: list, status: "dict | None" = None) -> dict:
    """同一次运行内复用：SEC 记录一天之内不会变，重复 load 只是浪费时间。
    键取 ticker 集合的哈希，池子变了会自然重取。"""
    import hashlib
    key = hashlib.md5(",".join(sorted(getattr(s, "code", "") for s in stocks)).encode()).hexdigest()
    if key in _MEM:
        if status is not None:
            status["sec_facts"] = _MEM[key][1] + " (in-run cache)"
        return _MEM[key][0]
    st: dict = {}
    out = load_universe(stocks, st)
    _MEM[key] = (out, st.get("sec_facts", ""))
    if status is not None:
        status.update(st)
    return out


def load_universe(stocks: list, status: "dict | None" = None) -> dict:
    """批量取基本面。返回 {ticker: 紧凑记录}。取不到的略过并如实计数。"""
    status = status if status is not None else {}
    if not sec_ua():                   # 一次性前置检查：不要在 503 家公司的循环里逐个报错
        raise RuntimeError(UA_HINT)
    out, ok, miss, foreign = {}, 0, 0, 0
    # 成分表没给 CIK 的（关注池里的非成分标的），用 SEC 官方清单补上再试一次。
    tmap = {}
    if any(not getattr(s, "cik", "") for s in stocks):
        tmap = ticker_cik_map()
        if tmap:
            log.info("已加载 SEC ticker→CIK 清单（%d 条），用于补齐非成分标的", len(tmap))
    log.info("开始取 SEC 基本面（%d 家；首次全量约需 %d 分钟，之后走缓存）",
             len(stocks), max(1, int(len(stocks) * 0.6 / 60)))
    for s in stocks:
        cik = getattr(s, "cik", "") or tmap.get(str(s.code).upper(), "")
        d = load_company(cik) if cik else None
        if d is None:
            miss += 1
            continue
        out[s.code] = d
        # 取到了记录但里面没有任何 us-gaap 事实 = 外国发行人（20-F / IFRS）。
        # 这与"取数失败"是两件事，必须分开计数，否则 ok 数会偏乐观。
        if has_us_gaap(d):
            ok += 1
        else:
            foreign += 1
    status["sec_facts"] = (f"us-gaap={ok} foreign(20-F/IFRS)={foreign} unavailable={miss}")
    log.info("SEC 基本面：us-gaap %d 家，外国发行人 %d 家（IFRS，覆盖外），取不到 %d 家",
             ok, foreign, miss)
    return out


# ---------------- PIT 存取（本模块的核心）----------------
def _is_annual(row) -> bool:
    """流量取年度口径：期间跨度 300–400 天。避免季度与年度混用造成量纲错乱。"""
    start, end = row[3], row[1]
    if not start:
        return False
    try:
        d = (datetime.strptime(end, "%Y-%m-%d") - datetime.strptime(start, "%Y-%m-%d")).days
        return 300 <= d <= 400
    except Exception:
        return False


def pit(fund: dict, concept: str, as_of, annual: bool = False):
    """【PIT 取值】as_of 当日可知的最新一条：filed <= as_of 中 end 最新者。

    重述处理：同一会计期可能有多个版本（原始 + 重述），各有不同 filed。
    本函数只看 filed <= as_of，因此自动取"当时已知的那一版"——
    重述后的数字在它被公布之前不可见，这正是 PIT 的要求。
    返回 (val, end) 或 (None, None)。
    """
    rows = (fund or {}).get(concept)
    if not rows:
        return None, None
    ao = as_of.strftime("%Y-%m-%d") if hasattr(as_of, "strftime") else str(as_of)[:10]
    best = None
    for r in rows:
        if r[0] > ao:                      # filed 晚于 as_of → 当时不可知，跳过
            continue
        if annual and not _is_annual(r):
            continue
        if best is None or r[1] > best[1] or (r[1] == best[1] and r[0] > best[0]):
            best = r
    return (best[2], best[1]) if best else (None, None)


def pit_yoy(fund: dict, concept: str, as_of, annual: bool = False):
    """同比配对：取 as_of 可知的最新期，再取【期末约早一年】且同样可知的那一期。
    两端都受 filed <= as_of 约束，绝不引入未来信息。返回 (now, year_ago) 或 (None, None)。"""
    val, end = pit(fund, concept, as_of, annual=annual)
    if val is None:
        return None, None
    rows = (fund or {}).get(concept) or []
    ao = as_of.strftime("%Y-%m-%d") if hasattr(as_of, "strftime") else str(as_of)[:10]
    try:
        target = (datetime.strptime(end, "%Y-%m-%d") - timedelta(days=365)).strftime("%Y-%m-%d")
    except Exception:
        return None, None
    best, gap = None, 10 ** 9
    for r in rows:
        if r[0] > ao or r[1] >= end:
            continue
        if annual and not _is_annual(r):
            continue
        try:
            g = abs((datetime.strptime(r[1], "%Y-%m-%d")
                     - datetime.strptime(target, "%Y-%m-%d")).days)
        except Exception:
            continue
        # 期末相差 4 个月内才算同比对应期；【同一会计期有多版本时取当时已知的最新版】——
        # 与 pit() 口径一致：重述一经公布，此后的历史对照就应使用重述值。
        if g <= 120 and (g < gap or (best is not None and g == gap and r[0] > best[0])):
            best, gap = r, g
    return (val, best[2]) if best else (None, None)


def _ratio(a, b, cap: float = 50.0):
    if a is None or b is None or abs(b) < 1e-9:
        return None
    v = a / b
    return max(-cap, min(cap, v))


# ---------------- 基本面因子（方向已对齐：越大越"好"）----------------
def f_gross_profit(ctx, i):
    """毛利/总资产（Novy-Marx 毛利率）。假设：毛利是最'干净'的盈利能力度量，
    比净利更少受会计操纵与一次性项目影响。"""
    g, _ = pit(ctx.fund, "GrossProfit", ctx.as_of, annual=True)
    a, _ = pit(ctx.fund, "Assets", ctx.as_of)
    return _ratio(g, a)


def f_gross_margin(ctx, i):
    """毛利率 = 毛利 / 营业收入（与营业利润率同为"率"口径，可并入 Profitability 维度）。
    注意与 f_gross_profit(毛利/总资产, Novy-Marx) 的区别：后者是资产回报口径，不是率。"""
    g, _ = pit(ctx.fund, "GrossProfit", ctx.as_of, annual=True)
    r, _ = pit(ctx.fund, "Revenues", ctx.as_of, annual=True)
    return _ratio(g, r)


def f_op_margin(ctx, i):
    """营业利润率 = 营业利润 / 营业收入。假设：高margin 反映定价权与护城河。"""
    o, _ = pit(ctx.fund, "OperatingIncomeLoss", ctx.as_of, annual=True)
    r, _ = pit(ctx.fund, "Revenues", ctx.as_of, annual=True)
    return _ratio(o, r)


def f_asset_growth(ctx, i):
    """资产增长率，取负（资产增长异象）。假设：激进扩张的公司后续收益偏低
    （过度投资/管理层帝国建造），故增长越低越好。"""
    now, ago = pit_yoy(ctx.fund, "Assets", ctx.as_of)
    r = _ratio(now, ago)
    return -(r - 1.0) if r is not None else None


def f_leverage(ctx, i):
    """负债率，取负。假设：高杠杆在紧缩环境下是脆弱性来源。"""
    l, _ = pit(ctx.fund, "Liabilities", ctx.as_of)
    a, _ = pit(ctx.fund, "Assets", ctx.as_of)
    r = _ratio(l, a)
    return -r if r is not None else None


def f_accruals(ctx, i):
    """应计 =(净利润 − 经营现金流)/总资产，取负（Sloan 应计异象）。
    假设：利润里现金含量低的部分质量差、难持续。"""
    ni, _ = pit(ctx.fund, "NetIncomeLoss", ctx.as_of, annual=True)
    cf, _ = pit(ctx.fund, "NetCashProvidedByUsedInOperatingActivities", ctx.as_of, annual=True)
    a, _ = pit(ctx.fund, "Assets", ctx.as_of)
    if ni is None or cf is None:
        return None
    r = _ratio(ni - cf, a)
    return -r if r is not None else None


def f_fcf_assets(ctx, i):
    """自由现金流 / 总资产 =(经营现金流 − 资本开支)/资产。假设：真实造血能力。"""
    cf, _ = pit(ctx.fund, "NetCashProvidedByUsedInOperatingActivities", ctx.as_of, annual=True)
    cx, _ = pit(ctx.fund, "PaymentsToAcquirePropertyPlantAndEquipment", ctx.as_of, annual=True)
    a, _ = pit(ctx.fund, "Assets", ctx.as_of)
    if cf is None or cx is None:      # 资本开支缺标签 ≠ 资本开支为 0，缺就是缺
        return None
    return _ratio(cf - cx, a)


def f_earnings_growth(ctx, i):
    """净利润同比增速。假设：盈利动量（基本面动量）。
    分母取绝对值，避免由负转正时符号错乱。"""
    now, ago = pit_yoy(ctx.fund, "NetIncomeLoss", ctx.as_of, annual=True)
    if now is None or ago is None or abs(ago) < 1e-9:
        return None
    return max(-5.0, min(5.0, (now - ago) / abs(ago)))


FUNDAMENTAL_FACTORS = {
    "毛利率":   {"fn": f_gross_profit,    "desc": "毛利/总资产（Novy-Marx 毛利率）"},
    "毛利margin": {"fn": f_gross_margin,  "desc": "毛利/营业收入（率口径，用于 Quality 的 Profitability 维度）"},
    "营业利润率": {"fn": f_op_margin,       "desc": "营业利润/营业收入"},
    "资产增长":  {"fn": f_asset_growth,    "desc": "资产同比增长，取负（资产增长异象）"},
    "杠杆":     {"fn": f_leverage,        "desc": "负债/总资产，取负"},
    "应计":     {"fn": f_accruals,        "desc": "(净利−经营现金流)/资产，取负（Sloan）"},
    "自由现金流": {"fn": f_fcf_assets,      "desc": "(经营现金流−资本开支)/资产"},
    "盈利增长":  {"fn": f_earnings_growth, "desc": "净利润同比增速（基本面动量）"},
}


# ---------------- 描述性快照（Systematic Analytics 用；不是因子）----------------
# 与上面的因子函数刻意分开：因子是"方向已对齐、越大越好"的研究量；
# 快照是【自然口径的事实】——杠杆就是负债/资产，不翻转符号。
# 测量报告不做方向判断，翻转符号本身就是一种判断。

_TICKER_MAP_PATH = FUND_DIR / "_ticker_cik.json"


def ticker_cik_map(max_age_days: int = 30) -> dict:
    """SEC 官方 ticker → CIK 清单（免费公开，一次请求，本地缓存 30 天）。

    为什么需要：成分表只给 S&P 500 成分的 CIK。关注池里的非成分标的
    （NVO / ARM / ASML / TSM）因此一个 CIK 都没有，基本面流程根本不会去尝试它们——
    结果它们整行空白，看起来和"公司没披露"一模一样，而真实原因是我们没查。
    补上映射之后：能报 us-gaap 的就有数；报 IFRS 的会被正确识别为覆盖范围之外。
    """
    import urllib.request
    if _TICKER_MAP_PATH.exists():
        try:
            if (time.time() - _TICKER_MAP_PATH.stat().st_mtime) <= max_age_days * 86400:
                return json.loads(_TICKER_MAP_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    ua = sec_ua()
    if not ua:
        return {}
    try:
        req = urllib.request.Request("https://www.sec.gov/files/company_tickers.json",
                                     headers={"User-Agent": ua})
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = json.loads(r.read())
        out = {}
        for _k, v in (raw or {}).items():
            tk = str(v.get("ticker", "")).upper().strip()
            cik = v.get("cik_str")
            if tk and cik is not None:
                out[tk] = str(cik).zfill(10)
        if out:
            _TICKER_MAP_PATH.write_text(json.dumps(out), encoding="utf-8")
        return out
    except Exception as e:
        log.warning("SEC ticker→CIK 清单取数失败（非成分标的将无基本面）：%s", e)
        return {}


def has_us_gaap(fund: dict) -> bool:
    """该记录里是否有任何 us-gaap 事实。
    外国发行人（20-F，IFRS 分类）会返回 False —— 这是覆盖范围之外，不是取数失败。"""
    if not fund:
        return False
    return any((not str(k).startswith("_")) and isinstance(v, list) and v
               for k, v in fund.items())


def latest_filed(fund: dict, as_of) -> str:
    """as_of 当日可见的【最近一次申报日期】（所有科目里 filed <= as_of 的最大值）。
    这是报告里 `Fundamentals as of ...` 的依据，也是 stale 判定的输入。"""
    if not fund:
        return ""
    ao = as_of.strftime("%Y-%m-%d") if hasattr(as_of, "strftime") else str(as_of)[:10]
    best = ""
    for k, rows in fund.items():
        if k.startswith("_") or not isinstance(rows, list):
            continue
        for r in rows:
            if r[0] <= ao and r[0] > best:
                best = r[0]
    return best


# 期末容差必须【按配对类型分开】，不能一个数走天下。
#   流量÷流量（毛利率、营业利润率）：必须是同一个会计期 → 容差要紧。
#   存量÷存量（杠杆、流动比率）：必须是同一张资产负债表 → 容差要紧。
#   流量÷存量（FCF/资产、毛利/资产）：**天然错开**。分子是最近一个完整年度的流量，
#       分母是最近一季的资产负债表——两者相差最多可达一整年，这是会计惯例，不是错误。
# 之前一律用 200 天，结果把 FCF/资产 这一列在半数公司上系统性地抹掉了
# （AMAT/MU/AAPL/QCOM 都有 FCF/Rev 却没有 FCF/资产，就是这么来的）。
_GAP_SAME_PERIOD = 45         # 同期口径：允许一点点申报错位，不允许差一个季度
_GAP_STOCK_AFTER_FLOW = 400   # 资产负债表可以比流量期末【晚】最多约一年
_GAP_STOCK_BEFORE_FLOW = 45   # 但不允许明显【早】于流量期末（旧资产配新流量是错的）


def _gap_days(e1: str, e2: str):
    try:
        return abs((datetime.strptime(e1, "%Y-%m-%d") - datetime.strptime(e2, "%Y-%m-%d")).days)
    except Exception:
        return None


def _signed_gap(later: str, earlier: str):
    """later - earlier 的天数（可为负）。取不到日期返回 None。"""
    try:
        return (datetime.strptime(later, "%Y-%m-%d") - datetime.strptime(earlier, "%Y-%m-%d")).days
    except Exception:
        return None


def _pair_ok(a_end: str, b_end: str, kind: str) -> bool:
    """分子分母的会计期是否可以配对。kind: same | flow_over_stock。"""
    if not a_end or not b_end:
        return True                     # 有一端没带期末信息时不拦（调用方自己保证）
    if kind == "flow_over_stock":
        d = _signed_gap(b_end, a_end)   # 存量期末 − 流量期末
        return d is not None and -_GAP_STOCK_BEFORE_FLOW <= d <= _GAP_STOCK_AFTER_FLOW
    g = _gap_days(a_end, b_end)
    return g is not None and g <= _GAP_SAME_PERIOD


def _pct(a, b, a_end: str = "", b_end: str = "", cap: float = 1000.0, kind: str = "same"):
    """比率转百分数。三种情况一律返回 None，绝不返回一个"看起来正常"的数：

    1. 任一端缺失，或分母为 0；
    2. **分子分母来自不同会计期**——每个 pit() 各自取"该科目最新可见的一期"，
       如果某个科目公司后来不再披露（或换了标签），两端就会静默错配：
       2026 年的资产 ÷ 2022 年的收入，算出来是个完全正常的百分数，没有任何迹象。
       这是本模块最危险的一类静默错误，必须在这里挡住。
    3. 触到上下限——被截断的值是**捏造的数字**，不是测量结果。宁可缺失，
       也不能让一个 1000% 的封顶值进入百分位排序（它会稳居榜首并挤走真实极值）。
    """
    if a is None or b is None or abs(b) < 1e-9:
        return None
    if not _pair_ok(a_end, b_end, kind):
        return None
    v = a / b * 100.0
    return None if abs(v) >= cap else v


def filing_cadence(fund: dict, as_of) -> "float | None":
    """这家公司【自己】的申报节奏（相邻两次申报的中位间隔，天）。

    为什么不能用一个固定的 100 天判断"陈旧"：
    美国国内发行人按季报（10-Q/10-K），间隔约 90 天；
    而外国私人发行人只报年报（20-F），间隔约 365 天——
    ASML 就是这样：最近一次申报在 180 天前，按 100 天的固定线会被标成 stale，
    可它完全是按自己的规矩准时申报的。那个标记传达的是"它晚了"，而事实不是。

    自校准的做法：拿这家公司自己的节奏当基准。数字本身有多旧照常显示
    （那才是 CRO 真正要的信息），只有【超出它自己的节奏】才标记。
    """
    if not fund:
        return None
    ao = as_of.strftime("%Y-%m-%d") if hasattr(as_of, "strftime") else str(as_of)[:10]
    dates = set()
    for k, rows in fund.items():
        if k.startswith("_") or not isinstance(rows, list):
            continue
        for r in rows:
            if r[0] <= ao:
                dates.add(r[0])
    ds = sorted(dates)[-12:]                 # 只看最近若干次，避免早年节奏干扰
    if len(ds) < 3:
        return None
    gaps = []
    for a, b in zip(ds, ds[1:]):
        g = _gap_days(b, a)
        if g and 5 <= g <= 800:              # 剔除同日多次申报与异常长空档
            gaps.append(g)
    if not gaps:
        return None
    gaps.sort()
    return float(gaps[len(gaps) // 2])


def snapshot(fund: dict, as_of, stale_days: int = 100) -> dict:
    """一家公司在 as_of 当日【可见的】基本面事实快照。

    严格 PIT：所有取值都经过 pit()，只看 filed <= as_of。
    所有字段都可能是 None —— 各公司披露口径不同（不是所有公司都报 GrossProfit），
    缺失就如实留空，绝不用行业中位数之类的方式填补：填补出来的数字会被下游当成事实。
    """
    from datetime import date as _date

    a, a_e = pit(fund, "Assets", as_of)
    li, li_e = pit(fund, "Liabilities", as_of)
    ac, ac_e = pit(fund, "AssetsCurrent", as_of)
    lc, lc_e = pit(fund, "LiabilitiesCurrent", as_of)
    rev, rev_e = pit(fund, "Revenues", as_of, annual=True)
    gp, gp_e = pit(fund, "GrossProfit", as_of, annual=True)
    op, op_e = pit(fund, "OperatingIncomeLoss", as_of, annual=True)
    cf, cf_e = pit(fund, "NetCashProvidedByUsedInOperatingActivities", as_of, annual=True)
    cx, cx_e = pit(fund, "PaymentsToAcquirePropertyPlantAndEquipment", as_of, annual=True)
    ie, ie_e = pit(fund, "InterestExpense", as_of, annual=True)
    rev_now, rev_ago = pit_yoy(fund, "Revenues", as_of, annual=True)

    # 自由现金流 = 经营现金流 − 资本开支。
    # **资本开支缺失时不能当 0**：不是所有公司都用 PaymentsToAcquirePropertyPlantAndEquipment
    # 这个标签，缺标签被当成"没有资本开支"，会让 FCF 直接等于经营现金流——
    # 对重资产公司（半导体、能源）虚高一倍以上，然后稳稳排进 FCF 百分位前列。
    # 这正是本模块"缺失就是缺失，绝不填 0"铁律要挡的东西。
    # —— 派生回退 ——
    # 这两个科目在 us-gaap 分类里都是可选的，大量公司根本不单独标：
    #   Liabilities   —— 只标 Assets 与 StockholdersEquity（资产负债表恒等式可反推）
    #   GrossProfit   —— 只标 Revenues 与 CostOfRevenue（相减即得）
    # 不做回退的后果不是"少一个数"，而是【杠杆和毛利率两整列在半数公司上是空的】，
    # 而这两列恰好是 CRO 最常看的。反推出来的值与直接披露的值口径一致，
    # 且仍然全程受 PIT 与同期配对约束，不是估算。
    # derived 记录的是【哪些输出字段】是反推来的，而不是公式字符串——
    # 报告要把星号打在真正推导出来的那个格子上，不是打在 ticker 上。
    derived: list = []
    if li is None:
        eq, eq_e = pit(fund, "StockholdersEquity", as_of)
        if a is not None and eq is not None and _pair_ok(a_e, eq_e, "same"):
            li, li_e = a - eq, a_e
            derived.append("liab_assets")
    if gp is None:
        cr, cr_e = pit(fund, "CostOfRevenue", as_of, annual=True)
        if rev is not None and cr is not None and _pair_ok(rev_e, cr_e, "same"):
            gp, gp_e = rev - cr, rev_e
            derived.append("gross_margin")

    fcf, fcf_e = None, ""
    if cf is not None and cx is not None and _pair_ok(cf_e, cx_e, "same"):
        fcf, fcf_e = cf - cx, cf_e
    filed = latest_filed(fund, as_of)
    age = None
    if filed:
        try:
            ao = as_of if isinstance(as_of, _date) else datetime.strptime(str(as_of)[:10], "%Y-%m-%d").date()
            age = (ao - datetime.strptime(filed, "%Y-%m-%d").date()).days
        except Exception:
            age = None

    cr = None                       # 流动比率：同一张资产负债表（存量÷存量）
    if ac is not None and lc is not None and abs(lc) > 1e-9 and _pair_ok(ac_e, lc_e, "same"):
        cr = ac / lc
    ic = None                       # 利息保障：同一年度（流量÷流量）
    if op is not None and ie is not None and abs(ie) > 1e-9 and _pair_ok(op_e, ie_e, "same"):
        ic = op / abs(ie)

    # 陈旧判定按【这家公司自己的申报节奏】校准，而不是一条固定的 100 天线。
    # 年报制的外国发行人（如 ASML）间隔约 365 天，固定线会把"准时"误判成"陈旧"。
    cadence = filing_cadence(fund, as_of)
    thr = max(float(stale_days), 1.5 * cadence) if cadence else float(stale_days)

    return {
        # **总负债 / 总资产，不是 debt / assets。**
        # li 来自 us-gaap 的 Liabilities（或由 资产−权益 反推），是【全部负债】：
        # 应付账款、预收款项、递延收入、应交税费、租赁负债、养老金负债……全都在里面，
        # 有息债务只是其中一部分。要真算 debt/assets 必须单独取
        # ShortTermBorrowings + LongTermDebtCurrent + LongTermDebtNoncurrent，
        # 绝不能用资产减权益倒推出来。
        # 二部做的是资产负债表状态描述，不是信用模型，所以口径就叫它的真名。
        "liab_assets": _pct(li, a, li_e, a_e),
        "gross_margin": _pct(gp, rev, gp_e, rev_e),
        "op_margin": _pct(op, rev, op_e, rev_e),
        "fcf_margin": _pct(fcf, rev, fcf_e, rev_e),
        "fcf_assets": _pct(fcf, a, fcf_e, a_e, kind="flow_over_stock"),
        "rev_growth": (_pct(rev_now - rev_ago, abs(rev_ago))
                       if (rev_now is not None and rev_ago is not None and abs(rev_ago) > 1e-9) else None),
        "current_ratio": cr,
        "interest_cover": ic,
        "filing_accepted_date": filed,
        "filing_age_days": age,
        "filing_stale": bool(age is not None and age > thr),
        "filing_cadence_days": (int(cadence) if cadence else None),
        "filing_stale_threshold_days": int(thr),
        "derived_fields": derived,
        # 本快照实际用到的最新会计期末。与 filing_accepted_date 是两个不同的日期：
        # 前者是"数字描述的期间"，后者是"这份数字什么时候变成公开信息"。
        "period_end": max([e for e in (a_e, rev_e, op_e) if e] or [""]),
    }
