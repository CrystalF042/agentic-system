"""证券一部 —— 材料实质度闸门（Material Substance Gate）。

**存在的理由。** 首次真机运行采集到 8 条材料，全部是"财报前瞻"类标题
（Seeking Alpha / Zacks / TipRanks / Moomoo），没有一条含因果内容：

    "Is Nvidia Stock a Buy Ahead of Q2 Earnings?"     ← 疑问句观点文
    "NVDA Earnings in 2 Days: How to Read the ..."    ← 日程
    "Nvidia: The Last Hurrah (Earnings Preview)"      ← 前瞻

结果是辩论几乎完全落回量化面板——因为**只有面板里有实质内容**。
而报告读起来却像是有基本面依据的：多头第一条论据"NVDA 预计第二季度业绩
将超预期【3】"，指向的其实是一篇标题党。

这把 CEO 定的证据层级整个倒过来了：

    Level 1 公司基本面/文件   最高   ← 实际只有 SEC 数字，没有管理层论述
    Level 2 事件/催化剂/修正  很高   ← 实际全是"下周有财报"，零增量
    Level 4 量化状态/因子     辅助   ← 实际承担了全部论据

**本模块不解决数据源问题**（那需要 SEC 全文 / 电话会记录 / 8-K 正文，
是 roadmap 上的事）。它只保证一件事：**报告不假装自己有它没有的证据。**

---

**判定方向是不对称的，所以默认判「薄」。**

    误判充分材料为「薄」  → 报告多一句不必要的警告。损失很小。
    误判标题党为「充分」  → 报告声称有基本面依据而实际没有。损失是全部。

所以「实质」是需要满足条件才能获得的标签；判不出来一律落到「背景」，
而不是乐观地算成实质。这与失效条件复检里"漏报远好过瞒报"是同一条原则的
另一个方向：在这里，宁可多警告，不可少警告。

---

**为什么用确定性规则而不是再叫一次 LLM。**

三个理由。一是成本：每只标的已经 6 次调用约 4 分钟，材料逐条判定会再加 N 次。
二是利益冲突：判定材料是否实质的，会是同一个随后要用这些材料写论据的模型——
它有动机说"够用"。三是可审计：规则是写死的正则，CEO 能读懂它为什么这么判，
判错了当场就能看见。所以报告里**逐条打印判定标签与理由**，而不是只给一个总分——
黑箱闸门比没有闸门更糟。

**刻意没有使用"来源域名先验"来降级。** 把 Zacks / TipRanks / Motley Fool 直接
降级很有诱惑力，但它们偶尔也转发真实公告；而 Reuters 也会发"本周看点"。
来源不等于内容。规则只看文本，来源照常打印，由 CEO 自己形成先验。

**但一手披露要单独处理**（build92 补），而真正的理由和直觉相反。

SEC filing 不是"一个新闻源"，它是**记录本身**：8-K 是公司依法必须在事件
发生后几个工作日内提交的重大事件披露。一份公告的条目长这样：

    标题   NVIDIA CORP 8-K (2026-08-28)          → 文本规则判「背景」
    正文   SEC filing 8-K filed 2026-08-28.      → 加上它就判「实质」

**问题在于第二行是采集器自己生成的占位串**（`collect.fetch_edgar_recent`），
而它里面正好有 `8-K`（硬锚点）和 `filed`（完成动作）。于是文本规则
把它判成实质——**判的不是世界发生了什么，是我们自己写下的那句话**。
后果是每一份公告都自动过闸，不管里面写了什么、甚至不管它有没有内容；
闸门开了而辩论手里是一条空存根，报告会声称有基本面依据而它没有。
**那比闸门不开更糟。**

所以这里的来源规则主要不是用来"提升"公告，而是用来**卡住内容为空的公告**：

    取到正文的公告   → 实质（PRIMARY_MIN_CHARS）
    只有表单号的公告 → 背景，并写明"正文未取到"

按来源降级新闻源那条老原则没有变；这一条守的是另一个方向——
**不把系统自己的输出当成外部证据**。
"""
from __future__ import annotations

import re

from .utils import get_logger

log = get_logger("cio.matgate")

# ---- Evidence Gate 三档（build66）----
# **没有新的可解释信息，就不制造新的观点。**
#
# 这不是省 4 分钟 6 次调用，而是把"是否启动一部"本身变成一项研究判断。
# 0 substantive 时跑辩论，实际发生的事是：两个模型拿毛利率、FCF、Beta、回撤
# 这些【二部已经确定性算好的数字】重新讲一遍故事——没有新的 information set，
# 也没有真正的因果推理，只是把二部的测量翻译成自然语言。
#
# 更糟的是观点漂移：同一批不变的数字，每天重跑 6 次 LLM，
# 今天 看多|中、明天 中性|弱、后天 看多|强——**这不是市场在变，是采样噪声**。
# 跳过反而更可靠。
#
# 三个部门因此有了三种不同的节奏：
#     CIO    continuous intelligence   —— 每天工作，它负责捕捉世界发生了什么
#     Unit B daily measurement         —— 每天工作，市场状态每天都在变
#     Unit A evidence-triggered research —— 有新证据才重新研究
INSUFFICIENT = "INSUFFICIENT"   # 0 条实质材料 → 不启动辩论，Formal vote: ABSTAIN
THIN = "THIN"                   # 1–2 条实质材料 → 启动，但信心上限「弱」
SUFFICIENT = "SUFFICIENT"       # ≥3 条实质材料 → 完整对抗流程

# THIN 为什么不直接 ABSTAIN：**一条真正有内容的材料有时就足够重要。**
# 只有一份 8-K、内容是管理层下调下季度毛利率指引——数量是 1，信息密度极高。
# 所以闸门不能退化成"substantive_count ≥ N 才开工"，而是分三档处理。
_SUFFICIENT_N = 3

SUBSTANTIVE = "实质"        # 含已发生、可核对的具体事实
CONTEXT = "背景"            # 与标的相关的真实报道，但无增量事实
EMPTY = "无实质"            # 前瞻/日程/行情复述/观点清单——不含任何新信息


# ---------------------------------------------------------------- 正向证据
# A. 完成时动作：这件事【已经发生了】。
#    注意只收 "reported" 不收 "report"——"is set to report" 是日程不是事实。
_DONE = re.compile(
    r"(宣布|签署|签订|收购|并购|中标|获批|获得|完成|推出|发布会|上调|下调|回购|"
    r"增持|减持|裁员|停产|投产|扩产|起诉|判决|批准|驳回|召回|下架|涨价|降价|"
    r"提价|中止|终止|达成|交割|上市|退市|募资|定增|分拆|和解|处罚|立案)"
    r"|(?<![A-Za-z])(announced|announces|signed|acquired|acquires|completed|awarded|"
    r"won|filed|files|launched|unveiled|raised|lifted|cut|slashed|approved|rejected|"
    r"sued|settled|recalled|halted|suspended|authorized|reported|posted|delivered|opened|"
    r"secured|agreed|terminated|divested|fined|charged"
    # 动名词。**新闻标题里因果几乎总是写成 "X rose after announcing …"**——
    # 事件是真的，只是语法上不是过去式。build96 真机漏判：
    # "AMD rose 12% after announcing a $10 billion Saudi contract" 判成了行情复述。
    # 单独一个动名词永远不足以判实质（还要 anchor / event / pct），所以放宽风险有限。
    r"|announcing|acquiring|signing|completing|launching|unveiling|winning|"
    r"securing|agreeing|awarding|approving|settling|recalling|halting|"
    r"terminating|divesting"
    # 人事变动：辞任本身就是已发生的动作，而它常常是标题里唯一的动词。
    r"|resigned|resigns|resignation|ousted|departed"
    r")(?![A-Za-z])"
    # 立案/调查是"被动开启"的事件，没有主动完成时动词，单列一条。
    r"|(?<![A-Za-z])(opened|launched|opens)\s+(an?\s+)?(\w+\s+){0,2}"
    r"(probe|investigation|inquiry|case)"
    r"|(?<![A-Za-z])steps?\s+down(?![A-Za-z])", re.I)

