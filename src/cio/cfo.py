"""财务部（CFO）—— 纸面账户账本引擎（零 LLM，纯确定性 SQLite 记账）。

定位：后台职能，中立、机械、可审计。运营两个 ¥100 万纸面账户（一部/二部各一）+ 影子盘，
按 CEO 批准的选股建仓、每日盯市、出盈亏表，用真实盈亏检验两套模型。
红线：只纸面不实盘；盯市价必须真实收盘价，缺则标"缺价"绝不估算；账本只追加、可审计；本部门零 LLM。
"""
from __future__ import annotations

import math
import os
import sqlite3
from pathlib import Path

from .config import BASE
from .models import CollectionStatus, DailyPick, PnLAccount, PnLPosition, PnLStatement
from .utils import get_logger, stamp_beijing

log = get_logger("cio.cfo")

CAPITAL = float(os.environ.get("CIO_CFO_CAPITAL", "1000000"))
COMMISSION = float(os.environ.get("CIO_CFO_COMMISSION", "0.0003"))   # 佣金率（双边），最低5元
STAMP = float(os.environ.get("CIO_CFO_STAMP", "0.0005"))             # 卖出印花税
MODE = os.environ.get("CIO_CFO_MODE", "hold_month")                  # hold_month/rolling/daily_rotate
_MAIN = ["一部", "二部"]
_ALL = _MAIN + ["一部_shadow", "二部_shadow"]


def _db_path() -> Path:
    return Path(os.environ.get("CIO_CFO_DB", str(BASE / "cfo.db")))


