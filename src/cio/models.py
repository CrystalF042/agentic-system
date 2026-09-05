"""统一数据模型（Pydantic v2）。所有 agent 输入输出都是 typed schema。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class Source(BaseModel):
    name: str
    url: str


class RawItem(BaseModel):
    """采集到的一条原始资料（RSS 条目 / 公告 / 行情快讯）。"""
    source_name: str
    source_category: str          # international / overseas_china / china_domestic / google_news / yahoo_ticker / edgar
    region: str = "international"  # international / china
    source_url: str
    title: str
    lang: str = "en"              # zh / en
    published_at: Optional[datetime] = None
    fetched_at: datetime
    body: str = ""
    weight: int = 2               # 源权威度基准分
    tickers: list[str] = Field(default_factory=list)
    raw_path: Optional[str] = None
    sha256: str = ""


class NewsItem(BaseModel):
    """处理后的一条情报（用于早报/专题）。事实来自来源，LLM 只做翻译与摘要。"""
    title_original: str
    title_zh: str = ""
    title_en: str = ""
    summary_zh: str = ""          # 2-3 句事实性摘要（只基于原文）
    body: str = ""                # 原文正文（内部用于摘要/向量，不进最终排版）
    region: str = "international"  # international / china
    signal: str = "弱"            # 强 / 中 / 弱（由 brief 相对排序分配）
    score: float = 0.0
    weight: int = 2               # 源权威度（国际十大排序用）
    primary_tag: str = ""         # 单一主标签（最高权重镜头）
    trend_tags: list[str] = Field(default_factory=list)  # 资金面/政策/预期修正/异动/公告
    is_noise: bool = False        # 标题党/自媒体夸张
    is_watchlist_hit: bool = False
    watchlist_sector: str = ""
    tickers: list[str] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)  # 本土源 | 海外源（交叉对照）
    published_at: Optional[datetime] = None
    # §6 事件四分卡（确定性、非方向）：relevance 由 §8 产出，其余三项由 scoring.py 算
    watchlist_relevance: str = "None"   # Direct / Sector / None
    source_confidence: int = 0          # 1–5（按来源域）
    event_type: str = ""                # earnings / m&a / regulatory / macro / …
    materiality: int = 0                # 1–5（按事件类型 + 是否异动）
    immediacy: str = ""                 # Today / This week / Medium-term / Background
    fact_suspect: list[str] = Field(default_factory=list)  # §零幻觉：摘要里出现但原文查无的年份/数字（供复核）


class Event(BaseModel):
    """§4 事件：把讲同一件事的多篇报道聚成一条（去重）。四分卡取成员的聚合值。
    member_count = 合并了几篇；sources = 各成员来源的并集（可审计）。"""
    event_id: str = ""
    headline: str = ""
    summary: str = ""
    sector: str = ""
    tickers: list[str] = Field(default_factory=list)
    event_type: str = ""
    primary_tag: str = ""
    signal: str = "弱"
    # 四分卡（聚合）
    confidence: int = 0           # max source confidence
    materiality: int = 0          # max materiality
    relevance: str = "None"       # 最强相关度（Direct > Sector > None）
    immediacy: str = ""           # 最紧迫（Today > This week > …）
    sources: list["Source"] = Field(default_factory=list)   # 各成员来源并集
    member_count: int = 1         # 合并的报道篇数
    published_at: Optional[datetime] = None   # 成员里最新的发布时间（时效闸用；缺则 None）


class WatchlistHit(BaseModel):
    sector: str                   # 银行 / 创新药 / 硬科技
    target: str                   # 标的或赛道
    fact: str
    signal: str = "弱"
    source: Source
    # §6 事件四分卡
    confidence: int = 0           # source confidence 1–5
    materiality: int = 0          # materiality 1–5
    relevance: str = "None"       # Direct / Sector / None
    immediacy: str = ""           # Today / This week / …


class IndexQuote(BaseModel):
    name: str
    symbol: str
    last: Optional[float] = None
    change_pct: Optional[float] = None
    note: str = ""                # 若取数失败，写降级说明
    group: str = ""               # 美股 / 港股 / 日韩 / 中国A股


class MarketTick(BaseModel):
    """盘前市场快照的一行。**as_of / age_label 是这个模型存在的理由**——
    没有它们，一个 15 小时前的收盘价和一个此刻的期货报价在报告里长得一样。"""
    group: str = ""               # 股指期货 / 宏观 / 海外市场
    name: str = ""
    symbol: str = ""
    last: Optional[float] = None
    change_pct: Optional[float] = None
    as_of: str = ""               # 该数字自己的时间戳（市场时区 MM-DD HH:MM）
    age_label: str = ""           # 实测年龄：实时 / N 小时前 / 昨日收盘 HH:MM / N 天前
    age_minutes: Optional[float] = None
    stale: bool = False           # 取不到、或明显过期，需要在报告上标出来
    note: str = ""                # 取数失败时的说明；**不省略这一行**


class FundFlow(BaseModel):
    name: str                     # 如 "北向资金净流入"
    value: str                    # 已格式化好的事实字符串（含单位）
    source: str
    trend_tag: str = "资金面"


class CollectionStatus(BaseModel):
    """数据采集状态（如实标注，绝不隐瞒）。"""
    structured: dict = Field(default_factory=dict)     # {yfinance: ok/降级, akshare: ...}
    unstructured: dict = Field(default_factory=dict)   # {各RSS源: ok/失败}
    ingested_vectors: int = 0
    fetched: int = 0
    deduped: int = 0
    errors: list[str] = Field(default_factory=list)
    degraded: list[str] = Field(default_factory=list)


class Brief(BaseModel):
    """盘前情报简报（对应 SOUL §8，两层结构）。"""
    kind: str = "premarket"
    title: str = "CIO 盘前情报简报"
    dt_beijing: str = ""
    dt_ny: str = ""
    # 核心要点 (BLUF)：当日最材料化的事件（只讲发生了什么，不复述行情点位）
    bluf: list[str] = Field(default_factory=list)
    # 数据锚定：8+A股指数真值（yfinance），行情数字的唯一真源
    anchor: list[IndexQuote] = Field(default_factory=list)
    # 盘前市场快照：期货 + 宏观 + 海外收盘，**每项带自己的 as-of 与实测年龄**。
    # 与 anchor 的分工：anchor 取「上一完整收盘」（刻意剔除盘中），
    # 这里取「此刻」——07:00 ET 的盘前，要的正是那根盘中 K 线。
    market_snapshot: list[MarketTick] = Field(default_factory=list)
    market_note: str = ""          # 时间基准说明（先写现在几点，读者才有参照系）
    # §7 市场异动：从锚定指数客观算出的异常（大幅波动 / 板块背离），只报事实不做解读
    anomalies: list[str] = Field(default_factory=list)
    # §21 方向性泄漏审计：CIO 自撰文本里越权的买卖/方向词（应为空；非空则供 CEO 复核）
    leakage_flags: list[str] = Field(default_factory=list)
    # §零幻觉数字核验：摘要中出现但原文查无的年份/数字（应为空；非空则供 CEO 复核）
    fact_flags: list[str] = Field(default_factory=list)
    # 国际十大要闻（综合，不限财经）
    world_top: list[NewsItem] = Field(default_factory=list)
    # 中国财经/关注池新闻
    top_news_china: list[NewsItem] = Field(default_factory=list)
    # 关注池命中 / 趋势信号 / 宏观 / 资金面
    watchlist_hits: list[WatchlistHit] = Field(default_factory=list)
    # §4 关注池事件（聚类去重后的四分卡事件；非空时 §III 以"事件卡"呈现）
    watchlist_events: list[Event] = Field(default_factory=list)
    # 聚类透明度：本次把多少篇关注池报道聚成多少事件、合并了几篇（让 Article→Event 可见可证）
    cluster_stat: str = ""
    trend_signals: list[NewsItem] = Field(default_factory=list)
    macro_policy: list[NewsItem] = Field(default_factory=list)
    fund_flows: list[FundFlow] = Field(default_factory=list)
    watch_ahead: list[str] = Field(default_factory=list)           # 今日待观察
    decisions: list[str] = Field(default_factory=list)  # 待CEO决断（只列事实+来源）
    # CEO 动态指令：本期焦点（如"美国金融动向"），置顶独立成栏
    focus_label: str = ""
    focus_items: list[NewsItem] = Field(default_factory=list)
    status: CollectionStatus = Field(default_factory=CollectionStatus)


class TimelineEvent(BaseModel):
    """历史脉络时间线上的一条（来自资料库沉淀）。"""
    date: str = ""                # YYYY-MM-DD（published_at 优先，缺则 ingested_at）
    title: str = ""
    source_name: str = ""
    source_url: str = ""
    layer: str = ""               # company / topic / raw


class DossierReport(BaseModel):
    """个股情报档案（资料库驱动）。先吃存量、再补增量，突出历史脉络与数据完备度。"""
    subject: str
    subject_type: str = "stock"
    resolved: str = ""
    title: str = ""
    dt_beijing: str = ""
    dt_ny: str = ""
    quote_facts: list[str] = Field(default_factory=list)         # 一、数据锚定（行情真值）
    timeline: list[TimelineEvent] = Field(default_factory=list)  # 二、历史脉络（资料库）
    recent: list[NewsItem] = Field(default_factory=list)         # 三、近期增量
    past_hits: list[str] = Field(default_factory=list)           # 四、关注池命中回顾
    filings: list[NewsItem] = Field(default_factory=list)        # 五、公告追踪
    cross_check: list[str] = Field(default_factory=list)         # 六、交叉验证与冲突
    completeness: str = ""                                       # 七、数据完备度（诚实标注）
    decisions: list[str] = Field(default_factory=list)           # 八、待 CEO 决断
    status: CollectionStatus = Field(default_factory=CollectionStatus)
    archive_docs: int = 0         # 命中存量库文档数
    fresh_docs: int = 0           # 本次增量条数


class MaterialItem(BaseModel):
    """一部辩论的带编号采集材料——论据只能引用这些，便于零幻觉核验。"""
    id: int
    text: str
    source_name: str = ""
    source_url: str = ""
    basis_text: str = ""
    """**实质度判定看这段，不看 `text`。** 空则回退到 `text`。

    两者的区别是：`text` 是给人读的（原文标题 + 模型生成的一句话摘要），
    `basis_text` 是**源头文本**（原始标题 + 正文片段），不含任何模型产物。

    为什么必须分开：

    一、`text` 里的摘要是 Ollama 生成的。拿它判实质度，等于让
        **一个零 LLM 的闸门去分类 LLM 的输出**——同一批新闻，
        摘要措辞一变，闸门的结论就可能变。可复算就没了。
        更循环的是：这个闸门决定的正是"要不要启动 LLM 辩论"。

    二、摘要只对**入选后**的材料生成。用 `text` 判定，就意味着
        入选的和被截掉的用的是**两种不同的输入**，两边的分档不可比——
        于是"被截掉的都不是实质"这句话根本无法成立，而它还在报告上印着。
        真机上就撞上了：AMD/LRCX 在截断修复后反而丢了实质材料。
    """


class UnitAAdvice(BaseModel):
    """证券一部《一部建议》（LLM-augmented：多空辩论 + 回测支撑）。
    独立于 CIO/二部，允许方向性判断（这是一部的职权）；仅研究观点，非投资指令。
    零幻觉硬约束：论据必须引用带编号的采集材料，标不到材料的自动打「⚠未核实」。"""
    subject: str
    resolved: str = ""
    symbol: str = ""
    a_share: bool = False
    dt_beijing: str = ""
    dt_ny: str = ""
    direction: str = "中性"          # 看多 / 看空 / 中性（裁判裁定）
    conviction: str = "中"           # 强 / 中 / 弱
    # target_position 保留仅为兼容旧渲染；**一部不再产出仓位建议**——
    # 仓位是 Portfolio Construction 的职权，一部只到"方向 + 论证"为止。
    # 若一部又开始给仓位/止损/目标价，就是在系统里长出第二个 PC 和第二个 CRO。
    target_position: str = "—"
    bull_case: str = ""              # 多头 Round1 论据（已核验，未溯源的打⚠未核实）
    bear_case: str = ""              # 空头 Round1 论据（已核验）
    bull_rebuttal: str = ""          # 多头 Round2：反驳 + 直面对己最不利的证据
    bear_rebuttal: str = ""          # 空头 Round2
    audit: str = ""                  # Judge 的论证审计（主张/证据/反驳/状态；不重做研究）
    synthesis: str = ""              # 一部观点综合
    catalysts: list[str] = Field(default_factory=list)      # 什么事件会证实论点
    invalidations: list[str] = Field(default_factory=list)  # 什么事实一旦发生论点即失效
    # 其中【只引用股价/风险统计量】的那几条。股价下跌不证明论点错——
    # 对逆向或长期论点，那可能恰恰是它最成立的时候。标出来供 CEO 判断，不自动删。
    market_only_invalidations: list[str] = Field(default_factory=list)
    # 小节标题出现了但解析器一条都没取到 —— 这是【解析失败】不是【模型没写】。
    # 两者结论相反：前者要修代码，后者是对分析师的判断。混为一谈就是拿自己的 bug 去指责模型。
    parse_warnings: list[str] = Field(default_factory=list)
    panel_text: str = ""             # 固定量化证据面板（呈现给多空双方的原文）
    panel: dict = Field(default_factory=dict)               # 面板结构化留痕
    # ---- 材料实质度闸门（build63）----
    # 首跑 8 条材料全是"财报前瞻"标题，辩论完全落回量化面板，而报告读起来
    # 像是有基本面依据的。这几个字段让【报告不假装自己有它没有的证据】。
    # Evidence Gate（build66）：没有新的可解释信息，就不制造新的观点。
    # activated=False 时【一次 LLM 都没调用】——不是省钱，是不让同一批不变的数字
    # 每天被重新讲成一个新故事（那不是市场在变，是采样噪声）。
    activated: bool = True           # 本轮是否启动了多空辩论
    gate_level: str = ""             # SUFFICIENT / THIN / INSUFFICIENT
    formal_vote: str = ""            # 未启动时为 "ABSTAIN"
    forced: bool = False             # 人工 override 强制复研（须与自动日常运行严格区分）
    conviction_capped: str = ""      # THIN 档被压低时，记下原判信心
    open_theses: list = Field(default_factory=list)   # 未启动时展示：仍在监控中的既有论点
    # 方向漂移复检：本轮结论与既有论点是否矛盾，以及矛盾时手上有没有新证据。
    # 失效条件复检问"新事实是否推翻了旧论点"；这里问相反的一半——
    # "旧论点是不是在没有新事实的情况下自己变了"。
    direction_drift: dict = Field(default_factory=dict)
    material_verdict: str = ""       # 材料充分 / 材料偏薄 / 无实质材料 / 无材料
    material_substantive: int = 0    # 其中含实质事实的条数
    material_banner: str = ""        # 报告顶部横幅（不需警告时为空）
    material_labels: dict = Field(default_factory=dict)   # {材料编号: "无实质·财报前瞻"}
    run_id: str = ""                 # 本次运行的身份。**界面按它取结果，不按"最近一次"**
    thesis_id: int = 0               # 论点台账 ID（供后续失效复检回指）
    invalidation_hits: list = Field(default_factory=list)   # 本轮命中的历史失效条件
    llm_calls: int = 0               # 本次辩论实际调用了几次模型（成本可见）
    # ---- 引擎血统（build124）----
    # 辩论可以跑在本地 gpt-oss，也可以跑在 Claude。**产出上必须记是谁写的**：
    # 两个引擎并存之后，半年后台账里一条「看多|中」说不出出处，
    # 那这两个引擎就永远比不出高下 —— 而"换 Claude 收益最大"会一直停在判断，
    # 变不成事实。
    engine: str = ""                 # 例：ollama:gpt-oss:20b / claude:claude-sonnet-5
    engine_remote: bool = False      # 这一轮的材料有没有离开本机
    usage: dict = Field(default_factory=dict)   # token 是事实，usd 是按带日期的价目表估的
    quant: list[str] = Field(default_factory=list)   # 回测/行情支撑（yfinance 真值）
    materials: list[MaterialItem] = Field(default_factory=list)  # 采集材料清单（论据引用依据）
    unverified_count: int = 0       # 被标「⚠未核实」的论据条数
    material_count: int = 0         # 采集到的事实材料条数
    status: CollectionStatus = Field(default_factory=CollectionStatus)


class UnitBPick(BaseModel):
    """证券二部一只量化选股。可复算、可解释：因子 z 分透明可查。"""
    rank: int
    code: str                       # A股 6 位代码 / 美股 ticker
    name: str = ""
    yahoo: str = ""                 # 行情取数标的（.SS/.SZ 或美股 ticker）
    sector: str = ""                # 关注池主题标签（兼容旧字段：cn=行业，us=focus 主题串）
    composite: float = 0.0          # = final_score（含 tilt）；保留旧名兼容 A 股渲染
    factors: dict = Field(default_factory=dict)   # {因子: z分}
    reason: str = ""                # 入选主因（贡献最大的因子）
    # §Data Contract：raw / tilt / final 分离（IC 自证只看 raw，tilt 单独归因）
    raw_quant_score: float = 0.0    # 纯因子合成 z（不含 tilt）
    focus_tilt: float = 0.0         # 关注池加权额度（0 或 +TILT）
    final_score: float = 0.0        # raw + tilt
    model_weight: float = 0.0       # 线内相对权重（%）——不是公司层最终仓位
    gics_sector: str = ""           # 官方 GICS 行业（美股；集中度用）
    focus_theme: list[str] = Field(default_factory=list)   # CEO 关注主题（AI/Semi/Pharma…；tilt 用）


class UnitBAdvice(BaseModel):
    """证券二部《量化选股建议》（零 LLM，纯确定性多因子）。
    与一部方法独立；仅研究观点、只回测不实盘，须经 CRO 与 CEO 决断。"""
    dt_beijing: str = ""
    dt_ny: str = ""
    universe: str = ""              # 选股池描述
    universe_count: int = 0         # 池内标的数
    scored_count: int = 0           # 实际参与打分（有足够历史）的只数
    picks: list[UnitBPick] = Field(default_factory=list)   # Top N 选股
    factor_desc: dict = Field(default_factory=dict)        # 因子说明
    ic_summary: str = ""            # 模型自证：IC/IR/分位收益差（纯因子 raw）
    tilt_note: str = ""             # 关注池加权说明
    status: CollectionStatus = Field(default_factory=CollectionStatus)
    # §Data Contract：市场 / 基准口径 / PIT 两层 / 归因 / 可复算 manifest
    market: str = "cn"              # cn / us
    benchmark: str = ""             # 展示名（S&P 500 / 沪深300）
    bench_source: str = ""          # 实际取数标的（SPY / 000300.SS）
    bench_basis: str = ""           # total_return_proxy / price_qfq
    universe_src: str = ""          # 成分来源（wikipedia / snapshot:日期 / fallback-DEGRADED）
    universe_snapshot: str = ""     # 用到的 last-known-good 快照名
    price_pit: bool = True          # 价格 point-in-time（只用≤T 数据算因子）
    universe_pit: bool = False      # 成分 point-in-time（当前成分回测历史→False，累积快照后→True）
    attribution: str = ""           # raw IC vs tilted IC 归因
    run_id: str = ""                # 可复算运行号
    manifest: dict = Field(default_factory=dict)   # 完整 run manifest（可复算清单）
    funnel: str = ""                # 数据漏斗：universe→有价→可打分，每一步剔除原因（闭合可审计）
    weighting_method: str = ""      # model_weight 的换算口径（让权重与因子分同等可复算）


class DailyPick(BaseModel):
    """两线每日选股的统一接口——CRO 风控、CFO 记账都消费它。"""
    source: str                     # 一部 / 二部
    code: str                       # 6 位A股代码
    name: str = ""
    yahoo: str = ""                 # .SS/.SZ（盯市/取数用）
    direction: str = "看多"          # 一部给方向；二部量化选股默认看多
    score: float = 0.0              # 一部信心代理 / 二部合成分
    sector: str = ""                # 关注池行业（银行/创新药/硬科技）或空


class RiskItem(BaseModel):
    """CRO 对一只选股的五维风险评级——全部客观可复算。"""
    source: str = ""                # 来源部门
    code: str = ""
    name: str = ""
    sector: str = ""
    vol: float = 0.0                # 近60日年化波动率
    max_dd: float = 0.0             # 近一年最大回撤（负数）
    liquidity: float = 0.0          # 近20日日均成交额（元）
    beta: float = 0.0               # 相对沪深300 Beta
    trend: float = 0.0              # 现价相对120日均线
    risk_score: float = 0.0         # 综合风险分（0-1，越高越险）
    rating: str = "中"              # 低 / 中 / 高
    vetoed: bool = False            # 是否一票否决
    reason: str = ""                # 主因 / 否决理由（可复算口径）


class CRORating(BaseModel):
    """CRO《风控评级》：投资倾向 + 逐只风险 + 两线一致性 + 送 CEO 终批清单。
    与两线方法独立；允许方向/风险判断（CRO 职权）；仅研究观点，须 CEO 决断。"""
    dt_beijing: str = ""
    dt_ny: str = ""
    leaning: str = "中性"           # 整体投资倾向：看多 / 中性 / 看空
    target_position: str = "中仓"    # 建议总仓位：轻仓 / 中仓 / 重仓
    bench_note: str = ""            # 大盘状态依据（沪深300 趋势/波动）
    consistency_note: str = ""      # 两线一致性（重叠=信心↑ / 分歧=风险↑）
    items: list[RiskItem] = Field(default_factory=list)     # 逐只风险评级
    approved_candidates: list[str] = Field(default_factory=list)  # 过筛后送 CEO 终批
    vetoed_count: int = 0
    status: CollectionStatus = Field(default_factory=CollectionStatus)


class PnLPosition(BaseModel):
    """盈亏表·持仓层一行。"""
    account: str = ""
    code: str = ""
    name: str = ""
    source: str = ""
    cost: float = 0.0               # 建仓价
    last: float = 0.0               # 现价（收盘）
    shares: int = 0
    market_value: float = 0.0
    pnl: float = 0.0                # 盈亏额
    pnl_pct: float = 0.0            # 盈亏率
    priced: bool = True             # 是否取到真实收盘价（否=缺价）


class PnLAccount(BaseModel):
    """盈亏表·账户层一行。"""
    account: str = ""
    capital: float = 0.0            # 初始资金
    cash: float = 0.0
    holdings: float = 0.0           # 持仓市值
    net_value: float = 0.0          # 总净值
    pnl: float = 0.0                # 累计盈亏额
    pnl_pct: float = 0.0            # 累计收益率
    day_pnl: float = 0.0            # 当日盈亏
    bench_pct: float = 0.0          # 同期沪深300 收益率
    excess: float = 0.0             # 超额（账户 - 基准）


class PnLStatement(BaseModel):
    """财务部《盈亏表》（零 LLM，纯账本）。中立记账，只报事实盈亏。"""
    dt_beijing: str = ""
    as_of: str = ""                 # 盯市日期
    mode: str = "hold_month"        # 持仓规则
    accounts: list[PnLAccount] = Field(default_factory=list)
    positions: list[PnLPosition] = Field(default_factory=list)
    compare_note: str = ""          # 一部 vs 二部 / 主盘 vs 影子盘 / 两线一致
    missing_prices: list[str] = Field(default_factory=list)  # 缺价标的（诚实标注）
    status: CollectionStatus = Field(default_factory=CollectionStatus)


class TopicReport(BaseModel):
    """专题情况报告（个股 / 主题）。"""
    subject: str                  # 标的或主题原文
    subject_type: str = "stock"   # stock / theme
    resolved: str = ""            # 解析后的标的（如 AAPL / 招商银行 / 创新药）
    title: str = ""
    dt_beijing: str = ""
    dt_ny: str = ""
    summary: str = ""             # 核心摘要（事实密集）
    quote_facts: list[str] = Field(default_factory=list)   # 行情事实
    fund_facts: list[FundFlow] = Field(default_factory=list)  # 资金面
    key_news: list[NewsItem] = Field(default_factory=list)    # 关键消息（中英对照）
    filings: list[NewsItem] = Field(default_factory=list)     # 公告追踪
    estimate_revisions: list[NewsItem] = Field(default_factory=list)  # 研报/一致预期
    policy: list[NewsItem] = Field(default_factory=list)      # 政策/监管
    decisions: list[str] = Field(default_factory=list)
    status: CollectionStatus = Field(default_factory=CollectionStatus)
    archived_from: int = 0        # 命中的历史资产条数


# ==================== 证券二部 — Systematic Analytics ====================
# 定位变更（2026-08）：二部不再宣称任何 alpha。它只做【客观状态测量】，
# 供 CRO 判断、Portfolio Construction 定仓位、CEO 决策。
#   Unit B: Measure → CRO: Assess → PC: Size → CEO: Decide
# 因此本节所有模型都只描述"是什么"，没有任何"所以应该怎么做"的字段。

class Pctile(BaseModel):
    """一个百分位读数。basis 必须随值一起出现——7 只样本里的 90th 没有信息量。"""
    value: float = 0.0            # 百分位 0–100（按原始值升序：90 = 高于全体 90%）
    basis: str = "universe"       # sector / universe（行业内样本不足时自动回退）
    n: int = 0                    # 该百分位的分母样本数


class AnalyticsRow(BaseModel):
    """一只标的的风险 / 风格 / 基本面状态快照。全部为描述性字段，无预测含义。"""
    code: str
    name: str = ""
    gics_sector: str = ""
    focus_theme: list[str] = Field(default_factory=list)
    member: bool = True                 # 是否为基准指数成分（非成分仍可对成分分布定位）
    identity_flag: str = ""             # 公司行为断点（合并/更名）

    # —— 风险测量（窗口写进字段名，不写脚注）——
    vol_60d: Optional[float] = None          # 年化已实现波动率 %
    downside_60d: Optional[float] = None     # 年化下行波动（对 0 的半标准差）%
    beta_250d: Optional[float] = None        # 对基准 Beta（日收益，日期对齐）
    corr_bench_60d: Optional[float] = None   # 与基准日收益相关性
    max_dd_250d: Optional[float] = None      # 近 250 日最大回撤 %（负数）
    px_vs_ma120: Optional[float] = None      # 现价相对 120 日均线 %
    trail_12_1: Optional[float] = None       # 尾随 12-1 收益 %（描述性，非动量因子）
    px_last: Optional[float] = None          # 最新收盘
    # 波动率是不是被单独一天主导。年化波动把 60 天平方和开根号再乘 √252，
    # 一次 +100% 的跳空就能单独把它推到 140% 以上——报出来像"极度动荡的股票"，
    # 实际上是一次事件或一条脏数据。摆出来，但不替读者判断是哪一种。
    max_1d_move: Optional[float] = None      # 窗口内最大单日涨跌幅 %
    max_1d_share: Optional[float] = None     # 该日占窗口平方和的比例 0–1
    max_1d_date: str = ""
    last_bar_date: str = ""                  # 本行数字实际用到的最后一根K线日期
    price_stale_days: int = 0                # 该日期落后 as_of 多少个交易日（0=不落后）

    # —— 基本面测量（来自 SEC XBRL，严格 PIT）——
    # **总负债 / 总资产 %**，不是 debt/assets。
    # 总负债包含应付账款、递延收入、租赁与养老金负债等非债务项；有息债务只是其中一部分。
    # 名字必须与公式一致：报告叫它 Liab/Assets，代码字段也叫 liab_assets。
    # 曾经叫 leverage 并在报告里印成 "debt / assets"——数值算得对，但说的不是同一件事。
    liab_assets: Optional[float] = None      # 总负债/总资产 %（自然方向，不翻转）
    gross_margin: Optional[float] = None     # 毛利率 %
    op_margin: Optional[float] = None        # 营业利润率 %
    fcf_margin: Optional[float] = None       # 自由现金流/营业收入 %
    fcf_assets: Optional[float] = None       # 自由现金流/总资产 %
    rev_growth: Optional[float] = None       # 营业收入同比 %
    current_ratio: Optional[float] = None    # 流动比率（有披露才有）
    interest_cover: Optional[float] = None   # 利息保障倍数（有披露才有）
    # 哪些【输出字段】是由恒等式反推的（liab_assets / gross_margin）。
    # 存字段名而不是公式串，报告才能把星号打在那个格子上，而不是打在 ticker 上。
    derived_fields: list = Field(default_factory=list)
    no_us_gaap: bool = False                 # 该公司无 us-gaap 事实（多为 20-F/IFRS 外国发行人）
    filing_accepted_date: str = ""           # 最近一份可见申报的 accepted 日期
    filing_stale: bool = False               # 超过【该公司自身节奏】未更新
    filing_age_days: Optional[int] = None
    filing_cadence_days: Optional[int] = None          # 这家公司自己的申报间隔中位数
    filing_stale_threshold_days: Optional[int] = None  # 据此校准出的陈旧阈值

    # —— 百分位（每个指标一份；key = 字段名）——
    pctile: dict = Field(default_factory=dict)   # {field: Pctile}


class AnalyticsException(BaseModel):
    """一条异常状态。只陈述"发生了什么"，绝不给"因此应该怎么做"。"""
    code: str = ""
    kind: str = ""                # volatility / drawdown / beta / leverage / correlation / stale / corp_action / extended
    message: str = ""             # 事实陈述
    threshold: str = ""           # 触发它的那条红线（配置值，报告里要印出来）
    # 越线幅度（越大越极端）。大面积触发时用它挑出最极端的几只，
    # 而不是把十几行等量齐观地全打印出来。
    extremity: float = 0.0


class PortfolioBlock(BaseModel):
    """组合层聚合。仅在确实存在持仓时渲染——没有持仓就整块不出现，绝不印 0。"""
    present: bool = False
    account: str = ""
    n_positions: int = 0
    market_value: float = 0.0
    beta_250d: Optional[float] = None            # 市值加权组合 Beta
    sector_weights: dict = Field(default_factory=dict)     # {GICS: %}
    top_sector: str = ""
    top_sector_pct: float = 0.0
    corr_clusters: list[str] = Field(default_factory=list)  # 高相关对（描述）
    coverage_note: str = ""                      # 有多少持仓没算进来、为什么


class AnalyticsReport(BaseModel):
    """《证券二部 — 系统化分析》日报。确定性、零 LLM、无方向性判断。"""
    # —— 四类日期，语义互不混用（审计记录的根基）——
    as_of_trade_date: str = ""        # 报告数字所依据的【最后一个已完成交易日】
    generated_at_utc: str = ""        # 生成时刻（UTC）
    generated_at_market: str = ""     # 生成时刻（市场本地时区）
    filing_window_note: str = ""      # 基本面数据的可见性口径说明
    fundamentals_note: str = ""       # 基本面为空时的原因（未设 UA / 取数失败 / 本轮跳过）

    market: str = "us"
    benchmark: str = ""               # 展示名（S&P 500）
    bench_source: str = ""            # 实际取数标的（SPY）
    bench_basis: str = ""             # total_return_proxy
    universe_src: str = ""
    universe_snapshot: str = ""
    universe_count: int = 0           # 计算百分位的全域分母
    displayed_count: int = 0          # 实际打印的关注池只数

    # —— 状态位：二部现在明确弃权 ——
    # 这三个字段必须由 build_analytics 依据台账推导后写入，不能靠默认值。
    # 曾经写死为 ABSTAIN，结果生产集非空时报告仍印"ABSTAIN / 无已验证模型"，
    # 而同一天 CRO 已经在投方向性票——报告和系统行为公然矛盾，正好发生在最要紧的分支。
    alpha_status: str = "no validated production model"
    alpha_vote: str = "ABSTAIN"
    research_status: str = "dormant"
    production_factor_set: list[str] = Field(default_factory=list)   # 应为空

    rows: list[AnalyticsRow] = Field(default_factory=list)
    exceptions: list[AnalyticsException] = Field(default_factory=list)
    portfolio: PortfolioBlock = Field(default_factory=PortfolioBlock)

    currency: str = ""                # 组合市值的货币符号（$ / ¥）——数字必须带单位
    thresholds_version: str = ""
    thresholds_shown: list[str] = Field(default_factory=list)   # 报告里要印出的红线原文
    windows_note: str = ""
    funnel: str = ""
    status: CollectionStatus = Field(default_factory=CollectionStatus)
    run_id: str = ""
    manifest: dict = Field(default_factory=dict)