# A'. **标题现在时。** 新闻标题用一般现在时表示【已经发生的事】，这是行业惯例：
#
#     AMD, Cisco and HUMAIN Expand Saudi Arabia's AI Infrastructure   已经扩了
#     Arm Holdings (ARM) Shifts Strategy to Sell Own Data Center Chips 已经转了
#     IBM Introduces New Mainframe Processor                          已经发布了
#
# build96 真机上这三条全判成「背景·相关报道，无可核对的增量事实」——
# ARM 转型自研数据中心芯片、AMD 沙特系统上线，都是一等一的实质材料。
# 原来的注释写着"只收 reported 不收 report"，但那条针对的是
# **助动词结构**（"is set to report" 是日程）；裸的标题现在时不是日程。
#
# 拆成两组，因为两组的误收风险完全不同：
#   第三人称单数 -s  不定式永远不带 -s，"to expands" 不成立 → 无需额外守卫
#   复数主语的裸词形  与不定式同形 → 必须挡住 to / 情态动词
_DONE_PRESENT = re.compile(
    r"(?<![A-Za-z])(expands|introduces|shifts|launches|unveils|wins|names|"
    r"appoints|adds|begins|enters|buys|sells|delivers|secures|signs|completes|"
    r"halts|raises|boosts|scraps|ships|acquires|opens)(?![A-Za-z])"
    r"|(?<!to )(?<!will )(?<!may )(?<!can )(?<!could )(?<!would )(?<!should )"
    r"(?<!might )(?<!must )(?<![A-Za-z])"
    r"(expand|introduce|shift|launch|unveil|win|acquire|complete|sign|appoint|"
    r"halt|deliver|secure)(?![A-Za-z])"
    r"|(?<![A-Za-z])go(?:es)?\s+live(?![A-Za-z])", re.I)


def _done_in(text: str) -> bool:
    """文本里有没有【已经发生】的动作。过去式与标题现在时都算。"""
    return bool(_DONE.search(text) or _DONE_PRESENT.search(text))

# B. 硬锚点：可以拿去核对的量或文件。
#    百分比【不在】这里——它太廉价（涨跌 3% 也是百分比），单独处理。
_ANCHOR = re.compile(
    r"\$\s?\d[\d,\.]*\s?(?:billion|million|trillion|bn|mn|[bmt])(?![a-z])"
    r"|\d[\d,\.]*\s?(?:亿|万亿|千万|百万)"
    r"|(?<![A-Za-z0-9])(?:8-K|10-Q|10-K|20-F|40-F|S-1|13[FDG]|424B|6-K|DEF\s?14A|SC\s?13[DG])"
    r"(?![A-Za-z0-9])"
    r"|(招股|年报|季报|临时公告|问询函|监管函|处罚决定|中标公告|停牌|复牌)"
    r"|\bSEC\s+filing\b|\bfiling\s+shows\b", re.I)

_PCT = re.compile(r"\d{1,3}(?:\.\d+)?\s?%|百分之\s?\d+")

# C. 实质事件名词：**有些真事实根本没有数字。**
#    "商务部宣布对英伟达 H20 实施出口管制" 是一等一的实质材料，却一个数都没有。
#    只要"完成动作 + 硬锚点"就会把这类整批漏成「背景」，所以再开一条通路：
#    完成动作 + 已命名的重大事件类型，同样算实质。
#    这份清单是判断，不是真理——会漏。所以标签逐条打印，默认仍然是【不实质】。
_EVENT = re.compile(
    r"(出口管制|禁运|制裁|关税|反垄断|反倾销|反补贴|立案调查|诉讼|专利|侵权|"
    r"和解金|罚款|召回|停产|退市|摘牌|要约收购|资产重组|破产|重整|减值|"
    r"辞任|离职|任命|接任|换帅|裁员|产能|良率|订单|合同|中标|供货|"
    r"临床|三期|上市许可|获批|认证|指引|业绩预告|分红|派息|回购|增发)"
    r"|(?<![A-Za-z])(export\s+control|sanction|sanctions|tariff|tariffs|antitrust|"
    r"lawsuit|litigation|patent|infringement|settlement|fine|recall|layoff|layoffs|"
    r"bankruptcy|restructuring|impairment|write[- ]down|resign|resigns|resigned|"
    r"appointed|steps\s+down|capacity|yield|order\s+book|contract|supply\s+deal|"
    # 订单/在手订单。原来只收 `order book`，于是真机上
    # "Arm Holdings Has $2 Billion in Orders It Cannot Fill Yet" 命不中任何事件词。
    # **只收复数 `orders`**：单数 `order` 会被 "in order to" 命中。
    r"orders|backlog|bookings|"
    r"clinical|phase\s+[123iii]+|fda|approval|approved|approves|"
    r"export\s+license|guidance|dividend|buyback|"
    # 动词形先前只收了名词形，于是 "acquired ZT Systems" 命不中 acquisition、
    # "Beijing approved H20 sales" 命不中 approval —— 事件是真的，词形不对而已。
    r"probe|investigation|inquiry|halt|halted|halts|shutdown|"
    r"acquired|acquires|merged|divestiture|"
    # 部署与经营里程碑。真机上 "AMD Instinct Systems Go Live"、
    # "Shifts Strategy to Sell Own Data Center Chips" 都因为没有任何一个
    # 已登记的事件名词而落到背景 —— 而它们正是最该触发重新研究的那类事实。
    r"go(?:es)?\s+live|deployment|deployed|rollout|rolled\s+out|"
    r"data\s+cent(?:er|re)|infrastructure|design\s+win|partnership|"
    r"joint\s+venture|tape[- ]?out|volume\s+production|foundry|"
    r"spin[- ]off|acquisition|merger|stake)(?![A-Za-z])", re.I)


