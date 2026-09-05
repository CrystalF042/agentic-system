"""证券一部（Trading Unit A）—— LLM-augmented 交易研究线。
自研轻量版：多头/空头/裁判 三个 gpt-oss 角色做多空辩论 → 回测支撑 → 出《一部建议》。

红线：本地 gpt-oss（严禁中国模型）、免费数据、只回测不实盘、只报事实不编造。
独立性（项目书铁律）：只用客观事实材料 + 行情真值；不读 CIO 的结论、不看证券二部——判断独立产生。
职权：一部允许方向性判断（看多/看空/仓位）——这是它的活；但仅研究观点，须经 CRO 与 CEO 决断。
"""
from __future__ import annotations

import collections
import itertools
import os
import re

from . import (backtest, collect, db, debate, evidence, material_gate, process,
               quant_data, runid, thesis_store, topic)
from .config import settings
from .models import CollectionStatus, DailyPick, MaterialItem, UnitAAdvice
from .ollama_client import get_ollama
from .utils import file_stamp, get_logger, safe_filename, stage, stamp_beijing, stamp_ny, truncate

log = get_logger("cio.unit_a")

# 一次进程 = 一次运行。界面拿到它之后整个页面只读这一个 id 的结果，
# 不再问"最近一次是什么"——那个问题在并发下会答错，而且答得毫无异常。
RUN_ID = runid.new_run_id("ua")

# 零幻觉硬约束：论据只能引用带编号的采集材料，句末标注 [编号]；标不到的自动打「⚠未核实」。
_SYS = ("你是证券一部分析角色。【硬约束·必须遵守】：\n"
        "1) 你只能使用下方【带编号的采集材料】里明确写到的信息；\n"
        "2) 每一条论据句末必须标注引用的材料编号，如 [3] 或 [1][4]；\n"
        "3) 材料里没有的信息一律不得写、不得凭记忆补充、不得编造公司/事件/数字/关联；\n"
        "4) 宁可只写 1 条有据可查的，也绝不许编第 2 条。\n"
        "这是研究观点、供 CEO 决断参考，不是投资指令。")

_BULL = ("标的：「{subj}」。**只依据下列带编号的采集材料**，用简体中文论证'看多'的理由，最多 5 条，"
         "每条一句、**句末必须标注引用编号**（如 [2]）。材料里没有的绝对不写。\n\n采集材料：\n{facts}")

_BEAR = ("标的：「{subj}」。**只依据下列带编号的采集材料**，用简体中文论证'看空/回避'的理由，最多 5 条，"
         "每条一句、**句末必须标注引用编号**（如 [2]）。材料里没有的绝对不写。\n\n采集材料：\n{facts}")

_JUDGE = ("你是【裁判】。下面是多头/空头对「{subj}」的论据；**带「⚠未核实」标记的表示无法追溯到采集材料、"
          "不可采信为事实**。请用简体中文：1) 列核心【分歧点】；2) 列【主要风险】；3) 给简短综合结论。"
          "**对⚠未核实的论据必须保持谨慎、不得当作事实依据**。\n"
          "最后必须另起一行，用固定格式输出一行：结论=方向|信心|仓位\n"
          "（方向 取 看多/看空/中性 之一；信心 取 强/中/弱 之一；仓位 取 轻仓/中仓/重仓/观望 之一）\n\n"
          "多头论据：\n{bull}\n\n空头论据：\n{bear}")

_VERDICT = re.compile(r"结论\s*[=＝:：]\s*(看多|看空|中性)\s*[|｜/丨]\s*(强|中|弱)\s*[|｜/丨]\s*(轻仓|中仓|重仓|观望)")
_CITE = re.compile(r"[\[【]\s*(?:材料)?\s*(\d{1,2})\s*[\]】]")


# 面板引用标记。**核验器必须认它**，否则会出现一个很坏的反向激励：
# 新提示词要求模型用 [面板] 标注量化证据来源，而核验器只认数字编号 [1..N]，
# 于是【模型越听话地引用面板，被标 ⚠未核实 的就越多】。
# 首次真机运行 32 条论据几乎全被标记，这个信号直接废掉——
# 而面板恰恰是确定性生成、最可溯源的那部分证据。
_PANEL_CITE = re.compile(r"[\[【]\s*(?:面板|panel|PANEL)\s*[\]】]")

# ---- 不是论据、因此不需要引用的行 ----
# build62 修好了 [面板]，但 build62 首跑仍有 14 条 ⚠未核实，全部来自 Round 2。
# 原因是 Round 2 的输出格式和 Round 1 不一样：它带 markdown 小标题、分隔线，
# 以及**逐条引述对方论点**的行。这些都不是新主张，却被逐行核验器当成了论据。
# 判据必须是"这一行是否作出了一个需要出处的断言"，而不是"这一行有没有中括号"。
_STRUCT = re.compile(
    r"^(?:[-*_=]{3,}"                                   # 分隔线 --- *** ___
    r"|#{1,6}\s+\S.*"                                   # markdown 标题
    r"|\|[\s:\-|]{3,}\|"                                # 表格分隔行 |---|---|
    r"|【[^】]{2,12}】[：:]?"                            # 【投资论点】这类小节标题
    r"|结论\s*[=＝:：].*"                               # 结论=看多|中 是结构化输出，不是论据
    r")$")
# 整行加粗且以序号开头的小标题：**1) 反驳对方最强的三条论据**
# 限定"整行加粗 + 序号开头 + 正文 ≤40 字"，避免把一句加粗的短论断也放过去。
_BOLD_HEAD = re.compile(
    r"^\*{2}\s*(?:\d{1,2}|[一二三四五六七八九十]+|[IVXivx]{1,4})\s*[).、:：]\s*"
    r"[^*]{0,40}\*{2}[：:]?$")
# 小标题不一定带序号。真机第六跑写的是光秃秃的 **反驳** 和 **直面不利证据**。
# 判据：整行加粗 + 正文 ≤14 字 + **不含任何数字**。
# 加"不含数字"这一条是为了不给论断开后门——"**毛利率跌破60%**" 有数字，
# 仍然要出处；而一个既没有数字又不到 14 字的整行加粗，本来就没携带信息。
_BOLD_LABEL = re.compile(r"^\*{2}\s*([^*\d]{1,14})\s*\*{2}[：:]?$")

# ---- 引述对方原话 ----
# Round 2 要求"挑出对方最强的 3 条逐条反驳"，模型于是先整行引述对方那句话，
# 再在下一行反驳。被引述的那句是【对方】的主张（且在 Round 1 已核验过），
# 不该要求引述者再给一次出处。
# 但这里不能只看"有没有引号"就放过——那等于给了模型一条免检通道：
# 编一句对方没说过的话，加上引号，就绕开核验。
# 所以真去比对：引号内容必须确实出自对方文本。**核不上的比没引用更严重**，
# 单独标成「⚠引述失实」——伪造对方论点会让整场辩论失去意义。
_QUOTED = re.compile(r"[“\"「『]([^”\"」』]{8,})[”\"」』]")
# 引述不一定带引号。真机上模型是这么写的：
#     1. **市盈率约42倍（E/P = 2.38%）显示估值偏高，存在回调空间。**
# 整行加粗、序号开头、没有引号——但它同样是在【复述对方的主张】，
# 只不过换了个标记方式。所以判据不能钉在标点上，要钉在
# **"这一行是不是结构上被标成了转述"** + **"内容是不是真的出自对方"**。
_RESTATED = re.compile(r"^(?:[-–—•]\s*|\d{1,2}[.)、]\s*)*\*{2}\s*(.+?)\s*\*{2}\s*$")

# 提示词里写着「若某条你无法反驳，就明确写『此点成立，我方承认』」。
# 让步句按定义没有新论据；核验器若给它打 ⚠，就是在惩罚提示词鼓励的行为。
_CONCEDE = re.compile(r"(此点成立|我方承认|无法反驳|我方无法|确实成立|承认这一点|"
                      r"该点我方不作反驳)")


# ---- 年份核验 ----
# 真机第四跑，催化剂写的是"**2024年**第一季度财报公布将进一步验证增长"，
# 句末标了 [面板]——【形式上完全合规】：面板引用是有效溯源，核验器放行。
# 但面板里根本没有 2024，as_of 是 2026-05-20。这是一个纯粹凭空的年份。
#
# 这暴露了引用核验的一个边界：**它核对的是"有没有出处"，不是"出处里有没有这句话"**。
# 全面核对内容需要逐句比对，误报会很多（"E/P 2.38% → P/E 约 42" 是合法推导，
# 42 不在面板里也不该被标）。所以只收最窄、最高精度的一类：
# **年份**——它不可能是推导出来的，凭空出现就是编的。
_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")


# ---- 同业比较核验 ----
# 规则定义在 debate.py（unit_a → debate 是既有的 import 方向），
# 这样辩论正文与催化剂/失效条件用的是**同一套判据**，不会两处各自漂移。
from .debate import _PEER_CLAIM, _sign_error, has_peer_stats   # noqa: E402


