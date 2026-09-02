"""Telegram 控制台 —— 没有网页之前，**手机就是界面**。

界面层的唯一职责是**触发状态变化**，不重写任何投资逻辑：

    /pending    看待批清单（带「批准 / 否决」按钮）
    /approve 12 /reject 12   改状态
    /execute    跑执行
    /book       看账本

按钮点下去调用的是 `proposal_store.transition()`，和命令行**同一个函数**。
不是"给 Telegram 也写一份"——两份规则一定会漂移，而漂移的那份不报错。

## 三个必须处理的坑

一、**别和 OpenClaw 抢 getUpdates。**
    Telegram 的一条更新**只会投递给一个** getUpdates 消费者。你的
    OpenClaw 已经在用 `TELEGRAM_BOT_TOKEN` 收消息；本程序如果用同一个
    token 去 poll，两边会互相抢，表现是**指令随机丢失**、偶尔 409，
    而丢掉的那条不会有任何提示——你以为点了批准，其实没到。

    所以默认用**另一个 bot 的 token**：`CIO_CTRL_BOT_TOKEN`。
    在 @BotFather 里 /newbot 再建一个就行，几十秒。
    没配就退回 `TELEGRAM_BOT_TOKEN` 并**大声警告**，不静默共用。

二、**只听你自己的 chat。** 任何其他 chat_id 的消息一律忽略并记日志。
    这是纸面组合，但"谁能按批准"这件事本身必须是确定的。

三、**offset 落盘。** 不记 offset 的话，重启会把历史指令重放一遍——
    昨天那条 `/approve 12` 会再执行一次。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

from .config import BASE, settings
from .utils import get_logger

log = get_logger("cio.tgbot")

API = "https://api.telegram.org/bot{token}/{method}"
OFFSET_FILE = BASE / "logs" / "tgbot.offset"
MAX_MSG = 3500                      # Telegram 上限 4096，留出余量
POLL_TIMEOUT = 25


def token() -> tuple:
    """返回 (token, 是否与 OpenClaw 共用)。**共用要让人看见。**"""
    t = os.environ.get("CIO_CTRL_BOT_TOKEN", "").strip()
    if t:
        return t, False
    return settings.TG_TOKEN, bool(settings.TG_TOKEN)


def _api(method: str, **data):
    tk, _ = token()
    if not tk:
        raise RuntimeError("没有 bot token：设 CIO_CTRL_BOT_TOKEN 或 TELEGRAM_BOT_TOKEN")
    r = httpx.post(API.format(token=tk, method=method), data=data, timeout=POLL_TIMEOUT + 10)
    if r.status_code == 409:
        raise RuntimeError(
            "409 Conflict —— **有另一个程序在用同一个 bot token 收消息**"
            "（多半是 OpenClaw）。两边会互相抢，指令会随机丢失。"
            "去 @BotFather 用 /newbot 另建一个 bot，把它的 token 设成 "
            "CIO_CTRL_BOT_TOKEN。")
    r.raise_for_status()
    return r.json()


def send(text: str, chat_id: str = "", keyboard: list = None) -> bool:
    """发消息。返回**是否真的发出去了**。

    两条：

    **必须认 `CIO_TG_DRYRUN`。** 这个函数最初漏了这一条，于是自测时
    调用方照着自己的 DRYRUN 标志印"只打印未真发"，而 send 在背后
    真去调了 API——**一句报告和一次真实发送同时存在**，而且不冲突、不报错。
    正是这套系统一直在防的那种缺陷，写在了防它的模块里。

    **长文自动切段。** 超过 4096 字会被 Telegram 整条拒收，
    在日志里只是一行 400，报告就这么没了。
    """
    cid = chat_id or settings.TG_CHAT_ID
    if settings.TG_DRYRUN:
        log.info("[DRYRUN] Telegram（chat %s，按钮 %d 组）：\n%s",
                 cid, len(keyboard or []), str(text)[:1200])
        return False
    parts, cur = [], ""
    for line in str(text).split("\n"):
        if len(cur) + len(line) + 1 > MAX_MSG:
            parts.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        parts.append(cur)
    ok = True
    for i, p in enumerate(parts):
        d = {"chat_id": cid, "text": p, "disable_web_page_preview": True}
        if keyboard and i == len(parts) - 1:
            d["reply_markup"] = json.dumps({"inline_keyboard": keyboard})
        try:
            _api("sendMessage", **d)
        except Exception as e:                                # noqa: BLE001
            log.error("发送失败：%s", e)
            ok = False
    return ok


def _read_offset() -> int:
    try:
        return int(OFFSET_FILE.read_text().strip())
    except Exception:                                        # noqa: BLE001
        return 0


def _write_offset(v: int) -> None:
    try:
        OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
        OFFSET_FILE.write_text(str(v))
    except Exception as e:                                   # noqa: BLE001
        log.warning("offset 落盘失败（重启会重放指令）：%s", e)


# ---------------------------------------------------------------- 子进程跑入口
def _run(script: str, *args) -> str:
    """跑一个入口脚本并取回输出。

    **subprocess，不 import。** 这是冻结过的执行契约：界面崩了不能带走引擎，
    引擎的一次异常也不该让界面进程死掉。
    """
    env = dict(os.environ)
    env.setdefault("CIO_MARKET", "us")
    try:
        p = subprocess.run([sys.executable, str(BASE / script), *args],
                           capture_output=True, text=True, timeout=900, env=env,
                           cwd=str(BASE))
    except subprocess.TimeoutExpired:
        return f"{script} 跑了 15 分钟还没结束，已中止。"
    out = (p.stdout or "").strip()
    if p.returncode != 0 and not out:
        tail = "\n".join((p.stderr or "").strip().split("\n")[-8:])
        return f"{script} 退出码 {p.returncode}\n{tail}"
    return out or f"{script} 没有输出。"


# ---------------------------------------------------------------- 指令
HELP = """CIO 控制台

  /pending    待批清单（带按钮）
  /book       账本与持仓
  /approve 12   批准（也可以 /approve NVDA）
  /reject 12 理由   否决
  /approveall  批准全部待批
  /execute    执行已批准的（T+1 开盘成交）
  /rebalance  重新出提案
  /pc         跑一遍 CRO→PC（较慢）
  /stats      提案状态分布

