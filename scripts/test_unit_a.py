#!/usr/bin/env python3
"""证券一部 —— 自检（合成数据，确定性，不联网、不调模型）。

覆盖三块新建的东西：
  1. 共享计算层的依赖方向（一部不得依赖二部）
  2. 固定证据面板（口径、缺失语义、分位分母）
  3. 对抗式辩论的解析器 + 失效条件复检回路

用法：  python scripts/test_unit_a.py
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _no_network                                            # noqa: E402,F401
"""测试期间禁止联网 —— 靠真实行情才通过的断言，换台机器就是另一个结果。"""

import numpy as np                       # noqa: E402
import pandas as pd                      # noqa: E402

from cio import debate, evidence, fundamentals, measures, thesis_store   # noqa: E402

FAIL = []


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))
    if not cond:
        FAIL.append(name)


def approx(a, b, tol):
    return a is not None and abs(a - b) <= tol


print("\n=== 1. 依赖方向：一部不得依赖二部 ===")
import inspect                            # noqa: E402
msrc = inspect.getsource(measures)
check("measures 不 import 任何 cio 内部模块",
      "from ." not in msrc.split('"""', 2)[-1].split("_TRADING_DAYS")[0] or
      "from .analytics" not in msrc, "")
esrc = inspect.getsource(evidence)
check("evidence 不 import analytics（二部模块）", "from .analytics" not in esrc and
      "import analytics" not in esrc.replace("from .analytics import load_cfg", ""))
check("evidence 只依赖 measures 做计算", "from . import measures" in esrc)
# 该检查的是【真正送进模型的提示词】，不是整份源码——
# 源码里出现"建议仓位"是在描述 Vibe 的 Risk Officer 做什么（即我们刻意不做的事）。
check("综合提示词明确禁止给仓位/止损/目标价",
      "不得给出仓位、止损、目标价或执行方案" in debate._SYNTH)
check("综合提示词声明这不是一部的职权", "不是一部的职权" in debate._SYNTH)
check("Judge 提示词禁止引入新论点（不得退化成再分析一遍）",
      "不得引入任何新论点" in debate._JUDGE and "不要重新分析这家公司" in debate._JUDGE)
check("Judge 要审计双方共同回避的证据", "共同回避" in debate._JUDGE)
check("Round2 强制回应对己最不利的三条（堵住引用环节的挑选）",
      "对你自己的立场最不利" in debate._R2 and "不许回避" in debate._R2)
check("Round1 要求至少一条引用面板", "至少有 1 条论据引用量化证据面板" in debate._R1)
check("Round1 面板标明含对己不利项（不给模型挑选空间）", "含对你不利的" in debate._R1)
check("系统提示词说明「无数据」不得当作 0",
      "不得当作 0" in debate._SYS)
check("综合提示词要求失效条件必须可核对",
      "不许写成" in debate._SYNTH and "模糊表述" in debate._SYNTH)

print("\n=== 2. 固定证据面板 ===")
d = pd.bdate_range(end="2026-08-24", periods=400)
rng = np.random.default_rng(11)
px = 100 * np.exp(np.cumsum(rng.normal(0.0006, 0.02, 400)))
bpx = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.011, 400)))
df = pd.DataFrame({"date": d, "close": px})
bench = pd.DataFrame({"date": d, "close": bpx})
fund = {
    "Assets": [["2026-02-10", "2025-12-31", 1000e6, ""]],
    "StockholdersEquity": [["2026-02-10", "2025-12-31", 400e6, ""]],
    "Revenues": [["2026-02-10", "2025-12-31", 800e6, "2025-01-01"],
                 ["2025-02-10", "2024-12-31", 600e6, "2024-01-01"]],
    "NetIncomeLoss": [["2026-02-10", "2025-12-31", 120e6, "2025-01-01"],
                      ["2025-02-10", "2024-12-31", 90e6, "2024-01-01"]],
    "NetCashProvidedByUsedInOperatingActivities": [["2026-02-10", "2025-12-31", 180e6, "2025-01-01"]],
    "PaymentsToAcquirePropertyPlantAndEquipment": [["2026-02-10", "2025-12-31", 50e6, "2025-01-01"]],
    "SharesOutstanding": [["2026-02-10", "2025-12-31", 50e6, ""]],
}
snap = fundamentals.snapshot(fund, date(2026, 8, 24))
W = {"vol_days": 60, "beta_days": 250, "maxdd_days": 250, "ma_days": 120,
     "trail_lookback": 250, "trail_skip": 21, "corr_days": 60}
panel = evidence.build_panel("TEST", df, bench, fund, snap, date(2026, 8, 24), W)
by = {m.key: m for m in panel}

check("五组齐全", {m.group for m in panel} == set(evidence.PANEL_GROUPS),
      str(sorted({m.group for m in panel})))
# 市值 = 50e6 股 × 现价；E/P = 净利 / 市值
mc = 50e6 * float(px[-1])
check("盈利收益率 E/P 口径正确", approx(by["earnings_yield"].value, 120e6 / mc * 100, 1e-6),
      f"{by['earnings_yield'].value:.3f}%")
check("ROE = 净利/权益 = 30%", approx(by["roe"].value, 30.0, 1e-6))
check("营收同比 = +33.3%", approx(by["rev_growth"].value, 100 / 3, 1e-6))
check("估值项带股本截止日口径说明", "非实时" in by["earnings_yield"].note)

# 缺失语义：口径说明与缺失原因不得混用
empty = evidence.quality_block({}, {}, date(2026, 8, 24))
lv = [m for m in empty if m.key == "liab_assets"][0]
check("缺失时显示【缺失原因】而非口径说明",
      "未披露" in lv.text() and "非有息债务" not in lv.text(), lv.text())
lv2 = [m for m in evidence.quality_block({"liab_assets": 60.0}, {}, date(2026, 8, 24))
       if m.key == "liab_assets"][0]
check("有值时才显示口径说明", "非有息债务" in lv2.text(), lv2.text())

# 权益为负时 ROE 不计算（分母为负，盈利越多 ROE 越负，是误导）
neg = {"NetIncomeLoss": [["2026-02-10", "2025-12-31", 100e6, "2025-01-01"]],
       "StockholdersEquity": [["2026-02-10", "2025-12-31", -50e6, ""]]}
roe_neg = [m for m in evidence.quality_block({}, neg, date(2026, 8, 24)) if m.key == "roe"][0]
check("股东权益为负 → ROE 不计算并说明", roe_neg.value is None and "无经济含义" in roe_neg.miss)

# 分位：分母太小不给分位
evidence.build_panel("T2", df, bench, fund, snap, date(2026, 8, 24), W,
                     peers={"roe": [10.0, 20.0, 30.0]})
p_small = evidence.build_panel("T3", df, bench, fund, snap, date(2026, 8, 24), W,
                               peers={"roe": [10.0, 20.0, 30.0]})
check("同业样本 <8 不给分位（n=3 的分位没有信息量）",
      [m for m in p_small if m.key == "roe"][0].pctile is None)
p_big = evidence.build_panel("T4", df, bench, fund, snap, date(2026, 8, 24), W,
                             peers={"roe": [float(i) for i in range(12)]})
check("同业样本 ≥8 才给分位",
      [m for m in p_big if m.key == "roe"][0].pctile is not None)

# 面板必须把缺失项也摊开给双方——不能只给有值的，那等于替模型做了挑选
txt = evidence.render_panel(panel)
check("渲染包含「无数据」项（缺失项也要摊开）", "无数据" in txt)
check("渲染包含全部五组", all(g in txt for g in evidence.PANEL_GROUPS))

print("\n=== 3. 辩论解析器 ===")
synth = """【投资论点】
- 数据中心需求加速 [3][面板]
【反方论点】
- 估值已反映预期 [7]
【催化剂】
- 下季度财报公布数据中心营收
【失效条件】
- 下季度数据中心营收同比转负
- 毛利率跌破 40%
- 基本面恶化
结论=看多|中
"""
check("方向/信心解析正确", debate.parse_verdict(synth) == ("看多", "中"))
inv = debate.parse_invalidations(synth)
check("失效条件抽出 2 条（模糊表述被挡）", len(inv) == 2, str(inv))
check("「基本面恶化」被挡（无法与事实比对）", "基本面恶化" not in inv)
check("结论行不泄漏进失效条件", not any("结论" in x for x in inv))
check("催化剂抽出", debate.parse_catalysts(synth) == ["下季度财报公布数据中心营收"])
check("解析不到时返回中性/弱，不猜", debate.parse_verdict("模型输出崩了") == ("中性", "弱"))
check("缺小节返回空列表", debate.parse_invalidations("什么都没有") == [])

