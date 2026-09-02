"""证券一部 —— 对抗式辩论（Bull → 交叉反驳 → Judge 审计 → Synthesizer）。

**与 Vibe-Trading 官方 `investment_committee` 的两点关键差异：**

一、官方是 `Bull ║ Bear → Risk Officer → Portfolio Manager`。
    它的 Risk Officer 会判断哪边可靠、算风险、建议仓位与止损；
    Portfolio Manager 会给 long/short/wait、仓位、目标价、执行方案。
    直接搬过来，一部内部就又长出一个 CRO 和一个 Portfolio Construction——
    而这两个角色在本系统里已经存在。组织重复比能力不足更难纠正。
    所以这里【不要】那两个角色：一部只到"观点"为止。

        Unit A: 解释未来  →  CRO: 风险是什么  →  PC: 该承担多少  →  CEO: 做不做

二、官方的 Bull 与 Bear 实际是【并行各写一篇报告】，彼此看不到，也不互相反驳。
    那不是辩论，是两篇立场相反的文章。真正的对抗必须有第二轮：

        Round 1  多空独立建案（互不可见 —— 防锚定）
             ↓
        Round 2  交换后各自反驳对方最强的三点，并【必须回应面板上对自己最不利的证据】
             ↓
        Judge    只做论证审计，不重做研究
             ↓
        Synthesizer  产出一部观点

Judge 的定位是本模块最容易做错的地方：
    如果问它"谁更有道理"，它会退化成第三个 LLM 重新分析一遍股票，
    并且因为它读了两边的材料，看起来还更权威。
    Judge 应该是 **argument auditor**：只判断哪条主张有证据、哪条经不起反驳、
    哪些分歧仍未解决。它不产生新论点，也不引入新事实。
"""
from __future__ import annotations

import os
import re

from .utils import get_logger, stage

log = get_logger("cio.debate")

# 反驳轮是成本主旋钮：开启后每只标的从 3 次 LLM 调用变成 6 次。
# 32GB 上 gpt-oss:20b 单次数十秒，_DEBATE_K=4 时差别是十几分钟量级。
REBUTTAL = os.environ.get("CIO_UA_REBUTTAL", "1") == "1"

_SYS = ("你是证券一部分析角色。【硬约束·必须遵守】：\n"
        "1) 你只能使用下方【带编号的采集材料】与【量化证据面板】里明确写到的信息；\n"
        "2) 每一条论据句末必须标注引用来源：材料用 [3]，面板用 [面板]；\n"
        "3) 两者都没有的信息一律不得写、不得凭记忆补充、不得编造公司/事件/数字/关联；\n"
        "4) 宁可只写 1 条有据可查的，也绝不许编第 2 条；\n"
        "5) 面板上标注「无数据」的项目表示【确实没有】，不得当作 0，也不得推测其数值。\n"
        "这是研究观点、供 CEO 决断参考，不是投资指令。")

# ---- Round 1：独立建案。两边互不可见，避免先看到对方论点后被锚定。----
_R1 = ("标的：「{subj}」。你是【{side}】。**只依据下列材料与面板**，用简体中文论证"
       "「{stance}」，最多 5 条，每条一句、句末标注引用（材料 [2] 或 [面板]）。\n\n"
       "**你必须至少有 1 条论据引用量化证据面板。**\n\n"
       "采集材料：\n{facts}\n\n量化证据面板（全部指标，含对你不利的）：\n{panel}"
       "{constraint}")

# ---- Round 2：交换反驳。这一轮才让"辩论"名副其实。----
# 强制回应最不利证据，是为了堵住 cherry-picking 从【引用行为】回来：
# 面板固定只挡住了"从 462 个因子里搜索"，挡不住"只援引对我有利的那几格"。
_R2 = ("标的：「{subj}」。你是【{side}】，此前你的论据如下：\n{own}\n\n"
       "对方（{other_side}）的论据如下：\n{other}\n\n"
       "请用简体中文完成两件事，仍然只能引用材料与面板：\n"
       "1)【反驳】挑出对方最强的 3 条，逐条说明为什么它不成立、或者它成立但影响被高估；\n"
       "2)【直面不利证据】从量化证据面板里挑出**对你自己的立场最不利的 3 项**，逐条回应——"
       "承认它、或说明为什么它不改变你的结论。**不许回避，不许挑对自己有利的来答。**\n"
       "每条句末标注引用。若某条你无法反驳，就明确写「此点成立，我方承认」——"
       "承认比硬辩更有价值。\n\n量化证据面板：\n{panel}"
       "{constraint}")