# ---------------------------------------------------------------- 负向标记
# 每一项都是 (正则, 中文理由)。理由会原样打印在报告里，方便 CEO 判断规则是否判错。
_NEGATIVE = [
    # —— 前瞻 / 日程：告诉你"何时会发生"，不告诉你"发生了什么" ——
    (re.compile(r"(财报|业绩|数据)\s*(前瞻|预览|预告|前情)"), "财报前瞻"),
    (re.compile(r"(将于|即将|下周|本周|明日|次日|周[一二三四五六日天])"
                r".{0,14}(公布|发布|披露|公告|发财报|开盘)"), "日程预告"),
    (re.compile(r"(?<![A-Za-z])earnings\s+(preview|date|week|season)", re.I), "earnings preview"),
    (re.compile(r"(?<![A-Za-z])(ahead\s+of|before|prior\s+to|in\s+advance\s+of)\s+"
                r"(the\s+|its\s+)?(q[1-4]\s+|fiscal\s+|second[- ]quarter\s+|"
                r"third[- ]quarter\s+)?earnings", re.I), "财报前瞻"),
    (re.compile(r"(?<![A-Za-z])(set|slated|scheduled|due|expected|poised|on\s+deck)\s+to\s+"
                r"(report|announce|post|release)", re.I), "日程预告"),
    (re.compile(r"(?<![A-Za-z])what\s+to\s+(expect|watch|know)", re.I), "看点预告"),
    (re.compile(r"(?<![A-Za-z])(earnings|results)\s+in\s+\d+\s+(day|days|hour|hours|week)", re.I),
     "倒计时"),
    (re.compile(r"\(\s*earnings\s+preview\s*\)", re.I), "earnings preview"),
    (re.compile(r"(?<![A-Za-z])(preview|countdown|curtain[- ]raiser)(?![A-Za-z])", re.I), "前瞻稿"),
    # "Reports Earnings This Week" —— 现在时 + 时间状语，说的是日程不是已发生。
    # 首跑材料 [4] 就是这条，最初被漏成「背景」。
    (re.compile(r"(?<![A-Za-z])(report|reports|reporting|announce|announces)\s+"
                r"(its\s+|q[1-4]\s+|fiscal\s+)?(earnings|results|quarterly)\s+"
                r"(this|next|on|in)\s+", re.I), "日程预告"),
    (re.compile(r"(?<![A-Za-z])(earnings|results)\s+(this|next)\s+"
                r"(week|month|quarter|monday|tuesday|wednesday|thursday|friday)", re.I),
     "日程预告"),

    # —— 观点 / 清单 / 疑问：作者的看法，不是新事实 ——
    # 清单体的数字不一定在行首 —— 真机上 "KLA Corporation (KLAC): 3 Reasons
    # We Love This Stock" 因为前面挂了公司名就漏掉了。冒号/破折号之后同样算开头。
    (re.compile(r"(?:^|[:：\-–—]\s*)\W{0,2}\d+\s*"
                r"(reasons?|things?|charts?|stocks?|picks?)(?![A-Za-z])", re.I),
     "清单体"),
    (re.compile(r"(?<![A-Za-z])(is|are)\s+[^?]{0,40}\b(a\s+)?(buy|sell|hold|bargain|"
                r"screaming\s+buy)\b", re.I), "买卖建议观点文"),
    (re.compile(r"(?<![A-Za-z])should\s+you\s+(buy|sell|own|hold)", re.I), "买卖建议观点文"),
    # `why\s+\w+\s+is` 原来只允许 why 和 is 之间**一个**词，于是真机上
    # "Why KLA Corporation (KLAC) Stock Is Down Today" 漏掉了（中间四个词）。
    (re.compile(r"(?<![A-Za-z])here'?s?\s+(is\s+)?(why|how)(?![A-Za-z])", re.I),
     "评论观点文"),
    (re.compile(r"(?<![A-Za-z])why\s+[\w\s().'’&-]{0,44}?\s*"
                r"(is|are|was|were|could|will|might|may)(?![A-Za-z])", re.I),
     "评论观点文"),
    # **转折否定式**：作者在纠正一个说法，不是在报告一件事。
    # 真机 build97–99 连续三轮误判：
    #     "AMD Enters a Sovereign AI Showcase, Not a Revenue Windfall"
    # 整篇文章的论点就是"这笔生意在财务上不重要"，却因为 Enters + infrastructure
    # 被判成实质。**要求它收尾**，这样 "AMD, not Intel, won the contract"
    # （否定短语在句中）不受影响。
    (re.compile(r",\s*not\s+(a|an|the)?\s*[\w\s'’&-]{0,34}"
                r"(?:\s*[-–—|]\s*[\w .&'’]{0,30})?$", re.I), "转折否定式"),
    # "What X Means For Shareholders / Investors" —— 解读体。
    # 真机上 KLAC 靠这一条标题 + 正文里的 "KLA reported…" 被顶成实质材料，
    # 是 build96 那个缺陷的原样复发，只是句式当时不在表里。
    (re.compile(r"(?<![A-Za-z])what\s+.{0,64}?\s+means?\s+(for|to)(?![A-Za-z])",
                re.I), "解读体"),
    (re.compile(r"(?<![A-Za-z])positioned\s+to\s+benefit|stands\s+to\s+(gain|benefit)",
                re.I), "评论观点文"),
    (re.compile(r"(?:^|[:：]\s*)buy\s+(the|these|this)(?![A-Za-z])", re.I), "荐股清单"),
    (re.compile(r"(?<![A-Za-z])(quant|trading)\s+signal|technical\s+(setup|analysis)"
                r"|as\s+an\s+input\s+in", re.I), "量化信号（非公司事实）"),
    (re.compile(r"(?<![A-Za-z])(my|our|top|best|worst)\s+\w{0,12}\s?(pick|picks|stock|stocks)"
                r"(?![A-Za-z])", re.I), "荐股清单"),
    (re.compile(r"(值得(买入|买|持有|投资)吗|该不该(买|卖)|是否值得)"), "买卖建议观点文"),
    # 问号常常不在行尾 —— RSS 标题几乎都带 " - Yahoo Finance" 这样的源名后缀。
    # build91 加正文之后这条更是彻底哑掉（问号被推到正文前面），
    # 真机上 "…Is The AI Story Fully Priced? - Yahoo Finance" 一直没被标出来。
    (re.compile(r"[?？]\s*(?:[-–—|]\s*[^?？\n]{0,40})?$"), "疑问式标题"),

    # —— 估值观点：作者/卖方对"值多少钱"的看法，不是公司发生了什么 ——
    # build95 真机：ARM 三条"实质"里两条是这一类，理由都是"已发生动作 + 具体比例"。
    (re.compile(r"(?<![A-Za-z])(fair\s+value|overvalued|undervalued|"
                r"intrinsic\s+value|price\s+target|dcf\s+analysis|"
                r"valuation\s+(run|looks|stretched))", re.I), "估值观点文"),
    (re.compile(r"(?<![A-Za-z])looks\s+(fully|richly|fairly|cheaply)\s+valued"
                r"|(?<![A-Za-z])fully\s+valued(?![A-Za-z])", re.I), "估值观点文"),
    (re.compile(r"\$\s?\d[\d,\.]*\s+target(?![A-Za-z])", re.I), "目标价"),
    (re.compile(r"(估值(偏高|偏低|过高|过低|合理)|目标价|内在价值)"), "估值观点文"),
    (re.compile(r"(?<![A-Za-z])\d{1,3}[Xx]\s+(earnings|multiple|p/e)", re.I), "估值倍数评论"),

    # —— 对比文：把两家公司摆一起比，本身不含任何一家的新事实 ——
    # **两侧必须都是词，不能是数。** "$13.3 billion vs $12.9 billion guidance"
    # 是一条真实的业绩事实（实际 vs 指引），不是对比文；
    # "Advanced Micro Devices vs. Arm Holdings" 才是。
    (re.compile(r"[A-Za-z]{2,}\s+(?:vs\.?|versus)\s+[A-Za-z]", re.I), "对比文"),
    (re.compile(r"(?<![A-Za-z])comparing\s+\w+\s+(trends|growth|performance)", re.I),
     "对比文"),

    # —— 涨跌复述，但标题里没有 "stock/shares" 这个词 ——
    # "Arm Rises 2.8% as $272 Target Prices the CPU Tollbooth" 走的就是这条漏网。
    # 面板里的波动率是同一件事更好的度量。
    (re.compile(r"^[\w\s.,'’()\-&]{0,44}?(?<![A-Za-z])"
                r"(rises|rose|falls|fell|jumps|jumped|gains|gained|drops|dropped|"
                r"surges|surged|slides|slid|climbs|climbed|sinks|sank|tumbles)"
                r"(?![A-Za-z])\s+\d", re.I), "行情复述"),

    # —— 行情复述：面板已有同一信息，且面板的更准 ——
    (re.compile(r"(股价|股票|股)\s*(大涨|大跌|飙升|暴跌|重挫|上涨|下跌|涨|跌)"), "行情复述"),
    (re.compile(r"(?<![A-Za-z])(stock|stocks|shares)\s+\w{0,8}\s?"
                r"(rise|rises|rose|rally|rallies|rallied|fall|falls|fell|jump|jumps|jumped|"
                r"slide|slides|slid|climb|climbs|climbed|drop|drops|dropped|surge|surges|"
                r"surged|plunge|plunges|plunged|sink|sinks|sank|slump|slumps|slumped|"
                r"tumble|tumbles|tumbled|"
                r"gain|gains|gained|soar|soars|soared)(?![A-Za-z])", re.I), "行情复述"),
    (re.compile(r"(?<![A-Za-z])(52[- ]week\s+(high|low)|all[- ]time\s+high|新高|新低)", re.I),
     "行情复述"),
    # 真机第五跑漏网的两条。都不致命（它们落到「背景」，同样不算实质，
    # 闸门判定没错），但标签不准就等于规则不可审计——她看不出规则为什么这么判。
    (re.compile(r"(?<![A-Za-z])\d+[- ]day\s+(losing|winning)\s+streak|连(涨|跌)\s?\d+", re.I),
     "行情复述"),
    (re.compile(r"(?<![A-Za-z])(could|may|might)\s+(set\s+up|deliver|drive|spark|fuel)\s+"
                r"[^.]{0,30}(surprise|beat|rally|selloff)", re.I), "财报前瞻"),
    (re.compile(r"(?<![A-Za-z])(may|could|might|will)\s+(plunge|surge|soar|crash|rally|"
                r"double|jump|drop)(?![A-Za-z])", re.I), "股价预测"),
    # **标题里的 "could be / may be" 一律是推测。**
    # "Not Nvidia, Not AMD. Micron Could Be September's Biggest AI Winner or Loser."
    # 真机上落到默认档「背景」——不影响闸门，但标签不准就不可审计。
    (re.compile(r"(?<![A-Za-z])(could|may|might)\s+be(?![A-Za-z])", re.I), "推测式"),
    # 期权隐含波动/隐含区间：这是市场定价，不是公司事实。
    # 面板里的已实现波动率是同一件事的更好度量，且口径固定可复算。
    (re.compile(r"(?<![A-Za-z])(implied\s+move|options?\s+(price|prices|pricing|signals?|"
                r"market)\s+|option\s+chain|straddle)", re.I), "期权定价（市场信息）"),
    (re.compile(r"(期权|隐含波动率)\s*(定价|隐含|价格|信号)"), "期权定价（市场信息）"),
]

