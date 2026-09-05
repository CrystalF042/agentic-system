"""模型引擎层 —— **辩论跑在哪个模型上，只有这一处决定。**

## 为什么单独抽一层

`debate.run_debate()` 里六步全走同一行 `oll.chat(...)`。要把辩论换到
Claude，最省事的写法是在那一行里加个 `if`——**那会立刻长出第二处**
（judge 那边也调 chat），然后两处漂移。

所以引擎是一个对象，接口和原来的 `Ollama` 一样：

    eng = llm.engine()                 # 从 CIO_DEBATE_ENGINE 读
    eng.chat(prompt, system=..., model=..., temperature=...)

`debate.py` 一个字都不用改。

## 三条硬规矩

### 一、失败**抛异常**，绝不返回提示词

原来的 `Ollama.chat()` 失败时 `return truncate(prompt, 240)`——
于是"多头论点"变成提示词的前 240 字，**没有异常、日志一行 warning、
报告照出**。本地模型很少挂，所以这条一直没咬到人。

换成远程 API 之后，失败模式从"服务没起"变成**限流 / 529 / key 过期 /
超时**——每天都可能发生。一次限流换来一份读起来像分析的提示词回声，
而它会走完闸门、进论点台账、被 CRO 定仓、推到 CEO 面前。

**这一层永远抛 `EngineError`。** 上层（调度器）已经会把异常记成
`FAILED` 并进心跳——那是对的落点：今天这只票没研究成，明天重来。

### 二、token 是事实，钱是估算

API 回来的 `usage` 是**事实**，落库。美元是**按一张带日期的价目表算出来的
估算**，会过期。两者分开记、分开印：

    tokens  in 24,113  out 4,802          ← 事实
    估算 $0.10（按 2026-09-05 价目表）      ← 估算，且说得出是哪天的表

不分开的话，半年后价目表变了，历史成本会被悄悄地重算成另一个数，
而账面上看不出来。

### 三、mock 与断网要走得通

`CIO_MOCK_LLM=1` 时不调任何东西。测试里 `_no_network` 把 socket 焊死，
真调用会抛——那正是我们要的：**一条会悄悄联网的断言，在两台机器上
就是两个测试。**
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Optional

from .config import settings
from .utils import get_logger, truncate

log = get_logger("cio.llm")

OLLAMA, CLAUDE = "ollama", "claude"
PROVIDERS = (OLLAMA, CLAUDE)

DEFAULT_SPEC = "ollama:gpt-oss:20b"
"""不设 `CIO_DEBATE_ENGINE` 就还是本地。**换引擎必须是一次明确的动作。**"""

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

MAX_TOKENS = int(os.environ.get("CIO_LLM_MAX_TOKENS", "2000"))
"""一轮辩论的输出上限。

