#!/usr/bin/env python3
"""Evidence Gate 扫描 —— 今天哪几只标的**真的有增量事实**（确定性，零 LLM）。

    CIO_MARKET=us python run_scan.py NVDA AVGO AMD MU TSM
    CIO_MARKET=us python run_scan.py --watchlist
    CIO_MARKET=us python run_scan.py NVDA AVGO --verbose      逐条列材料与判定理由
    CIO_MARKET=us python run_scan.py NVDA AVGO --json         结构化输出（给界面用）

**这个脚本一次模型都不调。** 它只做一部完整流程的前两步——采集材料、
过实质度闸门——然后告诉你哪几只值得跑完整的一部。

为什么需要它：一部按定义是 **evidence-triggered** 的，不是每日评论台。
不先扫就逐只跑，等于每只都花几分钟做辩论，然后多数在闸门那里被拦下——
时间花在了"确认今天没新闻"上。扫描把这件事从几分钟压到几秒，
而且**用的是同一个闸门、同一份采集代码**（`unit_a.collect_materials`），
不是另写一套近似规则。两套规则一定会漂移，漂移之后没人知道以哪个为准。

退出码：有任何一只达到 THIN 或以上返回 0，全部 INSUFFICIENT 返回 1——
方便串进 `run_scan.py ... && run_unit_a.py ...` 这种命令链。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from cio import material_gate, runid, unit_a        # noqa: E402
from cio.utils import get_logger, stage             # noqa: E402

log = get_logger("cio.run_scan")
RUN_ID = runid.new_run_id("sc")

_ORDER = {material_gate.SUFFICIENT: 0, material_gate.THIN: 1, material_gate.INSUFFICIENT: 2}
# **界面（含这个命令行）不重新解释闸门。** 这里原来写死了
# 「THIN → 信心将封顶为弱」——那是把 material_gate 的规则又抄了一遍，
# 闸门哪天改了封顶档，这行不会报错，只会开始说假话。
# 现在只做 状态 → 显示 的映射，档位含义一律从 assess() 的返回值里取。
_MARK = {material_gate.SUFFICIENT: "✅ 跑一部", material_gate.THIN: "◐ 可跑",
         material_gate.INSUFFICIENT: "— 不跑"}


def _watchlist() -> list:
    """从配置里的观察池取标的。取不到就返回空——**不猜一个默认列表**。"""
    try:
        from cio import quant_data
        stocks, _src = quant_data.get_universe(limit=20)
        return [s.yahoo or s.code for s in stocks]
    except Exception as e:                           # noqa: BLE001
        log.warning("观察池取不到：%s", e)
        return []


def scan_one(symbol: str, verbose: bool = False) -> dict:
    """采集 + 判定。**不调模型、不写台账、不生成报告。**"""
    try:
        c = unit_a.collect_materials(symbol)
    except Exception as e:                           # noqa: BLE001
        log.warning("%s 采集失败：%s", symbol, e)
        # **失败行的字段必须齐全。** 缺键的行会让界面在某几行上取到 None，
        # 而 None 在页面上和"没有封顶"长得一样——契约里不许有形状不同的行。
        return {"symbol": symbol, "resolved": symbol, "error": str(e),
                "level": material_gate.INSUFFICIENT, "verdict": "采集失败",
                "n": 0, "n_sub": 0, "n_sub_events": 0, "n_ctx": 0, "n_empty": 0,
                "activate": False, "conviction_cap": "", "banner": "",
                "intake": {}, "intake_note": ""}
    materials = c["materials"]
    g = material_gate.assess(materials)
    # **派生状态由 engine 给，不由调用方推。** activate / conviction_cap / banner
    # 都是闸门算好的结论；界面拿来直接显示，永远不必知道规则是什么。
    out = {"symbol": symbol, "resolved": c["subj"], "level": g["level"],
           "verdict": g["verdict"], "n": g["n"], "n_sub": g["n_sub"],
           # **两个数都要出来。** 只给归并后的数，"今天只有一件事"和
           # "我们把三条并成了一条"在界面上会长得一模一样。
           "n_sub_events": g["n_sub_events"],
           "n_ctx": g["n_ctx"], "n_empty": g["n_empty"],
           "activate": g["activate"], "conviction_cap": g["conviction_cap"],
           "banner": g["banner"], "error": "",
           # **进料口径也要出来。** 只报"N 条材料，实质 0"的话，
           # "今天确实没东西"和"我们只看了其中 N 条"在页面上一模一样。
           "intake": c.get("intake") or {},
           "intake_note": unit_a.intake_note(c)}
    if verbose:
        out["items"] = [(m.id, g["labels"].get(m.id, ("?", ""))[0],
                         g["labels"].get(m.id, ("?", ""))[1], m.text)
                        for m in materials]
    return out


def main() -> int:
    argv = [a for a in sys.argv[1:]]
    verbose = "--verbose" in argv
    as_json = "--json" in argv
    argv = [a for a in argv if a not in ("--verbose", "--json")]
    syms = _watchlist() if "--watchlist" in argv else [a for a in argv if not a.startswith("--")]
    if not syms:
        print(__doc__)
        return 2

    # --json：**stdout 只有 JSON，一个字节的人读文本都不许有。**
    # 日志走 stderr（get_logger 用的是 StreamHandler），所以两者天然分开；
    # 但人读的 print 必须整体关掉，否则调用方拿到的就是一段掺了中文的非法 JSON。
    stage("run_id", RUN_ID)
    stage("start", f"{len(syms)} 只")

    if as_json:
        import json as _json
        from cio import collect
        rows = [scan_one(s, True) for s in syms]
        rows.sort(key=lambda r: (_ORDER.get(r["level"], 9), -r["n_sub"]))
        print(_json.dumps(runid.envelope(
            "scan", RUN_ID,
            status="ok" if any(r["activate"] for r in rows) else "no_evidence",
            rows=rows,
            n_materials=sum(r["n"] for r in rows),
            n_substantive=sum(r["n_sub"] for r in rows),
            n_substantive_events=sum(r.get("n_sub_events", r["n_sub"])
                                     for r in rows),
            # 跳过的源必须一起交出去。界面只显示"全部无实质"而不显示
            # "有两个源今天挂了"，用户就会把数据缺失读成市场没消息。
            dead_feeds={k: {"fails": v[0], "error": v[1]}
                        for k, v in collect.dead_feeds().items()},
        ), ensure_ascii=False))
        stage("done", f"实质 {sum(r['n_sub'] for r in rows)} 条")
        return 0 if any(r["level"] != material_gate.INSUFFICIENT for r in rows) else 1

    print("=" * 74)
    print(f"Evidence Gate 扫描　{len(syms)} 只　（零 LLM：只做采集 + 实质度判定）")
    print("=" * 74)

    rows = []
    for s in syms:
        r = scan_one(s, verbose)
        rows.append(r)
        cap = f"（信心将封顶为「{r['conviction_cap']}」）" if r.get("conviction_cap") else ""
        print(f"\n{r['symbol']:8} {_MARK.get(r['level'], '?'):10} "
              f"{r['verdict']}（{r['n']} 条材料，实质 {r['n_sub']}"
              + (f"→ 归并为 {r.get('n_sub_events')} 个事件"
                 if r.get("n_sub_events", r["n_sub"]) != r["n_sub"] else "")
              + f"）{cap}"
              + (f"　采集失败：{r['error']}" if r["error"] else ""))
        if r.get("intake_note"):
            print(f"    {r['intake_note']}")
        for mid, tier, why, txt in r.get("items") or []:
            print(f"    [{mid}] {tier}·{why}\n        {txt[:96]}")
        # **被符号消歧丢掉的标题也要印。** 计数只能告诉你"丢了 17 条"，
        # 判断不了这一刀砍对没砍对——那必须看标题。
        # 一部的相关性闸此前是完全的盲区：丢掉的东西不会出现在任何输出里。
        cut = (r.get("intake") or {}).get("dropped_symbol_titles") or []
        if verbose and cut:
            n_cut_sym = (r.get("intake") or {}).get("dropped_symbol", 0)
            print(f"    ── 符号消歧丢掉的 {n_cut_sym} 条"
                  + (f"（下列 {len(cut)} 条为样本）" if n_cut_sym > len(cut) else "")
                  + "：")
            for t in cut:
                print(f"       ✗ {t[:92]}")
        # 其余三个丢弃原因也要能看见标题。**「标题党」那一闸是 is_noise 判的**，
        # 它有可能把一条真材料当标题党杀掉——在有样本之前，
        # 那件事在任何输出里都看不见。收了不印和没收是一回事。
        samples = (r.get("intake") or {}).get("dropped_samples") or {}
        by = (r.get("intake") or {}).get("dropped_by") or {}
        if verbose and samples:
            for reason in sorted(samples, key=lambda x: -by.get(x, 0)):
                titles = samples[reason]
                if not titles:
                    continue
                total = by.get(reason, len(titles))
                print(f"    ── {reason}丢掉的 {total} 条"
                      + (f"（下列 {len(titles)} 条为样本）"
                         if total > len(titles) else "") + "：")
                for t in titles:
                    print(f"       ✗ {t[:92]}")

    rows.sort(key=lambda r: (_ORDER.get(r["level"], 9), -r["n_sub"]))
    good = [r for r in rows if r["level"] != material_gate.INSUFFICIENT]

    print("\n" + "-" * 74)
    n_mat = sum(r["n"] for r in rows)
    n_sub = sum(r["n_sub"] for r in rows)
    n_ev = sum(r.get("n_sub_events", r["n_sub"]) for r in rows)
    print(f"材料总量：{n_mat} 条，其中实质 {n_sub} 条"
          + (f"（{n_sub / n_mat:.0%}）" if n_mat else "")
          # 转载不该把闸门顶开：档位数的是事件数，两个数都印出来。
          + (f"，归并为 {n_ev} 个不同事件（转载不重复计数）" if n_ev != n_sub else "")
          + "。")
    # 被截断掉的部分单独报。**"闸门太严"和"根本没让它看见"是两回事**，
    # 而它们在只印一个 n_sub 的报告里长得完全一样。
    n_cut = sum((r.get("intake") or {}).get("dropped", 0) for r in rows)
    n_cut_sub = sum((r.get("intake") or {}).get("dropped_substantive", 0) for r in rows)
    n_rel = sum((r.get("intake") or {}).get("relevant", 0) for r in rows)
    if n_cut:
        print(f"相关材料 {n_rel} 条，其中 {n_cut} 条因每只上限 "
              f"{unit_a.MATERIAL_CAP} 条未进闸门"
              + (f"　⚠ **其中 {n_cut_sub} 条是实质材料**" if n_cut_sub
                 else "（实质材料优先入选，被截掉的都不是实质）"))
    # 符号消歧砍掉的量单独报。**它和"今天没新闻"在输出上长得一模一样**，
    # 而且它砍的是**进闸门之前**的候选池——被砍掉的从来不会出现在任何地方。
    n_sym = sum((r.get("intake") or {}).get("dropped_symbol", 0) for r in rows)
    if n_sym:
        loud = [r["symbol"] for r in rows
                if (r.get("intake") or {}).get("dropped_symbol", 0)
                >= max(1, (r.get("intake") or {}).get("relevant", 0))]
        print(f"符号消歧另丢弃 {n_sym} 条（裸 ticker 撞上英文词，"
              f"如 ARM / ON / IT）"
              + (f"　⚠ **{'、'.join(loud)} 丢掉的比留下的还多**"
                 if loud else "")
              + ("" if verbose else "　—— 用 --verbose 看丢了哪些标题"))
    # **信息源失败必须报出来。** 一个悄悄挂掉的源，表现形式恰好就是
    # "今天这只票没有新材料"——和真的没有新闻长得一模一样。
    from cio import collect
    dead = collect.dead_feeds()
    if dead:
        print("⚠ 本次运行跳过的信息源（**它们的缺席会伪装成「今天没新闻」**）：")
        for name, (n, kind) in sorted(dead.items()):
            print(f"    {name}：连续失败 {n} 次（{kind}）")
    print()
    if not good:
        print(f"{len(rows)} 只全部 INSUFFICIENT —— **今天没有一只有增量事实。**")
        print("这不是故障：一部是 evidence-triggered 的，没有新证据就不制造新观点。")
        print("要在无新证据时强制复研，用 --force；那属于有意的人工决定，")
        print("且报告会标注它依据的是既有证据集。**强制复研仍是 INSUFFICIENT，")
        print("PC 依然不会给仓位**——闸门在链路上游，不是报告上的一句话。")
        return 1

    print("**建议跑完整一部的标的**（按实质材料条数排序）：")
    for r in good:
        print(f"  {r['symbol']:8} {r['level']:12} 实质 {r['n_sub']}/{r['n']} 条　{r['verdict']}")
    print("\n" + "　".join(f'CIO_MARKET=us python run_unit_a.py "{r["symbol"]}"' for r in good[:3]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