# ---- Judge：论证审计，不重做研究 ----
_JUDGE = ("你是【论证审计员】，不是分析师。**你不得引入任何新论点、新事实、新推测**，"
          "也不要重新分析这家公司。你只审计下面这场辩论。\n\n"
          "标的：「{subj}」\n多头（含反驳）：\n{bull}\n\n空头（含反驳）：\n{bear}\n\n"
          "量化证据面板：\n{panel}\n\n"
          "用简体中文输出三部分：\n"
          "1)【主张审计表】每行一条，格式：主张 | 提出方 | 证据 | 对方反驳 | 状态\n"
          "   状态只能取：事实成立 / 事实成立但推论未决 / 证据不足 / 已被驳倒\n"
          "   带「⚠未核实」标记的论据一律记为「证据不足」。\n"
          "2)【未解决的分歧】双方各执一词、现有证据无法裁决的点。\n"
          "3)【双方共同回避的证据】面板里明显重要、但多空都没有提及的项目——"
          "共同回避往往比双方争论的地方更值得注意。若无则写「无」。")

# ---- Synthesizer：产出一部观点 ----
# 输出到"观点"为止：方向/信心/论点/反方/争议/催化剂/失效条件。
# 不给仓位、不给止损、不给目标价、不给执行方案——那是 CRO 与 PC 的职权。
_SYNTH = ("你是【证券一部综合】。依据下面的辩论与审计结果，用简体中文产出一部观点。"
          "**不得给出仓位、止损、目标价或执行方案**——那不是一部的职权，"
          "一部只负责方向性看法与论证。\n\n"
          "标的：「{subj}」\n审计结果：\n{audit}\n\n多头：\n{bull}\n\n空头：\n{bear}\n\n"
          "按以下小节输出：\n"
          "【投资论点】3–5 条主要依据，每条句末标注引用。\n"
          "【反方论点】对方最有力的 2–3 条，即使你不同意也要如实写。\n"
          "【关键争议事实】哪些地方双方无法达成一致、需要新信息才能裁决。\n"
          "【催化剂】什么【具体、可观察】的事件会证实这个论点。\n"
          "【失效条件】什么事实一旦发生，这个论点就应视为失效——"
          "必须写成可以被后续事实直接比对的具体条件（例如"
          "「下季度营收同比转负」「该药物三期未达主要终点」「毛利率跌破 40%」），"
          "**不许写成「基本面恶化」这类无法核对的模糊表述**。\n"
          "**失效条件必须指向【公司事实】，不得只写股价表现。**"
          "「最大回撤超过 25%」「Beta 高于 2.5」「跑输基准」这类只是股价统计量——"
          "股价下跌本身不证明论点错了，它可能恰恰是论点最成立的时候。"
          "至少要有 2 条指向营收/利润/现金流/份额/订单/监管等公司层面的事实。"
          "每条一行，以「- 」开头。\n"
          "最后必须另起一行，输出一行固定格式：结论=方向|信心\n"
          "（方向 取 看多/看空/中性/观望 之一；信心 取 强/中/弱 之一）"
          "{constraint}")

_VERDICT2 = re.compile(r"结论\s*[=＝:：]\s*(看多|看空|中性|观望)\s*[|｜/丨]\s*(强|中|弱)")


# ---- 小节标题：本地模型在【】与 markdown 之间来回横跳，两种都得认 ----
# 真机第三跑，模型把小节写成了 **失效条件**（若发生即视为论点失效）而不是【失效条件】。
# 解析器只认【】，于是催化剂与失效条件【双双解析出 0 条】，而正文里明明写着 6 条。
# 报告因此在同一页上自相矛盾：
#     正文：**失效条件**（若发生即视为论点失效）→ 毛利率跌破60% …（共 6 条）
#     下一节：失效条件 →（本轮未产出可核对的失效条件）
# 更糟的是台账也存了 0 条——**明天的失效复检拿到的是空的**，而且不会报错。
# 这就是本项目一直在追的那一类：不报错、看起来正常、信息没了。
# 综合提示词里会出现的小节名。裸标题（"4. 失效条件"、"催化剂："）必须靠
# 【已知名字】来认——不能用"短行即标题"这种通用规则：
# "毛利率跌破60%" 也是一个 10 字无空格的短行，那样会把正文当成标题，把小节切碎。
_SEC_NAMES = ["投资论点", "反方论点", "关键争议事实", "催化剂", "失效条件",
              "证据来源", "风险", "结论"]