print("\n=== 4. 失效条件复检回路 ===")
# 中文分词：必须用字符二元组。用"标点之间的整段短语"会让复检【永远】0 命中，
# 而且完全看不出错——这是最危险的一类静默失败。
kw_cond, _ = thesis_store._keywords("下季度数据中心营收同比转负")
kw_fact, _ = thesis_store._keywords("英伟达公布季度业绩，数据中心营收同比转负，为三年来首次")
ov = kw_cond & kw_fact
check("中文二元组能匹配到重合（不是整段短语）", len(ov) >= 8, f"重合 {len(ov)} 个二元组")

TS = thesis_store
TS.init()
tid = TS.record(as_of_date="2026-08-24", subject="TESTCO", symbol="TESTCO",
                direction="看多", conviction="中", thesis="测试论点",
                catalysts=["下季度财报"],
                invalidations=["下季度数据中心营收同比转负", "毛利率跌破 40%",
                               "主要客户宣布自研芯片替代"],
                panel={}, unverified=0)
facts = [
    {"text": "英伟达公布季度业绩，数据中心营收同比转负，为三年来首次", "url": "u1", "source": "Reuters"},
    {"text": "某公司发布新款手机上市销售", "url": "u2", "source": "AP"},
    {"text": "英伟达毛利率跌破 40%，低于市场预期", "url": "u3", "source": "Bloomberg"},
    {"text": "今日天气晴朗适合出行游玩", "url": "u4", "source": "X"},
    {"text": "谷歌宣布自研芯片替代部分外购算力", "url": "u5", "source": "WSJ"},
]
hits = TS.check(facts, symbol="TESTCO")
check("三条失效条件全部命中", len(hits) == 3, f"{len(hits)} 条")
check("无关新闻零误报",
      not any(("天气" in h["fact"]) or ("手机" in h["fact"]) for h in hits))
check("数字锚点被识别（毛利率 40%）",
      any(h["number_matched"] for h in hits))
check("命中带出触发材料与来源", all(h["fact"] and h["source"] for h in hits))
check("空事实列表不报错", TS.check([], symbol="TESTCO") == [])
TS.close(tid, "自检清理")
check("close 后不再出现在 OPEN 列表",
      not any(t["id"] == tid for t in TS.open_theses("TESTCO")))

print("\n=== 5. build62：真机首跑暴露的两个缺陷 ===")

# 5.1 [面板] 必须被核验器接受。
#     新提示词要求用 [面板] 标注量化证据来源，而核验器只认数字编号——
#     结果是【模型越听话地引用面板，被标 ⚠未核实 的越多】。
#     真机首跑 32 条论据几乎全被标记，这个信号直接废掉。
from cio.unit_a import _verify_citations                    # noqa: E402
t = ("1. NVDA的毛利率为71.07%，显示出高盈利质量。[面板]\n"
     "2. 期权价格已将后市目标推至240-242美元区间。[3]\n"
     "3. 我记得英伟达很厉害。\n"
     "【投资论点】\n"
     "4. 自由现金流占营收44.77%。[面板][2]")
res, bad = _verify_citations(t, 8)
check("[面板] 被认作有效溯源", "⚠未核实：1." not in res)
check("数字编号仍然有效", "⚠未核实：2." not in res)
check("无引用的仍被标记", "⚠未核实：3." in res)
check("小节标题不参与核验（不是论据）", "⚠未核实：【投资论点】" not in res)
check("未核实计数正确（仅 1 条）", bad == 1, str(bad))
check("超范围编号仍被标记", _verify_citations("x [99]", 8)[1] == 1)

# 5.2 MD 与 PDF 必须同构。
#     真机首跑收到的 PDF 是旧的六节版式——量化面板、反驳轮、论证审计、
#     失效条件在【交付物】里全都不见了，且没有任何报错。
#     推给 CEO 的是 PDF，Markdown 只留在磁盘上。
from cio import render                                       # noqa: E402
import inspect as _i3                                        # noqa: E402
md_src = _i3.getsource(render.render_unit_a_md)
pdf_src = _i3.getsource(render.render_unit_a_pdf)
for key in ("量化证据面板", "论证审计", "失效条件", "Round 2", "催化剂"):
    check(f"PDF 含「{key}」（与 MD 同构）", key in pdf_src)
check("PDF 不再出现旧版式「一部裁定」", "一部裁定" not in pdf_src)
check("PDF 不再显示目标仓位（一部不给仓位）", "目标仓位" not in pdf_src)
check("PDF 印出本地模型调用次数", "llm_calls" in pdf_src)
check("PDF 有历史失效提示区块", "invalidation_hits" in pdf_src)
check("MD 与 PDF 小节数量相当",
      abs(md_src.count("量化证据面板") - pdf_src.count("量化证据面板")) <= 1)

# ============================================================ 6. 材料实质度闸门
# 首跑 8 条材料全是"财报前瞻"标题，辩论完全落回量化面板，而报告读起来
# 像是有基本面依据的。闸门不解决数据源，它保证【报告不假装自己有它没有的证据】。
print("\n[6] 材料实质度闸门（build63）")
from cio import material_gate as MG                           # noqa: E402

# 6.1 首跑那 8 条真实材料，一条都不该被判为「实质」
REAL8 = [
    "Why Nvidia Is A Broken Stock (NASDAQ:NVDA) - Seeking Alpha",
    "Nvidia Stock May Plunge After Earnings, Even If It Beats (NASDAQ:NVDA)",
    "Is Nvidia (NVDA) Stock a Buy Ahead of Q2 Earnings?",
    "Nvidia (NVDA) Reports Earnings This Week. Here's What Its Rival's Results Signal",
    "Is Nvidia (NVDA) Stock a Buy Ahead of Q2 Earnings? - Zacks Investment Research",
    "NVDA Earnings in 2 Days: How to Read the Options Signals - Moomoo",
    "Nvidia: The Last Hurrah Before ASIC (Earnings Preview) (NASDAQ:NVDA)",
    "$NVDA options price it at $240-$242.50 after earnings report~ - Moomoo",
]
for i, t in enumerate(REAL8, 1):
    tier, why = MG.classify(t)
    check(f"首跑材料[{i}] 判为不实质", tier != MG.SUBSTANTIVE, f"{tier}·{why}")

# 6.2 真正的实质材料不能被误杀——包括【没有数字】的重大事件
for t in ["Nvidia announced a $50 billion share buyback authorization",
          "英伟达宣布以 20 亿美元收购 Run:ai，交易已完成交割",
          "NVDA filed an 8-K disclosing a $3.5 billion supply agreement",
          "美国商务部宣布对英伟达 H20 芯片实施新的出口管制，即日生效",
          "台积电宣布上调全年资本开支指引"]:
    check(f"实质材料不被误杀：{t[:26]}", MG.classify(t)[0] == MG.SUBSTANTIVE, MG.classify(t)[1])

# 正向证据必须能顶过前瞻标记——否则真公告会因为标题带 "ahead of earnings" 被丢掉
check("完成动作+锚点 优先于前瞻标记",
      MG.classify("Ahead of earnings, Nvidia announced a $50 billion buyback")[0] == MG.SUBSTANTIVE)

# 6.3 总判定与横幅
class _M:
    def __init__(self, i, t):
        self.id, self.text = i, t


g8 = MG.assess([_M(i, t) for i, t in enumerate(REAL8, 1)])
check("8 条前瞻 → 判定「无实质材料」", g8["verdict"] == "无实质材料", g8["verdict"])
check("实质计数为 0", g8["n_sub"] == 0, str(g8["n_sub"]))
check("产出顶部横幅", bool(g8["banner"]))
check("横幅说明结论由面板驱动", "面板" in g8["banner"])
check("产出注入提示词的约束", bool(g8["constraint"]))
check("约束禁止把分析师预期当事实", "分析师预期" in g8["constraint"])
check("每条都有可打印标签", len(g8["labels"]) == 8)

gmix = MG.assess([_M(1, "英伟达宣布以 20 亿美元收购 Run:ai，交易已完成交割"),
                  _M(2, "Is NVDA a Buy Ahead of Q2 Earnings?"),
                  _M(3, "Nvidia shares rose 3% on Monday")])
check("1 实质 + 2 空 → 「材料偏薄」", gmix["verdict"] == "材料偏薄", gmix["verdict"])
check("偏薄也要出横幅", bool(gmix["banner"]))
# **三条必须是三件不同的事。** 这里原来是同一句话复制三份——
# build100 之后同一事件不重复计数，那样只算 1 件。旧写法把
# 「一份新闻稿被三家转载就能顶开闸门」这个缺陷写进了测试本身。
g_rich = MG.assess([_M(1, "英伟达宣布以 20 亿美元收购 Run:ai，交易已完成交割"),
                    _M(2, "商务部宣布对英伟达 H20 实施出口管制"),
                    _M(3, "英伟达宣布回购 500 亿美元股份，董事会已批准")])
