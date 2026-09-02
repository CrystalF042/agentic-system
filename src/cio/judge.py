"""判定器接口 —— 让「规则」和「模型」可以在同一把尺子上被量。

## 为什么需要这个文件

build95 到 build102 之间，材料闸门被真机输出连修了八轮。回头看，
修的几乎全是**词形和句式穷举不完**：

    acquired 命不中 acquisition      approved 命不中 approval
    order 命不中 orders              see 命不中 sees
    标题现在时 Shifts / Expand / Go Live —— 整整一个语法类

说白了那是在手工搭一个很差的语言模型。而 2026-08-31 那轮扩样测试
给出了明确的证据：

    熟悉的三只（ARM/AMD/KLAC）  实质判定 3/3 对
    陌生的两只（ON/IT）          实质判定 0/2 对

两条误判——「May Be 2% Undervalued」和「Might Change The Case For
Investing」——**任何能读英文的模型都不会判错**。

所以问题不是"要不要用模型"，是**用在哪一段**。

## 语言理解 与 政策，必须切开

    语言理解（模型强得多）          政策（必须是代码，不能交给模型）
    ──────────────────────        ────────────────────────────────
    这篇是不是关于这家公司？          Form 4 不触发闸门
    它在报告事实还是在讲观点？        同一事件只算一次
    说的是哪件事？                   ≥3 件 = 材料充分
                                    公告没取到正文不算实质
                                    THIN 时信心封顶「弱」

右边那一列是 CEO 定的规则，不是语言问题。让模型去判「这份 Form 4 该不该
开门」，它会给出一个听起来很有道理的答案——而那正是闸门存在的理由要防的事。

**本模块只覆盖左边。** `material_gate.assess()`（政策层）一行不动，
它照旧消费判定结果并施加确定性规则。

## 三个防漂移机制（写在代码里，不是写在提示词里）

1. **`span` 必须是原文的逐字子串。** 判「实质」时模型必须引用那半句原话，
   由代码做子串校验；对不上就降级为「背景」。这把"相信模型"变成
   "核对模型的引文"，而核对是确定性的。
2. **按内容哈希缓存。** 同一篇文章永远同一个判定。这不只是省钱——
   真机上出现过同一天相隔 17 分钟两跑、ARM 实质 2 与实质 1 的情况；
   缓存之后只有真正的新材料才会产生新判定。
3. **失败必须显式降级。** 模型不通就回落到规则，并且**把这件事报出来**。
   静默降级会让"今天模型不通"和"今天没新闻"长得一模一样——
   这个坑在死掉的 RSS 源上已经踩过一次。

## 刻意还没做的事

**没有接进主链路。** 这一版只提供接口与评测用的实现；
先用 `scripts/eval_judge.py` 量出分数，再决定接不接、接哪个。
先接线后评测，就等于用真机跑分当验收——那是这个项目已经反复吃过亏的做法。

**没有做批量调用。** 生产上一只票 55 条标题应当一次调用送进去，
但那需要序号对齐与错位回退，是没被验证过的复杂度。评测用逐条调用，
慢一点但不会静默错位。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import config as _config      # noqa: F401
"""**这个 import 看起来没用，但删不得。**

`claude_chat()` 从 `CIO_ANTHROPIC_API_KEY` 环境变量取密钥，而把 `.env` 读进
环境变量的是 `cio.config` 的导入副作用（`config._load_dotenv()`）。
本模块不导入它，密钥就只在"调用方碰巧先导过 config"时才存在。