def connect(db_path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or str(_db_path()))
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS accounts(
        account TEXT PRIMARY KEY, capital REAL, cash REAL);
    CREATE TABLE IF NOT EXISTS positions(
        id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT, code TEXT, name TEXT, source TEXT,
        book_date TEXT, cost REAL, shares INTEGER, commission REAL, open INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS marks(
        date TEXT, account TEXT, code TEXT, close REAL, market_value REAL, priced INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS ledger(
        date TEXT, account TEXT, cash REAL, holdings REAL, net_value REAL, day_pnl REAL, cum_return REAL);
    CREATE TABLE IF NOT EXISTS approvals(
        date TEXT, code TEXT, source TEXT, cro_vetoed INTEGER, ceo_approved INTEGER);
    CREATE TABLE IF NOT EXISTS bench(date TEXT PRIMARY KEY, close REAL);
    CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
    """)
    conn.commit()


def init_accounts(conn: sqlite3.Connection, capital: float = CAPITAL) -> None:
    """首次建库时初始化四个账户（两主 + 两影子）。已存在则不动（账本只追加）。"""
    cur = conn.execute("SELECT COUNT(*) c FROM accounts").fetchone()
    if cur["c"]:
        return
    for a in _ALL:
        conn.execute("INSERT INTO accounts(account,capital,cash) VALUES(?,?,?)", (a, capital, capital))
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('capital',?)", (str(capital),))
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('mode',?)", (MODE,))
    conn.commit()
    log.info("CFO 账本初始化：%s 各 ¥%.0f", "/".join(_ALL), capital)


def _commission(amount: float) -> float:
    return max(amount * COMMISSION, 5.0)


def book(conn: sqlite3.Connection, account: str, picks: list[DailyPick],
         date: str, prices: dict, n_slots: int | None = None) -> int:
    """按等权 + A股整手 + 佣金规则给某账户建仓。prices: {code: 收盘价}。
    n_slots 用于 hold_month（用固定份数分资金，如 3 只则每只用 1/3 初始资金）。返回建仓只数。"""
    row = conn.execute("SELECT cash FROM accounts WHERE account=?", (account,)).fetchone()
    if not row:
        return 0
    cash = row["cash"]
    slots = n_slots or len(picks) or 1
    per = cash / slots
    booked = 0
    for p in picks:
        px = prices.get(p.code)
        if not px or px <= 0:
            log.warning("建仓缺价，跳过 %s（%s）", p.code, account)
            continue
        lots = math.floor(per / (px * 100))          # 整手（100股）
        shares = lots * 100
        if shares <= 0:
            continue
        cost = shares * px
        comm = _commission(cost)
        if cost + comm > cash:
            continue
        conn.execute("INSERT INTO positions(account,code,name,source,book_date,cost,shares,commission,open)"
                     " VALUES(?,?,?,?,?,?,?,?,1)",
                     (account, p.code, p.name, p.source, date, px, shares, comm))
        cash -= (cost + comm)
        booked += 1
    conn.execute("UPDATE accounts SET cash=? WHERE account=?", (cash, account))
    conn.commit()
    return booked


def record_approvals(conn: sqlite3.Connection, date: str, rows: list[tuple]) -> None:
    """rows: [(code, source, cro_vetoed:int, ceo_approved:int)]，审批留痕。"""
    conn.executemany("INSERT INTO approvals(date,code,source,cro_vetoed,ceo_approved) VALUES(?,?,?,?,?)",
                     [(date, c, s, v, a) for (c, s, v, a) in rows])
    conn.commit()


def mark_and_settle(conn: sqlite3.Connection, date: str, prices: dict, bench_close: float | None = None) -> None:
    """对所有账户在 date 盯市 + 日结。prices: {code: 当日收盘价真值}。缺价的持仓沿用上一有效价并标注。
    幂等：同一天重跑会先清掉当日盯市/日结旧行再写，避免重复行（前一日净值仍作 prev 正确算当日盈亏）。"""
    if bench_close is not None:
        conn.execute("INSERT OR REPLACE INTO bench(date,close) VALUES(?,?)", (date, bench_close))
    conn.execute("DELETE FROM marks WHERE date=?", (date,))
    conn.execute("DELETE FROM ledger WHERE date=?", (date,))
    for account in _ALL:
        cap = conn.execute("SELECT capital,cash FROM accounts WHERE account=?", (account,)).fetchone()
        if not cap:
            continue
        cash = cap["cash"]
        holdings = 0.0
        for pos in conn.execute("SELECT * FROM positions WHERE account=? AND open=1", (account,)):
            px = prices.get(pos["code"])
            priced = 1
            if not px or px <= 0:                    # 缺价：沿用上一有效盯市价
                prev = conn.execute("SELECT close FROM marks WHERE account=? AND code=? AND priced=1 "
                                    "ORDER BY date DESC LIMIT 1", (account, pos["code"])).fetchone()
                px = prev["close"] if prev else pos["cost"]
                priced = 0
            mv = pos["shares"] * px
            holdings += mv
            conn.execute("INSERT INTO marks(date,account,code,close,market_value,priced) VALUES(?,?,?,?,?,?)",
                         (date, account, pos["code"], px, mv, priced))
        net = cash + holdings
        prev = conn.execute("SELECT net_value FROM ledger WHERE account=? ORDER BY date DESC LIMIT 1",
                            (account,)).fetchone()
        prev_net = prev["net_value"] if prev else cap["capital"]
        day_pnl = net - prev_net
        cum_ret = net / cap["capital"] - 1.0
        conn.execute("INSERT INTO ledger(date,account,cash,holdings,net_value,day_pnl,cum_return)"
                     " VALUES(?,?,?,?,?,?,?)", (date, account, cash, holdings, net, day_pnl, cum_ret))
    conn.commit()


def _bench_return(conn: sqlite3.Connection, as_of: str) -> float:
    rows = conn.execute("SELECT date,close FROM bench ORDER BY date").fetchall()
    if len(rows) < 1:
        return 0.0
    start = rows[0]["close"]
    cur = next((r["close"] for r in rows if r["date"] == as_of), rows[-1]["close"])
    return cur / start - 1.0 if start else 0.0


def build_statement(conn: sqlite3.Connection, as_of: str | None = None) -> PnLStatement:
    """出某日盈亏表（默认最新盯市日）。账户层 + 持仓层 + 对比层。"""
    last = conn.execute("SELECT MAX(date) d FROM ledger").fetchone()
    as_of = as_of or (last["d"] if last and last["d"] else stamp_beijing()[:10])
    bench_pct = _bench_return(conn, as_of)

    accounts: list[PnLAccount] = []
    for account in _MAIN:                            # 盈亏表主呈现两主账户；影子盘走对比层
        lg = conn.execute("SELECT * FROM ledger WHERE account=? AND date=?", (account, as_of)).fetchone()
        cap = conn.execute("SELECT capital,cash FROM accounts WHERE account=?", (account,)).fetchone()
        if not cap:
            continue
        if lg:
            net, cash, hold, day = lg["net_value"], lg["cash"], lg["holdings"], lg["day_pnl"]
        else:
            net, cash, hold, day = cap["capital"], cap["cash"], 0.0, 0.0
        ret = net / cap["capital"] - 1.0
        accounts.append(PnLAccount(account=account, capital=cap["capital"], cash=round(cash, 2),
                                   holdings=round(hold, 2), net_value=round(net, 2),
                                   pnl=round(net - cap["capital"], 2), pnl_pct=round(ret, 4),
                                   day_pnl=round(day, 2), bench_pct=round(bench_pct, 4),
                                   excess=round(ret - bench_pct, 4)))

    positions: list[PnLPosition] = []
    missing: list[str] = []
    for account in _MAIN:
        for pos in conn.execute("SELECT * FROM positions WHERE account=? AND open=1", (account,)):
            mk = conn.execute("SELECT close,priced FROM marks WHERE account=? AND code=? AND date=?",
                              (account, pos["code"], as_of)).fetchone()
            last_px = mk["close"] if mk else pos["cost"]
            priced = bool(mk["priced"]) if mk else False
            mv = pos["shares"] * last_px
            pnl = mv - pos["shares"] * pos["cost"]
            positions.append(PnLPosition(
                account=account, code=pos["code"], name=pos["name"], source=pos["source"],
                cost=round(pos["cost"], 3), last=round(last_px, 3), shares=pos["shares"],
                market_value=round(mv, 2), pnl=round(pnl, 2),
                pnl_pct=round(pnl / (pos["shares"] * pos["cost"]), 4) if pos["cost"] else 0.0,
                priced=priced))
            if not priced:
                missing.append(f"{account}·{pos['code']}")

    # 对比层：一部 vs 二部；主盘 vs 影子盘
    def _net(a):
        r = conn.execute("SELECT net_value FROM ledger WHERE account=? AND date=?", (a, as_of)).fetchone()
        c = conn.execute("SELECT capital FROM accounts WHERE account=?", (a,)).fetchone()
        return (r["net_value"] if r else (c["capital"] if c else CAPITAL))
    cmp_parts = []
    a_net, b_net = _net("一部"), _net("二部")
    cmp_parts.append(f"一部 {a_net/CAPITAL-1:+.2%} vs 二部 {b_net/CAPITAL-1:+.2%}（{'一部' if a_net>b_net else '二部'}领先）")
    for m in _MAIN:
        sh = _net(f"{m}_shadow")
        mn = _net(m)
        cmp_parts.append(f"{m}：主盘 {mn/CAPITAL-1:+.2%} vs 影子盘 {sh/CAPITAL-1:+.2%}（终批{'+' if mn>=sh else '-'}{abs(mn-sh)/CAPITAL:.2%}）")

    cov = CollectionStatus(fetched=len(positions), degraded=(["缺价:" + "/".join(missing)] if missing else []))
    return PnLStatement(dt_beijing=stamp_beijing(), as_of=as_of, mode=MODE, accounts=accounts,
                        positions=positions, compare_note=" ｜ ".join(cmp_parts),
                        missing_prices=missing, status=cov)


def archive_and_render(st: PnLStatement) -> tuple[str, str]:
    from .config import TOPIC_DIR
    from .render import render_cfo_md, render_cfo_pdf
    from .utils import file_stamp, safe_filename
    stamp = file_stamp()
    base = f"{safe_filename('财务部盈亏表')}+{stamp}"
    md_path = TOPIC_DIR / f"{base}.md"
    pdf_path = TOPIC_DIR / f"{base}.pdf"
    md_path.write_text(render_cfo_md(st), encoding="utf-8")
    try:
        render_cfo_pdf(st, str(pdf_path))
    except Exception as e:
        log.error("盈亏表 PDF 渲染失败: %s", e)
        pdf_path = None
    from . import db
    db.init_db()
    db.insert_brief("cfo", f"《财务部盈亏表 {st.as_of}》", str(md_path), str(pdf_path or ""))
    return str(md_path), str(pdf_path or "")
