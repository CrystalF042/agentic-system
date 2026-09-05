#!/usr/bin/env python3
"""Build 4 自测 —— **CRO → PC → 提案，然后停在授权闸前面。**

    python scripts/test_pipeline.py

第一条用例是这一版存在的理由：**自动化不许自己跨过 Approve。**
第二条紧随其后：**测量口径不符不是"风险高"** ——
一个错的原因比没有原因更糟，它会把人叫去查一个不存在的问题。
"""
from __future__ import annotations

import ast
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
from cio import proposal_store, propose                        # noqa: E402
from cio.research import pipeline as pl                        # noqa: E402
from cio.research import queue as q                            # noqa: E402
from cio.research import router as rt                          # noqa: E402
from cio.research import trigger as tg                         # noqa: E402

OK: list = []
BAD: list = []

LIN = {"setup_version": "setup-1.0.1", "score_version": "score-2.1.0"}


def check(name, fn):
    try:
        fn()
        OK.append(name)
        print(f"  OK    {name}")
    except Exception as e:                                     # noqa: BLE001
        BAD.append((name, f"{type(e).__name__}: {e}"))
        print(f"  ERR   {name}\n          {type(e).__name__}: {e}")


# ------------------------------------------------------------------ 夹具

def _tmp_queue():
    td = tempfile.TemporaryDirectory()
    q.QUEUE_PATH = Path(td.name) / "q.jsonl"
    return td


def _queued(symbol="AMD", score=0.9, as_of="2026-09-04"):
    """造一条走到 RESEARCHED 的队列条目。"""
    for task in rt.route([tg.technical_trigger(symbol, as_of, as_of, LIN,
                                               score=score)]):
        q.enqueue(task)
    it = [i for i in q.items().values() if i.symbol == symbol][0]
    q.transition(it.key, q.ENRICHING, "t")
    q.transition(it.key, q.RESEARCHING, "t")
    q.transition(it.key, q.RESEARCHED, "t")
    return q.get(it.key)


def _fake_layer(*, w_final=0.04, veto=False, veto_reason="", raise_units=False,
                thesis=True):
    """把 CRO/PC/取数全换成假的，**不联网、不调模型、不写库**。

    返回被真的调用过的名单，用来断"它到底跑了没有"。
    """
    calls = []
    pl._record = lambda item, cro, sz: calls.append(
        ("record", item.symbol, cro.get("veto"), sz.get("w_final")))
    pl._thesis_for = (lambda s, tid: {"id": 7, "direction": "看多",
                                      "conviction": "中", "invalidations": ["x"],
                                      "material_verdict": "SUFFICIENT"}
                      if thesis else {})

    import cio.cro_inputs as ci
    import cio.risk_officer as ro
    import cio.sizing as sz_mod
    ci.measures_for = lambda s: {"sigma_60": 0.30, "sigma_252": 0.28,
                                 "beta": 1.1, "maxdd": -0.2,
                                 "corr_bench": 0.7, "liquidity_cap": None,
                                 "beta_n_aligned": 300}

    def _assess(**kw):
        calls.append(("cro", kw.get("ticker")))
        if raise_units:
            raise ValueError("sigma_60=40.74 看起来是百分数，POLICY 用的是小数")
        return {"ticker": kw["ticker"], "direction": kw.get("direction", ""),
                "conviction": kw.get("conviction", ""),
                "evidence_gate": kw.get("evidence_gate", ""),
                "thesis_id": kw.get("thesis_id", 0), "regime": kw.get("regime", ""),
                "base_risk_budget": 0.02, "conviction_multiplier": 1.0,
                "regime_multiplier": 1.0, "adjusted_risk_budget": 0.02,
                "caps": {"single_name": 0.08}, "measures": {},
                "risk_constraints": [], "binding_risk_constraint": "",
                "veto": veto, "veto_reason": veto_reason,
                "portfolio_risk_cap": 0.2, "notes": []}
    ro.assess_one = _assess

    def _size(**kw):
        calls.append(("pc", kw.get("ticker")))
        return {"ticker": kw["ticker"], "evidence_gate": kw.get("evidence_gate"),
                "sigma_60": 0.30, "sigma_252": 0.28, "sigma_blend": 0.29,
                "sigma_floor": 0.15, "sigma_effective": 0.29,
                "sigma_binding_component": ["sigma_60"],
                "w_raw": 0.069, "w_final": w_final,
                "caps_evaluated": ["single_name"], "caps_not_evaluated": [],
                "binding_position_constraint": (["single_name"] if w_final
                                                is not None else []),
                "reason": "" if w_final is not None else "σ 算不出来，不给仓位"}
    sz_mod.size_one = _size

    import cio.regime as rgm
    rgm.assess = lambda fetch=None: {"regime": "neutral", "note": ""}
    return calls


