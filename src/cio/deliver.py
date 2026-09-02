"""触达：Telegram sendMessage(文字摘要) + sendDocument(PDF 附件)。
用 Bot API 主动推送（sendDocument 不消费 getUpdates，与 OpenClaw 收消息互不冲突）。
CIO_TG_DRYRUN=1 时只打印不真发，便于自测。"""
from __future__ import annotations

from pathlib import Path

import httpx

from .config import settings
from .utils import get_logger, truncate

log = get_logger("cio.deliver")

API = "https://api.telegram.org/bot{token}/{method}"


def _enabled() -> bool:
    return bool(settings.TG_TOKEN and settings.TG_CHAT_ID)


def send_text(text: str) -> bool:
    if settings.TG_DRYRUN or not _enabled():
        log.info("[DRYRUN/未配置] Telegram 文本：\n%s", truncate(text, 500))
        return settings.TG_DRYRUN
    url = API.format(token=settings.TG_TOKEN, method="sendMessage")
    base = {"chat_id": settings.TG_CHAT_ID, "text": text, "disable_web_page_preview": True}
    try:
        r = httpx.post(url, data={**base, "parse_mode": "Markdown"}, timeout=30)
        r.raise_for_status()
        return True
    except Exception as e:
        # Markdown 解析失败（如文本含未配对的 _ * [ ]）→ 退纯文本重发，绝不因排版丢消息
        log.warning("Markdown 发送失败(%s)，改纯文本重试", type(e).__name__)
        try:
            r = httpx.post(url, data=base, timeout=30)
            r.raise_for_status()
            return True
        except Exception as e2:
            log.error("sendMessage 失败: %s", e2)
            return False


def send_document(path: str, caption: str = "") -> bool:
    p = Path(path)
    if settings.TG_DRYRUN or not _enabled():
        log.info("[DRYRUN/未配置] Telegram 文件：%s（%s）", p.name, truncate(caption, 120))
        return settings.TG_DRYRUN
    if not p.exists():
        log.error("待发文件不存在: %s", p)
        return False
    try:
        with open(p, "rb") as f:
            r = httpx.post(API.format(token=settings.TG_TOKEN, method="sendDocument"),
                           data={"chat_id": settings.TG_CHAT_ID, "caption": truncate(caption, 1000)},
                           files={"document": (p.name, f, "application/pdf")}, timeout=90)
        r.raise_for_status()
        return True
    except Exception as e:
        log.error("sendDocument 失败: %s", e)
        return False


def deliver_brief(summary_text: str, pdf_path: str, caption: str) -> bool:
    ok1 = send_text(summary_text)
    ok2 = send_document(pdf_path, caption)
    return ok1 or ok2
