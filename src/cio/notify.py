"""待批提醒 —— **自动化走到这里就停了，剩下的一件事得让你知道。**

## 这一层要回答的问题只有一个

> 「有 2 条提案等你批准」这句话，**到没到你手机上。**

不是"我们发了"，是"她收到了"。这两件事在这套系统里被区分过很多次，
但在推送这一层它有一个新形状：

    deliver.send_text() 在 CIO_TG_DRYRUN=1 时 return True

也就是说**"演习"和"真发出去了"在返回值上长得一样**。
如果通知台账照着这个返回值记"已通知"，那么：

    演习跑了一次 → 台账记「已通知」 → 以后不再推
    → 那条真正要她批的消息，一次都不会发出去

所以本模块**不用布尔值**，用四态：

    SENT          真的发出去了      → 记进台账
    DRYRUN        有意不发（演习）  → **绝不记进台账**
    UNCONFIGURED  没配 token/chat   → 不记，且在心跳里喊
    FAILED        发送失败          → 不记，且在心跳里喊

## 不重复轰炸，但"没变化"不等于"不用管"

同一批待批提案不该每次跑都推一遍——人会开始忽略它。
去重键是**内容指纹**，不是条数：

    3 条换成另外 3 条 → 条数一样 → 按条数去重就**一条都不推**

指纹取 `(提案号, 状态, 动作, Δ股数, 有效期)` 排序后哈希。

但"内容没变"有两种完全不同的原因：

    刚推过，她还没来得及看      —— 不用再推
    挂了三个交易日没人动         —— **必须再推，而且要说挂了多久**

后者正是这套系统一直在防的形状：**一条挂到过期自动作废的提案，
和一条从来没产生过的提案，在结果上一模一样。** 所以有两条越过去重的路：

    有条目挂了 ≥ MIN_AGE_TO_REMIND 个交易日   每天最多再提醒一次
    有条目明天就过期作废                      同上，且措辞升级

## 这里没有批准动作

本模块只发消息。按钮的 `callback_data` 由 `tgbot` 处理，
而 `tgbot` 调的是 `proposal_store.transition()`——和命令行同一个函数。
**本模块的源码里一个 APPROVED 都没有**，探针（build122 那条）钉着。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .config import RAW_DIR
from .utils import get_logger

log = get_logger("cio.notify")

STATE_PATH = Path(RAW_DIR) / "notify" / "pending.json"

SCHEMA_VERSION = "notify-1.0.0"

SENT = "sent"
DRYRUN = "dryrun"
UNCONFIGURED = "unconfigured"
FAILED = "failed"

OUTCOMES = (SENT, DRYRUN, UNCONFIGURED, FAILED)

DELIVERED = (SENT,)
"""**只有这一个算送到了。** 其余三个都不许写进台账。

写进去的后果是同一个：台账说"已通知"，于是不再推，
而那条消息一次都没到过她手上。
"""

MIN_AGE_TO_REMIND = 2
"""挂了几个交易日就再提醒一次。

