#!/usr/bin/env python3
"""盘前早报一键入口：采集 → 处理 → 编撰 → 存档(md+PDF) → Telegram 推送 → 记忆。
每一步都 try/except：任一环节失败仍产出（降级）简报，并在"数据采集状态"如实标注。

用法：
  source .venv/bin/activate            # 或 cio-agent/.venv
  python run_premarket.py              # 正式跑（**只在市场时区的盘前窗口内出简报**）
  python run_premarket.py --force      # 无视时间窗口，立刻跑一份
  python run_premarket.py --when       # 只打印现在算不算盘前、以及该怎么排 cron
  CIO_MOCK_LLM=1 CIO_TG_DRYRUN=1 python run_premarket.py --force   # 离线冒烟自测

## 时间窗口这道闸是修一个真实故障加的

2026-09-02 这份简报送达时是**纽约 09-01 晚上 19:49** —— 收盘四小时之后。
简报本身完全正确（英文抬头、ET 时间戳、美股期货、当天真实新闻），
错的只有发车时间：cron 那行 `0 7 * * 1-5` 是**机器本地时间** 7 点，
而这台机器在北京时区，北京 07:00 就是纽约前一天 19:00。

cron 跟不了另一个国家的夏令时，所以判断挪进 Python（见 `cio.schedule`）：
cron 每小时敲一次门，窗口外在**发出任何网络请求之前**就退出。
"""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from cio import brief as briefmod          # noqa: E402
from cio import collect, db, deliver, funds, process, render  # noqa: E402
from cio.config import MEMORY_DIR, OUT_DIR, TOPIC_DIR         # noqa: E402
from cio.config import MARKET, market                        # noqa: E402
from cio.models import CollectionStatus                      # noqa: E402
from cio import schedule as sched                            # noqa: E402
from cio.utils import get_logger, now_beijing, safe_filename, truncate  # noqa: E402

log = get_logger("cio.premarket")


def _degraded(*status_dicts) -> list[str]:
    out = []
    for d in status_dicts:
        for k, v in d.items():
            if v != "ok":
                out.append(f"{k}:{v}")
    return out


def _market_stamp(b) -> str:
    """抬头的时间戳跟着**市场**走，不跟着机器走。

    原来写死的是 `{b.dt_beijing}（北京）`。US 模式下正文里印的是 ET，
    抬头和文件名却是北京时间——同一份简报，文件名说 09-02 07:49、
    正文说 09-01 19:49 EDT。两个都对（同一时刻），但**放在一起只会让人
    以为系统错乱了**，而真正错的是发车时间，被这层噪音盖住了。
    """
    return f"{b.dt_ny}" if MARKET == "us" else f"{b.dt_beijing}（北京）"


def _summary_text(b) -> str:
    """Telegram 正文：BLUF 先行，一屏看完要点，全文见 PDF。"""
    lines = [f"*CIO 盘前情报简报* — {_market_stamp(b)}", ""]
    if b.bluf:
        lines.append("*核心要点（BLUF）*")
        for i, s in enumerate(b.bluf, 1):
            lines.append(f"{i}. {truncate(s, 72)}")
        lines.append("")
    if b.fund_flows:
        lines.append("资金面：" + "；".join(truncate(f"{f.name} {f.value}", 56) for f in b.fund_flows[:2]))
    if b.watchlist_hits:
        lines.append(f"关注池命中 {len(b.watchlist_hits)} 条；强信号 "
                     f"{sum(1 for h in b.watchlist_hits if h.signal=='强')} 条。")
    lines.append(f"（采集 {b.status.fetched} / 去重 {b.status.deduped} / 入库 {b.status.ingested_vectors}）")
    lines.append("完整简报见附件 PDF。")
    return "\n".join(lines)


