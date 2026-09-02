"""通用工具：时区、命名、清洗、指纹、日志。"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from datetime import datetime, timezone, timedelta

# 时区
BEIJING = timezone(timedelta(hours=8))
NY = None
try:
    from zoneinfo import ZoneInfo
    NY = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    NY = timezone(timedelta(hours=-4))  # 兜底：EDT


def now_beijing() -> datetime:
    return datetime.now(BEIJING)


def now_ny() -> datetime:
    return datetime.now(NY)


def stamp_beijing(dt: datetime | None = None) -> str:
    dt = dt or now_beijing()
    return dt.astimezone(BEIJING).strftime("%Y-%m-%d %H:%M")


def stamp_ny(dt: datetime | None = None) -> str:
    dt = dt or now_ny()
    return dt.astimezone(NY).strftime("%Y-%m-%d %H:%M %Z")


def file_stamp(dt: datetime | None = None) -> str:
    """归档命名用：YYYY-MM-DD-HHMM（北京时间）。"""
    dt = dt or now_beijing()
    return dt.astimezone(BEIJING).strftime("%Y-%m-%d-%H%M")


# ---------------- 日期语义（四类，互不混用）----------------
# 这不是洁癖：整个系统的 archive / run_id / manifest 都按日期归位，
# 一旦"生成时刻"和"数据截止日"混在一个字段里，所有审计记录都会被污染，
# 而且是那种半年后才发现、且再也追不回来的污染。
#
#   as_of_trade_date     报告里的数字所依据的【最后一个已完成交易日】（数据的日期）
#   filing_accepted_date 某条基本面事实被 SEC 受理的日期（可见性的日期）
#   generated_at_utc     报告生成的时刻，UTC（机器时间，用于排序与去重）
#   generated_at_market  报告生成的时刻，市场本地时区（人读的时间）
#
# 三条规则：
#   1. 归档文件名用 as_of_trade_date（按内容归位才查得到）。
#   2. run_id 用 generated_at_utc（同一天可以跑多次，必须能区分）。
#   3. 任何"截至 X"的表述必须指明是哪一类日期，不许只写一个裸日期。
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def stamp_utc(dt: datetime | None = None) -> str:
    """generated_at_utc：ISO-8601，秒级，带 Z。"""
    return (dt or now_utc()).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def date_only(v) -> str:
    """任何日期状物 → YYYY-MM-DD。取不出来就返回空串，绝不猜。"""
    if not v:
        return ""
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d")
    s = str(v).strip()
    return s[:10] if len(s) >= 10 and s[4] == "-" and s[7] == "-" else ""


def sha256_text(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update((p or "").strip().encode("utf-8", errors="ignore"))
        h.update(b"\x00")
    return h.hexdigest()


_WS = re.compile(r"[ \t　]+")
_NL = re.compile(r"\n{3,}")


def clean_text(s: str | None) -> str:
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)          # 去残留标签
    s = s.replace("\r", "\n")
    s = _WS.sub(" ", s)
    s = _NL.sub("\n\n", s)
    return s.strip()


def detect_lang(s: str) -> str:
    """粗判中英：含 CJK 即判 zh，否则 en。"""
    if not s:
        return "en"
    cjk = len(re.findall(r"[一-鿿]", s))
    return "zh" if cjk >= max(2, len(s) * 0.08) else "en"


def safe_filename(title: str, maxlen: int = 60) -> str:
    title = re.sub(r"[\\/:*?\"<>|\n\r\t]+", "_", (title or "untitled").strip())
    return title[:maxlen].strip("_ ") or "untitled"


def get_logger(name: str = "cio") -> logging.Logger:
    lg = logging.getLogger(name)
    if not lg.handlers:
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                                         datefmt="%H:%M:%S"))
        lg.addHandler(h)
        lg.setLevel(os.environ.get("CIO_LOG_LEVEL", "INFO"))
        lg.propagate = False        # 不向 root 传播，避免每条日志被打两遍
    return lg


# **在导入时就配置好，不要延迟到第一次调用。**
# 原来写的是"第一次调用时若没有 handler 就配置"，而它有个静默的破绽：
# 只要别人先给这个 logger 挂了一个 handler（界面捕获、测试断言都会这么做），
# 那个分支就不成立，level 永远没被设过 → 继承 root 的 WARNING →
# **所有 INFO 级的阶段事件被无声丢弃**。界面上的表现是进度条永远不动。
_STAGE_LOG = get_logger("cio.stage")


def stage(name: str, detail: str = "") -> None:
    """发一条**机器可解析**的阶段事件。

        [STAGE] gate | THIN 实质 1 条

    为什么需要它：一部跑一次要三到四分钟，其中六次模型调用期间**终端完全安静**。
    人盯着终端还能猜"它在跑"，界面上就只剩一个转圈——看起来和卡死一样。

    刻意做成**命名事件而不是 n/N 进度**：闸门拦下时整条链只走三步，
    完整跑要九步，硬套一个分母就得在两条路径上各维护一套计数。
    调用方（界面）自己持有期望的阶段顺序，收到哪个就点亮哪个。

    走 stderr（和其它日志一致），所以 stdout 仍然干净，可以专门用来输出 JSON。
    """
    _STAGE_LOG.info("[STAGE] %s%s", name, f" | {detail}" if detail else "")


def truncate(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[: n - 1] + "…"