_TAIL = r"\s*[:：]?\s*(?:[（(][^)）\n]{0,40}[)）])?\s*[:：]?\s*$"
_PRE = r"^[ \t>]*(?:\d{1,2}[.、)）]\s*)?"


def _hdr_re(name: str):
    """一个小节标题的多种写法都要认。真机上已经见过五种，第六种是【组合】：

        【失效条件】      **失效条件**      ### 失效条件
        4. 失效条件       失效条件：        **【失效条件】**   ← 加粗包住方括号

    最后一种是 --force 那一跑出的，build64 只认前五种，于是催化剂和失效条件
    又双双解析出 0 条。所以这次把 ** 提出来作为【可选外壳】套在所有形式外面，
    而不是再往清单里加一条——加清单的做法会一直被新的组合绕过去。
    """
    n = re.escape(name)
    core = "|".join([r"【\s*" + n + r"\s*】", r"#{1,6}\s*" + n, n])
    return re.compile(_PRE + r"\*{0,2}\s*(?:" + core + r")\s*\*{0,2}" + _TAIL, re.M)


_INVALID_HDR = _hdr_re("失效条件")
_CATALYST_HDR = _hdr_re("催化剂")

# 小节的终止符：下一个【任意】小节标题，或最后那行 结论=。
# 只挡【】的话，markdown 版式下一节会把后面所有内容都吞进来。
_SECTION_END = re.compile(
    _PRE + r"(?:"
    r"(?:【[^】\n]{2,12}】"                                        # 【任意小节】
    r"|\*{2}[^*\n]{2,14}\*{2}"                                   # **任意小节**
    r"|#{1,6}\s*[^\n]{2,20}"                                     # ### 任意小节
    r"|(?:" + "|".join(_SEC_NAMES) + r"))" + _TAIL +              # 裸标题（限已知名字）
    # 结论= 那一行【不能】要求行尾——它后面还跟着"看多|中"。
    # 旧版是靠没有 $ 锚点才挡住的；加 $ 会让 "结论=看多|中" 混进失效条件列表。
    r"|结论\s*[=＝:：]"
    r")", re.M)


def parse_verdict(text: str) -> tuple:
    """解析 结论=方向|信心。解析不到就如实返回中性/弱，绝不猜。"""
    m = _VERDICT2.search(text or "")
    return (m.group(1), m.group(2)) if m else ("中性", "弱")


def _section(text: str, header_re) -> str:
    """取某个【小节】的正文，到下一个小节标题为止。
    本地 20B 模型的结构化输出并不稳定，取不到就返回空串——
    宁可缺一节，也不要把相邻小节的内容错当成它。"""
    m = header_re.search(text or "")
    if not m:
        return ""
    rest = text[m.end():]
    nxt = _SECTION_END.search(rest)
    return (rest[:nxt.start()] if nxt else rest).strip()


def _strip_bullet(ln: str) -> str:
    """剥掉行首的项目符号/序号与包裹的加粗。

    **项目符号会重复**：真机上出现过 "- - 营业收入同比增长跌破30%。"，
    旧写法 `lstrip("-–—•*")` 只去掉紧邻的那一批，中间隔了空格的第二个 `-`
    会留在条件文本里，跟着进台账、跟着进复检的关键词。
    """
    ln = (ln or "").strip()
    ln = re.sub(r"^(?:[-–—•]+\s*)+", "", ln)      # 重复项目符号 "- - "
    ln = re.sub(r"^\*\s+", "", ln)                # 单个 * 项目符号（不是 **加粗）
    ln = re.sub(r"^\d+[.)、]\s*", "", ln)
    return ln.strip().strip("*").strip()          # 去掉包裹的 **加粗**