def _bad_years(line: str, corpus: str) -> list:
    """句中出现、但面板与材料里都没有的年份。"""
    if not corpus:
        return []
    return [y for y in dict.fromkeys(_YEAR.findall(line or "")) if y not in corpus]


def _is_table_head(s: str) -> bool:
    """markdown 表头行：`| 对方论点 | 我方反驳（仅引用面板数据） |`。

    它是表格的栏目名，不是论断，却因为没有引用被逐行核验器标成 ⚠未核实。
    判据刻意窄：首尾是 |、单元格都短、且【没有任何数字】——
    有数字的行就是数据行，必须照常要求出处。
    """
    if not (s.startswith("|") and s.endswith("|") and s.count("|") >= 3):
        return False
    if re.search(r"\d", s) or _PANEL_CITE.search(s):
        return False
    cells = [c.strip() for c in s.strip("|").split("|")]
    # 上限从 16 提到 24：真机上出现过
    #   | 对方论点 | 我的回应（为何不成立或影响被高估） |
    # 第二格 17 字，只因为一个字超限就被当成论断。
    # 真正的判据是**没有数字、没有引用标记**——栏目名不携带可核对的量；
    # 长度只是防止把一整句话误当表头，24 字对栏目名已经很宽了。
    return bool(cells) and all(len(c) <= 24 for c in cells)


def _bigrams(s: str) -> set:
    s = re.sub(r"\s+", "", s or "")
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _quote_check(line: str, source: str, min_cover: float = 0.60):
    """判断本行是不是【忠实引述】source 里的话。

    返回 None（本行不是引述）/ True（引述属实）/ False（引述失实）。
    用字符二元组覆盖率比对，理由与失效条件复检相同：中文没有空格，
    整段短语做 token 永远对不上（见 thesis_store 里那个坑）。
    """
    line = line or ""
    m = _QUOTED.search(line)
    if m:
        q = m.group(1).strip()
        # 引号内容必须是这行的主体，否则只是句中带引号的普通论述，仍需引用
        if len(q) < max(8, 0.5 * len(re.sub(r"[\s*\-–—•]", "", line))):
            return None
    else:
        m2 = _RESTATED.match(line.strip())      # 整行加粗的转述（可带序号/项目符号）
        if not m2 or len(m2.group(1).strip()) < 8:
            return None
        q = m2.group(1).strip()
    if not source:
        return False
    qb = _bigrams(q)
    if not qb:
        return None
    return (len(qb & _bigrams(source)) / len(qb)) >= min_cover


def _verify_citations(text: str, n_materials: int, quote_source: str = "",
                      corpus: str = "", weak_ids: set | None = None,
                      peer_stats: bool = True) -> tuple[str, int]:
    """逐条核验论据：作出断言的行必须引用【材料编号 [1..N]】或【面板 [面板]】；
    否则打「⚠未核实」。返回 (处理后文本, 未核实条数)。

    两类引用都算有效溯源，但性质不同：
      [1..N] 指向采集到的原始材料，可点链接核对
      [面板] 指向本轮确定性生成的量化证据面板，数字可复算

    三类行不参与核验，因为它们并未作出需要出处的断言：
      · 结构行（分隔线 / markdown 标题 / 表格分隔 / 【小节】/ 加粗序号小标题）
      · 忠实引述对方原话的行（对方的主张，其出处属于对方）
      · 明确表示无法反驳的让步句（"此点成立，我方承认"）——提示词鼓励写它，
        核验器却罚它，就等于在惩罚诚实。

    quote_source：对方本轮之前的文本。缺省为空 → 任何引述都无法核实，
    一律按「⚠引述失实」处理，宁可错杀不放过伪造。

    corpus：面板全文 + 材料全文。用于年份核验——句末标了 [面板] 只证明
    "声称有出处"，不证明"出处里真有这句话"。年份是唯一不可能被推导出来的量，
    所以单收这一类：凭空出现的年份就是编的。

    peer_stats：本轮面板里是否真有同业统计量。为 False 时，任何
    「远高于行业平均」式的比较都会被标——面板里没有同业数字，
    这句话就没有出处，哪怕它规规矩矩地标了 [面板]。

    weak_ids：被材料闸门判为【无实质】的材料编号。引用它们**不算未核实**
    （出处是真的），但要单独标出来——这正是 CEO 定的证据层级最容易被绕过的地方：
    "市场预期 NVDA 将再次超出财报预期 [2]" 形式上完全合规，
    而 [2] 只是一条"财报前瞻"标题。不标出来，读者无从分辨
    哪些论据站在事实上、哪些站在标题上。
    """
    out, bad = [], 0
    for ln in (text or "").split("\n"):
        s = ln.strip()
        if not s:
            continue
        if (_STRUCT.fullmatch(s) or _BOLD_HEAD.fullmatch(s)
                or _BOLD_LABEL.fullmatch(s) or _is_table_head(s)):
            out.append(s)
            continue
        if _CONCEDE.search(s):                  # 让步句：不惩罚诚实
            out.append(s)
            continue
        cites = [int(x) for x in _CITE.findall(s)]
        ok_mat = any(1 <= c <= n_materials for c in cites) and n_materials > 0
        ok_panel = bool(_PANEL_CITE.search(s))
        qc = _quote_check(s, quote_source)
        if qc is True:
            out.append(s)                       # 忠实引述对方原话
            continue
        # 「引述失实」只适用于**没有出处**的转述行。
        # 一句带了有效引用的加粗断言（"- **近一年最大回撤 -20.21%【面板】**"）
        # 不是在冒充引述对方，它是有据可查的自己的话——
        # 把它标成"伪造对方论点"是把正确行为当成造假，
        # 比漏标更坏：核验器一旦开始惩罚合规行为，这个信号就废了
        # （build62 那 32 条 ⚠ 就是这么来的）。
        if qc is False and not (ok_mat or ok_panel):
            bad += 1
            out.append("⚠引述失实：" + s)       # 对方没说过这句 —— 比无出处更严重
            continue
        if ok_mat or ok_panel:
            yrs = _bad_years(s, corpus)
            weak = sorted(set(cites) & (weak_ids or set()))
            sign = _sign_error(s)
            # 顺序即严重度。**方向错误排第一**：缺出处只是无法核实，
            # 而把"贵"读成"便宜"会直接翻转结论——数字对、出处对、结论反。
            if sign:
                bad += 1
                out.append(f"⚠方向错误（{sign}）：" + s)
            elif yrs:                           # 有出处，但年份是编的
                bad += 1
                out.append(f"⚠年份存疑（{'、'.join(yrs)} 未见于面板与材料）：" + s)
            elif (not peer_stats) and _PEER_CLAIM.search(s):
                bad += 1                        # 拿一个面板里不存在的行业基准做比较
                out.append("⚠无同业基准（本轮面板不含任何同业统计量）：" + s)
            elif weak:                          # 出处是真的，但那条材料没有实质内容
                out.append(s + f"　〔据无实质材料 {'、'.join(f'[{c}]' for c in weak)}〕")
            else:
                out.append(s)                   # 有有效引用 → 保留
        else:
            bad += 1
            out.append("⚠未核实：" + s)         # 无法溯源 → 醒目标记（不静默）
    return "\n".join(out) or "（无有据可查的论据）", bad



def _parse_verdict(text: str) -> tuple[str, str, str]:
    m = _VERDICT.search(text or "")
    if m:
        return m.group(1), m.group(2), m.group(3)
    t = text or ""
    direction = "看多" if ("看多" in t or "做多" in t) else "看空" if ("看空" in t or "回避" in t) else "中性"
    return direction, "中", "观望"


# ---------------- 材料清洗（治 garbage-in）----------------
# 跨域噪音：个股基本面分析里，把只顺带提一句标的的加密货币等无关领域剔掉
_OFFTOPIC = re.compile(r"(Hyperliquid|加密货币|比特币|crypto|bitcoin|订单簿|区块链|blockchain|挖矿|meme\s?coin|NFT|山寨币)", re.I)
# 乱码/字段转储：轻模型偶发吐出"数字：数字 公司名称：… 股票代码：…"这类垃圾
_GARBLED = re.compile(r"(数字[：:]\s*数字|公司名称[：:]|股票代码[：:]|媒体/机构名称[：:])")
# 字段名前缀：轻模型偶发把字段名当正文吐出来（如"数字：ICBC…"），从译文头部剥掉
_FIELD_PREFIX = re.compile(r"^\s*(数字|公司名称|股票代码|媒体/机构名称|标题|摘要|正文|内容)\s*[：:]\s*")
# 中文名 → 英文/代码别名（跨语言相关性匹配；翻译常把工行错成中行，用英文原标题兜底）
_ALIASES = {
    "工商银行": ["ICBC", "Industrial and Commercial Bank"],
    "农业银行": ["ABC", "Agricultural Bank of China"],
    "中国银行": ["Bank of China", "BOC"],
    "建设银行": ["CCB", "China Construction Bank"],
    "交通银行": ["BOCOM", "Bank of Communications"],
    "邮储银行": ["PSBC", "Postal Savings Bank"],
    "苹果": ["Apple", "AAPL"], "英伟达": ["NVIDIA", "NVDA"], "特斯拉": ["Tesla", "TSLA"],
    "台积电": ["TSMC", "TSM"], "微软": ["Microsoft", "MSFT"],
}
# 易混公司的中文标记（含简称）——用于识别"张冠李戴"的错译（工行被译成中行/中银等）
_CONFUSABLE = {
    "工商银行": ["工商银行", "工行", "ICBC"],
    "中国银行": ["中国银行", "中行", "中银"],
    "农业银行": ["农业银行", "农行"],
    "建设银行": ["建设银行", "建行"],
    "交通银行": ["交通银行", "交行"],
    "邮储银行": ["邮储银行", "邮储"],
    "招商银行": ["招商银行", "招行"],
}


