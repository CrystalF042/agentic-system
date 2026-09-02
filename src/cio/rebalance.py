"""目标 → 交易（**纯函数：不联网、不读库、不写库**）。

PC 说"NVDA 目标 1.8%"。账本说"现在持有 7 股"。
本模块把这两句话变成一条指令：**买 4 股，还是卖 2 股，还是什么都不做。**

纯函数是刻意的：这一层的每一条规则都能被完整测试，
而且**不可能因为数据源抽风而给出不同的答案**。

## 四条执行规则（Build 1 冻结）

一、**批准的是股数，不是权重。**
    T 收盘后用 T 的收盘价和 T 的 NAV 算出 `target_shares`，
    CEO 批的就是这个整数，T+1 开盘按实际开盘价成交它。

    反过来做（批权重、执行时按新价重算股数）看起来更"准"，
    但批准的东西和执行的东西不是同一个，审批就失去意义了。
    更糟的是开盘跳空 −8% 时系统会自动买更多股（目标金额 ÷ 更低的价格），
    **这个行为没有任何人批准过。**
    真实世界里 PM 批的是"买 1200 股"，不是"买 1.8% 然后系统看着办"。

二、**成交价基准 T+1_OPEN，写死并印在报告上。**
    T 日所有信息形成观点 → CRO/PC 定仓 → CEO 批 → 次一交易日开盘成交。
    用 T 日收盘价成交等于用当天的信息买当天，是最经典的静默作弊：
    回测结果会好得莫名其妙，而没有一处报错。

三、**只交易差额，且有不交易门槛。**
    Δ = target_shares − current_shares，正数买负数卖 0 不动。
    开仓/加仓/减仓/清仓统一成一个逻辑。
    但没有下限的话每天会产生一堆 2 股、5 股的调仓，账本被噪音填满，
    真正重要的调整淹在里面看不见。**清仓例外——目标 0 永远执行。**

四、**"没有目标" ≠ "目标为 0"。** 见 `target_from_decision`。

## 这个模块最危险的一行代码

    target = targets.get(ticker, 0.0)      # ← **绝对不要这样写**

`.get(x, 0.0)` 把"本轮没对它做判断"和"本轮判定应该清仓"折成同一个值，
于是一只只是今天没有新材料的持仓会被安静地全部卖出。
没有异常、没有告警，卖出指令本身完全合法。
`target_from_decision()` 存在的唯一理由就是让这个折叠**不可能发生**。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .utils import get_logger

log = get_logger("cio.rebalance")

# ---------------------------------------------------------------- 执行约定
EXECUTION_PRICE_BASIS = "T+1_OPEN"
MAX_SESSION_GAP_DAYS = 4
"""批准的有效期（自然日）。

一次批准只对**下一个交易日开盘**有效。周五批的、周三才跑执行，
不能拿三天前的股数去成交——那个数是基于三天前的 NAV 和价格算出来的。
4 天覆盖"周末 + 一个假日"；超过就作废，重新提案。

**这正是那种不报错、结果全错的路径**：陈旧的股数成交完全合法。
"""

NO_TRADE_BAND_PCT = 0.0025          # NAV 的 0.25%
NO_TRADE_MIN_USD = 200.0
STALE_REVIEW_DAYS = 10
"""持仓多久没被复审就要在报告上标出来。

