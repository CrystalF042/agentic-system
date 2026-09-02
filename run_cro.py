#!/usr/bin/env python3
"""风控部（CRO）一键入口。
对当日两线选股做独立风控评级（五维风险 + 一票否决 + 投资倾向）→ 归档 → Telegram。
零 LLM、纯确定性；研究观点、非投资指令，须经 CEO 终批。

用法：
  source .venv/bin/activate
  python run_cro.py                 # 对当日二部量化选股做风控（一部日频选股接入后会一并纳入）
  CIO_QUANT_MOCK=1 CIO_TG_DRYRUN=1 python run_cro.py   # 离线冒烟
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from cio import cro, deliver, legacy_guard, ledger, unit_b   # noqa: E402
from cio.models import DailyPick                # noqa: E402
from cio.utils import get_logger               # noqa: E402

log = get_logger("cio.run_cro")


def unitb_to_picks(adv) -> list[DailyPick]:
    return [DailyPick(source="二部", code=p.code, name=p.name, yahoo=p.yahoo,
                      direction="看多", score=p.composite, sector=p.sector) for p in adv.picks]


def unitb_picks_or_abstain() -> list[DailyPick]:
    """二部是否给方向性选股，由【台账】决定，不是硬编码。

    Production Factor Set 为空 = 没有任何因子通过准入闸 = 二部没有已验证的预测能力，
    因此正式方向性投票 ABSTAIN，不向 CRO 提交任何选股。
    二部当天的贡献改由 Systematic Analytics 报告承担（测量，不是判断）。

    这条判断读台账而不是写死 return []，但它【不是】一个自动恢复开关：
    恢复投票要求生产集与实际驱动打分的因子集完全一致（见 ledger.alpha_vote_allowed）。
    只有某一个因子通过闸门是不够的——那只说明那个因子有效，
    不说明那个把它和另外四个已被证伪的因子等权相加的模型有效。
    """
    ok, why = ledger.alpha_vote_allowed(unit_b._FACTORS)
    if not ok:
        log.info("二部 ABSTAIN：%s。本日不提交方向性选股；"
                 "风险测量见 run_unit_b.py 的 Systematic Analytics 报告", why)
        return []
    log.warning("二部恢复方向性投票（%s）——请人工确认这是预期行为", why)
    return unitb_to_picks(unit_b.build_unit_b(top_n=3))


def main() -> int:
    try:
        picks = unitb_picks_or_abstain()
        # TODO：一部日频3选接入后，picks += 一部选股（source="一部"）
        if not picks:
            log.info("当日无任何选股进入风控（二部 ABSTAIN，一部日频尚未接入）")
        r = cro.build_cro(picks)
    except Exception:
        log.error("CRO 风控评级异常:\n%s", traceback.format_exc())
        return 1

    md_path, pdf_path = cro.archive_and_render(r)
    try:
        tbl = "\n".join(f"· {it.source} {it.code} {it.name}：{'❌否决' if it.vetoed else it.rating}（风险分{it.risk_score:.2f}）"
                        for it in r.items) or "（无选股）"
        summary = (f"*CRO 风控评级*（{r.dt_beijing} 北京）\n"
                   f"投资倾向：{r.leaning} ｜ 建议总仓位：{r.target_position}\n{tbl}\n"
                   f"送 CEO 终批：{len(r.approved_candidates)} 只，否决 {r.vetoed_count} 只\n"
                   f"（零 LLM 独立风控；研究观点，非投资指令，须经 CEO 决断）\n完整评级见附件 PDF。")
        if legacy_guard.legacy_push_allowed("run_cro.py"):
            deliver.deliver_brief(summary, pdf_path or "", caption="CRO 风控评级（退役模块）")
    except Exception:
        log.error("推送异常:\n%s", traceback.format_exc())

    log.info("CRO 完成：倾向=%s 仓位=%s 否决=%d", r.leaning, r.target_position, r.vetoed_count)
    print(md_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