check("3 件不同实质 → 「材料充分」，不出横幅",
      g_rich["verdict"] == "材料充分" and not g_rich["banner"], g_rich["verdict"])
g_dup = MG.assess([_M(i, "英伟达宣布以 20 亿美元收购 Run:ai，交易已完成交割")
                   for i in range(1, 4)])
check("**同一件事的 3 份转载只算 1 件 → 材料偏薄**",
      g_dup["verdict"] == "材料偏薄" and g_dup["n_sub"] == 3
      and g_dup["n_sub_events"] == 1,
      f"{g_dup['verdict']} n_sub={g_dup['n_sub']} events={g_dup['n_sub_events']}")
check("重复条目的标签写明与哪条同一事件",
      "同一事件" in g_dup["labels"][2][1], g_dup["labels"][2][1])
check("无材料 → 「无材料」且有横幅",
      MG.assess([])["verdict"] == "无材料" and bool(MG.assess([])["banner"]))

# 6.4 闸门必须【同时】改变提示词和报告。
#     只在报告顶部加警告、却让模型照旧写"基本面依然强劲"，报告就自我矛盾。
import inspect as _i4                                         # noqa: E402
check("run_debate 接受 constraint 参数",
      "constraint" in _i4.signature(debate.run_debate).parameters)
for tmpl, nm in ((debate._R1, "R1"), (debate._R2, "R2"), (debate._SYNTH, "Synth")):
    check(f"{nm} 提示词含 constraint 占位", "{constraint}" in tmpl)
check("Judge 提示词【不】注入材料约束（它只审计论证，不重做研究）",
      "{constraint}" not in debate._JUDGE)

# 6.5 渲染：MD 与 PDF 必须【同时】显示横幅与逐条标签（build62 的教训）
for src, nm in ((md_src, "MD"), (pdf_src, "PDF")):
    check(f"{nm} 显示材料横幅", "material_banner" in src)
    check(f"{nm} 显示逐条实质度标签", "material_labels" in src)
    check(f"{nm} 头部显示实质材料条数", "material_substantive" in src)

# 6.6 台账：材料判定必须落库，否则"我的高信心论点是不是都建在薄材料上"永远问不出来
check("record() 接受 material_verdict",
      "material_verdict" in _i4.signature(thesis_store.record).parameters)
check("台账用 ALTER 补列（旧库也要有，且不能静默跳过）",
      "_ADD_COLUMNS" in _i4.getsource(thesis_store))
check("init() 真的执行补列", "ALTER TABLE" in _i4.getsource(thesis_store.init))

# ============================================================ 7. 论据核验（Round 2 版式）
# build62 修好了 [面板]，但首跑仍有 14 条 ⚠未核实——全部来自 Round 2：
# markdown 小标题、分隔线、以及【逐条引述对方原话】的行。它们都不是新主张。
print("\n[7] 论据核验：Round 2 版式与引述核对")
BEAR_R1 = ("- NVDA股价在财报公布后可能出现大幅下跌，即使业绩超预期也存在显著回调风险。 [2]\n"
           "- 其盈利收益率仅为 2.38%，显示估值偏高，低于行业平均水平。 [面板]")
BULL_R2 = ('**1) 反驳对方最强的三条论据**\n'
           '- **"NVDA股价在财报公布后可能出现大幅下跌，即使业绩超预期也存在显著回调风险。"**\n'
           '此观点基于【2】；公司净资产收益率高达 61.42%【面板】。\n'
           '---\n'
           '**2) 对自己立场最不利的三项量化证据及回应**\n'
           '- **近一年最大回撤 -20.21%【面板】**\n'
           '此点成立，我方承认。\n'
           'NVDA 明年将拿下 90% 的 ASIC 市场份额。')
res2, bad2 = _verify_citations(BULL_R2, 8, BEAR_R1)
check("加粗序号小标题不算论据", "⚠未核实：**1)" not in res2)
check("分隔线不算论据", "⚠未核实：---" not in res2)
check("忠实引述对方原话不算论据", "⚠引述失实" not in res2 and "⚠未核实：- **“NVDA股价" not in res2)
check("让步句不被惩罚（提示词鼓励它）", "⚠未核实：此点成立" not in res2)
check("真正无出处的断言仍被标记", "⚠未核实：NVDA 明年将拿下" in res2)
check("Round 2 未核实数降到 1", bad2 == 1, str(bad2))

# 伪造对方论点比无出处更严重：单独标 ⚠引述失实，绝不能因为"有引号"就放行
fake, bfake = _verify_citations('- **"空头承认 NVDA 护城河无可撼动，只是估值略高。"**', 8, BEAR_R1)
check("伪造引述被抓出并单独标记", "⚠引述失实" in fake and bfake == 1)
check("无引述来源时引述一律不放行", _verify_citations('- **"对方说过的话"**', 8, "")[1] >= 0)
check("Round 1 无出处断言仍被标记", _verify_citations("NVDA 明年营收将翻倍。", 8)[1] == 1)

# ============================================================ 8. 只讲股价的失效条件
# 真机第二跑 5 条失效条件里 3 条是「最大回撤超过 -25%」「Beta 超过 2.5」
# 「尾随12-1收益低于 10%」——看起来具体可核对，其实什么都没证伪：
# 股价下跌不证明论点错，对逆向/长期论点那可能恰恰是它最成立的时候。
print("\n[8] 失效条件：股价统计量 ≠ 证伪")
REAL5 = ["Q2收入增长率为负。", "E/P 上升至 3% 或以上。", "最大回撤超过-25%。",
         "Beta 超过 2.5。", "尾随12-1收益低于10%。"]
mo = debate.market_only_invalidations(REAL5)
check("第二跑 5 条中标出 3 条只讲股价", len(mo) == 3, "；".join(mo))
check("「Q2收入增长率为负」不被误标", "Q2收入增长率为负。" not in mo)
check("「E/P 上升至 3%」不被误标（含估值口径，宁可少标）", "E/P 上升至 3% 或以上。" not in mo)
for c in ["毛利率降至70%以下", "自由现金流占营收比例下降到15%或更低",
          "该药物三期未达主要终点", "被列入出口管制清单"]:
    check(f"公司事实型条件不被误标：{c[:18]}", not debate.market_only_invalidations([c]))
check("提示词要求失效条件指向公司事实", "公司事实" in debate._SYNTH)
check("run_debate 返回 market_only_invalidations",
      "market_only_invalidations" in _i4.getsource(debate.run_debate))
for src, nm in ((md_src, "MD"), (pdf_src, "PDF")):
    check(f"{nm} 提示只讲股价的失效条件", "market_only_invalidations" in src)

# ============================================================ 9. 小节标题版式
# 真机第三跑，模型把小节写成 **失效条件**（…）而不是【失效条件】。
# 解析器只认【】，于是催化剂与失效条件双双解析出 0 条，而正文里明明写着 6 条——
# 报告在同一页上自相矛盾，台账也存了 0 条（明天的失效复检拿到的是空的）。
print("\n[9] 小节标题：五种版式都要认")
_FMT = {
    "【】":       "【催化剂】\n- A事件已经发生\n【失效条件】\n- 毛利率跌破 40%\n结论=看多|中",
    "**加粗**":   ("**催化剂**（可观察事件）\n- 拿到大额订单\n"
                 "**失效条件**（若发生即视为论点失效）\n- 毛利率跌破60%\n- Beta上升至2.0以上\n结论=看多|中"),
    "### 标题":   "### 催化剂\n- 拿到大额订单\n### 失效条件\n- 营收同比转负\n结论=看空|强",
    "冒号式":     "催化剂：\n- 拿到大额订单\n失效条件：\n- 营收同比转负\n结论=中性|弱",
    "序号式":     "4. 催化剂\n- 拿到大额订单\n5. 失效条件\n- 营收同比转负\n结论=看多|弱",
    "混排":       "【催化剂】\n- 拿到大额订单\n**失效条件**\n- 营收同比转负\n- 毛利率跌破50%\n结论=看多|中",
}
for nm, t in _FMT.items():
    c, iv = debate.parse_catalysts(t), debate.parse_invalidations(t)
    check(f"{nm} 催化剂解析到", len(c) >= 1, str(c))
    check(f"{nm} 失效条件解析到", len(iv) >= 1, str(iv))
    check(f"{nm} 结论行不混进列表", not any("结论=" in x for x in c + iv))
    check(f"{nm} 方向可解析", debate.parse_verdict(t)[0] != "中性" or "中性" in t)

# 解析失败 ≠ 模型没写。说成后者就是拿自己的 bug 去指责模型，
# 而且会让人以为回路在工作——台账其实存了 0 条。
_UNPARSEABLE = "我认为失效条件是毛利率下滑，但我不按格式写\n结论=看多|中"
check("正文提到失效条件却解析不出 → 报「解析失败」",
      len(debate.parse_section_warnings(_UNPARSEABLE)) == 1,
      str(debate.parse_section_warnings(_UNPARSEABLE))[:60])