# ---------------------------------------------------------------- 软 / 硬负向
# **不是所有负向标记都能否决一条材料，因为它们说的不是同一件事。**
#
#     硬标记  说的是【作者的姿态】：前瞻、日程、清单、买卖建议、估值观点、对比。
#             标题一旦自报是这一类，正文里的过去式动词就不该把它扶成事实材料。
#
#     软标记  说的是【价格动了】：行情复述、股价预测。
#             价格动是结果，它经常和**真实原因写在同一个标题里**：
#
#                 "Nvidia stock jumped after Beijing approved H20 sales"
#                 "AMD rose 12% after announcing a $10 billion Saudi contract"
#
#             把这两条否决掉，丢的是监管放行和一份百亿合同——
#             这正是闸门最该放行的东西。
#
# 所以软标记**照常打标签、照常堵死最弱那条通路**（done_h + pct），
# 但**不参与标题否决**。它只说明"这半句是行情"，不说明"整条是空的"。
_SOFT_WHY = frozenset({"行情复述", "股价预测"})


# ---------------------------------------------------------------- 分句
# **破折号前是事实，破折号后是钩子。** 这是财经标题里极常见的一个结构：
#
#     Arm Holdings Has $2 Billion in Orders It Cannot Fill Yet — Is ARM Stock a Buy at $257?
#     ↑ 20 亿在手订单填不满产能，硬事实                            ↑ 荐股钩子
#
#     IBM Just Opened Its Mainframes to Arm — Is the Market Missing the Shift?
#     ↑ Arm 架构打进大型机                     ↑ 观点问句
#
# 真机 8/31 一天之内两条真事实死在这上面：标题否决看的是**整条标题**，
# 于是后半句的钩子杀掉了前半句的事实。
#
# 冒号/分号也算分隔符（"KLA Corporation (KLAC): 3 Reasons We Love This Stock"），
# 带空格的连字符也算（RSS 的 " - Yahoo Finance" 源名后缀走的就是这条，
# 分出来的 "Yahoo Finance" 不含任何证据，不影响判定）。
# **不拆逗号**——"AMD, Cisco and HUMAIN Expand …" 会被拆坏。
_CLAUSE_SEP = re.compile(r"\s*[—–]\s*|\s+-\s+|\s*[:：;；]\s*")

# **这里原来有个 `_CLAUSE_MIN_CHARS = 12`，用来跳过太短的碎片。**
# 变异测试里把它改成 0，一条判定都没变——碎片（"Yahoo Finance"、公司名）
# 本来就不含证据，它什么也没在守。而且它还会反向误伤："AMD — $2B orders"
# 这样的短分句是**合格的事实**，却会被长度下限挡掉。
# 和之前删掉的 `_ENOUGH` 一样：不承重的常量只会让人以为调它有用。

# 前瞻标记：分句救援里**唯一**允许没有完成动作的通路是「硬锚点 + 事件名词」，
# 而那条通路必须挡住"别人预计会有多少"这一类。
#
#     Analysts See $5 Billion in Orders for AMD — Is It Enough?
#     ↑ 锚点 + 事件都在，但 See 说明这是别人的估计，不是已发生的事实
_FORWARD = re.compile(
    r"(?<![A-Za-z])(could|may|might|will|would|expects?|expected|expecting|"
    # `sees?` 而不是 `sees` —— "Analysts **See** $5 Billion in Orders" 里是裸词形。
    # 词形只收了一半，这在本模块已经是第四次了（acquired/acquisition、
    # approved/approval、order/orders，现在是 see/sees），所以这里写成词干加后缀。
    r"forecasts?|forecast|projects?|projected|sees?|seen|seeing|targets?|estimates?|"
    r"anticipates?|potential|opportunity|outlook|set\s+to|poised|on\s+track)"
    r"(?![A-Za-z])|(预计|有望|或将|料将|潜在|前景|目标价|市场空间)", re.I)


