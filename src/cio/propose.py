"""PC 那一次决策 → 逐票指令 → 事前合规 → 落提案。**只有这一份实现。**

## 为什么把它从 run_rebalance.py 里抽出来

build122 让自动流水线也要产生提案。最省事的写法是在流水线里再写一遍
「取决策 → 取价 → 算 NAV → 算股数 → 跑合规 → 落库」——**那是六步等价代码。**

上一版（build121）刚被同一个毛病咬过：`_research` 里那句"没有新的基本面事实"
判了两处，测试只走得到其中一处，**删掉另一处照样绿，而删掉的偏偏是真跑那处。**

两处等价的代码 = 一个测得到、一个测不到。所以：

    run_rebalance.py（手动）  ─┐
                              ├─→ propose.for_run()  ← 只有这一份
    research/pipeline.py（自动）┘

## 这里不做的两件事

**一、不批准。** 本模块只写到 `PENDING_APPROVAL`。
`proposal_store.record()` 自己决定初始状态（有指令 → 待批，没指令 → NO_TRADE），
**这里一行 APPROVED 都没有**，而且有一条探针专门钉着这件事。

**二、不重跑 PC。** 只读**已落库**的那一次决策。重跑会得到一份新的决策
（测量更新了、regime 变了），于是 CEO 批准的东西和台账里记的不是同一件事——
两份都正常、都可读、都对不上，而且没有任何一处报错。

## 状态，不是异常

账本没开、没有 PC 运行——这两件事**不是故障**，是很常见的正常状态。
抛异常的话，自动流水线会把它记成"这一步失败了"，而人看到"失败"
第一反应是去查代码。所以它们各自是一个 `status`，带一句人话。
"""
from __future__ import annotations

from .utils import get_logger

log = get_logger("cio.propose")

BOOK_NOT_OPEN = "book_not_open"
NO_PC_RUN = "no_pc_run"
COMPLETED = "completed"

STATUSES = (BOOK_NOT_OPEN, NO_PC_RUN, COMPLETED)