人工做投资最常见的失败不是算错，是**忘了当初为什么买、说好什么时候认错**。
一只票躺在账本里 40 天没人看过，这件事必须每天都能看见。
"""

# ---------------------------------------------------------------- 目标的三种来源
TARGET = "TARGET"                   # PC 给了权重
EXIT_DECIDED = "EXIT_DECIDED"       # 本轮判定不应持有 → 目标 0
NO_TARGET = "NO_TARGET"             # 本轮**没有**对"是否持有"做出判断

# ---------------------------------------------------------------- 指令
BUY = "BUY"
SELL = "SELL"
EXIT = "EXIT"
HOLD_AT_TARGET = "HOLD_AT_TARGET"           # 已经在目标上，不用动
HOLD_NOT_EVALUATED = "HOLD_NOT_EVALUATED"   # 持有中，本轮未复审 → **不动，不卖**
BELOW_BAND = "BELOW_BAND"                   # 差额小于门槛 → 不动
NOT_PRICED = "NOT_PRICED"                   # 取不到价 → 算不出目标股数
SUB_LOT = "SUB_LOT"                         # 目标金额不足 1 股
NO_ACTION = "NO_ACTION"                     # 无目标且无持仓

TRADING_ACTIONS = (BUY, SELL, EXIT)


def expires_on(decision_date: str, days: int = MAX_SESSION_GAP_DAYS) -> str:
    d = datetime.strptime(str(decision_date)[:10], "%Y-%m-%d").date()
    return (d + timedelta(days=days)).isoformat()


def days_between(a: str, b: str):
    """b − a 的自然日数。任一为空返回 None（**不返回 0**）。

    0 的意思是"今天刚复审过"，None 的意思是"没有复审记录"。
    对一只持仓而言，这两句话的含义完全相反。
    """
    try:
        d1 = datetime.strptime(str(a)[:10], "%Y-%m-%d").date()
        d2 = datetime.strptime(str(b)[:10], "%Y-%m-%d").date()
    except Exception:                                    # noqa: BLE001
        return None
    return (d2 - d1).days


def target_from_decision(row: dict) -> dict:
    """**从一条已落库的 PC 决策，读出它对"是否持有"说了什么。**

    整个系统里做这个映射的地方**只有这一处**。在别处手写
    `targets.get(t, 0)` 或 `if veto: sell` 就会出现第二份规则，
    两份规则一定会漂移，而漂移的那一份不会报错。

        veto = 1                    → 目标 0，清仓     风控说了不该持有
        w_final 有值（含 0.0）       → 目标 = w_final   PC 给出了权重
        w_final 为空、未否决          → **无目标**       本轮没判断该不该持有

    第三档是关键。它涵盖：Gate 材料不足、Gate 未记录、测量口径不符。
    这三种都**不是**"卖出"，是"我今天没有材料来重新确认这个仓位"。
    把它们当成目标 0，就会因为今天没新闻而清掉一只正常的持仓。

    ⚠ 给 Build 4 的提醒：`w_final == 0.0` 可能来自行业上限余量耗尽。
    实现 `sector_used` 时**必须把被定仓的这只自己排除在"已用"之外**
    （headroom = cap − 别人已用），否则一只处在满额行业里的持仓
    会算出自己的余量为 0，从而**提议清掉自己**——每天都清、每天都合法。
    """
    ticker = str(row.get("ticker") or "").upper()
    if row.get("veto"):
        return {"ticker": ticker, "basis": EXIT_DECIDED, "target_weight": 0.0,
                "reason": f"CRO 否决：{row.get('veto_reason') or '（未记原因）'}"}
    w = row.get("w_final")
    if w is not None:
        return {"ticker": ticker, "basis": TARGET, "target_weight": float(w),
                "reason": row.get("reason") or ""}
    return {"ticker": ticker, "basis": NO_TARGET, "target_weight": None,
            "reason": (row.get("reason") or "本轮未对该标的做出持有与否的判断")}


_FLOOR_EPS = 1e-9
"""取整前的容差 —— **这不是"差不多就行"，是在修一个必然发生的偏差。**

NAV 100,000 × 权重 1.8% ÷ 价格 180 的真值是整 10 股。
但二进制里 `100000 * 0.018 = 1799.9999999999998`，除以 180 得
`9.999999999999999`，直接取整就是 **9 股**。