不设这条的话，去重会把"挂了三天没人动"和"刚推过"变成同一件事——
而一条挂到过期作废的提案，和一条从来没产生过的，在结果上一模一样。
"""

EXPIRES_SOON_DAYS = 1
"""还剩几个自然日到期就升级措辞。**过期作废是静默的**，得提前说。"""


# ---------------------------------------------------------------- 台账

def _blank() -> dict:
    return {"fingerprint": "", "last_sent_at": "", "last_sent_day": "",
            "last_reminded_day": "", "n_sent": 0, "portfolio_id": "",
            "schema_version": SCHEMA_VERSION}


def state(portfolio_id: str) -> dict:
    """上一次真的送到是什么时候、送的是哪一批。**从磁盘读。**"""
    if not STATE_PATH.exists():
        return _blank()
    try:
        all_ = json.loads(STATE_PATH.read_text("utf-8"))
    except Exception:                                          # noqa: BLE001
        log.warning("%s 读不动，当作从来没通知过（宁可多推一次）", STATE_PATH.name)
        return _blank()
    s = dict(_blank())
    s.update((all_ or {}).get(portfolio_id) or {})
    return s


def record(portfolio_id: str, fingerprint: str, at: str, day: str,
           reminded: bool = False) -> dict:
    """记一次**真的送到**。`outcome != SENT` 的调用方不许走到这里。"""
    all_ = {}
    if STATE_PATH.exists():
        try:
            all_ = json.loads(STATE_PATH.read_text("utf-8")) or {}
        except Exception:                                      # noqa: BLE001
            all_ = {}
    s = dict(state(portfolio_id))
    s.update({"fingerprint": fingerprint, "last_sent_at": at,
              "last_sent_day": day, "portfolio_id": portfolio_id,
              "n_sent": int(s.get("n_sent", 0)) + 1,
              "schema_version": SCHEMA_VERSION})
    if reminded:
        s["last_reminded_day"] = day
    all_[portfolio_id] = s
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(all_, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    return s


# ---------------------------------------------------------------- 内容

def fingerprint(rows: list) -> str:
    """待批清单的内容指纹。**按内容，不按条数。**

    只数条数的话，3 条换成另外 3 条会得到同一个指纹，于是一条都不推——
    而那 3 条是全新的决定。
    """
    parts = sorted(
        f"{r.get('id')}|{r.get('state')}|{r.get('action')}|"
        f"{r.get('delta_shares')}|{r.get('target_shares')}|{r.get('expires_on')}"
        for r in (rows or []))
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def aged(rows: list, today: str) -> list:
    """挂了 ≥ `MIN_AGE_TO_REMIND` 个交易日的。**按交易日，不按自然日。**

    周五提的、周一还没批，那是 1 个交易日，不是 3 天。
    按自然日算会让周末每次都触发一次"挂太久"的提醒。
    """
    from .technical.review import trading_days_between
    out = []
    for r in (rows or []):
        n = trading_days_between(str(r.get("decision_date") or "")[:10], today)
        if n is not None and n >= MIN_AGE_TO_REMIND:
            out.append(dict(r, _age=n))
    return out


def expiring(rows: list, today: str) -> list:
    """还剩 ≤ `EXPIRES_SOON_DAYS` 天作废的。**过期是静默的，得提前说。**"""
    from .rebalance import days_between
    out = []
    for r in (rows or []):
        n = days_between(today, str(r.get("expires_on") or "")[:10])
        if n is not None and n <= EXPIRES_SOON_DAYS:
            out.append(dict(r, _left=n))
    return out


def message(portfolio_id: str, rows: list, today: str,
            reminding: bool = False) -> tuple:
    """`(文本, 按钮)`。**按钮和命令行两条路都给。**

    按钮要有人接才有用：`run_tgbot.py` 没在跑的时候，点按钮的表现是
    转一下圈然后什么都不发生——没有任何提示。所以文本里始终带命令行写法。
    """
    from . import tgbot
    ag = {r["id"]: r for r in aged(rows, today)}
    ex = {r["id"]: r for r in expiring(rows, today)}

    head = f"**{len(rows)} 条提案等你批准**　{portfolio_id}　{today}"
    if reminding:
        head = f"⏰ {head}　——　**上次推送之后还没有动静**"
    L = [head, ""]
    kb = []
    for r in rows:
        rid = r.get("id")
        line = (f"#{rid}　{r.get('ticker')}　{r.get('action')} "
                f"{int(r.get('delta_shares') or 0):+d} 股"
                f"　{r.get('current_shares')} → {r.get('target_shares')}")
        L.append(line)
        px = r.get("decision_price")
        L.append(f"    决策价 {px:,.2f}".rstrip() if px is not None
                 else "    决策价 **缺价**")
        L.append(f"    目标 {(r.get('target_weight') or 0):.2%}"
                 f"　合规 {r.get('compliance_status') or '未知'}"
                 f"　有效至 {r.get('expires_on')}")
        if rid in ag:
            L.append(f"    ⏰ **已挂 {ag[rid]['_age']} 个交易日**")
        if rid in ex:
            n = ex[rid]["_left"]
            L.append(f"    ⚠ **{'今天' if n <= 0 else f'还有 {n} 天'}就过期作废** "
                     f"—— 作废之后必须重新提案，股数会按新的 NAV 重算")
        kb.append([{"text": f"✅ 批准 {r.get('ticker')}",
                    "callback_data": f"ap:{rid}"},
                   {"text": f"❌ 否决 {r.get('ticker')}",
                    "callback_data": f"rj:{rid}"}])
    L += ["",
          "批准 = 固定这个整数股数，下一个交易日开盘成交它。",
          "",
          "按钮需要控制台在跑（python run_tgbot.py）。没跑的话在电脑上：",
          "  .venv/bin/python run_approve.py --approve <号>",
          "  .venv/bin/python run_approve.py --reject <号> --reason \"...\""]
    text = "\n".join(L)
    if len(text) > tgbot.MAX_MSG:
        log.info("待批清单 %d 字，tgbot.send 会自动切段", len(text))
    return text, kb


# ---------------------------------------------------------------- 发送

def _send(text: str, keyboard, dry_run: bool = False) -> str:
    """发一次，返回**四态之一**。

    `tgbot.send` 的布尔返回值把"演习"和"没配置"都归成 False，
    把"发出去了"归成 True。这里要的是**能区分"没送到的三种原因"**——
    因为它们的处理方式不同：演习不该重试，没配置该喊，失败该重试。
    """
    from . import tgbot
    from .config import settings
    if dry_run or settings.TG_DRYRUN:
        log.info("[DRYRUN] 待批推送（%d 组按钮）：\n%s",
                 len(keyboard or []), text[:800])
        return DRYRUN
    tok, _shared = tgbot.token()
    if not tok or not settings.TG_CHAT_ID:
        log.warning("Telegram 没配置（token/chat_id），待批提醒发不出去")
        return UNCONFIGURED
    try:
        return SENT if tgbot.send(text, keyboard=keyboard) else FAILED
    except Exception as e:                                     # noqa: BLE001
        log.error("待批推送失败：%s", e)
        return FAILED


def notify_pending(portfolio_id: str, today: str, hb=None,
                   force: bool = False, dry_run: bool = False) -> dict:
    """有待批就推。返回结果摘要（**0 条也返回**）。

    `dry_run=True` 走的是**同一条路**，只在最后发送那一步停手（记 `DRYRUN`）。
    另写一条预演分支的话，预演验证的就是那条永远不会真跑的代码。

    推 / 不推的判断：

        没有待批            不推（**不许每天推一条「今天 0 条」**——常亮的灯）
        内容指纹变了        推
        上次没真的送到      推（台账里根本没记过这一批）
        有条目挂太久/快过期  推，每天最多一次，措辞升级
        其余                不推，但心跳里照样有计数
    """
    from . import proposal_store
    from .utils import stamp_utc

    today = str(today)[:10]
    rows = proposal_store.pending(portfolio_id)
    fp = fingerprint(rows)
    st = state(portfolio_id)
    ag, ex = aged(rows, today), expiring(rows, today)

    res = {"portfolio_id": portfolio_id, "day": today, "pending": len(rows),
           "fingerprint": fp, "known_fingerprint": st.get("fingerprint", ""),
           "aged": len(ag), "expiring": len(ex), "sent": False,
           "outcome": "", "reason": "", "reminding": False,
           "dry_run": bool(dry_run),
           "last_sent_day": st.get("last_sent_day", ""),
           "n_sent": int(st.get("n_sent", 0))}

    if not rows:
        # **0 条不推。** 每天推一条"今天没有要批的"就是一盏常亮的灯，
        # 人学会忽略它之后，真正有事的那天也会被忽略。
        # 心跳里照样有 `pending 0` —— 那是"跑过了"的证据。
        res["reason"] = "没有待批的提案"
        _report(res, hb)
        return res

    changed = fp != st.get("fingerprint", "")
    remind = bool(ag or ex) and st.get("last_reminded_day", "") != today
    if not (force or changed or remind):
        res["reason"] = ("这一批已经推过了，内容没变、也没有挂太久的"
                         f"（上次 {st.get('last_sent_day') or '？'}）")
        _report(res, hb)
        return res

    res["reminding"] = bool(remind and not changed)
    text, kb = message(portfolio_id, rows, today, reminding=res["reminding"])
    res["text"] = text
    res["outcome"] = _send(text, kb, dry_run=dry_run)
    res["sent"] = res["outcome"] in DELIVERED
    res["reason"] = {
        SENT: "已送达",
        DRYRUN: "**演习，没有真发**　—— 不记进通知台账，下次还会推",
        UNCONFIGURED: "**Telegram 没配置**，这条提醒发不出去",
        FAILED: "**发送失败**，下次跑还会重试",
    }.get(res["outcome"], res["outcome"])

    if res["sent"]:
        # **只有真的送到才记台账。** 记早了，下次就不推了，
        # 而那条消息一次都没到过她手上。
        record(portfolio_id, fp, stamp_utc(), today, reminded=res["reminding"])
    _report(res, hb)
    return res


def _report(res: dict, hb) -> None:
    """记进心跳。**0 也记；没送到要喊。**"""
    if hb is None:
        return
    hb.count(pending=res["pending"], aged=res["aged"], expiring=res["expiring"],
             notified=1 if res["sent"] else 0)
    if not res["pending"]:
        hb.note("没有等你批的。**自动化到这一步为止，它自己过不去。**")
        return
    if res["sent"]:
        hb.note(f"已推送待批清单（{res['pending']} 条{'，含提醒' if res['reminding'] else ''}）"
                f"　—— 按钮需要 run_tgbot.py 在跑")
    elif res["outcome"] == DRYRUN:
        # **有意不发 ≠ 没送到。** 一个被关掉的推送和一个坏掉的推送
        # 不许长得一样 —— 演习每天点一次告警，那盏灯就废了。
        hb.note(f"{res['pending']} 条待批：**演习，没有真发**（下次还会推）")
    elif res["outcome"]:
        # **有事要她批、而通知没送到** —— 这条必须是告警。
        # 心跳掉一次第二天会补上；这条掉了，那笔交易就静静地过期作废。
        hb.alert(f"**{res['pending']} 条提案等你批准，但提醒没送到**"
                 f"（{res['reason']}）")
    else:
        hb.note(f"{res['pending']} 条待批：{res['reason']}")
    if res["expiring"]:
        hb.alert(f"**{res['expiring']} 条提案即将过期作废** —— "
                 f"过期不是拒绝，是这次决定被时间吃掉了")
    elif res["aged"]:
        hb.note(f"{res['aged']} 条已经挂了 {MIN_AGE_TO_REMIND} 个交易日以上")


def describe(res: dict) -> list:
    """给人看的几行。**0 条也要印。**"""
    out = [f"待批提醒　{res['day']}　{res['portfolio_id']}"]
    out.append(f"  待批 {res['pending']}　挂太久 {res['aged']}　"
               f"快过期 {res['expiring']}　"
               f"本次{'已推送' if res['sent'] else '未推送'}")
    if res.get("reason"):
        out.append(f"  {res['reason']}")
    if res.get("pending") and not res.get("sent") and res.get("outcome"):
        out.append("  **有事要你批，而提醒没送到。** 直接在电脑上跑："
                   "run_approve.py --pending")
    return out