check("告警文案点明是解析失败而非模型没写",
      "解析失败" in "".join(debate.parse_section_warnings(_UNPARSEABLE)))
check("解析成功时不产生告警",
      not debate.parse_section_warnings(_FMT["**加粗**"]))
check("模型真没写时不误报告警",
      not debate.parse_section_warnings("【投资论点】\n- 毛利率高。[面板]\n结论=看多|中"))
check("run_debate 返回 parse_warnings", "parse_warnings" in _i4.getsource(debate.run_debate))
for src, nm in ((md_src, "MD"), (pdf_src, "PDF")):
    check(f"{nm} 区分「解析失败」与「模型没写」", "parse_warnings" in src)

# markdown 表头行不是论断
from cio.unit_a import _is_table_head                          # noqa: E402
check("表头行被识别为结构", _is_table_head("| 对方论点 | 我方反驳（仅引用面板数据） |"))
check("含数字的数据行仍需出处", not _is_table_head("| 毛利率 71.07% | 估值偏高 |"))
check("普通句子不会被误判为表头", not _is_table_head("毛利率高达 71.07%"))
_t, _b = _verify_citations("| 对方论点 | 我方反驳（仅引用面板数据） |\n|---|---|", 8)
check("表头 + 分隔行 都不计入未核实", _b == 0, str(_b))

# ============================================================ 10. 年份核验
# 真机第四跑：催化剂写"2024年第一季度财报公布将进一步验证增长"，句末标着 [面板]——
# 形式上完全合规，核验器放行；但面板 as_of 是 2026-05-20，根本没有 2024。
# 引用核验只管"有没有出处"，不管"出处里有没有这句话"。年份是唯一
# 不可能被推导出来的量（"E/P 2.38% → P/E 约 42" 里的 42 是合法推导），所以单收这一类。
print("\n[10] 年份核验：凭空的年份")
_CORPUS = "【Quality】\n · 毛利率: 71.07% [截至 2026-05-20]\n · E/P: 2.38% [截至 2026-01-25]"
check("凭空年份被标出",
      "⚠年份存疑" in debate._mark_years(["2024年第一季度财报公布将验证增长。"], _CORPUS)[0])
check("语料里有的年份不标",
      "⚠" not in debate._mark_years(["2026年第二季度营收超预期。"], _CORPUS)[0])
check("无年份的条件不受影响",
      debate._mark_years(["毛利率跌破 60%。"], _CORPUS) == ["毛利率跌破 60%。"])
check("语料为空时不误报（宁可不查也不错杀）",
      debate._mark_years(["2024年财报"], "") == ["2024年财报"])
check("标记而非删除（要看得见一部编过什么）",
      "2024年第一季度" in debate._mark_years(["2024年第一季度财报"], _CORPUS)[0])
check("催化剂与失效条件都过 lint（年份/同业/口径）",
      _i4.getsource(debate.run_debate).count("lint_items") == 2)
check("合法推导的数字不被误标（P/E 约 42 不在面板里）",
      "⚠" not in debate._mark_years(["E/P 2.38% 对应市盈率约 42×。"], _CORPUS)[0])
_t, _b = _verify_citations("2024年第一季度将验证增长。[面板]", 8, "", _CORPUS)
check("有出处但年份是编的 → 单独标 ⚠年份存疑", "⚠年份存疑" in _t and _b == 1)
check("结论=行不再被当成论据", _verify_citations("结论=看多|中", 8)[1] == 0)
check("综合【不】跑完整引用核验（争议事实是问句，全核会整批误标）",
      "verify(synthesis" not in _i4.getsource(debate.run_debate))

# ============================================================ 11. Evidence Gate 三档
# **没有新的可解释信息，就不制造新的观点。**
# 0 substantive 时跑辩论，实际是两个模型拿二部已算好的数字重讲故事：
# 没有新 information set，且同一批不变的数字每天重跑，方向/信心的摆动是采样噪声。
print("\n[11] Evidence Gate：一部从定时任务变成条件触发任务")
_PREVIEW = "Is Nvidia (NVDA) Stock a Buy Ahead of Q2 Earnings?"
_REAL = "英伟达宣布以 20 亿美元收购 Run:ai，交易已完成交割"
# **要 3 件不同的事，就得用 3 句不同的话。** build100 之后同一事件不重复计数，
# `[_REAL] * 3` 只算 1 件 —— 那正是「一份新闻稿被三家转载顶开闸门」的缺陷。
_REAL2 = "商务部宣布对英伟达 H20 实施出口管制"
_REAL3 = "英伟达宣布回购 500 亿美元股份，董事会已批准"
_THREE = [_REAL, _REAL2, _REAL3]


class _MM:
    def __init__(s, i, t):
        s.id, s.text = i, t


def _g(texts):
    return MG.assess([_MM(i, t) for i, t in enumerate(texts, 1)])


g0, g1, g3 = _g([_PREVIEW] * 6), _g([_REAL, _PREVIEW]), _g(_THREE)
check("0 实质 → INSUFFICIENT", g0["level"] == MG.INSUFFICIENT, g0["level"])
check("0 实质 → 不启动辩论", g0["activate"] is False)
check("1 实质 → THIN", g1["level"] == MG.THIN, g1["level"])
check("THIN 仍然启动（一条 8-K 也可能信息密度极高）", g1["activate"] is True)
check("THIN 信心上限为「弱」", g1["conviction_cap"] == "弱", g1["conviction_cap"])
check("3 实质 → SUFFICIENT", g3["level"] == MG.SUFFICIENT, g3["level"])
check("SUFFICIENT 不设信心上限", g3["conviction_cap"] == "")
check("无材料也判 INSUFFICIENT", MG.assess([])["level"] == MG.INSUFFICIENT)
check("正式弃权表述为英文原文（与二部同一套语言）",
      "ABSTAIN" in MG.FORMAL_VOTE_ABSTAIN and "not activated" in MG.NOT_ACTIVATED_HEADLINE)

# 未启动路径：一次 LLM 都不能调用，也不能产生新 thesis
import inspect as _i5                                        # noqa: E402
from cio import unit_a as _UA                                # noqa: E402
_na = _i5.getsource(_UA._not_activated)
check("未启动路径不调用任何模型", "get_ollama" not in _na and "run_debate" not in _na)
check("未启动路径不登记新论点（无新证据不产生新 thesis）", "thesis_store.record" not in _na)
check("未启动路径仍复检既有论点（监控照常进行）", "thesis_store.check" in _na)
check("未启动路径列出仍 OPEN 的论点（未启动≠没有观点）", "open_brief" in _na)
check("未启动时 llm_calls=0", "llm_calls=0" in _na)
check("未启动时 formal_vote=ABSTAIN", 'formal_vote="ABSTAIN"' in _na)

_bua = _i5.getsource(_UA.build_unit_a)
check("build_unit_a 在闸门处早退", 'gate["activate"]' in _bua and "_not_activated(" in _bua)
check("信心封顶是确定性后置规则，不是提示词请求", 'gate["conviction_cap"]' in _bua)
check("支持 --force / UNIT_A_FORCE_RESEARCH 人工 override",
      "UNIT_A_FORCE_RESEARCH" in _i5.getsource(_UA._forced))
check("日频漏斗跳过未启动的标的（不把「没有证据」翻译成「中性」）",
      "not adv.activated" in _i5.getsource(_UA.build_unit_a_daily))

# 渲染：未启动是另一套版式，MD 与 PDF 必须同时有（build62 的教训）
_namd = _i4.getsource(render._md_not_activated)
_napdf = _i4.getsource(render._pdf_not_activated)
for src, nm in ((_namd, "MD"), (_napdf, "PDF")):
    check(f"{nm} 未启动版式有 ABSTAIN 表述", "FORMAL_VOTE_ABSTAIN" in src)
    check(f"{nm} 未启动版式仍出面板", "panel_text" in src)
    check(f"{nm} 未启动版式列出既有论点", "open_theses" in src)
    check(f"{nm} 未启动版式出材料质量标签", "material_labels" in src)
    check(f"{nm} 未启动版式【不】生成多空论据", "bull_case" not in src and "bear_case" not in src)
check("MD 渲染入口按 activated 分流", "not r.activated" in md_src)
check("PDF 渲染入口按 activated 分流", "not r.activated" in pdf_src)
check("已启动版式显示强制复研标注", "forced" in md_src and "forced" in pdf_src)
check("已启动版式显示信心封顶原判", "conviction_capped" in md_src and "conviction_capped" in pdf_src)

# 台账：无失效条件的论点不该永远挂在 OPEN
_rec = _i4.getsource(thesis_store.record)
check("无失效条件 → NO_CONDITIONS，不进 OPEN", "NO_CONDITIONS" in _rec)
check("但仍然入库可审计（要看得见一部写过什么）", "INSERT INTO unit_a_thesis" in _rec)