这个错永远只朝一个方向：**少买**。每一笔都少一点点，
账本永远比目标略轻，表现为一段解释不了的持续跑输——
而每一步计算都"没有错"。1e-9 远小于任何有意义的股数零头，
只吃掉浮点噪声，不会把 9.98 抬成 10。
"""


def target_shares(nav, target_weight, price, lot: int = 1):
    """目标金额 ÷ 价格，向零取整。**任一输入缺失就返回 None，不返回 0。**

    返回 0 的意思是"目标就是不持有"，返回 None 的意思是"算不出来"。
    下游对这两者的处理完全相反：前者去卖，后者什么都不做。
    """
    import math
    if nav is None or target_weight is None or price is None:
        return None
    p = float(price)
    if p <= 0:
        return None
    raw = float(nav) * float(target_weight) / p
    s = int(math.floor(abs(raw) + _FLOOR_EPS))
    s = -s if raw < 0 else s
    if lot and lot > 1:
        s = (s // lot) * lot
    return s


def no_trade_threshold(nav, band_pct: float = NO_TRADE_BAND_PCT,
                       min_usd: float = NO_TRADE_MIN_USD):
    if nav is None:
        return None
    return max(float(nav) * float(band_pct), float(min_usd))


def plan(*, nav, cash, holdings: dict, decisions: list, prices: dict,
         decision_date: str, band_pct: float = NO_TRADE_BAND_PCT,
         min_usd: float = NO_TRADE_MIN_USD, lot: int = 1,
         stale_days: int = STALE_REVIEW_DAYS) -> dict:
    """把决策 + 持仓 + 价格，变成一份**逐票的指令清单**。

    参数
      nav        决策时点的净值（T 收盘）。None = 算不出 → 全部标 NOT_PRICED
      holdings   {ticker: {shares, last_evaluated_on, ...}}
      decisions  pc_lineage 的行（dict），每行至少含 ticker / veto / w_final
      prices     {ticker: T 日收盘价}

    **清单覆盖"决策 ∪ 持仓"的并集**，不是只有要交易的那些。
    只列要交易的，报告上就看不出"哪些持仓今天没人看过"——
    而那恰恰是最该被看见的一行。
    """
    hold = {str(k).upper(): v for k, v in (holdings or {}).items()}
    px = {str(k).upper(): v for k, v in (prices or {}).items()}
    by_ticker = {}
    for d in (decisions or []):
        t = target_from_decision(d)
        # 同一 (run_id, ticker) 在台账里唯一，这里仍防一手：后来者不覆盖先到者，
        # 且要出声——静默覆盖会让报告只反映其中一条决策。
        if t["ticker"] in by_ticker:
            log.warning("%s 在本轮决策里出现多次，保留第一条（后续被忽略）", t["ticker"])
            continue
        t["_row"] = d
        by_ticker[t["ticker"]] = t

    thr = no_trade_threshold(nav, band_pct, min_usd)
    rows = []
    for tk in sorted(set(by_ticker) | set(hold)):
        cur = int((hold.get(tk) or {}).get("shares") or 0)
        t = by_ticker.get(tk) or {"ticker": tk, "basis": NO_TARGET,
                                  "target_weight": None,
                                  "reason": "本轮未纳入评估", "_row": {}}
        src = t.get("_row") or {}
        price = px.get(tk)
        last_eval = (hold.get(tk) or {}).get("last_evaluated_on") or ""
        since = days_between(last_eval, decision_date)
        r = {
            "ticker": tk, "basis": t["basis"], "reason": t["reason"],
            "target_weight": t["target_weight"],
            "current_shares": cur, "target_shares": None, "delta_shares": 0,
            "decision_price": (None if price is None else float(price)),
            "est_value": None, "est_weight": None,
            "action": NO_ACTION, "band_threshold": thr,
            "days_since_evaluated": since,
            "stale_review": bool(since is not None and since > stale_days),
            "thesis_id": src.get("thesis_id"), "direction": src.get("direction"),
            "conviction": src.get("conviction"),
            "evidence_gate": src.get("evidence_gate"),
        }

        # ---- 无目标：持有就原样持有，**绝不因为"今天没判断"而卖出** ----
        if t["basis"] == NO_TARGET:
            r["action"] = HOLD_NOT_EVALUATED if cur else NO_ACTION
            if cur:
                r["reason"] = (f"{t['reason']}　→ 维持 {cur} 股不动"
                               + (f"（上次复审 {last_eval}，{since} 天前）"
                                  if since is not None else "（无复审记录）"))
            rows.append(r)
            continue

        # ---- 有目标，但取不到价：算不出股数。**不能当成 0。** ----
        if price is None or float(price) <= 0:
            r["action"] = NOT_PRICED
            r["reason"] = (f"{tk} 取不到 {decision_date} 的收盘价 —— 目标股数不可计算。"
                           f"**不按 0 处理**：那会把一次取数失败变成一道清仓指令。")
            rows.append(r)
            continue

        tgt = target_shares(nav, t["target_weight"], price, lot)
        r["target_shares"] = tgt
        if tgt is None:
            r["action"] = NOT_PRICED
            r["reason"] = "NAV 不可计算，目标股数无法确定（**不按 0 处理**）"
            rows.append(r)
            continue

        # ---- 目标 > 0 但不足 1 股：这不是"清仓"，是"这次买不进" ----
        if tgt == 0 and (t["target_weight"] or 0) > 0:
            r["action"] = SUB_LOT if not cur else HOLD_AT_TARGET
            r["reason"] = (f"目标金额 {float(nav) * t['target_weight']:,.2f} "
                           f"不足一股（{float(price):,.2f}）"
                           + ("　→ 现有持仓维持不动：目标取整到 0 **不等于**判定清仓"
                              if cur else ""))
            rows.append(r)
            continue

        delta = tgt - cur
        r["est_value"] = tgt * float(price)
        r["est_weight"] = (None if not nav else r["est_value"] / float(nav))

        if delta == 0:
            # 已经在目标上 → 不动。但"目标 0 且本来就没持有"要读作**无动作**，
            # 不是"已在目标"：后者会让一条否决记录在清单上长得像一次确认持有。
            r["action"] = HOLD_AT_TARGET if cur else NO_ACTION
            rows.append(r)
            continue

        full_exit = (tgt == 0 and cur > 0)
        if not full_exit and thr is not None and abs(delta * float(price)) < thr:
            # **清仓例外**：目标 0 永远执行，不受门槛约束。
            r["action"] = BELOW_BAND
            r["reason"] = (f"差额 {abs(delta)} 股 ≈ {abs(delta) * float(price):,.2f}"
                           f"，低于门槛 {thr:,.2f}（NAV×{band_pct:.2%} 与 "
                           f"{min_usd:,.0f} 取大）→ 本轮不交易")
            rows.append(r)
            continue

        r["delta_shares"] = delta
        r["action"] = EXIT if full_exit else (BUY if delta > 0 else SELL)
        rows.append(r)

    return {"rows": rows, "summary": _summary(rows, nav, cash, decision_date, thr)}


def _summary(rows: list, nav, cash, decision_date: str, thr) -> dict:
    buy = sum(r["delta_shares"] * r["decision_price"]
              for r in rows if r["action"] == BUY)
    sell = sum(-r["delta_shares"] * r["decision_price"]
               for r in rows if r["action"] in (SELL, EXIT))
    # **现金需求只算买入，不拿同场卖出的回款去抵。**
    # 美股 T+1 交收，同一开盘的卖出回款当天并未到账；靠它顶买入
    # 会让提案在纸面上"刚好够钱"，而真实执行时不够——一个恰好成立的假设。
    need = float(buy)
    avail = None if cash is None else float(cash)
    short = None if avail is None else max(0.0, need - avail)
    gross = sum(r["est_weight"] or 0.0 for r in rows
                if r["target_shares"] not in (None, 0))
    n = {}
    for r in rows:
        n[r["action"]] = n.get(r["action"], 0) + 1
    return {
        "decision_date": decision_date,
        "execution_price_basis": EXECUTION_PRICE_BASIS,
        "expires_on": expires_on(decision_date),
        "nav_at_decision": nav, "cash_at_decision": avail,
        "no_trade_threshold": thr,
        "buy_value": float(buy), "sell_value": float(sell),
        "cash_required": need, "cash_shortfall": short,
        "gross_target_weight": gross,
        "cash_residual": (None if nav is None else 1.0 - gross),
        "n_by_action": n,
        "n_trades": sum(1 for r in rows if r["action"] in TRADING_ACTIONS),
        "n_stale_review": sum(1 for r in rows if r["stale_review"]),
    }


def render(p: dict) -> str:
    """一屏清单。**每一行都要说清"为什么不动"，不能只列要动的。**"""
    s = p["summary"]
    L = [f"决策日 {s['decision_date']}　成交价基准 {s['execution_price_basis']}"
         f"　批准有效至 {s['expires_on']}",
         (f"决策时 NAV {s['nav_at_decision']:,.2f}　现金 {s['cash_at_decision']:,.2f}"
          if s["nav_at_decision"] is not None and s["cash_at_decision"] is not None
          else "决策时 NAV **不可计算**（见账本缺价提示）"),
         (f"不交易门槛 {s['no_trade_threshold']:,.2f}"
          if s["no_trade_threshold"] is not None else "不交易门槛：NAV 未知，无法计算"),
         ""]
    w = {BUY: "买入", SELL: "减持", EXIT: "清仓", HOLD_AT_TARGET: "已在目标",
         HOLD_NOT_EVALUATED: "持有·本轮未复审", BELOW_BAND: "低于门槛不动",
         NOT_PRICED: "缺价·不可计算", SUB_LOT: "不足一股", NO_ACTION: "无动作"}
    for r in p["rows"]:
        head = f"{r['ticker']:<6} {w.get(r['action'], r['action']):<12}"
        if r["action"] in TRADING_ACTIONS:
            L.append(f"{head} {r['current_shares']:>6} → {r['target_shares']:<6} 股"
                     f"　Δ {r['delta_shares']:+d}"
                     f"　@ {r['decision_price']:,.2f}"
                     f"　目标 {(r['target_weight'] or 0):.2%}")
        else:
            L.append(f"{head} 持有 {r['current_shares']} 股"
                     + (f"　目标 {r['target_weight']:.2%}"
                        if r["target_weight"] is not None else "　**无目标**"))
        if r["reason"]:
            L.append(f"       {r['reason']}")
        if r["stale_review"]:
            L.append(f"       ⚠ 已 {r['days_since_evaluated']} 天未复审 —— "
                     f"该回去看一眼当初的失效条件还成不成立")
    L += ["", f"买入 {s['buy_value']:,.2f}　卖出 {s['sell_value']:,.2f}　"
              f"需现金 {s['cash_required']:,.2f}"
              + (f"　**缺口 {s['cash_shortfall']:,.2f}**"
                 if s["cash_shortfall"] else "")]
    if s["cash_shortfall"]:
        L.append("（现金需求**不拿同场卖出的回款抵扣**：美股 T+1 交收，"
                 "那笔钱当天没到账。）")
    L.append(f"目标合计仓位 {s['gross_target_weight']:.2%}"
             + (f"　现金残差 {s['cash_residual']:.2%}（不归一化到 100%）"
                if s["cash_residual"] is not None else ""))
    return "\n".join(L)
