"""配置加载：读取 config/*.yaml 与环境变量 (.env)。"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

# 目录基准：本文件位于 <BASE>/src/cio/config.py → BASE = parents[2]
BASE = Path(__file__).resolve().parents[2]
CONFIG_DIR = BASE / "config"

RAW_DIR = BASE / "raw-data"
COMPANY_DIR = BASE / "Company Archive"
TOPIC_DIR = BASE / "Topic Archive"
MEMORY_DIR = BASE / "memory"
LANCE_DIR = BASE / "lancedb"
OUT_DIR = BASE / "out"

# `CIO_DB` 让演示、自测、check_build 指到一个临时库，**不碰真账**。
# 与 `CIO_CFO_DB` 同一套做法。默认仍是 <BASE>/cio.db，行为不变。
#
# 为什么必须有：账本、提案、审批状态都落在这个库里。没有这个开关，
# 一次「跑给人看」的演示会在真账里留下真提案 —— 那些提案将来会被
# 批准流程当成真实待办，而它们从来不是。
DB_PATH = Path(os.environ.get("CIO_DB") or (BASE / "cio.db"))

for _d in (RAW_DIR, COMPANY_DIR, TOPIC_DIR, MEMORY_DIR, LANCE_DIR, OUT_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def _load_dotenv() -> None:
    """极简 .env 读取（不覆盖已存在的环境变量）。"""
    env = BASE / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)


_load_dotenv()


MARKET_BUCKETS = {
    "us": ("world", "us"),
    "cn": ("world", "cn"),
}
"""**每个市场收哪些新闻桶。** `world` 两边都收；`cn` 桶只在 A 股模式下收。

2026-09-02 那份美股盘前简报里混进了「浙江宁波…」和几条 A 股外资流入，
共同社与财新还各失败一次——因为 `sources()` 把整份 yaml 原样返回，
**六个中国源和三条中国关键词在美股模式下照常抓**。

