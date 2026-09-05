#!/usr/bin/env python3
"""盘前早报一键入口：采集 → 处理 → 编撰 → 存档(md+PDF) → Telegram 推送 → 记忆。
每一步都 try/except：任一环节失败仍产出（降级）简报，并在"数据采集状态"如实标注。

用法：
  source .venv/bin/activate            # 或 cio-agent/.venv
  python run_premarket.py              # 正式跑（**只在市场时区的盘前窗口内出简报**）
  python run_premarket.py --force      # 无视时间窗口，立刻跑一份
  python run_premarket.py --when       # 只打印现在算不算盘前、以及该怎么排 cron
  python run_premarket.py --doctor     # **读机器上真正装着的排程**，对不上会指出来
  CIO_MOCK_LLM=1 CIO_TG_DRYRUN=1 python run_premarket.py --force   # 离线冒烟自测

## 时间窗口这道闸是修一个真实故障加的

2026-09-02 这份简报送达时是**纽约 09-01 晚上 19:49** —— 收盘四小时之后。
简报本身完全正确（英文抬头、ET 时间戳、美股期货、当天真实新闻），
**错的只有发车时间**。

我当时把病因断成"机器在北京时区，本地 07:00 就是纽约 19:00"。
**那个诊断是错的**：2026-09-05 实测这台机器是 `EDT`（`date` 显示
`Fri Sep  4 22:19:54 EDT 2026`），机器本地时间就是市场时间，
`0 7 * * 1-5` 在这台机器上本来就是美东 07:00。

所以 19:49 那一班是别的原因——**而我到现在还不知道是哪一个**。
用 `--doctor` 去读机器上真正装着的排程，不要再靠推测。

不管病因是什么，这里有两道防线，**它们都不依赖我诊断对**：

1. **闸门在最前面**，跑在任何网络请求之前（`cio.schedule.is_premarket`）。
   时区判断在 Python 里算，不靠 cron ——cron 跟不了另一个国家的夏令时。
2. **绕过闸门必须在简报上看得见。** `--force` 允许存在，但用它产出的简报
   会在 Telegram 正文、抬头和存档文件名上都带一个窗口外标记。
   上次那次故障的要害是：**一份 19:49 发的简报和一份 07:00 发的长得一模一样。**
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


OUT_OF_WINDOW_MARK = "　⚠窗口外"
"""窗口外产出的标记。**它出现在正文第一行、caption、和存档文件名上。**