_CJK = re.compile(r"[一-鿿]")


def alias_hit(alias: str, text: str) -> bool:
    """标的别名是否**作为一个词**出现在文本里。

    ## 为什么不能用子串

    真机上 ARM 的相关性闸放进来这么几条：

        Venezuelan opposition up in **arms** over US oil stake
        Reality check for China's c**arm**akers
        Ph**arm**a stocks slide on tariff threat
        Al**arm** bells for chip supply chains

    委内瑞拉的石油新闻被当成 ARM 的材料，还被判成**实质**——
    真跑一部的话，多空辩论会拿它当论据。

    三四个字母的 ticker 全有这个问题：ARM / MU / KLA / AI / ON / IT。
    子串匹配对它们等于没有匹配。

    ## 中文别名仍然用子串

    中文没有空格，"工商银行"出现在"中国工商银行公告"里就是子串关系，
    加词边界反而会漏。所以按别名本身的语种分流。
    """
    a = (alias or "").strip()
    if not a or not text:
        return False
    if _CJK.search(a):
        return a in text
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(a)}(?![A-Za-z0-9])",
                     text, re.I) is not None


# ---------------------------------------------------------------- 符号消歧
AMBIGUOUS_SYMBOLS = frozenset({
    "ARM", "ON", "IT", "AI", "ALL", "KEY", "CAT", "GPS", "LOW", "NOW", "OPEN",
    "PLAY", "RUN", "FAST", "REAL", "HOPE", "LOVE", "EAT", "MOVE", "NICE",
    "PATH", "PLUS", "POST", "SAFE", "SAVE", "TRUE", "WIRE", "GOOD", "BIG",
    "NEW", "WELL", "SO", "BE", "DD", "EW", "MA", "F", "GM", "LUV", "TAP",
})
"""**同时是常用英文词的 ticker。** 对它们，裸符号匹配等于没有匹配。

这是一份判断，不是真理 —— 会漏。所以下面还有一层不依赖名单的兜底。
"""

# 只有股票引用才会取的形态。裸词永远长不成这样。
_SYMBOL_ID = (
    r"\(\s*{s}\s*\)"                       # (ARM)
    r"|(?:NASDAQ|NYSE|AMEX|OTC|LSE)\s*:\s*{s}"
    r"|{s}\s*:\s*(?:US|LN|UN|UQ)"
    r"|\${s}"                              # $ARM
    r"|{s}['’]s"                           # ARM's
    r"|{s}\s+(?:stock|shares|stocks|corp|corporation|inc|plc|ltd|holdings|"
    r"group|technologies|semiconductor|earnings|q[1-4])"
)


def symbol_hit(sym: str, text: str) -> bool:
    """ticker 符号是否作为**股票引用**出现，而不是碰巧同形的英文词。

    ## 真机上 ARM 的 10 条材料里有 4 条完全无关

        Current ARM mortgage rates report for Aug. 31, 2026    浮动利率房贷
        Multiple crews battle 2-alarm fire in Glen Arm          地名
        Mom Who Had Arm Amputated After Shark Attack            身体部位
        Guggenheim buys debt linked to its asset management arm 部门

    build95 把子串改成词边界，挡住了 `arms` / `pharma` / `carmakers`；
    但 **ARM 本身就是一个英文单词**，词边界对它无能为力。
    这四条不只是噪音——每只标的只有 10 个进闸门的名额，
    它们**挤掉了真材料**（当天 26 条相关材料里有 16 条根本没进闸门）。

    ## 两层，第二层不依赖任何名单

        一  名单内的符号（ARM / ON / IT / AI …）→ 裸匹配一律不认，
            必须出现 (ARM) / NASDAQ:ARM / ARM's / ARM stock 这类身份形态
        二  **大小写对不上的裸匹配一律不认** —— 不需要名单：
            公司符号在正文里写作 ARM，写成 "Arm" / "arm" 的多半是普通词
            （Glen Arm / asset management arm / Arm Amputated）

    第二层是兜底：名单漏掉的新标的，只要它撞的是小写常用词，照样挡得住。

    **代价写在这里，免得以后当成 bug 去"修"：** 公司全名仍然照常匹配
    （"Arm Holdings" 是另一个别名），所以损失只是**只提了裸符号、
    连一次身份形态都没有**的那些条目，例如
    "IBM Introduces Processor With Arm Architecture" —— 那条讲的是 IBM。
    """
    s = (sym or "").strip()
    if not s or not text:
        return False
    pat = _SYMBOL_ID.format(s=re.escape(s))
    if re.search(pat, text, re.I):
        return True
    if s.upper() in AMBIGUOUS_SYMBOLS:
        return False
    # 兜底：裸匹配必须**大小写完全一致**
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(s)}(?![A-Za-z0-9])",
                     text) is not None


def _aliases(info: dict) -> list[str]:
    al = [info.get("resolved", "")] + (info.get("queries") or []) + _ALIASES.get(info.get("resolved", ""), [])
    if info.get("symbol"):
        al.append(info["symbol"])
    return [a for a in dict.fromkeys(x for x in al if x and len(str(x)) >= 2)]


def _clean_zh(t: str) -> str:
    """剥掉轻模型吐出的字段名前缀（数字：/公司名称：…），最多剥 3 层。"""
    t = (t or "").strip()
    for _ in range(3):
        m = _FIELD_PREFIX.match(t)
        if not m:
            break
        t = t[m.end():].strip()
    return t


def _wrong_company(zh: str, orig: str, subj: str) -> bool:
    """张冠李戴检测：译文里出现【别的公司】的中文名（如工行被译成中行/中银），
    而英文原文其实是本标的 → 判定为错译，应退回英文原标题。"""
    if not zh:
        return False
    self_marks = _CONFUSABLE.get(subj, [subj])
    orig_l = orig.lower()
    self_in_orig = any(m in orig for m in self_marks) or any(
        a.lower() in orig_l for a in _ALIASES.get(subj, []))
    if not self_in_orig:
        return False                                  # 原文都不确定是本标的，不轻易判错
    for name, marks in _CONFUSABLE.items():
        if name == subj:
            continue
        if any(m in zh for m in marks):               # 译文点名了另一家银行…
            other_en = _ALIASES.get(name, [])
            if name in orig or any(a.lower() in orig_l for a in other_en):
                continue                              # …但原文确实也提到了它 → 不算错译
            return True                               # …原文没提它 → 错译（张冠李戴）
    return False


MATERIAL_POOL = 40
"""**清洗之后**的候选池上限：多少条相关材料参与补正文与实质度排序。

## 这个常量原来砍错了地方

老代码是这样的：

    pool = sorted(news, key=score)[:MATERIAL_POOL]    # 先按相关性砍到 40
    kept_pool = _prefilter(pool, info)                # 再做相关性清洗

**先砍再筛。** 真机 8/31 的 ARM：

    去重 55 → 池 40（砍掉 15，没有任何人看过它们）→ 清洗 → 相关 10

活下来进入清洗的那 40 条里，有 30 条随即被判为不相关丢掉了——
**池子的 40 个名额，四分之三花在了马上就要扔掉的东西上**；
与此同时 15 条从未被检查过的材料被直接丢弃，其中可能有相关的、
甚至有实质的。而进料行上一个字都没提这一刀。

这和 build91 修的是同一个缺陷（"按相关性截断 → 才判实质度"），
只是在管道的另一端，而我当时没看见它。

## 现在的顺序

    去重 → 清洗（全部，正则，便宜）→ 相关 N → 池上限 → 补正文 → 排序 → CAP

于是 40 个名额全部给**已经确认相关**的材料。真机 ARM 上这一刀因此
根本不会触发（相关材料只有十几条），它退回成一个真正的安全上限。

## 为什么池子这一刀不能按实质度排

CAP 那一刀是**补完正文之后**才切的，所以可以实质优先。
池子这一刀在补正文**之前**——此时判定只能看标题，而
build91 修的正是"标题含糊、事实在正文里"那一类。按标题级实质度排序，
恰恰会把这类材料排到最后先砍掉，等于把 build91 的缺陷倒着重做一遍。

所以这一刀仍然按相关性切，但**必须报出来**，并写明这些条目
**未经实质度判定**——盲砍可以，装作没砍不行。
"""