def parse_section_warnings(text: str) -> list:
    """小节标题出现了、但一条都没解析出来 —— 这是【解析失败】，不是【模型没写】。

    两者结论相反，必须分开说：
      模型没写 → "这个论点此后无法被证伪，值得注意"（对分析师的判断）
      解析失败 → "我的解析器坏了，原文里其实有"（对代码的判断）
    把后者说成前者，就是拿自己的 bug 去指责模型，而且会让人以为回路在工作。
    """
    t = text or ""
    out = []
    for name, fn in (("失效条件", parse_invalidations), ("催化剂", parse_catalysts)):
        if name in t and not fn(t):
            out.append(f"⚠ 综合文本里出现了「{name}」，但解析器一条都没取到——"
                       f"这是**解析失败**，不是模型没写。请看上方【一部观点】原文，"
                       f"并把这份报告发回给开发修解析器。")
    return out


def parse_invalidations(text: str) -> list:
    """抽出失效条件。这是一部唯一【可被后续事实证伪】的产出，必须结构化落库。

    没有这个回路，一部就是每天重新编一个故事、永远不会被检验——
    写下失效条件很容易，回来检查才是它有价值的地方。
    """
    body = _section(text, _INVALID_HDR)
    out = []
    for ln in body.splitlines():
        ln = _strip_bullet(ln)
        if len(ln) < 6 or ln.startswith("【"):
            continue
        # 模糊表述挡回去：无法与事实比对的条件等于没写
        if re.fullmatch(r"(基本面|情况|形势|前景|逻辑)?\s*(恶化|变差|走弱|不及预期)\s*[。.]?", ln):
            continue
        out.append(ln[:160])
    return out[:6]


# ---- 只讲股价的失效条件 ----
# 真机第二跑产出的 5 条失效条件里有 3 条是这样：
#     「最大回撤超过 -25%」「Beta 超过 2.5」「尾随12-1收益低于 10%」
# 它们看起来具体、可核对，其实**什么都没证伪**：
# 股价下跌不证明论点错——对一个逆向或长期论点来说，那恰恰可能是它最成立的时候。
# 用股价当失效条件，等于把"论点错了"和"暂时亏钱"划等号，
# 而这正是最该被这套流程挡住的思维方式。
#
# 这里只【标记】不【删除】：把它删掉，一部就会连"我承认我在拿股价当论据"
# 这个信息都不给你了。标出来，你自己判断。
_MKT_ONLY = re.compile(r"(beta|贝塔|最大回撤|回撤|波动率|波动性|夏普|sharpe|相对强弱|"
                       r"超额收益|超额回报|跑输|跑赢|尾随\s*12|均线|股价|收盘价|"
                       r"目标价|换手|成交量)", re.I)
# 只要同时提到公司层面的量，就不算"只讲股价"——宁可少标，不可错标。
_FUNDAMENTAL = re.compile(r"(营收|收入|营业额|毛利|净利|利润|现金流|FCF|自由现金|ROE|"
                          r"净资产收益|负债|杠杆|订单|产能|良率|出货|份额|客户|指引|"
                          r"管制|制裁|获批|专利|诉讼|裁员|停产|召回|E/P|市盈|市销|估值|"
                          r"分红|回购|减值)", re.I)


def market_only_invalidations(conds: list) -> list:
    """挑出【只引用股价/风险统计量】的失效条件。返回其中的条目原文。"""
    return [c for c in (conds or [])
            if _MKT_ONLY.search(c) and not _FUNDAMENTAL.search(c)]


def parse_catalysts(text: str) -> list:
    body = _section(text, _CATALYST_HDR)
    out = []
    for ln in body.splitlines():
        ln = _strip_bullet(ln)
        if len(ln) >= 6 and not ln.startswith("【"):
            out.append(ln[:160])
    return out[:6]


_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")

# ---- 同业比较 / 口径错标 ----
# 这两条规则原本只作用在辩论正文上，而真机 8/26 11:49 那跑说明**更该查的是
# 催化剂和失效条件**——它们要进台账、要被后续事实逐条比对。那一跑写下的是：
#     失效条件：净资产收益率下降到行业平均以下（<20%）
#     催化剂　：市盈率（E/P）下降至行业平均水平以下
# 面板里没有任何同业数字，所以"行业平均以下"这条**永远无法核对**——
# 它看起来是可证伪的条件，实际是一句永远悬空的话。
_PEER_CLAIM = re.compile(
    r"(行业平均|行业均值|行业水平|同业|同行|业内平均|板块平均|可比公司)"
    r"|(?<![A-Za-z])(industry|sector|peer)\s+(average|median|mean)(?![A-Za-z])"
    r"|(?<![A-Za-z])(vs\.?|versus|compared\s+to)\s+(industry|sector|peers)(?![A-Za-z])")