`judge.claude_chat()` 里那个 `max_tokens=400` 是给判定器用的（回一个档位），
**装不下一轮辩论**。照抄过来的话，多头论点会在半句话处被截断，
而返回的是一段合法的文本——又一个不报错的错。
"""

PRICE_TABLE_AS_OF = "2026-09-05"
"""价目表的日期。**印在报告上。** 一张过期的表算出来的钱，
和一个没算过的钱，区别在于前者看起来是对的。
"""

PRICES = {
    # model 前缀 → (输入 $/Mtok, 输出 $/Mtok)
    "claude-sonnet-5": (2.0, 10.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-5": (5.0, 25.0),
}
"""按 `PRICE_TABLE_AS_OF` 那天的公开价目。**本地模型不在表里 —— 它不花钱。**"""


class EngineError(RuntimeError):
    """模型调用失败。**必须抛出去，不许降级成一段看起来像分析的文本。**"""


@dataclass
class Usage:
    """一次跑下来花了多少。**token 是事实，usd 是估算。**"""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    usd: float = 0.0
    priced: bool = True
    """`False` = 这个模型不在价目表里，`usd` 只是 0，**不是"免费"的证据**。"""
    engine: str = ""
    price_table_as_of: str = PRICE_TABLE_AS_OF
    per_call: list = field(default_factory=list)

    def add(self, in_tok: int, out_tok: int, model: str) -> None:
        self.calls += 1
        self.input_tokens += int(in_tok or 0)
        self.output_tokens += int(out_tok or 0)
        usd, priced = estimate_usd(model, in_tok, out_tok)
        self.usd += usd
        if not priced:
            self.priced = False
        self.per_call.append({"in": int(in_tok or 0), "out": int(out_tok or 0),
                              "usd": round(usd, 6), "model": model})

    def to_dict(self) -> dict:
        return {"calls": self.calls, "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens, "usd": round(self.usd, 4),
                "priced": self.priced, "engine": self.engine,
                "price_table_as_of": self.price_table_as_of}

    def describe(self) -> str:
        if not self.calls:
            return "本次没有调用模型"
        s = (f"{self.engine or '?'}　{self.calls} 次　"
             f"in {self.input_tokens:,}　out {self.output_tokens:,}")
        if self.priced and self.usd:
            return s + f"　估算 ${self.usd:.4f}（按 {self.price_table_as_of} 价目表）"
        if not self.priced:
            return s + "　**不在价目表里，成本未估算**（不等于免费）"
        return s + "　本地模型，不花钱"


def estimate_usd(model: str, in_tok, out_tok) -> tuple:
    """`(美元, 在不在表里)`。

    **不在表里返回 `(0.0, False)`，不是 `(0.0, True)`。** 两者都印 0，
    但含义相反：一个是"本地模型不花钱"，一个是"我们不知道它多少钱"。
    """
    m = str(model or "")
    if not m.startswith("claude"):
        return 0.0, True                       # 本地模型：真的不花钱
    for pref, (pin, pout) in PRICES.items():
        if m.startswith(pref):
            return (int(in_tok or 0) * pin + int(out_tok or 0) * pout) / 1e6, True
    log.warning("%s 不在价目表（%s）里，成本不估算 —— **不等于免费**",
                m, PRICE_TABLE_AS_OF)
    return 0.0, False


def parse_spec(spec: str = "") -> tuple:
    """`"claude:claude-sonnet-5"` → `("claude", "claude-sonnet-5")`。

    **不认识的 spec 抛异常，不悄悄退回本地。** 拼错一个字就静默用回
    gpt-oss，然后台账里一半论点是另一个模型写的而没人知道。
    """
    s = (spec or os.environ.get("CIO_DEBATE_ENGINE", "") or DEFAULT_SPEC).strip()
    if ":" not in s:
        raise ValueError(
            f"引擎要写成 provider:model，收到 {s!r}"
            f"（{OLLAMA}:gpt-oss:20b / {CLAUDE}:claude-sonnet-5）")
    provider, model = s.split(":", 1)
    provider = provider.strip().lower()
    if provider not in PROVIDERS:
        raise ValueError(f"不认识的 provider {provider!r}，只有 {'/'.join(PROVIDERS)}")
    if not model.strip():
        raise ValueError(f"{provider} 后面没有模型名：{s!r}")
    return provider, model.strip()


class Engine:
    """接口和 `Ollama` 一样，所以 `debate.run_debate` 一个字都不用改。"""

    def __init__(self, spec: str = "", usage: Optional[Usage] = None):
        self.provider, self.model = parse_spec(spec)
        self.spec = f"{self.provider}:{self.model}"
        self.usage = usage if usage is not None else Usage()
        self.usage.engine = self.spec

    @property
    def remote(self) -> bool:
        """**要不要把材料发到本机之外。** 报告上要说得出来。"""
        return self.provider == CLAUDE

    def chat(self, prompt: str, *, system: str = "", model: Optional[str] = None,
             temperature: float = 0.2) -> str:
        """跑一次。**失败抛 `EngineError`，绝不返回提示词。**"""
        m = model or self.model
        if settings.MOCK_LLM:
            self.usage.add(0, 0, m)
            return "[MOCK] " + truncate(str(prompt).replace("\n", " "), 200)
        if self.provider == OLLAMA:
            return self._ollama(prompt, system, m, temperature)
        return self._claude(prompt, system, m, temperature)

    # ---- 本地 ----

    def _ollama(self, prompt, system, model, temperature) -> str:
        from .ollama_client import get_ollama
        try:
            out = get_ollama().chat(prompt, system=system, model=model,
                                    temperature=temperature, strict=True)
        except Exception as e:                                 # noqa: BLE001
            raise EngineError(f"{self.spec} 调用失败：{type(e).__name__}: {e}") from e
        # 本地这条路没有 usage 回报。**记 0 而不是估**：
        # 估出来的 token 数会被当成事实读，而它不是。
        self.usage.add(0, 0, model)
        return out

    # ---- 远程 ----

    def _claude(self, prompt, system, model, temperature) -> str:
        import httpx
        key = (os.environ.get("CIO_ANTHROPIC_API_KEY")
               or os.environ.get("ANTHROPIC_API_KEY") or "")
        if not key:
            raise EngineError(
                "没有 API key：把 CIO_ANTHROPIC_API_KEY 写进 .env 再跑。"
                "**不会自动退回本地** —— 退回的话台账里会同时存在两个引擎"
                "写的论点，而没人知道哪条是哪个")
        body = {"model": model, "max_tokens": MAX_TOKENS,
                "temperature": temperature,
                "messages": [{"role": "user", "content": prompt}]}
        if system:
            body["system"] = system
        try:
            r = httpx.post(ANTHROPIC_URL, headers={
                "x-api-key": key,
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            }, json=body, timeout=httpx.Timeout(180.0))
            r.raise_for_status()
            data = r.json()
        except Exception as e:                                 # noqa: BLE001
            raise EngineError(f"{self.spec} 调用失败：{type(e).__name__}: {e}") from e
        u = data.get("usage") or {}
        self.usage.add(u.get("input_tokens", 0), u.get("output_tokens", 0), model)
        parts = data.get("content") or []
        text = "".join(p.get("text", "") for p in parts
                       if isinstance(p, dict) and p.get("type") == "text").strip()
        if not text:
            # **空回复不是"它没话说"，是这一次没成。**
            raise EngineError(
                f"{self.spec} 返回了空内容（stop_reason={data.get('stop_reason')!r}）")
        return text


def engine(spec: str = "", usage: Optional[Usage] = None) -> Engine:
    return Engine(spec, usage=usage)


def describe_spec(spec: str = "") -> str:
    """给人看的一行：**这一跑会不会把材料发到本机之外。**"""
    try:
        provider, model = parse_spec(spec)
    except ValueError as e:
        return f"**引擎配置有问题**：{e}"
    if provider == OLLAMA:
        return f"辩论引擎 {provider}:{model}　—— 本地，材料不出本机"
    return (f"辩论引擎 {provider}:{model}　—— **材料会发到本机之外**"
            f"（公开新闻标题与正文片段、SEC 文件、公开行情面板、代码；"
            f"不含持仓、净值、论点台账、账本）")


def price_line() -> str:
    return ("价目表 " + PRICE_TABLE_AS_OF + "：" +
            "　".join(f"{k} ${v[0]}/${v[1]}" for k, v in
                      sorted(PRICES.items()))) + "　（每百万 token，输入/输出）"


def dump_prices() -> str:
    return json.dumps({"as_of": PRICE_TABLE_AS_OF, "prices": PRICES},
                      ensure_ascii=False, indent=2)