def _restore():
    """把上面换掉的都换回来。**不还原的话下一条用例测的是上一条的假货。**"""
    import importlib
    for m in ("cio.cro_inputs", "cio.risk_officer", "cio.sizing", "cio.regime",
              "cio.research.pipeline"):
        importlib.reload(sys.modules[m])


# ------------------------------------------------------------------ 用例

def t_automation_stops_at_the_approval_gate():
    """**这一版存在的理由：自动化跑到「待你批准」就必须停。**

    她定的：可见性到处都有，硬闸只有一道。而"只有一道"如果只是
    我们的约定，那它就不是闸——**必须由代码保证绕不过去**。

    钉三层：
      一、状态机：`APPROVED` 只能从 `PENDING_APPROVAL` 来
      二、源码：自动那几个模块里**不许出现**把队列/提案改成 APPROVED 的调用
      三、跑一遍：终点是 PENDING_APPROVAL，且账本上一笔交易都没有
    """
    # ---- 一、状态机 ----
    for st in q.STATES:
        if st == q.PENDING_APPROVAL:
            continue
        assert q.APPROVED not in q.LEGAL.get(st, ()), \
            f"{st} 居然可以直接到 APPROVED —— 授权闸有旁路"
    assert q.APPROVED in q.LEGAL[q.PENDING_APPROVAL]

    # ---- 二、源码：自动链上不许有批准动作 ----
    autos = ["src/cio/research/pipeline.py", "src/cio/research/scheduler.py",
             "src/cio/research/router.py", "src/cio/propose.py",
             "src/cio/notify.py", "scripts/research_run.py",
             "scripts/notify_run.py"]
    for rel in autos:
        src = (ROOT / rel).read_text("utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = (fn.attr if isinstance(fn, ast.Attribute)
                    else getattr(fn, "id", ""))
            if name != "transition":
                continue
            seg = ast.get_source_segment(src, node) or ""
            assert "APPROVED" not in seg or "PENDING_APPROVAL" in seg, (
                f"{rel} 里有一次把状态改成 APPROVED 的调用 —— "
                f"**自动化不许自己授权资本**：\n{seg}")

    # ---- 三、真的跑一遍 ----
    real_prop = propose.for_run
    try:
        with _tmp_queue():
            calls = _fake_layer(w_final=0.04)
            it = _queued("AMD")
            propose.for_run = lambda **kw: {
                "status": propose.COMPLETED, "note": "", "run_id": kw["run_id"],
                "saved": [{"id": 91, "ticker": "AMD", "action": "BUY",
                           "state": proposal_store.PENDING_APPROVAL}],
                "expired": [], "decisions": [], "rows": [], "summary": {},
                "compliance": {}, "expires": "", "nav": {}, "prices": {},
                "price_detail": {}, "held": {}, "n_marked": 0, "renders": {}}
            res = pl.advance("2026-09-04", portfolio_id="TEST")
            assert res["targets"] == 1, res
            after = q.get(it.key)
            assert after.state == q.PENDING_APPROVAL, after.state
            assert after.proposal_id == 91, after.proposal_id
            assert q.counts().get(q.APPROVED, 0) == 0, \
                "自动跑一遍之后居然有条目自己进了 APPROVED"
            assert ("pc", "AMD") in calls
    finally:
        propose.for_run = real_prop
        _restore()