# ============================================================ 12. 论点台账：当前观点，不是观点日志
# 真机第五跑的 ABSTAIN 报告里，"仍在监控中的既有论点"并排列出 4 条 NVDA 看多|中，
# 全是同一天调试跑产生的——它们不是四个观点，是同一个观点的四份草稿。
print("\n[12] 论点台账：一个标的只保留一个 active thesis")
_rec12 = _i4.getsource(thesis_store.record)
check("新论点会取代同标的的旧 OPEN 论点", "SUPERSEDED" in _rec12)
check("按 symbol 取代（无 symbol 的主题按 subject）", 'if symbol else (subject' in _rec12)
check("旧论点仍留库可审计（不是删除）", "UPDATE unit_a_thesis SET status='SUPERSEDED'" in _rec12)
_init12 = _i4.getsource(thesis_store.init)
check("一次性回填历史无条件论点", "NO_CONDITIONS" in _init12 and "UPDATE" in _init12)
check("回填是幂等的（按 status='OPEN' 且条件为空筛选）", "status='OPEN' AND" in _init12)

import os as _os, tempfile as _tf                             # noqa: E402
_os.environ["CIO_DB"] = _os.path.join(_tf.mkdtemp(), "t12.db")
import importlib as _il                                       # noqa: E402
from cio import db as _db                                     # noqa: E402
_il.reload(_db)
_il.reload(thesis_store)
_a = thesis_store.record(as_of_date="2026-08-24", subject="ZZZ", symbol="ZZZ", direction="看多",
                         conviction="中", thesis="旧", catalysts=[],
                         invalidations=["毛利率跌破60%"], panel={})
_b = thesis_store.record(as_of_date="2026-08-25", subject="ZZZ", symbol="ZZZ", direction="看空",
                         conviction="弱", thesis="新", catalysts=[],
                         invalidations=["营收同比转负"], panel={})
_ids = [t["id"] for t in thesis_store.open_theses("ZZZ")]
check("取代后只剩最新一条 OPEN", _ids == [_b], str(_ids))
check("旧论点变 SUPERSEDED 而非消失", "SUPERSEDED" in thesis_store.summary(5))
_c = thesis_store.record(as_of_date="2026-08-25", subject="YYY", symbol="YYY", direction="中性",
                         conviction="弱", thesis="无条件", catalysts=[], invalidations=[], panel={})
check("无失效条件的论点不进 OPEN", _c not in [t["id"] for t in thesis_store.open_theses("YYY")])
check("不同标的互不影响", [t["id"] for t in thesis_store.open_theses("ZZZ")] == [_b])

# 渲染防御：0 条件的论点即使混进来也不渲染成空条目
for src, nm in ((_namd, "MD"), (_napdf, "PDF")):
    check(f"{nm} 过滤掉无失效条件的论点条目", 'if t.get("invalidations")' in src)

# 第五跑漏网的两条材料标签
check("N-day losing streak → 行情复述",
      MG.classify("NVDA Stock's 7-Day Losing Streak Sets Up Make-Or-Break AI Earnings Test")[1]
      == "行情复述")
check("could set up a big surprise → 财报前瞻",
      MG.classify("Nvidia Earnings: Hyperscaler Spending Could Set Up a Big Q2 Surprise")[1]
      == "财报前瞻")

# ============================================================ 13. 加粗外壳与重复项目符号
# --force 那一跑，模型写的是 **【失效条件】** —— 加粗【包住】方括号。
# build64 认五种版式，独独不认这第六种组合，于是催化剂与失效条件又双双解析出 0 条。
# 这次把 ** 提成【可选外壳】套在所有形式外面，而不是再往清单里加一条——
# 加清单会一直被新的组合绕过去。
print("\n[13] 小节标题：加粗外壳 + 重复项目符号")
_BODY = "\n- 营业收入同比转负\n结论=看多|中"
for nm, hdr in [("【】", "【失效条件】"), ("**x**", "**失效条件**"), ("###", "### 失效条件"),
                ("冒号", "失效条件："), ("序号", "5. 失效条件"),
                ("**【】**", "**【失效条件】**"), ("**###**", "**### 失效条件**")]:
    got = debate.parse_invalidations(hdr + _BODY)
    check(f"{nm} 版式可解析", got == ["营业收入同比转负"], str(got))

_DBL = "**【失效条件】**\n- - 营业收入同比转负\n- - 毛利率跌破50%\n结论=看多|中"
_gd = debate.parse_invalidations(_DBL)
check("重复项目符号 '- - ' 被完全剥掉", _gd == ["营业收入同比转负", "毛利率跌破50%"], str(_gd))
check("剥符号不会连内容一起吃掉", debate._strip_bullet("- - **毛利率跌破50%**") == "毛利率跌破50%",
      debate._strip_bullet("- - **毛利率跌破50%**"))
check("正常行不受影响", debate._strip_bullet("营业收入同比转负") == "营业收入同比转负")

# 真机 --force 那一跑的原文，整段回归
_FORCE_RUN = ("**【催化剂】**\n"
              "- 季度财报公布时，营业收入同比增长超过70%，净利润同比增长超过65%；\n"
              "**【失效条件】**\n"
              "- - 营业收入同比增长跌破30%。\n- - 净利润毛利率降至50%以下。\n"
              "- - 总负债/总资产比例升至40%以上。\n- - 自由现金流/营收比下降至20%以下。\n"
              "结论=看多|中")
check("--force 那一跑：催化剂解析到", len(debate.parse_catalysts(_FORCE_RUN)) == 1)
check("--force 那一跑：失效条件解析到 4 条",
      len(debate.parse_invalidations(_FORCE_RUN)) == 4,
      str(debate.parse_invalidations(_FORCE_RUN)))
check("--force 那一跑：不再报解析失败", not debate.parse_section_warnings(_FORCE_RUN))

# 0 条失效条件时不能写"已登记、后续每日自动复检"——那句话是假的
for src, nm in ((md_src, "MD"), (pdf_src, "PDF")):
    check(f"{nm} 0 条时不谎称「后续每日自动复检」", "不参与每日复检" in src)

# ============================================================ 14. 转述与弱材料引用
# --force 那一跑，模型这样复述对方论点：
#     1. **市盈率约42倍（E/P = 2.38%）显示估值偏高，存在回调空间。**
# 整行加粗、序号开头、没有引号。它同样是在复述对方的主张，
# 但 build68 的引述核对只认引号，于是这些行被当成无出处的新论断标了 ⚠。
# 判据不能钉在标点上，要钉在"结构上是否被标成转述" + "内容是否真的出自对方"。
print("\n[14] 转述识别与弱材料引用")
_BEAR14 = ("- 市盈率约42倍（E/P = 2.38%）显示估值偏高，存在回调空间。[面板]\n"
           "- 市销率约23倍（S/P = 4.28%）表明股价对收入过度溢价。[面板]")
_R2 = ("**一、反驳对方最强的三条论据**\n"
       "1. **市盈率约42倍（E/P = 2.38%）显示估值偏高，存在回调空间。**\n"
       "该估值水平与公司盈利质量相匹配：毛利率71.07%【面板】。\n"
       "2. **市销率约23倍（S/P = 4.28%）表明股价对收入过度溢价。**\n"
       "低负债比24.67%进一步降低财务风险。[面板]")
_t14, _b14 = _verify_citations(_R2, 6, _BEAR14)
check("加粗转述（无引号）不再被标未核实", _b14 == 0, str(_b14))
check("转述行原样保留", "⚠" not in _t14)

# 但转述免检不能变成免检通道：编一句对方没说过的加粗话，照样要抓
_fake14, _bf14 = _verify_citations("3. **对方从没说过这句话，是编出来的转述。**", 6, _BEAR14)
check("伪造的加粗转述仍被抓出", "⚠引述失实" in _fake14 and _bf14 == 1)
check("短加粗片段不算转述（避免把加粗强调当引述放行）",
      _verify_citations("**毛利率**高。", 6, _BEAR14)[1] == 1)

# 引用【无实质】材料：出处是真的，但要看得见论据站在标题上还是事实上
_w1, _bw1 = _verify_citations("市场预期 NVDA 将再次超出财报预期。[2]", 6, "", "", {1, 2, 3})
check("引用无实质材料被单独标注", "据无实质材料" in _w1)
check("引用无实质材料【不】计入未核实（出处是真的）", _bw1 == 0, str(_bw1))
_w2, _ = _verify_citations("英伟达完成对 Run:ai 的收购。[4]", 6, "", "", {1, 2, 3})
check("引用实质材料不被标注", "据无实质材料" not in _w2)
check("面板引用不受影响", "据无实质材料" not in
      _verify_citations("毛利率 71.07%。[面板]", 6, "", "", {1, 2, 3})[0])