MATERIAL_CAP = 10
"""最终进闸门的材料条数。"""


EDGAR_WINDOW_DAYS = 7
"""一部只收**这个窗口内**提交的公告。

不设窗口的话，SEC 那个接口永远返回"最近 8 份"——**和提交日期无关**。
于是每只票每天都能拿到 8 份公告，闸门每天都判"材料充分"，
evidence-triggered 的研究退化成每日评论台，而这正是闸门要防的东西。

真机上接入 EDGAR 当天，10 只票全部变 SUFFICIENT、实质材料从 4% 跳到 57%——
**看起来像大成功，其实是闸门被拆了**。

7 天：8-K 要求在事件发生后 4 个工作日内提交，一周足以覆盖，
又不至于把上个季度的 10-Q 当成今天的新闻。
"""

BASIS_ENRICH_N = 25
"""判定之前给多少条相关候选补正文。

比最终的 10 条大，是因为**判定要在截断之前发生**——名额花在
"哪十条能被看见"这个决定上，才有意义。
"""

BASIS_BODY_CHARS = 400
"""判定依据里取多少正文。标题常常只是"某公司据称将…"，
正文头几百字才有已发生的动作和硬锚点。"""


def basis_text(n) -> str:
    """一条新闻的**实质度判定依据**：原始标题 + 正文片段。**不含任何模型产物。**

    排序（截断前）和闸门（截断后）都调这一个函数，所以两边看的是同一段文本。

    上一版排序只看标题、闸门看"标题 + 模型摘要"，于是两边在**两个方向上**
    都出现过分歧：AVGO 排序判实质、闸门判不是；AMD/LRCX 排序判不是、
    闸门原本判是——于是修完截断反而把它们的实质材料挤掉了。
    同一件事必须由同一段文本决定。
    """
    t = (getattr(n, "title_original", "") or getattr(n, "title_zh", "") or "").strip()
    body = (getattr(n, "body", "") or "").strip()
    return (t + ("\n" + body[:BASIS_BODY_CHARS] if body else "")).strip()


def _rank_by_substance(news: list) -> tuple:
    """按**实质度优先、相关性其次**排序。返回 (排好序的列表, 分档计数, id→档位)。

    ## 这个函数在修什么

    原来的顺序是：按相关性打分排序 → 截断到 10 条 → 交给闸门判实质度。

    于是一条**真正有增量事实**的材料，只要相关性排在第 11 位，
    就永远到不了闸门。而报告上写的是「10 条材料，实质 0」——
    读起来是"今天确实没东西"，实际是"我们只看了其中 10 条"。
    **截断本身不可见，被截掉的东西也不可见。**

    真机上的证据：TSM / ARM / KLAC / AMD 四只全是整 10 条 —— 那不是巧合，
    是撞上了上限。

    现在把实质度判定提到截断**之前**：分类是纯正则、零 LLM，
    多判三十条的成本可以忽略，而它决定的是哪十条能被看见。

    ## 一条纪律

    这里调的是 `material_gate.classify` **本身**，不是另写一套近似规则。
    两套规则一定会漂移，而漂移之后没人知道以哪个为准——
    尤其这一套只在"排序"里悄悄生效，漂了也不会有人发现。

    ## 排序与闸门看同一段文本

    两边都调 `basis_text(n)`（原始标题 + 正文片段，**不含模型产物**）。

    上一版不是这样：排序只看标题、闸门看"标题 + 模型摘要"。我当时把它
    写成"一个已知的近似"，真机数据证明那句话太轻描淡写了——
    两个方向的分歧都出现了：AVGO 排序判实质而闸门判不是；
    AMD/LRCX 排序判不是而闸门原本判是，于是**修完截断反倒把它们的
    实质材料挤掉了**。一个只在排序里悄悄生效的近似，
    刚好就是没人会发现的那种。
    """
    order = {material_gate.SUBSTANTIVE: 0, material_gate.CONTEXT: 1,
             material_gate.EMPTY: 2}
    tiers: dict = {}
    tier_of: dict = {}
    pinned: dict = {}
    for n in news:
        src = (getattr(n, "sources", None) or [None])[0]
        sname = getattr(src, "name", "") if src else ""
        surl = getattr(src, "url", "") if src else ""
        basis = basis_text(n)
        tier, _why = material_gate.tier_of(basis, sname, surl)
        tier_of[id(n)] = tier
        tiers[tier] = tiers.get(tier, 0) + 1
        # **档位被规则钉死的排在同档最后。** 持股/交易申报按定义不可能
        # 触发闸门，而闸门名额只有 MATERIAL_CAP 条：真机 AMD 的 10 个名额
        # 里 3 个给了内部人申报，门外还有 20 条相关材料。普通新闻至少
        # 可能在补完正文后变成实质，钉死的不会。见 material_gate.never_substantive。
        pinned[id(n)] = 1 if material_gate.never_substantive(basis, sname, surl) else 0
    # 用下标兜底做最后一级键，避免同分时去比较对象本身（那会抛 TypeError）。
    idx = sorted(range(len(news)),
                 key=lambda i: (order.get(tier_of[id(news[i])], 3),
                                pinned[id(news[i])],
                                -float(getattr(news[i], "score", 0) or 0), i))
    return [news[i] for i in idx], tiers, tier_of


def intake_note(c: dict) -> str:
    """把"采集了多少、进闸门多少、丢了多少"讲清楚的一行。

    **没有这一行，「10 条材料，实质 0」读起来就是"今天没东西"**，
    而它真正的意思可能是"我们只看了其中 10 条"。
    """
    k = (c or {}).get("intake") or {}
    if not k:
        return ""
    # **一手披露要单独报"取了几条 / 活下来几条 / 进闸门几条"。**
    # 只报"取了 8 条"的话，它们在相关性闸那里被全部丢掉也看不出来——
    # 真机上就是这样，10 只票里 9 只的公告全没了，而进料行一切正常。
    ed = ""
    if k.get("edgar"):
        ed = (f"（EDGAR 一手披露 {k['edgar']} 条"
              f" → 过相关性 {k.get('edgar_kept', 0)}"
              f" → 进闸门 {k.get('edgar_in_gate', 0)}）")
        if not k.get("edgar_kept"):
            ed = ed[:-1] + "　⚠ **公告全部未通过相关性闸**）"
    # **符号消歧丢了多少，和截断丢了多少一样要报。**
    # ARM 上线当天相关材料从 26 条掉到 9 条，而这一行写的是"相关 9"——
    # 读起来和"今天这只票没什么新闻"一模一样。过滤器越狠，
    # 输出越像"世界很安静"，这是本项目反复撞上的形状。
    #
    # **四个丢弃原因全部打印。** build98 只印了「符号消歧」那一个，
    # 另外三个（标题无标的 / 跨域噪音 / 标题党）收进 `dropped_by` 就扔了。
    # 后果立刻就来了：真机 ARM 有 18 条丢弃完全没有说明，而那 18 条里
    # 到底有没有一条真材料被 `is_noise` 当成标题党杀掉，**没有任何地方看得见**。
    # 收了不印和没收是一回事。
    by = dict(k.get("dropped_by") or {})
    sd = k.get("dropped_symbol") or by.get(DROP_SYMBOL, 0) or 0
    by.pop(DROP_SYMBOL, None)
    rest = "、".join(f"{r} {c}" for r, c in
                     sorted(by.items(), key=lambda kv: -kv[1]) if c)
    sym_note = ""
    if sd:
        share = sd / (sd + k["relevant"]) if (sd + k["relevant"]) else 0.0
        sym_note = f"（符号消歧丢弃 {sd} 条，占 {share:.0%}"
        sym_note += "　⚠" if share >= SYMBOL_DROP_WARN_RATIO else ""
        sym_note += (f"；另：{rest}）" if rest else "）")
    elif rest:
        sym_note = f"（清洗丢弃：{rest}）"
    # 池上限这一刀切在补正文之前，**此时还没有人判过它们有没有实质内容**。
    # 老代码里这一刀切在清洗之前、而且从不打印：真机 ARM 去重 55 → 池 40，
    # 15 条从没被检查过就没了，进料行上一个字都没有。
    pc = k.get("pool_cut") or 0
    pool_note = (f" → 池上限 {k.get('pool_limit', '?')}"
                 f"（按相关性丢弃 {pc} 条，**未经实质度判定**　⚠）" if pc else "")
    s = (f"采集 {k['raw']} 条" + ed
         + f" → 去重 {k['scored']} → 相关 {k['relevant']}" + sym_note + pool_note
         + (f"（补正文 {k['enriched']} 条）" if k.get("enriched") else "")
         + f" → 前 {k['cap']} 条进闸门")
    if k["dropped"]:
        t = k.get("tiers_before_cap") or {}
        s += (f"（截掉 {k['dropped']} 条；相关材料分档 "
              + "、".join(f"{kk} {vv}" for kk, vv in t.items()) + "）")
        if k["dropped_substantive"]:
            s += f"　⚠ **被截掉的里面有 {k['dropped_substantive']} 条实质材料**"
    if sd and (sd / (sd + k["relevant"]) if (sd + k["relevant"]) else 0) \
            >= SYMBOL_DROP_WARN_RATIO:
        s += ("\n    ⚠ **这只票的候选主要是被符号消歧决定的**"
              "（裸符号撞上了英文词）—— `--verbose` 里有被丢掉的标题，"
              "扫一眼是不是丢了真材料。")
    return s


