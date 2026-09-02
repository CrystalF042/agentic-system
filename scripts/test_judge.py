#!/usr/bin/env python3
"""判定器自测 —— **模型接进来之前，先把护栏钉死。**

    python scripts/test_judge.py

这一套测的不是"模型判得准不准"（那是 `eval_judge.py` 的事），
而是**模型判错、判飘、或者根本不通的时候，系统会怎么样**。

三条护栏，每一条都在这里有对应的用例：

    引文核对    判「实质」必须能从原文里逐字引出依据，引不出就降级
    显式降级    模型不通就回落到规则，并且 degraded=True 一路带出去
    缓存不固化故障  降级的结果**不进缓存** —— 否则一次网络故障会被
                    永久固化成这条材料的判定
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _no_network                                            # noqa: E402,F401
"""判定器自测**绝不允许联网** —— 一个靠真模型才通过的断言，换台机器就是另一个结果。"""

import _material_corpus as corpus                             # noqa: E402
from cio import judge as J                                    # noqa: E402
from cio import material_gate                                 # noqa: E402

OK, BAD = [], []


def check(name, fn):
    try:
        fn()
        OK.append(name)
        print(f"  OK    {name}")
    except AssertionError as e:
        BAD.append((name, str(e)))
        print(f"  FAIL  {name}\n          {e}")
    except Exception as e:                                     # noqa: BLE001
        BAD.append((name, f"{type(e).__name__}: {e}"))
        print(f"  ERR   {name}\n          {type(e).__name__}: {e}")


_REAL = ("AMD announced it completed the acquisition of ZT Systems "
         "for $4.9 billion on Monday.")


def _fake(reply):
    """一个假后端：不管问什么都回同一串。"""
    return lambda _prompt: reply


def t_rule_and_llm_return_the_same_shape():
    """规则和模型返回**同一个形状** —— 否则下游要分两套代码走。"""
    r = J.RuleJudge().judge_one(_REAL)
    m = J.LLMJudge(_fake('{"tier":"实质","why":"完成收购","span":"completed the '
                         'acquisition of ZT Systems","event":"AMD 收购 ZT"}'),
                   name="fake").judge_one(_REAL)
    assert isinstance(r, J.Verdict) and isinstance(m, J.Verdict)
    assert set(r.__dict__) == set(m.__dict__)
    assert r.tier == m.tier == material_gate.SUBSTANTIVE, (r.tier, m.tier)


def t_fabricated_span_is_downgraded():
    """**判「实质」却引不出原文，就不算实质。**

    这是三条护栏里最要紧的一条：它把"相信模型"变成"核对模型的引文"，
    而核对是确定性的、离线的、不需要再叫一次模型的。
    """
    jd = J.LLMJudge(_fake('{"tier":"实质","why":"完成收购",'
                          '"span":"AMD signed a $50 billion deal with Oracle",'
                          '"event":"x"}'), name="fake")
    v = jd.judge_one(_REAL)
    assert v.tier == material_gate.CONTEXT, v
    assert v.degraded is True
    assert "对不上" in v.why, v.why
    # 引文对得上就照判
    good = J.LLMJudge(_fake('{"tier":"实质","why":"完成收购",'
                            '"span":"completed the acquisition","event":"x"}'),
                      name="fake").judge_one(_REAL)
    assert good.tier == material_gate.SUBSTANTIVE and good.degraded is False


def t_span_check_is_not_fooled_by_whitespace_or_case():
    """引文核对按规范化后的文本比 —— 换行和大小写不算篡改。"""
    assert J._span_ok("COMPLETED   the\nacquisition", _REAL)
    assert not J._span_ok("completed the merger", _REAL)
    assert not J._span_ok("AMD", _REAL), "太短的片段不该算引文"


def t_model_failure_degrades_loudly():
    """模型不通 → 回落到规则，且 **degraded=True 必须带出去**。

    静默降级会让"今天模型不通"和"今天没新闻"在输出上长得一模一样——
    这个坑在死掉的 RSS 源上已经踩过一次。
    """
    def boom(_p):
        raise RuntimeError("connection refused")
    v = J.LLMJudge(boom, name="fake").judge_one(_REAL)
    assert v.degraded is True, v
    assert v.tier == J.RuleJudge().judge_one(_REAL).tier
    assert "fake→rules" == v.judge, v.judge


def t_garbage_and_unknown_tier_also_degrade():
    """模型回了非 JSON、或者一个不认识的档位 —— 都不许被当成判定。"""
    for reply in ("我觉得这条挺重要的", "{}", '{"tier":"很重要","span":""}',
                  '{"tier":"SUBSTANTIVE","span":""}'):
        v = J.LLMJudge(_fake(reply), name="fake").judge_one(_REAL)
        assert v.degraded is True, (reply, v)


def t_cache_makes_it_reproducible():
    """**同一篇文章永远同一个答案。**

    真机上出现过同一天相隔 17 分钟两跑、ARM 实质 2 与实质 1 的情况。
    那次是源在变；模型自己抖起来会更难查，所以判定按内容哈希缓存。
    """
    import tempfile
    calls = []

    def counting(_p):
        calls.append(1)
        return '{"tier":"背景","why":"x","span":"","event":"e"}'
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "c.json"
        jd = J.LLMJudge(counting, name="fake", cache_path=p)
        a = jd.judge_one(_REAL)
        b = jd.judge_one(_REAL)
        assert a.tier == b.tier and len(calls) == 1, (a, b, len(calls))
        jd._cache.flush()
        # 换个进程也要命中（缓存真的落了盘）
        jd2 = J.LLMJudge(counting, name="fake", cache_path=p)
        assert jd2.judge_one(_REAL).tier == a.tier and len(calls) == 1


def t_degraded_results_are_not_cached():
    """**降级的结果不进缓存。**

    缓存一条"因为模型不通所以按规则判的"结论，等于把一次网络故障
    永久固化成这条材料的判定 —— 而且以后再也不会重试。
    """
    import tempfile
    state = {"fail": True}

    def flaky(_p):
        if state["fail"]:
            raise RuntimeError("down")
        return '{"tier":"实质","why":"ok","span":"completed the acquisition","event":"e"}'
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "c.json"
        jd = J.LLMJudge(flaky, name="fake", cache_path=p)
        assert jd.judge_one(_REAL).degraded is True
        state["fail"] = False
        v = jd.judge_one(_REAL)
        assert v.degraded is False and v.tier == material_gate.SUBSTANTIVE, v


def t_eval_reports_degradation():
    """**评测必须先报降级率,再报分数。**

    key 错了 / 模型不通时 `judge.py` 会回落到规则(那是设计好的护栏),
    于是三个分数**恰好等于规则基线**。你会看到"留出集 3/8、相关性 13/20",
    和规则一模一样,然后得出"换模型没用"——**而模型一次都没被调用过。**

    这正是这个项目一整天在抓的那类缺陷,而它出现在了测量工具本身里。
    """
    import ast
    import importlib.util as iu
    spec = iu.spec_from_file_location(
        "ej", Path(__file__).resolve().parent / "eval_judge.py")
    src = spec.origin and Path(spec.origin).read_text("utf-8")
    tree = ast.parse(src)
    # 断言的是**具名字段**，不是元组长度：加一栏统计不该让这条红，
    # 而把降级折进分数里必须让它红。
    cls = next(n for n in ast.walk(tree)
               if isinstance(n, ast.ClassDef) and n.name == "Score")
    fields = {t.target.id for t in cls.body if isinstance(t, ast.AnnAssign)}
    assert {"ok", "n", "degraded"} <= fields, f"Score 缺字段：{fields}"
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_score_tier")
    rets = [n for n in ast.walk(fn) if isinstance(n, ast.Return)]
    assert rets and all(isinstance(r.value, ast.Call)
                        and getattr(r.value.func, "id", "") == "Score"
                        for r in rets), "_score_tier 没有把降级条数单独返回"
    # **顺序**：降级率必须印在三个分数之前。降级高的时候，那三个数不是模型的分数。
    assert 0 <= src.index("  降级 ") < src.index("调参集（规则为它改过"), \
        "降级率印在了分数后面"
    for must in ("降级", "--smoke"):
        assert must in src, f"eval_judge 里没有 {must}"


def t_judge_loads_dotenv_itself():
    """**`judge` 必须自己导入 `config`,不能依赖调用方的导入顺序。**

    `claude_chat()` 从环境变量取 key,而把 `.env` 读进环境变量的是
    `cio.config` 的导入副作用。真机踩到过:`--smoke` 分支插在
    `from cio.config import MEMORY_DIR` 之前,于是 `.env` 从没被读过,
    一个配置完全正确的 key 报"没有 API key"。
    """
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(J))
    imported = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            imported.add(n.module or "")
            imported |= {a.name for a in n.names}
    assert "config" in imported, \
        "judge.py 没有导入 config —— .env 只在调用方碰巧先导过时才会被读"
    import sys as _s
    assert "cio.config" in _s.modules


def t_prompt_never_asks_the_model_about_policy():
    """**提示词只问语言问题，不问政策。**

    表单号该不该开门、几条算充分、同一事件怎么合并 —— 这些是 CEO 定的规则，
    不是语言问题。让模型去判，它会给出一个听起来很有道理的答案，
    而那正是闸门存在的理由要防的事。
    """
    text = J._TIER_PROMPT + J._REL_PROMPT
    for banned in ("Form 4", "13G", "13D", "SUFFICIENT", "THIN", "INSUFFICIENT",
                   "材料充分", "材料偏薄", "闸门", "信心", "仓位"):
        assert banned not in text, f"提示词里出现了政策词：{banned}"


def t_policy_layer_is_untouched():
    """政策层（`material_gate.assess`）**不接受**任何模型输入。

    这一版刻意没有把判定器接进主链路。这个断言是防止以后有人
    "顺手"把模型接进 assess()，让政策也变成模型说了算。
    """
    import inspect
    src = inspect.getsource(material_gate)
    assert "judge" not in src.lower().replace("judgement", ""), \
        "material_gate 里出现了 judge —— 政策层被接上模型了？"


def t_heldout_is_actually_held_out():
    """**留出集不能和调参集重叠。** 重叠了它就不是留出集。

    这份文件的全部意义在于：调参集是规则的训练数据，它上面的分数
    不说明能力。一旦为了让留出集变绿而去改规则，这个区分就没了。
    """
    tuned = {t for _b, t, _w, _n in corpus.CASES}
    held = {t for _b, t, _w, _n in corpus.HELDOUT}
    assert held, "留出集是空的"
    assert not (tuned & held), sorted(tuned & held)[:2]
    # 两个方向都要有条目，否则只测得出一半
    assert len({w for _b, _t, w, _n in corpus.HELDOUT}) >= 1
    rel = [w for _s, _c, _t, w, _n in corpus.RELEVANCE_CASES]
    assert rel.count(True) >= 5 and rel.count(False) >= 5, \
        f"相关性用例两个方向都要有：相关 {rel.count(True)}、不相关 {rel.count(False)}"


def t_baseline_is_recorded_and_imperfect():
    """**基线必须被记录下来，而且它现在不是满分。**

    2026-08-31 的实测：调参集 67/67，留出集 3/8，相关性 13/20。
    如果哪天规则在留出集上也满分了，多半不是规则变强了，
    是有人把留出集的判例拿去调参了 —— 那时这个用例会提醒去换新样本。
    """
    jd = J.RuleJudge()
    h_bad = [1 for _b, t, w, _n in corpus.HELDOUT if jd.judge_one(t).tier != w]
    r_bad = [1 for s, c, t, w, _n in corpus.RELEVANCE_CASES
             if bool(jd.judge_relevance(t, s, c)) != bool(w)]
    assert h_bad, "规则在留出集上满分了 —— 留出集是不是被拿去调参了？"
    assert r_bad, "规则在相关性上满分了 —— 同上"


_SEC_URL = "https://www.sec.gov/Archives/edgar/data/2488/x.htm"
_ALWAYS_SUB = ('{"tier":"实质","why":"看着像实质","span":"%s","event":"e"}')


def _echo_sub(text):
    """一个会把原文头 40 个字当引文抄回来的假模型 —— 引文核对必过。"""
    span = (text or "").strip().split("\n", 1)[0][:40]
    return lambda _p: _ALWAYS_SUB % span


def t_policy_beats_the_model():
    """**来源与表单是政策，模型连问都不该被问到。**

    这是 build105 那个缺陷的回归用例。`judge.py` 开头整整一节写着
    「语言理解 与 政策，必须切开」，而 `LLMJudge.judge_one` 当时直接把文本
    丢给了模型——`is_primary` / `OWNERSHIP_FORMS` / `PRIMARY_MIN_CHARS`
    三条一条都没走。2026-09-01 首次真机评测抓到：语料里的
    `SC 13G body=True` 期望「背景」，Claude 判「实质」。

    用一个**被调用就抛异常**的后端来测：结果仍然正确，就证明它没被调用。
    """
    def boom(_p):
        raise AssertionError("政策条目不该问模型")
    for form, want in (("SC 13G", material_gate.CONTEXT),
                       ("4", material_gate.CONTEXT),
                       ("8-K", material_gate.SUBSTANTIVE)):
        text = corpus.filing_text(form, True)
        for jd in (J.LLMJudge(boom, name="fake"),
                   J.HybridJudge(J.LLMJudge(boom, name="fake"), name="hy")):
            v = jd.judge_one(text, "EDGAR", _SEC_URL)
            assert v.tier == want, (jd.name, form, v.tier, want)
            assert v.policy is True and v.degraded is False, (jd.name, form, v)


def t_policy_is_not_counted_as_degradation():
    """**政策直判不是降级。** 降级是"模型该跑而没跑成"，是故障；
    政策直判是"这条本来就不该问模型"，是设计。混成一个数，
    一份正常工作的公告会让评测报出"模型不通"。"""
    src = (Path(__file__).resolve().parent / "eval_judge.py").read_text("utf-8")
    assert "policy" in src and "政策直判" in src, "评测没有单独报政策直判"
    v = J.LLMJudge(_echo_sub("x"), name="f").judge_one(
        corpus.filing_text("4", True), "EDGAR", _SEC_URL)
    assert v.policy and not v.degraded and not v.vetoed, v


def t_hybrid_veto_only_pushes_down():
    """**否决只准往下压，不准往上抬。**

    往上抬（"谁说实质就算实质"）会把规则在**自己的训练数据**上的那份
    正确一起并进来——留出集 3/8 到 7/8 的差距，量的就是这份过拟合。
    """
    hy = J.HybridJudge(J.LLMJudge(_echo_sub(""), name="m"), name="hy")
    # 一、模型判实质 + 标题硬标记 + 规则不判实质 → 压回规则的档
    down = ("What KLA (KLAC)'s AI-Fueled Advanced Packaging Momentum Means For "
            "Shareholders\nKLA reported a fiscal fourth-quarter revenue of "
            "$3.2 billion, up 12% year over year.")
    m = J.LLMJudge(_echo_sub(down), name="m").judge_one(down)
    assert m.tier == material_gate.SUBSTANTIVE, "前提没成立：模型这里必须判实质"
    v = J.HybridJudge(J.LLMJudge(_echo_sub(down), name="m"), name="hy").judge_one(down)
    assert v.tier == material_gate.EMPTY and v.vetoed is True, v
    assert "解读体" in v.why, v.why

    # 二、模型判背景 + 规则判实质 → **保持背景**，不许被规则抬上去
    up = "商务部宣布对英伟达 H20 实施出口管制"
    assert material_gate.classify(up)[0] == material_gate.SUBSTANTIVE
    quiet = J.HybridJudge(
        J.LLMJudge(_fake('{"tier":"背景","why":"x","span":"","event":"e"}'),
                   name="m"), name="hy")
    w = quiet.judge_one(up)
    assert w.tier == material_gate.CONTEXT and w.vetoed is False, w
    assert hy.name == "hy"

    # 三、**软标记不参与否决。** "价格动了"经常和真实原因写在同一个标题里——
    #     build96 花了整整一轮把这条分出来，否决权不能把它又合回去。
    soft = "Micron stock jumped on a supply deal with a hyperscaler"
    assert material_gate.hard_marker(soft) == "", "软标记被当成硬标记了（build96）"
    assert material_gate.classify(soft)[0] != material_gate.SUBSTANTIVE, \
        "前提没成立：规则在这条上必须不判实质，否则这个用例是空的"
    s = J.HybridJudge(J.LLMJudge(_echo_sub(soft), name="m"), name="hy").judge_one(soft)
    assert s.tier == material_gate.SUBSTANTIVE and s.vetoed is False, s


def t_hybrid_veto_respects_clause_rescue():
    """**规则自己判实质的，不否决。**

    否决的条件有三个，缺一不可：模型判实质、标题命中硬标记、
    **而且规则自己不判实质**。第三条让分句救援继续有效——
    "$2 Billion in Orders — Is ARM a Buy?" 后半句的荐股钩子
    不该杀掉前半句的在手订单，规则从 build99 起就认这条。
    """
    t = ("Arm Holdings Has $2 Billion in Orders It Cannot Fill Yet "
         "— Is ARM Stock a Buy at $257?")
    assert material_gate.hard_marker(t), "前提没成立：这个标题该有硬标记"
    assert material_gate.classify(t)[0] == material_gate.SUBSTANTIVE, \
        "前提没成立：分句救援该把它救成实质"
    v = J.HybridJudge(J.LLMJudge(_echo_sub(t), name="m"), name="hy").judge_one(t)
    assert v.tier == material_gate.SUBSTANTIVE and v.vetoed is False, v


def t_hybrid_veto_does_not_hurt_the_corpus():
    """**否决在现有语料上一条都不误伤。**

    75 条里满足否决条件（标题硬标记 + 规则不判实质）的有 31 条，
    其中没有一条的期望等级是「实质」。这不是"因为它不会出错所以安全"，
    是"如果它出错，这条用例会红"。
    """
    hurt = []
    for tag, text, want, _n in list(corpus.CASES) + list(corpus.HELDOUT):
        head = text.split("\n", 1)[0]
        if (material_gate.hard_marker(head)
                and material_gate.classify(text)[0] != material_gate.SUBSTANTIVE
                and want == material_gate.SUBSTANTIVE):
            hurt.append((tag, head[:60]))
    assert not hurt, f"否决会误伤 {len(hurt)} 条：{hurt[:3]}"


def t_hybrid_shares_the_cache_and_flushes_through():
    """**混合判定与被它包住的模型共用缓存，而且 flush 要能穿过包装。**

    共用缓存：混合问模型的问题和纯模型一模一样（否决发生在拿到答案之后），
    所以评完 `claude:<m>` 再评 `hybrid:claude:<m>` 一个请求都不发，
    两栏的差异只可能来自否决逻辑。

    flush 穿透：评测原来靠 `getattr(jd, "_cache", None)` 去够私有字段，
    包一层之后那句就够不着了——缓存从不落盘，下次重新调一遍模型，
    **多花的钱不会有任何提示**。所以 `flush()` 是接口的一部分。
    """
    import re
    import tempfile
    assert J.cache_stem("hybrid:claude:x") == J.cache_stem("claude:x") == "claude_x"
    src = (Path(__file__).resolve().parent / "eval_judge.py").read_text("utf-8")
    assert "jd.flush()" in src, "评测没有走 flush() 接口"
    assert not re.search(r"getattr\(\s*jd|jd\._cache", src), \
        "评测还在够 judge 的私有缓存字段"
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "c.json"
        hy = J.HybridJudge(J.LLMJudge(_echo_sub(_REAL), name="m", cache_path=p),
                           name="hy")
        assert hy.judge_one(_REAL).tier == material_gate.SUBSTANTIVE
        hy.flush()
        assert p.exists() and len(p.read_text("utf-8")) > 20, "缓存没落盘"


def t_hybrid_relevance_comes_from_the_model():
    """**相关性整条交给模型。** 19/20 对 13/20，而且规则的错法是**认错**
    （`It's` 被当成 ticker `IT` 的所有格），不是判得严不严——
    这里没有可以保留的规则优势。"""
    hy = J.HybridJudge(J.LLMJudge(_fake('{"relevant": false}'), name="m"),
                       name="hy")
    t = "Gartner Inc reported contract value growth of 6%"
    assert J.RuleJudge().judge_relevance(t, "IT", "Gartner") is True
    assert hy.judge_relevance(t, "IT", "Gartner") is False


def t_hybrid_needs_a_second_judge():
    """`hybrid:rules` 必须报错。**它就是 rules**，没有第二个判定器可混，
    而它会印出一栏看起来像混合判定的分数。"""
    for bad in ("hybrid:rules", "hybrid:"):
        try:
            J.build(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad} 应该报错")


TESTS = [
    ("规则与模型返回同一形状", t_rule_and_llm_return_the_same_shape),
    ("**编造的引文会被降级**", t_fabricated_span_is_downgraded),
    ("引文核对不被空白与大小写骗过", t_span_check_is_not_fooled_by_whitespace_or_case),
    ("**模型不通时显式降级**", t_model_failure_degrades_loudly),
    ("乱回与未知档位一律降级", t_garbage_and_unknown_tier_also_degrade),
    ("**缓存让判定可复现**", t_cache_makes_it_reproducible),
    ("**降级结果不进缓存**", t_degraded_results_are_not_cached),
    ("**评测先报降级率再报分数**", t_eval_reports_degradation),
    ("**judge 自己加载 .env，不靠导入顺序**", t_judge_loads_dotenv_itself),
    ("**提示词不问政策**", t_prompt_never_asks_the_model_about_policy),
    ("政策层没有被接上模型", t_policy_layer_is_untouched),
    ("**留出集真的没被调参用过**", t_heldout_is_actually_held_out),
    ("基线已记录，且不是满分", t_baseline_is_recorded_and_imperfect),
    ("**政策直判：模型连问都不问**", t_policy_beats_the_model),
    ("政策直判不算降级", t_policy_is_not_counted_as_degradation),
    ("**否决只往下压，不往上抬**", t_hybrid_veto_only_pushes_down),
    ("规则自己判实质的不否决（分句救援）", t_hybrid_veto_respects_clause_rescue),
    ("**否决在语料上一条不误伤**", t_hybrid_veto_does_not_hurt_the_corpus),
    ("**混合共用缓存，flush 穿过包装**", t_hybrid_shares_the_cache_and_flushes_through),
    ("相关性整条交给模型", t_hybrid_relevance_comes_from_the_model),
    ("hybrid:rules 必须报错", t_hybrid_needs_a_second_judge),
]

print("=" * 72)
print("判定器自测 —— 模型接进来之前，先把护栏钉死")
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