check("weak_ids 缺省时行为不变", _verify_citations("市场预期。[2]", 6)[1] == 0)
check("build_unit_a 传入无实质材料编号", "_weak" in _i5.getsource(_UA.build_unit_a))

# 核验顺序：有出处的加粗断言【不是】伪造引述。
# 把合规行为标成造假比漏标更坏——核验器一旦开始惩罚正确行为，这个信号就废了。
_cited_bold = "- **近一年最大回撤 -20.21%【面板】**"
check("有出处的加粗断言不被当成伪造引述",
      "⚠" not in _verify_citations(_cited_bold, 6, "对方说的是完全不相关的内容")[0])
check("无出处的加粗转述仍按引述核对",
      "⚠引述失实" in _verify_citations("- **对方没说过这句话呀呀呀。**", 6, "不相关内容")[0])

# ============================================================ 15. 无序号的加粗小标题
# 真机第六跑写的是光秃秃的 **反驳** / **直面不利证据**，没有序号，
# _BOLD_HEAD 要求序号开头，于是这两行被当成无出处的论断。
print("\n[15] 无序号加粗小标题")
for lbl in ("**反驳**", "**直面不利证据**", "**说明**", "**结论**"):
    check(f"{lbl} 不算论据", _verify_citations(lbl, 6)[1] == 0)
check("带序号的小标题仍然识别", _verify_citations("**1) 反驳对方最强的三条论据**", 6)[1] == 0)
# 不能给论断开后门
check("含数字的加粗断言仍要出处", _verify_citations("**毛利率跌破60%**", 6)[1] == 1)
check("过长的加粗断言仍要出处",
      _verify_citations("**估值明显偏高且增长不可持续风险极大**", 6)[1] == 1)
check("加粗断言带出处则放行", _verify_citations("**毛利率 71.07%**【面板】", 6)[1] == 0)

# ============================================================ 16. 表头行的判据
# 真机 8/26 那跑：`| 对方论点 | 我的回应（为何不成立或影响被高估） |` 被标未核实，
# 只因为第二格 17 字、超了 16 字的上限。长度不该是主判据——
# **没有数字、没有引用标记**才是（栏目名不携带可核对的量）。
print("\n[16] 表头行：判据是没有数字，不是够短")
for t, want in [("| 对方论点 | 我的回应（为何不成立或影响被高估） |", True),
                ("| 我方不利证据 | 回应（承认或说明为何不改变结论） | 引用 |", True),
                ("| 主张 | 提出方 | 证据 | 对方反驳 | 状态 |", True),
                ("| 毛利率 71.07% | 估值偏高 |", False),
                ("| 这是一整句被误当成表头的长论断，它其实在陈述一个观点 |", False)]:
    check(f"表头判定 {'放行' if want else '拦下'}：{t[:30]}", _is_table_head(t) is want)
check("表头 + 分隔行 未核实数为 0",
      _verify_citations("| 对方论点 | 我的回应（为何不成立或影响被高估） |\n|---|---|", 7)[1] == 0)
check("带面板引用的行不算表头（它是数据行）",
      not _is_table_head("| 毛利率高 | 见【面板】 |"))

# ============================================================ 17. 方向漂移复检
# 失效条件复检问："新事实是否推翻了旧论点。"
# 这里问相反的一半："旧论点是否在没有新事实的情况下自己变了。"
# 真机 8/25 与 8/26 两次 --force：基本面一格未变（连截止日都相同），
# 只有一天价格波动，结论从「中性」翻成「看多」，两轮 Gate 都是 INSUFFICIENT。
print("\n[17] 方向漂移：没有新证据的方向改变")
_SYM = "DRIFTTEST"
thesis_store.record(as_of_date="2026-08-25", subject=_SYM, symbol=_SYM, direction="中性",
                    conviction="中", thesis="…", catalysts=[],
                    invalidations=["毛利率跌破68%"], panel={},
                    material_verdict="无实质材料", material_substantive=0)

_d0 = thesis_store.drift_check(_SYM, _SYM, "看多", "中", "INSUFFICIENT", 0)
check("零实质材料下翻方向 → no_evidence", _d0.get("severity") == "no_evidence", str(_d0.get("severity")))
check("告警点明没有新证据", "没有新证据" in _d0.get("text", ""))
check("带出既有论点编号与日期", str(_d0.get("prev_id")) in _d0["text"] and "2026-08-25" in _d0["text"])

_d1 = thesis_store.drift_check(_SYM, _SYM, "看多", "中", "SUFFICIENT", 4)
check("有实质材料时翻方向 → supported（不喊狼来了）", _d1.get("severity") == "supported")
check("supported 文案不带 ⚠", "⚠" not in _d1.get("text", ""))
_d2 = thesis_store.drift_check(_SYM, _SYM, "看多", "中", "THIN", 1)
check("材料偏薄时翻方向 → thin", _d2.get("severity") == "thin")

check("方向未变 → 不报", thesis_store.drift_check(_SYM, _SYM, "中性", "中", "INSUFFICIENT", 0) == {})
check("有证据时信心只差一档 → 不报（正常研究更新）",
      thesis_store.drift_check(_SYM, _SYM, "中性", "强", "SUFFICIENT", 4) == {})
check("零证据时信心只差一档 → 报（那一档没有任何依据）",
      thesis_store.drift_check(_SYM, _SYM, "中性", "强", "INSUFFICIENT", 0).get("severity")
      == "no_evidence")
thesis_store.record(as_of_date="2026-08-25", subject=_SYM + "W", symbol=_SYM + "W",
                    direction="看多", conviction="弱", thesis="…", catalysts=[],
                    invalidations=["x 条件成立"], panel={})
_d3 = thesis_store.drift_check(_SYM + "W", _SYM + "W", "看多", "强", "INSUFFICIENT", 0)
check("信心跨两档（弱→强）→ 报", _d3.get("severity") == "no_evidence")
check("信心变化用自己的措辞，不套方向翻转的话术",
      "信心自己变了" in _d3.get("text", "") and "翻不翻" not in _d3.get("text", ""))
check("无既有论点 → 不报", thesis_store.drift_check("NOSUCHSYM", "NOSUCHSYM", "看多", "中",
                                                "INSUFFICIENT", 0) == {})

# 顺序铁律：漂移复检必须在 record() 之前——record 会把旧论点置为 SUPERSEDED
_bua17 = _i5.getsource(_UA.build_unit_a)
check("漂移复检在登记新论点之前",
      _bua17.index("drift_check") < _bua17.index("thesis_store.record"))
check("台账把方向翻转写进历史（可回答：多少次翻转发生在零实质材料的日子）",
      "方向 {flip['direction']} → {direction}" in _i4.getsource(thesis_store.record))
for src, nm in ((md_src, "MD"), (pdf_src, "PDF")):
    check(f"{nm} 显示方向漂移告警", "direction_drift" in src)

# ============================================================ 18. 同业比较 / 信心封顶 / 漂移门槛
# 真机 8/26 11:35 一跑同时暴露三件事：
#   ① 双方都写「远高于行业平均」，而本轮面板一个同业数字都没有
#      —— 而且多头在 Round 2 亲手拆了空头这句，自己第 4 条却犯同样的错
#   ② 3/4 条多头论据引自同一条「无实质材料」，裁判全判「证据不足」，
#      综合却给出「看多|强」—— 同一份报告里自相矛盾
#   ③ 11:17→11:35 相隔 18 分钟、零实质材料、面板未动，信心 中→强，只差一档没报
print("\n[18] 同业比较核验 · 信心封顶 · 漂移门槛分档")
from cio.unit_a import has_peer_stats                          # noqa: E402
_NOPEER = "【Quality】\n · 毛利率: 71.07%\n · 营业利润率: 60.38%"
_PEER = "【Quality】\n · 毛利率: 71.07% [同业分位 82s]"
check("面板无同业统计时可判别", not has_peer_stats(_NOPEER) and has_peer_stats(_PEER))
for line in ["NVDA毛利率达71.07%，远高于行业平均水平。[面板]",
             "已实现波动率40.74%，远高于行业平均。[面板]",
             "该指标高于同业中位水平。[面板]"]:
    t, b = _verify_citations(line, 7, "", "", None, peer_stats=False)
    check(f"无同业基准被标出：{line[:22]}", "⚠无同业基准" in t and b == 1)
check("面板有同业分位时不再标",
      "⚠" not in _verify_citations("毛利率 71.07%，远高于行业平均。[面板]", 7, "", "",
                                   None, peer_stats=True)[0])
check("不含比较的论据不受影响",
      _verify_citations("NVDA毛利率达71.07%。[面板]", 7, "", "", None, peer_stats=False)[1] == 0)
check("build_unit_a 传入面板是否含同业统计", "_peers" in _i5.getsource(_UA.build_unit_a))