def _fact_clause(head: str) -> str:
    """标题里有没有一个**自成事实**的分句。返回该分句，没有则空串。

    只在整条标题已经被【硬标记】否决时才会被问到——它是那条否决的例外，
    不是一条新的常规通路。两个条件缺一不可：

        分句自身不含硬标记   否则就回到"整条标题自报家门"那个情形
        分句自身站得住       完成动作 + (锚点 | 事件)，
                             或 锚点 + 事件 且不含前瞻标记

    第二条里那个"锚点 + 事件、无完成动作"的通路是这次新开的，因为
    `Has $2 Billion in Orders` 是**状态**不是动作——而它和"签下 20 亿订单"
    一样可核对。完成动作原本就只是"不是前瞻"的一个近似，
    这里改用更直接的判据（`_FORWARD`）。

    **软标记不参与否决**（与 build96 一致）：
    `AMD Stock Jumps on $10 Billion Contract — Is It Too Late to Buy?`
    前半句是行情复述加一份百亿合同，合同是真的。
    """
    parts = [p.strip() for p in _CLAUSE_SEP.split(head or "") if p and p.strip()]
    if len(parts) < 2:
        return ""
    for p in parts:
        if _neg_scan(p)[1]:
            continue
        anchor, event = bool(_ANCHOR.search(p)), bool(_EVENT.search(p))
        if _done_in(p) and (anchor or event):
            return p
        if anchor and event and not _FORWARD.search(p):
            return p
    return ""


def _neg_scan(text: str) -> tuple[str, str]:
    """返回 (第一个命中的理由, 第一个命中的**硬**理由)。两者都可能为空串。

    分开返回是因为**打标签用第一个命中、否决只用硬命中**。
    合成一个值就得靠 `_NEGATIVE` 的排列顺序来决定否不否决——
    往列表里插一条新规则就可能悄悄改变另一条材料的判定，
    而且不会有任何报错。
    """
    first = hard = ""
    for rx, why in _NEGATIVE:
        if rx.search(text):
            if not first:
                first = why
            if why not in _SOFT_WHY:
                hard = why
                break
    return first, hard


def hard_marker(text: str) -> str:
    """这段文字里的**硬**负向标记（前瞻/日程/清单/荐股/估值/对比/期权…）。
    没有就返回空串。

    公开出来是给**外部的否决权**用的：`_neg_scan` 是私有的，而
    "标题自报家门是评论文"这条判断，除了 `classify` 自己，还有别的判定器
    需要拿它当一票否决。软标记（行情复述/股价预测）不在其内——
    价格动了经常和真实原因写在同一个标题里，见 `_SOFT_WHY`。

    **只返回标记，不返回等级。** 拿到标记的一方自己决定怎么用它，
    本模块不替它做判定。
    """
    return _neg_scan(text or "")[1]


def classify(text: str) -> tuple[str, str]:
    """判定单条材料的实质度。返回 (等级, 理由)。

    判定顺序刻意如此：

    1. **完成动作 + 硬锚点 → 实质**，即使标题里也带前瞻字样。
       "Ahead of earnings, NVDA announced a $50B buyback" 确实是实质材料，
       不能因为出现 "ahead of earnings" 就丢掉。正向证据优先于负向标记。
    2. 命中负向标记 → 无实质，并给出**具体理由**（不是笼统的"质量低"）。
    3. 其余 → 背景。有部分信号但不足以称为可核对事实。

    第 3 步是默认落点，而它算【不实质】——这是本模块的安全方向。
    """
    t = (text or "").strip()
    if not t:
        return EMPTY, "空材料"

    # **标题与正文分开看。** `basis_text` 的构造是「标题 \n 正文片段」。
    #
    # build91 把正文加进判定依据，解决了"标题含糊、事实在正文里"的漏判。
    # 但它同时让正向证据变得**太容易满足**：随便一篇评论文章的正文里
    # 都会有个过去式动词和一个百分数。build95 真机上 ARM 的三条"实质"
    # 全是这么来的——估值评论、对比文、涨跌复述，理由清一色
    # "已发生动作 + 具体比例"。
    #
    # 规则本身是按**标题**校准的，加了正文却没有重新校准，这是我的疏漏。
    head = t.split("\n", 1)[0]

    done = _done_in(t)
    anchor = bool(_ANCHOR.search(t))
    event = bool(_EVENT.search(t))
    neg, _ = _neg_scan(t)
    pct = bool(_PCT.search(t))

    done_h = _done_in(head)
    anchor_h = bool(_ANCHOR.search(head))
    event_h = bool(_EVENT.search(head))
    neg_h, hard_h = _neg_scan(head)

    # 0) **标题自报家门。** 标题命中【硬】负向标记、且标题里没有强正向证据 →
    #    正文里的 "reported…15%" 不能把一篇估值评论变成事实材料。
    #    两条例外刻意保留：
    #      a) "Ahead of earnings, NVDA announced a $50B buyback"
    #         标题里既有负向标记也有 announced + $50B → 照判实质。
    #      b) 软标记（行情复述/股价预测）根本不参与否决，见 _SOFT_WHY。
    if hard_h and not (done_h and (anchor_h or event_h)):
        # **但先看看事实是不是只在其中一个分句里。**
        # "Arm Holdings Has $2 Billion in Orders It Cannot Fill Yet
        #  — Is ARM Stock a Buy at $257?"
        # 后半句的荐股钩子不该杀掉前半句的 20 亿在手订单。见 _fact_clause。
        fact = _fact_clause(head)
        if fact:
            return SUBSTANTIVE, (f"分句含可核对事实「{fact[:44]}"
                                 f"{'…' if len(fact) > 44 else ''}」"
                                 f"（同标题另一半是{hard_h}）")
        return EMPTY, hard_h

    # 1) 正向证据足够 —— 可以顶过负向标记
    if done and anchor:
        return SUBSTANTIVE, "已发生动作 + 可核对锚点"
    if done and event:
        return SUBSTANTIVE, "已发生动作 + 重大事件"
    # 百分比太廉价（"shares rose 3%" 里的 3% 不是基本面信息，面板里的波动率
    # 是同一件事更好的度量），所以这条最弱的通路要求**完成动作出现在标题里**：
    # 正文里随便一个过去式动词不足以把一篇评论顶成实质材料。
    if done_h and pct and not neg:
        return SUBSTANTIVE, "已发生动作（标题）+ 具体比例"

    # 2) 负向标记 —— 明确说清是哪一类空材料
    #
    # **软标记不能把一条材料压到默认落点【以下】。** 它说的只是"价格动了"，
    # 而价格动经常和真实原因写在同一个标题里：
    #
    #     Intel stock slid after the company halted its Ohio fab
    #     KLA shares fell after the CFO resigned
    #     Micron stock jumped on a supply deal with a hyperscaler
    #
    # 这三条从 build63 起就被判成「无实质·行情复述」——**闸门判定没错**
    # （它们本来也进不了实质档），但标签是错的，而标签错了规则就不可审计：
    # 她翻报告只会看见"行情复述"，看不出系统其实认得那半句里的停产、
    # 辞任、供货协议。所以有正向信号时降到「背景」，理由写成两半。
    _, hard = _neg_scan(t)
    if hard:
        return EMPTY, hard
    if neg:
        if done or anchor or event:
            return CONTEXT, f"{neg}；另有具体信息，但不足以构成可核对事实"
        return EMPTY, neg

    # 3) 默认落点：不够格称为实质，但也不诬为空
    if done or anchor or event:
        return CONTEXT, "有具体信息，但缺少已发生事实与可核对内容的组合"
    return CONTEXT, "相关报道，无可核对的增量事实"


