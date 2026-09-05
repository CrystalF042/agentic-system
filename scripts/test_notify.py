#!/usr/bin/env python3
"""Build 5 自测 —— **「N 条提案等你批准」这句话，到没到你手机上。**

    python scripts/test_notify.py

第一条用例是这一版存在的理由：**演习不算送到。**
`deliver.send_text()` 在 `CIO_TG_DRYRUN=1` 时 return True ——
照着这个返回值记通知台账，一次演习就能让那条真正要她批的消息永远不再发出。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("CIO_MARKET", "us")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _no_network                                             # noqa: E402,F401

from cio import heartbeat as hbmod                             # noqa: E402
from cio import notify as nt                                   # noqa: E402
from cio import proposal_store as ps                           # noqa: E402

OK: list = []
BAD: list = []
PID = "TEST"


def check(name, fn):
    try:
        fn()
        OK.append(name)
        print(f"  OK    {name}")
    except Exception as e:                                     # noqa: BLE001
        BAD.append((name, f"{type(e).__name__}: {e}"))
        print(f"  ERR   {name}\n          {type(e).__name__}: {e}")


# ------------------------------------------------------------------ 夹具

def _tmp_state():
    td = tempfile.TemporaryDirectory()
    nt.STATE_PATH = Path(td.name) / "pending.json"
    return td


def _row(pid=1, ticker="AMD", action="BUY", delta=10, shares=10,
         decision_date="2026-09-04", expires="2026-09-08", price=180.0,
         weight=0.04, comp="PASS"):
    return {"id": pid, "ticker": ticker, "action": action, "delta_shares": delta,
            "current_shares": 0, "target_shares": shares,
            "decision_date": decision_date, "expires_on": expires,
            "decision_price": price, "target_weight": weight,
            "compliance_status": comp, "state": ps.PENDING_APPROVAL}


class _Sender:
    """假的发送端。**记下每次被调用时的文本**，用来断"真的发了什么"。"""

    def __init__(self, outcome=nt.SENT):
        self.outcome, self.calls = outcome, []

    def __call__(self, text, keyboard, dry_run=False):
        self.calls.append({"text": text, "kb": keyboard, "dry_run": dry_run})
        return nt.DRYRUN if dry_run else self.outcome


def _with(rows, sender=None, real_send=False):
    """装上假的待批清单和假的发送端。返回 sender。"""
    ps.pending = lambda pid: list(rows)
    if not real_send:
        s = sender or _Sender()
        nt._send = s
        return s
    return None


def _restore():
    import importlib
    for m in ("cio.notify", "cio.proposal_store"):
        importlib.reload(sys.modules[m])


# ------------------------------------------------------------------ 用例

def t_a_rehearsal_is_not_a_delivery():
    """**这一版存在的理由。**

    `deliver.send_text()` 在 `CIO_TG_DRYRUN=1` 时 return True ——
    "演习"和"真发出去了"在返回值上长得一样。照着它记通知台账：

        演习跑了一次 → 台账记「已通知」 → 以后不再推
        → 那条真正要她批的消息，一次都不会发出去

    所以本模块用四态，**只有 SENT 才写台账**。
    """
    try:
        with _tmp_state():
            s = _with([_row()])
            r = nt.notify_pending(PID, "2026-09-04", dry_run=True)
            assert r["outcome"] == nt.DRYRUN, r
            assert r["sent"] is False, r
            assert s.calls and s.calls[0]["dry_run"] is True
            # **台账一个字都不许写**
            assert nt.state(PID)["fingerprint"] == "", nt.state(PID)
            assert nt.state(PID)["n_sent"] == 0
            # 下一次（真发）**还得推**
            r2 = nt.notify_pending(PID, "2026-09-04")
            assert r2["sent"] is True and r2["outcome"] == nt.SENT, r2
            assert nt.state(PID)["fingerprint"] == r2["fingerprint"]
            assert nt.state(PID)["n_sent"] == 1
    finally:
        _restore()


def t_unconfigured_and_failed_do_not_count_either():
    """**没配 token 和发送失败，同样不许记成"已通知"。**

    三种"没送到"的处理方式不同（演习不该重试、没配置该喊、失败该重试），
    但它们有一个共同点：**都不能让台账以为这批已经通知过了。**
    """
    for bad in (nt.UNCONFIGURED, nt.FAILED):
        try:
            with _tmp_state():
                _with([_row()], _Sender(bad))
                r = nt.notify_pending(PID, "2026-09-04")
                assert r["outcome"] == bad and r["sent"] is False, r
                assert nt.state(PID)["fingerprint"] == "", (bad, nt.state(PID))
                # 再跑一次 —— **必须再试**，不能因为"推过了"而沉默
                s2 = _with([_row()], _Sender(nt.SENT))
                r2 = nt.notify_pending(PID, "2026-09-04")
                assert r2["sent"] is True, (bad, r2)
                assert len(s2.calls) == 1, s2.calls
        finally:
            _restore()


def t_not_delivered_is_an_alert():
    """**有事要她批、而提醒没送到，必须是告警，不是日志里的一行。**

    心跳掉一次第二天会补上；这一条掉了，那笔交易就静静地过期作废。
    """
    try:
        with _tmp_state():
            _with([_row()], _Sender(nt.FAILED))
            rep = hbmod.Report("2026-09-04")
            with rep.stage("ceo") as hb:
                nt.notify_pending(PID, "2026-09-04", hb=hb)
            al = rep.alerts()
            assert al and any("没送到" in t for _l, t in al), al
            head = rep.render().split("[技术快照]")[0]
            assert "没送到" in head, "告警没印在报告最上方：\n" + rep.render()
    finally:
        _restore()


def t_a_rehearsal_does_not_light_the_alarm():
    """**有意不发 ≠ 没送到。**

    一个被关掉的推送和一个坏掉的推送不许长得一样：
    演习每天点一次告警，那盏灯就废了 —— **常亮的灯 = 不亮的灯。**
    """
    try:
        with _tmp_state():
            _with([_row()])
            rep = hbmod.Report("2026-09-04")
            with rep.stage("ceo") as hb:
                r = nt.notify_pending(PID, "2026-09-04", hb=hb, dry_run=True)
            assert r["outcome"] == nt.DRYRUN
            assert not rep.alerts(), f"演习点了告警：{rep.alerts()}"
            assert any("演习" in n for n in hb.notes), hb.notes
    finally:
        _restore()


def t_same_batch_is_not_pushed_twice():
    """**同一批不重复轰炸** —— 人会开始忽略它。"""
    try:
        with _tmp_state():
            s = _with([_row()])
            nt.notify_pending(PID, "2026-09-04")
            r2 = nt.notify_pending(PID, "2026-09-04")
            assert r2["sent"] is False and not r2["outcome"], r2
            assert "没变" in r2["reason"], r2["reason"]
            assert len(s.calls) == 1, f"同一批推了 {len(s.calls)} 次"
    finally:
        _restore()


def t_a_different_batch_of_the_same_size_must_be_pushed():
    """**去重按内容，不按条数。**

    3 条换成另外 3 条 → 条数一样 → 按条数去重就**一条都不推**，
    而那 3 条是全新的决定。判别力全在这一条上。
    """
    try:
        with _tmp_state():
            s = _with([_row(1, "AMD"), _row(2, "MU"), _row(3, "AVGO")])
            nt.notify_pending(PID, "2026-09-04")
            assert len(s.calls) == 1
            # 条数一样，内容全变
            ps.pending = lambda pid: [_row(4, "NVDA"), _row(5, "SLB"),
                                      _row(6, "BBY")]
            r2 = nt.notify_pending(PID, "2026-09-04")
            assert r2["sent"] is True, r2
            assert len(s.calls) == 2, "换了一批却没推"
            assert "NVDA" in s.calls[1]["text"], s.calls[1]["text"]
            # 连股数变了也要推（同一只票、同一个号，Δ 不一样了）
            ps.pending = lambda pid: [_row(4, "NVDA", delta=99, shares=99),
                                      _row(5, "SLB"), _row(6, "BBY")]
            r3 = nt.notify_pending(PID, "2026-09-04")
            assert r3["sent"] is True, r3
            assert len(s.calls) == 3, "股数变了却没推 —— 批准的是股数"
    finally:
        _restore()


def t_an_item_hanging_too_long_breaks_the_dedupe():
    """**"内容没变"有两种完全不同的原因。**

        刚推过，她还没来得及看   → 不用再推
        挂了三个交易日没人动     → **必须再推，而且要说挂了多久**

    一条挂到过期自动作废的提案，和一条从来没产生过的提案，
    在结果上一模一样。
    """
    try:
        with _tmp_state():
            s = _with([_row(decision_date="2026-09-04", expires="2026-09-30")])
            nt.notify_pending(PID, "2026-09-04")
            assert len(s.calls) == 1
            # 同一天再跑：不推
            nt.notify_pending(PID, "2026-09-04")
            assert len(s.calls) == 1
            # 两个交易日之后：内容一样，**但必须再推一次**
            r = nt.notify_pending(PID, "2026-09-08")     # 周一
            assert r["sent"] is True and r["reminding"] is True, r
            assert r["aged"] == 1, r
            assert len(s.calls) == 2, "挂了两个交易日却一声不吭"
            assert "已挂" in s.calls[1]["text"], s.calls[1]["text"]
            assert "还没有动静" in s.calls[1]["text"]
            # 同一天不再重复提醒
            nt.notify_pending(PID, "2026-09-08")
            assert len(s.calls) == 2, "同一天提醒了两次"
    finally:
        _restore()


def t_aging_is_counted_in_trading_days():
    """**按交易日，不按自然日。**

    周五提的、周一还没批，那是 1 个交易日不是 3 天。
    按自然日算的话，每个周末都会触发一次"挂太久"。
    """
    fri = _row(decision_date="2026-09-04")      # 2026-09-04 是周五
    assert nt.aged([fri], "2026-09-07") == [], "周一就报挂太久（按自然日算了）"
    got = nt.aged([fri], "2026-09-08")
    assert len(got) == 1 and got[0]["_age"] == 2, got


def t_expiring_soon_is_said_out_loud():
    """**过期作废是静默的，得提前说。**

    过期不是拒绝——它是"这次决定被时间吃掉了"，而账本上什么都看不出来。
    """
    try:
        with _tmp_state():
            s = _with([_row(expires="2026-09-05")])
            rep = hbmod.Report("2026-09-04")
            with rep.stage("ceo") as hb:
                r = nt.notify_pending(PID, "2026-09-04", hb=hb)
            assert r["expiring"] == 1, r
            assert "过期作废" in s.calls[0]["text"], s.calls[0]["text"]
            assert any("过期作废" in t for _l, t in rep.alerts()), rep.alerts()
    finally:
        _restore()


def t_zero_pending_sends_nothing():
    """**每天推一条「今天 0 条」就是一盏常亮的灯。**

    心跳里照样有 `pending 0` —— 那是"跑过了"的证据。
    """
    try:
        with _tmp_state():
            s = _with([])
            rep = hbmod.Report("2026-09-04")
            with rep.stage("ceo") as hb:
                r = nt.notify_pending(PID, "2026-09-04", hb=hb)
            assert r["pending"] == 0 and r["sent"] is False, r
            assert not s.calls, "没有待批却发了消息"
            assert not rep.alerts(), rep.alerts()
            assert hb.counts.get("pending") == 0, hb.counts
            assert "pending 0" in rep.render(), rep.render()
            assert "自动化到这一步为止" in rep.render()
    finally:
        _restore()


def t_the_message_carries_both_paths():
    """**按钮要有人接才有用。**

    `run_tgbot.py` 没在跑的时候，点按钮的表现是转一下圈然后什么都不发生 ——
    没有任何提示。所以文本里始终带命令行写法。
    """
    try:
        with _tmp_state():
            s = _with([_row(1, "AMD"), _row(2, "MU")])
            nt.notify_pending(PID, "2026-09-04")
            txt, kb = s.calls[0]["text"], s.calls[0]["kb"]
            assert "run_approve.py --approve" in txt, txt
            assert "run_tgbot.py" in txt, txt
            assert len(kb) == 2, kb
            assert kb[0][0]["callback_data"] == "ap:1", kb
            assert kb[0][1]["callback_data"] == "rj:1", kb
            # 批准的是股数这件事要写在消息里
            assert "整数股数" in txt, txt
            assert "+10 股" in txt, txt
    finally:
        _restore()


def t_force_pushes_anyway():
    """`--force`：明知推过了也再推一次（比如上次发歪了）。"""
    try:
        with _tmp_state():
            s = _with([_row()])
            nt.notify_pending(PID, "2026-09-04")
            nt.notify_pending(PID, "2026-09-04")
            assert len(s.calls) == 1
            r = nt.notify_pending(PID, "2026-09-04", force=True)
            assert r["sent"] is True and len(s.calls) == 2, r
    finally:
        _restore()


def t_a_corrupt_ledger_pushes_rather_than_goes_silent():
    """**台账读不动的时候，宁可多推一次，也不许沉默。**

    反过来（读不动就当成"已通知"）的后果是：一个坏掉的文件
    能让所有提醒永远停发，而且没有任何一处报错。
    """
    try:
        with _tmp_state():
            nt.STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            nt.STATE_PATH.write_text("{ 这不是 json", encoding="utf-8")
            # **读不动 = 从来没通知过。** 断在 state() 上，而不只是断
            # "结果推了"：随便返回一个假指纹也会让它推，
            # 那样这条用例对"读不动被当成已通知"就没有判别力。
            st = nt.state(PID)
            assert st["fingerprint"] == "", f"读不动却记着一批：{st}"
            assert st["n_sent"] == 0, f"读不动却说通知过 {st['n_sent']} 次"
            assert st["last_sent_day"] == "", st
            assert st["last_reminded_day"] == "", st
            s = _with([_row()])
            r = nt.notify_pending(PID, "2026-09-04")
            assert r["sent"] is True and len(s.calls) == 1, r
            # 而且这一次要真的写进去 —— 坏文件不能让台账永远写不下
            assert nt.state(PID)["n_sent"] == 1, nt.state(PID)
    finally:
        _restore()


def t_the_dry_run_path_is_the_same_path():
    """**预演和真跑必须是同一条路。**

    另写一条预演分支的话，预演验证的就是那条永远不会真跑的代码。
    这里断的是：`dry_run` 只影响最后发送那一步，
    **该算的（指纹、挂太久、快过期、文本）一样都不少。**
    """
    try:
        with _tmp_state():
            s = _with([_row(expires="2026-09-05",
                            decision_date="2026-09-01")])
            r = nt.notify_pending(PID, "2026-09-04", dry_run=True)
            assert r["pending"] == 1 and r["fingerprint"], r
            assert r["aged"] == 1 and r["expiring"] == 1, r
            assert s.calls and "AMD" in s.calls[0]["text"], s.calls
            assert s.calls[0]["kb"], "预演连按钮都没算"
    finally:
        _restore()


def t_only_sent_is_in_delivered():
    """**四态里只有一个算送到。** 这条防的是有人往 `DELIVERED` 里多加一个。"""
    assert nt.DELIVERED == (nt.SENT,), nt.DELIVERED
    for bad in (nt.DRYRUN, nt.UNCONFIGURED, nt.FAILED):
        assert bad not in nt.DELIVERED, bad
    assert set(nt.OUTCOMES) == {nt.SENT, nt.DRYRUN, nt.UNCONFIGURED, nt.FAILED}


TESTS = [
    ("**演习不算送到**", t_a_rehearsal_is_not_a_delivery),
    ("**没配置 / 发送失败同样不记台账，且会重试**",
     t_unconfigured_and_failed_do_not_count_either),
    ("**没送到是告警，不是日志里的一行**", t_not_delivered_is_an_alert),
    ("**演习不点告警（有意不发 ≠ 没送到）**", t_a_rehearsal_does_not_light_the_alarm),
    ("同一批不重复轰炸", t_same_batch_is_not_pushed_twice),
    ("**换一批同样条数的，必须推**",
     t_a_different_batch_of_the_same_size_must_be_pushed),
    ("**挂太久要越过去重再提醒一次**", t_an_item_hanging_too_long_breaks_the_dedupe),
    ("**挂多久按交易日算**", t_aging_is_counted_in_trading_days),
    ("**快过期要说出来**", t_expiring_soon_is_said_out_loud),
    ("0 条不发消息（但心跳里有）", t_zero_pending_sends_nothing),
    ("**按钮和命令行两条路都给**", t_the_message_carries_both_paths),
    ("--force 明知推过也再推", t_force_pushes_anyway),
    ("**台账读不动时宁可多推，不许沉默**",
     t_a_corrupt_ledger_pushes_rather_than_goes_silent),
    ("**预演和真跑是同一条路**", t_the_dry_run_path_is_the_same_path),
    ("四态里只有一个算送到", t_only_sent_is_in_delivered),
]

print("=" * 72)
print("Build 5 自测 —— 那句话到没到你手机上")
print("=" * 72)
for _n, _f in TESTS:
    check(_n, _f)

print("\n" + "=" * 72)
if BAD:
    print(f"{len(BAD)} 项失败 / 共 {len(TESTS)}")
    for n, e in BAD:
        print(f"  · {n}\n      {e}")
    raise SystemExit(1)
print(f"全部 {len(OK)} 项通过。")
raise SystemExit(0)