真机踩到过：`eval_judge.py --smoke` 那条分支插在
`from cio.config import MEMORY_DIR` **之前**，于是走 smoke 时 `.env`
从没被读过——一个配置完全正确的 key，报的是"没有 API key"。
依赖调用方的导入顺序来决定密钥读不读得到，是隐式依赖；改成显式。
"""

from . import material_gate
from .utils import get_logger

log = get_logger("cio.judge")


@dataclass
class Verdict:
    """一条材料的判定结果。**规则和模型返回同一个形状。**"""

    tier: str = material_gate.CONTEXT
    why: str = ""
    span: str = ""
    """判「实质」的依据，必须是原文里的**逐字**片段。规则判定留空。"""
    event: str = ""
    """这条材料说的是哪件事（一句话）。用于同一事件归并；规则判定留空。"""
    judge: str = ""
    degraded: bool = False
    """True 表示这条是**降级**得来的（模型不通、或引文对不上）。

    必须一路带到进料行上。静默降级会让"模型今天不通"和"今天没新闻"
    在输出上长得一模一样。
    """
    policy: bool = False
    """True 表示这条是**政策直判** —— 按来源与表单定的档，根本没问模型。

    **和 `degraded` 必须分开数。** 降级是"模型该跑而没跑成"，是故障；
    政策直判是"这条本来就不该问模型"，是设计。混成一个数，
    一份正常工作的公告会让评测报出"模型不通"。
    """
    vetoed: bool = False
    """True 表示模型判了实质，但被规则的硬标记否决压了下来。见 `HybridJudge`。"""


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


class Judge:
    """判定器接口。两件事：这条材料**是不是关于这家公司**、**有没有实质**。"""

    name = "?"

    def judge_one(self, text: str, source_name: str = "",
                  source_url: str = "") -> Verdict:
        raise NotImplementedError

    def judge_relevance(self, title: str, symbol: str = "",
                        company: str = "") -> bool:
        raise NotImplementedError

    def flush(self) -> None:
        """把缓存落盘。没有缓存的实现什么都不做。

        **这是接口的一部分，不是实现细节。** 原来评测脚本靠
        `getattr(jd, "_cache", None)` 去够私有字段——包一层
        （比如 `HybridJudge`）之后那句就够不着了，缓存于是从不落盘：
        下次评测重新调一遍模型，多花的钱不会有任何提示。
        """


def policy_verdict(text: str, source_name: str = "", source_url: str = "",
                   name: str = "") -> Optional[Verdict]:
    """**来源与表单是政策，不是语言问题——这一段绝不问模型。** 不适用返回 None。

    这个函数是 build105 那个缺陷的修复。`judge.py` 开头整整一节写着
    「语言理解 与 政策，必须切开」，而 `LLMJudge.judge_one` 的实现直接把
    文本丢给了模型，`is_primary` / `OWNERSHIP_FORMS` / `PRIMARY_MIN_CHARS`
    三条一条都没走——**文档里承诺的边界，代码里没有。**

    2026-09-01 首次真机评测抓到了它：语料里的 `SC 13G body=True`
    期望「背景」（持股申报不触发闸门，那是 CEO 定的规则），
    Claude 判「实质」。模型没有错——它读到的是一份真实、有正文、
    依法必须披露的文件；错的是这个问题根本不该由它回答。
    """
    if not material_gate.is_primary(source_name, source_url):
        return None
    tier, why = material_gate.tier_of(text, source_name, source_url)
    return Verdict(tier=tier, why=why, judge=f"{name}(政策)" if name else "policy",
                   policy=True)


class RuleJudge(Judge):
    """现在这套确定性规则。**基线、离线、零成本、永远可用。**"""

    name = "rules"

    def judge_one(self, text, source_name="", source_url=""):
        tier, why = material_gate.tier_of(text, source_name, source_url)
        return Verdict(tier=tier, why=why, judge=self.name)

    def judge_relevance(self, title, symbol="", company=""):
        from .unit_a import alias_hit, symbol_hit
        if company and alias_hit(company, title):
            return True
        return bool(symbol) and symbol_hit(symbol, title)


# ---------------------------------------------------------------- 提示词
# **只问语言问题，不问政策。** 表单号该不该开门、几条算充分、
# 同一事件怎么合并——这些一个字都不出现在提示词里。
_TIER_PROMPT = """你在给一条投资研究材料做分类。只回答 JSON，不要任何其他文字。

三个档位：
  实质  —— 报告了【已经发生】的、可以去核对的公司事实
           （签了合同/完成收购/上线/停产/裁员/监管放行/管理层调整指引…）
  背景  —— 与这家公司相关的真实报道，但没有新的可核对事实
  无实质 —— 前瞻、日程、行情涨跌复述、估值观点、荐股、清单体、对比文

**判不出来就判「背景」，不要乐观。** 误判观点文为实质的代价，
远大于误判实质为背景。

判「实质」时必须在 span 里**逐字抄出**原文中让你这么判的那一小段
（不要改写、不要翻译、不要加引号以外的字）。判不了实质就把 span 留空。

材料：
<<<
{text}
>>>

