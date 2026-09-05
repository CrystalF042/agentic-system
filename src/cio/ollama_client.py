"""本地 Ollama 客户端（原生端点，严禁云端/中国模型）。

- chat(): /api/chat  用于翻译、2-3句摘要、编撰重组（只做 摘要/翻译/分类/重组，不补事实）
- embed(): /api/embeddings  用于 nomic 向量化
- MOCK 模式 (CIO_MOCK_LLM=1)：不联网也能跑通全链路，便于离线自测与冷启动前冒烟。
"""
from __future__ import annotations

import hashlib
import struct
from typing import Optional

import httpx

from .config import settings
from .utils import detect_lang, get_logger, truncate

log = get_logger("cio.ollama")

_EMBED_DIM = 768  # nomic-embed-text-v2-moe 默认维度（mock 用；真实以返回为准）


def _mock_vector(text: str, dim: int = _EMBED_DIM) -> list[float]:
    """确定性 hash 伪向量（仅供离线自测；不具语义，但保证链路可跑、维度稳定）。"""
    out: list[float] = []
    seed = text.encode("utf-8", "ignore")
    i = 0
    while len(out) < dim:
        h = hashlib.sha256(seed + i.to_bytes(4, "little")).digest()
        for j in range(0, len(h), 4):
            out.append(struct.unpack("<I", h[j:j + 4])[0] / 2**32 - 0.5)
            if len(out) >= dim:
                break
        i += 1
    return out


# 提示词回声过滤：小模型偶发把系统指令抄进输出，命中即丢弃回退原文
_ECHO_MARKERS = ("只输出译文", "只输出摘要", "只输出这一句", "严禁音译", "臆造机构", "不做方向判断",
                 "数字先行", "卖方研究", "基于且仅基于", "不添加、不评论", "保留英文原样",
                 "严禁合并或引入", "方向性或推测", "严禁感叹", "≤40字", "无前缀")


def _strip_echo(out: str, fallback: str) -> str:
    o = (out or "").strip()
    for p in ("译文：", "译文:", "摘要：", "摘要:", "中文：", "中文:", "翻译：", "翻译:", "标题：", "标题:"):
        if o.startswith(p):
            o = o[len(p):].strip()
    if not o or any(m in o for m in _ECHO_MARKERS):
        return (fallback or "").strip()
    return o