_PEER_STAT = re.compile(r"(分位|pctile|percentile|同业|行业中位)", re.I)

# 口径错标：名字和公式对不上。这与二部那个 `Leverage = debt/assets` 实为
# 总负债/总资产是同一类——**不是数据错，是 metric definition 与实际计算不一致**。
# 只收互为倒数这种硬错误，不做泛化的术语检查。
_MISLABEL = [
    (re.compile(r"(市盈率|P\s*/\s*E)\s*[（(]\s*E\s*/\s*P\s*[)）]"),
     "市盈率与 E/P 互为倒数，不是同一个量"),
    (re.compile(r"(盈利收益率|earnings\s+yield)\s*[（(]\s*P\s*/\s*E\s*[)）]", re.I),
     "盈利收益率是 E/P，不是 P/E"),
    (re.compile(r"(市销率|P\s*/\s*S)\s*[（(]\s*S\s*/\s*P\s*[)）]"),
     "市销率与 S/P 互为倒数"),
]


# ---- 估值口径的方向错误 ----
# 真机 8/26 12:02 那跑，多头连着犯了两次同一个错：
#     "E/P 为 2.33% 实际上属于低的收益率……表明股票被低估而非高估"
#     "自由现金流收益率仅为 1.88%，低于行业平均水平，表明股票相对被低估"
# **收益率口径的方向是反的。** E/P 是盈利收益率：
#     E/P 低  = 每一元盈利要付更多股价 = 贵
#     E/P 高  = 便宜
# 2.33% 的 E/P 对应约 43 倍市盈率，那是贵，不是被低估。
#
# 这一类和引用核验完全无关——句子规规矩矩标了 [面板]，数字也没抄错，
# **错的是从数字到结论那一步的方向**。它比缺出处严重得多：
# 缺出处只是无法核实，方向反了是把"贵"读成了"便宜"，直接翻转结论。
#
# 与二部那个 `Leverage = debt/assets` 实为总负债/总资产是同一个家族：
# 数据没错，是口径与解释对不上。所以只收**倒数关系明确、方向唯一**的那几个比率。
_YIELD = r"(E\s*/\s*P|S\s*/\s*P|FCF\s*/\s*P|盈利收益率|自由现金流收益率|earnings\s+yield)"
_MULTIPLE = r"(市盈率|市销率|P\s*/\s*E|P\s*/\s*S)"
_LOW = r"(仅为|偏低|较低|低于|下降|走低|低)"
_HIGH = r"(高达|偏高|较高|高于|过高|上升|高)"
# 结论词前若紧跟否定（"而非高估"/"并非被低估"），说明作者是在排除该结论，不算方向错。
#
# **`(?!值)` 是 build76 补的，它挡住一次真实的误报。** build75 上线后第一跑就把
# 空头这句**完全正确**的话标成了方向错误：
#     "E/P 为 2.33%（即 P/E 约 43），表明市价已处于较高估值区间……
#      自由现金流的优势并不能抵消【高估值】带来的下行风险。"
# 因为 `被?高估` 在「高估值」里匹配到了「高估」——
# 中文这里是「高 + 估值」（valuation is high），不是「高估 + 值」。
# 一个子串匹配，看起来对，实际把正确推理判成了错误。
#
# **这正是这套核验器最不能犯的错。** 惩罚合规行为会让信号作废（build62 那 32 条
# ⚠ 就是这么废掉的），而这次废掉的还是全套里最严重的那一档标记。
_CHEAP = r"(?<![非不无])(被?低估(?!值)|便宜|折价|价值洼地|undervalued)"
_EXPENSIVE = r"(?<![非不无])(被?高估(?!值)|昂贵|溢价|overvalued)"