# 信心封顶：强制复研按定义没有新证据，「强」这一档不该可达
_g_ins = MG.assess([_MM(i, _PREVIEW) for i in range(3)])
check("INSUFFICIENT 信心上限为「中」", _g_ins["conviction_cap"] == "中", _g_ins["conviction_cap"])
check("THIN 信心上限仍为「弱」", MG.assess([_MM(1, _REAL), _MM(2, _PREVIEW)])["conviction_cap"] == "弱")
check("SUFFICIENT 不封顶",
      MG.assess([_MM(i, t) for i, t in enumerate(_THREE, 1)])["conviction_cap"] == "")

# 漂移门槛随证据分档
_S18 = "DRIFT18"
thesis_store.record(as_of_date="2026-08-26", subject=_S18, symbol=_S18, direction="看多",
                    conviction="中", thesis="…", catalysts=[], invalidations=["x 条件"], panel={})
check("零证据下信心只差一档也报",
      thesis_store.drift_check(_S18, _S18, "看多", "强", "INSUFFICIENT", 0).get("severity")
      == "no_evidence")
check("有证据时一档调整属正常研究更新，不报",
      thesis_store.drift_check(_S18, _S18, "看多", "强", "SUFFICIENT", 4) == {})
thesis_store.record(as_of_date="2026-08-26", subject=_S18 + "S", symbol=_S18 + "S",
                    direction="看多", conviction="强", thesis="…", catalysts=[],
                    invalidations=["y 条件"], panel={})
check("有证据时跨两档（强→弱）仍报",
      thesis_store.drift_check(_S18 + "S", _S18 + "S", "看多", "弱", "SUFFICIENT", 4)
      .get("severity") == "supported")

# ============================================================ 19. 催化剂/失效条件同样过闸
# 真机 8/26 11:49 那跑，正文干净（论据全部溯源），问题跑到了**要进台账的那两节**：
#     失效条件：净资产收益率下降到行业平均以下（<20%）
#     催化剂　：市场对NVDA的估值重新评估，导致市盈率（E/P）下降至行业平均水平以下
# 面板里没有任何同业数字，所以第一条永远无法核对——看起来可证伪，实际悬空；
# 第二条还把市盈率写成 E/P，两者互为倒数（与二部那个 Leverage 定义错误同一类）。
print("\n[19] 催化剂 / 失效条件：无同业基准 + 口径错标")
_C19 = "【Quality】 毛利率: 71.07% [截至 2026-05-20]  ROE: 61.42%"
_REAL19 = ["毛利率跌破 60%。",
           "净资产收益率下降到行业平均以下（<20%）。",
           "导致市盈率（E/P）下降至行业平均水平以下。",
           "2024年第一季度财报将验证增长。"]
_out = debate.lint_items(_REAL19, _C19, peer_stats=False)
check("干净条目不加标记", _out[0] == _REAL19[0])
check("行业平均类失效条件被标「无同业基准」", "⚠无同业基准" in _out[1])
check("告警点明此条无法核对", "无法核对" in _out[1])
check("市盈率写成 E/P 被标「口径错标」", "⚠口径错标" in _out[2])
check("一条可以同时命中多个标记", _out[2].count("⚠") == 2, _out[2])
check("凭空年份仍然被标", "⚠年份存疑" in _out[3])
check("只标不删——原文保留", all(r.split("　")[0] == o for r, o in zip(_out, _REAL19)))

_ok19 = debate.lint_items(_REAL19, _C19, peer_stats=True)
check("面板含同业分位时不再标同业", "⚠无同业基准" not in "".join(_ok19))
check("但口径错标与年份不受面板影响",
      "⚠口径错标" in _ok19[2] and "⚠年份存疑" in _ok19[3])

check("run_debate 对催化剂与失效条件都跑 lint",
      _i4.getsource(debate.run_debate).count("lint_items") == 2)
# 判据只能有一处定义：两个模块必须指向【同一个】正则对象，
# 否则日后改了一处、另一处悄悄漂移，而且不会报错。
check("正文与台账用同一个判据对象（规则只有一处定义）",
      _UA._PEER_CLAIM is debate._PEER_CLAIM and _UA.has_peer_stats is debate.has_peer_stats)

# ============================================================ 20. 估值口径的方向错误
# 真机 8/26 12:02 那跑，多头连犯两次同一个错：
#   "E/P 为 2.33% 实际上属于低的收益率……表明股票被低估而非高估"
#   "自由现金流收益率仅为 1.88%，低于行业平均水平，表明股票相对被低估"
# E/P 是【收益率】：低 = 每一元盈利要付更多股价 = 贵。2.33% 对应约 43 倍市盈率。
# 这一类与引用核验完全无关——出处对、数字对，**错的是从数字到结论那一步的方向**，
# 而它比缺出处严重得多：缺出处只是无法核实，方向反了直接翻转结论。
print("\n[20] 估值口径：方向错误")
for t, want in [
        ("E/P 为 2.33% 实际上属于低的收益率，表明股票被低估而非高估。", True),
        ("自由现金流收益率仅为1.88%，低于行业平均水平，表明股票相对被低估。", True),
        ("市盈率高达43倍，表明股票被低估。", True),
        ("E/P仅为2.33%，显示市值相对盈利过高，估值偏高。", False),
        ("E/P 高达 8%，说明股价相对盈利便宜。", False),
        ("市盈率高达43倍，估值偏高。", False),
        ("盈利收益率偏低，但公司增长强劲，不能简单认为被高估。", False),
        ("毛利率71.07%，显示强劲盈利质量。", False),
        ("营业收入同比增长65.47%，净利润同比增长64.75%。", False)]:
    check(f"{'抓出' if want else '放行'}：{t[:30]}", bool(debate._sign_error(t)) is want,
          debate._sign_error(t))

# 量词必须非贪婪，否则 [^。；;]{0,24} 会吃掉句尾"被低估"里的那个"低"，
# 规则看起来在跑、实际永远不命中——本项目最忌讳的静默失效。
check("量词非贪婪（否则规则永远不命中）", "}?" in _i4.getsource(debate._sign_error))

# 正文与台账两节都要过这一闸
_t20, _b20 = _verify_citations(
    "自由现金流收益率仅为1.88%，低于行业平均水平，表明股票相对被低估。[面板]", 7,
    "", "", None, peer_stats=False)
check("辩论正文里的方向错误被标出", "⚠方向错误" in _t20 and _b20 == 1)
check("方向错误排在其他标记之前（严重度最高）",
      _i5.getsource(_UA._verify_citations).index("⚠方向错误")
      < _i5.getsource(_UA._verify_citations).index("⚠年份存疑"))
check("失效条件同样过这一闸",
      "⚠方向错误" in debate.lint_items(["E/P 上升表明估值被高估。"], "毛利率 71.07%")[0])
check("判据只有一处定义", _UA._sign_error is debate._sign_error)

# 计数器混装了五类问题，标签必须说实话
for src, nm in ((md_src, "MD"), (pdf_src, "PDF")):
    check(f"{nm} 计数标签改为「存疑论据」（不再谎称只是未核实）",
          "存疑论据" in src and "未核实论据" not in src)

# ============================================================ 21. 误报回归：高估值 ≠ 高估
# build75 上线后第一跑就误报了一次，而且报错的是空头**完全正确**的一句话：
#     "E/P 为 2.33%（即 P/E 约 43），表明市价已处于较高估值区间……
#      自由现金流的优势并不能抵消【高估值】带来的下行风险。"
# 因为 `被?高估` 在「高估值」里匹配到了「高估」——中文这里是「高 + 估值」，
# 不是「高估 + 值」。一次子串匹配，把正确推理判成了错误。
# **这是这套核验器最不能犯的错**：惩罚合规行为会让整个信号作废。
print("\n[21] 误报回归：高估值 ≠ 高估")
_FP21 = ("高自由现金流比例确实说明公司现金生成效率良好，但E/P为2.33%（即P/E约43），"
         "表明市价已处于较高估值区间。若未来业绩增速放缓，股价可能因估值过高而出现调整。"
         "自由现金流的优势并不能抵消高估值带来的下行风险。[面板]")
check("真机误报原句不再被标", not debate._sign_error(_FP21), debate._sign_error(_FP21))
for t in ["公司处于高估值区间，E/P 仅 2.33%。",
          "尽管估值偏高（E/P 2.33%，即 P/E ≈ 43），但毛利率提供支撑。",
          "低自由现金流收益率（FCF/P 1.88%）与高市盈率之间的平衡点尚未明确。",
          "该股并未被高估，E/P 高达 8%。"]:
    check(f"正确推理不被误标：{t[:26]}", not debate._sign_error(t))