# **这里原来有第二个阈值常量 `_ENOUGH = 3`，谁都没有读它。**
# 它和 `_SUFFICIENT_N` 一模一样，注释也在讲同一件事，但 `assess()` 只用
# `_SUFFICIENT_N`。把 `_ENOUGH` 调成 2 会看起来像是放宽了闸门，
# 实际什么都不会发生——不报错、日志正常、行为不变。
# 这正是本项目在防的那类缺陷，所以删掉它，阈值只留一处。


# ---------------------------------------------------------------- 判定 → 档位
# 论点台账存的是**中文判定**（material_verdict），下游要的是**档位**。
# 这张表是两者之间唯一的换算点。
#
# **认不出的字符串一律 UNRECORDED，绝不折成 INSUFFICIENT。**
# 这两件事在报告上会印出同一句话「一部未产出观点」，但含义完全相反：
#
#     INSUFFICIENT   闸门跑过了，判定确实是"没有实质材料"——一部**主动**弃权
#     UNRECORDED     闸门根本没跑过（论点早于 build63，material_verdict 为空）
#                    ——一部**产出过观点**，只是我们没记录当时的材料判定
#
# 折成一档的后果是：所有闸门存在之前的历史论点会被静默判成"没有观点"，
# PC 永远不给它们仓位，而报告读起来完全正常、甚至像个稳健的保守决策。
# **一条永远不给仓位的链路，和一条工作正常的链路，输出长得一模一样。**
UNRECORDED = "UNRECORDED"

_VERDICT_LEVEL = {
    "材料充分": SUFFICIENT,
    "材料偏薄": THIN,
    "无实质材料": INSUFFICIENT,
    "无材料": INSUFFICIENT,
}


def level_from_verdict(verdict: str) -> str:
    """中文判定 → Evidence Gate 档位。**空串与不认识的值返回 UNRECORDED。**"""
    return _VERDICT_LEVEL.get((verdict or "").strip(), UNRECORDED)


PRIMARY_NAMES = ("EDGAR",)
PRIMARY_HOSTS = ("sec.gov",)
PRIMARY_WHY = "一手披露：SEC filing 是公司依法必须披露的已发生事件"
"""**一手披露单独走一条判定路径，理由和直觉相反。**

不是因为文本规则会漏掉公告——恰恰相反，它会**因为错误的原因收下公告**：
采集器给每份公告造的那行 body（`SEC filing 8-K filed …`）里正好有
`8-K` 和 `filed`，文本规则据此判「实质」。判的不是公司做了什么，
是我们自己写下的那句占位。

所以这条路径的作用是**卡住内容为空的公告**，见 `PRIMARY_MIN_CHARS`。
顺序：**先看来源，再看文本**。
"""


def is_primary(source_name: str = "", source_url: str = "") -> bool:
    n = (source_name or "").strip().upper()
    u = (source_url or "").lower()
    return (n in PRIMARY_NAMES) or any(h in u for h in PRIMARY_HOSTS)


PRIMARY_MIN_CHARS = 200
"""一手披露要算「实质」，**必须真的取到了正文**。

公告条目的标题就是 `NVIDIA CORP 8-K (2026-08-28)` —— 一个表单号加日期。
只凭它就判「实质」，闸门会开，而辩论手里拿到的是一条**什么都没说的存根**：
报告于是声称有基本面依据，而它没有。**那比闸门不开更糟。**

所以门槛是"这条材料里有没有可供引用的内容"，不是"它来自哪里"。
取不到正文的公告降为「背景」，并写明原因——它仍然告诉你"这天有一份 8-K"，
只是不能拿来当论据。
"""


EVENT_FORMS = frozenset({
    "8-K", "8-K/A", "10-Q", "10-Q/A", "10-K", "10-K/A", "20-F", "40-F", "6-K",
    "S-1", "S-3", "S-4", "425", "DEF 14A", "DEFA14A", "SC TO-T", "SC TO-I",
})
"""**事件性披露** —— 公司在说"我们发生了什么"。这些才是研究触发器。"""

OWNERSHIP_FORMS = frozenset({
    "3", "4", "5", "144", "SC 13D", "SC 13G", "SC 13D/A", "SC 13G/A",
    "13F-HR", "13F-HR/A", "4/A", "144/A",
})
"""**持股与交易申报** —— 某个人或机构买卖了股票。

真机上 AMD 被判「材料充分」，三条实质材料是：

    ADVANCED MICRO DEVICES INC 4   (2026-08-27)  CFO 的股份交易
    ADVANCED MICRO DEVICES INC 4   (2026-08-26)  另一位高管的交易
    ADVANCED MICRO DEVICES INC 144 (2026-08-25)  拟出售登记

同一天真正的商业事件——AMD 与思科在沙特的 AI 基础设施上线——
被判成「背景」。**证据层级整个反了。**

Form 4 是真实提交、有正文、依法必须披露的文件，所以它不是"无实质"；
但它说的是**某个人卖了股票**，不是**这家公司发生了什么**。
高管按预定计划减持每个季度都有，把它当成"今天值得重新研究这家公司"，
闸门就退化成了一个日历。

所以它落「背景」：照常显示、可被引用，但**不触发闸门**。
"""

_FORM_IN_BODY = re.compile(r"SEC filing\s+(\S+(?:\s+\d+[A-Z]*)?)\s+filed", re.I)


def filing_form(text: str) -> str:
    """从材料文本里取表单号。取不到返回空串。

    认的是 `collect.fetch_edgar_recent` 写死的那句
    `SEC filing {form} filed {date}.` —— **格式由我们自己控制**，
    不去猜公司法定名称后面那截是什么。
    """
    m = _FORM_IN_BODY.search(text or "")
    return (m.group(1).strip().upper() if m else "")


def tier_of(text: str, source_name: str = "", source_url: str = "") -> tuple:
    """判定实质度的**唯一入口**。排序（截断前）和闸门（截断后）都必须调它。

    两边各调各的，就会出现"排序说是实质、闸门说不是"这种谁也说不清的分歧——
    真机上出现过，而且两个方向都出现过。
    """
    if is_primary(source_name, source_url):
        form = filing_form(text)
        # **先看是什么表单，再看有没有正文。** 一份 Form 4 哪怕正文抓得再全，
        # 说的也是某个人卖了股票，不是这家公司发生了什么。
        if form in OWNERSHIP_FORMS:
            return (CONTEXT, f"一手披露（{form} 持股/交易申报）—— 说的是"
                             f"某人买卖了股票，不是公司发生了什么，**不触发闸门**")
        if len((text or "").strip()) >= PRIMARY_MIN_CHARS:
            return (SUBSTANTIVE, PRIMARY_WHY + (f"（{form}）" if form else ""))
        return (CONTEXT, "一手披露，但正文未取到 —— 只有表单号与日期，"
                         "不足以作为论据（**不按实质计**）")
    return classify(text)