三处都要，因为这三处是简报被人看到的三条路——只标一处，
另外两条路上的那份仍然和正点发的长得一模一样。
"""


def archive_base(stamp: str, out_of_window: bool = False) -> str:
    """存档文件名。**窗口外产出的必须认得出来。**

    归档里躺着的两份长得一样，三个月后没人知道哪一份是凌晨正点发的、
    哪一份是晚上 `--force` 补跑的——而这正是上次那次故障的形状。
    """
    return f"CIO盘前情报简报+{stamp}" + ("+窗口外" if out_of_window else "")


def _summary_text(b, out_of_window: bool = False) -> str:
    """Telegram 正文：BLUF 先行，一屏看完要点，全文见 PDF。

    **窗口外产出的第一行就要说清楚。** 上次故障不是发错了时间，
    是发错时间的那份看不出来发错了时间。
    """
    head = f"*CIO 盘前情报简报* — {_market_stamp(b)}"
    lines = [head + (OUT_OF_WINDOW_MARK if out_of_window else ""), ""]
    if out_of_window:
        ok, why = sched.is_premarket()
        lines += [f"⚠ *这份不是在盘前窗口内产出的*：{why}",
                  "（用 --force 绕过了时间闸。内容照常，但**发车时间不对**。）", ""]
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
    forced = bool(force or "--force" in sys.argv
                  or os.environ.get("CIO_PREMARKET_FORCE") == "1")
    in_window, why = sched.is_premarket()
    if not forced:
        if not in_window:
            log.info("不在盘前窗口，本次不产出：%s", why)
            log.info("下一班：%s（市场时区）；要立刻跑一份用 --force",
                     sched.next_window_start().strftime("%Y-%m-%d %H:%M %Z"))
            print(f"跳过：{why}")
            return 0
        log.info("盘前窗口内：%s", why)
    elif not in_window:
        # **绕过闸门是允许的，隐瞒绕过不允许。**
        # 上次故障的要害不是"发错了时间"，是"发错时间的那份和发对时间的
        # 长得一模一样"。所以这里不拦，只保证它在成品上藏不住。
        log.warning("**窗口外强制产出**：%s —— 简报会带窗口外标记", why)
    _out_of_window = forced and not in_window
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
    base = archive_base(stamp, _out_of_window)
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
        mark = OUT_OF_WINDOW_MARK if _out_of_window else ""
        deliver.deliver_brief(_summary_text(b, _out_of_window),
                              str(pdf_path) if pdf_ok else "",
                              caption=f"CIO 盘前简报 {_market_stamp(b)}{mark}")
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


def doctor() -> int:
    """**读机器上真正装着的排程，不推测。**

    2026-09-01 那份 19:49 的简报，我的病因诊断（"机器在北京时区"）
    在 09-05 被 `date` 一条命令否掉了。同一轮里我还猜错过两次别的。
    共同点是：**我照着一个说得通的故事往下推，而不是先取那个能一击定案的数。**

    所以这里不解释，只把三件事并排印出来：
    程序认为该几点跑、机器上装了什么、两者对不对得上。
    """
    import plistlib
    import subprocess

    print("=" * 60)
    print("一、程序认为该几点跑（时区在 Python 里算，不靠 cron）")
    ok, why = sched.is_premarket()
    print(f"  现在{'算' if ok else '不算'}盘前：{why}")
    print(f"  市场现在　　{sched.market_now().strftime('%Y-%m-%d %H:%M %Z')}")
    import datetime as _dt
    mine = _dt.datetime.now().astimezone()
    print(f"  这台机器现在{mine.strftime('%Y-%m-%d %H:%M %Z')}（{mine.tzname()}）")
    lo, hi = sched.window()
    print(f"  盘前窗口（市场）{lo.strftime('%H:%M')}–{hi.strftime('%H:%M')}")
    print("  盘前窗口（本机）{}–{}".format(*sched.local_window()))
    print(f"  下一班　　　{sched.next_window_start().strftime('%Y-%m-%d %H:%M %Z')}")

    print()
    print("二、机器上真正装着什么")
    found = False
    home = Path.home()
    for plist in sorted((home / "Library" / "LaunchAgents").glob("*cio*.plist")):
        found = True
        print(f"  launchd  {plist.name}")
        try:
            d = plistlib.loads(plist.read_bytes())
        except Exception as e:                                 # noqa: BLE001
            print(f"    读不动：{type(e).__name__}: {e}")
            continue
        args = d.get("ProgramArguments") or []
        print(f"    命令   {' '.join(str(a) for a in args)}")
        if any("--force" in str(a) for a in args):
            print("    **命令里带 --force —— 时间闸被绕过，任何时刻都会发**")
        cal = d.get("StartCalendarInterval")
        cal = cal if isinstance(cal, list) else ([cal] if cal else [])
        if not cal:
            print("    **没有 StartCalendarInterval —— 它不会按时间触发**")
        for c in cal:
            wd = c.get("Weekday")
            print(f"    触发   周{wd if wd is not None else '?'} "
                  f"{c.get('Hour', '?'):0>2}:{c.get('Minute', 0):0>2}（本机时间）")
        if any(c.get("Weekday") is None for c in cal):
            print("    **没有 Weekday 键 —— 它每天都触发，包括周末**")
        hours = {c.get("Hour") for c in cal if c.get("Hour") is not None}
        wl, wh = sched.local_window()
        want, want_hi = int(wl.split(":")[0]), int(wh.split(":")[0])
        if hours and not any(want <= h <= want_hi for h in hours):
            print(f"    **对不上**：本机盘前窗口是 {wl}–{wh}，而它排在 "
                  f"{sorted(hours)} 点")
            # **窗口外的任务不是"发错时间"，是"什么都不发"。**
            # 时间闸修好之后它每天照常触发、照常退出，静悄悄地什么都不做——
            # 一个不发简报的早晨和一个没跑过的早晨长得一模一样。
            print("    **后果不是发错时间，是什么都不发**：时间闸会让它当场退出。")
            logf = Path(__file__).resolve().parent / "logs" / "premarket.out.log"
            if logf.exists():
                try:
                    txt = logf.read_text("utf-8", errors="replace")
                    n_skip = txt.count("跳过")
                    print(f"    日志里已经有 {n_skip} 次「跳过」"
                          f"（{logf}）")
                except Exception:                              # noqa: BLE001
                    pass
            print("    修： bash scripts/install_launchd.sh   （小时数从窗口现算）")
    try:
        cr = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10)
        lines = [x for x in (cr.stdout or "").splitlines()
                 if "premarket" in x or "cio" in x.lower()]
        for x in lines:
            found = True
            print(f"  cron     {x.strip()}")
            if "--force" in x:
                print("    **这行带 --force —— 时间闸被绕过**")
    except Exception:                                          # noqa: BLE001
        pass
    if not found:
        print("  **没找到任何 cio 相关的 launchd / cron 条目。**")
        print("  也就是说：现在没有任何东西会自动发盘前简报。")
        print("  装一个： bash scripts/install_launchd.sh")

    print()
    print("三、建议")
    print("\n".join("  " + x for x in sched.cron_hint()))
    return 0


if __name__ == "__main__":
    if "--doctor" in sys.argv:
        raise SystemExit(doctor())
    if "--when" in sys.argv:
        # **只回答"什么时候该跑"，一个网络请求都不发。**
        ok, why = sched.is_premarket()
        print(("现在该跑：" if ok else "现在不该跑：") + why)
        print(f"下一班：{sched.next_window_start().strftime('%Y-%m-%d %H:%M %Z')}（市场时区）")
        print()
        print("\n".join(sched.cron_hint()))
        raise SystemExit(0)
    raise SystemExit(main())