按钮和命令走的是同一套状态机。
批准 = 固定那个整数股数，下一个交易日开盘成交它。"""


def _pid() -> str:
    from . import portfolio
    from .config import market
    return portfolio.MARKET_PORTFOLIO.get(
        market().get("news_region", "us"), portfolio.US_PAPER)


def _pending_msg() -> tuple:
    """(文本, 按钮)。按钮 callback_data 用提案号，短且稳定。"""
    from . import proposal_store
    pid = _pid()
    rows = proposal_store.pending(pid)
    if not rows:
        ap = proposal_store.approved(pid)
        t = f"{pid}：没有待批准的提案。"
        if ap:
            t += f"\n已批准待成交 {len(ap)} 条 —— /execute 去成交。"
        return t, None
    L = [f"{pid}　待批准 {len(rows)} 条", ""]
    kb = []
    for r in rows:
        L.append(f"#{r['id']}　{r['ticker']}　{r['action']} {r['delta_shares']:+d} 股"
                 f"　{r['current_shares']} → {r['target_shares']}")
        L.append(f"    决策价 {r['decision_price']:,.2f}　目标 "
                 f"{(r['target_weight'] or 0):.2%}　有效至 {r['expires_on']}")
        L.append(f"    合规 {r['compliance_status']}")
        kb.append([{"text": f"✅ 批准 {r['ticker']}", "callback_data": f"ap:{r['id']}"},
                   {"text": f"❌ 否决 {r['ticker']}", "callback_data": f"rj:{r['id']}"}])
    L += ["", "批准 = 固定这个整数股数，下一个交易日开盘成交它。"]
    return "\n".join(L), kb


def _book_msg() -> str:
    from . import book, marks
    pid = _pid()
    if not book.is_book_portfolio(pid):
        return f"{pid} 还没开账。先在电脑上跑一次 run_rebalance.py --open-book"
    hs = book.holdings(pid)
    px = marks.price_map([h["ticker"] for h in hs]) if hs else {}
    return book.render(pid, px)


def _do(cmd: str, arg: str, chat_id: str) -> None:
    from . import compliance, proposal_store
    pid = _pid()

    if cmd in ("/start", "/help"):
        send(HELP, chat_id)
    elif cmd == "/pending":
        t, kb = _pending_msg()
        send(t, chat_id, kb)
    elif cmd == "/book":
        send(_book_msg(), chat_id)
    elif cmd == "/stats":
        s = proposal_store.stats(pid)
        send(f"{pid} 提案状态：\n" + ("\n".join(f"  {k}　{v}" for k, v in s.items())
                                      or "（无记录）"), chat_id)
    elif cmd in ("/approve", "/reject"):
        if not arg:
            send(f"用法：{cmd} <提案号或代码>"
                 + ("　理由" if cmd == "/reject" else ""), chat_id)
            return
        parts = arg.split(None, 1)
        ref, reason = parts[0], (parts[1] if len(parts) > 1 else "")
        rows = proposal_store.get_by_ref(ref, pid)
        if not rows:
            send(f"{ref}：找不到提案。/pending 看清单。", chat_id)
            return
        if len(rows) > 1:
            send(f"{ref}：有 {len(rows)} 条同代码提案，**不自动挑一条**，请用提案号："
                 + "、".join(f"#{r['id']}({r['state']})" for r in rows), chat_id)
            return
        r = rows[0]
        to = proposal_store.APPROVED if cmd == "/approve" else proposal_store.REJECTED
        if to == proposal_store.APPROVED and r["compliance_status"] == compliance.BREACH:
            send(f"#{r['id']} {r['ticker']}：事前合规**破限**，Telegram 上不提供强批。"
                 f"要强行批准请在电脑上跑：\n"
                 f"  python run_approve.py --approve {r['id']} --force", chat_id)
            return
        try:
            new = proposal_store.transition(r["id"], to, actor=f"ceo:tg:{chat_id}",
                                            note=reason)
        except ValueError as e:
            send(str(e), chat_id)
            return
        verb = "已批准" if to == proposal_store.APPROVED else "已否决"
        send(f"#{new['id']} {new['ticker']} {verb}　{new['action']} "
             f"{new['delta_shares']:+d} 股"
             + (f"\n理由：{reason}" if reason else "")
             + ("\n\n下一步 /execute（下一个交易日开盘成交）"
                if to == proposal_store.APPROVED else ""), chat_id)
    elif cmd == "/approveall":
        rows = proposal_store.pending(pid)
        if not rows:
            send("没有待批准的提案。", chat_id)
            return
        ok, blocked = [], []
        for r in rows:
            if r["compliance_status"] == compliance.BREACH:
                blocked.append(r)
                continue
            proposal_store.transition(r["id"], proposal_store.APPROVED,
                                      actor=f"ceo:tg:{chat_id}", note="批准全部")
            ok.append(r)
        msg = f"已批准 {len(ok)} 条：" + "、".join(f"{r['ticker']}" for r in ok)
        if blocked:
            msg += (f"\n跳过 {len(blocked)} 条（合规破限，需在电脑上 --force）："
                    + "、".join(r["ticker"] for r in blocked))
        send(msg + "\n\n下一步 /execute", chat_id)
    elif cmd == "/execute":
        send("正在执行……", chat_id)
        send(_run("run_execute.py"), chat_id)
    elif cmd == "/rebalance":
        send("正在出提案……", chat_id)
        send(_run("run_rebalance.py"), chat_id)
        t, kb = _pending_msg()
        send(t, chat_id, kb)
    elif cmd == "/pc":
        send("正在跑 CRO→PC，可能要一两分钟……", chat_id)
        send(_run("run_pc.py"), chat_id)
    else:
        send(f"不认识的指令 {cmd}\n\n{HELP}", chat_id)


def _on_callback(cb: dict) -> None:
    """按钮点击。**先应答再干活**——不应答的话手机上那个转圈会一直转，
    人会以为没点上，然后再点一次。"""
    from . import proposal_store
    data = str(cb.get("data") or "")
    chat_id = str(((cb.get("message") or {}).get("chat") or {}).get("id") or "")
    try:
        _api("answerCallbackQuery", callback_query_id=cb.get("id"), text="收到")
    except Exception:                                        # noqa: BLE001
        pass
    if str(chat_id) != str(settings.TG_CHAT_ID):
        log.warning("忽略来自 chat %s 的按钮（只听 %s）", chat_id, settings.TG_CHAT_ID)
        return
    if ":" not in data:
        return
    kind, ref = data.split(":", 1)
    to = {"ap": proposal_store.APPROVED, "rj": proposal_store.REJECTED}.get(kind)
    if not to:
        return
    p = proposal_store.get(int(ref))
    if not p:
        send(f"提案 #{ref} 不存在了。", chat_id)
        return
    from . import compliance
    if to == proposal_store.APPROVED and p["compliance_status"] == compliance.BREACH:
        send(f"#{p['id']} {p['ticker']}：合规破限，按钮不提供强批。"
             f"电脑上：python run_approve.py --approve {p['id']} --force", chat_id)
        return
    try:
        new = proposal_store.transition(int(ref), to, actor=f"ceo:tg:{chat_id}",
                                        note="Telegram 按钮")
    except ValueError as e:
        send(str(e), chat_id)
        return
    verb = "已批准" if to == proposal_store.APPROVED else "已否决"
    send(f"#{new['id']} {new['ticker']} {verb}　{new['delta_shares']:+d} 股"
         + ("\n下一步 /execute" if to == proposal_store.APPROVED else ""), chat_id)


def serve(once: bool = False) -> int:
    """长轮询主循环。Ctrl-C 退出。"""
    tk, shared = token()
    if not tk:
        print("没有 bot token。在 .env 里设 CIO_CTRL_BOT_TOKEN（推荐）"
              "或 TELEGRAM_BOT_TOKEN。")
        return 1
    if not settings.TG_CHAT_ID:
        print("没有 TELEGRAM_CHAT_ID —— 不知道该听谁的，拒绝启动。")
        return 1
    if shared:
        print("=" * 68)
        print("⚠ 正在与 OpenClaw 共用同一个 bot token。")
        print("Telegram 的一条更新只投递给一个 getUpdates 消费者，")
        print("两边会互相抢：**指令会随机丢失，而且没有任何提示**。")
        print("去 @BotFather /newbot 另建一个 bot，把 token 设成 CIO_CTRL_BOT_TOKEN。")
        print("=" * 68)
    me = _api("getMe").get("result", {})
    print(f"控制台已启动：@{me.get('username', '?')}　只听 chat {settings.TG_CHAT_ID}")
    print("手机上发 /help 试试。Ctrl-C 退出。")
    send("CIO 控制台已上线。\n\n" + HELP)
    offset = _read_offset()
    while True:
        try:
            r = _api("getUpdates", offset=offset, timeout=POLL_TIMEOUT)
        except RuntimeError as e:
            print(e)
            return 2
        except Exception as e:                               # noqa: BLE001
            log.warning("取更新失败，5 秒后重试：%s", e)
            time.sleep(5)
            continue
        for u in r.get("result", []):
            offset = max(offset, int(u["update_id"]) + 1)
            _write_offset(offset)
            if "callback_query" in u:
                try:
                    _on_callback(u["callback_query"])
                except Exception as e:                       # noqa: BLE001
                    log.error("按钮处理异常：%s", e)
                continue
            m = u.get("message") or u.get("edited_message") or {}
            chat_id = str((m.get("chat") or {}).get("id") or "")
            text = str(m.get("text") or "").strip()
            if not text:
                continue
            if chat_id != str(settings.TG_CHAT_ID):
                # **不回复陌生 chat**，只记日志。回复等于确认这个 bot 存在。
                log.warning("忽略来自 chat %s 的消息：%s", chat_id, text[:80])
                continue
            cmd, _, arg = text.partition(" ")
            cmd = cmd.split("@")[0].lower()
            log.info("指令 %s %s", cmd, arg)
            try:
                _do(cmd, arg.strip(), chat_id)
            except Exception as e:                           # noqa: BLE001
                log.error("处理 %s 异常：%s", cmd, e)
                send(f"处理 {cmd} 时出错：{type(e).__name__}: {e}", chat_id)
        if once:
            return 0