# ---------------------------------------------------------------- 事件归并
# **闸门数的必须是事件，不是文章。**
#
# 真机 8/31 AMD 判「材料充分」，三条实质材料里有两条是同一件事：
#
#     AMD, Cisco and HUMAIN Expand Saudi Arabia's AI Infrastructure
#         as AMD Instinct Systems Go Live
#     AMD and Cisco Expand AI Infrastructure in Saudi Arabia
#
# 一份新闻稿被两家转载，`_SUFFICIENT_N = 3` 就被转载量顶穿了。
# 这和 build94「8 份历史公告 = 材料充分」是同一个家族的缺陷：
# **同一件事被多次计数就能开门**，而开门意味着启动一场完整的多空辩论。
#
# 去重（`process.dedupe_and_score`）拦不住这个：两家的标题措辞不同。
_STOP = frozenset("""
a an the and or but of in on at to for from with by as is are was were be been being
it its this that these those has have had will would can could may might must
than then so if not no nor own new more most just now today about into over under
after before out up down off all its it's
""".split())

_TOKEN = re.compile(r"[A-Za-z0-9$%一-鿿]+")
_SRC_SUFFIX = re.compile(r"\s*[-–—|]\s*[\w .&'’]{1,40}$")

SAME_EVENT_OVERLAP = 0.7
"""判为同一事件的重合度门槛。

用的是**重合系数**（交集 ÷ 较短的一方）而不是 Jaccard：
转载常常是长标题的一个子集，Jaccard 会因为长度差把它们判成不同事件。

    "AMD, Cisco and HUMAIN Expand Saudi Arabia's AI Infrastructure…"  12 词
    "AMD and Cisco Expand AI Infrastructure in Saudi Arabia"           7 词
    交集 7 → Jaccard 0.58（漏），重合系数 1.00（并）

反过来，同一家公司的两件不同的事不会被误并，因为实体和数字都不一样：

    "AMD Wins $2B Order From Oracle"  vs  "AMD Wins $3B Order From Meta"
    交集 {amd, wins, order} = 3，较短方 5 → 0.60 < 0.7
"""


def event_key(text: str) -> frozenset:
    """一条材料的事件指纹：标题里的实词集合（去源名后缀、去停用词）。

    **只看标题。** 正文是各家自己写的，长度和取到多少都不稳定——
    真机上同一篇文章三轮抓到三段不同的正文。用它做指纹，
    同一件事会时而并、时而不并，而那种不稳定不会报错。
    """
    head = (text or "").split("\n", 1)[0]
    head = _SRC_SUFFIX.sub("", head)
    return frozenset(w for w in (m.group(0).lower()
                                 for m in _TOKEN.finditer(head))
                     if len(w) > 1 and w not in _STOP)


def same_event(a: frozenset, b: frozenset) -> bool:
    if not a or not b:
        return False
    return len(a & b) / min(len(a), len(b)) >= SAME_EVENT_OVERLAP


def group_events(keyed: list) -> list:
    """keyed: [(id, event_key)] → [[id, …], …]，保持出现顺序。"""
    groups: list = []
    keys: list = []
    for mid, k in keyed:
        for i, rep in enumerate(keys):
            if same_event(rep, k):
                groups[i].append(mid)
                break
        else:
            groups.append([mid])
            keys.append(k)
    return groups


def never_substantive(text: str, source_name: str = "",
                      source_url: str = "") -> bool:
    """这条材料的档位是**按规则钉死的**吗——再取正文也不会变成实质？

    目前只有一类：**持股与交易申报**（Form 3/4/5/144/SC 13D/13G/13F）。
    `tier_of` 一看表单号就短路返回「背景」，正文写什么都不影响。

    ## 为什么这件事影响排序

    闸门名额只有 `MATERIAL_CAP` 条。真机 8/31 的 AMD：

        [3] 背景·一手披露（4 持股/交易申报）    ← 不触发闸门
        [4] 背景·一手披露（4 持股/交易申报）    ← 不触发闸门
        [5] 背景·一手披露（144 持股/交易申报）  ← 不触发闸门
        …同一行还写着「截掉 20 条」

    **10 个名额里 3 个给了按定义不可能开门的纸，门外还有 20 条相关材料。**
    ARM 更极端：Form 4、Form 144、外加一篇报道同一笔减持的新闻——
    同一个 CFO 的同一次卖出占了三个名额。

    普通新闻至少还**可能**在补完正文后变成实质（build91 那一类：
    "AMD in the spotlight this week" 的正文里是 49 亿美元的收购）。
    所以排序上：钉死在背景的，排在还可能改变的后面。

    **它们不被丢弃**——照常显示、照常可引用、判定理由照常打印。
    改的只是"谁先占名额"，而名额是稀缺的。
    """
    return (is_primary(source_name, source_url)
            and filing_form(text) in OWNERSHIP_FORMS)


def basis_of(m) -> str:
    """取一条材料的**判定依据文本**。

    **排序和判定必须用同一个函数取同一段文本。** 两处各取各的，
    就会出现"排序说它是实质、闸门说不是"这种谁也说不清的分歧——
    真机上出现过，两个方向都出现过。
    """
    return (getattr(m, "basis_text", "") or getattr(m, "text", "") or "")


