"""US_PAPER 账本 —— **事实层**（零 LLM、零判断，只记发生过什么）。

四层里的最后一层，也是唯一一层"真值"：

    PC 产生目标   →   CEO 产生授权   →   Execution 产生交易   →   Ledger 产生事实
                                                                    ↑ 本模块

四层不能合并。合并的具体后果是：目标一变，历史持仓跟着变——
于是"我们当时持有什么"这个问题**再也没有答案**，而且不会有任何一处报错。

## 为什么不复用 cfo.py

`cfo.py` 是给已经退役的 A 股试点写的，整套度量衡都不同：

    6 位数字代码        美股是字母 ticker
    整手 100 股         美股按 1 股
    双边佣金 + 印花税    美股零佣金
    沪深300 基准        美股要 SPY 总回报
    人民币              美元

**这些差别没有一处会抛异常。** 把 NVDA 塞进按整手取整的建仓逻辑里，
`floor(per / (px*100)) * 100` 会算出一个 100 的倍数，程序愉快地跑完，
账本里出现一笔 700 股的持仓，而正确答案是 7 股。

所以是**新开一本账**，不是迁移旧账。旧的 cfo.db 原样封存，
US_PAPER 从一个**写明的日期、写明的初始资金**重新开账——
真实世界里换策略不会去转换老基金的历史，是发一只新基金。

## 三条纪律

一、**NAV 缺一个价就是算不出来，不是"按剩下的算"。**
    漏掉一只票的市值，NAV 会小一截，而这一截看起来完全正常——
    净值曲线上不会有缺口，只会有一个凭空出现的亏损日。
    所以 `nav()` 在有持仓取不到价时返回 `nav=None` 并列出是哪几只。

二、**每一行持仓、每一笔交易都带 run_id 和 portfolio_id。**
    没有 run_id，三个月后看到某笔亏 8%，就再也回不到"这是哪次运行、
    哪条论点、当时哪些材料"。账本和台账会变成两本对不上的账。

三、**账本只追加。** 平仓是把 open 置 0 并记一笔反向 trade，
    不是删除持仓行。删掉的那一刻，业绩归因的分母就没了。
"""
from __future__ import annotations

import os
import re
from datetime import date

from .db import connect
from .utils import get_logger, stamp_utc

log = get_logger("cio.book")

# ---------------------------------------------------------------- 美股口径常数
CURRENCY = "USD"
LOT_SIZE = 1                    # 美股按 1 股，**不是 100**
BENCHMARK_SYMBOL = "SPY"
BENCHMARK_BASIS = "TOTAL_RETURN"
"""基准必须是**含息总回报**。

组合会收到分红（Build 3 记为现金入账）。拿 SPY 的**价格收益**去比，
等于让组合白拿分红而基准不拿，凭空多出每年约 1.5% 的假 alpha——
一个完全正常、完全错误的超额收益数字。

这里存的是**要求**。Build 3 的 P&L 层在能兑现这个口径之前，
必须拒绝输出超额收益，而不是先用价格收益顶上。
"""

DEFAULT_CAPITAL = float(os.environ.get("CIO_BOOK_CAPITAL", "100000"))

# 美股零佣金是真实的。**滑点在 v1 记 0，但字段存在并显示为 0**——
# 编一个 5bps 看着像建模，其实是猜，而且会让 P&L 变得没法证伪：
# 收益差了 0.3%，你分不清是策略还是那个拍出来的数。
COMMISSION_PER_TRADE = 0.0
SLIPPAGE_BPS = 0.0

