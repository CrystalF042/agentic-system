#!/usr/bin/env python3
"""证券二部 Admission Gate 入口 —— 因子准入闸。

先定后测；纯净窗口每个研究只能花一次（由台账强制）。

用法：
  python run_gate.py status
  python run_gate.py closeout                    # 幂等应用已决定的研究收尾（CLOSED_FAIL / VOID）
  python run_gate.py register UB-US-002 "低波在美股大盘呈反向效应" --factors 低波 --horizons 20
  python run_gate.py develop  UB-US-002          # 在已烧毁窗口上检验（可重复）
  python run_gate.py candidates                  # 列出所有 development 已通过、待确认的候选
  python run_gate.py batch BATCH-01              # 把这些候选打包成一个确认批次（先定后测）
  python run_gate.py confirm-batch BATCH-01      # 纯净窗口一次性确认整批（批内 Holm 校正）
  python run_gate.py library                     # 查看研究因子库

说明：--factors 用引擎内部因子名（动量/反转/低波/趋势/量能），或 composite。
取数一次拉满本地缓存（10 年），再由闸按日期切成两段，不重复下载。
"""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from cio import gate, ledger, quant_data          # noqa: E402
from cio.config import TOPIC_DIR                  # noqa: E402
from cio.utils import file_stamp, get_logger      # noqa: E402

log = get_logger("cio.gate.run")
FULL_DAYS = int(os.environ.get("CIO_GATE_DAYS", "2500"))    # 拉满本地 10 年缓存


def _load():
    limit = int(os.environ.get("CIO_UB_LIMIT", "0"))
    stocks, src = quant_data.get_universe(limit=limit)
    status: dict = {}
    panels = quant_data.get_history(stocks, days=FULL_DAYS, status=status)
    log.info("取数：%d 只，%s", len(panels), status.get("quant_history", ""))
    return stocks, panels


def _save(study_id: str) -> str:
    p = TOPIC_DIR / f"AdmissionGate_{study_id}+{file_stamp()}.md"
    p.write_text(gate.render(study_id), encoding="utf-8")
    return str(p)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cmd = sys.argv[1]
    try:
        if cmd == "status":
            print(ledger.summary())
            return 0

        if cmd == "closeout":
            # 幂等：把 2026-08 定稿的收尾决定写进台账（UB-US-008 CLOSED_FAIL / UB-US-002 VOID）。
            # 刻意做成一条【显式命令】而不是报告运行时的副作用——台账只增不改，
            # 收尾是一次有意的、留时间戳和理由的行为，不该悄悄发生。
            for sid, msg in ledger.apply_closeout():
                print(f"  {sid:14} {msg}")
            print()
            print(ledger.summary())
            return 0

        if cmd == "register":
            sid, hyp = sys.argv[2], sys.argv[3]
            factors, horizons, cur = [], [], None
            crit = dict(gate.DEFAULT_CRITERIA)
            note = ""
            args = sys.argv[4:]
            k = 0
            while k < len(args):
                a = args[k]
                if a == "--factors":
                    cur = factors
                elif a == "--horizons":
                    cur = horizons
                elif a == "--step":
                    k += 1; crit["step"] = int(args[k]); cur = None
                elif a == "--sign":
                    k += 1; crit["require_sign"] = args[k]; cur = None
                elif a == "--neutral":
                    crit["sector_neutral"] = True; cur = None
                elif a == "--note":
                    k += 1; note = args[k]; cur = None
                elif cur is factors:
                    factors.append(a)
                elif cur is horizons:
                    horizons.append(int(a))
                k += 1
            ledger.register(sid, hyp, factors or ["composite"], horizons or [20], crit, note)
            print(f"冻结规格：{crit}")
            print(f"已注册 {sid}\n{ledger.summary()}")
            return 0

        if cmd == "library":
            from cio import factors as FL
            print(f"{'因子':12} {'家族':10} {'最少历史':>8}  说明")
            for n, spec in FL.LIBRARY.items():
                # 这五个是【历史打分模型用到的因子】，不是"生产集"。
                # UB-US-001 整体 FAIL，生产集是空的；沿用旧标签会让人以为它们已获准入。
                prod = " [legacy UB-US-001 model · not admitted]" if n in __import__(
                    "cio.unit_b", fromlist=["x"])._FACTORS else ""
                print(f"{n:12} {spec['family']:10} {spec['min_hist']:>8}  {spec['desc']}{prod}")
            print(f"\nProduction Factor Set（已通过准入闸）：{ledger.production_factors() or '（无）'}")
            return 0

        if cmd == "candidates":
            c = gate.collect_candidates()
            if not c:
                print("暂无待确认候选（需先 develop 且通过）")
            for x in c:
                print(f"  {x['study']:14} {x['factor']:12} fwd{x['horizon']:<4} dev_IC={x.get('dev_ic')}")
            return 0

        if cmd == "batch":
            bid = sys.argv[2]
            c = gate.collect_candidates()
            if not c:
                print("[闸门拒绝] 无 development 通过的候选，不得动用纯净窗口")
                return 1
            b = ledger.register_batch(bid, c, gate.DEFAULT_CRITERIA)
            print(f"已注册批次 {bid}：{len(c)} 个候选，纯净窗口第 {b['holdout_use_index']} 次使用")
            for x in c:
                print(f"  {x['study']:14} {x['factor']:12} fwd{x['horizon']}")
            return 0

        if cmd == "confirm-batch":
            bid = sys.argv[2]
            stocks, panels = _load()
            r = gate.run_batch_confirmation(bid, panels, stocks)
            print(gate.render_batch(bid))
            p = TOPIC_DIR / f"AdmissionGate_batch_{bid}+{file_stamp()}.md"
            p.write_text(gate.render_batch(bid), encoding="utf-8")
            print(f"\n批次结果：{'PASS' if r.get('passed') else 'FAIL'}  →  {p}")
            return 0

        if cmd in ("develop", "confirm"):
            sid = sys.argv[2]
            force = "--force" in sys.argv
            stocks, panels = _load()
            if cmd == "develop":
                r = gate.run_development(sid, panels, stocks)
            else:
                r = gate.run_confirmation(sid, panels, stocks, force=force)
            print(gate.render(sid))
            print(f"\n结果：{'PASS' if r.get('passed') else 'FAIL'}  →  {_save(sid)}")
            return 0

        print(__doc__)
        return 2
    except ValueError as e:
        print(f"\n[闸门拒绝] {e}\n")      # 预期内的制度性拒绝，不是崩溃
        return 1
    except Exception:
        log.error("异常:\n%s", traceback.format_exc())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