def assess(materials: list) -> dict:
    """对整批材料做实质度判定。

    materials: 具有 .id / .text / .source_name 的对象列表（MaterialItem）。

    返回 dict：
      verdict         中文总判定：材料充分 / 材料偏薄 / 无实质材料 / 无材料
      level           Evidence Gate 档位：SUFFICIENT / THIN / INSUFFICIENT
      activate        是否启动多空辩论（INSUFFICIENT 时为 False）
      conviction_cap  信心上限（THIN 时为"弱"，否则空串表示不设限）
      labels          {material_id: (等级, 理由)}
      banner          报告顶部横幅（无需警告时为空串）
      constraint      注入辩论提示词的约束（无需约束时为空串）
      n / n_sub / n_ctx / n_empty  计数
    """
    labels: dict = {}
    n_sub = n_ctx = n_empty = 0
    for m in materials or []:
        # **判定看 basis_text（源头文本），不看 text（含模型摘要）。**
        # 见 models.MaterialItem.basis_text：拿模型生成的摘要判实质度，
        # 等于让一个零 LLM 的闸门去分类 LLM 的输出，而这个闸门决定的
        # 正是"要不要启动 LLM 辩论"。没有 basis_text 的老调用方回退到 text。
        tier, why = tier_of(basis_of(m), getattr(m, "source_name", ""),
                            getattr(m, "source_url", ""))
        labels[getattr(m, "id", 0)] = (tier, why)
        if tier == SUBSTANTIVE:
            n_sub += 1
        elif tier == CONTEXT:
            n_ctx += 1
        else:
            n_empty += 1
    n = len(materials or [])

    # **档位数的是事件，不是文章。** 一份新闻稿被两家转载不该把闸门顶开。
    # 归并后的重复条目照常显示、照常可引用，只是不再重复计数——
    # 而且**标签上写明它和哪一条是同一件事**，否则这一步归并就是又一个
    # 看不见的变换（本项目已经在相关性闸上吃过这个亏）。
    groups = group_events([(getattr(m, "id", 0), event_key(basis_of(m)))
                           for m in (materials or [])
                           if labels.get(getattr(m, "id", 0), ("", ""))[0]
                           == SUBSTANTIVE])
    n_sub_events = len(groups)
    for g in groups:
        for dup in g[1:]:
            tier, why = labels[dup]
            labels[dup] = (tier, f"{why}（与 #{g[0]} 同一事件，不重复计数）")

    if n == 0:
        verdict, level = "无材料", INSUFFICIENT
    elif n_sub_events >= _SUFFICIENT_N:
        verdict, level = "材料充分", SUFFICIENT
    elif n_sub_events >= 1:
        verdict, level = "材料偏薄", THIN
    else:
        verdict, level = "无实质材料", INSUFFICIENT

    banner = constraint = ""
    if verdict == "无材料":
        banner = ("⚠ **本轮无采集材料。** 以下结论完全由量化证据面板驱动，"
                  "不含任何基本面或事件信息。")
        constraint = _CONSTRAINT_NONE
    elif verdict == "无实质材料":
        kinds = _kind_summary(labels)
        banner = (f"⚠ **本轮 {n} 条材料无一含实质增量事实**（{kinds}）。"
                  f"以下结论实质上由量化证据面板驱动——"
                  f"报告中出现的「基本面」论述来自面板数字，不是来自新的公司信息。")
        constraint = _CONSTRAINT_NONE
    elif verdict == "材料偏薄":
        # 归并后条数会少于文章数，横幅必须说的是**事件数**——
        # 否则会出现「仅 3 条含实质事实」却判「偏薄」这种自相矛盾。
        dup = f"（{n_sub} 条报道归并为 {n_sub_events} 个事件）" \
            if n_sub_events != n_sub else ""
        banner = (f"⚠ **材料偏薄：{n} 条中仅 {n_sub_events} 件实质事实{dup}。** "
                  f"论证的主要重量仍在量化证据面板上。")
        constraint = _CONSTRAINT_THIN.format(n=n, n_sub=n_sub_events)

    if level == INSUFFICIENT:
        log.warning("Evidence Gate=%s（%s，%d 条材料，实质 0 条）——一部不启动，Formal vote: ABSTAIN",
                    level, verdict, n)
    else:
        log.info("Evidence Gate=%s（%s，共 %d，实质 %d%s，背景 %d，无实质 %d）",
                 level, verdict, n, n_sub,
                 f"（归并为 {n_sub_events} 个事件）" if n_sub_events != n_sub else "",
                 n_ctx, n_empty)

    return {"verdict": verdict, "level": level,
            "activate": level != INSUFFICIENT,
            # 信心上限是【确定性后置规则】，不是提示词里的请求。
            # 让模型自己"注意材料薄所以别太自信"是不可靠的——它照样会写"中"。
            #
            # INSUFFICIENT 也要封顶。这一档正常不会启动，能走到这里的只有
            # 人工强制复研——而强制复研**按定义就是没有新证据**。
            # 真机 8/26 11:35 那跑：3/4 条多头论据引自同一条「无实质材料」，
            # 裁判把它们全判成「证据不足」，综合却给出「看多|强」。
            # 审计说证据不足、综合说信心强，这是同一份报告里的自相矛盾。
            # 没有新证据时「强」这一档就不该可达。
            "conviction_cap": "弱" if level == THIN else ("中" if level == INSUFFICIENT else ""),
            "labels": labels, "banner": banner, "constraint": constraint,
            "n": n, "n_sub": n_sub, "n_ctx": n_ctx, "n_empty": n_empty,
            # **两个数都要交出去。** 只给归并后的数，"今天只有一件事"和
            # "我们把三条并成了一条"在界面上会长得一模一样。
            "n_sub_events": n_sub_events, "event_groups": groups}


def _kind_summary(labels: dict) -> str:
    """把负向理由汇总成"前瞻 4 / 观点 3 / 行情 1"这样的一句话。"""
    cnt: dict = {}
    for tier, why in labels.values():
        if tier != SUBSTANTIVE:
            cnt[why] = cnt.get(why, 0) + 1
    parts = sorted(cnt.items(), key=lambda kv: -kv[1])[:4]
    return "、".join(f"{k} {v} 条" for k, v in parts) or "无实质"


# 注入辩论提示词的约束。**必须同时改变报告和论证**——
# 只在顶部加一条警告、却让模型继续写"基本面依然强劲"，
# 报告就会自我矛盾：横幅说没有基本面材料，正文却在谈基本面。
# 这正是 build55 审计出的那类缺陷（报告与自身内容打架）。
_CONSTRAINT_NONE = (
    "\n\n【本轮材料约束 · 必须遵守】\n"
    "经实质度判定，本轮采集材料**全部不含增量事实**（属于财报前瞻、日程预告、"
    "行情复述或观点评论类）。因此：\n"
    "1) 你的论证只能建立在【量化证据面板】与材料标题里的【字面内容】之上；\n"
    "2) 不得声称掌握管理层表态、订单、产能、客户、竞争格局、指引变化、"
    "监管进展等材料中并不存在的信息；\n"
    "3) 不得把「分析师预期」「市场预计」「有望超预期」当作已发生的事实来引用——"
    "那是别人的看法，不是证据；\n"
    "4) 如果因此某一方论据不足 3 条，就只写有据的那几条，并明说材料不足。"
    "写不满比编满更有价值。")

_CONSTRAINT_THIN = (
    "\n\n【本轮材料约束 · 必须遵守】\n"
    "经实质度判定，本轮 {n} 条材料中仅 {n_sub} 条含实质事实，其余为前瞻/日程/"
    "行情复述/观点类。引用后者时**必须如实说明它只是观点或日程，不是已发生的事实**，"
    "不得把「分析师预期」「市场预计」当作证据。")


def render_labels(materials: list, gate: dict) -> dict:
    """给渲染层用：{material_id: "无实质·财报前瞻"} 这样的短标签。"""
    out = {}
    for m in materials or []:
        mid = getattr(m, "id", 0)
        tier, why = gate.get("labels", {}).get(mid, (CONTEXT, ""))
        out[mid] = f"{tier}·{why}" if why and tier != SUBSTANTIVE else tier
    return out


# ---- 未启动时的正式表述（英文原样保留，与二部 "Formal alpha vote: ABSTAIN" 同一套语言）----
NOT_ACTIVATED_HEADLINE = "Unit A not activated — no substantive new evidence."
FORMAL_VOTE_ABSTAIN = "Formal vote: ABSTAIN"
PANEL_FOLLOWS = "Deterministic panel follows for situational awareness."

# 人工 override 的说明。**必须与自动日常运行严格区分**——
# 强制复研是一个有意的决定（首次建仓、季度复审、论点到期、重大决策前重新审视），
# 报告要写清楚它依据的是既有证据集，不是新证据。
FORCED_NOTE = ("Forced review — no new substantive evidence; "
               "analysis relies on existing evidence set.")


def gate_summary(gate: dict) -> str:
    """一行式闸门摘要，报告头部与日志共用。"""
    ns, ne = gate.get("n_sub", 0), gate.get("n_sub_events", gate.get("n_sub", 0))
    return (f"Evidence Gate = {gate.get('level', '?')}"
            f"（{gate.get('verdict', '')}：{gate.get('n', 0)} 条材料，"
            f"实质 {ns} 条"
            + (f"，归并为 {ne} 个事件" if ne != ns else "") + "）")