DROP_SYMBOL = "符号消歧"
DROP_NO_SUBJECT = "标题无标的"
DROP_OFFTOPIC = "跨域噪音"
DROP_CLICKBAIT = "标题党"

SYMBOL_DROP_SAMPLE = 8
"""进料行里最多留几条被消歧丢掉的标题（给 `--verbose` 打印）。"""

SYMBOL_DROP_WARN_RATIO = 0.5
"""消歧丢掉的比例超过这个数就在进料行上打 ⚠。

**为什么要有这条警告线。** build97 上线后 ARM 的相关材料从 26 条掉到 9 条——
我预期它挡掉 4 条噪音，实际挡掉 17 条。而进料行上写的是"相关 9"，
读起来和"今天这只票没什么新闻"**一模一样**。

这正是本项目反复撞上的那个形状：过滤器работа得越狠，输出越像"世界很安静"。
所以消歧的丢弃数必须单独报，比例过半还要主动喊一声。
"""


def _prefilter(news: list, info: dict, drops: dict | None = None) -> list:
    """材料清洗：① 相关性——标的（含中英别名）必须出现在【原始标题】，只在正文/译文顺带提及的丢；
    ② 跨域噪音（加密货币等）丢；③ 标题党（is_noise）丢。
    相关性只认原始标题：译文可能把工行错译成中行、或凭空带出标的名，不可作相关性依据。

    `drops` 传一个 dict 进来就会收到 `{丢弃原因: [标题…]}`。
    **不传也能跑**，但那样这一步丢了什么就没有任何人知道——
    调用方应当传。
    """
    sym = (info.get("symbol") or "").strip()
    al = [a for a in _aliases(info) if a != sym]   # 符号单独走 symbol_hit

    def _drop(reason: str, title: str) -> None:
        if drops is not None:
            drops.setdefault(reason, []).append(title)

    out = []
    for n in news:
        orig = n.title_original or ""
        ol = orig.lower()
        src = (getattr(n, "sources", None) or [None])[0]
        if src is not None and material_gate.is_primary(
                getattr(src, "name", ""), getattr(src, "url", "")):
            # **一手披露不过相关性闸。**
            #
            # 这些公告是**按 CIK 取回来的**——身份由 SEC 自己的公司标识确定，
            # 不需要再用字符串去猜。而相关性闸认的是"标的名出现在原始标题里"，
            # 公告标题用的却是公司**法定名称**：
            #
            #     AMD  → ADVANCED MICRO DEVICES INC 8-K   不含 "AMD"
            #     MU   → MICRON TECHNOLOGY INC 10-Q       不含 "MU"
            #     KLAC → KLA CORP 8-K                     不含 "KLAC"
            #     NVDA → NVIDIA CORP 8-K                  不含 "NVDA"
            #
            # 真机上 10 只票里 **9 只的公告全部被这一步丢光**，只有 ARM 侥幸
            # 活下来（"ARM" 恰好是 "Arm Holdings plc" 的子串）。
            # 而进料行只显示"采集 73 条（含 EDGAR 8 条）→ 相关 17"——
            # **看不出那 8 条已经全没了**。
            #
            # 相关性闸防的是新闻里"顺带提一句"的噪音；按 CIK 取回的公告
            # 根本不属于那一类。
            out.append(n)
            continue
        if not (any(alias_hit(a, orig) for a in al)
                or (sym and symbol_hit(sym, orig))):
            # 原始标题里没标的 → 顺带提一句的噪音。
            # **裸 ticker 单独走 symbol_hit**：ARM / ON / IT 本身就是英文词，
            # 词边界对它们无能为力，见 symbol_hit 的文档。
            #
            # 两种丢弃必须分开记：**裸符号确实作为一个词出现过**的，
            # 是 build97 消歧砍掉的那一刀（旧规则会留下它，所以这是本次
            # 改动的净影响，必须能被看见）；其余是普通的"顺带提一句"。
            _drop(DROP_SYMBOL if (sym and alias_hit(sym, orig))
                  else DROP_NO_SUBJECT, orig)
            continue
        if _OFFTOPIC.search(f"{orig} {n.title_zh or ''}"):
            _drop(DROP_OFFTOPIC, orig)            # 跨域噪音（原文或译文任一命中即剔）
            continue
        if getattr(n, "is_noise", False):
            _drop(DROP_CLICKBAIT, orig)           # 标题党
            continue
        out.append(n)
    return out


def _build_panel(info: dict):
    """为该标的构建固定量化证据面板。取不到行情就返回空面板并写明原因——
    绝不因为缺数据就让辩论在没有量化地基的情况下自由发挥。"""
    sym = info.get("symbol") or ""
    if not sym:
        return [], "（主题类标的，非个股：本轮无量化证据面板）"
    try:
        from . import quant_data
        from .analytics import load_cfg          # 仅取阈值窗口配置，不取二部任何结论
        yahoo = sym if not info.get("a_share") else (f"{sym}.SS" if sym[:1] == "6" else f"{sym}.SZ")
        st = quant_data.Stock(code=sym, name=info.get("resolved", ""), yahoo=yahoo)
        panels = quant_data.get_history([st], days=400)
        df = panels.get(sym)
        bench = quant_data.get_benchmark(days=400)
        fund, snap = {}, {}
        try:
            from . import fundamentals
            recs = fundamentals.load_universe_cached([st])
            fund = recs.get(sym) or {}
            if fund and df is not None and len(df):
                import pandas as pd
                as_of = pd.to_datetime(df["date"]).max().date()
                snap = fundamentals.snapshot(fund, as_of)
        except Exception as e:
            log.info("一部面板：基本面不可得（%s），仅出市场行为组", e)
        as_of_d = None
        if df is not None and len(df):
            import pandas as pd
            as_of_d = pd.to_datetime(df["date"]).max().date()
        cfg = (load_cfg() or {}).get("windows") or {}
        p = evidence.build_panel(sym, df, bench, fund, snap, as_of_d, cfg)
        return p, evidence.render_panel(p)
    except Exception as e:
        log.warning("一部面板构建失败：%s", e)
        return [], f"（量化证据面板本轮不可得：{e}）"


# 人工 override：即使 Gate=INSUFFICIENT 也强制跑一次完整研究。
# 场景是**有意的决定**——首次建仓前、季度复审、既有论点到期、重大决策前重新审视。
# 与自动日常运行严格区分：报告必须写明它依据的是既有证据集，不是新证据。
def _forced(explicit: bool = False) -> bool:
    return bool(explicit) or os.environ.get("UNIT_A_FORCE_RESEARCH") == "1"


