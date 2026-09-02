"""持仓的唯一真源（Portfolio Ledger）。

**架构冻结 v1.0：CRO 只能从这里读持仓，不能从二部报告里那句
`3 open positions on file` 推断组合真值。**

核心纪律：**每一个 position 必须归属明确的 portfolio / account。**

不用 `NULL` 也不用 `UNKNOWN`——那两个值迟早会被某个 `WHERE portfolio_id IS NULL
OR portfolio_id = ?` 之类的查询意外扫进风险计算，而且不会报错。
迁移时按市场推断一个**显式**的 id，推断规则写在代码里、可审计。

现有三笔 A 股持仓（688012 / 002371 / 300308）归入 `LEGACY_A_SHARE_PAPER`：
现有信息只证明它们是 A 股 open positions，**不能证明它们属于当前美股组合**。
以后若确认同属一个总基金，通过 portfolio hierarchy 汇总做跨市场集中度分析，
而不是现在就把它们混进美股风险判断。
"""
from __future__ import annotations

import re

from .utils import get_logger

log = get_logger("cio.portfolio")

LEGACY_A_SHARE = "LEGACY_A_SHARE_PAPER"
US_PAPER = "US_PAPER"
CN_PAPER = "CN_PAPER"

# 市场 → 默认 portfolio。CRO 跑哪个市场就只读哪个 portfolio。
MARKET_PORTFOLIO = {"us": US_PAPER, "cn": CN_PAPER}

_A_SHARE_CODE = re.compile(r"^\d{6}$")


def infer_portfolio_id(code: str, book_date: str = "") -> str:
    """按标的代码推断 portfolio。**规则显式、可审计，不产生 NULL/UNKNOWN。**

    6 位纯数字 = A 股代码。迁移时这批一律进 LEGACY_A_SHARE_PAPER——
    它们是本次冻结之前留下的记录，来源不明确，**默认不参与任何风险计算**
    比默认参与安全得多。
    """
    return LEGACY_A_SHARE if _A_SHARE_CODE.match(str(code or "").strip()) else US_PAPER


def ensure_schema(conn) -> int:
    """给 positions 补 portfolio_id 列并回填。幂等。返回回填条数。"""
    have = {r[1] for r in conn.execute("PRAGMA table_info(positions)")}
    if "portfolio_id" not in have:
        conn.execute("ALTER TABLE positions ADD COLUMN portfolio_id TEXT DEFAULT ''")
        log.info("持仓台账已补列 portfolio_id")
    rows = list(conn.execute(
        "SELECT id, code, book_date FROM positions "
        "WHERE portfolio_id IS NULL OR portfolio_id=''"))
    for pid, code, bdate in rows:
        conn.execute("UPDATE positions SET portfolio_id=? WHERE id=?",
                     (infer_portfolio_id(code, bdate or ""), pid))
    if rows:
        conn.commit()
        log.info("持仓台账回填 portfolio_id：%d 条（A 股 → %s，其余 → %s）",
                 len(rows), LEGACY_A_SHARE, US_PAPER)
    return len(rows)


# 影子账户：run_pilot 给每个部门额外记一套纸面镜像（cfo._ALL 里的 一部_shadow /
# 二部_shadow）。**它们和真实账户持有同样的标的，计入聚合就是成倍虚增。**
#
# analytics.py 早就把它们排除了（`not str(p["account"]).endswith("_shadow")`），
# 而 portfolio.open_positions 没有——**同一批行在两个模块里被当成不同的东西**，
# 于是两边算出的集中度、行业占用会各自"正常"地给出不同的数字，谁都不报错。
# 今天还看不出来，是因为影子仓在 A 股组合里、没进美股风险计算；
# 等 sector_used / theme_used 开始从持仓算，每一项暴露都会正好翻倍。
SHADOW_SUFFIX = "_shadow"


def is_shadow(account: str) -> bool:
    return str(account or "").endswith(SHADOW_SUFFIX)