def main(force: bool = False) -> int:
    # **时间闸在最前面，跑在任何取数之前。**
    # 放在后面就等于"每小时采集一次全网新闻再决定要不要发"。
    if not (force or "--force" in sys.argv
            or os.environ.get("CIO_PREMARKET_FORCE") == "1"):
        ok, why = sched.is_premarket()
        if not ok:
            log.info("不在盘前窗口，本次不产出：%s", why)
            log.info("下一班：%s（市场时区）；要立刻跑一份用 --force",
                     sched.next_window_start().strftime("%Y-%m-%d %H:%M %Z"))
            print(f"跳过：{why}")
            return 0
        log.info("盘前窗口内：%s", why)
    db.init_db()
    status_u: dict = {}
    status_s: dict = {}
    raws, anchor, fund_flows = [], [], []

    try:
        raws, anchor = collect.collect_premarket(status_u, status_s)
    except Exception:
        log.error("采集异常:\n%s", traceback.format_exc())
    try:
        fund_flows = funds.collect_funds(status_s)
    except Exception:
        log.error("资金面异常:\n%s", traceback.format_exc())

    try:
        collect.save_raw(raws)
    except Exception:
        log.error("原始归档异常:\n%s", traceback.format_exc())

    try:
        news, deduped = process.dedupe_and_score(raws)
    except Exception:
        log.error("去重打分异常:\n%s", traceback.format_exc())
        news, deduped = [], 0

    try:
        vecs = process.ingest_to_archive(raws)
    except Exception:
        log.error("入库异常:\n%s", traceback.format_exc())
        vecs = 0

    status = CollectionStatus(
        structured=status_s, unstructured=status_u,
        fetched=len(raws), deduped=deduped, ingested_vectors=vecs,
        degraded=_degraded(status_s, status_u),
    )

    b = briefmod.build_premarket(news, anchor, fund_flows, status)

    # 存档 md + PDF。**文件名用市场时区**：归档是按交易日排的，
    # 用机器时区命名会让纽约 09-01 傍晚那份落到 09-02 的档里。
    stamp = sched.market_now().strftime("%Y-%m-%d-%H%M")
    base = f"CIO盘前情报简报+{stamp}"
    md_path = TOPIC_DIR / f"{base}.md"
    pdf_path = OUT_DIR / f"{base}.pdf"
    try:
        md_path.write_text(render.render_brief_md(b), encoding="utf-8")
    except Exception:
        log.error("md 渲染异常:\n%s", traceback.format_exc())
    pdf_ok = True
    try:
        try:
            from cio import render_html
            engine = render_html.render_brief_pdf_styled(b, str(pdf_path))  # mockup 风格：HTML→PDF
            log.info("PDF 用 mockup 风格（%s）", engine)
        except Exception as e:
            log.warning("HTML→PDF 失败(%s)，回退 reportlab 版式", type(e).__name__)
            render.render_brief_pdf(b, str(pdf_path))
        # 同时在 Topic Archive 留一份 PDF
        (TOPIC_DIR / f"{base}.pdf").write_bytes(pdf_path.read_bytes())
    except Exception:
        pdf_ok = False
        log.error("PDF 渲染异常:\n%s", traceback.format_exc())

    # 推送
    try:
        deliver.deliver_brief(_summary_text(b), str(pdf_path) if pdf_ok else "",
                              caption=f"CIO 盘前简报 {_market_stamp(b)}")
    except Exception:
        log.error("推送异常:\n%s", traceback.format_exc())

    # 记账 + 记忆
    try:
        db.insert_brief("premarket", b.title, str(md_path), str(pdf_path) if pdf_ok else "")
        db.log_collection(kind="premarket", fetched=len(raws), deduped=deduped,
                          hits=len(b.watchlist_hits), vectors=vecs,
                          errors=status.errors, degraded=status.degraded)
        mem = MEMORY_DIR / f"{now_beijing().strftime('%Y-%m-%d')}.md"
        with open(mem, "a", encoding="utf-8") as f:
            f.write(f"\n## 盘前 {b.dt_beijing}\n- 采集{len(raws)}/去重{deduped}/入库{vecs}；"
                    f"关注池命中{len(b.watchlist_hits)}；降级：{'；'.join(status.degraded) or '无'}\n")
    except Exception:
        log.error("记账异常:\n%s", traceback.format_exc())

    log.info("盘前完成：采集%d 去重%d 入库%d 命中%d PDF=%s",
             len(raws), deduped, vecs, len(b.watchlist_hits), pdf_ok)
    print(str(md_path))
    return 0


if __name__ == "__main__":
    if "--when" in sys.argv:
        # **只回答"什么时候该跑"，一个网络请求都不发。**
        ok, why = sched.is_premarket()
        print(("现在该跑：" if ok else "现在不该跑：") + why)
        print(f"下一班：{sched.next_window_start().strftime('%Y-%m-%d %H:%M %Z')}（市场时区）")
        print()
        print("\n".join(sched.cron_hint()))
        raise SystemExit(0)
    raise SystemExit(main())