只输出这个 JSON：
{{"tier": "实质|背景|无实质", "why": "不超过20字的中文理由",
  "span": "原文逐字片段或空串", "event": "这条说的是哪件事，一句话"}}"""

_REL_PROMPT = """判断这条新闻标题是不是**关于指定的这家公司**。只回答 JSON。

公司：{company}
股票代码：{symbol}

注意代码本身可能是常用英文词（ON / IT / ARM / CAT …）。
标题里出现同形的普通词不算命中；报道另一家公司而只是顺带提到它，也不算。
公司的**常用简称**算（例如 KLA Corporation 常被写作 KLA，
Gartner Inc 常被写作 Gartner）。

标题：{title}

只输出：{{"relevant": true|false}}"""


class LLMJudge(Judge):
    """调模型做判定。`chat` 是一个 (prompt) -> str 的可调用对象。

    后端由调用方注入（本地 Ollama 或 Claude API），**本类不关心是哪一个**——
    这样评测脚本可以用同一把尺子量任意模型。
    """

    def __init__(self, chat: Callable[[str], str], name: str = "llm",
                 fallback: Optional[Judge] = None, cache_path: Optional[Path] = None):
        self.chat = chat
        self.name = name
        self.fallback = fallback or RuleJudge()
        self._cache = _Cache(cache_path) if cache_path else None

    # ---------------------------------------------------------------- 实质度
    def judge_one(self, text, source_name="", source_url=""):
        # **政策先走，而且走在缓存之前。** 一手披露该判什么由代码定，
        # 模型连问都不问 —— 见 `policy_verdict`。
        pv = policy_verdict(text, source_name, source_url, self.name)
        if pv is not None:
            return pv
        key = _key(self.name, "tier", text, source_name)
        if self._cache is not None:
            hit = self._cache.get(key)
            if hit is not None:
                return Verdict(**hit)
        v = self._judge_uncached(text, source_name, source_url)
        if self._cache is not None and not v.degraded:
            # **降级的结果不进缓存。** 缓存一条"因为模型不通所以按规则判的"
            # 结论，等于把一次网络故障永久固化成这条材料的判定。
            self._cache.put(key, v.__dict__)
        return v

    def _judge_uncached(self, text, source_name, source_url) -> Verdict:
        try:
            raw = self.chat(_TIER_PROMPT.format(text=(text or "")[:2000]))
            obj = _first_json(raw)
        except Exception as e:                                    # noqa: BLE001
            log.warning("%s 判定失败(%s)，降级到规则", self.name, type(e).__name__)
            obj = None
        if not obj:
            v = self.fallback.judge_one(text, source_name, source_url)
            v.judge, v.degraded = f"{self.name}→{self.fallback.name}", True
            return v

        tier = str(obj.get("tier", "")).strip()
        if tier not in (material_gate.SUBSTANTIVE, material_gate.CONTEXT,
                        material_gate.EMPTY):
            v = self.fallback.judge_one(text, source_name, source_url)
            v.judge, v.degraded = f"{self.name}→{self.fallback.name}", True
            v.why = f"模型返回了无法识别的档位（{tier[:20]!r}），已按规则判定"
            return v

        span = str(obj.get("span", "") or "")
        why = str(obj.get("why", "") or "")[:40]
        # **引文核对。** 判实质却引不出原文，就不算实质。
        if tier == material_gate.SUBSTANTIVE and not _span_ok(span, text):
            return Verdict(tier=material_gate.CONTEXT,
                           why="模型判实质但引用的原文对不上，按背景计",
                           span=span, event=str(obj.get("event", "") or "")[:80],
                           judge=self.name, degraded=True)
        return Verdict(tier=tier, why=why, span=span,
                       event=str(obj.get("event", "") or "")[:80], judge=self.name)

    # ---------------------------------------------------------------- 相关性
    def judge_relevance(self, title, symbol="", company=""):
        key = _key(self.name, "rel", title, f"{symbol}|{company}")
        if self._cache is not None:
            hit = self._cache.get(key)
            if hit is not None:
                return bool(hit.get("relevant"))
        try:
            raw = self.chat(_REL_PROMPT.format(
                company=company or symbol, symbol=symbol, title=(title or "")[:400]))
            obj = _first_json(raw)
        except Exception as e:                                    # noqa: BLE001
            log.warning("%s 相关性判定失败(%s)，降级到规则", self.name, type(e).__name__)
            obj = None
        if not obj or "relevant" not in obj:
            return self.fallback.judge_relevance(title, symbol, company)
        rel = bool(obj["relevant"])
        if self._cache is not None:
            self._cache.put(key, {"relevant": rel})
        return rel

    def flush(self):
        if self._cache is not None:
            self._cache.flush()


class HybridJudge(Judge):
    """**模型判语言，规则留否决权，政策归代码。**

    ## 为什么是这个分工

    2026-09-01 首次真机评测（Claude Haiku 4.5，降级 0/75）：

        　　　　　　　规则　　　Claude
        调参集　　　　67/67　　 51/67
        留出集　　　　 3/8　　 　7/8
        相关性　　　　13/20　　 19/20

    调参集那 67/67 不是能力——**每一条都是规则的训练数据**。
    真正被测到的是留出集和相关性两栏，模型在这两栏都大幅领先。

    16 条分歧里 **13 条是模型更严、3 条是模型更松**。三条更松的全是同一类：

        AMD Enters a Sovereign AI Showcase, Not a Revenue Windfall   转折否定式
        What KLA (KLAC)'s … Means For Shareholders                   解读体
        SC 13G body=True                                             （政策，已另修）

    前两条是**评论体标题 + 事实性正文**——正是规则花了 build96 到 build100
    四轮才收干净的那一类，规则在这上面是对的。

    ## 所以否决只准往下压，不准往上抬

    直觉上的做法是取"两边谁说实质就算实质"，把那 13 条更严的也捞回来。
    **不能这么做。** 那 13 条里规则的"正确"，绝大部分是它在自己的训练数据上
    的正确；把这种正确并进来，等于把留出集刚刚量出来的那份过拟合
    重新装回系统。留出集从 3/8 到 7/8 的差距就是这么来的。

    于是：**档位以模型为准，规则只有一票否决**，且只在

        模型判「实质」 且 标题命中硬标记 且 规则自己不判实质

    三个条件同时成立时行使。第三个条件让分句救援（`_fact_clause`）继续有效：
    规则自己都把这条判成实质了，就不存在分歧。

    在现有 75 条语料上验过：满足否决条件的有 31 条，**其中没有一条的期望
    等级是「实质」**——也就是说这个否决在当前语料上不会误伤。
    这不是"因为它不会出错所以安全"，是"如果它出错，语料会红"。

    ## 相关性整条交给模型

    19/20 对 13/20，而且规则的错法是**认错**（`It's` 被当成 ticker `IT`
    的所有格，真机上 Gartner 十个名额里六个是委内瑞拉石油和橄榄球赛程），
    不是判得严不严。这里没有可以保留的规则优势。
    """

    def __init__(self, inner: Judge, rules: Optional[Judge] = None,
                 name: str = ""):
        self.inner = inner
        self.rules = rules or RuleJudge()
        self.name = name or f"hybrid:{inner.name}"

    def judge_one(self, text, source_name="", source_url=""):
        pv = policy_verdict(text, source_name, source_url, self.name)
        if pv is not None:
            return pv
        v = self.inner.judge_one(text, source_name, source_url)
        v.judge = f"{self.name}→{v.judge}" if v.degraded else self.name
        if v.tier != material_gate.SUBSTANTIVE:
            return v
        head = (text or "").strip().split("\n", 1)[0]
        hard = material_gate.hard_marker(head)
        if not hard:
            return v
        rv = self.rules.judge_one(text, source_name, source_url)
        if rv.tier == material_gate.SUBSTANTIVE:
            return v            # 规则自己也判实质 —— 没有分歧，不动
        v.tier, v.vetoed = rv.tier, True
        v.why = (f"模型判实质，但标题命中硬标记「{hard}」——按规则否决，"
                 f"改判{rv.tier}")
        return v

    def judge_relevance(self, title, symbol="", company=""):
        return self.inner.judge_relevance(title, symbol, company)

    def flush(self):
        self.inner.flush()


def _span_ok(span: str, text: str) -> bool:
    """模型引用的片段必须真的出现在原文里。**这是确定性核对，不是信任。**"""
    s = _norm(span)
    return len(s) >= 8 and s in _norm(text)


_JSON = re.compile(r"\{.*\}", re.S)


def _first_json(raw: str) -> Optional[dict]:
    """从模型输出里取第一个 JSON 对象。取不到返回 None（**不猜**）。"""
    m = _JSON.search(raw or "")
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except Exception:                                             # noqa: BLE001
        return None
    return obj if isinstance(obj, dict) else None


def _key(name: str, kind: str, text: str, extra: str) -> str:
    h = hashlib.sha256(f"{name}\x00{kind}\x00{text}\x00{extra}".encode()).hexdigest()
    return h


class _Cache:
    """按内容哈希缓存判定。**同一篇文章永远同一个答案。**"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data: dict = {}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text("utf-8"))
            except Exception:                                     # noqa: BLE001
                log.warning("判定缓存读不动，当空的用：%s", self.path)
        self._dirty = False

    def get(self, k):
        return self.data.get(k)

    def put(self, k, v):
        self.data[k] = v
        self._dirty = True

    def flush(self):
        if not self._dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False), "utf-8")
        self._dirty = False