def for_run(*, portfolio_id: str, as_of: str, run_id: str = "",
            actor: str = "system", expire_first: bool = True,
            before_record=None) -> dict:
    """把某一次 PC 决策变成提案。返回一份**结构化结果**，不打印任何东西。

    参数
      run_id  空 = 用该组合最近一次 PC 运行
      actor   写进 proposal_event 的 actor（谁做的这次提案动作）
      before_record
              **先序列化，后落库**用的钩子：指令与合规都算完、
              但**一条都还没写库**时调一次，参数是当前的 `out`。
              调用方在这里做序列化（会炸就在这里炸），此时整次运行
              干净地失败，重试是安全的。反过来的话存在这样一条路径：
              提案已写库 → 序列化炸了 → 界面判定失败 → 用户点重试
              → 库里两条一模一样的提案。
              **这条纪律必须留在库里，不能留在某一个入口里** ——
              留在入口里，另一个入口就会漏掉它。

    返回
      {status, note, run_id, as_of, portfolio_id, expired[], decisions[],
       rows[], summary{}, compliance{}, saved[], expires, nav{}, prices{},
       price_detail{}, held{}, renders{}}

    `renders` 里是给人看的几段文字（价格说明 / 账本 / 指令清单 / 合规），
    **调用方决定印不印**——库不打印，这样自动流水线可以只把它放进心跳。
    """
    from . import book, compliance, marks, pc_ledger, proposal_store, rebalance

    out = {"status": "", "note": "", "run_id": run_id, "as_of": str(as_of)[:10],
           "portfolio_id": portfolio_id, "expired": [], "decisions": [],
           "rows": [], "summary": {}, "compliance": {}, "saved": [],
           "expires": "", "nav": {}, "prices": {}, "price_detail": {},
           "held": {}, "n_marked": 0, "renders": {}}

    # ---- 账本没开：**是状态，不是故障** ----
    if not book.is_book_portfolio(portfolio_id):
        out["status"] = BOOK_NOT_OPEN
        out["note"] = (
            f"{portfolio_id} 还没开账，所以算不出股数、也落不了提案。先跑一次：\n"
            f"    CIO_MARKET=us python run_rebalance.py --open-book "
            f"--capital 100000 --opened-on {out['as_of']}\n"
            f"**开账日和初始资金是写进去的事实，不从最早一笔交易倒推。**")
        return out
    book.assert_single_source(portfolio_id)

    # ---- 先作废过期提案。**必须在提案之前。** ----
    # 否则昨天那批 PENDING 会一直挂着，某天被批准并按当日开盘成交 ——
    # 用的却是好几天前的 NAV 与价格算出的股数。
    if expire_first:
        out["expired"] = book_expired = proposal_store.expire_stale(
            portfolio_id, out["as_of"], actor=actor)
        if book_expired:
            log.info("%d 条旧提案已过期作废：%s", len(book_expired),
                     "、".join(e["ticker"] for e in book_expired))

    # ---- 取那一次决策 ----
    rid = run_id or pc_ledger.latest_run_id(portfolio_id)
    if not rid:
        out["status"] = NO_PC_RUN
        out["note"] = (
            f"{portfolio_id} 在 pc_lineage 里没有任何一次 PC 运行。"
            f"**这不是「今天没有候选」**：那种情况下 PC 会留下一次运行记录，"
            f"只是每一行都没有仓位。")
        return out
    out["run_id"] = rid
    decisions = pc_ledger.decisions_for_run(rid, portfolio_id)
    out["decisions"] = decisions

    held = book.holdings_map(portfolio_id)
    out["held"] = held

    # ---- 价格与 NAV ----
    tickers = sorted({d["ticker"] for d in decisions if d.get("ticker")} | set(held))
    px_detail = marks.close_prices(tickers)
    prices = {t: d["price"] for t, d in px_detail.items() if d.get("price") is not None}
    out["price_detail"], out["prices"] = px_detail, prices
    nv = book.nav(portfolio_id, prices)
    out["nav"] = nv

    # ---- 目标 → 指令 ----
    pl = rebalance.plan(
        nav=nv["nav"], cash=nv["cash"], holdings=held, decisions=decisions,
        prices=prices, decision_date=out["as_of"],
        lot=int(book.portfolio_row(portfolio_id).get("lot_size") or 1))
    out["rows"], out["summary"] = pl["rows"], pl["summary"]
    out["expires"] = pl["summary"]["expires_on"]

    # ---- 事前合规 ----
    cmp_res = compliance.check_proforma(
        nav=nv["nav"], cash_available=nv["cash"],
        cash_required=pl["summary"]["cash_required"], rows=pl["rows"])
    out["compliance"] = cmp_res

    out["renders"] = {
        "prices": marks.render_note(px_detail),
        "book": book.render(portfolio_id, prices),
        "plan": rebalance.render(pl),
        "compliance": compliance.render(cmp_res),
    }

    # **先序列化，后落库。** 见上面 before_record 的说明 ——
    # 这一行之前，库里一个字都还没写。
    if before_record is not None:
        before_record(out)

    # ---- 落库。**每一行都落，包括不带指令的。** ----
    # 只记要交易的两只，报告上就看不出另外八只发生了什么 ——
    # "持有但今天没人复审"和"根本没纳入评估"必须能被分辨。
    saved = []
    for r in pl["rows"]:
        saved.append(proposal_store.record(
            run_id=rid, portfolio_id=portfolio_id, row=r, nav=nv["nav"],
            decision_date=out["as_of"], expires=out["expires"],
            compliance=cmp_res, actor=actor))
    out["saved"] = saved

    # 本轮真正做出过判断的持仓 → 刷新复审日期
    evaluated = [r["ticker"] for r in pl["rows"] if r["basis"] != rebalance.NO_TARGET]
    out["n_marked"] = book.mark_evaluated(
        portfolio_id, [t for t in evaluated if t in held], out["as_of"], rid)
    out["status"] = COMPLETED
    return out


def pending_count(res: dict) -> int:
    """本次落库里**真的在等你批**的有几条。

    `saved` 里同时有 NO_TRADE（终态，不需要你做任何事）。
    把两者加在一起报"5 条提案"，会让人以为有 5 件事要处理。
    """
    from . import proposal_store
    return sum(1 for s in (res.get("saved") or [])
               if s.get("state") == proposal_store.PENDING_APPROVAL)


def by_ticker(res: dict) -> dict:
    """`{ticker: 落库后的提案行}`。给上游做**队列 ↔ 提案库**的连接用。"""
    return {str(s.get("ticker") or "").upper(): s
            for s in (res.get("saved") or []) if s.get("ticker")}