这不是崩溃，是稀释：十条 Watch Today 里占掉两条，
就等于当天真正该看的美股条目少了两条。
"""


def buckets(market: str = "") -> tuple:
    return MARKET_BUCKETS.get((market or MARKET), MARKET_BUCKETS["cn"])


@lru_cache(maxsize=2)
def sources(all_buckets: bool = False) -> dict:
    """数据源配置，**按市场过滤新闻桶**。

    `all_buckets=True` 拿到未过滤的全量——专题研究用它：CEO 点名要一份
    关于某个中国主题的报告时，静默把中文源摘掉是同一类缺陷，只是方向相反。

    过滤掉了什么会写进 `cfg["_bucket_filter"]`，由 `collect_premarket`
    抄进采集状态栏。**看不见的过滤和没有过滤长得一模一样**，
    而这次要防的恰恰是"某个源今天没出新闻"和"某个源根本没被抓"分不清。
    """
    cfg = yaml.safe_load((CONFIG_DIR / "sources.yaml").read_text(encoding="utf-8"))
    if all_buckets:
        return cfg
    keep = set(buckets())
    gn = cfg.get("google_news") or {}
    dropped_rss = [f.get("name", "?") for f in (cfg.get("rss") or [])
                   if f.get("bucket") not in keep]
    dropped_q = [q.get("q", "?") for q in (gn.get("standing_queries") or [])
                 if q.get("bucket") not in keep]
    cfg["rss"] = [f for f in (cfg.get("rss") or []) if f.get("bucket") in keep]
    gn["standing_queries"] = [q for q in (gn.get("standing_queries") or [])
                              if q.get("bucket") in keep]
    gn["section_feeds"] = [f for f in (gn.get("section_feeds") or [])
                           if f.get("bucket") in keep]
    cfg["google_news"] = gn
    cfg["_bucket_filter"] = {
        "market": MARKET, "kept": sorted(keep),
        "dropped_rss": dropped_rss, "dropped_queries": dropped_q,
    }
    return cfg


# ---------------- 市场开关（共存，不 fork）----------------
# CIO_MARKET=cn（默认，A股）/ us（美股）。各市场惯例集中此处，引擎读 market()，不硬编码。
MARKET = os.environ.get("CIO_MARKET", "cn").lower()
MARKET_PROFILE = {
    "cn": {"name": "A股", "lang": "zh", "currency": "¥", "news_region": "china",
           "tz": "Asia/Shanghai", "benchmark": "000300.SS", "bench_name": "沪深300",
           # 二部量化：基准取数标的 + 交易日历。cn 基准本身就是价格指数，个股用前复权，口径一致。
           "bench_source": "000300.SS", "bench_basis": "price_qfq", "calendar": "XSHG"},
    "us": {"name": "US", "lang": "en", "currency": "$", "news_region": "us",
           "tz": "America/New_York", "benchmark": "^GSPC", "bench_name": "S&P 500",
           # §Data Contract：基准用 SPY adjusted 作免费 total-return 代理（与个股 adjusted 同口径）；
           # 报告仍显示 "S&P 500"。日历用 XNYS（感恩节/圣诞半日/夏令时）。
           "bench_source": "SPY", "bench_basis": "total_return_proxy", "calendar": "XNYS"},
}


def market() -> dict:
    return MARKET_PROFILE.get(MARKET, MARKET_PROFILE["cn"])


# ---------------- 业务日期一律走【市场时区】----------------
# 铁律：机器所在时区已经走到第二天，不能改变研究对象所属的交易日。
#
# 这条曾经真的出过问题：她的 Mac 在纽约，18:28 EDT 跑盘后分析，
# 而代码用北京时间给成分快照命名 —— 北京已是次日 06:28，
# 于是同一份报告里出现 `as-of trade date 2026-08-24` 配 `snapshot sp500_2026-08-25`。
# 数字全对，但审计记录自相矛盾：一份描述 8/24 收盘的报告，
# 它的成分依据看起来来自一个还没发生的交易日。
#
# 因此：凡是【业务凭证的身份】（快照名、run_id、归档文件名）一律用市场时区的日期，
# 最好直接用 as_of_trade_date；UTC 时间戳只出现在 metadata 里。
def market_now():
    """市场本地时区的当前时刻。"""
    from datetime import datetime, timedelta, timezone
    name = market().get("tz", "America/New_York")
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(name)
    except Exception:                       # 无 tzdata 时的保守兜底
        tz = timezone(timedelta(hours=8 if name.startswith("Asia") else -4))
    return datetime.now(tz)


def market_date() -> str:
    """市场本地时区的今天（YYYY-MM-DD）。快照命名、日内归档都用它，绝不用机器本地时区。"""
    return market_now().strftime("%Y-%m-%d")


@lru_cache(maxsize=1)
def watchlist() -> dict:
    """市场感知：CIO_MARKET=us 且存在 watchlist_us.yaml 时用美股关注池，否则回退默认（A股）。"""
    path = CONFIG_DIR / f"watchlist_{MARKET}.yaml"
    if not path.exists():
        path = CONFIG_DIR / "watchlist.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


class Settings:
    """运行期设置（模型名、Telegram、开关）。全部可用 .env 覆盖。"""

    # Ollama（本地推理，严禁云端；原生端点，勿加 /v1）
    OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
    MODEL_BRIEF = os.environ.get("CIO_MODEL_BRIEF", "gpt-oss:20b")        # 编撰
    MODEL_LIGHT = os.environ.get("CIO_MODEL_LIGHT", "phi4-mini")          # 翻译/摘要/分类
    MODEL_EMBED = os.environ.get("CIO_MODEL_EMBED", "nomic-embed-text-v2-moe")

    # 离线自测：置 1 时不调 Ollama（翻译=原样、摘要=截断、向量=hash 伪向量）
    MOCK_LLM = os.environ.get("CIO_MOCK_LLM", "0") == "1"

    # Telegram（allowlist 已在 openclaw.json 层保证只对 CEO）
    TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    TG_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
    # 干跑：置 1 时不真发 Telegram，只打印
    TG_DRYRUN = os.environ.get("CIO_TG_DRYRUN", "0") == "1"

    # SEC EDGAR 要求带 User-Agent
    SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "CIO-Agent research contact@example.com")

    # 早报参数
    NEWS_PER_REGION = int(os.environ.get("CIO_NEWS_PER_REGION", "5"))     # 国际5 + 中国5
    TREND_MAX = int(os.environ.get("CIO_TREND_MAX", "12"))               # 趋势信号栏最多条数


settings = Settings()
