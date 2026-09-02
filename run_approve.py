#!/usr/bin/env python3
"""CEO 审批入口 —— **授权这一层的手动操作口**。

    PC 产生目标   →   CEO 产生授权   →   Execution 产生交易   →   Ledger 产生事实
                      ↑ 本入口

批准的是**一个已经算好的整数股数**，不是权重。批完它就固定了，
T+1 开盘按实际开盘价成交这个数量。

用法：
    CIO_MARKET=us python run_approve.py                       看待批清单（默认）
    CIO_MARKET=us python run_approve.py --approve 12          按提案号批
    CIO_MARKET=us python run_approve.py --approve NVDA        按代码批（唯一时）
    CIO_MARKET=us python run_approve.py --approve 12 14 15    一次批多条
    CIO_MARKET=us python run_approve.py --approve-all
    CIO_MARKET=us python run_approve.py --reject 12 --reason "估值太贵，等回调"
    CIO_MARKET=us python run_approve.py --history 12          看这条的状态变更留痕
    CIO_MARKET=us python run_approve.py --json

两条守则：

**合规破限的提案默认批不了。** 想强行批要显式加 `--force`，
而且那次强批会写进事件日志、带上是谁批的。事前合规存在的意义就是
在批准之前拦一道；批完再看等于没看。

**同一代码有多条待批时拒绝执行，不自动挑一条。** 自动挑就是替人
做了一个他没做的决定。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from cio import compliance, portfolio, proposal_store, runid       # noqa: E402
from cio.config import market                                      # noqa: E402
from cio.db import connect                                         # noqa: E402
from cio.utils import get_logger                                   # noqa: E402

log = get_logger("cio.run_approve")
RUN_ID = runid.new_run_id("ap")


def _pid(argv) -> str:
    if "--portfolio" in argv:
        i = argv.index("--portfolio")
        if i + 1 < len(argv):
            return argv[i + 1]
    return portfolio.MARKET_PORTFOLIO.get(
        market().get("news_region", "us"), portfolio.US_PAPER)


def _values_after(argv, flag) -> list:
    """取 `--approve a b c` 后面的连续值（遇到下一个 -- 开头就停）。"""
    if flag not in argv:
        return []
    out = []
    for a in argv[argv.index(flag) + 1:]:
        if a.startswith("--"):
            break
        out.append(a)
    return out


def _resolve(ref: str, pid: str) -> tuple:
    """ref → 一条提案。返回 (row, 错误说明)。"""
    rows = proposal_store.get_by_ref(ref, pid)
    if not rows:
        return None, f"{ref}：找不到对应提案（用不带参数的 run_approve.py 看清单）"
    if len(rows) > 1:
        ids = "、".join(f"#{r['id']}({r['state']})" for r in rows)
        return None, (f"{ref}：有 {len(rows)} 条同代码提案 {ids} —— "
                      f"**不自动挑一条**，请用提案号")
    return rows[0], ""


def _act(refs, pid, to_state, actor, reason, force) -> int:
    bad = 0
    for ref in refs:
        row, err = _resolve(ref, pid)
        if err:
            print(f"  ✗ {err}")
            bad += 1
            continue
        if (to_state == proposal_store.APPROVED
                and row["compliance_status"] == compliance.BREACH and not force):
            print(f"  ✗ #{row['id']} {row['ticker']}：**事前合规破限，默认不能批**。"
                  f"要强行批准请加 --force（会写进事件日志）。")
            bad += 1
            continue
        try:
            note = reason or ("强行批准（已知合规破限）" if force else "")
            new = proposal_store.transition(row["id"], to_state, actor=actor, note=note)
        except ValueError as e:
            print(f"  ✗ {e}")
            bad += 1
            continue
        verb = "已批准" if to_state == proposal_store.APPROVED else "已否决"
        print(f"  ✓ #{new['id']} {new['ticker']} {verb}"
              f"　{new['action']} {new['delta_shares']:+d} 股"
              f"　有效至 {new['expires_on']}"
              + (f"　理由：{reason}" if reason else ""))
    return bad


def _history(pid_ref: str) -> int:
    proposal_store.init()
    with connect() as con:
        rows = list(con.execute(
            "SELECT at, from_state, to_state, actor, note FROM proposal_event "
            "WHERE proposal_id=? ORDER BY id", (int(pid_ref),)))
    p = proposal_store.get(int(pid_ref))
    if not p:
        print(f"提案 #{pid_ref} 不存在")
        return 1
    print(f"提案 #{p['id']} {p['ticker']}　{p['action']} {p['delta_shares']:+d} 股"
          f"　决策日 {p['decision_date']}　当前 {p['state']}")
    for at, f, t, actor, note in rows:
        print(f"  {at}　{f} → {t}　{actor}" + (f"\n      {note}" if note else ""))
    return 0


def main() -> int:
    argv = sys.argv[1:]
    pid = _pid(argv)
    force = "--force" in argv
    reason = ""
    if "--reason" in argv:
        i = argv.index("--reason")
        reason = argv[i + 1] if i + 1 < len(argv) else ""

    if "--history" in argv:
        v = _values_after(argv, "--history")
        return _history(v[0]) if v else 1

    if "--json" in argv:
        import json as _json
        print(_json.dumps(runid.envelope(
            "approve", RUN_ID, status="completed", portfolio_id=pid,
            pending=proposal_store.pending(pid),
            approved=proposal_store.approved(pid),
            stats=proposal_store.stats(pid)), ensure_ascii=False, default=str))
        return 0

    approve = _values_after(argv, "--approve")
    reject = _values_after(argv, "--reject")

    if "--approve-all" in argv:
        approve = [str(r["id"]) for r in proposal_store.pending(pid)]
        if not approve:
            print(f"{pid}：没有待批准的提案。")
            return 0
        print(f"批准全部 {len(approve)} 条：")

    if approve:
        bad = _act(approve, pid, proposal_store.APPROVED, f"ceo:{RUN_ID}", reason, force)
    elif reject:
        if not reason:
            print("否决建议带上 --reason \"...\" —— 三个月后回看，"
                  "「为什么当时没做」和「为什么当时做了」一样重要。")
        bad = _act(reject, pid, proposal_store.REJECTED, f"ceo:{RUN_ID}", reason, force)
    else:
        print(proposal_store.render_pending(pid))
        ap = proposal_store.approved(pid)
        if ap:
            print(f"\n已批准待成交 {len(ap)} 条：")
            for r in ap:
                print(f"  #{r['id']:<5} {r['ticker']:<6} {r['action']:<5} "
                      f"{r['delta_shares']:+d} 股　决策日 {r['decision_date']}"
                      f"　有效至 {r['expires_on']}")
            print("\n下一步：CIO_MARKET=us python run_execute.py")
        print("\n批准 = 固定那个整数股数，T+1 开盘按实际开盘价成交它。")
        print("批准：python run_approve.py --approve <号|代码>")
        print("否决：python run_approve.py --reject <号|代码> --reason \"...\"")
        return 0

    if not approve and not reject:
        return 0
    ap = proposal_store.approved(pid)
    if ap:
        print(f"\n现在有 {len(ap)} 条已批准待成交。"
              f"下一步：CIO_MARKET=us python run_execute.py")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