class Ollama:
    def __init__(self) -> None:
        self.host = settings.OLLAMA_HOST.rstrip("/")
        self.mock = settings.MOCK_LLM
        self._client = httpx.Client(timeout=httpx.Timeout(120.0))
        self._dim: Optional[int] = None

    # ---------------- chat ----------------
    def chat(self, prompt: str, *, system: str = "", model: Optional[str] = None,
             temperature: float = 0.2, strict: bool = False) -> str:
        """跑一次。`strict=True` 时**失败抛异常**。

        默认那条降级路径（`return truncate(prompt, 240)`）是给
        翻译 / 摘要 / 分类用的——它们外面套着 `_strip_echo`，
        认得出这是回声并退回原文，**降级在那里是安全的**。

        辩论和判定不一样：那 240 个字会变成"多头论点"，
        **没有异常、日志一行 warning、报告照出**，然后走完闸门、
        进论点台账、被 CRO 定仓、推到 CEO 面前。所以那两条路走 `strict`。
        """
        model = model or settings.MODEL_LIGHT
        if self.mock:
            return "[MOCK] " + truncate(prompt.replace("\n", " "), 200)
        try:
            r = self._client.post(
                f"{self.host}/api/chat",
                json={
                    "model": model,
                    "messages": ([{"role": "system", "content": system}] if system else [])
                    + [{"role": "user", "content": prompt}],
                    "stream": False,
                    "options": {"temperature": temperature},
                },
            )
            r.raise_for_status()
            return (r.json().get("message", {}) or {}).get("content", "").strip()
        except Exception as e:
            if strict:
                # **绝不把提示词当成产出交出去。** 上层会把它记成 FAILED
                # 并进心跳——今天这只票没研究成，明天重来，这是对的落点。
                raise
            log.warning("chat 失败(%s)，降级返回原文截断", type(e).__name__)
            return truncate(prompt, 240)

    # ---------------- embeddings ----------------
    def _embed_once(self, text: str, model: str) -> list[float]:
        """先试 /api/embeddings(prompt)，不行再试 /api/embed(input)。"""
        try:
            r = self._client.post(f"{self.host}/api/embeddings",
                                  json={"model": model, "prompt": text})
            r.raise_for_status()
            vec = r.json().get("embedding") or []
            if vec:
                return vec
        except Exception:
            pass
        r = self._client.post(f"{self.host}/api/embed",
                              json={"model": model, "input": text})
        r.raise_for_status()
        data = r.json()
        embs = data.get("embeddings") or []
        if embs and isinstance(embs[0], list):
            return embs[0]
        return data.get("embedding") or []

    def embed_dim(self) -> int:
        """探测并缓存真实向量维度（用于降级伪向量与向量库对齐）。"""
        if self._dim is not None:
            return self._dim
        if self.mock:
            self._dim = _EMBED_DIM
            return self._dim
        try:
            v = self._embed_once("维度探测 dimension probe", settings.MODEL_EMBED)
            self._dim = len(v) if v else _EMBED_DIM
        except Exception:
            self._dim = _EMBED_DIM
        return self._dim

    def embed(self, text: str, *, model: Optional[str] = None) -> list[float]:
        model = model or settings.MODEL_EMBED
        text = (text or " ").strip() or " "   # 空串会被 Ollama 拒 → 兜底空格
        if self.mock:
            return _mock_vector(text, self.embed_dim())
        last = "unknown"
        for _ in range(3):                     # 瞬时失败(与网关抢 Ollama)重试
            try:
                vec = self._embed_once(text, model)
                if vec:
                    return vec
                last = "empty"
            except Exception as e:
                last = type(e).__name__
        log.warning("embed 失败(%s)，降级 hash 伪向量", last)
        return _mock_vector(text, self.embed_dim())

    # ---------------- 高阶封装 ----------------
    def translate_to_zh(self, text: str, *, model: Optional[str] = None) -> str:
        """英文→中文，忠实、不改立场、保留数字与专有名词。中文原样返回。"""
        if not text.strip() or detect_lang(text) == "zh":
            return text.strip()
        sys = ("你是金融情报翻译。把英文标题忠实翻译成简体中文："
               "数字、公司名、股票代码、以及媒体/机构名（如 Reuters、AP News、CNBC、Fed、SEC）一律保留英文原样，"
               "严禁音译或臆造机构名；不添加、不评论、不做方向判断。只输出译文。")
        return _strip_echo(self.chat(text, system=sys, model=model or settings.MODEL_LIGHT), text.strip())

    def summarize_zh(self, title: str, body: str, *, model: Optional[str] = None) -> str:
        """一句话事实性中文摘要（卖方研究晨报风格），只基于给定文本，零补充、禁串台。"""
        src = f"标题：{title}\n正文：{truncate(body, 1500)}"
        sys = ("你是卖方研究晨报编辑。基于且仅基于给定文本，用简体中文写【一句话】事实摘要（≤40字）："
               "术语准确（同比/环比/基点bp/净流入等）、客观克制。"
               "【数字铁律】严禁改写、推算、杜撰或补全任何数字；只能引用原文明确出现的数字，拿不准就不写数字；"
               "大盘指数点位与涨跌幅一律不写（由数据锚定负责）。"
               "【时间铁律】严禁写入原文没有出现的年份、日期或时间（如凭记忆补 '2021 年'）——原文没写时间就不写时间。"
               "严禁补充原文之外的任何背景、上下文、历史或数字。"
               "严禁合并或引入本篇之外的其他新闻或数据；严禁出现'建议/利好/利空/看多/看空/或将/有望/值得关注'"
               "等方向性或推测词汇；严禁感叹与标题党。只输出这一句，无前缀。")
        fallback = truncate((body or title or "").strip(), 60)
        return _strip_echo(self.chat(src, system=sys, model=model or settings.MODEL_LIGHT), fallback)

    def summarize_en(self, title: str, body: str, *, model: Optional[str] = None) -> str:
        """One-sentence factual English summary (US market; sell-side morning-note style), text-only, zero fabrication."""
        src = f"Title: {title}\nBody: {truncate(body, 1500)}"
        sys = ("You are a sell-side morning-note editor. Based ONLY on the given text, write ONE factual sentence "
               "(<=30 words) in English: precise terms (YoY/QoQ/basis points/net inflow), objective and restrained. "
               "NUMBER RULE: never rewrite, infer, fabricate or complete any figure; cite only numbers explicitly present; "
               "if unsure, omit the number; never state index levels or percent moves (the data anchor owns those). "
               "TIME RULE: never add a year, date or time reference that is not in the given text (do NOT append things "
               "like 'in 2021' from memory) — if the text gives no date, give none. "
               "Add NO background, context, history or figures beyond the given text. "
               "Do not merge in or introduce any other news or data; no directional or speculative words "
               "(buy/sell/bullish/bearish/could/expected/likely/watch); no exclamation, no clickbait. "
               "Output that one sentence only, no prefix.")
        fallback = truncate((body or title or "").strip(), 80)
        return _strip_echo(self.chat(src, system=sys, model=model or settings.MODEL_LIGHT), fallback)


_ollama: Optional[Ollama] = None


def get_ollama() -> Ollama:
    global _ollama
    if _ollama is None:
        _ollama = Ollama()
    return _ollama
