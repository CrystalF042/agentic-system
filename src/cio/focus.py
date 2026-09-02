"""CEO 动态指令 / 本期焦点（功能②）。
CEO 在 Telegram 发一句「最近重点看美国金融动向」→ 解析成对 config/focus.yaml 的更新 →
下一份简报把该主题置顶、加权、扩容。纯代码解析、纯本地配置、持久化、可随时改回。"""
from __future__ import annotations

import re
from datetime import timedelta

import yaml

from .config import CONFIG_DIR
from .utils import get_logger, now_beijing

log = get_logger("cio.focus")
FOCUS_PATH = CONFIG_DIR / "focus.yaml"

# 常见焦点主题 → 扩展关键词（让口令自动带出相关词，命中更全）
_TOPIC_MAP = {
    "美国金融": ["美联储", "Fed", "FOMC", "美股", "美国金融", "利率", "降息", "加息", "华尔街",
                 "美债", "美元", "银行股", "Wall Street", "Nasdaq", "标普", "道琼斯"],
    "美股": ["美股", "纳斯达克", "标普", "道琼斯", "Nasdaq", "S&P", "Dow", "美联储", "Fed"],
    "半导体": ["半导体", "芯片", "存储芯片", "光刻", "晶圆", "EDA", "GPU", "先进制程", "semiconductor", "chip"],
    "人工智能": ["人工智能", "AI", "大模型", "算力", "GPU", "artificial intelligence"],
    "创新药": ["创新药", "临床", "医保谈判", "集采", "license-out", "NMPA", "FDA", "biotech"],
    "银行": ["银行", "国有银行", "不良贷款", "净息差", "股息率", "汇金"],
    "地缘": ["地缘", "制裁", "冲突", "战争", "关税", "出口管制", "sanction", "tariff"],
    "港股": ["港股", "恒生", "恒生科技", "港股通", "南向"],
    "香港": ["香港", "恒生", "恒生科技", "港股", "港股通", "南向", "HKEX", "Hong Kong", "港元", "H股"],
    "日本": ["日经", "日本央行", "BOJ", "日元", "Nikkei"],
}


def _expand(topic: str) -> list[str]:
    t = (topic or "").strip()
    kws = [t]
    for key, ex in _TOPIC_MAP.items():
        if key in t or t in key:
            kws += ex
    for part in re.split(r"[\s，、和/]+", t):
        if len(part) >= 2:
            kws.append(part)
    return list(dict.fromkeys([k for k in kws if len(str(k)) >= 2]))


def load_focus() -> list[dict]:
    """读取当前生效的焦点（过期的自动剔除）。"""
    if not FOCUS_PATH.exists():
        return []
    try:
        data = yaml.safe_load(FOCUS_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    today = now_beijing().strftime("%Y-%m-%d")
    out = []
    for f in (data.get("focus") or []):
        if not isinstance(f, dict):
            continue
        until = str(f.get("until") or "")
        if until and until < today:      # 过期自动失效
            continue
        out.append(f)
    return out


def set_focus(topic: str, days: int = 14, weight: int = 8, replace: bool = True) -> dict:
    topic = (topic or "").strip()
    if not topic:
        return {}
    entry = {
        "topic": topic,
        "keywords": _expand(topic),
        "weight": weight,
        "until": (now_beijing() + timedelta(days=days)).strftime("%Y-%m-%d"),
    }
    existing = [] if replace else load_focus()
    data = {"focus": existing + [entry]}
    FOCUS_PATH.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    log.info("焦点已更新：%s（至 %s）", topic, entry["until"])
    return entry


def clear_focus() -> None:
    FOCUS_PATH.write_text("focus: []\n", encoding="utf-8")


def focus_keywords() -> list[str]:
    kws: list[str] = []
    for f in load_focus():
        kws += (f.get("keywords") or [])
    return list(dict.fromkeys(kws))


def active_label() -> str:
    return "；".join(f.get("topic", "") for f in load_focus() if f.get("topic"))


def focus_weight() -> int:
    ws = [int(f.get("weight", 8)) for f in load_focus()]
    return max(ws) if ws else 0


# ---------------- CEO 指令解析 ----------------

_CLEAR = re.compile(r"(取消|清除|去掉|删除|恢复默认|恢复正常|不用看了|不看了)\s*(焦点|重点|关注)?|/focus\s+clear")
_SET = re.compile(
    r"(?:最近\s*)?(?:重点|着重|多|优先)?\s*"
    r"(?:关注|看看|看|盯|留意|聚焦|侧重|focus)\s*[:：]?\s*(.+)")


def parse_command(text: str) -> dict:
    """把 CEO 的自然语言/斜杠命令解析成 {action: set/clear/none, topic}。"""
    t = (text or "").strip()
    t = re.sub(r"^@?CIO[\s,，:：]*", "", t, flags=re.I)
    t = re.sub(r"^/focus\s*", "", t)
    if _CLEAR.search(t):
        return {"action": "clear"}
    m = _SET.search(t)
    if m and m.group(1).strip():
        topic = m.group(1).strip().rstrip("。.!！?？")
        topic = re.sub(r"(的)?(动向|情况|走势|形势|形式|局势|新闻|资讯|方面|板块|市场)$", "", topic).strip() or m.group(1).strip()
        return {"action": "set", "topic": topic}
    return {"action": "none"}


def handle_command(text: str) -> str:
    """执行 CEO 焦点指令，返回给 CEO 的一句中文确认。"""
    cmd = parse_command(text)
    if cmd["action"] == "clear":
        clear_focus()
        return "（@CIO）已清除本期焦点，下一份简报恢复默认侧重。"
    if cmd["action"] == "set":
        e = set_focus(cmd["topic"])
        return (f"（@CIO）已设定本期焦点：**{e['topic']}**（有效期至 {e['until']}）。"
                f"下一份盘前简报起，该主题将置顶、加权、扩容。发\"取消焦点\"可随时恢复默认。")
    return "（@CIO）未识别为焦点指令。示例：\"最近重点看美国金融动向\" 或 \"取消焦点\"。"