def t_unit_mismatch_is_not_a_veto():
    """**口径不符 ≠ 风险高。**

    `check_units` 抛 ValueError 时，真实情况是"我们没法评估它"，
    落成一次否决就变成"风控认为它危险"——
    **一个错的原因比没有原因更糟**：它把人叫去查一个不存在的问题。
    """
    try:
        with _tmp_queue():
            _fake_layer(raise_units=True)
            it = _queued("BBY")
            res = pl.advance("2026-09-04", portfolio_id="TEST")
            assert res["unmeasurable"] == 1, res
            assert res["vetoed"] == 0, "口径不符被记成了 CRO 否决"
            assert res["targets"] == 0 and res["failed"] == 0, res
            after = q.get(it.key)
            assert after.state == q.FAILED, after.state
            assert "口径" in after.note, after.note
            assert "否决" not in after.note, \
                f"未评估的条目上写着「否决」：{after.note}"
            txt = "\n".join(pl.describe(res))
            assert "测量不可用" in txt, txt
            assert "否决 0" in txt, txt
    finally:
        _restore()


def t_a_veto_is_an_alert_not_a_note():
    """**稀有事件排在第五节第三行，等于没有发生过。**

    一道从没否决过的风控闸，和一道否决了但没人看见的风控闸，
    在结果上是同一道闸。所以否决走 alert，印在报告最上方。
    """
    try:
        with _tmp_queue():
            _fake_layer(veto=True, veto_reason="已实现波动率(60日年化) 92.31% "
                                               "触及否决线 80.00%")
            it = _queued("MU")
            rep = hbmod.Report("2026-09-04")
            with rep.stage("cro_pc") as hb:
                res = pl.advance("2026-09-04", portfolio_id="TEST", hb=hb)
            assert res["vetoed"] == 1 and res["targets"] == 0, res
            assert q.get(it.key).state == q.VETOED
            # **是 alert，不是 note。**
            assert not any("否决 MU" in n for n in hb.notes), \
                "否决被塞进了 notes —— 那是六节报告里的第五节第三行"
            al = rep.alerts()
            # **只此一条。** "今天没有一只走到目标"是最常见的正常状态，
            # 它不许也点一盏灯 —— 常亮的灯 = 不亮的灯。
            assert len(al) == 1 and "MU" in al[0][1], al
            text = rep.render()
            head = text.split("[技术快照]")[0]
            assert "MU" in head, \
                "告警没有印在报告最上方：\n" + text
            assert "92.31%" in head, "告警里没有把阈值和数值印出来：\n" + head
    finally:
        _restore()


def t_no_position_is_a_conclusion_not_a_disappearance():
    """**PC 说"给不出仓位"是一个结论，不是一次失败，也不许无声消失。**

    走到 NO_TRADE 终态、带原因、进计数。一条走到这里就从计数里蒸发的记录，
    和一条根本没进来的，看起来一样。
    """
    try:
        with _tmp_queue():
            _fake_layer(w_final=None)
            it = _queued("SLB")
            res = pl.advance("2026-09-04", portfolio_id="TEST")
            assert res["no_position"] == 1, res
            assert res["vetoed"] == 0, "算不出仓位被记成了风控否决"
            after = q.get(it.key)
            assert after.state == q.NO_TRADE, after.state
            assert q.NO_TRADE in q.TERMINAL
            assert after.note, "无仓位没有写原因"
            # **计数里要看得见。** counts 印全部状态，0 也印。
            assert q.counts().get(q.NO_TRADE) == 1, q.counts()
            assert "无仓位 1" in "\n".join(pl.describe(res))
            # 没有目标就不该去提案，而且要说一声
            assert res["propose_status"] == "no_targets", res
    finally:
        _restore()