# ---------------------------------------------------------------- 后端
def ollama_chat(model: str, temperature: float = 0.0) -> Callable[[str], str]:
    """本地 Ollama。**温度默认 0** —— 判定要可复现。"""
    from .ollama_client import get_ollama
    oll = get_ollama()

    def _chat(prompt: str) -> str:
        return oll.chat(prompt, model=model, temperature=temperature)
    return _chat


ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"


def claude_chat(model: str, temperature: float = 0.0,
                max_tokens: int = 400) -> Callable[[str], str]:
    """Claude API。密钥从 `CIO_ANTHROPIC_API_KEY` 或 `ANTHROPIC_API_KEY` 取。

    **这条路径会把材料文本发到本机之外。** 送出去的只有公开新闻的
    标题与正文片段——不含持仓、论点台账、净值或任何属于账户的东西。
    要不要用由 CEO 决定；本模块只保证**送出去的就是这些**。
    """
    import httpx
    key = (os.environ.get("CIO_ANTHROPIC_API_KEY")
           or os.environ.get("ANTHROPIC_API_KEY") or "")
    if not key:
        raise RuntimeError("没有 API key：设置 CIO_ANTHROPIC_API_KEY 后再跑")
    client = httpx.Client(timeout=httpx.Timeout(60.0))

    def _chat(prompt: str) -> str:
        r = client.post(ANTHROPIC_URL, headers={
            "x-api-key": key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }, json={"model": model, "max_tokens": max_tokens,
                 "temperature": temperature,
                 "messages": [{"role": "user", "content": prompt}]})
        r.raise_for_status()
        parts = r.json().get("content") or []
        return "".join(p.get("text", "") for p in parts if isinstance(p, dict))
    return _chat


