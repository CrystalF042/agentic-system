#!/usr/bin/env python3
"""证券一部（Trading Unit A）一键入口。
多空辩论（本地 gpt-oss）+ 回测支撑 → 《一部建议》→ 归档 → Telegram。
研究观点、非投资指令；只回测不实盘；与二部/CIO 独立。

用法：
  source .venv/bin/activate
  python run_unit_a.py "工商银行"
  python run_unit_a.py AAPL
  CIO_UNIT_A_SUBJECT="英伟达" python run_unit_a.py            # 钩子/路由用
  CIO_MOCK_LLM=1 CIO_TG_DRYRUN=1 python run_unit_a.py "苹果"   # 离线冒烟自测
  python run_unit_a.py "NVDA" --force                        # 强制复研（见下）
  python run_unit_a.py "NVDA" --json                         # 结构化输出（给界面用）

Evidence Gate（build66）：没有新的可解释信息，就不制造新的观点。
  INSUFFICIENT（0 条实质材料）→ 一部不启动，Formal vote: ABSTAIN，0 次 LLM 调用
  THIN        （1–2 条）      → 启动，但信心上限「弱」
  SUFFICIENT  （≥3 条）       → 完整 6 次调用对抗流程

--force / UNIT_A_FORCE_RESEARCH=1 会在 INSUFFICIENT 时仍然启动。
这是【有意的人工决定】——首次建仓前、季度复审、既有论点到期、重大决策前重新审视，
与自动日常运行严格区分：报告会标明分析依据的是既有证据集，不是新证据。
"""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from cio import deliver, runid, unit_a     # noqa: E402
from cio.utils import get_logger, stage    # noqa: E402

log = get_logger("cio.run_unit_a")


def main() -> int:
    raw = sys.argv[1:]
    as_json = "--json" in raw
    argv = [a for a in raw if a not in ("--force", "-f", "--json")]
    force = any(a in ("--force", "-f") for a in raw)
    say = (lambda *a, **k: None) if as_json else print
    subject = (os.environ.get("CIO_UNIT_A_SUBJECT") or (argv[0] if argv else "")).strip()
    if not subject:
        print('用法：python run_unit_a.py "标的名/代码" [--force] [--json]')
        return 2

    # **第一件事就是把身份发出去。** 界面在运行的第 0 秒就要知道这次是谁，
    # 才能把后续所有阶段事件和最终结果挂到同一个 run 上——
    # 否则它只能等跑完再问"最近一次是什么"，而那正是并发下会答错的问题。
    stage("run_id", unit_a.RUN_ID)
    stage("start", f"{subject}{'（强制复研）' if force else ''}")

    try:
        r = unit_a.build_unit_a(subject, force=force)
    except Exception:
        log.error("一部建议编撰异常:\n%s", traceback.format_exc())
        stage("failed", "编撰异常，详见 stderr")
        if as_json:
            import json as _json
            print(_json.dumps(runid.envelope("unit_a", unit_a.RUN_ID, status="failed",
                                             subject=subject,
                                             error="build_unit_a 抛出异常，详见 stderr"),
                              ensure_ascii=False))
        return 1

    md_path, pdf_path = unit_a.archive_and_render(r)

    try:
        # **一部不推送仓位。** 架构冻结 v1.0：PC 是唯一给仓位的地方。
        # 旧的这一行往 Telegram 上发「目标仓位：中仓」，同一个频道上还有老 CRO 的
        # 「建议总仓位：中仓」和新链路的「CRO 给约束，PC 给权重」——
        # **三个互相矛盾的仓位口径发在同一个群里**，谁看谁糊涂。
        # 换成 Evidence Gate：那才是一部这一轮真正产出的东西。
        gline = f"Evidence Gate：{r.gate_level or '未判定'}"
        if r.material_verdict:
            gline += f"（{r.material_verdict}，{r.material_count} 条材料实质 {r.material_substantive} 条）"
        if r.formal_vote:
            gline += f" ｜ Formal vote: {r.formal_vote}"
        if r.forced:
            gline += " ｜ 人工强制复研"
        if r.conviction_capped:
            gline += f" ｜ 信心由「{r.conviction_capped}」封顶为「{r.conviction}」"
        summary = (f"*证券一部建议 · {r.resolved}*（{r.dt_beijing} 北京）\n"
                   f"方向：{r.direction} ｜ 信心：{r.conviction}\n"
                   f"{gline}\n"
                   f"（LLM 多空辩论 + 回测；研究观点，非投资指令。"
                   f"**仓位由 PC 决定，一部不给仓位。**）\n完整建议见附件 PDF。")
        deliver.deliver_brief(summary, pdf_path or "", caption=f"证券一部建议 {r.resolved}")
    except Exception:
        log.error("推送异常:\n%s", traceback.format_exc())

    log.info("一部建议完成：%s 方向=%s 信心=%s Gate=%s 材料=%d",
             r.resolved, r.direction, r.conviction, r.gate_level or "未判定", r.material_count)
    if as_json:
        # **产出端已经把结构化写在磁盘上了，这里原样读回来再打印。**
        # 不从 r 上重新 dump 一份——两条序列化路径迟早会长出差异，
        # 而"页面上的字段和归档文件里的字段不一样"是查不出来的那种 bug。
        import json as _json
        jp = Path(unit_a.advice_json_path(md_path))
        if not jp.exists():
            print(_json.dumps(runid.envelope("unit_a", r.run_id or unit_a.RUN_ID,
                                             status="failed", subject=subject,
                                             error=f"结构化输出未生成：{jp}"),
                              ensure_ascii=False))
            return 1
        print(jp.read_text(encoding="utf-8"))
        return 0
    say(md_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