def t_veto_and_no_position_are_not_the_same_bucket():
    """**VETOED 和 NO_TRADE 合成一个，就答不出"那道闸拦下过什么"。**

    而那正是判断要不要拆闸的唯一依据。这条用两只票同时跑，
    **一只被否决、一只算不出仓位**，断它们落在不同的状态上。
    """
    try:
        with _tmp_queue():
            _fake_layer(veto=True, veto_reason="Beta 2.30 触及否决线 2.00")
            a = _queued("AMD")
            res1 = pl.advance("2026-09-04", portfolio_id="TEST")
            assert res1["vetoed"] == 1, res1
            _restore()
            _fake_layer(w_final=None)
            b = _queued("BBY")
            res2 = pl.advance("2026-09-04", portfolio_id="TEST")
            assert res2["no_position"] == 1, res2
            sa, sb = q.get(a.key).state, q.get(b.key).state
            assert sa == q.VETOED and sb == q.NO_TRADE, (sa, sb)
            assert sa != sb, "风控否决和算不出仓位落进了同一个桶"
    finally:
        _restore()


def t_targets_without_a_proposal_must_shout():
    """**算出了目标却没落成提案，是这套系统最危险的静默形态。**

    账本没开、没有 PC 运行 —— 都是很正常的状态，但如果它们只让
    提案数停在 0 而不说话，报告上看起来就和"今天没有目标"一模一样。
    """
    real_prop = propose.for_run
    try:
        with _tmp_queue():
            _fake_layer(w_final=0.05)
            it = _queued("NVDA")
            propose.for_run = lambda **kw: {
                "status": propose.BOOK_NOT_OPEN,
                "note": "US_PAPER 还没开账，所以算不出股数、也落不了提案。",
                "run_id": kw["run_id"], "saved": [], "expired": [],
                "decisions": [], "rows": [], "summary": {}, "compliance": {},
                "expires": "", "nav": {}, "prices": {}, "price_detail": {},
                "held": {}, "n_marked": 0, "renders": {}}
            rep = hbmod.Report("2026-09-04")
            with rep.stage("cro_pc") as hb:
                res = pl.advance("2026-09-04", portfolio_id="TEST", hb=hb)
            assert res["targets"] == 1 and res["proposals"] == 0, res
            al = rep.alerts()
            assert al and any("提案没落成" in t for _l, t in al), \
                f"目标算出来了、提案一条没有，而报告一声不吭：{al}"
            assert any("开账" in t for _l, t in al), al
            # 条目停在 PC_COMPLETE：**它没走完，不许标成走完了**
            assert q.get(it.key).state == q.PC_COMPLETE, q.get(it.key).state
    finally:
        propose.for_run = real_prop
        _restore()


def t_queue_and_store_must_reconcile():
    """**"队列里 3 条待批"和"提案库里 2 条待批"，第三条去哪了。**

    两个数长期没人对，第一次对不上时没人知道是今天开始的还是三个月前。
    """
    real_pending = proposal_store.pending
    real_prop = propose.for_run
    try:
        with _tmp_queue():
            _fake_layer(w_final=0.04)
            _queued("AMD")
            propose.for_run = lambda **kw: {
                "status": propose.COMPLETED, "note": "", "run_id": kw["run_id"],
                "saved": [{"id": 5, "ticker": "AMD", "action": "BUY",
                           "state": proposal_store.PENDING_APPROVAL}],
                "expired": [], "decisions": [], "rows": [], "summary": {},
                "compliance": {}, "expires": "", "nav": {}, "prices": {},
                "price_detail": {}, "held": {}, "n_marked": 0, "renders": {}}
            proposal_store.pending = lambda pid: [{"id": 5}]
            rep = hbmod.Report("2026-09-04")
            with rep.stage("cro_pc") as hb:
                res = pl.advance("2026-09-04", portfolio_id="TEST", hb=hb)
            assert res["reconcile"]["ok"] is True, res["reconcile"]
            assert not rep.alerts(), rep.alerts()

            # 现在让提案库那边"少一条"——**必须告警，不是安静地对不上**
            proposal_store.pending = lambda pid: []
            rc = pl.reconcile("TEST")
            assert rc["ok"] is False, rc
            assert rc["queue_pending"] == 1 and rc["store_pending"] == 0, rc
            rep2 = hbmod.Report("2026-09-04")
            with rep2.stage("cro_pc") as hb2:
                pl._report({"picked": 0, "targets": 0, "no_position": 0,
                            "vetoed": 0, "unmeasurable": 0, "failed": 0,
                            "proposals": 0, "pending": 0, "pc_run_id": "",
                            "regime": "", "rows": [], "propose_status": "",
                            "reconcile": rc}, hb2)
            assert any("对不上" in t for _l, t in rep2.alerts()), rep2.alerts()
    finally:
        proposal_store.pending = real_pending
        propose.for_run = real_prop
        _restore()


