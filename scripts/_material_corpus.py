"""闸门判定的**回归语料** —— 真机上出现过的材料，连同它应该被判成什么。

**为什么要有这个文件。**

build91 把正文加进判定依据，修好了"标题含糊、事实在正文里"的漏判。
那一改是对的。问题在于**规则本身是按标题校准的**，加了正文之后
没有人回去重新验一遍旧的判例——于是最弱那条通路（完成动作 + 百分比）
悄悄变得几乎恒真：任何一篇评论文的正文里都有一个过去式动词和一个百分数。

四轮之后才在真机 `--verbose` 里看见后果：ARM 的三条"实质材料"
全是估值评论、对比文和涨跌复述，理由清一色「已发生动作 + 具体比例」。
**中间没有任何一次报错**，实质占比反而从 4% 涨到了 29%，看起来像在变好。

一份逐条列出旧判例的语料能在改动当天就把这件事拦下来。所以规则每改一次，
整份语料重跑一次——不是只跑新加的那几条。

---

**每条的三个字段。**

    text   材料的判定依据文本（`basis_text`：标题 \\n 正文片段）
    want   期望等级：SUBSTANTIVE / CONTEXT / EMPTY
    note   它从哪来、为什么该是这个判定

**`want` 记的是"该判成什么"，不是"当前会判成什么"。**
照着当前行为回填期望值，语料就退化成了实现的复印件——
规则改错时它会跟着一起改错，永远不会红。
"""
from __future__ import annotations

SUBSTANTIVE = "实质"
CONTEXT = "背景"
EMPTY = "无实质"