def collect_materials(text: str) -> dict:
    """独立采集并清洗带编号的事实材料。**从 build_unit_a 原样抽出，逻辑一字未改。**

    抽出来是因为【采集 + 实质度判定】这一段完全确定性、零 LLM、不调模型，
    可以脱离辩论单独使用：先扫一批标的，看今天哪几只真的有增量事实，
    再只对那几只跑完整的一部。

    一部按定义就是 evidence-triggered 的——"不是每日评论台"。
    这个函数就是那个 trigger 的判据来源。
    """
    info = topic.parse_subject(text)
    subj = info["resolved"]
    status_u: dict = {}

    # 1) 独立采集客观事实材料（不读 CIO 结论/二部；只拿原始事实）
    raws: list = []
    region = "china" if info.get("a_share") else "international"
    for q in (info.get("queries") or [])[:2]:
        raws += collect.fetch_google_news(q, region, status_u)
    en = topic.THEME_EN.get(subj) or (info["symbol"] if info.get("symbol") and not info.get("a_share") else "")
    if en:
        raws += collect.fetch_google_news(en, "international", status_u)
    raws += collect.scan_rss_for_subject([subj] + (info.get("queries") or []), status_u, limit=15)
    n_edgar = 0
    if info.get("symbol") and not info.get("a_share"):
        raws += collect.fetch_yahoo_ticker(info["symbol"], status_u)
        # **一手披露。** dossier 和 topic 早就在取 EDGAR，唯独一部没取——
        # 而一部是最需要"已发生的事实"的那一个。8-K/10-Q 是公司依法必须
        # 提交的重大事件披露，按定义就是增量事实；Google News 给的是评论。
        try:
            cik = topic._get_cik(info["symbol"])
            if cik:
                before = len(raws)
                raws += collect.fetch_edgar_recent(
                    cik, status_u, within_days=EDGAR_WINDOW_DAYS)
                n_edgar = len(raws) - before
        except Exception as e:                               # noqa: BLE001
            log.info("EDGAR 取不到（不影响其余材料）：%s", e)
    try:
        collect.enrich_fulltext(raws, top_n=10)
    except Exception:
        pass
    news, _ = process.dedupe_and_score(raws)
    n_scored = len(news)
    # **先清洗，再排序，最后才截断。** 顺序很重要，见 _rank_by_substance。
    #
    # 清洗跑在**全部**去重结果上，不再先按相关性砍到 MATERIAL_POOL——
    # 那样池子的名额会大半花在马上要被判为不相关的条目上，
    # 同时把从没检查过的材料直接扔掉。见 MATERIAL_POOL 的文档。
    drops: dict = {}
    relevant = _prefilter(news, info, drops)   # 相关性 + 跨域噪音 + 标题党
    n_relevant = len(relevant)
    # 池上限：补正文与判定的工作量上限，切在**已确认相关**的材料上。
    # 仍然按相关性切（此时正文还没取，实质度判不准，见文档），
    # 但切掉多少必须报出来。
    kept_pool = sorted(relevant, key=lambda n: n.score,
                       reverse=True)[:MATERIAL_POOL]
    n_pool_cut = n_relevant - len(kept_pool)
    sym_dropped = drops.get(DROP_SYMBOL) or []

    def _is_primary_item(n) -> bool:
        src = (getattr(n, "sources", None) or [None])[0]
        return bool(src) and material_gate.is_primary(
            getattr(src, "name", ""), getattr(src, "url", ""))
    n_edgar_kept = sum(1 for n in kept_pool if _is_primary_item(n))
    # **判定之前先给相关候选补正文。** 标题常常只是"某公司本周受关注"，
    # 正文头几百字里才有已发生的动作和硬锚点。名额花在清洗之后的候选上，
    # 而不是清洗之前的一堆将被丢掉的条目上。
    n_body = 0
    try:
        n_body = collect.enrich_news_fulltext(kept_pool, BASIS_ENRICH_N)
    except Exception as e:                               # noqa: BLE001
        log.info("补正文失败（判定退回只看标题）：%s", e)
    ranked, tiers, tier_of = _rank_by_substance(kept_pool)
    news = ranked[:MATERIAL_CAP]
    dropped = ranked[MATERIAL_CAP:]
    n_dropped_sub = sum(1 for n in dropped
                        if tier_of.get(id(n)) == material_gate.SUBSTANTIVE)
    intake = {"raw": len(raws), "scored": n_scored, "pool": len(kept_pool),
              # 池上限砍掉多少必须报。这些条目**未经实质度判定**——
              # 盲砍可以，装作没砍不行。
              "pool_cut": n_pool_cut, "pool_limit": MATERIAL_POOL,
              "relevant": n_relevant, "cap": MATERIAL_CAP, "kept": len(news),
              "dropped": len(dropped), "dropped_substantive": n_dropped_sub,
              "enriched": n_body, "edgar": n_edgar,
              "edgar_kept": n_edgar_kept,
              "edgar_in_gate": sum(1 for n in news if _is_primary_item(n)),
              "tiers_before_cap": tiers,
              # **相关性闸丢了什么，必须和截断丢了什么一样可见。**
              # 截断有 dropped/dropped_substantive 报了好几个 build 了；
              # 相关性闸一直是完全的盲区——而 build97 的符号消歧
              # 一刀砍掉了 ARM 三分之二的候选，进料行上却只显示"相关 9"。
              "dropped_by": {k: len(v) for k, v in drops.items()},
              "dropped_symbol": len(sym_dropped),
              "dropped_symbol_titles": sym_dropped[:SYMBOL_DROP_SAMPLE],
              # **每个原因都留样本，不只是符号消歧。**
              # 「标题党」那一闸是 `is_noise` 判的，它有可能把一条真材料
              # 当标题党杀掉——而在有样本之前，那件事在任何输出里都看不见。
              "dropped_samples": {r: v[:SYMBOL_DROP_SAMPLE]
                                  for r, v in drops.items()
                                  if r != DROP_SYMBOL}}
    try:
        process.hydrate(news[:8])
    except Exception:
        pass
    # 带编号采集材料（论据只能引用这些；便于零幻觉逐条核验）
    # 质量门：以【原始标题】为忠实骨架（源头原话，任何语言都无翻译层出错、绝不乱码），
    # 中文仅作「注释」附加——摘要/标题译文须过「剥字段前缀 + 非乱码 + 无张冠李戴」三关，
    # 否则不附。直接治 数字：前缀 / 工行→中行 错译 / 香料航空-穆姆木 乱码专名。
    def _zh_ok(s: str) -> bool:
        return bool(s) and not _GARBLED.search(s) and not _wrong_company(s, orig, subj)

    materials: list[MaterialItem] = []
    for n in news:
        orig = (n.title_original or "").strip()
        summ = _clean_zh(n.summary_zh)
        zh_title = _clean_zh(n.title_zh)
        gloss = summ if _zh_ok(summ) else (zh_title if _zh_ok(zh_title) else "")   # 优先摘要，其次干净标题译文
        if orig:
            txt = orig + ("：" + gloss if gloss else "")          # 英文/原文锚 + 干净中文注释
        else:
            txt = gloss or zh_title                               # 原文缺失才退译文
        txt = truncate((txt or "").strip("：: "), 120)
        if not txt:
            continue
        src = n.sources[0] if n.sources else None
        materials.append(MaterialItem(
            id=len(materials) + 1, text=txt,
            # **判定依据 = 源头文本**（原标题 + 正文），与截断前排序用的完全一致。
            # `text` 里那句摘要是模型生成的，只给人读，不参与判定。
            basis_text=basis_text(n),
            source_name=(src.name if src else ""), source_url=(src.url if src else "")))
    out = {"info": info, "subj": subj, "materials": materials,
           "news": news, "raws": raws, "status": status_u, "intake": intake}
    # **进料口径进日志。** 只印"N 条材料"的话，"确实没东西"和
    # "只看了其中 N 条"在日志里长得一模一样。
    stage("collect", f"{len(materials)} 条材料｜" + intake_note(out))
    return out


