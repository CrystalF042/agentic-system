#!/usr/bin/env python3
"""进料自测 —— **实质度必须在截断之前判**。

    python scripts/test_intake.py

修的是这个：原来按相关性排序 → 截断到 10 条 → 才判实质度。
于是一条真有增量事实的材料，只要相关性排在第 11 位就永远到不了闸门，
而报告写的是「10 条材料，实质 0」——读起来是"今天确实没东西"。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _no_network                                            # noqa: E402,F401
"""测试期间禁止联网 —— 靠真实行情才通过的断言，换台机器就是另一个结果。"""

from cio import material_gate, unit_a                         # noqa: E402
from cio.models import MaterialItem                           # noqa: E402

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


class N:
    """最小的新闻条：判定看标题 + 正文，排序再看相关性分。"""

    def __init__(self, title, score, body=""):
        self.title_original = title
        self.title_zh = ""
        self.body = body
        self.score = score


_FILLER = "Nvidia Stock: What Analysts Expect Ahead of Q3 Earnings ({})"
_REAL = ("Nvidia announced it has completed the acquisition of Run:ai "
         "for $700 million")


def _mix(n_filler=10, real_score=1):
    items = [N(_FILLER.format(i), 100 - i) for i in range(n_filler)]
    items.append(N(_REAL, real_score))
    return items


def t_substantive_survives_truncation():
    """**核心**：实质材料相关性排最后，仍必须进前 10。"""
    items = _mix()
    ranked, tiers, tier_of = unit_a._rank_by_substance(items)
    kept = ranked[:unit_a.MATERIAL_CAP]
    assert tiers.get(material_gate.SUBSTANTIVE) == 1, tiers
    assert any(tier_of[id(n)] == material_gate.SUBSTANTIVE for n in kept), \
        "实质材料被截掉了 —— 这正是要修的缺陷"
    assert ranked[0].title_original == _REAL, ranked[0].title_original


def t_old_order_would_have_dropped_it():
    """对照：纯按相关性排序时，那条实质材料确实进不了前 10。"""
    items = _mix()
    _r, _t, tier_of = unit_a._rank_by_substance(items)
    old = sorted(items, key=lambda n: -n.score)[:unit_a.MATERIAL_CAP]
    assert not any(tier_of[id(n)] == material_gate.SUBSTANTIVE for n in old), \
        "对照组没有复现旧行为，这个用例就证明不了什么"


def t_relevance_still_breaks_ties():
    """同档之内仍按相关性排 —— 实质度只决定分组，不打乱组内顺序。"""
    items = [N(_FILLER.format(i), 100 - i) for i in range(5)]
    ranked, _t, _to = unit_a._rank_by_substance(items)
    assert [n.score for n in ranked] == [100, 99, 98, 97, 96], \
        [n.score for n in ranked]


def t_ranking_uses_the_gate_classifier():
    """**用的是闸门自己的分类器，不是另写一套近似规则。**

    做法是把 `material_gate.classify` 换掉，看排序结果是否跟着变——
    跟着变就证明它真的在调那个函数，而不是复制了一份规则。
    两套规则一定会漂移，而这一套只在排序里悄悄生效，漂了没人会发现。
    """
    items = [N("aaa", 1), N("zzz", 99)]
    real = material_gate.classify
    try:
        material_gate.classify = lambda t: (
            (material_gate.SUBSTANTIVE, "假装实质") if t == "aaa"
            else (material_gate.EMPTY, "假装无实质"))
        ranked, _t, _to = unit_a._rank_by_substance(items)
        assert ranked[0].title_original == "aaa", \
            "换掉 classify 后排序没变 —— 说明它没在用闸门的分类器"
    finally:
        material_gate.classify = real


_VAGUE = "AMD in the spotlight this week"
_BODY = ("Advanced Micro Devices announced it has completed the acquisition "
         "of ZT Systems for $4.9 billion, the company said Monday.")


def t_basis_text_includes_body():
    """标题看不出实质、正文里才有 —— 判定依据必须带上正文。

    真机上 AMD/LRCX 就是这样：修完截断反而丢了实质材料，
    因为排序只看标题、闸门看的是别的东西。
    """
    n = N(_VAGUE, 1, _BODY)
    assert material_gate.classify(_VAGUE)[0] != material_gate.SUBSTANTIVE
    assert material_gate.classify(unit_a.basis_text(n))[0] == material_gate.SUBSTANTIVE
    assert _BODY[:40] in unit_a.basis_text(n)


def t_gate_reads_basis_not_display_text():
    """闸门判的是 basis_text（源头文本），不是 text（含模型摘要）。"""
    n = N(_VAGUE, 1, _BODY)
    display = _VAGUE + "：这周值得关注"
    without = MaterialItem(id=1, text=display)
    with_basis = MaterialItem(id=1, text=display, basis_text=unit_a.basis_text(n))
    assert material_gate.assess([without])["n_sub"] == 0
    assert material_gate.assess([with_basis])["n_sub"] == 1, \
        "闸门没有读 basis_text —— 它还在拿模型摘要判实质度"


def t_ranker_and_gate_cannot_disagree():
    """**核心不变量**：同一条材料，排序的判定与闸门的判定必须一致。

    上一版两边看不同的文本，于是在两个方向上都出现过分歧：
    AVGO 排序判实质而闸门判不是；AMD/LRCX 反过来。
    """
    for title, body in ((_VAGUE, _BODY), (_REAL, ""), (_FILLER.format(1), "")):
        n = N(title, 1, body)
        rank_tier, _ = material_gate.classify(unit_a.basis_text(n))
        m = MaterialItem(id=1, text=title + "：给人读的摘要",
                         basis_text=unit_a.basis_text(n))
        gate_tier, _ = material_gate.classify(material_gate.basis_of(m))
        assert rank_tier == gate_tier, (title[:40], rank_tier, gate_tier)


def t_basis_of_falls_back_to_text():
    """老调用方没有 basis_text 时回退到 text，不炸也不静默变空。"""
    m = MaterialItem(id=1, text="something happened")
    assert material_gate.basis_of(m) == "something happened"


def t_enrich_budget_is_before_the_cut():
    """补正文的名额必须大于最终条数 —— 判定发生在截断之前。"""
    assert unit_a.BASIS_ENRICH_N > unit_a.MATERIAL_CAP, \
        (unit_a.BASIS_ENRICH_N, unit_a.MATERIAL_CAP)


def t_no_crash_on_empty():
    ranked, tiers, tier_of = unit_a._rank_by_substance([])
    assert ranked == [] and tiers == {} and tier_of == {}


_STUB = "NVIDIA CORP 8-K (2026-08-28)\nSEC filing 8-K filed 2026-08-28."
_FILED = _STUB + "\n" + ("On August 28, 2026, NVIDIA Corporation entered into "
                          "a definitive agreement to acquire " * 6)
_SEC_URL = "https://www.sec.gov/Archives/edgar/data/1045810/x.htm"


def t_primary_source_with_body_is_substantive():
    """取到正文的 8-K = 实质。**按来源认定，不按标题措辞。**

    公告标题就是一个表单号加日期,没有完成时动词也没有金额锚点——
    按文本规则会被判成背景,于是"分析师预计…"和"公司已提交 8-K"
    落到同一层级,方向还正好反了。
    """
    tier, why = material_gate.tier_of(_FILED, "EDGAR", _SEC_URL)
    assert tier == material_gate.SUBSTANTIVE, (tier, why)
    # 只有标题时,文本规则判「背景」——所以来源判定确实有用武之地
    assert material_gate.classify("NVIDIA CORP 8-K (2026-08-28)")[0] \
        == material_gate.CONTEXT


def t_primary_source_without_body_is_not_substantive():
    """**取不到正文的公告只算背景。**

    只凭表单号就判实质,闸门会开而辩论手里是一条什么都没说的存根——
    报告于是声称有基本面依据而它没有,比闸门不开更糟。
    """
    tier, why = material_gate.tier_of(_STUB, "EDGAR", _SEC_URL)
    assert tier == material_gate.CONTEXT, (tier, why)
    assert "正文未取到" in why, why


def t_our_own_placeholder_would_fool_the_text_rule():
    """**采集器自己写的占位文本，会把文本规则喂饱。**

    `fetch_edgar_recent` 给每份公告造了一行 body：

        SEC filing 8-K filed 2026-08-28.

    这行字是**我们自己生成的**，而里面正好有 `8-K`（硬锚点）和 `filed`
    （完成动作）——文本规则于是判它「实质」。

    那不是关于世界的证据，是关于**我们自己那个占位串**的证据。
    每一份公告都会自动过闸，不管里面写了什么，甚至不管它有没有内容。
    `PRIMARY_MIN_CHARS` 守的就是这个：**没真取到正文就不算实质。**
    """
    assert material_gate.classify(_STUB)[0] == material_gate.SUBSTANTIVE, \
        "占位串没有触发文本规则,这个用例就证明不了什么"
    assert material_gate.tier_of(_STUB, "EDGAR", _SEC_URL)[0] \
        == material_gate.CONTEXT, "来源守卫没拦住内容为空的公告"


def t_short_ticker_substring_false_matches():
    """**三四个字母的 ticker 不能用子串匹配。**

    真机上 ARM 的相关性闸放进来这几条,而且被判成实质:

        Venezuelan opposition up in **arms** over US oil stake
        Reality check for China's c**arm**akers
        Ph**arm**a stocks slide / Al**arm** bells

    委内瑞拉的石油新闻成了 ARM 的实质材料——真跑一部,辩论会拿它当论据。
    """
    bad = ["Venezuelan opposition up in arms over oil",
           "Reality check for China's carmakers",
           "Pharma stocks slide on tariff threat",
           "Alarm bells for chip supply chains"]
    good = ["Arm Holdings (ARM) rose 2.8%",
            "ARM HOLDINGS PLC /UK 144 (2026-08-27)",
            "Arm's royalty revenue grew 12%",
            "Analysts weigh in on ARM."]
    for t in bad:
        assert not unit_a.alias_hit("ARM", t), f"子串误命中: {t}"
    for t in good:
        assert unit_a.alias_hit("ARM", t), f"真材料被漏掉: {t}"


def t_cjk_alias_still_uses_substring():
    """中文别名仍走子串 —— 中文没有空格,加词边界反而会漏。"""
    assert unit_a.alias_hit("工商银行", "中国工商银行发布公告")


def t_ownership_forms_do_not_trigger_the_gate():
    """**Form 4 / 144 / SC 13G 不触发闸门。**

    真机上 AMD 被判「材料充分」,三条实质材料全是内部人交易申报;
    同一天真正的商业事件(与思科在沙特的 AI 基础设施上线)反倒判成背景。
    **证据层级整个反了。**

    它们不是"无实质"——是真实提交、依法必须披露的文件;但说的是
    *某个人卖了股票*,不是*这家公司发生了什么*。
    """
    body = " On August 27, 2026, the company entered into an agreement." * 8
    for form in ("4", "144", "SC 13G"):
        text = f"CO {form} (2026-08-27)\nSEC filing {form} filed 2026-08-27." + body
        tier, why = material_gate.tier_of(text, "EDGAR", _SEC_URL)
        assert tier == material_gate.CONTEXT, (form, tier)
        assert "不触发闸门" in why, why
    for form in ("8-K", "10-Q", "10-K"):
        text = f"CO {form} (2026-08-27)\nSEC filing {form} filed 2026-08-27." + body
        assert material_gate.tier_of(text, "EDGAR", _SEC_URL)[0] \
            == material_gate.SUBSTANTIVE, form


# ------------------------------------------------------------ build96 标题否决
# build91 把正文加进判定依据,解决了"标题含糊、事实在正文里"的漏判,
# **却没有重新校准按标题写的规则**。后果:随便一篇评论文的正文里都会有
# 一个过去式动词和一个百分数,于是 `done + pct` 这条最弱通路把评论顶成了实质。
#
# 下面三条是 build95 真机上 ARM 判为"实质"的全部材料,理由清一色
# 「已发生动作 + 具体比例」。三条没有一条讲了 ARM 这家公司发生了什么。
_ARM_COMMENTARY = [
    ("Arm (ARM) Stock Looks Above Fair Value Even After AI Progress\n"
     "Arm Holdings reported royalty revenue growth of 25% in the June quarter, "
     "and management raised full-year guidance. Even so, our fair value estimate "
     "of $105 implies the shares trade well above what the business is worth.",
     "估值观点文"),
    ("Advanced Micro Devices vs. Arm Holdings: Comparing Revenue Trends\n"
     "AMD reported revenue of $7.7 billion last quarter, up 32%. Arm posted "
     "royalty growth of 25%.",
     "对比文"),
    ("Arm Rises 2.8% as $272 Target Prices the CPU Tollbooth\n"
     "Shares of Arm Holdings climbed 2.8% on Thursday after an analyst raised "
     "his price target to $272.",
     "目标价"),
]


def t_title_veto_blocks_commentary_with_factual_body():
    """**标题自报是评论文的,正文里的过去式动词不能把它扶成实质材料。**

    正文里有 "reported ... 25%" 是真的,但那是作者为了论证自己的估值观点
    引用的旧数字,不是今天的增量事实。按 build95 的规则它们全是「实质」。
    """
    for text, why_want in _ARM_COMMENTARY:
        tier, why = material_gate.classify(text)
        assert tier != material_gate.SUBSTANTIVE, (text.split("\n")[0], tier, why)
        assert why == why_want, (text.split("\n")[0], why, why_want)


def t_title_veto_keeps_the_documented_exception():
    """否决不能吃掉**标题里就带真事件**的那一类——模块文档承诺过这条通路。"""
    tier, _ = material_gate.classify("Ahead of earnings, NVDA announced a $50B buyback")
    assert tier == material_gate.SUBSTANTIVE, "前瞻词 + 真事件被误杀了"
    # build91 修的那条:标题含糊,事实在正文里。否决只看标题的**负向**标记,
    # 标题不含负向标记时正文照常参与判定。
    tier, _ = material_gate.classify(
        "AMD, Cisco and HUMAIN Expand Saudi Arabia's AI Infrastructure\n"
        "AMD announced that its Instinct MI355X systems have gone live under a "
        "contract awarded earlier this year, part of a $10 billion buildout.")
    assert tier == material_gate.SUBSTANTIVE, "build91 的正文通路被否决吃掉了"


def t_price_move_does_not_veto_a_real_cause():
    """**「股价动了」不是自报家门,它经常和真实原因写在同一个标题里。**

    把这两条否决掉,丢的是监管放行和一份百亿合同——闸门最该放行的东西。
    所以行情复述是【软】标记:照常打标签,但不参与标题否决。
    """
    for title in ("AMD rose 12% after announcing a $10 billion Saudi contract",
                  "Nvidia stock jumped after Beijing approved H20 sales",
                  "Micron shares fell 8% after the company cut its Q4 guidance"):
        tier, why = material_gate.classify(title)
        assert tier == material_gate.SUBSTANTIVE, (title, tier, why)
    # 反向:只有行情、没有原因的,仍然不是实质。
    for title in ("Micron Rises 4.1% as Analysts Lift Targets",
                  "Nvidia stock rose 3% on Tuesday"):
        assert material_gate.classify(title)[0] != material_gate.SUBSTANTIVE, title


def t_soft_markers_are_declared_not_inferred():
    """软标记是一份**显式名单**,不是靠 `_NEGATIVE` 的排列顺序碰出来的。

    靠顺序就意味着:往列表里插一条新规则,可能悄悄改掉另一条材料的判定,
    而且不会有任何报错。所以 `_neg_scan` 把"第一个命中"和"第一个硬命中"
    分开返回,否决只用后者。
    """
    assert material_gate._SOFT_WHY, "软标记名单是空的"
    whys = {why for _rx, why in material_gate._NEGATIVE}
    unknown = material_gate._SOFT_WHY - whys
    assert not unknown, f"软标记名单里有 _NEGATIVE 中不存在的理由：{unknown}"
    first, hard = material_gate._neg_scan("Arm Rises 2.8% as $272 Target Prices")
    assert hard and hard not in material_gate._SOFT_WHY, (first, hard)
    first, hard = material_gate._neg_scan("Nvidia stock rose 3% on Tuesday")
    assert first in material_gate._SOFT_WHY and not hard, (first, hard)


def t_vs_between_numbers_is_not_a_comparison_piece():
    """**"$13.3 billion vs $12.9 billion guidance" 是业绩事实,不是对比文。**

    对比文指的是"把两家公司摆一起比",两侧都得是词。裸的 `vs` 会把
    "实际 vs 指引" 这类最标准的业绩标题整批误杀。
    """
    tier, why = material_gate.classify(
        "Intel Q3 revenue $13.3 billion vs $12.9 billion guidance\n"
        "Intel reported third-quarter revenue of $13.3 billion, above its own "
        "guidance, and said foundry losses narrowed.")
    assert tier == material_gate.SUBSTANTIVE, (tier, why)
    assert material_gate.classify(
        "AMD vs Intel: Which Chip Stock Is the Better Buy?")[0] \
        != material_gate.SUBSTANTIVE


def t_regression_corpus():
    """**整份回归语料跑一遍 —— 规则每改一次都要过它,不是只跑新加的那几条。**

    build91 加正文那一改是对的,但它悄悄把最弱那条通路变成了几乎恒真,
    而**没有人回去重验旧判例**。四轮之后才在真机 --verbose 里看见后果。
    这份语料的全部作用就是让那种事在改动当天就红。
    """
    import _material_corpus as corpus
    bad = corpus.run(material_gate)
    assert not bad, "语料 {} 条中 {} 条判错：\n{}".format(
        corpus.TOTAL, len(bad),
        "\n".join(f"      [{b}] {h}\n        期望 {w} 实得 {g}·{y}"
                  for b, h, w, g, y in bad))


def t_corpus_expectations_are_not_a_copy_of_behaviour():
    """语料的期望值必须是**判断**,不是照着当前行为回填的。

    照实现回填的语料是实现的复印件:规则改错时它跟着一起改错,永远不会红。
    这里做不到形式化验证,能做的是保证语料**两个方向都有**——
    只收"应该判实质"的会漏掉放行过宽,只收"应该拦住"的会漏掉误杀。
    """
    import _material_corpus as corpus
    wants = [w for _b, _t, w, _n in corpus.CASES]
    assert wants.count(corpus.SUBSTANTIVE) >= 5, "语料里『应判实质』的太少"
    assert wants.count(corpus.EMPTY) >= 5, "语料里『应拦住』的太少"
    # 每条都得写清来处与理由,否则半年后没人知道它在守什么。
    for b, t, _w, note in corpus.CASES:
        assert b and note, (b, t[:40])


def t_filing_form_parsing():
    """表单号从我们自己写的那句里取,不去猜公司法定名称后面那截。"""
    assert material_gate.filing_form("X 4 (d)\nSEC filing 4 filed 2026-08-27.") == "4"
    assert material_gate.filing_form(
        "X\nSEC filing SC 13G filed 2026-08-27.") == "SC 13G"
    assert material_gate.filing_form("no filing here") == ""


def t_primary_detection():
    assert material_gate.is_primary("EDGAR", "")
    assert material_gate.is_primary("", "https://data.sec.gov/submissions/x.json")
    assert not material_gate.is_primary("Reuters", "https://reuters.com/x")
    assert not material_gate.is_primary("Zacks", "")


def t_news_sources_are_not_upgraded():
    """普通新闻源**不因来源被升级也不被降级** —— 那条老原则没变。"""
    hot = "Is Nvidia Stock a Buy Ahead of Q2 Earnings?"
    assert material_gate.tier_of(hot, "Zacks", "https://zacks.com/x")[0] \
        == material_gate.classify(hot)[0]
    real = ("Reuters: Nvidia announced it has completed the acquisition of "
            "Run:ai for $700 million")
    assert material_gate.tier_of(real, "Reuters", "https://reuters.com/x")[0] \
        == material_gate.SUBSTANTIVE


def t_ranker_and_gate_agree_on_filings():
    """一手披露上,排序与闸门也必须一致（同一个 tier_of 入口）。"""
    from cio.models import MaterialItem

    class Src:
        def __init__(s, n, u):
            s.name, s.url = n, u

    class NN:
        def __init__(s, t, b, src):
            s.title_original, s.title_zh, s.body, s.score = t, "", b, 1
            s.sources = [src]
    for body in (_FILED, ""):
        n = NN("NVIDIA CORP 8-K (2026-08-28)", body, Src("EDGAR", _SEC_URL))
        ranked, tiers, tier_of = unit_a._rank_by_substance([n])
        m = MaterialItem(id=1, text="给人读的", basis_text=unit_a.basis_text(n),
                         source_name="EDGAR", source_url=_SEC_URL)
        gate = material_gate.assess([m])["labels"][1][0]
        assert tier_of[id(n)] == gate, (body[:20], tier_of[id(n)], gate)


_EDGAR_TITLES = {
    "AMD": "ADVANCED MICRO DEVICES INC 8-K (2026-08-28)",
    "MU": "MICRON TECHNOLOGY INC 10-Q (2026-08-28)",
    "KLAC": "KLA CORP 8-K (2026-08-28)",
    "NVDA": "NVIDIA CORP 8-K (2026-08-28)",
}


class _Src:
    def __init__(self, n, u):
        self.name, self.url = n, u


class _NS:
    is_noise = False

    def __init__(self, title, src, body=""):
        self.title_original, self.title_zh, self.body = title, "", body
        self.score, self.sources = 1, [src]


def t_filings_bypass_the_relevance_filter():
    """**按 CIK 取回的公告不过相关性闸。**

    相关性闸认的是"标的名出现在原始标题里"，而公告标题用的是公司**法定名称**：
    `ADVANCED MICRO DEVICES INC 8-K` 里没有 "AMD"。
    真机上 10 只票有 9 只的公告在这一步被全部丢光，只有 ARM 侥幸活下来
    （"ARM" 恰好是 "Arm Holdings plc" 的子串）——而进料行完全看不出来。
    """
    from cio import topic
    sec = _Src("EDGAR", "https://www.sec.gov/Archives/edgar/data/2488/x.htm")
    for sym, title in _EDGAR_TITLES.items():
        info = topic.parse_subject(sym)
        kept = unit_a._prefilter([_NS(title, sec)], info)
        assert len(kept) == 1, f"{sym} 的公告被相关性闸丢掉了：{title}"


def t_dictionary_word_ticker_needs_an_identity_form():
    """**ARM 本身就是一个英文单词,词边界对它无能为力。**

    build95 把子串改成词边界,挡住了 arms / pharma / carmakers。
    真机 8/31 ARM 的 10 条材料里仍有 4 条完全无关:

        Current ARM mortgage rates report        浮动利率房贷
        2-alarm fire at small business in Glen Arm  地名
        Mom Who Had Arm Amputated After Shark Attack 身体部位
        debt linked to its asset management arm   部门

    每只标的只有 10 个进闸门的名额,这四条**挤掉了真材料**
    ——当天 26 条相关材料里 16 条根本没进闸门。
    """
    bad = ["Current ARM mortgage rates report for Aug. 31, 2026 - Fortune",
           "Multiple crews battle 2-alarm fire at small business in Glen Arm",
           "Mom Who Had Arm Amputated After Shark Attack Shuts Down GoFundMe",
           "Guggenheim affiliate buys up debt linked to its asset management arm"]
    good = ["Arm Holdings (ARM) Shifts Strategy to Sell Own Data Center Chips",
            "ARM's Expanding AI Growth Opportunity Goes Beyond Market Hype",
            "ARM stock rose 2.8% on Thursday",
            "NASDAQ:ARM upgraded by analysts",
            "$ARM is the cleanest AI royalty play"]
    for t in bad:
        assert not unit_a.symbol_hit("ARM", t), f"英文词误命中: {t}"
    for t in good:
        assert unit_a.symbol_hit("ARM", t), f"真材料被漏掉: {t}"
    # **名单之外靠兜底**：大小写对不上的裸匹配一律不认，不需要维护名单。
    assert "ZZZ" not in unit_a.AMBIGUOUS_SYMBOLS
    assert unit_a.symbol_hit("ZZZ", "ZZZ posts record revenue")
    assert not unit_a.symbol_hit("ZZZ", "he zzz through the meeting")
    # 不与常用词撞车的符号不受影响。
    assert unit_a.symbol_hit("AMD", "AMD lifted to Strong Buy - Investing.com")


def t_prefilter_uses_symbol_hit_for_the_bare_ticker():
    """相关性闸真的走了 symbol_hit —— 只在 symbol_hit 上加规则、
    而 `_prefilter` 仍在用旧的 alias_hit，是这个仓库出过多次的那类"改了但没接上"。
    """
    from cio import topic
    zx = _Src("Zacks", "https://zacks.com/x")
    info = topic.parse_subject("ARM")
    kept = unit_a._prefilter(
        [_NS("Current ARM mortgage rates report for Aug. 31, 2026", zx)], info)
    assert kept == [], "房贷利率新闻不该过相关性闸"
    kept = unit_a._prefilter(
        [_NS("Arm Holdings (ARM) Shifts Strategy to Sell Own Data Center Chips",
             zx)], info)
    assert len(kept) == 1, "真材料被相关性闸挡掉了"


_SAUDI_A = ("AMD, Cisco and HUMAIN Expand Saudi Arabia's AI Infrastructure "
            "as AMD Instinct Systems Go Live - TipRanks")
_SAUDI_B = ("AMD and Cisco Expand AI Infrastructure in Saudi Arabia\n"
            "AMD and Cisco have expanded their AI infrastructure.")
_ARM_SHIFT = "Arm Holdings (ARM) Shifts Strategy to Sell Own Data Center Chips"


def t_gate_counts_events_not_articles():
    """**闸门数的必须是事件，不是文章。**

    真机 8/31 AMD 判「材料充分」,三条实质里有两条是同一件事——
    一份新闻稿被两家转载。`_SUFFICIENT_N = 3` 就被转载量顶穿了。

    这和 build94「8 份历史公告 = 材料充分」是同一个家族：
    **同一件事被多次计数就能开门**,而开门意味着启动一场完整的多空辩论。
    """
    ms = [MaterialItem(id=1, text=_SAUDI_A, basis_text=_SAUDI_A),
          MaterialItem(id=2, text=_SAUDI_B, basis_text=_SAUDI_B),
          MaterialItem(id=3, text=_ARM_SHIFT, basis_text=_ARM_SHIFT)]
    g = material_gate.assess(ms)
    assert g["n_sub"] == 3, g["n_sub"]
    assert g["n_sub_events"] == 2, (g["n_sub_events"], g["event_groups"])
    assert g["level"] == material_gate.THIN, g["level"]
    # **归并必须写在标签上。** 不写的话，这一步就是又一个看不见的变换。
    assert "同一事件" in g["labels"][2][1], g["labels"][2][1]
    assert "同一事件" not in g["labels"][1][1], g["labels"][1][1]


def t_different_events_are_not_merged():
    """同一家公司的两件不同的事不能被并掉 —— 实体和数字都不一样。"""
    a = "AMD Wins $2 Billion Order From Oracle"
    b = "AMD Wins $3 Billion Order From Meta"
    assert not material_gate.same_event(material_gate.event_key(a),
                                        material_gate.event_key(b))
    # 转载：短标题是长标题的子集，用重合系数才并得上（Jaccard 会漏）
    ka, kb = material_gate.event_key(_SAUDI_A), material_gate.event_key(_SAUDI_B)
    assert material_gate.same_event(ka, kb), (sorted(ka), sorted(kb))
    inter = len(ka & kb)
    assert inter / len(ka | kb) < material_gate.SAME_EVENT_OVERLAP, \
        "Jaccard 也能并上的话，这个用例证明不了必须用重合系数"


def t_event_key_ignores_source_suffix_and_body():
    """指纹只看标题、且去掉 ' - 源名' 后缀。

    **正文不能进指纹**：真机上同一篇文章三轮抓到三段不同的正文，
    用它做指纹，同一件事会时而并、时而不并，而那种不稳定不会报错。
    """
    base = "AMD and Cisco Expand AI Infrastructure in Saudi Arabia"
    assert material_gate.event_key(base) == \
        material_gate.event_key(base + " - Yahoo Finance")
    assert material_gate.event_key(base) == \
        material_gate.event_key(base + "\nSome body text about revenue.")
    assert "the" not in material_gate.event_key("The AMD and the deal")


def t_fact_clause_survives_the_hook_clause():
    """**破折号前是事实，破折号后是钩子。**

    真机 8/31 一天两条真事实死在这上面:

        Arm Holdings Has $2 Billion in Orders It Cannot Fill Yet
            — Is ARM Stock a Buy at $257?
        IBM Just Opened Its Mainframes to Arm — Is the Market Missing the Shift?

    标题否决看的是**整条标题**,于是后半句的钩子杀掉了前半句的硬事实。
    """
    # 这两条**只能**靠分句救回来：整条标题上既没有完成动作（Has / Jumps
    # 都不是），钩子那半句又带硬标记。
    for t in ("Arm Holdings Has $2 Billion in Orders It Cannot Fill Yet "
              "— Is ARM Stock a Buy at $257?",
              "AMD Stock Jumps on $10 Billion Contract — Is It Too Late to Buy?"):
        tier, why = material_gate.classify(t)
        assert tier == material_gate.SUBSTANTIVE, (t[:46], tier, why)
        assert "分句" in why, why
    # 这一条整条标题就站得住（Signs + $4 Billion），走的是 build96 那条老通路。
    # **走哪条不重要，判对才重要**——所以这里只断言等级。
    assert material_gate.classify(
        "Micron Signs a $4 Billion Supply Deal: Should You Buy the Stock?")[0] \
        == material_gate.SUBSTANTIVE
    # **别人的估计不是事实。** 无完成动作那条通路必须挡住前瞻标记。
    for t in ("Analysts See $5 Billion in Orders for AMD — Is It Enough?",
              "Analysts Expect $5 Billion in Orders for AMD — Is It Enough?",
              "AMD Could Win $5 Billion in Orders — Is It Enough?",
              "AMD's $10 Billion Opportunity in Sovereign AI — Is the Market "
              "Underpricing It?",
              "Nvidia Stock Soars to $200 Billion Market Cap — Time to Sell?",
              # **分句自身带硬标记的不救。** 否则绕回 build96 那个缺陷：
              # 观点文只要在标题里引一个真数字就能被顶成实质材料。
              "Is AMD a Buy After Its $10 Billion Contract? — Analysts Weigh In",
              "3 Reasons AMD's $10 Billion Saudi Contract Matters — Our Take"):
        tier, why = material_gate.classify(t)
        assert tier != material_gate.SUBSTANTIVE, (t[:46], tier, why)


def t_fact_clause_needs_a_real_separator_and_a_clean_clause():
    """救援只在**多分句**标题上生效,且分句自身不能是硬标记那一类。

    不拆逗号:"AMD, Cisco and HUMAIN Expand …" 会被拆坏。
    分句自身带硬标记的不救:那就回到"整条标题自报家门"那个情形。
    """
    assert material_gate._fact_clause("AMD wins a $2 billion order") == "", \
        "单分句不该走救援"
    assert material_gate._fact_clause(
        "Advanced Micro Devices vs. Arm Holdings: Comparing Revenue Trends") == ""
    # 逗号不是分隔符
    assert material_gate._fact_clause(
        "Ahead of earnings, NVDA announced a $50B buyback") == ""
    got = material_gate._fact_clause(
        "Intel Wins a $3 Billion Foundry Order; Should You Buy?")
    assert got.startswith("Intel Wins"), got
    # 短分句照样可以是事实："AMD — $2B orders" 里锚点和事件都在。
    assert material_gate._fact_clause("AMD — $2B orders") == "$2B orders"


def _collect_tree():
    import ast
    import inspect
    src = inspect.getsource(unit_a.collect_materials)
    return src, ast.parse(src.lstrip())


def t_ownership_filings_do_not_hog_gate_slots():
    """**按定义不可能开门的材料，不该先占名额。**

    真机 8/31 的 AMD：10 个闸门名额里 3 个给了内部人申报（Form 4/4/144），
    同一行还写着「截掉 20 条」——**30% 的窗口花在按设计不可能触发闸门
    的纸上**，而门外还有 20 条相关材料。它们相关性分很高，所以稳稳排在前面。

    普通新闻至少**可能**在补完正文后变成实质（build91 那一类），
    持股申报不会：`tier_of` 一看表单号就短路。所以钉死的排在同档最后。

    **不是丢弃**——照常显示、照常可引用，改的只是谁先占名额。
    """
    body = " On August 27, 2026, the reporting person sold shares." * 8
    items = [_NS(f"CO 4 (2026-08-2{i})\nSEC filing 4 filed 2026-08-2{i}.{body}",
                 _Src("EDGAR", _SEC_URL)) for i in (7, 6)]
    for n, sc in zip(items, (95, 94)):
        n.score = sc
    news = _NS("AMD in talks with a hyperscaler on capacity", _Src("Zacks", "z"))
    news.score = 10                       # 相关性分远低于公告
    ranked, _t, tier_of = unit_a._rank_by_substance(items + [news])
    assert ranked[0] is news, \
        [n.title_original[:40] for n in ranked]
    # 档位没被改，只是排序靠后
    assert all(tier_of[id(n)] == material_gate.CONTEXT for n in items)


def t_only_categorically_pinned_materials_sink():
    """钉死的只有持股申报。**8-K 取不到正文也不算钉死**——它补上正文
    还可能变成实质，把它一起沉底就会误伤真正的事件性披露。
    """
    body = " On August 27, 2026, the company entered into an agreement." * 8
    for form in ("4", "144", "SC 13G"):
        t = f"CO {form} (2026-08-27)\nSEC filing {form} filed 2026-08-27.{body}"
        assert material_gate.never_substantive(t, "EDGAR", _SEC_URL), form
    for form in ("8-K", "10-Q", "10-K"):
        t = f"CO {form} (2026-08-27)\nSEC filing {form} filed 2026-08-27.{body}"
        assert not material_gate.never_substantive(t, "EDGAR", _SEC_URL), form
    stub = "CO 8-K (2026-08-27)\nSEC filing 8-K filed 2026-08-27."
    assert material_gate.tier_of(stub, "EDGAR", _SEC_URL)[0] \
        == material_gate.CONTEXT
    assert not material_gate.never_substantive(stub, "EDGAR", _SEC_URL), \
        "取不到正文的 8-K 被钉死了 —— 它补上正文还可能是实质"
    assert not material_gate.never_substantive("AMD wins a $2B order", "Zacks", "z")


def t_every_drop_reason_is_printed():
    """**四个丢弃原因全要印，不只是符号消歧。**

    build98 只印了符号消歧那一个，另外三个收进 `dropped_by` 就扔了。
    后果立刻就来：真机 ARM 有 18 条丢弃完全没有说明，而那 18 条里
    到底有没有一条真材料被 `is_noise` 当成标题党杀掉，没有任何地方看得见。
    **收了不印和没收是一回事。**
    """
    base = {"raw": 107, "scored": 55, "pool": 10, "cap": 10, "kept": 10,
            "dropped": 0, "dropped_substantive": 0, "enriched": 6,
            "relevant": 10, "tiers_before_cap": {"实质": 1}, "pool_limit": 40,
            "pool_cut": 0}
    s = unit_a.intake_note({"intake": dict(
        base, dropped_symbol=27,
        dropped_by={unit_a.DROP_SYMBOL: 27, unit_a.DROP_NO_SUBJECT: 12,
                    unit_a.DROP_CLICKBAIT: 4, unit_a.DROP_OFFTOPIC: 2})})
    assert "符号消歧丢弃 27 条" in s and "73%" in s, s
    for r, c in ((unit_a.DROP_NO_SUBJECT, 12), (unit_a.DROP_CLICKBAIT, 4),
                 (unit_a.DROP_OFFTOPIC, 2)):
        assert f"{r} {c}" in s, (r, s)
    # 没有符号丢弃时，其余原因仍然要印
    s2 = unit_a.intake_note({"intake": dict(
        base, relevant=30, dropped_symbol=0,
        dropped_by={unit_a.DROP_CLICKBAIT: 3})})
    assert "清洗丢弃" in s2 and f"{unit_a.DROP_CLICKBAIT} 3" in s2, s2


def t_prefilter_sees_every_deduped_item():
    """**清洗必须跑在全部去重结果上，不能先按相关性砍到池子大小。**

    老代码是「先砍再筛」:

        pool = sorted(news, key=score)[:MATERIAL_POOL]   # 先砍到 40
        kept = _prefilter(pool, info)                    # 再判相关性

    真机 8/31 的 ARM:去重 55 → 池 40（砍掉 15,没人看过）→ 相关 10。
    活着进入清洗的 40 条里有 30 条随即被判不相关——**池子的名额
    四分之三花在了马上要扔的东西上**,同时 15 条从没被检查过的直接没了。

    这和 build91 修的是同一个缺陷,只是在管道的另一端。
    """
    import ast
    _src, tree = _collect_tree()
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_prefilter"]
    assert calls, "collect_materials 里没有调 _prefilter？"
    for c in calls:
        arg = c.args[0]
        assert not isinstance(arg, ast.Subscript), \
            "_prefilter 收到的是一个切片 —— 又变回「先砍再筛」了"
        assert isinstance(arg, ast.Name), ast.dump(arg)
    # 池截断必须在清洗**之后**
    pool_lines = [n.lineno for n in ast.walk(tree)
                  if isinstance(n, ast.Name) and n.id == "MATERIAL_POOL"]
    assert pool_lines, "collect_materials 里找不到 MATERIAL_POOL"
    assert min(pool_lines) > max(c.lineno for c in calls), \
        "池截断切在了清洗之前"


def t_pool_cut_cannot_rank_by_substance_yet():
    """**池子这一刀不能按实质度排——因为此时正文还没取。**

    CAP 那一刀是补完正文之后才切的,所以可以实质优先。
    池子这一刀在补正文之前,判定只能看标题,而 build91 修的正是
    "标题含糊、事实在正文里"那一类:按标题级实质度排序,恰好会把
    这类材料排到最后先砍掉,等于把 build91 的缺陷倒着重做一遍。

    所以它仍然按相关性切,但必须报出来并写明未经实质度判定。
    """
    assert material_gate.classify(_VAGUE)[0] != material_gate.SUBSTANTIVE, \
        "这个用例要求标题单独看是不实质的，否则证明不了什么"
    n = N(_VAGUE, 1, _BODY)
    assert material_gate.classify(unit_a.basis_text(n))[0] \
        == material_gate.SUBSTANTIVE, "补上正文后必须变成实质，否则用例失效"


def t_pool_cut_is_reported():
    """池上限砍掉多少必须出现在进料行上，并写明**未经实质度判定**。"""
    import ast
    base = {"raw": 107, "scored": 55, "pool": 40, "cap": 10, "kept": 10,
            "dropped": 0, "dropped_substantive": 0, "enriched": 6,
            "relevant": 58, "tiers_before_cap": {"实质": 2}, "pool_limit": 40}
    cut = unit_a.intake_note({"intake": dict(base, pool_cut=18)})
    assert "池上限 40" in cut and "18 条" in cut, cut
    assert "未经实质度判定" in cut, cut
    none = unit_a.intake_note({"intake": dict(base, relevant=13, pool_cut=0)})
    assert "池上限" not in none, none
    # 契约里真的有这两个键（查字典键，不是查源码里有没有这串字符）
    _src, tree = _collect_tree()
    keys: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict) \
                and any(getattr(t, "id", "") == "intake" for t in node.targets):
            keys = {k.value for k in node.value.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    assert {"pool_cut", "pool_limit"} <= keys, keys


def t_symbol_disambiguation_drops_are_counted_and_named():
    """**消歧砍掉了什么，必须能被看见。**

    build97 上线后 ARM 的相关材料从 26 条掉到 9 条 —— 我预期挡掉 4 条噪音，
    实际挡掉 17 条。而进料行上写的是"相关 9"，读起来和
    "今天这只票没什么新闻"**一模一样**。

    相关性闸此前是完全的盲区:截断至少有 dropped/dropped_substantive 报着,
    这一步丢掉的东西不会出现在任何输出里。
    """
    from cio import topic
    zx = _Src("Zacks", "https://zacks.com/x")
    info = topic.parse_subject("ARM")
    junk = ["Current ARM mortgage rates report for Aug. 31, 2026",
            "Multiple crews battle 2-alarm fire at small business in Glen Arm",
            "Mom Who Had Arm Amputated After Shark Attack Shuts Down GoFundMe"]
    real = "Arm Holdings (ARM) Shifts Strategy to Sell Own Data Center Chips"
    off = "Bitcoin miners rally as ETF inflows accelerate"
    drops: dict = {}
    kept = unit_a._prefilter([_NS(t, zx) for t in junk + [real, off]],
                             info, drops)
    assert len(kept) == 1, [n.title_original for n in kept]
    assert len(drops.get(unit_a.DROP_SYMBOL) or []) == 3, drops
    # **裸符号根本没出现的，不算在消歧头上。** 把普通的"顺带提一句"
    # 也记成消歧，这个数就没法用来判断消歧砍得对不对。
    assert off not in (drops.get(unit_a.DROP_SYMBOL) or []), drops
    # 丢掉的标题要留下来，计数判断不了砍得对不对，那必须看标题。
    assert junk[0] in drops[unit_a.DROP_SYMBOL]
    # 不传 drops 也要能跑（老调用方）。
    assert len(unit_a._prefilter([_NS(real, zx)], info)) == 1


def t_intake_note_reports_symbol_drops_and_warns_when_large():
    """进料行要报消歧丢弃数；**丢掉的比留下的还多时主动喊一声**。"""
    base = {"raw": 106, "scored": 54, "pool": 40, "relevant": 9, "cap": 10,
            "kept": 9, "dropped": 0, "dropped_substantive": 0, "enriched": 6,
            "tiers_before_cap": {"实质": 1}}
    loud = unit_a.intake_note({"intake": dict(base, dropped_symbol=17)})
    assert "符号消歧丢弃 17 条" in loud, loud
    assert "65%" in loud and "⚠" in loud, loud
    assert "--verbose" in loud, "没告诉她去哪看被丢掉的标题"
    mild = unit_a.intake_note({"intake": dict(base, relevant=20,
                                              dropped_symbol=3)})
    assert "符号消歧丢弃 3 条" in mild and "⚠" not in mild, mild
    none = unit_a.intake_note({"intake": dict(base, dropped_symbol=0)})
    assert "符号消歧" not in none, none


def t_collect_materials_puts_symbol_drops_into_intake():
    """**光有函数不接线等于没改。** 这个仓库出过多次：规则改了、
    进料行没接上，于是新数据一个字都不会出现在输出里。

    不联网，所以查的是 `collect_materials` 里确实把 `drops` 传给了
    `_prefilter`、并把结果写进了 intake 契约。
    """
    import ast
    import inspect
    src = inspect.getsource(unit_a.collect_materials)
    tree = ast.parse(src.lstrip())
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and getattr(n.func, "id", "") == "_prefilter"]
    assert calls, "collect_materials 里没有调 _prefilter？"
    assert any(len(c.args) >= 3 for c in calls), \
        "_prefilter 没有把 drops 传进去 —— 丢弃原因收集不到"
    # **查真实的字典键，不是源码里有没有这串字符。**
    # `"dropped_symbol" in src` 会被 `"dropped_symbol_titles"` 满足，
    # 于是键改了名探针照样绿 —— 本仓库第七次踩「断言文本」这个坑。
    keys: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict) \
                and any(getattr(t, "id", "") == "intake" for t in node.targets):
            keys = {k.value for k in node.value.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    missing = {"dropped_symbol", "dropped_symbol_titles", "dropped_by"} - keys
    assert not missing, f"intake 契约里缺键：{missing}"


def t_headline_present_tense_is_a_completed_action():
    """**新闻标题用一般现在时表示已经发生的事。**

    真机 8/31：3 只票全 INSUFFICIENT,而当天 ARM 转型自研数据中心芯片、
    AMD 沙特系统上线都在材料里,全判成「背景·相关报道,无可核对的增量事实」。
    原来的注释写着"只收 reported 不收 report",但那条针对的是助动词结构
    （"is set to report" 是日程）——裸的标题现在时不是日程。
    """
    for t in ("AMD, Cisco and HUMAIN Expand Saudi Arabia's AI Infrastructure "
              "as AMD Instinct Systems Go Live",
              "Arm Holdings (ARM) Shifts Strategy to Sell Own Data Center Chips",
              "AMD and Cisco Expand AI Infrastructure in Saudi Arabia"):
        tier, why = material_gate.classify(t)
        assert tier == material_gate.SUBSTANTIVE, (t[:50], tier, why)
    # **不定式与情态动词必须挡住** —— 裸词形和不定式同形，这是放宽的全部风险。
    for t in ("Nvidia is set to expand capacity next quarter",
              "Analysts expect AMD to win more data center share",
              "AMD could acquire a networking vendor this year",
              "Arm will launch a server chip in 2027"):
        tier, why = material_gate.classify(t)
        assert tier != material_gate.SUBSTANTIVE, (t[:50], tier, why)
    # 进行时也不是完成时。
    assert material_gate.classify(
        "AMD's Saudi AI Bet Is Scaling Toward 1 Gigawatt")[0] \
        != material_gate.SUBSTANTIVE


def t_relevance_filter_still_drops_unrelated_news():
    """普通新闻源照旧要过相关性闸 —— 那条规则本身没被削弱。"""
    from cio import topic
    zx = _Src("Zacks", "https://zacks.com/x")
    info = topic.parse_subject("AMD")
    kept = unit_a._prefilter(
        [_NS("Intel earnings preview: what to expect", zx)], info)
    assert kept == [], "无关新闻不该过闸"


def t_intake_note_shows_filing_survival():
    """公告"取了几条 / 过闸几条 / 进闸门几条"必须分开报。

    只报"取了 8 条"的话，它们被全部丢掉也看不出来。
    """
    base = {"raw": 73, "scored": 47, "pool": 40, "relevant": 17, "cap": 10,
            "kept": 10, "dropped": 7, "dropped_substantive": 0, "enriched": 1,
            "tiers_before_cap": {"背景": 15, "实质": 1}}
    ok = unit_a.intake_note({"intake": dict(base, edgar=8, edgar_kept=8,
                                            edgar_in_gate=5)})
    dead = unit_a.intake_note({"intake": dict(base, edgar=8, edgar_kept=0,
                                              edgar_in_gate=0)})
    assert "EDGAR 一手披露 8 条" in ok and "进闸门 5" in ok, ok
    assert "⚠" in dead and "全部未通过相关性闸" in dead, dead


def t_edgar_window_filters_old_filings():
    """**一部只收窗口内提交的公告。**

    SEC 那个接口返回的是"最近 8 份",**和提交日期无关**——一家公司只要
    历史上提交过 8 份文件,就永远能取到 8 份。不加窗口的话,闸门每天对
    每只票都判"材料充分",evidence-triggered 的研究退化成每日评论台,
    而这正是闸门当初要防的东西。

    真机上接入 EDGAR 当天 10 只票全部变 SUFFICIENT、实质材料 4% → 57%——
    **看起来像大成功,其实是闸门被拆了。**
    """
    import datetime
    from cio import collect
    today = datetime.date.today()
    fresh = (today - datetime.timedelta(days=2)).isoformat()
    stale = (today - datetime.timedelta(days=90)).isoformat()
    payload = {"name": "TESTCO", "filings": {"recent": {
        "form": ["8-K", "10-Q", "8-K"],
        "filingDate": [fresh, stale, stale],
        "accessionNumber": ["0001-26-000001", "0001-26-000002", "0001-26-000003"],
        "primaryDocument": ["a.htm", "b.htm", "c.htm"]}}}

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return payload
    real = collect.httpx.get
    try:
        collect.httpx.get = lambda *a, **k: _Resp()
        st_win, st_all = {}, {}
        win = collect.fetch_edgar_recent("0000000001", st_win, with_body=False,
                                         within_days=7)
        allf = collect.fetch_edgar_recent("0000000001", st_all, with_body=False)
    finally:
        collect.httpx.get = real
    assert len(win) == 1, f"7 天窗口应只留 1 份,实际 {len(win)}"
    assert len(allf) == 3, f"不带窗口应保留全部 3 份(dossier 用),实际 {len(allf)}"
    assert "滤掉 2 份更早的" in st_win["EDGAR"], st_win["EDGAR"]


def t_edgar_window_default_preserves_other_callers():
    """默认 `within_days=0`（不筛）—— dossier / topic 要的是公司近况全貌,
    不能因为一部的需求把它们的行为一起改了。"""
    import inspect
    from cio import collect, unit_a
    assert inspect.signature(
        collect.fetch_edgar_recent).parameters["within_days"].default == 0
    assert unit_a.EDGAR_WINDOW_DAYS >= 3


def t_intake_note_reports_truncation():
    """截断必须看得见：说清楚采集多少、进闸门多少、截掉多少。"""
    c = {"intake": {"raw": 60, "scored": 42, "pool": 40, "relevant": 25,
                    "cap": 10, "kept": 10, "dropped": 15,
                    "dropped_substantive": 0,
                    "tiers_before_cap": {"实质": 2, "背景": 8, "无实质": 15}}}
    s = unit_a.intake_note(c)
    assert "25" in s and "10" in s and "截掉 15" in s, s
    assert "实质材料" not in s or "被截掉的里面有" not in s


def t_intake_note_flags_dropped_substantive():
    """**万一实质材料真被截掉，必须大声说。** 正常不该发生（实质优先入选），
    但守着这条才能在规则回退时立刻发现。"""
    c = {"intake": {"raw": 60, "scored": 42, "pool": 40, "relevant": 25,
                    "cap": 10, "kept": 10, "dropped": 15,
                    "dropped_substantive": 3,
                    "tiers_before_cap": {"实质": 13, "背景": 8, "无实质": 4}}}
    s = unit_a.intake_note(c)
    assert "3 条实质材料" in s and "⚠" in s, s


def t_intake_note_empty_when_no_data():
    assert unit_a.intake_note({}) == ""
    assert unit_a.intake_note({"intake": {}}) == ""


def t_pool_is_bigger_than_cap():
    """候选池必须明显大于最终条数，否则排序无从选起。"""
    assert unit_a.MATERIAL_POOL > unit_a.MATERIAL_CAP * 2, \
        (unit_a.MATERIAL_POOL, unit_a.MATERIAL_CAP)


def t_network_guard_is_armed():
    assert _no_network.BLOCKED, "断网闸没装上（CIO_TEST_ALLOW_NET 开着？）"
    import socket
    try:
        socket.create_connection(("example.com", 80), timeout=1)
    except _no_network.NetworkUsedInTest:
        return
    except Exception as e:                                     # noqa: BLE001
        raise AssertionError(f"被拦住了但抛的不是 NetworkUsedInTest：{type(e).__name__}")
    raise AssertionError("测试居然连上了外网 —— 断网闸没起作用")


def t_us_mode_drops_the_cn_bucket():
    """**美股盘前不收中国桶。**

    2026-09-02 那份美股简报的 Watch Today 里混进了「浙江宁波…」
    和几条 A 股外资流入，共同社与财新还各失败一次——因为 `sources()`
    把整份 yaml 原样返回，六个中国源和三条中国关键词照常抓。

    这不是崩溃，是**稀释**：十条 Watch Today 里占掉两条，
    当天真正该看的美股条目就少两条。
    """
    import importlib
    from cio import config as cfgmod
    old = cfgmod.MARKET
    try:
        cfgmod.MARKET = "us"
        cfgmod.sources.cache_clear()
        c = cfgmod.sources()
        names = [f["name"] for f in c["rss"]]
        assert not any(f.get("bucket") == "cn" for f in c["rss"]), names
        assert not any(q.get("bucket") == "cn"
                       for q in c["google_news"]["standing_queries"])
        assert "Caixin Global" not in names and "东方财富-要闻" not in names
        assert "BBC World" in names and "CNBC Markets" in names, "world 桶被误伤"
        # 关掉 cn 之后关键词不能整段变空 —— 那是一个静默失效的采集通道
        assert c["google_news"]["standing_queries"], "standing_queries 空了"
        # cn 模式反过来
        cfgmod.MARKET = "cn"
        cfgmod.sources.cache_clear()
        c2 = cfgmod.sources()
        n2 = [f["name"] for f in c2["rss"]]
        assert "Caixin Global" in n2 and "BBC World" in n2
        assert not any(q.get("bucket") == "us"
                       for q in c2["google_news"]["standing_queries"])
    finally:
        cfgmod.MARKET = old
        cfgmod.sources.cache_clear()
        importlib.reload(cfgmod)


def t_filtering_is_visible_not_silent():
    """**过滤掉了什么必须写进采集状态。**

    看不见的过滤和没有过滤长得一模一样：少了六个源之后，
    "今天中国没新闻"和"今天根本没抓中国"在报告上没有任何区别。
    这正是这个项目一整条主线在防的形状。
    """
    import ast
    import importlib
    from cio import config as cfgmod
    old = cfgmod.MARKET
    try:
        cfgmod.MARKET = "us"
        cfgmod.sources.cache_clear()
        bf = cfgmod.sources().get("_bucket_filter")
        assert bf, "sources() 没有记录过滤了什么"
        assert len(bf["dropped_rss"]) == 6 and len(bf["dropped_queries"]) == 3, bf
    finally:
        cfgmod.MARKET = old
        cfgmod.sources.cache_clear()
        importlib.reload(cfgmod)
    # collect_premarket 必须把它抄进 status
    src = (Path(__file__).resolve().parents[1] / "src" / "cio" / "collect.py").read_text("utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "collect_premarket")
    body = ast.get_source_segment(src, fn) or ""
    assert "_bucket_filter" in body and "源过滤" in body, \
        "collect_premarket 没有把过滤结果写进采集状态"


def t_topic_research_still_sees_every_bucket():
    """**专题研究不按市场过滤。**

    CEO 点名要一份中国主题的报告，系统却静默摘掉中文源——
    和上面那条是同一类缺陷，只是方向相反。
    """
    import ast
    src = (Path(__file__).resolve().parents[1] / "src" / "cio" / "collect.py").read_text("utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "scan_rss_for_subject")
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
             and getattr(n.func, "id", "") == "sources"]
    assert calls and any(k.arg == "all_buckets" and k.value.value is True
                         for c in calls for k in c.keywords), \
        "专题研究用的是过滤过的源"


TESTS = [
    ("**实质材料排最后也要进前 10**", t_substantive_survives_truncation),
    ("对照：旧的纯相关性排序会把它丢掉", t_old_order_would_have_dropped_it),
    ("同档之内仍按相关性排", t_relevance_still_breaks_ties),
    ("**排序用的是闸门自己的分类器**", t_ranking_uses_the_gate_classifier),
    ("**判定依据含正文（标题不够时靠它）**", t_basis_text_includes_body),
    ("闸门判的是源头文本，不是模型摘要", t_gate_reads_basis_not_display_text),
    ("**排序与闸门不可能给出不同判定**", t_ranker_and_gate_cannot_disagree),
    ("没有 basis_text 时回退到 text", t_basis_of_falls_back_to_text),
    ("补正文名额大于最终条数", t_enrich_budget_is_before_the_cut),
    ("空输入不炸", t_no_crash_on_empty),
    ("**取到正文的 8-K 算实质**", t_primary_source_with_body_is_substantive),
    ("**取不到正文的公告只算背景**", t_primary_source_without_body_is_not_substantive),
    ("**占位串会骗过纯文本规则,来源守卫拦住它**",
     t_our_own_placeholder_would_fool_the_text_rule),
    ("**短 ticker 的子串误匹配（arms / pharma / alarm）**",
     t_short_ticker_substring_false_matches),
    ("中文别名仍走子串", t_cjk_alias_still_uses_substring),
    ("**Form 4 / 144 不触发闸门**", t_ownership_forms_do_not_trigger_the_gate),
    ("**标题自报是评论文时,正文顶不上来**", t_title_veto_blocks_commentary_with_factual_body),
    ("否决没吃掉「前瞻词 + 真事件」", t_title_veto_keeps_the_documented_exception),
    ("**股价动了不参与否决(原因可能是真的)**", t_price_move_does_not_veto_a_real_cause),
    ("软标记是显式名单,不靠列表顺序", t_soft_markers_are_declared_not_inferred),
    ("**数字之间的 vs 是业绩事实,不是对比文**", t_vs_between_numbers_is_not_a_comparison_piece),
    ("**整份回归语料(改规则必须整份重跑)**", t_regression_corpus),
    ("语料的期望值不是照抄当前行为", t_corpus_expectations_are_not_a_copy_of_behaviour),
    ("表单号解析", t_filing_form_parsing),
    ("一手披露的识别", t_primary_detection),
    ("**按 CIK 取回的公告不过相关性闸**", t_filings_bypass_the_relevance_filter),
    ("**与英文词撞车的 ticker 要身份形态**",
     t_dictionary_word_ticker_needs_an_identity_form),
    ("相关性闸真的接上了 symbol_hit", t_prefilter_uses_symbol_hit_for_the_bare_ticker),
    ("**闸门数事件不数文章（转载不顶开闸门）**", t_gate_counts_events_not_articles),
    ("同一公司的两件不同的事不被误并", t_different_events_are_not_merged),
    ("事件指纹只看标题、去源名后缀", t_event_key_ignores_source_suffix_and_body),
    ("**破折号前的事实不该被后半句的钩子杀掉**",
     t_fact_clause_survives_the_hook_clause),
    ("救援只在多分句、且分句自身干净时生效",
     t_fact_clause_needs_a_real_separator_and_a_clean_clause),
    ("**持股申报不该占掉闸门名额**", t_ownership_filings_do_not_hog_gate_slots),
    ("只有持股申报沉底，8-K 不受影响", t_only_categorically_pinned_materials_sink),
    ("**四个丢弃原因全要印**", t_every_drop_reason_is_printed),
    ("**清洗跑在全部去重结果上（不先砍再筛）**",
     t_prefilter_sees_every_deduped_item),
    ("池截断此时判不了实质度（正文还没取）", t_pool_cut_cannot_rank_by_substance_yet),
    ("池上限砍掉多少要报，并写明未经判定", t_pool_cut_is_reported),
    ("**消歧丢了什么必须能被看见**",
     t_symbol_disambiguation_drops_are_counted_and_named),
    ("进料行报消歧丢弃数，过半就警告",
     t_intake_note_reports_symbol_drops_and_warns_when_large),
    ("消歧数据真的接进了 intake 契约",
     t_collect_materials_puts_symbol_drops_into_intake),
    ("**标题现在时算已发生动作**", t_headline_present_tense_is_a_completed_action),
    ("普通新闻照旧要过相关性闸", t_relevance_filter_still_drops_unrelated_news),
    ("公告的存活数分开报", t_intake_note_shows_filing_survival),
    ("**一部只收窗口内的公告(否则闸门被拆)**", t_edgar_window_filters_old_filings),
    ("默认不筛,保住 dossier/topic 的行为", t_edgar_window_default_preserves_other_callers),
    ("普通新闻源不因来源被升降级", t_news_sources_are_not_upgraded),
    ("一手披露上排序与闸门也一致", t_ranker_and_gate_agree_on_filings),
    ("截断看得见", t_intake_note_reports_truncation),
    ("实质材料被截掉要大声说", t_intake_note_flags_dropped_substantive),
    ("没有进料数据时不硬编一行", t_intake_note_empty_when_no_data),
    ("候选池明显大于最终条数", t_pool_is_bigger_than_cap),
    ("断网闸真的装上了", t_network_guard_is_armed),
    ("**美股模式不收 cn 桶**", t_us_mode_drops_the_cn_bucket),
    ("**过滤掉了什么必须印出来**", t_filtering_is_visible_not_silent),
    ("专题研究仍看得到全部桶", t_topic_research_still_sees_every_bucket),
]

print("=" * 72)
print("进料自测：实质度在截断之前判 + 测试断网闸")
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