def open_positions(portfolio_id: str, include_shadow: bool = False) -> list:
    """读取指定 portfolio 的未平仓持仓。**默认排除影子账户，且排除要出声。**

    **必须传 portfolio_id，没有"读全部"这个选项。** 提供一个默认读全部的入口，
    就等于给未来某次调用留了把 A 股测试仓位混进美股风险判断的机会。
    要做跨组合分析，显式列出要合并哪几个 portfolio。
    """
    if not portfolio_id:
        raise ValueError("open_positions 必须指定 portfolio_id —— 没有『读全部』这个选项")

    # **路由，不是合并。** US_PAPER 归新账本（book.py，美股口径）管，
    # 旧的 A 股试点仍在 cfo.db 里。同一个 portfolio 出现在两处时
    # `assert_single_source` 会抛——两个真源各自给出一份都『正常』的持仓，
    # 是这套系统最难发现的一类错误。
    from . import book
    if book.is_book_portfolio(portfolio_id):
        book.assert_single_source(portfolio_id)
        return [{"account": portfolio_id, "code": h["ticker"], "ticker": h["ticker"],
                 "name": h["ticker"], "cost": h["avg_cost"], "shares": h["shares"],
                 "portfolio_id": portfolio_id, "is_shadow": False,
                 "opened_on": h["opened_on"], "opened_run_id": h["opened_run_id"],
                 "last_run_id": h["last_run_id"],
                 "last_evaluated_on": h["last_evaluated_on"]}
                for h in book.holdings(portfolio_id)]

    try:
        from . import cfo
        conn = cfo.connect()
        cfo.init_schema(conn)
        ensure_schema(conn)
        rows = list(conn.execute(
            "SELECT account, code, name, cost, shares, portfolio_id FROM positions "
            "WHERE open=1 AND portfolio_id=?", (portfolio_id,)))
        conn.close()
    except Exception as e:
        log.info("未读到持仓台账（组合层将不渲染）：%s", e)
        return []
    out = [{"account": r[0], "code": str(r[1]).upper(), "name": r[2],
            "cost": float(r[3] or 0), "shares": int(r[4] or 0), "portfolio_id": r[5],
            "is_shadow": is_shadow(r[0])} for r in rows]
    if include_shadow:
        return out
    kept = [p for p in out if not p["is_shadow"]]
    dropped = len(out) - len(kept)
    if dropped:
        # **排除必须出声。** 静默地少算一半持仓，和静默地多算一倍一样危险。
        log.warning("%s：排除 %d 笔影子账户持仓（%s）——影子账户是纸面镜像，"
                    "计入聚合会让集中度与行业占用成倍虚增", portfolio_id, dropped,
                    "、".join(sorted({p["account"] for p in out if p["is_shadow"]})))
    return kept


def summary() -> list:
    """所有 portfolio 的持仓分布，供人核对归属是否正确。

    **按 (portfolio, account) 分组，不是只按 portfolio 分组。** 只按 portfolio
    汇总时，两个账户各持同一只票会显示成"6 笔：688012、002371、300308、
    688012、002371、300308"——读起来像台账重复，实际上是两个账户，
    而这两种情况的处理方式完全相反（一个要去重，一个绝不能去重）。
    """
    try:
        from . import cfo
        conn = cfo.connect()
        cfo.init_schema(conn)
        ensure_schema(conn)
        rows = list(conn.execute(
            "SELECT portfolio_id, COALESCE(account,''), COUNT(*), GROUP_CONCAT(code) "
            "FROM positions WHERE open=1 GROUP BY portfolio_id, account "
            "ORDER BY portfolio_id, account"))
        conn.close()
    except Exception as e:
        log.info("持仓台账不可读：%s", e)
        return []
    out = {}
    for pid, acct, n, codes in rows:
        cl = [c for c in (codes or "").split(",") if c]
        d = out.setdefault(pid, {"portfolio_id": pid, "n": 0, "n_real": 0,
                                 "codes": [], "accounts": []})
        d["n"] += n
        d["n_real"] += 0 if is_shadow(acct) else n
        d["codes"] += cl
        d["accounts"].append({"account": acct or "（未填账户）", "n": n, "codes": cl,
                              "is_shadow": is_shadow(acct)})

    # 新账本里的组合也要出现在这张总表上。**漏掉的后果是"看起来一笔持仓都没有"**，
    # 而那和"确实一笔都没有"在页面上完全一样。
    try:
        from . import book
        book.init()
        from .db import connect as _c
        with _c() as con:
            bp = [r[0] for r in con.execute("SELECT portfolio_id FROM book_portfolio")]
        for pid in bp:
            hs = book.holdings(pid)
            d = out.setdefault(pid, {"portfolio_id": pid, "n": 0, "n_real": 0,
                                     "codes": [], "accounts": []})
            d["n"] += len(hs)
            d["n_real"] += len(hs)
            d["codes"] += [h["ticker"] for h in hs]
            d["accounts"].append({"account": f"{pid}（新账本）", "n": len(hs),
                                  "codes": [h["ticker"] for h in hs],
                                  "is_shadow": False})
    except Exception as e:                                   # noqa: BLE001
        log.info("新账本不可读（旧账部分仍已列出）：%s", e)
    return list(out.values())


def duplicates(portfolio_id: str = "") -> list:
    """同一 (portfolio, account, code) 有多条 open=1 记录 —— **台账重复**。

    这必须被检出而不是被汇总掉：重复的 open 行会让任何按持仓聚合的口径
    （集中度、行业占用、已用风险预算）成倍虚增，**而且每一个数字都是正常数字**。
    """
    try:
        from . import cfo
        conn = cfo.connect()
        cfo.init_schema(conn)
        ensure_schema(conn)
        q = ("SELECT portfolio_id, COALESCE(account,''), code, COUNT(*) FROM positions "
             "WHERE open=1 " + ("AND portfolio_id=? " if portfolio_id else "")
             + "GROUP BY portfolio_id, account, code HAVING COUNT(*)>1")
        rows = list(conn.execute(q, (portfolio_id,) if portfolio_id else ()))
        conn.close()
    except Exception as e:
        log.info("持仓台账不可读：%s", e)
        return []
    return [{"portfolio_id": r[0], "account": r[1] or "（未填账户）",
             "code": str(r[2]).upper(), "n": r[3]} for r in rows]
