#!/usr/bin/env python3
"""研究流水线自测 —— Trigger / Router / Queue。

    python scripts/test_research.py

**第一条用例是这一版存在的理由**：Evidence Gate 不许拦 Technical Trigger。
写成 `TECHNICAL → run_scan → INSUFFICIENT → STOP`，技术入口就静默死了——
队列照跑、简报照发、日志全绿，而那条路上永远出不来一个名字。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("CIO_MARKET", "us")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _no_network                                             # noqa: E402,F401

from cio.research import queue as q                            # noqa: E402
from cio.research import router as rt                          # noqa: E402
from cio.research import scheduler as sc                       # noqa: E402
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


def _tmp_queue():
    td = tempfile.TemporaryDirectory()
    q.QUEUE_PATH = Path(td.name) / "q.jsonl"
    return td


def t_evidence_gate_never_blocks_a_technical_trigger():
    """**这一版存在的理由。**

    `TECHNICAL → run_scan → INSUFFICIENT → STOP` 会让技术入口静默死亡：
    队列照跑、日志全绿，而那条路上永远出不来一个名字。
    **和盘前简报失踪三天是同一个形状。**

    进队列之后可以补跑 Evidence Scan 给 Unit A 补材料，
    但 `INSUFFICIENT` 仍然让它跑，只是要求明说"没有发现新的基本面事实"。
    """
    t = tg.technical_trigger("AMD", "2026-09-04", "2026-09-04", LIN, score=0.87)
    # 技术入口产生时 evidence_gate **留空**：没问过 ≠ 问过没有
    assert t.evidence_gate == "", f"技术 trigger 不该自带 evidence 判定：{t!r}"

    # 补跑 Evidence Scan 之后判成 INSUFFICIENT
    t.evidence_gate = "INSUFFICIENT"
    tasks = rt.route([t])
    assert len(tasks) == 1, f"INSUFFICIENT 把技术触发过滤掉了：{tasks}"
    assert tasks[0].symbol == "AMD"
    assert tg.TECHNICAL in tasks[0].trigger_types

    with _tmp_queue():
        it, act = q.enqueue(tasks[0])
        assert act == "queued", act
        assert it.state == q.QUEUED
        # 一路能走到 Unit A（RESEARCHING）
        q.transition(it.key, q.ENRICHING, "补材料：Evidence INSUFFICIENT")
        got = q.transition(it.key, q.RESEARCHING, "技术触发，无新基本面事实")
        assert got.state == q.RESEARCHING, got.state

    # 路由层不许出现任何"按 evidence_gate 过滤"的代码
    import ast
    src = (Path(__file__).resolve().parents[1] / "src" / "cio" / "research"
           / "router.py").read_text("utf-8")
    for fn in ("merge", "route", "dedupe"):
        node = next(n for n in ast.walk(ast.parse(src))
                    if isinstance(n, ast.FunctionDef) and n.name == fn)
        body = ast.get_source_segment(src, node) or ""
        assert "evidence_gate" not in body, \
            f"{fn} 里在看 evidence_gate —— 那就是把它当成了拦截器：\n{body}"


def t_one_event_is_one_task_not_one_per_day():
    """**闸门是一个状态，不是一个脉冲。**

    AMD 连着 6 天满足同一个形态，是**一件事**。按 (日期, 标的) 去重，
    它会连着 6 天吃掉研究预算。去重键必须是 `event_id`。
    """
    days = [f"2026-09-{d:02d}" for d in (4, 7, 8, 9, 10, 11)]
    ts = [tg.technical_trigger("AMD", d, "2026-09-04", LIN, score=0.8)
          for d in days]
    ids = {t.event_id for t in ts}
    assert len(ids) == 1, f"同一段形态产生了 {len(ids)} 个 event_id：{ids}"
    assert len({t.trigger_id for t in ts}) == 1, "trigger_id 不幂等"

    # **event_id 必须真的跟着"事件起始日"走。**
    # 上一版这几条断言全用同一个 start，所以"改成按今天算"的变异
    # 照样绿 —— 同一次运行里 today 也是同一个值，夹具没有判别力。
    e1 = tg.make_event_id("AMD", "2026-09-04", LIN)
    e2 = tg.make_event_id("AMD", "2026-08-01", LIN)
    assert e1 != e2, "起始日不同却是同一个 event_id —— 两段形态会被并成一件事"
    assert e1 == tg.make_event_id("AMD", "2026-09-04", LIN), "同一输入不稳定"
    assert e1 != tg.make_event_id("MU", "2026-09-04", LIN), "不同标的同一个 id"

    tasks = rt.route(ts)
    assert len(tasks) == 1, f"六天的同一形态变成了 {len(tasks)} 个任务"

    with _tmp_queue():
        acts = [q.enqueue(tasks[0])[1] for _ in range(3)]
        assert acts == ["queued", "exists", "exists"], acts
        assert len(q.items()) == 1, "队列凭空变长了"

    # **换了 setup 版本，就不是同一件事了**，不该被去重掉
    other = tg.technical_trigger(
        "AMD", "2026-09-04", "2026-09-04",
        dict(LIN, setup_version="setup-1.1.0"), score=0.8)
    assert other.event_id != ts[0].event_id, \
        "换了 setup 版本还算同一个事件 —— 那两版的命中会被混在一起统计"


def t_two_entrances_merge_into_one_task():
    """AMD 今天既有 8-K 又有技术触发 → **一个**任务，标 `EVIDENCE + TECHNICAL`。"""
    a = tg.technical_trigger("AMD", "2026-09-04", "2026-09-04", LIN, score=0.87)
    b = tg.evidence_trigger("AMD", "2026-09-04", "SUFFICIENT",
                            reason_codes=["8-K"])
    tasks = rt.route([a, b])
    assert len(tasks) == 1, f"两条入口变成了 {len(tasks)} 个任务 —— Unit A 会跑两遍"
    t = tasks[0]
    assert t.both_entrances, t.trigger_types
    assert set(t.trigger_types) == {tg.TECHNICAL, tg.EVIDENCE}
    assert "EVIDENCE" in t.label() and "TECHNICAL" in t.label(), t.label()
    # 双入口加成**单独记**，能一眼看出它贡献了多少
    assert t.priority_parts.get(rt.P_BOTH) == tg.BOTH_ENTRANCES_BONUS, \
        t.priority_parts
    assert t.priority == sum(t.priority_parts.values())
    # 双入口必须排在只有一条入口的同分票前面
    solo = tg.technical_trigger("MU", "2026-09-04", "2026-09-04", LIN, score=0.87)
    ranked = rt.route([a, b, solo])
    assert ranked[0].symbol == "AMD", [x.symbol for x in ranked]


def t_priority_must_say_where_it_came_from():
    """**一个说不出来历的数，在这个项目里等于没有。**"""
    t = tg.technical_trigger("AMD", "2026-09-04", "2026-09-04", LIN, score=0.87)
    assert t.priority == 87, t.priority
    assert t.priority_parts == {tg.P_SCORE: 87}, t.priority_parts
    tg.check_priority_adds_up(t)
    t.priority = 999
    try:
        tg.check_priority_adds_up(t)
        raise AssertionError("对不上的 priority 被放过了")
    except ValueError as e:
        assert "来历" in str(e), str(e)
    # 没有分数 → 0，**不是补一个中性的 50**
    n = tg.technical_trigger("X", "2026-09-04", "2026-09-04", LIN, score=None)
    assert n.priority == 0, n.priority


def t_lineage_travels_with_the_trigger():
    """`setup-1.0.1` 下的命中和以后 `1.1.0` 下的**不是同一种东西**。"""
    t = tg.technical_trigger("AMD", "2026-09-04", "2026-09-04", LIN, score=0.5)
    for k in ("setup_version", "score_version"):
        assert t.lineage.get(k) == LIN[k], t.lineage
    assert t.schema_version, "trigger 没有 schema 版本"
    back = tg.Trigger.from_dict(t.to_dict())
    assert back.to_dict() == t.to_dict(), "序列化再读回来不一致"


def t_an_open_thesis_means_recheck_not_a_new_debate():
    """已有 OPEN 论点的票，来了新触发是**复检**，不是重跑 Bull/Bear/Judge。

    不分岔的话，要么白花一次钱重跑，要么 supersede 把昨天的论点冲掉。
    """
    a = tg.technical_trigger("AMD", "2026-09-04", "2026-09-04", LIN, score=0.87)
    real = rt._thesis_branch
    try:
        rt._thesis_branch = lambda s: (rt.RECHECK, 42) if s == "AMD" else (rt.NEW_THESIS, 0)
        tasks = rt.route([a])
        assert tasks[0].kind == rt.RECHECK, tasks[0].kind
        assert tasks[0].thesis_id == 42, tasks[0].thesis_id
        assert "复检" in "\n".join(rt.describe(tasks))
        rt._thesis_branch = lambda s: (rt.NEW_THESIS, 0)
        tasks2 = rt.route([a])
        assert tasks2[0].kind == rt.NEW_THESIS and tasks2[0].thesis_id == 0
    finally:
        rt._thesis_branch = real


def t_deferred_items_age_so_they_do_not_starve():
    """**"被推迟"和"被丢弃"不许长得一样。**

    每天只跑前 K 个。如果排序只看当天优先级，一个中等分数的票
    可能永远排在第 6 名。
    """
    low = tg.technical_trigger("OLD", "2026-09-01", "2026-09-01", LIN, score=0.40)
    high = tg.technical_trigger("NEW", "2026-09-11", "2026-09-11", LIN, score=0.55)
    fresh = rt.route([low, high])
    assert fresh[0].symbol == "NEW", [x.symbol for x in fresh]
    # OLD 等了 8 个交易日之后应当追上来
    aged = rt.route([low, high], ages={"OLD": 8})
    assert aged[0].symbol == "OLD", [(x.symbol, x.priority) for x in aged]
    assert aged[0].priority_parts.get(rt.P_AGE) == 8 * rt.AGE_POINTS_PER_DAY
    # **但等待加成有上限** —— 防饿死不该变成另一种饿死
    capped = rt.route([low, high], ages={"OLD": 999})
    got = capped[0].priority_parts.get(rt.P_AGE)
    assert got == rt.AGE_CAP_DAYS * rt.AGE_POINTS_PER_DAY, got


def t_illegal_transitions_raise_and_cro_veto_is_not_ceo_reject():
    """非法跃迁抛异常；**CRO 否决和 CEO 否决分开记**。

    合成一个 `REJECTED`，以后就答不出"那道闸到底拦下过什么"——
    而那正是判断要不要拆闸的唯一依据。
    """
    a = tg.technical_trigger("AMD", "2026-09-04", "2026-09-04", LIN, score=0.9)
    with _tmp_queue():
        it, _ = q.enqueue(rt.route([a])[0])
        for bad in (q.APPROVED, q.EXECUTED, q.PC_COMPLETE):
            try:
                q.transition(it.key, bad)
                raise AssertionError(f"QUEUED → {bad} 居然合法")
            except ValueError as e:
                assert "不是合法跃迁" in str(e), str(e)
        try:
            q.transition(it.key, "不存在的状态")
            raise AssertionError("不认识的状态被收下了")
        except ValueError:
            pass
        try:
            q.transition("没有这个 key", q.ENRICHING)
            raise AssertionError("凭空跃迁一个不存在的条目")
        except KeyError:
            pass

        for st in (q.ENRICHING, q.RESEARCHING, q.RESEARCHED, q.RISK_REVIEW):
            q.transition(it.key, st)
        assert q.VETOED in q.LEGAL[q.RISK_REVIEW], "CRO 没法否决"
        assert q.REJECTED not in q.LEGAL[q.RISK_REVIEW], \
            "CRO 用的是 CEO 那个否决 —— 两道闸会混成一个数"
        assert q.REJECTED in q.LEGAL[q.PENDING_APPROVAL]
        assert q.VETOED not in q.LEGAL[q.PENDING_APPROVAL]
        v = q.transition(it.key, q.VETOED, "行业已超配")
        assert v.state == q.VETOED
        assert q.LEGAL[q.VETOED] == (), "终态还有出边"


def t_a_failed_item_does_not_vanish_and_does_not_retry_forever():
    """**一次 API 超时，不能让一只股票从世界上消失。**

    但也不能无限重试：一条坏掉的记录会每天消耗一次研究预算，
    而它看起来和正常排队一模一样。
    """
    a = tg.technical_trigger("AMD", "2026-09-04", "2026-09-04", LIN, score=0.9)
    with _tmp_queue():
        it, _ = q.enqueue(rt.route([a])[0])
        for i in range(q.MAX_ATTEMPTS):
            q.transition(it.key, q.ENRICHING)
            f = q.transition(it.key, q.FAILED, f"API 超时 {i + 1}")
            assert f.attempts == i + 1, f.attempts
            if f.attempts < q.MAX_ATTEMPTS:
                r = q.retry(it.key)
                assert r.state == q.QUEUED, r.state
        last = q.retry(it.key)
        assert last.state == q.STALE, \
            f"失败 {q.MAX_ATTEMPTS} 次还在重试：{last.state}"
        # **它没有消失** —— 还在队列里，状态说得清
        assert q.get(it.key) is not None
        assert len(q.get(it.key).history) >= 6, q.get(it.key).history


def t_the_queue_survives_a_restart_and_stuck_items_are_visible():
    """状态落盘、追加式；**卡住三天的条目要看得见**。

    一条卡住的记录和一条正常排队的记录，在计数上长得一样。
    """
    a = tg.technical_trigger("AMD", "2026-09-04", "2026-09-04", LIN, score=0.9)
    b = tg.technical_trigger("MU", "2026-09-04", "2026-09-04", LIN, score=0.8)
    with _tmp_queue():
        for t in rt.route([a, b]):
            q.enqueue(t)
        q.transition(q.items()[q.task_key(rt.route([a])[0])].key, q.ENRICHING)
        # 重新读一遍（模拟重启）
        again = q.items()
        assert len(again) == 2, again
        assert any(i.state == q.ENRICHING for i in again.values())
        box = q.counts()
        assert box[q.QUEUED] == 1 and box[q.ENRICHING] == 1, box
        # **0 也在字典里**，喂给心跳
        assert box[q.EXECUTED] == 0 and set(box) == set(q.STATES)
        # 卡住：把 updated_at 推回三天前
        it = [i for i in again.values() if i.state == q.ENRICHING][0]
        it.updated_at = "2026-09-01T18:00:00-04:00"
        q._append(it)
        st = q.stuck(days=2, today="2026-09-04")
        assert st and st[0][0].symbol == it.symbol, st
        assert "卡住" in "\n".join(q.describe())


def t_zero_is_printed_not_blank():
    """**0 条也要能印出来。** 空白和 0 是两件事。"""
    assert "0 条" in "\n".join(rt.describe([]))
    with _tmp_queue():
        assert "空" in "\n".join(q.describe())


def t_the_snapshot_reports_router_and_queue_into_the_same_heartbeat():
    """Build 2 加的两节**接进同一份心跳报告**，不另起一套日志。"""
    import ast
    src = (Path(__file__).resolve().parent / "technical_snapshot.py"
           ).read_text("utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "main")
    body = ast.get_source_segment(src, fn) or ""
    assert 'rep.stage("research_router")' in body, body[-600:]
    assert 'rep.stage("research_queue")' in body, body[-600:]
    # 没接上的那三节仍要被显式标注
    for k in ("unit_a", "cro_pc", "ceo"):
        assert k in body, f"{k} 从报告里消失了"
    # **真的跑一遍，看计数有没有落进心跳。**
    # 上一版断的是 `"hb.count(" in 函数体` —— 而 `_route_technical` 里有
    # 两处 hb.count，删掉第一处照样绿：子串从另一个调用被满足了。
    import importlib.util
    from cio import heartbeat as hbmod
    from cio.technical import score as sc
    spec = importlib.util.spec_from_file_location(
        "ts_probe", Path(__file__).resolve().parent / "technical_snapshot.py")
    ts_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ts_mod)

    ranked = [sc.Ranked(symbol="AMD", as_of="2026-09-04", passed_gate=True,
                        score=0.87, band="HIGH", families_used=5,
                        families_possible=5, rank=1, within_budget=True),
              sc.Ranked(symbol="ZZZ", as_of="2026-09-04", passed_gate=False)]
    with _tmp_queue():
        ts_mod.rq.QUEUE_PATH = q.QUEUE_PATH
        rep = hbmod.Report("2026-09-04")
        with rep.stage("research_router") as hb1:
            tasks = ts_mod._route_technical(ranked, hb1)
        assert hb1.counts.get("raw_triggers") == 1, hb1.counts
        assert hb1.counts.get("unique_symbols") == 1, hb1.counts
        assert "merged" in hb1.counts and "both_entrances" in hb1.counts, hb1.counts
        with rep.stage("research_queue") as hb2:
            ts_mod._fill_queue(tasks, hb2)
        assert hb2.counts.get("queued") == 1, hb2.counts
        assert hb2.counts.get("open_items") == 1, hb2.counts
        text = rep.render()
        assert "raw_triggers 1" in text and "queued 1" in text, text



def _tmp_sched():
    """临时队列 + 临时账本。**账本也要临时**，否则测试会污染真实预算。"""
    td = tempfile.TemporaryDirectory()
    q.QUEUE_PATH = Path(td.name) / "q.jsonl"
    sc.SPEND_DIR = Path(td.name) / "spend"
    return td


def _fake_pipeline(tier="INSUFFICIENT"):
    """把 `_enrich` / `_research` 换成假的，**不调模型**。返回被真跑过的名单。"""
    calls = []
    sc._enrich = lambda s: {"tier": tier, "n_sub": 0, "n": 4}

    def _r(it, tr, dry_run):
        calls.append((it.symbol, TECH in it.trigger_types, tr))
        return {"dry_run": False, "force": TECH in it.trigger_types, "tier": tr,
                "direction": "中性", "conviction": "弱",
                "note": (sc.NO_NEW_FACTS_NOTE
                         if TECH in it.trigger_types and tr == "INSUFFICIENT" else "")}
    sc._research = _r
    return calls


TECH = tg.TECHNICAL


def t_a_technical_trigger_forces_unit_a_past_insufficient():
    """**那条规矩在这一层才真的致命。**

    `build_unit_a(text, force=False)` 的默认行为是：

        Evidence Gate = INSUFFICIENT → 一部不启动，ABSTAIN，0 次 LLM 调用

    也就是说即使路由老老实实把 TECHNICAL trigger 送到了 Unit A，
    **一部自己会把它挡回去**。技术入口照样静默死亡，只是死在更深一层。

    所以调度对技术触发**必须传 force=True**，并在产出上写明没有新的基本面事实。
    """
    real_e, real_r = sc._enrich, sc._research
    try:
        with _tmp_sched():
            calls = _fake_pipeline("INSUFFICIENT")
            a = tg.technical_trigger("AMD", "2026-09-04", "2026-09-04", LIN, score=0.9)
            for task in rt.route([a]):
                q.enqueue(task)
            res = sc.run("2026-09-04", budget=5)
            assert calls, "Evidence=INSUFFICIENT 把技术触发挡在了一部门口"
            sym, forced, tier = calls[0]
            assert sym == "AMD" and tier == "INSUFFICIENT"
            assert forced is True, "技术触发没有传 force=True —— 一部会 ABSTAIN"
            assert res["forced_past_insufficient"] == 1, res
            assert sc.NO_NEW_FACTS_NOTE in "\n".join(sc.describe(res)), \
                "越过 INSUFFICIENT 却没写明「没有新的基本面事实」"
            it = list(q.items().values())[0]
            assert it.state == q.RESEARCHED, it.state
    finally:
        sc._enrich, sc._research = real_e, real_r

    # 结构上钉住：真的把 force 传下去了，而且是按 TECHNICAL 判的
    import ast
    src = (Path(__file__).resolve().parents[1] / "src" / "cio" / "research"
           / "scheduler.py").read_text("utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "_research")
    body = ast.get_source_segment(src, fn) or ""
    assert "force=force" in body, "没把 force 传给 build_unit_a：\n" + body
    assert "TECHNICAL in" in body, "force 不是按 TECHNICAL 判的：\n" + body


def t_the_note_comes_from_the_code_not_from_my_fixture():
    """**上一条用例差点是假的。**

    `_fake_pipeline()` 里那个假的 `_research` **自己返回了 `NO_NEW_FACTS_NOTE`**。
    于是「越过 INSUFFICIENT 要写明没有新的基本面事实」这句断言，
    验的是我的夹具，不是被测代码——把真 `_research` 里那行删掉，用例照样绿。

    **用实现自己的输出验证实现，永远验证不出东西。**

    所以这条直接调**真的 `sc._research`**（`dry_run=True`，零模型调用），
    只喂进去 trigger_types 和 tier，看它自己吐什么。
    """
    class _It:                                                 # 最小替身：只有被读的两个字段
        def __init__(self, symbol, types):
            self.symbol, self.trigger_types = symbol, types

    tech = _It("AMD", [TECH])
    evid = _It("BBY", [tg.EVIDENCE])

    r = sc._research(tech, "INSUFFICIENT", dry_run=True)
    assert r["force"] is True, "技术触发没传 force=True —— 一部会 ABSTAIN"
    assert r["note"] == sc.NO_NEW_FACTS_NOTE, \
        f"越过 INSUFFICIENT 却没写明「没有新的基本面事实」：{r!r}"

    r2 = sc._research(evid, "INSUFFICIENT", dry_run=True)
    assert r2["force"] is False, "非技术触发也 force —— 那 Evidence 闸就白设了"
    assert not r2.get("note"), \
        f"不是技术触发也挂那句话 —— 那句话会变成一句永远都在的废话：{r2!r}"

    r3 = sc._research(tech, "SUFFICIENT", dry_run=True)
    assert not r3.get("note"), \
        f"材料够也说「没有新的基本面事实」—— **常亮的灯 = 不亮的灯**：{r3!r}"

    # **判断只能有一个出处。** 预演一处、真跑一处地判两次，
    # 上面这条用的是预演那处 —— 删掉真跑那处，它照样绿，
    # 而删掉的偏偏是真的会跑起来的那处。
    import ast
    src = (Path(__file__).resolve().parents[1] / "src" / "cio" / "research"
           / "scheduler.py").read_text("utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "_research")
    body = ast.get_source_segment(src, fn) or ""
    assert "_note_for(" in body, "_research 不走那个唯一出处：\n" + body
    assert "NO_NEW_FACTS_NOTE" not in body, \
        "_research 自己又判了一次那句话 —— 两处等价代码，测得到的不是跑起来的那处：\n" + body


def t_budget_is_counted_on_disk_not_in_memory():
    """**预算是数出来的，不是配置出来的**，而且要经得住中途重启。

    Approve 挡的是坏交易；它挡不住"连着三周把研究预算花在垃圾上"，
    而那看起来和正常运行一模一样。
    """
    real_e, real_r = sc._enrich, sc._research
    try:
        with _tmp_sched():
            _fake_pipeline()
            for s, v in [("AMD", .9), ("MU", .8), ("AVGO", .7)]:
                for task in rt.route([tg.technical_trigger(
                        s, "2026-09-04", "2026-09-04", LIN, score=v)]):
                    q.enqueue(task)
            res = sc.run("2026-09-04", budget=2)
            assert res["done"] == 2 and res["deferred"] == 1, res
            assert sc.spend("2026-09-04")["unit_a_calls"] == 2
            assert sc.remaining("2026-09-04", 2) == 0
            # 超预算的**转 DEFERRED，不是消失**
            assert q.counts()[q.DEFERRED] == 1, q.counts()
            # **再跑一次不许超支**（模拟中途重启：计数从磁盘读）
            res2 = sc.run("2026-09-04", budget=2)
            assert res2["done"] == 0, res2
            assert "预算已用完" in res2["blocked"], res2["blocked"]
            assert sc.spend("2026-09-04")["unit_a_calls"] == 2, "超支了"
            # 账本记得住花在谁身上
            syms = [x["symbol"] for x in sc.spend("2026-09-04")["symbols"]]
            assert syms == ["AMD", "MU"], syms
    finally:
        sc._enrich, sc._research = real_e, real_r


def t_dry_run_spends_nothing_and_uses_the_same_plan():
    """**预演和真跑必须用同一份 plan**，否则预演不等于实跑。"""
    real_e, real_r = sc._enrich, sc._research
    try:
        with _tmp_sched():
            _fake_pipeline()
            for s, v in [("AMD", .9), ("MU", .8)]:
                for task in rt.route([tg.technical_trigger(
                        s, "2026-09-04", "2026-09-04", LIN, score=v)]):
                    q.enqueue(task)
            before = dict(q.counts())
            res = sc.run("2026-09-04", budget=5, dry_run=True)
            assert res["dry_run"] and res["picked"] == 2, res
            assert res["done"] == 0, "预演真的跑了"
            assert sc.spend("2026-09-04")["unit_a_calls"] == 0, "预演花钱了"
            assert dict(q.counts()) == before, "预演改了队列状态"
            # 同一份 plan
            p = sc.plan("2026-09-04", budget=5)
            assert [i.symbol for i in p.picks] == [
                r["symbol"] for r in res["results"]], (p.picks, res["results"])
    finally:
        sc._enrich, sc._research = real_e, real_r


def t_the_kill_switch_is_visible_not_silent():
    """**被关掉的流水线和坏掉的流水线，不许长得一样。**"""
    import os as _os
    real_e, real_r = sc._enrich, sc._research
    keep = _os.environ.get("CIO_RESEARCH_ENABLED")
    try:
        with _tmp_sched():
            _fake_pipeline()
            for task in rt.route([tg.technical_trigger(
                    "AMD", "2026-09-04", "2026-09-04", LIN, score=0.9)]):
                q.enqueue(task)
            _os.environ["CIO_RESEARCH_ENABLED"] = "0"
            assert sc.enabled() is False
            res = sc.run("2026-09-04", budget=5)
            assert res["done"] == 0 and res["enabled"] is False, res
            assert "关掉" in res["blocked"], res["blocked"]
            text = "\n".join(sc.describe(res))
            assert "被关掉" in text, text
            # 关掉时**队列不许被清空**，条目还在
            assert len(q.items()) == 1
            # 而且**必须还数得出来有几条在等**。
            # 只保证"条目没被删"是不够的：把等待名单清成空的，
            # 条目照样在文件里，可是关掉期间攒了 40 条这件事就**没人看得见**——
            # 那和"今天本来就没有"长得一模一样。
            assert res["deferred"] == 1, \
                f"关掉时不报还有几条在等 —— 攒着的活变成了看不见的活：{res}"
            p = sc.plan("2026-09-04", budget=5)
            assert [i.symbol for i in p.deferred] == ["AMD"], \
                f"关掉时 plan 不列出等待的条目：{p.deferred}"
            assert not p.picks, "关掉了还挑人跑"
            assert "关掉" in "\n".join(p.describe())
    finally:
        sc._enrich, sc._research = real_e, real_r
        if keep is None:
            _os.environ.pop("CIO_RESEARCH_ENABLED", None)
        else:
            _os.environ["CIO_RESEARCH_ENABLED"] = keep


def t_a_failure_costs_budget_and_lands_in_FAILED():
    """**先记账再花钱。**

    跑一半崩了那一次也算花过——否则一条反复崩溃的记录每天吃掉整份预算，
    而账上永远显示"今天还没花"。
    """
    real_e, real_r = sc._enrich, sc._research
    try:
        with _tmp_sched():
            sc._enrich = lambda s: {"tier": "SUFFICIENT", "n_sub": 3, "n": 5}

            def _boom(it, tier, dry_run):
                raise RuntimeError("模型超时")
            sc._research = _boom
            for task in rt.route([tg.technical_trigger(
                    "AMD", "2026-09-04", "2026-09-04", LIN, score=0.9)]):
                q.enqueue(task)
            res = sc.run("2026-09-04", budget=5)
            assert res["failed"] == 1 and res["done"] == 0, res
            it = list(q.items().values())[0]
            assert it.state == q.FAILED, it.state
            assert it.attempts == 1, it.attempts
            # **那一次算花过了**
            assert sc.spend("2026-09-04")["unit_a_calls"] == 1, sc.spend("2026-09-04")
            # 它没有消失，还能重试
            assert q.retry(it.key).state == q.QUEUED
    finally:
        sc._enrich, sc._research = real_e, real_r


def t_the_scheduler_reports_into_the_same_heartbeat():
    """Build 3 这一节也接进同一份心跳，**0 也要记**。"""
    from cio import heartbeat as hbmod
    real_e, real_r = sc._enrich, sc._research
    try:
        with _tmp_sched():
            _fake_pipeline()
            rep = hbmod.Report("2026-09-04")
            with rep.stage("unit_a") as hb:
                sc.run("2026-09-04", budget=5, hb=hb)
            # 空队列也要有计数，而不是一片空白
            assert hb.counts.get("picked") == 0, hb.counts
            assert hb.counts.get("budget") == 5, hb.counts
            assert "picked 0" in rep.render(), rep.render()

            for task in rt.route([tg.technical_trigger(
                    "AMD", "2026-09-04", "2026-09-04", LIN, score=0.9)]):
                q.enqueue(task)
            rep2 = hbmod.Report("2026-09-04")
            with rep2.stage("unit_a") as hb2:
                sc.run("2026-09-04", budget=5, hb=hb2)
            assert hb2.counts.get("done") == 1, hb2.counts
            assert hb2.counts.get("forced_past_insufficient") == 1, hb2.counts
            assert hb2.counts.get("budget_used") == 1, hb2.counts
            assert "INSUFFICIENT" in rep2.render(), rep2.render()
    finally:
        sc._enrich, sc._research = real_e, real_r


def t_enrichment_never_blocks_only_informs():
    """**补材料这一步只补信息，不做准入。**

    它判成 INSUFFICIENT 也不许 return / continue / raise 把条目拦下来。
    """
    import ast
    src = (Path(__file__).resolve().parents[1] / "src" / "cio" / "research"
           / "scheduler.py").read_text("utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_enrich")
    body = ast.get_source_segment(src, fn) or ""
    assert "build_unit_a" not in body, "补材料那一步调了模型 —— 它该是零 LLM 的"
    # run() 里不许出现"按 tier 决定跑不跑"的分支
    runfn = next(n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef) and n.name == "run")
    for node in ast.walk(runfn):
        if isinstance(node, ast.If):
            seg = ast.get_source_segment(src, node.test) or ""
            assert "INSUFFICIENT" not in seg, \
                f"run() 里按 Evidence 档位分支了 —— 那就是拦截器：{seg}"


TESTS = [
    ("**Evidence Gate 不许拦 Technical Trigger**", t_evidence_gate_never_blocks_a_technical_trigger),
    ("**一次事件 = 一个任务，不是一天一个**", t_one_event_is_one_task_not_one_per_day),
    ("**两条入口合并成一个任务**", t_two_entrances_merge_into_one_task),
    ("**优先级要说得出来历**", t_priority_must_say_where_it_came_from),
    ("血统跟着 trigger 走", t_lineage_travels_with_the_trigger),
    ("**有 OPEN 论点 → 复检，不是重跑辩论**", t_an_open_thesis_means_recheck_not_a_new_debate),
    ("**推迟的会老化，不会饿死（且加成有上限）**", t_deferred_items_age_so_they_do_not_starve),
    ("**非法跃迁抛异常；CRO 否决 ≠ CEO 否决**", t_illegal_transitions_raise_and_cro_veto_is_not_ceo_reject),
    ("**失败不消失，也不无限重试**", t_a_failed_item_does_not_vanish_and_does_not_retry_forever),
    ("队列可重启读回，卡住的看得见", t_the_queue_survives_a_restart_and_stuck_items_are_visible),
    ("0 条也要印出来", t_zero_is_printed_not_blank),
    ("**两节接进同一份心跳报告**", t_the_snapshot_reports_router_and_queue_into_the_same_heartbeat),
    ("**技术触发要 force 越过一部的 INSUFFICIENT 门**", t_a_technical_trigger_forces_unit_a_past_insufficient),
    ("**那句话由代码写，不是由我的夹具写**", t_the_note_comes_from_the_code_not_from_my_fixture),
    ("**预算数在磁盘上，重启不清零**", t_budget_is_counted_on_disk_not_in_memory),
    ("**预演不花钱，且和真跑同一份 plan**", t_dry_run_spends_nothing_and_uses_the_same_plan),
    ("**开关关掉要看得见，不是静默**", t_the_kill_switch_is_visible_not_silent),
    ("**先记账再花钱；失败落 FAILED 不消失**", t_a_failure_costs_budget_and_lands_in_FAILED),
    ("调度也接进同一份心跳（0 也记）", t_the_scheduler_reports_into_the_same_heartbeat),
    ("**补材料只补信息，不做准入**", t_enrichment_never_blocks_only_informs),
]

print("=" * 72)
print("研究流水线自测 —— 两条入口，一条队列")
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