def t_dry_run_touches_nothing():
    """**预演不许改状态、不许落库、不许领 run_id。**"""
    try:
        with _tmp_queue():
            _fake_layer(w_final=0.04)
            it = _queued("AMD")
            before = q.get(it.key).state
            res = pl.advance("2026-09-04", portfolio_id="TEST", dry_run=True)
            assert q.get(it.key).state == before == q.RESEARCHED
            assert res["pc_run_id"] == "", "预演领了一个 PC run_id"
            assert res["targets"] == 0 and res["proposals"] == 0, res
            assert len(res["rows"]) == 1 and res["rows"][0]["outcome"] == "dry_run"
            assert "预演" in "\n".join(pl.describe(res))
    finally:
        _restore()


def t_one_advance_is_one_pc_run():
    """**一次 advance = 一次 PC 运行**，不是逐票各领一个。

    拆开的话 `run_rebalance.py --run-id` 只能提案其中一只，
    而"另外四只去哪了"没有任何一处能回答。
    """
    seen = []
    real_prop = propose.for_run
    try:
        with _tmp_queue():
            calls = _fake_layer(w_final=0.04)
            pl._record = lambda item, cro, sz: seen.append(pl._RUN["id"])
            _queued("AMD", score=0.9)
            _queued("MU", score=0.8)
            propose.for_run = lambda **kw: {
                "status": propose.COMPLETED, "note": "", "run_id": kw["run_id"],
                "saved": [], "expired": [], "decisions": [], "rows": [],
                "summary": {}, "compliance": {}, "expires": "", "nav": {},
                "prices": {}, "price_detail": {}, "held": {}, "n_marked": 0,
                "renders": {}}
            res = pl.advance("2026-09-04", portfolio_id="TEST")
            assert len(seen) == 2, seen
            assert len(set(seen)) == 1, f"两只票各领了一个 run_id：{seen}"
            assert seen[0] == res["pc_run_id"], (seen, res["pc_run_id"])
            assert calls
    finally:
        propose.for_run = real_prop
        _restore()


def t_heartbeat_records_zero_too():
    """**"今天没有可推进的"和"今天这一节没跑"必须分得开。**"""
    try:
        with _tmp_queue():
            _fake_layer()
            rep = hbmod.Report("2026-09-04")
            with rep.stage("cro_pc") as hb:
                pl.advance("2026-09-04", portfolio_id="TEST", hb=hb)
            assert hb.counts.get("picked") == 0, hb.counts
            assert hb.counts.get("vetoed") == 0, hb.counts
            assert "picked 0" in rep.render() and "vetoed 0" in rep.render()
    finally:
        _restore()


def t_a_quiet_day_lights_no_lamp():
    """**常亮的灯 = 不亮的灯。**

    这条是被自己写的缺陷逼出来的：第一版里"提案没落成"的告警没有先看
    有没有目标，于是**最常见的正常状态**（今天没有一只走到目标权重）
    每天都会点亮它。人学会忽略它之后，真正需要看的那天也会被忽略。

    一天什么都没发生 → **一条告警都不许有**；
    有一只走到了目标却没落成提案 → 必须有。
    """
    real_prop = propose.for_run
    try:
        # 一、空队列：什么都没发生
        with _tmp_queue():
            _fake_layer()
            rep = hbmod.Report("2026-09-04")
            with rep.stage("cro_pc") as hb:
                res = pl.advance("2026-09-04", portfolio_id="TEST", hb=hb)
            assert res["picked"] == 0 and res["targets"] == 0, res
            assert not rep.alerts(), f"什么都没发生却点了灯：{rep.alerts()}"
        _restore()
        # 二、有目标而提案没落成：必须点灯（判别力在这一半）
        with _tmp_queue():
            _fake_layer(w_final=0.05)
            _queued("NVDA")
            propose.for_run = lambda **kw: {
                "status": propose.NO_PC_RUN, "note": "没有任何一次 PC 运行",
                "run_id": kw["run_id"], "saved": [], "expired": [],
                "decisions": [], "rows": [], "summary": {}, "compliance": {},
                "expires": "", "nav": {}, "prices": {}, "price_detail": {},
                "held": {}, "n_marked": 0, "renders": {}}
            rep2 = hbmod.Report("2026-09-04")
            with rep2.stage("cro_pc") as hb2:
                pl.advance("2026-09-04", portfolio_id="TEST", hb=hb2)
            assert rep2.alerts(), "有目标却没落成提案，灯居然不亮"
    finally:
        propose.for_run = real_prop
        _restore()


