#!/usr/bin/env python3
"""build124 自测 —— **辩论换引擎，失败绝不返回提示词。**

    python scripts/test_llm.py

第一条用例是这一版存在的理由：`Ollama.chat()` 失败时
`return truncate(prompt, 240)`——于是"多头论点"变成提示词的前 240 字，
**没有异常、报告照出**，然后走完闸门、进论点台账、被 CRO 定仓、
推到 CEO 面前。本地模型很少挂，所以这条一直没咬到人；
换成远程 API 之后，限流 / 529 / 超时**每天都可能发生。**
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

from cio import llm                                            # noqa: E402
from cio import ollama_client                                  # noqa: E402
from cio.research import scheduler as sc                       # noqa: E402

OK: list = []
BAD: list = []


def check(name, fn):
    try:
        fn()
        OK.append(name)
        print(f"  OK    {name}")
    except Exception as e:                                     # noqa: BLE001
        BAD.append((name, f"{type(e).__name__}: {e}"))
        print(f"  ERR   {name}\n          {type(e).__name__}: {e}")


def _env(**kw):
    """临时改环境变量。"""
    class _C:
        def __enter__(self):
            self.keep = {k: os.environ.get(k) for k in kw}
            for k, v in kw.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            return self

        def __exit__(self, *a):
            for k, v in self.keep.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            return False
    return _C()


# ------------------------------------------------------------------ 用例

def t_a_failed_call_never_returns_the_prompt():
    """**这一版存在的理由。**

    失败时把提示词的前 240 字当成产出交出去，是这套系统里最难看的
    静默失败形状：它不报错、格式合法、读起来像分析。
    """
    o = ollama_client.Ollama()
    o.mock = False

    class _Boom:
        def post(self, *a, **k):
            raise RuntimeError("connection refused")
    o._client = _Boom()

    prompt = "你是多头。请基于以下材料建案：[1] 苹果发布新品……" * 20

    # 非 strict：翻译/摘要那条路还留着降级（外面有 _strip_echo 兜着）。
    # `truncate` 会加省略号，所以断**前缀**，不断子串。
    soft = o.chat(prompt, strict=False)
    assert soft and prompt.startswith(soft[:40]), soft[:80]

    # strict：**必须抛**
    try:
        o.chat(prompt, strict=True)
        raise AssertionError("strict 模式下失败却没抛异常 —— "
                             "那 240 个字会变成「多头论点」")
    except RuntimeError:
        pass

    # 引擎层永远是 strict 的那条
    with _env(CIO_DEBATE_ENGINE="ollama:gpt-oss:20b", CIO_MOCK_LLM=None):
        eng = llm.engine()
        import cio.ollama_client as oc
        real = oc.get_ollama
        try:
            oc.get_ollama = lambda: o
            try:
                eng.chat(prompt, system="s")
                raise AssertionError("引擎层失败却没抛 EngineError")
            except llm.EngineError as e:
                assert "调用失败" in str(e), str(e)
                # **异常里不许把提示词带出来当结果**
                assert prompt[:60] not in str(e), str(e)[:200]
        finally:
            oc.get_ollama = real


def t_the_debate_path_is_strict():
    """结构上钉住：辩论与判定走的是 `strict`，翻译/摘要那三处不走。"""
    src = (ROOT / "src" / "cio" / "judge.py").read_text("utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "ollama_chat")
    body = ast.get_source_segment(src, fn) or ""
    assert "strict=True" in body, "LLM 判定器没走 strict：\n" + body

    # **断结构，不要断文本。** 第一版这里断的是
    # `"llm.engine()" in 源码`——而我自己写的那行注释里就有 `llm.engine()`，
    # **注释满足了断言**：把 `oll = _llm.engine()` 改回 `get_ollama()`，
    # 用例照样绿。第三次栽在同一个坑里，所以这里走 AST。
    ua = (ROOT / "src" / "cio" / "unit_a.py").read_text("utf-8")
    uf = next(n for n in ast.walk(ast.parse(ua))
              if isinstance(n, ast.FunctionDef) and n.name == "build_unit_a")
    got = None
    for node in ast.walk(uf):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id == "oll" \
                and isinstance(node.value, ast.Call):
            f = node.value.func
            got = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
    assert got == "engine", \
        f"一部拿到的 oll 不是引擎层给的（是 {got!r}）—— 那它还钉死在本地模型上"
    # 翻译/摘要那三处**故意**不 strict：它们外面有 _strip_echo 兜底
    oc = (ROOT / "src" / "cio" / "ollama_client.py").read_text("utf-8")
    tree = ast.parse(oc)
    for name in ("translate_to_zh", "summarize_zh"):
        f = next((n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == name), None)
        if f is None:
            continue
        b = ast.get_source_segment(oc, f) or ""
        assert "strict" not in b, f"{name} 走了 strict —— 它有 _strip_echo 兜底，" \
                                  f"不需要，而且会让一次翻译失败拖垮整条采集"


def t_an_unknown_spec_does_not_silently_fall_back():
    """**拼错一个字不许悄悄退回本地。**

    退回的话，台账里一半论点出自另一个引擎而没人知道是哪一半。
    """
    for bad in ("gpt-oss:20b", "claude", "openai:gpt-4", "ollama:", ""):
        try:
            llm.parse_spec(bad if bad else "x")
            if bad:
                raise AssertionError(f"{bad!r} 被收下了")
        except ValueError:
            pass
    assert llm.parse_spec("claude:claude-sonnet-5") == ("claude", "claude-sonnet-5")
    assert llm.parse_spec("ollama:gpt-oss:20b") == ("ollama", "gpt-oss:20b")


def t_no_key_means_stop_not_fall_back():
    """**没 key 就停，不退回本地。** 理由同上。"""
    with _env(CIO_ANTHROPIC_API_KEY="", ANTHROPIC_API_KEY="",
              CIO_DEBATE_ENGINE="claude:claude-sonnet-5", CIO_MOCK_LLM=None):
        eng = llm.engine()
        assert eng.remote is True
        try:
            eng.chat("你好", system="s")
            raise AssertionError("没有 key 却跑起来了")
        except llm.EngineError as e:
            assert "API key" in str(e), str(e)
            assert "不会自动退回本地" in str(e), str(e)


def t_a_remote_failure_also_raises():
    """**远程那条路的失败也必须抛。**

    上一条用例走的是本地那条（连接被拒）。远程失败在测试里够不着——
    没 key 就更早地停了，有 key 又会真的联网——所以**那一半一直没被测到**，
    而它恰恰是换成 Claude 之后每天都会发生的那一半（限流 / 529 / 超时）。

    这里把 `httpx.post` 换成会抛的假货，直接打那条路。
    """
    import httpx
    real = httpx.post
    with _env(CIO_ANTHROPIC_API_KEY="sk-test-not-real",
              CIO_DEBATE_ENGINE="claude:claude-sonnet-5", CIO_MOCK_LLM=None):
        eng = llm.engine()
        prompt = "你是多头。请基于以下材料建案……" * 30
        try:
            httpx.post = lambda *a, **k: (_ for _ in ()).throw(
                RuntimeError("429 rate limit"))
            try:
                eng.chat(prompt, system="s")
                raise AssertionError("远程失败却没抛 —— 那 240 个字会变成「多头论点」")
            except llm.EngineError as e:
                assert "429" in str(e) or "调用失败" in str(e), str(e)
                assert prompt[:60] not in str(e), "异常里把提示词带出来了"

            # 空回复也是失败，不是"它没话说"
            class _R:
                status_code = 200

                def raise_for_status(self):
                    return None

                def json(self):
                    return {"content": [], "stop_reason": "max_tokens",
                            "usage": {"input_tokens": 10, "output_tokens": 0}}
            httpx.post = lambda *a, **k: _R()
            try:
                eng.chat(prompt, system="s")
                raise AssertionError("空回复被当成了「多头论点」")
            except llm.EngineError as e:
                assert "空内容" in str(e), str(e)
        finally:
            httpx.post = real


def t_tokens_are_facts_and_dollars_are_estimates():
    """**token 是事实，钱是按一张带日期的表估的。** 两者分开记、分开印。

    不分开的话，半年后价目表变了，历史成本会被悄悄重算成另一个数，
    而账面上看不出来。
    """
    u = llm.Usage(engine="claude:claude-sonnet-5")
    u.add(26000, 5000, "claude-sonnet-5")
    assert u.calls == 1 and u.input_tokens == 26000 and u.output_tokens == 5000
    assert abs(u.usd - (26000 * 2 + 5000 * 10) / 1e6) < 1e-9, u.usd
    assert u.priced is True
    d = u.describe()
    assert "in 26,000" in d and "$" in d and llm.PRICE_TABLE_AS_OF in d, d
    assert u.to_dict()["price_table_as_of"] == llm.PRICE_TABLE_AS_OF


def t_an_unpriced_model_is_not_free():
    """**不在价目表里 → 0 元，但那不是"免费"，是"不知道"。**

    两者都印 0，含义相反。`priced=False` 就是那个区分。
    """
    usd, priced = llm.estimate_usd("claude-something-new-9", 1_000_000, 1_000_000)
    assert usd == 0.0 and priced is False, (usd, priced)
    # 本地模型是真的不花钱
    usd2, priced2 = llm.estimate_usd("gpt-oss:20b", 999999, 999999)
    assert usd2 == 0.0 and priced2 is True, (usd2, priced2)
    u = llm.Usage(engine="claude:x")
    u.add(1000, 1000, "claude-something-new-9")
    assert u.priced is False
    assert "不等于免费" in u.describe(), u.describe()


def t_remote_is_said_out_loud():
    """**这一跑会不会把材料发到本机之外，报告上要说得出来。**"""
    local = llm.describe_spec("ollama:gpt-oss:20b")
    remote = llm.describe_spec("claude:claude-sonnet-5")
    assert "不出本机" in local, local
    assert "发到本机之外" in remote, remote
    assert local != remote
    # 边界写清楚：哪些送、哪些不送
    for need in ("持仓", "净值", "论点台账", "账本"):
        assert need in remote, f"远程那句话没写明不送 {need}：{remote}"
    assert llm.engine("ollama:gpt-oss:20b").remote is False
    assert llm.engine("claude:claude-sonnet-5").remote is True


def t_default_stays_local():
    """**不设环境变量就还是本地。** 换引擎必须是一次明确的动作。"""
    with _env(CIO_DEBATE_ENGINE=None):
        assert llm.parse_spec() == ("ollama", "gpt-oss:20b"), llm.parse_spec()
        assert llm.engine().remote is False


def t_max_tokens_is_big_enough_for_a_debate_round():
    """`judge.claude_chat()` 那个 `max_tokens=400` 是给判定器用的（回一个档位）。

    照抄到辩论上，多头论点会在半句话处被截断，**而返回的是一段合法文本**。
    """
    assert llm.MAX_TOKENS >= 1500, llm.MAX_TOKENS
    # **断结构，不要断文本。** 第一版这里断的是
    # `"max_tokens=400" not in 源码`，而模块开头的注释里正好解释了
    # "判定器那个 400 装不下一轮辩论" —— **我自己的注释满足了我自己的断言。**
    # 又是子串碰撞：那条断言测的是有没有人提过 400，不是代码用了几。
    src = (ROOT / "src" / "cio" / "llm.py").read_text("utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "_claude")
    found = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and k.value == "max_tokens":
                    found.append(v)
    assert found, "_claude 的请求体里根本没有 max_tokens"
    for v in found:
        assert not isinstance(v, ast.Constant), \
            f"max_tokens 写成了字面量 {getattr(v, 'value', '?')!r} —— " \
            f"它必须是那个说得出来历的常量"
        assert isinstance(v, ast.Name) and v.id == "MAX_TOKENS", ast.dump(v)


def t_an_empty_reply_is_a_failure_not_a_verdict():
    """**空回复不是"它没话说"，是这一次没成。**

    当成空的多头论点收下，闸门会照常放行，报告上是一段空白的"多头论点"。
    """
    src = (ROOT / "src" / "cio" / "llm.py").read_text("utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "_claude")
    body = ast.get_source_segment(src, fn) or ""
    assert "if not text" in body and "EngineError" in body, \
        "空回复没有被当成失败：\n" + body


def t_money_is_a_second_gate():
    """**次数上限挡不住钱。**

    一只材料特别多的票能吃掉另一只十倍的 token，五次调用之内就可能
    花掉预想的几倍钱，而次数账上完全正常。
    """
    td = tempfile.TemporaryDirectory()
    old = sc.SPEND_DIR
    try:
        sc.SPEND_DIR = Path(td.name) / "spend"
        day = "2026-09-04"
        assert sc.spent_usd(day) == 0.0
        assert sc.over_usd_budget(day, cap=5.0) is False
        sc.record_spend(day, "AMD", "new_thesis")
        sc.record_spend(day, "AMD", "new_thesis",
                        usage={"input_tokens": 26000, "output_tokens": 5000,
                               "usd": 4.99, "engine": "claude:claude-sonnet-5",
                               "priced": True, "price_table_as_of": "2026-09-05"})
        s = sc.spend(day)
        assert s["unit_a_calls"] == 1, s          # 补记不许多算一次
        assert s["input_tokens"] == 26000 and s["output_tokens"] == 5000, s
        assert abs(s["usd"] - 4.99) < 1e-9, s
        assert s["symbols"][0]["usd"] == 4.99, s["symbols"]
        assert sc.over_usd_budget(day, cap=5.0) is False
        sc.record_spend(day, "MU", "new_thesis")
        sc.record_spend(day, "MU", "new_thesis",
                        usage={"input_tokens": 100, "output_tokens": 10,
                               "usd": 0.02, "engine": "claude:claude-sonnet-5",
                               "priced": True})
        # **次数还剩 3，钱已经超了** —— 判别力全在这一半
        assert sc.remaining(day, budget=5) == 3, sc.remaining(day, budget=5)
        assert sc.over_usd_budget(day, cap=5.0) is True, sc.spent_usd(day)
        assert sc.spent_usd(day) > 5.0
        # cap<=0 = 不限（本地模型时就是这样）
        assert sc.over_usd_budget(day, cap=0) is False
    finally:
        sc.SPEND_DIR = old
        td.cleanup()


def t_the_money_gate_blocks_the_plan_and_says_why():
    """钱花完了，`plan()` 要说得出是**钱**花完了，不是次数用完了。

    两句话在报告上长得像，处理方式完全不同：一个是明天再来，
    一个是要么加钱、要么换便宜的引擎。
    """
    import cio.research.queue as q
    td = tempfile.TemporaryDirectory()
    old_s, old_q = sc.SPEND_DIR, q.QUEUE_PATH
    try:
        sc.SPEND_DIR = Path(td.name) / "spend"
        q.QUEUE_PATH = Path(td.name) / "q.jsonl"
        from cio.research import router as rt, trigger as tg
        for task in rt.route([tg.technical_trigger(
                "AMD", "2026-09-04", "2026-09-04",
                {"setup_version": "setup-1.0.1"}, score=0.9)]):
            q.enqueue(task)
        day = "2026-09-04"
        sc.record_spend(day, "X", "new_thesis")
        sc.record_spend(day, "X", "new_thesis",
                        usage={"input_tokens": 1, "output_tokens": 1,
                               "usd": 999.0, "engine": "claude:x", "priced": True})
        p = sc.plan(day, budget=5)
        assert p.blocked and "花费" in p.blocked, p.blocked
        assert "预算已用完" not in p.blocked, \
            f"钱花完了却说成次数用完了：{p.blocked}"
        assert not p.picks and len(p.deferred) == 1, (p.picks, p.deferred)
    finally:
        sc.SPEND_DIR, q.QUEUE_PATH = old_s, old_q
        td.cleanup()


def t_the_thesis_records_which_engine_wrote_it():
    """**两个引擎并存之后，"这条论点是谁写的"必须答得出来。**"""
    from cio import models, thesis_store
    a = models.UnitAAdvice(subject="AMD")
    assert hasattr(a, "engine") and hasattr(a, "engine_remote") \
        and hasattr(a, "usage"), "UnitAAdvice 上没有引擎血统字段"
    assert a.engine == "" and a.engine_remote is False
    src = (ROOT / "src" / "cio" / "thesis_store.py").read_text("utf-8")
    assert '("engine", "TEXT' in src, "论点台账没有 engine 列"
    assert "engine" in [c for c, _d in thesis_store._ADD_COLUMNS], \
        thesis_store._ADD_COLUMNS
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "record")
    body = ast.get_source_segment(src, fn) or ""
    assert "engine" in body, "record() 收了 engine 却没写进去"
    # 一部真的把它**传给论点台账**了。
    # 断 `"engine=oll.spec" in 源码`是不够的：那个片段在 `UnitAAdvice(...)`
    # 里也有一份，**删掉台账那一处照样绿** —— 而台账才是半年后要回答
    # "这条论点是谁写的"的地方。所以要找到那一次调用本身。
    ua = (ROOT / "src" / "cio" / "unit_a.py").read_text("utf-8")
    rec = [n for n in ast.walk(ast.parse(ua))
           if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
           and n.func.attr == "record"
           and getattr(n.func.value, "id", "") == "thesis_store"]
    assert rec, "一部根本没往论点台账写"
    for c in rec:
        kws = {k.arg for k in c.keywords}
        assert "engine" in kws, \
            f"thesis_store.record 没带 engine —— 台账里那条论点说不出是谁写的：{sorted(kws)}"


def t_mock_mode_costs_nothing_and_touches_no_network():
    """`CIO_MOCK_LLM=1` 时不调任何东西 —— 测试里 socket 是焊死的。"""
    with _env(CIO_MOCK_LLM="1", CIO_DEBATE_ENGINE="claude:claude-sonnet-5"):
        import importlib
        import cio.config as cfg
        importlib.reload(cfg)
        importlib.reload(llm)
        eng = llm.engine()
        out = eng.chat("你好", system="s")
        assert out.startswith("[MOCK]"), out
        assert eng.usage.calls == 1 and eng.usage.usd == 0.0, eng.usage.to_dict()
    import importlib
    import cio.config as cfg
    importlib.reload(cfg)
    importlib.reload(llm)


TESTS = [
    ("**失败绝不返回提示词**", t_a_failed_call_never_returns_the_prompt),
    ("**辩论与判定走 strict；翻译/摘要不走**", t_the_debate_path_is_strict),
    ("**拼错的 spec 不许悄悄退回本地**", t_an_unknown_spec_does_not_silently_fall_back),
    ("**没 key 就停，不退回本地**", t_no_key_means_stop_not_fall_back),
    ("**远程失败也抛，空回复是失败**", t_a_remote_failure_also_raises),
    ("**token 是事实，钱是估算**", t_tokens_are_facts_and_dollars_are_estimates),
    ("**不在价目表里 ≠ 免费**", t_an_unpriced_model_is_not_free),
    ("**材料出不出本机，报告上说得出来**", t_remote_is_said_out_loud),
    ("不设变量就还是本地", t_default_stays_local),
    ("辩论的 max_tokens 不能抄判定器那个 400",
     t_max_tokens_is_big_enough_for_a_debate_round),
    ("**空回复是失败，不是结论**", t_an_empty_reply_is_a_failure_not_a_verdict),
    ("**钱是第二道闸（次数挡不住它）**", t_money_is_a_second_gate),
    ("**钱花完了要说是钱，不是次数**", t_the_money_gate_blocks_the_plan_and_says_why),
    ("**论点记得住是哪个引擎写的**", t_the_thesis_records_which_engine_wrote_it),
    ("mock 不花钱、不联网", t_mock_mode_costs_nothing_and_touches_no_network),
]

print("=" * 72)
print("build124 自测 —— 辩论换引擎")
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