def cache_stem(spec: str) -> str:
    """判定缓存的文件名主干。**`hybrid:` 前缀不参与。**

    混合判定问模型的问题和纯模型判定**一模一样**（否决发生在拿到答案之后），
    共用一份缓存意味着：评完 `claude:<m>` 再评 `hybrid:claude:<m>`
    一次 API 调用都不会再发，两栏分数的差异因此完全来自否决逻辑，
    而不是模型这次心情不同。
    """
    s = (spec or "rules").strip()
    if s.startswith("hybrid:"):
        s = s.split(":", 1)[1]
    return s.replace(":", "_")


def build(spec: str, cache_path: Optional[Path] = None) -> Judge:
    """`rules` / `ollama:<m>` / `claude:<m>` / `hybrid:<其中之一>` → 一个 Judge。"""
    spec = (spec or "rules").strip()
    if spec == "rules":
        return RuleJudge()
    if spec.startswith("hybrid:"):
        inner = spec.split(":", 1)[1].strip()
        if not inner or inner == "rules":
            raise ValueError("hybrid: 后面要跟一个模型（ollama:<m> / claude:<m>）"
                             "—— hybrid:rules 就是 rules，没有第二个判定器可混")
        return HybridJudge(build(inner, cache_path=cache_path), name=spec)
    if spec.startswith("ollama:"):
        m = spec.split(":", 1)[1]
        return LLMJudge(ollama_chat(m), name=spec, cache_path=cache_path)
    if spec.startswith("claude:"):
        m = spec.split(":", 1)[1]
        return LLMJudge(claude_chat(m), name=spec, cache_path=cache_path)
    raise ValueError(f"不认识的 judge：{spec!r}"
                     f"（rules / ollama:<m> / claude:<m> / hybrid:claude:<m>）")