def _sign_error(line: str) -> str:
    """收益率口径与"贵/便宜"的方向是否搞反了。返回理由，无误则返回空串。"""
    t = line or ""

    def near(a, b):
        """a 与 b 同现且 b 在 a 之后不远处——同一句里在讲同一件事。"""
        m = re.search(a, t, re.I)
        return bool(m) and bool(re.search(b, t[m.end():m.end() + 60], re.I))

    # 两个量词约束，各挡一类错：
    # ① **非贪婪**：贪婪的 {0,24} 会一路吃到句尾"被低估"里的那个「低」当成 _LOW，
    #    剩下的窗口只剩"估。"，结论词被自己的量词吞掉 —— 规则永远不命中。
    # ② **窗口收到 16 字**：修饰词必须贴着指标，否则它修饰的是别的东西。
    #    "E/P为2.33%（即P/E约43），表明市价已处于【较高】估值区间" 里那个「较高」
    #    形容的是估值，不是 E/P，隔了 23 个字 —— 不该被当成"E/P 高"。
    if near(_YIELD + r"[^。；;]{0,16}?" + _LOW, _CHEAP):
        return "收益率口径方向反了：E/P 等收益率【低】= 贵，不是被低估"
    if near(_YIELD + r"[^。；;]{0,16}?" + _HIGH, _EXPENSIVE):
        return "收益率口径方向反了：E/P 等收益率【高】= 便宜，不是高估"
    if near(_MULTIPLE + r"[^。；;]{0,16}?" + _HIGH, _CHEAP):
        return "倍数口径方向反了：市盈率【高】= 贵，不是被低估"
    if near(_MULTIPLE + r"[^。；;]{0,16}?" + _LOW, _EXPENSIVE):
        return "倍数口径方向反了：市盈率【低】= 便宜，不是高估"
    return ""


def has_peer_stats(panel_text: str) -> bool:
    """本轮面板里是否真有同业统计量。没有的话，任何"高于行业平均"都没有出处。"""
    return bool(_PEER_STAT.search(panel_text or ""))


def lint_items(items: list, corpus: str, peer_stats: bool = True) -> list:
    """给催化剂 / 失效条件逐条加确定性标记。

    三类，都属于"形式合规、内容无据"——引用核验抓不到，因为它们规规矩矩标了出处：

      ⚠年份存疑    凭空的年份。年份不可能被推导出来（"E/P 2.38% → P/E 约 42"
                   里的 42 是合法推导），所以它一旦不在语料里就是编的。
      ⚠无同业基准  面板里没有任何同业统计量，却拿"行业平均"做门槛。
                   这类失效条件**永远无法核对**——看起来可证伪，实际悬空。
      ⚠口径错标    名字和公式互为倒数（市盈率写成 E/P）。与二部那个
                   `Leverage = debt/assets` 实为总负债/总资产是同一类错误。

    **一律只标不删。** 删掉你就看不见一部写过什么；而标记会连同条目一起进台账——
    一条锚在错误基准上的失效条件，本来就不该被当成可核对的条件。
    """
    out = []
    for it in items or []:
        marks = []
        if corpus:
            bad = [y for y in dict.fromkeys(_YEAR.findall(it)) if y not in corpus]
            if bad:
                marks.append(f"⚠年份存疑（{'、'.join(bad)} 未见于面板与材料）")
        if (not peer_stats) and _PEER_CLAIM.search(it):
            marks.append("⚠无同业基准（本轮面板不含同业统计量，此条无法核对）")
        for rx, why in _MISLABEL:
            if rx.search(it):
                marks.append(f"⚠口径错标（{why}）")
                break
        _se = _sign_error(it)
        if _se:
            marks.append(f"⚠方向错误（{_se}）")
        out.append(f"{it}　" + "　".join(marks) if marks else it)
    return out


# 旧名保留：只做年份的那版仍被自检直接调用
def _mark_years(items: list, corpus: str) -> list:
    return lint_items(items, corpus, peer_stats=True)