# (来源build, 文本, 期望等级, 说明)
CASES = [
    # ---------------------------------------------------------- 必须判「实质」
    ("build63", "Ahead of earnings, NVDA announced a $50B buyback", SUBSTANTIVE,
     "标题里既有前瞻词也有真事件 —— 正向证据优先于负向标记，模块文档承诺过这条通路"),
    ("build63", "商务部宣布对英伟达 H20 实施出口管制", SUBSTANTIVE,
     "一等一的实质材料，却一个数字都没有 —— 完成动作 + 事件名词那条通路"),
    ("build91",
     "AMD, Cisco and HUMAIN Expand Saudi Arabia's AI Infrastructure\n"
     "AMD announced that its Instinct MI355X systems have gone live in HUMAIN's "
     "Saudi data centers under a contract awarded earlier this year, part of a "
     "$10 billion buildout.", SUBSTANTIVE,
     "标题含糊、事实在正文里 —— build91 加正文就是为了它"),
    ("build96", "AMD rose 12% after announcing a $10 billion Saudi contract",
     SUBSTANTIVE,
     "行情 + 真实原因写在同一个标题里。按行情复述否决掉，丢的是一份百亿合同"),
    ("build96", "Nvidia stock jumped after Beijing approved H20 sales", SUBSTANTIVE,
     "同上：丢的是监管放行"),
    ("build96", "Micron shares fell 8% after the company cut its Q4 guidance",
     SUBSTANTIVE, "同上：管理层下调指引"),
    ("build96",
     "Intel Q3 revenue $13.3 billion vs $12.9 billion guidance\n"
     "Intel reported third-quarter revenue of $13.3 billion, above its own "
     "guidance, and said foundry losses narrowed.", SUBSTANTIVE,
     "「实际 vs 指引」是最标准的业绩标题。裸的 vs 会把这一类整批误杀"),
    ("build96", "Applied Materials cut 4% of staff\n"
     "The company said the reductions were completed in August.", SUBSTANTIVE,
     "完成动作在标题里 + 具体比例 —— 最弱那条通路应有的样子"),
    ("build96", "Qualcomm completed its acquisition of Arduino", SUBSTANTIVE,
     "完成动作 + 事件名词"),
    ("build96", "KLA raised its dividend by 12%", SUBSTANTIVE, "完成动作 + 事件名词"),
    # 下面这批从 build63 起就被判「无实质·行情复述」。闸门判定没错（它们本来
    # 也进不了实质档），但**标签是错的**：系统其实认得那半句里的停产、辞任、
    # 收购、立案，只是词形对不上（acquired 命不中 acquisition，
    # approved 命不中 approval）。词形补齐之后它们回到该在的档位。
    ("build96", "Intel stock slid after the company halted its Ohio fab",
     SUBSTANTIVE, "停产是实打实的经营事件"),
    ("build96", "AMD stock rose after it acquired ZT Systems", SUBSTANTIVE,
     "acquired 原来命不中 acquisition"),
    ("build96", "KLA shares fell after the CFO resigned", SUBSTANTIVE, "人事变动"),
    ("build96", "Nvidia shares tumbled as Beijing opened an antitrust probe",
     SUBSTANTIVE, "立案调查没有主动完成时动词，单列一条通路"),
    ("build96", "Micron stock jumped on a supply deal with a hyperscaler",
     CONTEXT, "有事件名词但没有已发生动词 —— 落背景，不是无实质"),
    ("build96", "Arm shares rose as analysts raised targets", CONTEXT,
     "行情 + 分析师动作：不是公司事实，但也不是空的"),

    # ---- build97：**标题现在时**。真机 8/31 三只票全 INSUFFICIENT，
    #      而当天 ARM 转型自研数据中心芯片、AMD 沙特系统上线都在材料里，
    #      全判成了「背景·相关报道，无可核对的增量事实」——
    #      因为标题用的是新闻体的一般现在时，规则只认过去式。
    ("build97",
     "AMD, Cisco and HUMAIN Expand Saudi Arabia's AI Infrastructure as "
     "AMD Instinct Systems Go Live", SUBSTANTIVE,
     "系统已经上线，是可核对的完成事实。Expand / Go Live 都是新闻体现在时"),
    ("build97", "AMD and Cisco Expand AI Infrastructure in Saudi Arabia",
     SUBSTANTIVE, "同一事件的另一条"),
    ("build97", "Arm Holdings (ARM) Shifts Strategy to Sell Own Data Center Chips",
     SUBSTANTIVE, "授权模式改为自己卖芯片 —— 一等一的战略事实"),
    ("build97", "AMD's Saudi AI Bet Is Scaling Toward 1 Gigawatt", CONTEXT,
     "**进行时不是完成时** —— Is Scaling Toward 说的是还在推进"),
    ("build97", "Nvidia is set to expand capacity next quarter", CONTEXT,
     "set to + 不定式是日程。裸词形与不定式同形，守卫必须挡住它"),
    ("build97", "Analysts expect AMD to win more data center share", CONTEXT,
     "to win 是不定式，不是已发生"),
    ("build97", "KLA Corporation (KLAC): 3 Reasons We Love This Stock", EMPTY,
     "清单体的数字不在行首 —— 前面挂了公司名就漏掉了"),
    ("build97",
     "Arm Holdings (ARM) Heads To TestMu Conference, Is The AI Story Fully "
     "Priced? - Yahoo Finance", EMPTY,
     "疑问式标题。问号被 ' - Yahoo Finance' 推离行尾，规则一直没抓到"),

    # ---- build99：**破折号前是事实，破折号后是钩子。**
    #      真机 8/31 一天两条真事实死在这上面 —— 标题否决看的是整条标题，
    #      于是后半句的荐股钩子杀掉了前半句的硬事实。
    ("build99",
     "Arm Holdings Has $2 Billion in Orders It Cannot Fill Yet — "
     "Is ARM Stock a Buy at $257?", SUBSTANTIVE,
     "20 亿在手订单填不满产能。**状态也是可核对事实**，不必有完成动作"),
    ("build99", "AMD Stock Jumps on $10 Billion Contract — Is It Too Late to Buy?",
     SUBSTANTIVE, "前半句是行情复述加一份百亿合同 —— 软标记不否决分句"),
    ("build99", "Micron Signs a $4 Billion Supply Deal: Should You Buy the Stock?",
     SUBSTANTIVE, "冒号也是分隔符"),
    ("build99", "Analysts See $5 Billion in Orders for AMD — Is It Enough?",
     EMPTY, "**别人的估计不是事实。** 无完成动作那条通路必须挡住前瞻标记"),
    ("build99", "AMD Could Win $5 Billion in Orders — Is It Enough?", EMPTY,
     "情态动词同理"),
    ("build99",
     "AMD's $10 Billion Opportunity in Sovereign AI — Is the Market "
     "Underpricing It?", EMPTY, "「机会」是估算的市场空间，不是已发生的事"),
    ("build99", "Nvidia Stock Soars to $200 Billion Market Cap — Time to Sell?",
     EMPTY, "市值不是事件名词 —— 锚点单独不够"),
    ("build99", "In order to compete, AMD must cut prices — Is the Stock a Buy?",
     EMPTY, "`in order to` 不能被当成订单 —— 事件词只收复数 orders"),
    # **分句自身带硬标记的不救。** 否则就绕回 build96 那个缺陷：
    # 一篇观点文只要在标题里引一个真数字就能被顶成实质材料。
    ("build99",
     "Is AMD a Buy After Its $10 Billion Contract? — Analysts Weigh In",
     EMPTY, "分句里锚点和事件都在，但这半句本身就是一个荐股问句"),
    ("build99",
     "3 Reasons AMD's $10 Billion Saudi Contract Matters — Our Take",
     EMPTY, "同上，清单体"),

    # ---- build100：真机连续三轮的两条误判，以及它们同类的句式 ----
    ("build100",
     "AMD Enters a Sovereign AI Showcase, Not a Revenue Windfall - Yahoo Finance\n"
     "The European contract is valued at $1.2 billion and AMD announced "
     "systems went live.", EMPTY,
     "**转折否定式**：整篇的论点就是「这笔生意在财务上不重要」"),
    ("build100",
     "What KLA (KLAC)'s AI-Fueled Advanced Packaging Momentum Means For "
     "Shareholders\nKLA reported a fiscal fourth-quarter revenue of $3.2 "
     "billion, up 12% year over year.", EMPTY,
     "**解读体** —— build96 那个缺陷的原样复发，只是句式当时不在表里"),
    ("build100", "Own KLAC Stock? Here Is How To Collect 21% A Year On It",
     EMPTY, "Here Is How —— 原来只收 here's why"),
    ("build100",
     "KLA Corporation (KLAC) Positioned to Benefit from Semiconductor Chip "
     "Complexity", EMPTY, "「有望受益」是观点"),
    ("build100", "KLA (KLAC) Stock Looks Fully Valued After Its Huge Run",
     EMPTY, "估值观点：fully valued 原来不在表里"),
    ("build100", "Why KLA Corporation (KLAC) Stock Is Down Today", EMPTY,
     "why 与 is 之间原来只允许一个词，这里隔了四个"),
    ("build100", "KLA Corporation: Buy The 2027+ Double Tailwind (NASDAQ:KLAC)",
     EMPTY, "祈使句荐股"),
    ("build100", "(KLAC) Movement as an Input in Quant Signal Sets", EMPTY,
     "量化信号推广，不是公司事实"),
    # —— 必须不被上面几条误伤 ——
    ("build100", "AMD, not Intel, won the $10 billion Saudi contract",
     SUBSTANTIVE, "否定短语在**句中**，不是转折收尾 —— 这是一条真事实"),
    ("build100",
     "Why AMD Cut Its Guidance: CFO Explains\nAMD cut its full-year guidance "
     "to $25 billion, the CFO said.", SUBSTANTIVE,
     "`why` 规则只在接 is/are/was/were/情态动词时才算评论；"
     "接动作动词的是在解释一件真发生的事"),
    ("build100",
     "Not Nvidia, Not AMD. Micron Could Be September's Biggest AI Winner or "
     "Loser.", EMPTY, "**推测式**：标题里的 could be 一律是推测，不是事实"),

    # ---------------------------------------------------------- 必须判「不实质」
    ("build63", "Is Nvidia Stock a Buy Ahead of Q2 Earnings?", EMPTY, "疑问式观点文"),
    ("build63", "NVDA Earnings in 2 Days: How to Read the Print", EMPTY, "倒计时"),
    ("build63", "Nvidia: The Last Hurrah (Earnings Preview)", EMPTY, "前瞻稿"),
    ("build63", "3 Reasons to Buy Nvidia Stock Before Earnings", EMPTY, "清单体"),
    ("build63", "Nvidia stock rose 3% on Tuesday", EMPTY,
     "行情复述 —— 面板里的波动率是同一件事更好的度量"),
    ("build92", "What to Expect From KLA's Q4 Report", EMPTY, "看点预告"),
    ("build95",
     "Arm (ARM) Stock Looks Above Fair Value Even After AI Progress\n"
     "Arm Holdings reported royalty revenue growth of 25% in the June quarter, "
     "and management raised full-year guidance. Even so, our fair value estimate "
     "of $105 implies the shares trade well above what the business is worth.",
     EMPTY,
     "真机 ARM [1]。正文里的 reported…25% 是作者为论证自己的估值观点引的旧数字"),
    ("build95",
     "Advanced Micro Devices vs. Arm Holdings: Comparing Revenue Trends\n"
     "AMD reported revenue of $7.7 billion last quarter, up 32%. Arm posted "
     "royalty growth of 25%.", EMPTY,
     "真机 ARM [2]。把两家摆一起比，本身不含任何一家的新事实"),
    ("build95",
     "Arm Rises 2.8% as $272 Target Prices the CPU Tollbooth\n"
     "Shares of Arm Holdings climbed 2.8% on Thursday after an analyst raised "
     "his price target to $272.", EMPTY,
     "真机 ARM [3]。涨了 2.8% 是行情，$272 是分析师观点 —— 两半都不是公司事实"),
    ("build96", "AMD vs Intel: Which Chip Stock Is the Better Buy?", EMPTY, "对比 + 荐股"),
    ("build96", "Arm Holdings: Fair Value Suggests 30% Downside", EMPTY, "估值观点文"),
    ("build96", "Nvidia trades at 45x earnings. Here's why that's justified.",
     EMPTY, "估值倍数评论"),
    ("build96", "Micron Rises 4.1% as Analysts Lift Targets", EMPTY,
     "行情 + 分析师观点，没有公司事实"),
    ("build96", "Should You Buy ARM Stock After Its 25% Run?", EMPTY, "买卖建议观点文"),
    ("build92", "Options price a 9% implied move for NVDA earnings", EMPTY,
     "期权定价是市场信息，不是公司事实"),
]