def t_an_alert_must_say_what_happened():
    """**说不出原因的告警比没有告警更糟** —— 它把人叫去查一个不存在的问题。"""
    st = hbmod.Stage("cro_pc", "风控与仓位")
    for empty in ("", "   ", None):
        try:
            st.alert(empty)
            raise AssertionError(f"空告警被收下了：{empty!r}")
        except ValueError:
            pass
    st.alert("CRO 否决 AMD：Beta 2.30 触及否决线 2.00")
    assert len(st.alerts) == 1
    assert st.to_dict()["alerts"] == st.alerts


def t_measurement_conversion_has_one_home():
    """**CRO 的口径换算只能有一份实现。**

    build121 刚被这个形状咬过：同一个判断写两处，测得到的不是跑起来的那处。
    `run_pc.py` 的 `_measures_for` 现在只是 `cro_inputs.measures_for` 的别名，
    它的函数体里**不许再出现自己算 σ 的代码**。
    """
    src = (ROOT / "run_pc.py").read_text("utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "_measures_for")
    body = ast.get_source_segment(src, fn) or ""
    assert "cro_inputs.measures_for" in body, "run_pc 没有走那份共用实现：\n" + body
    for banned in ("ann_vol", "beta_corr", "max_drawdown", "as_ratio"):
        assert banned not in body, \
            f"run_pc 里还自己算 {banned} —— 两处等价代码：\n{body}"
    # 自动这边走的也是同一份
    psrc = (ROOT / "src" / "cio" / "research" / "pipeline.py").read_text("utf-8")
    assert "cro_inputs.measures_for" in psrc, "自动流水线没走那份共用实现"
    for banned in ("ann_vol", "beta_corr", "max_drawdown"):
        assert banned not in psrc, f"自动流水线里自己算 {banned}"


def t_proposal_writing_has_one_home():
    """**提案那六步只能有一份实现。**

    手动入口和自动流水线各写一遍的下场是：改了其中一份，
    另一份继续按旧规矩落提案，而两边都不报错。
    """
    src = (ROOT / "run_rebalance.py").read_text("utf-8")
    assert "propose.for_run" in src, "run_rebalance 没走共用实现"
    assert "proposal_store.record(" not in src, \
        "run_rebalance 里还自己 record 提案 —— 两处等价代码"
    assert "rebalance.plan(" not in src, "run_rebalance 里还自己算指令清单"
    psrc = (ROOT / "src" / "cio" / "research" / "pipeline.py").read_text("utf-8")
    assert "propose.for_run" in psrc, "自动流水线没走共用实现"
    assert "proposal_store.record(" not in psrc, "自动流水线自己 record 提案"


def t_serialize_before_record_stays_in_the_library():
    """**"先序列化，后落库"这条纪律必须住在库里，不能住在某一个入口里。**

    住在入口里，另一个入口就会漏掉它：提案已写库 → 序列化炸了 →
    界面判定失败 → 用户点重试 → 库里两条一模一样的提案。
    """
    seen = {}
    real_record = proposal_store.record
    try:
        import cio.book as bk
        import cio.compliance as cp
        import cio.marks as mk
        import cio.pc_ledger as pcl
        import cio.rebalance as rb
        bk.is_book_portfolio = lambda pid: True
        bk.assert_single_source = lambda pid: None
        bk.holdings_map = lambda pid: {}
        bk.nav = lambda pid, px: {"nav": 100000.0, "cash": 100000.0}
        bk.portfolio_row = lambda pid: {"lot_size": 1}
        bk.render = lambda pid, px=None: ""
        bk.mark_evaluated = lambda *a, **k: 0
        mk.close_prices = lambda ts: {t: {"price": 100.0} for t in ts}
        mk.render_note = lambda d: ""
        pcl.latest_run_id = lambda pid: "pc-x"
        pcl.decisions_for_run = lambda rid, pid="": [
            {"ticker": "AMD", "veto": 0, "w_final": 0.04}]
        cp.check_proforma = lambda **kw: {"status": "PASS", "n_total": 1,
                                          "n_not_evaluated": 0}
        cp.render = lambda c: ""
        rb.render = lambda p: ""

        def _rec(**kw):
            seen["recorded"] = seen.get("recorded", 0) + 1
            return {"id": 1, "ticker": kw["row"]["ticker"], "state": "NO_TRADE"}
        proposal_store.record = _rec
        proposal_store.expire_stale = lambda pid, d, actor="": []

        def _boom(o):
            seen["hook_saw_rows"] = len(o["rows"])
            seen["recorded_at_hook"] = seen.get("recorded", 0)
            raise RuntimeError("序列化炸了")

        try:
            propose.for_run(portfolio_id="TEST", as_of="2026-09-04",
                            before_record=_boom)
            raise AssertionError("钩子抛了异常，for_run 却正常返回了")
        except RuntimeError:
            pass
        assert seen.get("hook_saw_rows"), "钩子被调用时指令清单还没算出来"
        assert seen.get("recorded_at_hook") == 0, \
            "钩子被调用时库里已经写过了 —— 顺序反了，重试会写出两条"
        assert seen.get("recorded", 0) == 0, "抛异常之后还是落了库"
    finally:
        proposal_store.record = real_record
        _restore()


def t_transition_fields_are_whitelisted():
    """**跃迁时顺手写字段要有白名单。**

    不设的话，调用方一个笔误就能改掉 `priority` 或 `state` 这类
    已经被判断过的字段，而那不会报错。
    """
    with _tmp_queue():
        it = _queued("AMD")
        q.transition(it.key, q.RISK_REVIEW, "t", fields={"proposal_id": 3})
        assert q.get(it.key).proposal_id == 3
        try:
            q.transition(it.key, q.PC_COMPLETE, "t", fields={"priority": 999})
            raise AssertionError("非白名单字段被写进去了")
        except ValueError:
            pass
        assert q.get(it.key).priority != 999


TESTS = [
    ("**自动化停在授权闸前面（状态机 + 源码 + 真跑）**",
     t_automation_stops_at_the_approval_gate),
    ("**口径不符 ≠ 风险高**", t_unit_mismatch_is_not_a_veto),
    ("**CRO 否决是告警，不是备注**", t_a_veto_is_an_alert_not_a_note),
    ("**算不出仓位是结论，不是消失**",
     t_no_position_is_a_conclusion_not_a_disappearance),
    ("**风控否决 ≠ 算不出仓位**", t_veto_and_no_position_are_not_the_same_bucket),
    ("**有目标却没提案，必须喊出来**", t_targets_without_a_proposal_must_shout),
    ("**队列待批和提案库待批要对得上**", t_queue_and_store_must_reconcile),
    ("预演不碰任何状态", t_dry_run_touches_nothing),
    ("**一次 advance = 一次 PC 运行**", t_one_advance_is_one_pc_run),
    ("心跳 0 也记", t_heartbeat_records_zero_too),
    ("**什么都没发生的那天，一盏灯都不许亮**", t_a_quiet_day_lights_no_lamp),
    ("**告警必须说清是什么事**", t_an_alert_must_say_what_happened),
    ("**口径换算只有一份实现**", t_measurement_conversion_has_one_home),
    ("**落提案只有一份实现**", t_proposal_writing_has_one_home),
    ("**先序列化后落库这条纪律住在库里**",
     t_serialize_before_record_stays_in_the_library),
    ("跃迁可写字段有白名单", t_transition_fields_are_whitelisted),
]

print("=" * 72)
print("Build 4 自测 —— 研究完了往下走，然后停在你面前")
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