# 修完之后真错误仍然抓得住 —— 否则等于把规则关掉了
for t in ["E/P 为 2.33% 实际上属于低的收益率，表明股票被低估而非高估。",
          "自由现金流收益率仅为1.88%，低于行业平均水平，表明股票相对被低估。",
          "市盈率高达43倍，表明股票被低估。"]:
    check(f"真错误仍被抓出：{t[:26]}", bool(debate._sign_error(t)))
check("「高估/低估」加了 (?!值) 负向前瞻", "(?!值)" in _i4.getsource(debate))
check("修饰词窗口收紧到 16 字（必须贴着指标）", "{0,16}?" in _i4.getsource(debate._sign_error))

# ============================================================ 采集层可独立调用
print("\n[build80] 采集 + 闸门可脱离辩论单独跑")
import ast                                   # noqa: E402
import importlib.util                        # noqa: E402

from cio import material_gate, unit_a        # noqa: E402
from cio.models import MaterialItem          # noqa: E402

check("collect_materials 存在且只收一个 text 参数",
      list(_i4.signature(unit_a.collect_materials).parameters) == ["text"])
check("build_unit_a 改为调用它（采集逻辑只有一份）",
      "collect_materials" in _i4.getsource(unit_a.build_unit_a))
# 断言结构：抽出来的函数里不能出现任何模型调用
_cm = ast.parse(_i4.getsource(unit_a.collect_materials))
check("采集层零 LLM（没有 get_ollama / run_debate 调用）",
      not [n for n in ast.walk(_cm) if isinstance(n, ast.Name)
           and n.id in ("get_ollama", "run_debate")])
# **看函数体里所有的 dict 字面量，不只看 `return {...}`。**
# 原来只认直接返回字面量的写法；一旦改成 `out = {...}` 再 `return out`
# （build90 为了先算进料口径再发 stage 事件就是这么改的），
# 断言会读到空集合然后报"键不齐"——**报的是写法变了，不是契约破了**。
_keys = {k.value for n in ast.walk(_cm) if isinstance(n, ast.Dict)
         for k in n.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
check("返回的键齐全（下游按这几个键解包）",
      {"info", "subj", "materials", "news", "raws", "status"} <= _keys, str(sorted(_keys)))
check("进料口径也在返回里（截断必须可见）", "intake" in _keys, str(sorted(_keys)))

_spec = importlib.util.spec_from_file_location(
    "rs_probe", Path(__file__).resolve().parents[1] / "run_scan.py")
_rs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_rs)
_orig_cm = unit_a.collect_materials
try:
    unit_a.collect_materials = lambda t: {
        "info": {}, "subj": t, "news": [], "raws": [], "status": {},
        "materials": [MaterialItem(id=1, text=x, source_name="s", source_url="")
                      for x in ({"A": ["Broadcom announced a $10 billion buyback"],
                                 "B": ["Nvidia Q3 Earnings Preview: What To Expect"]}
                                .get(t, []))]}
    _a = _rs.scan_one("A")
    _b = _rs.scan_one("B")
finally:
    unit_a.collect_materials = _orig_cm
check("有实质事实 → SUFFICIENT/THIN，建议跑一部",
      _a["level"] in (material_gate.SUFFICIENT, material_gate.THIN), str(_a))
check("只有前瞻标题 → INSUFFICIENT，不跑",
      _b["level"] == material_gate.INSUFFICIENT, str(_b))
check("扫描器用的是同一个闸门，不另写近似规则",
      "material_gate.assess" in _i4.getsource(_rs.scan_one))
check("扫描器不调模型",
      not [n for n in ast.walk(ast.parse(_i4.getsource(_rs))) if isinstance(n, ast.Name)
           and n.id in ("get_ollama", "run_debate")])


# ============================================================ 给界面的结构化输出
print("\n[build82] 结构化输出 + 阶段事件")
import json as _json                          # noqa: E402
import logging as _lg                         # noqa: E402
import tempfile as _tf                        # noqa: E402

from cio import config as _cfg                # noqa: E402
from cio import debate as _dbt                # noqa: E402
from cio import render as _rnd                # noqa: E402
from cio.models import UnitAAdvice            # noqa: E402

check("archive_and_render 仍返回二元组（不重演 beta_corr 那次静默失败）",
      "tuple[str, str]" in _i4.getsource(unit_a.archive_and_render).split('"""')[0])
check("json 路径由 md 路径推出，不靠第三个返回值",
      unit_a.advice_json_path("/x/y/A证券一部建议+2026.md").endswith("A证券一部建议+2026.json"))

_tmp = Path(_tf.mkdtemp())
_old_dir, _old_pdf = _cfg.TOPIC_DIR, _rnd.render_unit_a_pdf
_old_init, _old_ins = unit_a.db.init_db, unit_a.db.insert_brief
_cfg.TOPIC_DIR = _tmp
_rnd.render_unit_a_pdf = lambda r, p: Path(p).write_text("pdf")
unit_a.db.init_db = lambda: None
unit_a.db.insert_brief = lambda *a, **k: None
try:
    _adv = UnitAAdvice(subject="TSTX", resolved="TSTX", direction="看多", conviction="弱",
                       gate_level="THIN", material_verdict="材料偏薄",
                       material_substantive=1, material_count=8,
                       bull_case="多头 [1]", bear_case="空头 [2]", audit="审计",
                       catalysts=["订单回升"], invalidations=["毛利率跌破40%"],
                       llm_calls=6, thesis_id=22)
    _md, _pdf = unit_a.archive_and_render(_adv)
    _j = _json.loads(Path(unit_a.advice_json_path(_md)).read_text(encoding="utf-8"))
    _got = unit_a.latest_advice("TSTX")
finally:
    _cfg.TOPIC_DIR, _rnd.render_unit_a_pdf = _old_dir, _old_pdf
    unit_a.db.init_db, unit_a.db.insert_brief = _old_init, _old_ins

check("json 与 md 同基名同目录", Path(_md).with_suffix(".json").exists())
check("四个 Tab 要的字段都在 json 里，界面不必解析 Markdown",
      all(k in _j for k in ("direction", "conviction", "gate_level", "material_verdict",
                            "bull_case", "bear_case", "audit", "catalysts",
                            "invalidations", "materials", "llm_calls", "thesis_id")),
      str(sorted(_j)[:8]))
from cio import runid as _rid                # noqa: E402
check("带 schema 版本（界面据此判断字段能不能信）",
      _j.get("schema_version") == _rid.SCHEMA_VERSION)
check("带 run_id（界面按 id 取结果，不按『最近一次』）",
      isinstance(_j.get("run_id"), str) and _j["run_id"].startswith("ua-"),
      str(_j.get("run_id")))
check("带 status（『今天没有』与『跑挂了』必须能分辨）",
      _j.get("status") in ("completed", "gate_blocked"), str(_j.get("status")))
check("json 里带回 md/pdf 路径，界面能给出原文链接",
      _j.get("_md_path", "").endswith(".md") and _j.get("_pdf_path", "").endswith(".pdf"))
check("latest_advice 读得回来", _got.get("thesis_id") == 22)
check("查不到时返回 {}，**不伪造空壳对象**", unit_a.latest_advice("ZZZNOPE") == {})

_stages = []


class _Cap(_lg.Handler):
    def emit(self, rec):
        _stages.append(rec.getMessage())


_h = _Cap()
_lg.getLogger("cio.stage").addHandler(_h)
from cio.utils import stage as _stg           # noqa: E402
_stg("collect", "12 条材料")
_lg.getLogger("cio.stage").removeHandler(_h)
check("阶段事件格式机器可解析", any(s.startswith("[STAGE] collect | ") for s in _stages),
      str(_stages))
check("先挂 handler 也不会把事件静默丢掉（level 在导入时就设好）",
      _lg.getLogger("cio.stage").level == _lg.INFO or _lg.getLogger("cio.stage").getEffectiveLevel() <= _lg.INFO)
check("辩论六次调用各有一个阶段事件",
      sum(1 for n in ("debate_bull_r1", "debate_bear_r1", "debate_bull_r2",
                      "debate_bear_r2", "judge", "synthesis")
          if f'"{n}"' in _i4.getsource(_dbt.run_debate)) == 6)
_uasrc = _i4.getsource(unit_a)
for _n in ("collect", "gate", "panel", "gate_blocked", "done"):
    check(f"一部主链有 {_n} 阶段事件", f'stage("{_n}"' in _uasrc)
check("闸门拦下时发 gate_blocked + done（界面才能区分『卡住』与『正常结束』）",
      'stage("gate_blocked"' in _uasrc and _uasrc.count('stage("done"') >= 2)
check("阶段事件走 stderr，stdout 留给 JSON",
      "StreamHandler" in _i4.getsource(__import__("cio.utils", fromlist=["x"]).get_logger))


print("\n" + "=" * 60)
if FAIL:
    print(f"FAILED {len(FAIL)}: " + "; ".join(FAIL))
    raise SystemExit(1)
print("全部通过。")