# 一手披露走 tier_of 的来源路径，单列。(表单号, 有无正文, 期望等级)
FILING_CASES = [
    ("8-K", True, SUBSTANTIVE, "事件性披露 + 取到正文"),
    ("10-Q", True, SUBSTANTIVE, "事件性披露 + 取到正文"),
    ("10-K", True, SUBSTANTIVE, "事件性披露 + 取到正文"),
    ("8-K", False, CONTEXT, "**只有表单号与日期** —— 那行 body 是采集器自己造的占位串"),
    ("4", True, CONTEXT, "持股/交易申报：说的是某人卖了股票，不是公司发生了什么"),
    ("144", True, CONTEXT, "同上"),
    ("SC 13G", True, CONTEXT, "同上"),
]

# ================================================================== 留出集
#
# **上面那份 CASES 是照着规则拟合出来的，拿它比较规则和模型是偏袒规则的。**
# 每一条都来自某个 build 的修复现场——规则见过它、并且是为它改的。
# 在这种语料上，规则接近满分是设计出来的结果，不是能力的证据。
#
# 下面这批来自 2026-08-31 的扩样测试：ON（安森美）与 IT（Gartner）
# 这两只票**规则从来没有见过**，而它们当天的判定是 0/2。
#
# 纪律：**永远不要为了让留出集变绿而去改规则。**
# 一旦那么做，它就不再是留出集，这份文件也就失去了全部意义。
# 要修可以，但修完必须把用到的判例挪进 CASES，并在这里换新的留出样本。
HELDOUT = [
    ("ON",
     "ON Semiconductor (ON) Stock May Be 2% Undervalued As AI Data Center "
     "Wins Build\nON Semiconductor's AI data center revenue more than doubled "
     "year over year.", EMPTY,
     "**估值观点文。** 真机判成实质：标题里的 Undervalued 是硬标记，"
     "但豁免条款 `done_h and (anchor_h or event_h)` 被 Wins + Data Center 顶开了"),
    ("ON",
     "Power Semis Soar Monday: Wolfspeed, STMicro and On Semiconductor Rally "
     "on Vera Rubin Ramp Signal", EMPTY,
     "板块涨跌复述，没有一条关于 ON 自己的事实"),
    ("ON", "Why I've Begun Accumulating ON Semiconductor\n"
     "AI data center revenue more than doubled year over year.", EMPTY,
     "个人持仓日记 —— 作者的操作，不是公司的事"),
    ("ON",
     "Wolfspeed Sinks 12% on Wider-Than-Expected Loss, ON Semiconductor Slips "
     "as Chip Sector Holds Steady", EMPTY, "行情复述，且主角是 Wolfspeed"),
    ("IT",
     "Stronger Outlook And AI Security Demand Might Change The Case For "
     "Investing In Gartner (IT)\nGartner reported contract value growth of "
     "6% to $5.6 billion in the second quarter.", EMPTY,
     "**「投资这家公司的理由可能会变」是观点。** 真机判成实质："
     "标题一个负向标记都没命中，正文里的数字把它顶上来了 —— "
     "build96 那个缺陷的第三次复发"),
    ("KLAC", "KLA Falls 3.9% as Chip-Equipment Sentiment Remains Fragile",
     EMPTY, "行情复述 + 情绪，无公司事实"),
    ("KLAC", "KLA: The AI Yield Bottleneck Is Becoming More Valuable", EMPTY,
     "论点文"),
    ("IT", "Gartner Stock Gains 28% in 3 Months: Here's What You Should Know",
     EMPTY, "涨幅复述 + 解读体"),
]