def run_debate(subj: str, facts: str, panel_text: str, oll, model: str,
               verify, temperature: float = 0.15, constraint: str = "") -> dict:
    """跑完整对抗流程。verify(text, quote_source="") -> (标注后文本, 未核实条数)。

    constraint：材料实质度闸门产出的约束（见 material_gate）。
    **它必须同时进入提示词和报告**——只在报告顶部加警告、却让模型照旧写
    "基本面依然强劲"，报告就会自我矛盾：横幅说没有基本面材料，正文却在谈基本面。
    所以这里把它接进 R1 / R2 / Synth 三处提示词。

    返回 dict：bull / bear / bull_rebuttal / bear_rebuttal / audit / synthesis /
    direction / conviction / catalysts / invalidations / unverified / llm_calls
    """
    calls = 0

    # 年份核验的语料 = 面板 + 材料。两者之外出现的年份就是编的。
    corpus = (panel_text or "") + "\n" + (facts or "")

    def ask(prompt: str) -> str:
        nonlocal calls
        calls += 1
        return oll.chat(prompt, system=_SYS, model=model, temperature=temperature)

    # Round 1：互不可见
    stage("debate_bull_r1", "多头独立建案")
    bull_raw = ask(_R1.format(subj=subj, side="多头", stance="看多", facts=facts,
                              panel=panel_text, constraint=constraint))
    stage("debate_bear_r1", "空头独立建案")
    bear_raw = ask(_R1.format(subj=subj, side="空头", stance="看空/回避", facts=facts,
                              panel=panel_text, constraint=constraint))
    bull, ub = verify(bull_raw, "", corpus)
    bear, ur = verify(bear_raw, "", corpus)
    unverified = ub + ur

    bull_reb = bear_reb = ""
    if REBUTTAL:
        # Round 2：交换。此时才允许看到对方——顺序很重要，
        # 若第一轮就同时可见，后写的一方会被先写的一方锚定。
        stage("debate_bull_r2", "多头反驳并直面不利证据")
        br_raw = ask(_R2.format(subj=subj, side="多头", other_side="空头",
                                own=bull, other=bear, panel=panel_text,
                                constraint=constraint))
        stage("debate_bear_r2", "空头反驳并直面不利证据")
        rr_raw = ask(_R2.format(subj=subj, side="空头", other_side="多头",
                                own=bear, other=bull, panel=panel_text,
                                constraint=constraint))
        # 反驳轮会【整行引述对方原话】。核验器需要拿到对方的文本才能分辨
        # "忠实引述"与"伪造对方论点"——后者比无出处更严重，单独标 ⚠引述失实。
        bull_reb, u1 = verify(br_raw, bear, corpus)
        bear_reb, u2 = verify(rr_raw, bull, corpus)
        unverified += u1 + u2

    bull_full = bull + (("\n\n【反驳与直面不利证据】\n" + bull_reb) if bull_reb else "")
    bear_full = bear + (("\n\n【反驳与直面不利证据】\n" + bear_reb) if bear_reb else "")

    stage("judge", "裁判做论证审计（不引入新事实）")
    audit = ask(_JUDGE.format(subj=subj, bull=bull_full, bear=bear_full, panel=panel_text))
    stage("synthesis", "综合出方向与信心（不给仓位）")
    synthesis = ask(_SYNTH.format(subj=subj, audit=audit, bull=bull_full, bear=bear_full,
                                  constraint=constraint))

    direction, conviction = parse_verdict(synthesis)
    # 综合是【推给 CEO 的那段文字】，此前完全没过任何核验——
    # 催化剂里那个凭空的"2024年第一季度"就是从这里出去的（句末还标着 [面板]）。
    #
    # 但**不能**对综合跑完整的引用核验：【关键争议事实】那一节按设计就是问句
    # （"高毛利率是否可持续？"），它们没有也不该有引用，全核一遍会把它们整批误标。
    # 所以这里只跑最窄的一项：年份。
    _peers = has_peer_stats(panel_text)
    _inval = lint_items(parse_invalidations(synthesis), corpus, _peers)
    _cat = lint_items(parse_catalysts(synthesis), corpus, _peers)
    return {
        "bull": bull, "bear": bear,
        "bull_rebuttal": bull_reb, "bear_rebuttal": bear_reb,
        "audit": audit, "synthesis": synthesis,
        "direction": direction, "conviction": conviction,
        "catalysts": _cat,
        "invalidations": _inval,
        "parse_warnings": parse_section_warnings(synthesis),
        "market_only_invalidations": market_only_invalidations(_inval),
        "unverified": unverified, "llm_calls": calls,
    }