_A_SHARE_CODE = re.compile(r"^\d{6}$")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS book_portfolio (
    portfolio_id      TEXT PRIMARY KEY,
    opened_on         TEXT,      -- 开账日：写明，不从"最早一笔交易"倒推
    currency          TEXT,
    lot_size          INTEGER,
    initial_capital   REAL,
    cash              REAL,
    benchmark_symbol  TEXT,
    benchmark_basis   TEXT,
    created_at        TEXT,
    note              TEXT
);
CREATE TABLE IF NOT EXISTS book_position (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_id      TEXT,
    ticker            TEXT,
    shares            INTEGER,
    avg_cost          REAL,
    opened_on         TEXT,
    opened_run_id     TEXT,      -- 建仓那次运行 → 可回溯到 pc_lineage
    last_run_id       TEXT,      -- 最近一次对它做出判断的运行
    last_evaluated_on TEXT,      -- 最近一次复审日期 → 多久没看了
    closed_on         TEXT,
    realized_pnl      REAL DEFAULT 0,
    open              INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS book_trade (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                TEXT,
    portfolio_id          TEXT,
    ticker                TEXT,
    side                  TEXT,          -- BUY / SELL
    shares                INTEGER,
    decision_date         TEXT,
    decision_time         TEXT,
    decision_price        REAL,
    execution_date        TEXT,
    execution_time        TEXT,
    execution_price       REAL,
    execution_price_basis TEXT,          -- T+1_OPEN
    commission            REAL DEFAULT 0,
    slippage              REAL DEFAULT 0,
    cash_flow             REAL,          -- 负=买入付出，正=卖出收到
    proposal_id           INTEGER,
    realized_pnl          REAL,
    created_at            TEXT
);
CREATE TABLE IF NOT EXISTS book_corp_action (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_id      TEXT,
    ticker            TEXT,
    kind              TEXT,      -- SPLIT / DIVIDEND
    ex_date           TEXT,
    ratio             REAL,      -- 拆股比例（4:1 → 4.0；1:10 反向 → 0.1）
    amount_per_share  REAL,      -- 每股分红
    shares_before     INTEGER,
    shares_after      INTEGER,
    avg_cost_before   REAL,
    avg_cost_after    REAL,
    cash_delta        REAL,      -- 分红入账 / 拆股零头折现
    ex_close          REAL,
    applied_at        TEXT,
    note              TEXT
);
CREATE TABLE IF NOT EXISTS book_mark (
    date          TEXT,
    portfolio_id  TEXT,
    ticker        TEXT,
    close         REAL,
    shares        INTEGER,
    market_value  REAL,
    priced        INTEGER DEFAULT 1,
    PRIMARY KEY (date, portfolio_id, ticker)
);
CREATE TABLE IF NOT EXISTS book_nav (
    date             TEXT,
    portfolio_id     TEXT,
    cash             REAL,
    holdings_value   REAL,
    nav              REAL,      -- 缺价时为 NULL（**不按剩下的算**）
    day_pnl          REAL,      -- 前一日不完整时为 NULL（不把两天当一天）
    cum_return       REAL,
    bench_close      REAL,
    bench_cum_return REAL,
    bench_basis      TEXT,
    invested_pct     REAL,
    n_positions      INTEGER,
    n_unpriced       INTEGER,
    complete         INTEGER DEFAULT 1,
    note             TEXT,
    created_at       TEXT,
    PRIMARY KEY (date, portfolio_id)
);
"""

# **索引与建表分开，在补列之后。** 混在一起时，旧库缺某列会让整段脚本抛
# `no such column`，而紧随其后的补列迁移**永远跑不到**（build87 真实事故）。
_INDEXES = """
CREATE UNIQUE INDEX IF NOT EXISTS ux_book_position_open
    ON book_position(portfolio_id, ticker) WHERE open=1;
CREATE UNIQUE INDEX IF NOT EXISTS ux_book_trade_run
    ON book_trade(run_id, portfolio_id, ticker);
CREATE INDEX IF NOT EXISTS ix_book_trade_pid ON book_trade(portfolio_id, execution_date);
CREATE UNIQUE INDEX IF NOT EXISTS ux_book_corp_action
    ON book_corp_action(portfolio_id, ticker, kind, ex_date);
CREATE INDEX IF NOT EXISTS ix_book_nav_pid ON book_nav(portfolio_id, date);
"""

# 后续版本给旧库补列写这里，**不要**改上面的 CREATE TABLE ——
# 那对已存在的表没有任何作用。
_MIGRATIONS = {
    "book_trade": {"realized_pnl": "REAL"},
    "book_position": {"realized_pnl": "REAL"},
    "book_portfolio": {"bench_open_close": "REAL"},
}


def init() -> None:
    """**建表 → 补列 → 建索引**。幂等。"""
    from .db import ensure_columns
    with connect() as con:
        con.executescript(_SCHEMA)
        for table, cols in _MIGRATIONS.items():
            added = ensure_columns(con, table, cols)
            if added:
                log.info("%s 已补列：%s", table, "、".join(added))
        con.executescript(_INDEXES)


def assert_us_ticker(ticker: str) -> str:
    """**6 位纯数字进美股账本是口径错误，必须抛，不能记进去。**

    不抛的后果不是崩溃，是账本里躺着一只 `002371`，
    盯市时按美股源取不到价 → 标"缺价" → 沿用成本价 → 它的盈亏永远是 0，
    看起来像一只特别稳的股票。
    """
    t = str(ticker or "").strip().upper()
    if not t:
        raise ValueError("ticker 为空")
    if _A_SHARE_CODE.match(t):
        raise ValueError(f"{t} 是 6 位 A 股代码，不能记入美股账本 —— "
                         f"A 股试点账在 cfo.db，已封存，不迁移")
    return t


def is_book_portfolio(portfolio_id: str) -> bool:
    """这个 portfolio 是否由**新账本**管。

    存在的理由：`portfolio.open_positions()` 要据此路由。
    同一个 portfolio_id 既在新账本又在旧 cfo.db 里，
    两个模块会各自"正常地"给出不同的持仓——`assert_single_source()` 守这条。
    """
    init()
    with connect() as con:
        row = con.execute("SELECT 1 FROM book_portfolio WHERE portfolio_id=?",
                          (portfolio_id,)).fetchone()
    return bool(row)


def _assert_not_future(day: str) -> None:
    """开账日不能在未来。

    真机上发生过：照着 README 的例子填了 `--opened-on 2026-09-01`，
    而当天是 08-31。于是账本的开账日在明天，**每一次盯市都会被
    "盯市日早于开账日" 挡下来**，而报错信息指向盯市，不指向开账。
    """
    from .config import market_date
    today = str(market_date())[:10]
    d = str(day)[:10]
    if d > today:
        raise ValueError(
            f"开账日 {d} 在未来（今天 {today}）—— 账本不能在明天开张。"
            f"每一次盯市都会被挡下，而报错会指向盯市而不是这里。")


def is_untouched(portfolio_id: str) -> bool:
    """这本账还没发生过任何事：没有交易、没有持仓、没有净值记录。

    只有这种账才允许改开账日 —— 有交易之后再改，历史区间和收益率的
    分母就全变了，而每一个数字看起来仍然正常。
    """
    init()
    with connect() as con:
        for t, col in (("book_trade", "portfolio_id"), ("book_position", "portfolio_id"),
                       ("book_nav", "portfolio_id"), ("book_corp_action", "portfolio_id")):
            n = con.execute(f"SELECT COUNT(*) FROM {t} WHERE {col}=?",
                            (portfolio_id,)).fetchone()[0]
            if n:
                return False
    return True


def open_book(portfolio_id: str, *, capital: float = None, opened_on: str = "",
              note: str = "", reset_open_date: bool = False) -> dict:
    """开账。**已存在则原样返回，绝不覆盖**——重跑一次开账命令不该重置资金。

    开账日和初始资金是**写进去的事实**，不是从最早一笔交易倒推的。
    倒推在没有交易时得不到答案，而"这本账什么时候开的"必须永远有答案。

    `reset_open_date=True` 只用来**改正填错的开账日**，且仅在这本账
    还没发生过任何事时生效（见 `is_untouched`）。
    """
    init()
    cur = portfolio_row(portfolio_id)
    if cur:
        if reset_open_date and opened_on and opened_on != cur["opened_on"]:
            if not is_untouched(portfolio_id):
                raise ValueError(
                    f"{portfolio_id} 已经有交易/持仓/净值记录，**不能改开账日** —— "
                    f"改了之后历史区间和收益率的分母全变，而数字看起来仍然正常。")
            _assert_not_future(opened_on)
            with connect() as con:
                con.execute("UPDATE book_portfolio SET opened_on=?, bench_open_close=NULL "
                            "WHERE portfolio_id=?", (opened_on, portfolio_id))
            log.warning("%s 开账日改为 %s（原 %s）—— 这本账还没发生过任何事，"
                        "基准锚点一并清空重取", portfolio_id, opened_on, cur["opened_on"])
            return portfolio_row(portfolio_id)
        log.info("%s 已开账（%s，初始 %s %.2f），本次不改动",
                 portfolio_id, cur["opened_on"], cur["currency"], cur["initial_capital"])
        return cur
    cap = float(DEFAULT_CAPITAL if capital is None else capital)
    day = opened_on or date.today().isoformat()
    _assert_not_future(day)
    with connect() as con:
        con.execute(
            "INSERT INTO book_portfolio(portfolio_id,opened_on,currency,lot_size,"
            "initial_capital,cash,benchmark_symbol,benchmark_basis,created_at,note) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (portfolio_id, day, CURRENCY, LOT_SIZE, cap, cap,
             BENCHMARK_SYMBOL, BENCHMARK_BASIS, stamp_utc(), note))
    log.info("%s 开账：%s 起，初始资金 %s %.2f，基准 %s（%s）",
             portfolio_id, day, CURRENCY, cap, BENCHMARK_SYMBOL, BENCHMARK_BASIS)
    return portfolio_row(portfolio_id)


def portfolio_row(portfolio_id: str) -> dict:
    init()
    with connect() as con:
        r = con.execute("SELECT * FROM book_portfolio WHERE portfolio_id=?",
                        (portfolio_id,)).fetchone()
    return dict(r) if r else {}


def cash(portfolio_id: str):
    """可用现金。**账没开就是 None（不知道），不是 0（没钱）。**

    0 会让 rebalance 算出"现金不足，全部买入取消"——一个理由充分、
    完全错误的结论；None 会让它说"账本没开"，那才是真的。
    """
    p = portfolio_row(portfolio_id)
    return None if not p else float(p.get("cash") or 0.0)


def holdings(portfolio_id: str) -> list:
    """未平仓持仓。**必须传 portfolio_id，没有"读全部"这个入口。**"""
    if not portfolio_id:
        raise ValueError("holdings 必须指定 portfolio_id —— 没有『读全部』这个选项")
    init()
    with connect() as con:
        rows = list(con.execute(
            "SELECT ticker, shares, avg_cost, opened_on, opened_run_id, "
            "last_run_id, last_evaluated_on FROM book_position "
            "WHERE portfolio_id=? AND open=1 ORDER BY ticker", (portfolio_id,)))
    return [{"ticker": r[0], "shares": int(r[1] or 0), "avg_cost": float(r[2] or 0.0),
             "opened_on": r[3] or "", "opened_run_id": r[4] or "",
             "last_run_id": r[5] or "", "last_evaluated_on": r[6] or ""}
            for r in rows]


def holdings_map(portfolio_id: str) -> dict:
    return {h["ticker"]: h for h in holdings(portfolio_id)}


def nav(portfolio_id: str, prices: dict = None) -> dict:
    """净值 = 现金 + 持仓市值。

    **有持仓取不到价 → nav 为 None，并列出是哪几只。**
    "按能取到价的那部分算"会得到一个偏小的 NAV，而它在净值曲线上
    表现为一个凭空出现的亏损日，没有缺口、没有报错、没法事后分辨。

    没有持仓时 NAV = 现金，这是确定的（不是"缺价"）。
    """
    p = portfolio_row(portfolio_id)
    if not p:
        return {"portfolio_id": portfolio_id, "opened": False, "nav": None,
                "cash": None, "holdings_value": None, "unpriced": [],
                "note": "账本未开 —— 先跑 run_rebalance.py --open-book"}
    px = {str(k).upper(): v for k, v in (prices or {}).items()}
    hs = holdings(portfolio_id)
    mv, unpriced, lines = 0.0, [], []
    for h in hs:
        v = px.get(h["ticker"])
        if v is None or float(v) <= 0:
            unpriced.append(h["ticker"])
            continue
        m = h["shares"] * float(v)
        mv += m
        lines.append({"ticker": h["ticker"], "shares": h["shares"],
                      "price": float(v), "market_value": m,
                      "avg_cost": h["avg_cost"],
                      "last_evaluated_on": h["last_evaluated_on"]})
    c = float(p["cash"] or 0.0)
    ok = not unpriced
    return {"portfolio_id": portfolio_id, "opened": True,
            "currency": p["currency"], "opened_on": p["opened_on"],
            "initial_capital": float(p["initial_capital"] or 0.0),
            "cash": c,
            "holdings_value": (mv if ok else None),
            "nav": (c + mv if ok else None),
            "n_positions": len(hs), "positions": lines, "unpriced": unpriced,
            "note": ("" if ok else
                     f"{len(unpriced)} 只持仓取不到价（{'、'.join(unpriced)}）"
                     f"—— NAV 不可计算。**不按剩下的算**：那会得到一个偏小的"
                     f"净值，在曲线上表现为凭空出现的亏损日。")}


def mark_evaluated(portfolio_id: str, tickers, on: str, run_id: str) -> int:
    """记下"这几只持仓在 `on` 被复审过"。返回更新条数。

    这是 `days_since_evaluated` 的来源，也是那句
    "已 40 天未复审 —— 该回去看一眼当初的失效条件"能被说出口的前提。
    **只标本轮真正做出过判断的**，不能因为跑了一次就把全部持仓刷新——
    那样这个字段会永远显示 0 天，等于没有。
    """
    ts = [str(t).upper() for t in (tickers or [])]
    if not ts:
        return 0
    init()
    n = 0
    with connect() as con:
        for t in ts:
            cur = con.execute(
                "UPDATE book_position SET last_evaluated_on=?, last_run_id=? "
                "WHERE portfolio_id=? AND ticker=? AND open=1",
                (str(on)[:10], run_id, portfolio_id, t))
            n += cur.rowcount
    return n


def assert_single_source(portfolio_id: str) -> None:
    """同一个 portfolio 不能同时存在于新账本和旧 cfo.db。

    两处都有时，`book.holdings()` 和 `cfo` 的 positions 表会各自给出
    一份持仓，**两份都是正常数据**，谁都不报错，而按它们算出的集中度、
    行业占用、已用风险预算全都不一样。
    """
    if not is_book_portfolio(portfolio_id):
        return
    try:
        from . import cfo
        conn = cfo.connect()
        cfo.init_schema(conn)
        have = {r[1] for r in conn.execute("PRAGMA table_info(positions)")}
        if "portfolio_id" not in have:
            conn.close()
            return
        n = conn.execute("SELECT COUNT(*) FROM positions WHERE open=1 AND portfolio_id=?",
                         (portfolio_id,)).fetchone()[0]
        conn.close()
    except Exception as e:                                   # noqa: BLE001
        log.info("旧账 cfo.db 不可读（不影响新账本）：%s", e)
        return
    if n:
        raise RuntimeError(
            f"{portfolio_id} 同时存在于新账本和旧 cfo.db（旧账 {n} 笔 open）——"
            f"两个真源会给出两份都『正常』的持仓。请把旧账里这些行归入 "
            f"LEGACY_A_SHARE_PAPER，或改用别的 portfolio_id。")


def render(portfolio_id: str, prices: dict = None) -> str:
    """账本一屏。**开没开账、有没有缺价，都要看得见。**"""
    n = nav(portfolio_id, prices)
    if not n["opened"]:
        return f"账本 {portfolio_id}：未开账。{n['note']}"
    p = portfolio_row(portfolio_id)
    L = [f"账本 {portfolio_id}　{p['currency']}　{p['opened_on']} 开账　"
         f"初始 {n['initial_capital']:,.2f}　每手 {p['lot_size']} 股",
         f"基准 {p['benchmark_symbol']}（{p['benchmark_basis']}）"]
    if n["nav"] is None:
        L.append(f"现金 {n['cash']:,.2f}　持仓 {n['n_positions']} 笔　"
                 f"NAV **不可计算** —— {n['note']}")
    else:
        L.append(f"现金 {n['cash']:,.2f}　持仓市值 {n['holdings_value']:,.2f}　"
                 f"NAV {n['nav']:,.2f}")
    for r in n["positions"]:
        L.append(f"  {r['ticker']:<6} {r['shares']:>6} 股 @ {r['price']:>9,.2f}"
                 f"　市值 {r['market_value']:>12,.2f}"
                 f"　成本 {r['avg_cost']:,.2f}"
                 f"　上次复审 {r['last_evaluated_on'] or '（无记录）'}")
    for t in n["unpriced"]:
        L.append(f"  {t:<6} **取不到价** —— 不计入市值，NAV 因此不可计算")
    return "\n".join(L)