def build_unit_a(text: str, force: bool = False) -> UnitAAdvice:
    c = collect_materials(text)
    info, subj = c["info"], c["subj"]
    materials, news, raws = c["materials"], c["news"], c["raws"]
    status_u = c["status"]
    status_s: dict = {}

    facts_text = "\n".join(f"[{mi.id}] {mi.text}" for mi in materials) or "（公开免费源暂无相关材料——无材料则不得编造，辩论以行情/回测为主）"

    # 1b) 材料实质度闸门：判定这批材料到底有没有增量事实。
    #     首跑 8 条材料全是"财报前瞻"标题，辩论因此完全落回量化面板，
    #     而报告读起来却像有基本面依据——闸门不解决数据源，
    #     它保证【报告不假装自己有它没有的证据】。详见 material_gate 模块头。
    gate = material_gate.assess(materials)
    _ev = gate.get("n_sub_events", gate["n_sub"])
    stage("gate", f"{gate['level']}（{gate['verdict']}，实质 {gate['n_sub']}/{gate['n']} 条"
                  + (f"，归并为 {_ev} 个事件" if _ev != gate["n_sub"] else "") + "）")

    # 2) 固定量化证据面板（确定性生成；**不 import 二部的 analytics**，只调共享 measures 层）
    #    面板预先定死，多空双方拿到【同一张表的全部内容】——
    #    这是不让 LLM 从 462 个 alpha 里挑对自己有利那撮的唯一办法。
    panel, panel_text = _build_panel(info)
    stage("panel", "量化证据面板就绪")

    # 2b) Evidence Gate —— 决定要不要启动一部。
    #     **没有新的可解释信息，就不制造新的观点。**
    #     0 条实质材料时跑辩论，实际发生的是两个模型拿二部已经算好的数字重讲一遍故事：
    #     没有新的 information set，没有真正的因果推理。而且同一批不变的数字每天重跑，
    #     今天看多|中、明天中性|弱——那不是市场在变，是采样噪声。
    #     跳过反而更可靠，也让 thesis 台账干净：没有新实质信息就不该产生新 thesis。
    forced = _forced(force)
    if not gate["activate"] and not forced:
        # 闸门拦下：整条链到此为止，**发一个明确的终止事件**。
        # 界面上"停在第 2 步不动"和"第 2 步之后主动结束"看起来一样，
        # 前者是卡死，后者是系统正常工作——这两件事必须能分辨。
        stage("gate_blocked", "无实质材料，一部不启动（Formal vote: ABSTAIN）")
        out = _not_activated(text, subj, info, gate, panel, panel_text,
                             materials, news, raws, status_u)
        stage("done", "未启动")
        return out

    # 3) 对抗式辩论：Round1 独立建案（互不可见防锚定）→ Round2 交换反驳并直面不利证据
    #    → Judge 只做论证审计 → Synthesizer 出一部观点（到"方向+论证"为止，不给仓位）
    # **辩论跑在哪个引擎上，只有 `llm.engine()` 这一处决定。**
    # 不设 CIO_DEBATE_ENGINE 就还是本地 gpt-oss —— 换引擎必须是一次明确的动作。
    # 失败**抛异常**：调度器会把它记成 FAILED 并进心跳，
    # 而不是把提示词的回声当成"多头论点"交出去。
    from . import llm as _llm
    oll = _llm.engine()
    mdl = oll.model
    stage("engine", _llm.describe_spec(oll.spec))
    nmat = len(materials)
    # 被闸门判为「无实质」的材料编号。引用它们不算错，但要在报告里看得见——
    # 否则一条建立在"财报倒计时"标题上的论据，读起来和一条建立在 8-K 上的一样。
    _weak = {mid for mid, (tier, _r) in gate["labels"].items()
             if tier == material_gate.EMPTY}
    # 本轮面板有没有同业统计量。没有的话，「远高于行业平均」这类比较
    # 就是拿一个不存在的基准说话——形式合规，内容无据。
    _peers = has_peer_stats(panel_text)
    #    闸门约束【同时】进提示词和报告：只在报告顶部加警告、却让模型照旧写
    #    "基本面依然强劲"，报告就会自我矛盾。
    d = debate.run_debate(subj, facts_text, panel_text, oll, mdl,
                          verify=lambda t, src="", corp="": _verify_citations(
                              t, nmat, src, corp, _weak, _peers),
                          constraint=gate["constraint"])
    bull, bear = d["bull"], d["bear"]
    synthesis = d["synthesis"]
    direction, conviction = d["direction"], d["conviction"]
    # THIN 档的信心上限是【确定性后置规则】，不是提示词里的请求——
    # 让模型自己"注意材料薄所以别太自信"不可靠，它照样会写"中"。
    # 保留原判并印出来：压低的事实本身是信息。
    capped = ""
    if gate["conviction_cap"] and conviction != gate["conviction_cap"]:
        capped, conviction = conviction, gate["conviction_cap"]
    position = "—"                      # 一部不给仓位：那是 PC 的职权
    unverified = d["unverified"]

    # 3) 回测/行情支撑（yfinance 真值；A股用 .SS/.SZ）
    quant: list[str] = []
    if info.get("symbol"):
        quant, _ = backtest.quant_support(info["symbol"])
    else:
        quant = ["主题类标的（非个股），本轮不做单标的回测。"]

    # 4) 组装
    seen, sources = set(), []
    for n in news:
        if n.sources and n.sources[0].url and n.sources[0].url not in seen:
            seen.add(n.sources[0].url)
            sources.append(n.sources[0])
    status = CollectionStatus(structured=status_s, unstructured=status_u, fetched=len(raws),
                              degraded=[f"{k}:{v}" for k, v in status_u.items() if v not in ("ok",)])

    # 5) 论点台账：把失效条件落库，并用今天的材料复检【历史】论点是否已被证伪。
    #    没有这个回路，一部就是每天重新编一个故事、永远不会被检验。
    # 方向漂移复检 —— **必须在 record() 之前**，因为 record 会把旧论点置为 SUPERSEDED，
    # 之后就再也取不到"我昨天是怎么想的"了。
    drift = {}
    try:
        drift = thesis_store.drift_check(
            symbol=info.get("symbol") or "", subject=subj,
            direction=direction, conviction=conviction,
            gate_level=gate["level"], n_substantive=gate["n_sub"])
        if drift:
            log.info("方向漂移：%s", drift["text"].replace("**", ""))
    except Exception as e:
        log.warning("方向漂移复检失败（不影响本次产出）：%s", e)

    hits, tid = [], 0
    try:
        facts_for_check = [{"text": m.text, "url": m.source_url, "source": m.source_name}
                           for m in materials]
        hits = thesis_store.check(facts_for_check, symbol=info.get("symbol") or "")
        tid = thesis_store.record(
            as_of_date=stamp_beijing()[:10], subject=subj,
            symbol=info.get("symbol") or "", direction=direction, conviction=conviction,
            thesis=synthesis, catalysts=d["catalysts"], invalidations=d["invalidations"],
            panel=evidence.panel_dict(panel), unverified=unverified,
            material_verdict=gate["verdict"], material_substantive=gate["n_sub"],
            engine=oll.spec)
    except Exception as e:
        log.warning("论点台账写入/复检失败（不影响本次观点产出）：%s", e)

    stage("done", f"{direction}|{conviction}")
    return UnitAAdvice(
        subject=text, resolved=subj, symbol=info.get("symbol") or "", a_share=bool(info.get("a_share")),
        dt_beijing=stamp_beijing(), dt_ny=stamp_ny(),
        direction=direction, conviction=conviction, target_position=position,
        bull_case=bull, bear_case=bear,
        bull_rebuttal=d["bull_rebuttal"], bear_rebuttal=d["bear_rebuttal"],
        audit=d["audit"], synthesis=synthesis,
        catalysts=d["catalysts"], invalidations=d["invalidations"],
        market_only_invalidations=d.get("market_only_invalidations") or [],
        parse_warnings=d.get("parse_warnings") or [],
        panel_text=panel_text, panel=evidence.panel_dict(panel),
        run_id=RUN_ID,
        activated=True, gate_level=gate["level"], forced=forced, conviction_capped=capped,
        direction_drift=drift,
        material_verdict=gate["verdict"], material_substantive=gate["n_sub"],
        material_banner=gate["banner"], material_labels=material_gate.render_labels(materials, gate),
        thesis_id=tid, invalidation_hits=hits, llm_calls=d["llm_calls"],
        # **两个引擎并存之后，"这条论点是谁写的"必须答得出来。**
        # 半年后台账里一条「看多|中」，说不出是 gpt-oss 还是 Claude 写的，
        # 那这两个引擎就永远比不出高下。
        engine=oll.spec, engine_remote=oll.remote, usage=oll.usage.to_dict(),
        quant=quant,
        sources=sources[:8], materials=materials, unverified_count=unverified,
        material_count=len(materials), status=status,
    )


def _not_activated(text: str, subj: str, info: dict, gate: dict, panel, panel_text: str,
                   materials: list, news: list, raws: list, status_u: dict) -> UnitAAdvice:
    """Gate=INSUFFICIENT 且未强制 → 一部不启动。**一次 LLM 都不调用。**

    产出只有三样东西：正式弃权表述、确定性面板、材料质量摘要。
    刻意【不生成】自然语言版的多空论据——那正是要避免的东西：
    在 0 substantive 的情况下，Bull/Bear 就是拿毛利率/FCF/Beta/回撤重新讲故事，
    与二部的确定性测量完全重叠，且每天的措辞漂移是采样噪声而非市场变化。

    但**既有论点的监控照常进行**：今天的材料仍然逐条与仍 OPEN 的失效条件比对。
    未启动 ≠ 没有观点——既有观点仍然有效、仍然在被证伪流程盯着。
    """
    hits, opens = [], []
    try:
        facts_for_check = [{"text": m.text, "url": m.source_url, "source": m.source_name}
                           for m in materials]
        hits = thesis_store.check(facts_for_check, symbol=info.get("symbol") or "")
        opens = thesis_store.open_brief(info.get("symbol") or "")
    except Exception as e:
        log.warning("未启动路径的论点复检失败（不影响本次产出）：%s", e)

    seen, sources = set(), []
    for n in news:
        if n.sources and n.sources[0].url and n.sources[0].url not in seen:
            seen.add(n.sources[0].url)
            sources.append(n.sources[0])
    status = CollectionStatus(structured={}, unstructured=status_u, fetched=len(raws),
                              degraded=[f"{k}:{v}" for k, v in status_u.items() if v not in ("ok",)])
    log.info("一部未启动：%s → Formal vote: ABSTAIN（本轮 LLM 调用 0 次，"
             "既有论点 %d 条仍在监控，命中失效条件 %d 条）",
             material_gate.gate_summary(gate), len(opens), len(hits))
    return UnitAAdvice(
        subject=text, resolved=subj, symbol=info.get("symbol") or "",
        a_share=bool(info.get("a_share")),
        dt_beijing=stamp_beijing(), dt_ny=stamp_ny(),
        run_id=RUN_ID,
        activated=False, gate_level=gate["level"], formal_vote="ABSTAIN",
        direction="—", conviction="—", target_position="—",
        panel_text=panel_text, panel=evidence.panel_dict(panel),
        open_theses=opens, invalidation_hits=hits, llm_calls=0,
        material_verdict=gate["verdict"], material_substantive=gate["n_sub"],
        material_banner=gate["banner"],
        material_labels=material_gate.render_labels(materials, gate),
        sources=sources[:8], materials=materials, material_count=len(materials),
        status=status,
    )