# ================================================================== 相关性
#
# **相关性闸的失手比闸门本身更贵**：被它丢掉的材料不会出现在任何输出里。
# 2026-08-31 扩样测试暴露了两类，都不是"判得严不严"的问题，是**认错了**：
#
#   一、`It's` 被当成 ticker `IT` 的所有格 → Gartner 十个名额里六个是
#       委内瑞拉石油、噪音污染、国债、橄榄球赛程
#   二、公司常用简称不是别名 → "KLA Falls 3.9%" 与 "Gartner Stock Gains 28%"
#       被判为"标题里没有这只票"
#
# (ticker, 公司名, 标题, 应不应该算相关, 说明)
RELEVANCE_CASES = [
    # ---- 必须判【相关】 ----
    ("IT", "Gartner Inc",
     "Gartner Stock Gains 28% in 3 Months: Here's What You Should Know",
     True, "**公司常用简称**。真机漏掉了：别名是法定名 Gartner Inc"),
    ("KLAC", "KLA Corporation",
     "KLA Falls 3.9% as Chip-Equipment Sentiment Remains Fragile", True,
     "同上：媒体写 KLA，票号是 KLAC，法定名是 KLA Corporation，三者都对不上"),
    ("KLAC", "KLA Corporation",
     "KLA: The AI Yield Bottleneck Is Becoming More Valuable - Seeking Alpha",
     True, "同上"),
    ("KLAC", "KLA Corporation", "KLA Corporation: See What Wall Street Sees",
     True, "**法定名逐字出现都没命中** —— 词边界卡在 Corp|oration"),
    ("ON", "ON Semiconductor",
     "ON Semiconductor (ON) Stock May Be 2% Undervalued", True, "括号形态"),
    ("ON", "ON Semiconductor",
     "Why I've Begun Accumulating ON Semiconductor", True, "公司全名"),
    ("ARM", "Arm Holdings",
     "Arm Holdings (ARM) Shifts Strategy to Sell Own Data Center Chips", True, ""),
    ("AMD", "Advanced Micro Devices",
     "AMD and Cisco Expand AI Infrastructure in Saudi Arabia", True, "裸符号，大小写一致"),

    # ---- 必须判【不相关】 ----
    ("IT", "Gartner Inc", "It's Game Week! - West Virginia University Athletics",
     False, "**`It's` 被当成 IT 的所有格。** 真机上这类占了 Gartner 六个名额"),
    ("IT", "Gartner Inc",
     "Why it's so hard to access your data from companies - WBUR", False, "同上"),
    ("IT", "Gartner Inc",
     "The National Debt Hit $40 Trillion, But It's Not an Issue in the Midterms",
     False, "同上"),
    ("IT", "Gartner Inc",
     "Trump on talk of Hegseth 2028 run: 'It's very early to talk about that'",
     False, "同上"),
    ("ON", "ON Semiconductor",
     "Supreme Court allows construction on White House ballroom to continue",
     False, "`on` 是介词。这一类真机上挡住了 —— 因为 on 没有所有格形式"),
    ("ON", "ON Semiconductor",
     "Hosting G20 meeting, Bessent tries to rally allies on Iran", False, "同上"),
    ("ARM", "Arm Holdings",
     "Current ARM mortgage rates report for Aug. 31, 2026 - Fortune", False,
     "浮动利率房贷"),
    ("ARM", "Arm Holdings",
     "Multiple firefighter crews battle 2-alarm fire in Glen Arm", False, "地名"),
    ("ARM", "Arm Holdings",
     "Venezuelan opposition up in arms over reports US wants big stake in oil",
     False, "build95 那条"),
    ("AMD", "Advanced Micro Devices",
     "Is Broadcom (AVGO) Stock a Buy Before Its Q3 Earnings?", False, "另一家公司"),
    ("KLAC", "KLA Corporation",
     "Egyptian queen's 673-diamond necklace stolen in Vienna smash-and-grab raid",
     False, "完全无关"),
    ("IT", "Gartner Inc",
     "What Does NetApp (NTAP) Being Named A Gartner Leader Mean Now?", False,
     "**主角是 NetApp**，Gartner 只是被顺带提到 —— 这一条最难，"
     "简称匹配放宽之后很容易误收"),
]


_SEC_URL = "https://www.sec.gov/Archives/edgar/data/2488/x.htm"
_BODY = " On August 27, 2026, the company entered into an agreement." * 8


def filing_text(form: str, with_body: bool) -> str:
    """按 `collect.fetch_edgar_recent` 的真实格式造一条公告材料。"""
    stub = f"CO {form} (2026-08-27)\nSEC filing {form} filed 2026-08-27."
    return stub + (_BODY if with_body else "")


def run(gate) -> list:
    """整份语料跑一遍。返回不符合期望的条目 [(build, 首行, 期望, 实得, 理由)]。"""
    bad = []
    for build, text, want, _note in CASES:
        tier, why = gate.classify(text)
        if tier != want:
            bad.append((build, text.split("\n", 1)[0][:56], want, tier, why))
    for build_form, with_body, want, _note in FILING_CASES:
        text = filing_text(build_form, with_body)
        tier, why = gate.tier_of(text, "EDGAR", _SEC_URL)
        if tier != want:
            bad.append(("filing", f"{build_form} body={with_body}", want, tier, why))
    return bad


TOTAL = len(CASES) + len(FILING_CASES)