# ---------------- 一部日频3选（漏斗：关注池→新闻催化缩池→辩论→选看多前3）----------------
_POOL_CAP = int(os.environ.get("CIO_UA_POOL_CAP", "40"))    # 候选池上限（默认覆盖全关注池；调小=省新闻探测时间）
_DEBATE_K = int(os.environ.get("CIO_UA_DEBATE_K", "4"))     # 实际跑辩论的只数（成本主旋钮，越大越慢）
_BULL_RANK = {"看多": 2, "中性": 1, "看空": 0}                # 看多程度排序（勿与 _BULL 提示词模板同名）
_CONV = {"强": 2, "中": 1, "弱": 0}


def _watch_pool() -> list[tuple[str, str, str]]:
    """候选池 = 关注池（银行/创新药/硬科技）curated 名单。返回 [(code,name,sector)]。

    三板块【轮转交错】排列后再截断：银行1→创新药1→硬科技1→银行2→…
    这样无论 _POOL_CAP 调到多小，三个板块都按比例留得下名额。

    旧版是三板块 dict 直接合并后 pool[:12] 切片，按 dict 插入序银行永远排最前，
    而银行恰好 12 只 —— 创新药/硬科技那 26 只永远进不了候选池，一部每天只能在
    银行里选股（2026-08-06 实测坐实：候选3/候选6 全是工行、中行、农行）。
    """
    smap = quant_data.sector_map()
    sectors = [list(quant_data._bank_codes().items()),
               list(quant_data._INNOVDRUG.items()),
               list(quant_data._HARDTECH.items())]
    pool: list[tuple[str, str, str]] = []
    for row in itertools.zip_longest(*sectors):
        for item in row:
            if item is None:
                continue
            code, name = item
            pool.append((code, name or code, smap.get(code, "")))
    return pool[:_POOL_CAP]


def _news_activity(name: str) -> int:
    """一部独立的催化剂视角：某标的近端新闻活跃度（条数越多=越有事发生）。"""
    try:
        return len(collect.fetch_google_news(name, "china", {}) or [])
    except Exception:
        return 0


def build_unit_a_daily(top_n: int = 3) -> tuple[list[DailyPick], list[UnitAAdvice]]:
    """一部日频选股：关注池 → 按新闻活跃度缩到 _DEBATE_K 只 → 逐只跑完整多空辩论 →
    按【看多程度(方向+信心)】选前 top_n。返回 (picks, 入选的辩论全文)。
    独立性：缩池用"新闻活跃度"（一部自采），不碰二部因子、不读 CIO 结论。"""
    pool = _watch_pool()
    ranked = sorted(pool, key=lambda t: _news_activity(t[1]), reverse=True)   # 催化剂缩池
    # 只受 _DEBATE_K 约束。旧版写 max(_DEBATE_K, top_n)，导致这个成本旋钮永远压不到
    # top_n(=3) 以下——冒烟测试设 K=1 实际仍跑 3 只完整辩论。K<top_n 时自然只出 K 只 pick。
    shortlist = ranked[:max(1, _DEBATE_K)]
    log.info("一部日频候选池 %d 只（%s）→ 按新闻活跃度取前 %d 只辩论：%s", len(pool),
             "、".join(f"{sec or '未分类'}{n}" for sec, n in
                       collections.Counter(t[2] for t in pool).items()),
             len(shortlist), "、".join(t[1] for t in shortlist))

    advices: list[tuple[UnitAAdvice, str, str, str]] = []
    for code, name, sec in shortlist:
        try:
            adv = build_unit_a(name)          # 复用完整多空辩论（含材料清洗、引用核验、回测）
            if not adv.activated:
                # Evidence Gate=INSUFFICIENT：一部没有观点，就不该出现在选股里。
                # 强行给它一个方向，等于把"没有证据"翻译成"中性"——那是一个观点。
                log.info("一部日频跳过 %s：%s，Formal vote: ABSTAIN",
                         name, material_gate.gate_summary({**{"level": adv.gate_level},
                                                           "verdict": adv.material_verdict,
                                                           "n": adv.material_count,
                                                           "n_sub": adv.material_substantive}))
                continue
            advices.append((adv, code, name, sec))
        except Exception as e:
            log.warning("一部日频辩论失败 %s：%s", name, e)

    # 按看多程度排序（方向优先，信心次之），取前 top_n
    advices.sort(key=lambda t: (_BULL_RANK.get(t[0].direction, 1), _CONV.get(t[0].conviction, 1)), reverse=True)
    picks: list[DailyPick] = []
    for adv, code, name, sec in advices[:top_n]:
        picks.append(DailyPick(
            source="一部", code=code, name=name,
            yahoo=(f"{code}.SS" if code[:1] == "6" else f"{code}.SZ"),
            direction=adv.direction, sector=sec,
            score=float(_BULL_RANK.get(adv.direction, 1)) + 0.1 * _CONV.get(adv.conviction, 1)))
    log.info("一部日频3选：候选%d→辩论%d→选%d（%s）", len(pool), len(advices), len(picks),
             "、".join(f"{p.name}:{p.direction}" for p in picks))
    return picks, [a[0] for a in advices[:top_n]]


# 结构化落盘的版本号。**界面按它判断字段能不能信**——
# 加字段不动它，改字段含义必须动它。
ADVICE_SCHEMA = 1


def advice_json_path(md_path: str) -> str:
    """由 md 路径推出同名 json 路径。两者始终同基名、同目录。"""
    from pathlib import Path as _P
    return str(_P(md_path).with_suffix(".json"))


def archive_and_render(r: UnitAAdvice) -> tuple[str, str]:
    """写 md + pdf + json 到 Topic Archive（决策留痕、可审计）。

    **返回值仍然是 (md_path, pdf_path) 两元组，没有变成三元组。**
    json 路径由 `advice_json_path(md_path)` 推出。
    这是刻意的：`beta_corr` 从两元组改成三元组那次，调用方按两个解包，
    抛出的 `too many values to unpack` 被外层 except 吞成一句
    "测量取不到"，于是 Beta 静默变成 None、报告照常生成。
    **能不动的返回签名就不动。**

    为什么要 json：md 和 pdf 是给人读的，界面要的是字段。
    从 Markdown 反向解析结构，正是这个项目栽过最多跟头的地方
    （`**失效条件**` 与 `【失效条件】` 那次，催化剂和失效条件同时解析成 0，
    报告在同一页上自相矛盾）。**产出端直接给结构化，下游就不必解析。**
    """
    from .config import TOPIC_DIR
    from .render import render_unit_a_md, render_unit_a_pdf
    stamp = file_stamp()
    base = f"{safe_filename(r.resolved)}证券一部建议+{stamp}"
    md_path = TOPIC_DIR / f"{base}.md"
    pdf_path = TOPIC_DIR / f"{base}.pdf"
    md_path.write_text(render_unit_a_md(r), encoding="utf-8")
    try:
        render_unit_a_pdf(r, str(pdf_path))
    except Exception as e:
        log.error("一部建议 PDF 渲染失败: %s", e)
        pdf_path = None

    # json 失败不能影响 md/pdf 的留痕——它是给界面用的副本，不是决策凭证
    try:
        import json as _json
        d = r.model_dump(mode="json") if hasattr(r, "model_dump") else r.dict()
        d["schema_version"] = runid.SCHEMA_VERSION
        d["kind"] = "unit_a"
        d["status"] = "completed" if r.activated else "gate_blocked"
        # **run_id 在契约里必须非空**：界面拿它当结果的身份。
        # 对象上没有（例如从别处构造出来再归档）就用本进程的——
        # 归档这件事本身就发生在这次运行里，这个归属是真的。
        d["run_id"] = d.get("run_id") or RUN_ID
        d["_md_path"] = str(md_path)
        d["_pdf_path"] = str(pdf_path or "")
        _P = TOPIC_DIR / f"{base}.json"
        _P.write_text(_json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        log.info("结构化输出：%s", _P.name)
    except Exception as e:                           # noqa: BLE001
        log.error("结构化 json 写入失败（md/pdf 不受影响）：%s", e)

    db.init_db()
    db.insert_brief("unit_a", f"《{r.resolved} 证券一部建议》", str(md_path), str(pdf_path or ""))
    return str(md_path), str(pdf_path or "")


def latest_advice(symbol: str = "") -> dict:
    """取最近一次一部结果的结构化输出。界面的主要入口。

    取不到就返回 {}——**不要伪造一个空壳对象**：一个所有字段都是默认值的
    UnitAAdvice，在界面上看起来就是"方向中性、信心中、没有材料"，
    和一次真实的中性结论长得一模一样。
    """
    import json as _json
    from .config import TOPIC_DIR
    pat = f"{safe_filename(symbol)}证券一部建议+*.json" if symbol else "*证券一部建议+*.json"
    files = sorted(TOPIC_DIR.glob(pat))
    for p in reversed(files):                        # 文件名带时间戳，字典序即时间序
        try:
            return _json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:                       # noqa: BLE001
            log.warning("结构化输出读不了，跳到上一份：%s（%s）", p.name, e)
    return {}
