#!/usr/bin/env python3
"""装没装上 —— 一条命令给出确定答案。

**为什么需要这个脚本。** 到 build64 为止，安装出过四次问题：
解压前先跑了、cp 目标写错了、两个 zip 只装上一个、路径里有空格被 xargs 拆开。
每一次的表现都一样：**程序照常跑完、报告照常生成、只是里面是旧代码。**
没有报错，所以要等到看 PDF 才发现，而那时已经浪费了一整轮 4 分钟的真机跑。

这个脚本把"我以为装上了"变成一个可以回答的问题。它只检查【本次交付新增的
可观察特征】——不是版本号（版本号可以是对的而文件是旧的），
而是真去调用那个函数、看它的行为对不对。

    python scripts/check_build.py

全绿才去跑 run_unit_a.py。有红的先看那条 MISS 后面的异常信息——\n"没装上"只是可能原因之一（还可能是探针写错、或环境变量与预期不符）。
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# ---------------------------------------------------------------- 解释器自检
# **先确认是不是用对了 Python，再谈装没装上。**
#
# 真实发生过：用系统 python（没激活 venv）跑这个脚本，第三方依赖一个都导不进来，
# 于是 60 项全红，脚本信心十足地报告"文件没有真正落到 src/cio/ 下，请重新安装"——
# **一句假话，而且指挥人去做一件完全没用的事。**
#
# 一个诊断工具给出自信的错误诊断，比它直接崩掉更糟：崩掉你会去查，
# 而一句读起来合理的错误结论会被照着执行。
def _preflight():
    """先确认解释器，再谈装没装上。"""
    import importlib.util
    missing = [m for m in ("pydantic", "yfinance", "pandas", "numpy", "httpx")
               if importlib.util.find_spec(m) is None]
    if not missing:
        return
    here = Path(__file__).resolve().parents[1]
    sub = "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    venv = here / ".venv" / sub
    print("=" * 68)
    print("解释器不对 —— 这不是「没装上」，是【用错了 Python】。")
    print("=" * 68)
    print()
    print(f"当前解释器：{sys.executable}")
    print(f"导不进来的依赖：{'、'.join(missing)}")
    print()
    print("这些依赖装在项目的虚拟环境里，不在系统 Python 里。")
    print("换成 venv 里的解释器再跑一次：")
    print()
    if venv.exists():
        print(f"    {venv} scripts/check_build.py")
    else:
        print(f"    找不到 {venv}")
        print("    先按 README 建好虚拟环境并 pip install -r requirements.txt")
    print()
    print("（本次不做任何检查。在错的解释器上跑，全部项目都会红，")
    print("  而那个结果说明不了代码到底装没装上。）")
    raise SystemExit(2)


_preflight()

ROWS: list[tuple[str, str, bool, str]] = []


def probe(build: str, name: str, fn):
    """fn() -> bool。任何异常都算失败，并把异常信息带出来——
    ImportError 本身就是最常见的"没装上"信号，不能被吞掉。"""
    try:
        ok, detail = fn(), ""
    except Exception as e:                       # noqa: BLE001
        ok, detail = False, f"{type(e).__name__}: {e}"
    ROWS.append((build, name, bool(ok), detail))


# ---------------------------------------------------------------- build61–62
def _b61_measures():
    from cio import measures
    src = inspect.getsource(measures)
    return "from .analytics" not in src and hasattr(measures, "ann_vol")


def _b61_thesis_bigram():
    from cio.thesis_store import _keywords
    feats, _ = _keywords("毛利率跌破")
    return "毛利" in feats and "跌破" in feats      # 字符二元组，不是整段短语


def _b62_panel_cite():
    from cio.unit_a import _verify_citations
    return _verify_citations("毛利率 71.07%。[面板]", 8)[1] == 0


def _b62_pdf_isomorphic():
    from cio import render
    src = inspect.getsource(render.render_unit_a_pdf)
    return "量化证据面板" in src and "一部裁定" not in src


# ---------------------------------------------------------------- build63
def _b63_material_gate():
    from cio import material_gate
    tier, _ = material_gate.classify("Is Nvidia Stock a Buy Ahead of Q2 Earnings?")
    return tier != material_gate.SUBSTANTIVE


def _b63_gate_banner():
    from cio import material_gate

    class M:
        id, text = 1, "Nvidia: The Last Hurrah (Earnings Preview)"
    g = material_gate.assess([M()])
    return g["verdict"] == "无实质材料" and bool(g["banner"]) and bool(g["constraint"])


def _b63_constraint_in_prompts():
    from cio import debate
    return all("{constraint}" in t for t in (debate._R1, debate._R2, debate._SYNTH))


def _b63_render_gate():
    from cio import render
    return all("material_banner" in inspect.getsource(f)
               for f in (render.render_unit_a_md, render.render_unit_a_pdf))


def _b63_ledger_columns():
    from cio import thesis_store
    return "material_verdict" in inspect.signature(thesis_store.record).parameters


def _b63_quote_check():
    from cio.unit_a import _verify_citations
    bad = _verify_citations('- **"对方从没说过这句话，是编的。"**', 8, "对方真正说过的是别的内容")[0]
    return "⚠引述失实" in bad


# ---------------------------------------------------------------- build64
def _b64_markdown_headers():
    from cio import debate
    md = "**失效条件**（若发生即视为论点失效）\n- 毛利率跌破60%\n- 营收同比转负\n结论=看多|中"
    got = debate.parse_invalidations(md)
    return len(got) == 2 and not any("结论=" in g for g in got)


def _b64_legacy_headers():
    from cio import debate
    old = "【失效条件】\n- 毛利率跌破 40%\n结论=看多|中"
    got = debate.parse_invalidations(old)
    return got == ["毛利率跌破 40%"]                 # 老版式不能被新版式挤掉


def _b64_parse_warning():
    from cio import debate
    return len(debate.parse_section_warnings("我认为失效条件是毛利率下滑，但不按格式写\n结论=看多|中")) == 1


def _b64_table_head():
    from cio.unit_a import _is_table_head
    return _is_table_head("| 对方论点 | 我方反驳（仅引用面板数据） |") and \
        not _is_table_head("| 毛利率 71.07% | 估值偏高 |")


def _b64_market_only():
    from cio import debate
    return debate.market_only_invalidations(["Beta 超过 2.5。", "营业收入同比转负。"]) == ["Beta 超过 2.5。"]


def _b65_year_check():
    from cio import debate
    c = "面板 as_of 2026-05-20，E/P 2.38% [截至 2026-01-25]"
    return "⚠年份存疑" in debate._mark_years(["2024年第一季度财报公布"], c)[0] and \
        "⚠" not in debate._mark_years(["2026年第二季度营收超预期"], c)[0]


def _b65_verdict_line():
    from cio.unit_a import _verify_citations
    return _verify_citations("结论=看多|中", 8)[1] == 0


# ---------------------------------------------------------------- build66
def _b66_gate_tiers():
    from cio import material_gate as MG

    class M:
        def __init__(s, i, t):
            s.id, s.text = i, t
    prev = "Is Nvidia Stock a Buy Ahead of Q2 Earnings?"
    real = "英伟达宣布以 20 亿美元收购 Run:ai，交易已完成交割"
    # **三条必须是三件不同的事。** 这里原来是同一句话复制三份 ——
    # build100 之后同一事件不重复计数，那样只算 1 件，判 THIN 而不是
    # SUFFICIENT。旧写法把「转载能顶开闸门」这个缺陷写进了探针本身。
    real2 = "商务部宣布对英伟达 H20 实施出口管制"
    real3 = "英伟达宣布回购 500 亿美元股份，董事会已批准"
    g0 = MG.assess([M(i, prev) for i in range(3)])
    g1 = MG.assess([M(1, real), M(2, prev)])
    g3 = MG.assess([M(1, real), M(2, real2), M(3, real3)])
    # 同一件事的三份转载只算一件 → THIN，不是 SUFFICIENT
    gdup = MG.assess([M(i, real) for i in range(3)])
    if not (gdup["level"] == MG.THIN and gdup["n_sub"] == 3
            and gdup["n_sub_events"] == 1):
        return False
    return (g0["level"] == MG.INSUFFICIENT and not g0["activate"]
            and g1["level"] == MG.THIN and g1["conviction_cap"] == "弱"
            and g3["level"] == MG.SUFFICIENT and g3["conviction_cap"] == "")


def _b66_not_activated_path():
    from cio import unit_a
    src = inspect.getsource(unit_a._not_activated)
    return ("get_ollama" not in src and "thesis_store.record" not in src
            and "thesis_store.check" in src and 'formal_vote="ABSTAIN"' in src)


def _b66_render_split():
    from cio import render
    return ("not r.activated" in inspect.getsource(render.render_unit_a_md)
            and "not r.activated" in inspect.getsource(render.render_unit_a_pdf)
            and hasattr(render, "_pdf_not_activated"))


def _b66_force():
    from cio import unit_a
    return ("UNIT_A_FORCE_RESEARCH" in inspect.getsource(unit_a._forced)
            and "force" in inspect.signature(unit_a.build_unit_a).parameters)


def _b66_no_conditions_status():
    from cio import thesis_store
    return "NO_CONDITIONS" in inspect.getsource(thesis_store.record)


def _b67_supersede():
    from cio import thesis_store
    src = inspect.getsource(thesis_store.record)
    return "SUPERSEDED" in src and "if symbol else (subject" in src


def _b67_backfill():
    from cio import thesis_store
    return "NO_CONDITIONS" in inspect.getsource(thesis_store.init)


def _b68_bold_wrapped_header():
    from cio import debate
    body = "\n- 营业收入同比转负\n结论=看多|中"
    return all(debate.parse_invalidations(h + body) == ["营业收入同比转负"]
               for h in ("【失效条件】", "**失效条件**", "**【失效条件】**",
                         "### 失效条件", "失效条件：", "5. 失效条件"))


def _b68_double_bullet():
    from cio import debate
    return debate._strip_bullet("- - **毛利率跌破50%**") == "毛利率跌破50%"


def _b69_restate():
    from cio.unit_a import _verify_citations
    bear = "- 市盈率约42倍（E/P = 2.38%）显示估值偏高，存在回调空间。[面板]"
    ok = _verify_citations("1. **市盈率约42倍（E/P = 2.38%）显示估值偏高，存在回调空间。**",
                           6, bear)[1] == 0
    fake = "⚠引述失实" in _verify_citations("2. **对方没说过这句，是编的转述内容。**", 6, bear)[0]
    # 有出处的加粗断言不能被当成伪造引述（惩罚合规行为会让信号作废）
    cited = "⚠" not in _verify_citations("- **近一年最大回撤 -20.21%【面板】**", 6, "无关文本")[0]
    return ok and fake and cited


def _b69_weak_cite():
    from cio.unit_a import _verify_citations
    t, b = _verify_citations("市场预期将超预期。[2]", 6, "", "", {2})
    return "据无实质材料" in t and b == 0


def _b71_table_head_len():
    from cio.unit_a import _is_table_head
    return (_is_table_head("| 对方论点 | 我的回应（为何不成立或影响被高估） |")
            and not _is_table_head("| 毛利率 71.07% | 估值偏高 |"))


def _b70_bold_label():
    from cio.unit_a import _verify_citations
    return (_verify_citations("**反驳**", 6)[1] == 0
            and _verify_citations("**直面不利证据**", 6)[1] == 0
            and _verify_citations("**毛利率跌破60%**", 6)[1] == 1)


def _b72_drift():
    from cio import thesis_store, unit_a, render
    src = inspect.getsource(unit_a.build_unit_a)
    return (hasattr(thesis_store, "drift_check")
            and src.index("drift_check") < src.index("thesis_store.record")
            and "direction_drift" in inspect.getsource(render.render_unit_a_md)
            and "direction_drift" in inspect.getsource(render.render_unit_a_pdf))


def _b72_drift_grading():
    from cio import thesis_store as TS
    import inspect as _i
    s = _i.getsource(TS.drift_check)
    return "no_evidence" in s and "supported" in s and "thin" in s


def _b73_peer_claim():
    from cio.unit_a import _verify_citations, has_peer_stats
    t, b = _verify_citations("毛利率 71.07%，远高于行业平均。[面板]", 7, "", "", None,
                             peer_stats=False)
    clean = "⚠" not in _verify_citations("毛利率 71.07%，远高于行业平均。[面板]", 7, "", "",
                                         None, peer_stats=True)[0]
    return "⚠无同业基准" in t and b == 1 and clean and not has_peer_stats("毛利率: 71.07%")


def _b73_conviction_cap():
    from cio import material_gate as MG

    class M:
        def __init__(s, i, t):
            s.id, s.text = i, t
    prev = "Is Nvidia Stock a Buy Ahead of Q2 Earnings?"
    return MG.assess([M(i, prev) for i in range(3)])["conviction_cap"] == "中"


def _b74_lint_items():
    from cio import debate
    c = "毛利率: 71.07%"
    out = debate.lint_items(["净资产收益率下降到行业平均以下（<20%）。",
                             "导致市盈率（E/P）下降。", "毛利率跌破 60%。"], c, peer_stats=False)
    return ("⚠无同业基准" in out[0] and "⚠口径错标" in out[1]
            and out[2] == "毛利率跌破 60%。")


def _b75_sign_error():
    from cio import debate
    from cio.unit_a import _verify_citations
    caught = bool(debate._sign_error("E/P 仅为 2.33%，表明股票被低估。"))
    clean = not debate._sign_error("E/P仅为2.33%，显示估值偏高。")
    body = "⚠方向错误" in _verify_citations("E/P 仅为 2.33%，表明股票被低估。[面板]", 7)[0]
    return caught and clean and body


def _b76_no_false_positive():
    from cio import debate
    # 「高估值」= 高 + 估值，不是 高估 + 值。build75 在这句上误报过。
    return (not debate._sign_error("E/P为2.33%（即P/E约43），表明市价已处于较高估值区间；"
                                   "自由现金流优势不能抵消高估值带来的下行风险。")
            and bool(debate._sign_error("E/P 仅为 2.33%，表明股票被低估。")))


def _b77_unit_gate():
    from cio import risk_officer
    ok = risk_officer.assess_one(                    # 40.74% 写成小数，不该被否决
        ticker="X", direction="看多", conviction="中", evidence_gate="SUFFICIENT",
        invalidation_conditions=["x"], measures={"sigma_60": 0.4074})
    if ok["veto"]:
        return False
    try:                                             # 40.74 写成百分数，必须抛错
        risk_officer.assess_one(
            ticker="X", direction="看多", conviction="中", evidence_gate="SUFFICIENT",
            invalidation_conditions=["x"], measures={"sigma_60": 40.74})
        return False
    except ValueError as e:
        return "单位不符" in str(e)


def _b77_as_ratio():
    from cio import measures
    return (abs(measures.as_ratio(40.74) - 0.4074) < 1e-12
            and measures.as_ratio(None) is None
            and len(measures.beta_corr(None, None, 250, 60)) == 3)


def _b77_veto_recorded():
    import ast
    import inspect as _i
    import importlib.util
    import textwrap
    spec = importlib.util.spec_from_file_location(
        "_rp_probe", Path(__file__).resolve().parents[1] / "run_pc.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    tree = ast.parse(textwrap.dedent(_i.getsource(mod.main)))
    loops = [n for n in ast.walk(tree) if isinstance(n, ast.For)
             and isinstance(n.iter, ast.Name) and n.iter.id == "rows"]
    return bool(loops) and not [c for c in ast.walk(loops[0])
                                if isinstance(c, ast.Continue)]


def _b77_ledger_dupes():
    from cio import portfolio
    return hasattr(portfolio, "duplicates") and "accounts" in inspect.getsource(
        portfolio.summary)


def _b78_unrecorded_gate():
    from cio import material_gate, sizing
    if material_gate.level_from_verdict("") != material_gate.UNRECORDED:
        return False
    if material_gate.level_from_verdict("材料偏薄") != material_gate.THIN:
        return False
    a = sizing.size_one(ticker="X", conviction="中", evidence_gate="UNRECORDED",
                        sigma_60=0.4, sigma_252=0.3, caps={"single_name": 0.05},
                        base_rb=0.015)
    b = sizing.size_one(ticker="X", conviction="中", evidence_gate="INSUFFICIENT",
                        sigma_60=0.4, sigma_252=0.3, caps={"single_name": 0.05},
                        base_rb=0.015)
    return a["w_final"] is None and b["w_final"] is None and a["reason"] != b["reason"]


def _b78_shadow_excluded():
    from cio import portfolio
    import inspect as _i
    p = _i.signature(portfolio.open_positions).parameters
    return (portfolio.is_shadow("二部_shadow") and not portfolio.is_shadow("二部")
            and "include_shadow" in p and p["include_shadow"].default is False)


def _b79_legacy_guard():
    import os
    from cio import legacy_guard
    had = os.environ.pop(legacy_guard.ENV, None)
    try:
        off = legacy_guard.legacy_push_allowed("安装自检")
        os.environ[legacy_guard.ENV] = "1"
        on = legacy_guard.legacy_push_allowed("安装自检")
    finally:
        os.environ.pop(legacy_guard.ENV, None)
        if had is not None:
            os.environ[legacy_guard.ENV] = had
    return off is False and on is True


def _b79_unit_a_no_position():
    import ast
    src = (Path(__file__).resolve().parents[1] / "run_unit_a.py").read_text()
    return not [n for n in ast.walk(ast.parse(src))
                if isinstance(n, ast.Attribute) and n.attr == "target_position"]


def _b79_pc_telegram():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_rp_tg", Path(__file__).resolve().parents[1] / "run_pc.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    txt = mod._tg_summary("2026-01-01", "US_PAPER", {"regime": "neutral", "note": ""},
                          [({"ticker": "A", "direction": "看多", "conviction": "中",
                             "evidence_gate": "SUFFICIENT", "veto": False},
                            {"w_final": 0.03, "sigma_effective": 0.3,
                             "binding_position_constraint": ["risk_budget"]})],
                          {"scale_factor": 1.0, "weights": {"A": 0.03}})
    return "**" not in txt and "绑定 risk_budget" in txt


def _b80_collect_extracted():
    from cio import unit_a
    return (list(inspect.signature(unit_a.collect_materials).parameters) == ["text"]
            and "collect_materials" in inspect.getsource(unit_a.build_unit_a))


def _b80_scan():
    import importlib.util
    from cio import material_gate, unit_a
    from cio.models import MaterialItem
    spec = importlib.util.spec_from_file_location(
        "_rs", Path(__file__).resolve().parents[1] / "run_scan.py")
    rs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rs)
    orig = unit_a.collect_materials
    try:
        unit_a.collect_materials = lambda t: {
            "info": {}, "subj": t, "news": [], "raws": [], "status": {},
            "materials": [MaterialItem(id=1, source_name="s", source_url="",
                                       text=("Broadcom announced a $10 billion buyback"
                                             if t == "A" else
                                             "Nvidia Q3 Earnings Preview: What To Expect"))]}
        a, b = rs.scan_one("A"), rs.scan_one("B")
    finally:
        unit_a.collect_materials = orig
    return (a["level"] in (material_gate.SUFFICIENT, material_gate.THIN)
            and b["level"] == material_gate.INSUFFICIENT)


def _b81_dead_feed():
    from cio import collect
    collect.reset_feed_health()
    st, bad = {}, {"name": "_PROBE_DEAD", "url": "http://127.0.0.1:9/none.xml"}
    for _ in range(3):
        collect.fetch_rss(bad, st)
    ok = ("跳过" in st["_PROBE_DEAD"] and "_PROBE_DEAD" in collect.dead_feeds())
    collect.reset_feed_health()
    return ok


def _b82_advice_json():
    import json
    import tempfile
    from pathlib import Path as P
    from cio import config, render, unit_a
    from cio.models import UnitAAdvice
    tmp = P(tempfile.mkdtemp())
    old = (config.TOPIC_DIR, render.render_unit_a_pdf, unit_a.db.init_db,
           unit_a.db.insert_brief)
    config.TOPIC_DIR = tmp
    render.render_unit_a_pdf = lambda r, p: P(p).write_text("x")
    unit_a.db.init_db = lambda: None
    unit_a.db.insert_brief = lambda *a, **k: None
    try:
        md, _pdf = unit_a.archive_and_render(
            UnitAAdvice(subject="PROBE", resolved="PROBE", direction="看多",
                        conviction="弱", gate_level="THIN", thesis_id=7))
        d = json.loads(P(unit_a.advice_json_path(md)).read_text(encoding="utf-8"))
    finally:
        (config.TOPIC_DIR, render.render_unit_a_pdf, unit_a.db.init_db,
         unit_a.db.insert_brief) = old
    from cio import runid
    return (d.get("schema_version") == runid.SCHEMA_VERSION
            and d.get("kind") == "unit_a" and d.get("thesis_id") == 7)


def _b82_stage_events():
    import logging
    from cio import debate
    from cio.utils import stage
    got = []

    class C(logging.Handler):
        def emit(self, rec):
            got.append(rec.getMessage())

    h = C()
    lg = logging.getLogger("cio.stage")
    lg.addHandler(h)
    try:
        stage("collect", "9 条材料")
    finally:
        lg.removeHandler(h)
    six = sum(1 for n in ("debate_bull_r1", "debate_bear_r1", "debate_bull_r2",
                          "debate_bear_r2", "judge", "synthesis")
              if f'"{n}"' in inspect.getsource(debate.run_debate))
    return got == ["[STAGE] collect | 9 条材料"] and six == 6


def _b82_json_flags():
    import ast
    root = Path(__file__).resolve().parents[1]
    scan = (root / "run_scan.py").read_text()
    pc = (root / "run_pc.py").read_text()
    # run_pc 的 --json 绝不能提前 return 掉 pc_ledger.record
    tree = ast.parse(pc)
    fn = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "main"][0]
    src = ast.get_source_segment(pc, fn) or ""
    rec_at = src.index("pc_ledger.record")
    json_at = src.index("if as_json:")
    return ("--json" in scan and "dead_feeds" in scan
            and "--json" in pc and json_at > rec_at)


def _stdout_of(mod_path: str, argv: list, patch) -> str:
    """在进程内跑一个入口的 main()，把 stdout **整段**抓下来。

    不是"从输出里找 JSON"——**契约是整个 stdout 一次 json.loads 成功**。
    一个偷偷混进 stdout 的 debug print 就能打坏接口，而它不会报错：
    界面那边只会看到 json.loads 抛异常，然后显示"任务失败"。
    """
    import contextlib
    import importlib.util
    import io
    import sys as _s
    spec = importlib.util.spec_from_file_location(
        "_probe_" + Path(mod_path).stem, Path(__file__).resolve().parents[1] / mod_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    undo = patch(mod)
    buf = io.StringIO()
    old_argv = _s.argv
    _s.argv = [mod_path] + argv
    try:
        with contextlib.redirect_stdout(buf):
            mod.main()
    finally:
        _s.argv = old_argv
        if callable(undo):
            undo()
    return buf.getvalue()


def _b83_scan_json_contract():
    import json
    from cio import material_gate, runid, unit_a
    from cio.models import MaterialItem
    orig = unit_a.collect_materials

    def patch(_mod):
        unit_a.collect_materials = lambda t: {
            "info": {}, "subj": t, "news": [], "raws": [], "status": {},
            "materials": [MaterialItem(
                id=1, source_name="s", source_url="",
                text=("Broadcom announced a $10 billion buyback" if t == "A"
                      else "Nvidia Q3 Earnings Preview"))]}
        return lambda: setattr(unit_a, "collect_materials", orig)

    out = _stdout_of("run_scan.py", ["A", "B", "--json"], patch)
    d = json.loads(out)                      # 整段一次解析，不做任何清洗
    r = {x["symbol"]: x for x in d["rows"]}
    return (d["schema_version"] == runid.SCHEMA_VERSION
            and d["run_id"].startswith("sc-") and d["kind"] == "scan"
            and r["A"]["conviction_cap"] and r["A"]["activate"] is True
            and r["B"]["level"] == material_gate.INSUFFICIENT
            and "dead_feeds" in d)


def _b83_pc_json_contract():
    import json
    from cio import db, pc_ledger, portfolio, regime, runid, thesis_store
    import tempfile
    from pathlib import Path as P
    saved = {}

    def patch(mod):
        saved["db"] = db.DB_PATH
        db.DB_PATH = P(tempfile.mkdtemp()) / "t.db"
        saved.update(op=portfolio.open_positions, sm=portfolio.summary,
                     du=portfolio.duplicates, ra=regime.assess,
                     ob=thesis_store.open_brief, mf=mod._measures_for)
        portfolio.open_positions = lambda pid, include_shadow=False: []
        portfolio.summary = lambda: []
        portfolio.duplicates = lambda pid="": []
        regime.assess = lambda fetch=None: {"regime": "neutral", "score": 0,
                                            "signals": [], "note": "probe"}
        thesis_store.open_brief = lambda s, limit=50: [
            {"id": 1, "subject": "PRB", "direction": "看多", "conviction": "中",
             "material_verdict": "材料充分", "invalidations": ["x"]}]
        mod._measures_for = lambda sym: {"sigma_60": 0.40, "sigma_252": 0.35,
                                         "beta": 1.5, "maxdd": -0.2,
                                         "corr_bench": 0.5, "liquidity_cap": None}

        def undo():
            db.DB_PATH = saved["db"]
            portfolio.open_positions, portfolio.summary = saved["op"], saved["sm"]
            portfolio.duplicates, regime.assess = saved["du"], saved["ra"]
            thesis_store.open_brief, mod._measures_for = saved["ob"], saved["mf"]
        return undo

    out = _stdout_of("run_pc.py", ["--json"], patch)
    d = json.loads(out)
    p0 = d["positions"][0]
    return (d["schema_version"] == runid.SCHEMA_VERSION
            and d["run_id"].startswith("pc-") and d["status"] == "completed"
            and p0["ticker"] == "PRB" and p0["w_final"] is not None
            and p0["binding_position_constraint"])


def _b83_ledger_idempotent():
    import tempfile
    from pathlib import Path as P
    from cio import db, pc_ledger, risk_officer, sizing
    old = db.DB_PATH
    db.DB_PATH = P(tempfile.mkdtemp()) / "t.db"
    try:
        cro = risk_officer.assess_one(
            ticker="IDEM", direction="看多", conviction="中",
            evidence_gate="SUFFICIENT", invalidation_conditions=["x"],
            measures={"sigma_60": 0.4, "sigma_252": 0.3}, regime="neutral")
        sz = sizing.size_one(ticker="IDEM", conviction="中", evidence_gate="SUFFICIENT",
                             sigma_60=0.4, sigma_252=0.3, caps=cro["caps"],
                             base_rb=cro["base_risk_budget"], regime="neutral")
        a = pc_ledger.record(as_of_date="2026-08-26", portfolio_id="US_PAPER",
                             cro=cro, size=sz, run_id="pc-fixed-1")
        b = pc_ledger.record(as_of_date="2026-08-26", portfolio_id="US_PAPER",
                             cro=cro, size=sz, run_id="pc-fixed-1")   # 重试
        c = pc_ledger.record(as_of_date="2026-08-26", portfolio_id="US_PAPER",
                             cro=cro, size=sz, run_id="pc-fixed-2")   # 新的一次运行
        n = pc_ledger.binding_stats()["n"]
    finally:
        db.DB_PATH = old
    return a == b and c != a and n == 2


def _b83_pc_serialize_before_record():
    import ast
    src = (Path(__file__).resolve().parents[1] / "run_pc.py").read_text()
    fn = [n for n in ast.walk(ast.parse(src))
          if isinstance(n, ast.FunctionDef) and n.name == "main"][0]
    rec = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call)
           and getattr(n.func, "attr", "") == "record"]
    pay = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Assign)
           and any(getattr(t, "id", "") == "payload" for t in n.targets)
           and isinstance(n.value, ast.Call)]
    prn = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call)
           and getattr(n.func, "id", "") == "print"
           and any(getattr(a, "id", "") == "payload" for a in n.args)]
    return (bool(pay and rec and prn)
            and max(pay) < min(rec) and max(rec) < min(prn))


def _b83_ui_owns_no_rules():
    """界面层不得重新解释闸门。

    断言【结构】不断言注释文本——解释这条改动的注释里必然写着"封顶为弱"，
    文本匹配会永远失败（这已经是第五次栽在同一个坑上）。
    真正要断言的是：状态→显示 的映射表里，不出现任何信心档位词；
    档位一律从 assess() 的返回值取。
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_rs_rules", Path(__file__).resolve().parents[1] / "run_scan.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    no_rule_in_display = not any(w in v for v in mod._MARK.values()
                                 for w in ("强", "中", "弱"))
    src = inspect.getsource(mod.scan_one)
    return (no_rule_in_display and "material_gate.assess" in src
            and 'g["conviction_cap"]' in src)


def _b85_freshness_measured():
    """新鲜度必须是【测】出来的，不是按品种假设的。"""
    import datetime as dt
    from zoneinfo import ZoneInfo
    from cio import market_now as mn
    et = ZoneInfo("America/New_York")
    now = dt.datetime(2026, 8, 27, 7, 0, tzinfo=et)
    live, _m, st1 = mn.classify_age(now - dt.timedelta(minutes=3), now)
    old, _m2, st2 = mn.classify_age(now - dt.timedelta(days=3), now)
    fut, _m3, st3 = mn.classify_age(now + dt.timedelta(hours=2), now)
    unk, _m4, st4 = mn.classify_age(None, now)
    return (live == mn.FRESH_LIVE and not st1
            and "3 天前" in old and st2          # 陈数据自己会变标签
            and st3 and st4)                     # 未来时间戳与缺失都要标出来


def _b85_snapshot_keeps_failures():
    """取不到的符号也要保留一行——静默省略会被读成"今天没异动"。"""
    from cio import market_now as mn
    rows = [("g", "好", "OK"), ("g", "空", "NONE"), ("g", "炸", "BOOM")]

    def fake(sym):
        if sym == "BOOM":
            raise RuntimeError("x")
        return None if sym == "NONE" else {"last": 1.0, "change_pct": 0.1, "as_of": None}
    out = mn.snapshot(fetch=fake, symbols=rows)
    return len(out) == 3 and sum(1 for t in out if t.stale) == 3


def _b85_three_renderers_agree():
    """md / reportlab / HTML 三个渲染器必须都有快照与图例。

    **这是 build62 栽过的坑**：MD 改了、PDF 渲染器没跟上，
    同一天两份报告内容不同，而且都不报错。
    """
    import datetime as dt
    from zoneinfo import ZoneInfo
    from cio import market_now as mn, render, render_html
    from cio.models import Brief, CollectionStatus, Event
    et = ZoneInfo("America/New_York")
    now = dt.datetime(2026, 8, 27, 7, 0, tzinfo=et)
    ticks = mn.snapshot(
        fetch=lambda s: {"last": 100.0, "change_pct": 0.5,
                         "as_of": now - dt.timedelta(minutes=4)}, now=now)
    b = Brief(market_snapshot=ticks, market_note=mn.render_note(ticks, now),
              status=CollectionStatus(),
              watchlist_events=[Event(headline="X", confidence=5, materiality=4,
                                      relevance="Direct", immediacy="Today", event_id="E1")])
    md = render.render_brief_md(b)
    html = render_html.render_brief_html(b)
    src_pdf = inspect.getsource(render.render_brief_pdf)
    # **断言语言无关的东西。** 报告标题会随 CIO_MARKET 在中英文之间切换
    # （us 模式下是 "Pre-market Snapshot"），写死中文标题的探针在 us 机器上
    # 会红——而它红的原因和"代码装没装上"毫无关系，却会让人去重装一遍。
    #
    # 所以改断言两样跨语言不变的：
    #   · 快照里的品种名（来自 market_now.SYMBOLS，两种语言下都是中文常量）
    #   · 四分卡的 C/M 记号（中英文图例里都有）
    tick_name = ticks[0].name if ticks else "标普500期货"
    return (tick_name in md and tick_name in html
            and "C=" in md                     # md 图例：C=来源可信度 / C=source confidence
            and "C1–5" in html                 # html 图例：C1–5 …（中英文都有）
            and "market_snapshot" in src_pdf)


# ---------------------------------------------------------------- build87
# Build 1：PC 目标 → CEO 授权 → （Build 2 执行）→ 账本。
# **需要写库的探针一律指到临时库**，不碰真账 —— 自检不该在待批清单里
# 留下几条假提案，那些提案将来会被当成真实待办。
from contextlib import contextmanager as _ctx        # noqa: E402


@_ctx
def _tmpdb():
    """把 cio.db 临时指到一次性文件，**退出时一定还原**。

    不还原的话，后面追加的任何探针都会安静地跑在临时库上——
    它们仍然全绿，但检查的已经不是真环境了。
    """
    import tempfile
    from pathlib import Path as _P
    from cio import db as _db
    old = _db.DB_PATH
    _db.DB_PATH = _P(tempfile.mkdtemp(prefix="cio-probe-")) / "probe.db"
    try:
        yield _db.DB_PATH
    finally:
        _db.DB_PATH = old


def _b87_no_target_is_not_zero():
    """**本 build 的核心契约**：w_final 为空且未否决 = 无目标，不是目标 0。

    折成 0 的后果是一只只是今天没有新材料的持仓被安静地全部卖出——
    指令格式完全正确，账本欣然接受。
    """
    from cio import rebalance as rb
    no = rb.target_from_decision({"ticker": "AMAT", "w_final": None, "veto": False})
    veto = rb.target_from_decision({"ticker": "NVDA", "w_final": None, "veto": True})
    tgt = rb.target_from_decision({"ticker": "AVGO", "w_final": 0.02, "veto": False})
    return (no["basis"] == rb.NO_TARGET and no["target_weight"] is None
            and veto["basis"] == rb.EXIT_DECIDED and veto["target_weight"] == 0.0
            and tgt["basis"] == rb.TARGET)


def _b87_held_not_evaluated_holds():
    """持有 + 本轮未复审 → 维持不动，且带上"多少天没看过"。"""
    from cio import rebalance as rb
    p = rb.plan(nav=100000.0, cash=100000.0,
                holdings={"NVDA": {"shares": 10, "last_evaluated_on": "2026-08-20"}},
                decisions=[], prices={"NVDA": 180.0}, decision_date="2026-08-31")
    r = p["rows"][0]
    return (r["action"] == rb.HOLD_NOT_EVALUATED and r["delta_shares"] == 0
            and r["days_since_evaluated"] == 11)


def _b87_unpriced_is_not_liquidation():
    """取不到价 → 不可计算，**不是**清仓。"""
    from cio import rebalance as rb
    p = rb.plan(nav=100000.0, cash=100000.0,
                holdings={"NVDA": {"shares": 10, "last_evaluated_on": "2026-08-30"}},
                decisions=[{"ticker": "NVDA", "w_final": 0.02, "veto": False}],
                prices={}, decision_date="2026-08-31")
    r = p["rows"][0]
    return (r["action"] == rb.NOT_PRICED and r["delta_shares"] == 0
            and r["target_shares"] is None)


def _b87_band_and_exit_exception():
    """小额不交易；但目标 0 的清仓不受门槛约束。"""
    from cio import rebalance as rb
    small = rb.plan(nav=100000.0, cash=100000.0,
                    holdings={"X": {"shares": 9, "last_evaluated_on": "2026-08-30"}},
                    decisions=[{"ticker": "X", "w_final": 0.018, "veto": False}],
                    prices={"X": 180.0}, decision_date="2026-08-31")["rows"][0]
    exit_row = rb.plan(nav=100000.0, cash=100000.0,
                       holdings={"X": {"shares": 1, "last_evaluated_on": "2026-08-30"}},
                       decisions=[{"ticker": "X", "w_final": 0.0, "veto": False}],
                       prices={"X": 180.0}, decision_date="2026-08-31")["rows"][0]
    return (small["action"] == rb.BELOW_BAND and small["delta_shares"] == 0
            and exit_row["action"] == rb.EXIT and exit_row["delta_shares"] == -1)


def _b87_floor_eps_no_lost_share():
    """100000×1.8%÷180 的真值是 10 股。

    二进制里它是 9.999999999999999，直接取整就少买一股——
    **每笔都少一点、永远朝同一个方向**，表现为一段解释不了的持续跑输。
    """
    from cio import rebalance as rb
    return (rb.target_shares(100000, 0.018, 180.0) == 10
            and rb.target_shares(100000, 0.018, 190.0) == 9)


def _b87_compliance_never_pass_when_unknown():
    """六项里四项未评估 → PARTIAL。**PASS 会被读成"风控查过了"。**"""
    from cio import compliance as cp
    from cio import rebalance as rb
    p = rb.plan(nav=100000.0, cash=100000.0, holdings={},
                decisions=[{"ticker": "AVGO", "w_final": 0.02, "veto": False}],
                prices={"AVGO": 300.0}, decision_date="2026-08-31")
    res = cp.check_proforma(nav=100000.0, cash_available=100000.0,
                            cash_required=p["summary"]["cash_required"],
                            rows=p["rows"])
    ids = {c[0] for c in cp.CHECKS}
    return (res["status"] == cp.PARTIAL and res["n_not_evaluated"] == 4
            and len(ids) == 6 and "sector_cap" in ids)


def _b87_execution_basis_and_expiry():
    """成交价基准写死 T+1_OPEN；批准有有效期。"""
    from cio import rebalance as rb
    return (rb.EXECUTION_PRICE_BASIS == "T+1_OPEN"
            and rb.expires_on("2026-08-31") == "2026-09-04"
            and rb.MAX_SESSION_GAP_DAYS == 4)


def _b87_state_machine_rejects_illegal():
    """被否的提案不能被执行 —— 非法跃迁必须抛，不能静默忽略。"""
    with _tmpdb():
        from cio import proposal_store as ps
        from cio import rebalance as rb
        p = rb.plan(nav=100000.0, cash=100000.0, holdings={},
                    decisions=[{"ticker": "QCOM", "w_final": 0.02, "veto": False}],
                    prices={"QCOM": 150.0}, decision_date="2026-08-31")
        row = ps.record(run_id="probe", portfolio_id="P", row=p["rows"][0],
                        nav=100000.0, decision_date="2026-08-31",
                        expires="2026-09-04", compliance={"status": "PARTIAL"})
        same = ps.record(run_id="probe", portfolio_id="P", row=p["rows"][0],
                         nav=100000.0, decision_date="2026-08-31",
                         expires="2026-09-04", compliance={"status": "PARTIAL"})
        if row["id"] != same["id"] or row["state"] != ps.PENDING_APPROVAL:
            return False
        ps.transition(row["id"], ps.REJECTED, actor="probe")
        try:
            ps.transition(row["id"], ps.EXECUTED, actor="probe")
        except ValueError:
            return ps.TRANSITIONS[ps.NO_TRADE] == frozenset()
        return False

def _b87_us_book_conventions():
    """美股账本口径：1 股、USD、含息总回报基准、拒收 6 位 A 股代码。"""
    with _tmpdb():
        from cio import book
        if not (book.LOT_SIZE == 1 and book.CURRENCY == "USD"
                and book.BENCHMARK_BASIS == "TOTAL_RETURN"):
            return False
        try:
            book.assert_us_ticker("002371")
            return False
        except ValueError:
            pass
        book.open_book("P", capital=100000.0, opened_on="2026-08-31")
        keep = book.open_book("P", capital=1.0)          # 重开账不得覆盖
        return abs(keep["initial_capital"] - 100000.0) < 1e-9

def _b87_nav_unknown_when_unpriced():
    """有持仓取不到价 → NAV 返回 None，**不按剩下的算**。"""
    with _tmpdb():
        from cio import book
        from cio.db import connect
        book.open_book("P", capital=100000.0, opened_on="2026-08-31")
        with connect() as con:
            con.execute("INSERT INTO book_position(portfolio_id,ticker,shares,avg_cost,"
                        "opened_on,opened_run_id,last_evaluated_on,open) "
                        "VALUES('P','NVDA',10,170.0,'2026-08-20','r','2026-08-20',1)")
        bad = book.nav("P", {})
        good = book.nav("P", {"NVDA": 180.0})
        return (bad["nav"] is None and bad["unpriced"] == ["NVDA"]
                and abs(good["nav"] - 101800.0) < 1e-6)

def _b87_book_price_basis_differs_from_measurement():
    """账本用未复权价，测量用复权序列 —— 两个口径**刻意不同**。

    复权价会回溯改变（下月一次分红会下调今天的复权价），
    拿它当成本价，账本迟早和数据源对不上，而两边都不报错。
    """
    from cio import marks
    return marks.PRICE_BASIS == "RAW_UNADJUSTED" and hasattr(marks, "next_session_after")


def _b87_proposal_derives_from_recorded_run():
    """提案从**已落库的那一次决策**派生，不重跑 PC。"""
    import inspect as _i
    from cio import pc_ledger
    src = _i.getsource(_i.getmodule(pc_ledger))
    has = hasattr(pc_ledger, "decisions_for_run") and hasattr(pc_ledger, "latest_run_id")
    sig = _i.signature(pc_ledger.decisions_for_run)
    return has and "run_id" in sig.parameters and "portfolio_id" in sig.parameters \
        and "pc_lineage" in src


# ---------------------------------------------------------------- build88
# Build 2：CEO 批准 → T+1 开盘成交 → 入账；外加 Telegram 控制台。
def _b88_old_schema_migrates():
    """**建表 → 补列 → 建索引**，顺序不能换。

    真实事故：pc_lineage 的 run_id 迁移写在 executescript 之后，而
    executescript 里的唯一索引正好用到 run_id → 脚本先抛
    `no such column: run_id` → **那段专门补 run_id 的迁移永远跑不到**。
    """
    import sqlite3
    import tempfile
    from pathlib import Path as _P
    from cio import db as _db
    tmp = _P(tempfile.mkdtemp(prefix="cio-mig-")) / "old.db"
    con = sqlite3.connect(tmp)
    con.executescript("CREATE TABLE pc_lineage (id INTEGER PRIMARY KEY, "
                      "as_of_date TEXT, portfolio_id TEXT, ticker TEXT);")
    con.commit()
    con.close()
    old = _db.DB_PATH
    _db.DB_PATH = tmp
    try:
        from cio import pc_ledger
        pc_ledger.init()
        with _db.connect() as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(pc_lineage)")}
            idx = {r[1] for r in c.execute("PRAGMA index_list(pc_lineage)")}
        return "run_id" in cols and "ux_pc_lineage_run" in idx
    finally:
        _db.DB_PATH = old


def _b88_ensure_columns_exists():
    from cio import db as _db
    import inspect as _i
    return (hasattr(_db, "ensure_columns")
            and set(_i.signature(_db.ensure_columns).parameters) >=
            {"con", "table", "cols"})


def _b88_only_approved_executes():
    with _tmpdb():
        from cio import book, execution, proposal_store, rebalance
        book.open_book("P", capital=100000.0, opened_on="2026-08-31")
        row = {"ticker": "AAA", "basis": rebalance.TARGET, "reason": "",
               "target_weight": 0.02, "target_shares": 10, "current_shares": 0,
               "delta_shares": 10, "decision_price": 180.0, "est_value": 1800.0,
               "action": rebalance.BUY, "days_since_evaluated": 1, "thesis_id": 1}
        p = proposal_store.record(run_id="r1", portfolio_id="P", row=row,
                                  nav=100000.0, decision_date="2026-08-31",
                                  expires="2026-09-04", compliance={"status": "PARTIAL"})
        no = execution.execute_one(p, session={"date": "2026-09-01", "open": 182.0})
        if no["status"] != execution.FAILED:
            return False
        ap = proposal_store.transition(p["id"], proposal_store.APPROVED, actor="t")
        yes = execution.execute_one(ap, session={"date": "2026-09-01", "open": 182.0})
        h = book.holdings_map("P").get("AAA") or {}
        return (yes["status"] == execution.FILLED and h.get("shares") == 10
                and yes["execution_price"] == 182.0)


def _b88_waits_for_session():
    """下一个交易日还没到 → 等，**保持已批准，不拿今天的价硬成交**。"""
    with _tmpdb():
        from cio import book, execution, proposal_store, rebalance
        book.open_book("P", capital=100000.0, opened_on="2026-08-31")
        row = {"ticker": "BBB", "basis": rebalance.TARGET, "reason": "",
               "target_weight": 0.02, "target_shares": 5, "current_shares": 0,
               "delta_shares": 5, "decision_price": 180.0, "est_value": 900.0,
               "action": rebalance.BUY, "days_since_evaluated": 1, "thesis_id": 1}
        p = proposal_store.record(run_id="r1", portfolio_id="P", row=row,
                                  nav=100000.0, decision_date="2026-08-31",
                                  expires="2026-09-04", compliance={"status": "PARTIAL"})
        p = proposal_store.transition(p["id"], proposal_store.APPROVED, actor="t")
        r = execution.execute_one(p, session={})
        return (r["status"] == execution.WAITING
                and proposal_store.get(p["id"])["state"] == proposal_store.APPROVED)


def _b88_execution_idempotent_and_no_partial():
    with _tmpdb():
        from cio import book, execution, proposal_store, rebalance
        from cio.db import connect as _c
        book.open_book("P", capital=100000.0, opened_on="2026-08-31")

        def mk(tk, delta, run):
            row = {"ticker": tk, "basis": rebalance.TARGET, "reason": "",
                   "target_weight": 0.02, "target_shares": delta,
                   "current_shares": 0, "delta_shares": delta,
                   "decision_price": 100.0, "est_value": delta * 100.0,
                   "action": rebalance.BUY, "days_since_evaluated": 1,
                   "thesis_id": 1}
            p = proposal_store.record(run_id=run, portfolio_id="P", row=row,
                                      nav=100000.0, decision_date="2026-08-31",
                                      expires="2026-09-04",
                                      compliance={"status": "PARTIAL"})
            return proposal_store.transition(p["id"], proposal_store.APPROVED,
                                             actor="t")
        s = {"date": "2026-09-01", "open": 100.0}
        p = mk("CCC", 5, "r1")
        execution.execute_one(p, session=s)
        again = execution.execute_one({**proposal_store.get(p["id"]),
                                       "state": proposal_store.APPROVED}, session=s)
        with _c() as con:
            n = con.execute("SELECT COUNT(*) FROM book_trade WHERE ticker='CCC'"
                            ).fetchone()[0]
        # 现金不足：整条不成交，且不动现金
        cash0 = book.cash("P")
        big = mk("DDD", 100000, "r2")
        bad = execution.execute_one(big, session=s)
        return (again["status"] == execution.SKIPPED and n == 1
                and bad["status"] == execution.FAILED
                and abs(book.cash("P") - cash0) < 1e-9)


def _b88_exit_keeps_row():
    """平仓是置 open=0 并留行 —— 删行会让业绩归因失去分母。"""
    with _tmpdb():
        from cio import book, execution, proposal_store, rebalance
        from cio.db import connect as _c
        book.open_book("P", capital=100000.0, opened_on="2026-08-31")
        with _c() as con:
            con.execute("INSERT INTO book_position(portfolio_id,ticker,shares,"
                        "avg_cost,opened_on,opened_run_id,last_evaluated_on,"
                        "realized_pnl,open) VALUES('P','EEE',10,100.0,'2026-08-20',"
                        "'r0','2026-08-20',0,1)")
        row = {"ticker": "EEE", "basis": rebalance.EXIT_DECIDED, "reason": "",
               "target_weight": 0.0, "target_shares": 0, "current_shares": 10,
               "delta_shares": -10, "decision_price": 120.0, "est_value": 0.0,
               "action": rebalance.EXIT, "days_since_evaluated": 1, "thesis_id": 1}
        p = proposal_store.record(run_id="r1", portfolio_id="P", row=row,
                                  nav=100000.0, decision_date="2026-08-31",
                                  expires="2026-09-04", compliance={"status": "PARTIAL"})
        p = proposal_store.transition(p["id"], proposal_store.APPROVED, actor="t")
        execution.execute_one(p, session={"date": "2026-09-01", "open": 120.0})
        with _c() as con:
            r = con.execute("SELECT shares, open, closed_on, realized_pnl FROM "
                            "book_position WHERE ticker='EEE'").fetchone()
        return (r is not None and r[1] == 0 and r[2] == "2026-09-01"
                and abs(r[3] - 200.0) < 1e-9 and "EEE" not in book.holdings_map("P"))


def _b88_no_same_session_proceeds():
    """美股 T+1 交收：同场卖出的回款当天不能拿去买。"""
    with _tmpdb():
        from cio import book, execution, proposal_store, rebalance
        from cio.db import connect as _c
        book.open_book("P", capital=100.0, opened_on="2026-08-31")
        with _c() as con:
            con.execute("INSERT INTO book_position(portfolio_id,ticker,shares,"
                        "avg_cost,opened_on,opened_run_id,last_evaluated_on,"
                        "realized_pnl,open) VALUES('P','FFF',100,10.0,'2026-08-20',"
                        "'r0','2026-08-20',0,1)")

        def mk(tk, delta, cur, tgt, run, act):
            row = {"ticker": tk, "basis": rebalance.TARGET, "reason": "",
                   "target_weight": 0.02, "target_shares": tgt,
                   "current_shares": cur, "delta_shares": delta,
                   "decision_price": 50.0, "est_value": tgt * 50.0,
                   "action": act, "days_since_evaluated": 1, "thesis_id": 1}
            p = proposal_store.record(run_id=run, portfolio_id="P", row=row,
                                      nav=100000.0, decision_date="2026-08-31",
                                      expires="2026-09-04",
                                      compliance={"status": "PARTIAL"})
            return proposal_store.transition(p["id"], proposal_store.APPROVED,
                                             actor="t")
        mk("FFF", -100, 100, 0, "r1", rebalance.EXIT)
        mk("GGG", 10, 0, 10, "r2", rebalance.BUY)
        res = execution.run("P", today="2026-09-02",
                            sessions={"FFF": {"date": "2026-09-01", "open": 50.0},
                                      "GGG": {"date": "2026-09-01", "open": 50.0}})
        by = {r["ticker"]: r for r in res["rows"]}
        order = [r["ticker"] for r in res["rows"]]
        return (by["FFF"]["status"] == execution.FILLED
                and by["GGG"]["status"] == execution.FAILED
                and order.index("FFF") < order.index("GGG"))


def _b88_approval_blocked_on_breach():
    """合规破限的提案，命令行与 Telegram 都不能直接批。"""
    import inspect as _i
    from pathlib import Path as _P
    src = (_P(__file__).resolve().parents[1] / "run_approve.py").read_text(encoding="utf-8")
    from cio import tgbot
    tg = _i.getsource(tgbot._do) + _i.getsource(tgbot._on_callback)
    return ("compliance.BREACH" in src and "--force" in src
            and "compliance.BREACH" in tg)


def _b88_tgbot_send_honours_dryrun():
    """`tgbot.send` 必须认 CIO_TG_DRYRUN，并按**真实结果**返回。

    这一条是被自己写出来的缺陷逼出来的：send 最初不看 DRYRUN，
    于是调用方印"只打印未真发"的同时，send 在背后真发了一条。
    一句报告和一次真实发送同时存在，不冲突、不报错。
    """
    import os as _o
    from cio import tgbot
    from cio.config import settings as _s
    old = _s.TG_DRYRUN
    _s.TG_DRYRUN = True
    try:
        r = tgbot.send("probe —— 这条不该真的发出去", chat_id="0")
    finally:
        _s.TG_DRYRUN = old
    return r is False


def _b88_tgbot_separate_token_and_allowlist():
    """控制台默认用独立 token（避免和 OpenClaw 抢 getUpdates），且只听一个 chat。"""
    import inspect as _i
    import os as _o
    from cio import tgbot
    had = _o.environ.get("CIO_CTRL_BOT_TOKEN")
    _o.environ["CIO_CTRL_BOT_TOKEN"] = "abc123"
    try:
        tk, shared = tgbot.token()
    finally:
        if had is None:
            _o.environ.pop("CIO_CTRL_BOT_TOKEN", None)
        else:
            _o.environ["CIO_CTRL_BOT_TOKEN"] = had
    serve = _i.getsource(tgbot.serve)
    return (tk == "abc123" and shared is False
            and "TG_CHAT_ID" in serve            # 只听自己的 chat
            and "subprocess" in _i.getsource(tgbot._run))   # 子进程，不 import


# ---------------------------------------------------------------- build89
# Build 3：公司行为 → 盯市 → 对账 → 盈亏表。
def _b89_seed(con, pid="P", ticker="X", shares=100, cost=800.0,
              opened="2026-08-20"):
    con.execute("INSERT INTO book_position(portfolio_id,ticker,shares,avg_cost,"
                "opened_on,opened_run_id,last_run_id,last_evaluated_on,"
                "realized_pnl,open) VALUES(?,?,?,?,?,'r0','r0',?,0,1)",
                (pid, ticker, shares, cost, opened, opened))


def _b89_split_no_phantom_loss():
    """**4:1 拆股不能变成 −75%。**

    不处理公司行为时：账本 100 股 @800，市场 200 —— 持仓当天显示 −75%，
    没有异常、没有告警，净值曲线上只多一个巨亏日。
    """
    with _tmpdb():
        from cio import book, corp_actions, valuation
        from cio.db import connect as _c
        book.open_book("P", capital=100000.0, opened_on="2026-08-19")
        with _c() as con:
            _b89_seed(con)
        before = 100 * 800.0
        r = corp_actions.apply_one("P", book.holdings_map("P")["X"],
                                   {"kind": corp_actions.SPLIT,
                                    "ex_date": "2026-09-01", "ratio": 4.0,
                                    "amount_per_share": None}, ex_close=200.0)
        h = book.holdings_map("P")["X"]
        m = valuation.mark("P", on="2026-09-01", prices={"X": 200.0}, bench_close=None)
        return (r["applied"] and h["shares"] == 400
                and abs(h["avg_cost"] - 200.0) < 1e-9
                and abs(m["holdings_value"] - before) < 1e-6)


def _b89_split_without_price_refused():
    """取不到除权日价 → **不应用**。硬取整会静默吞掉零股价值。"""
    with _tmpdb():
        from cio import book, corp_actions
        from cio.db import connect as _c
        book.open_book("P", capital=100000.0, opened_on="2026-08-19")
        with _c() as con:
            _b89_seed(con, shares=5, cost=100.0)
        r = corp_actions.apply_one("P", book.holdings_map("P")["X"],
                                   {"kind": corp_actions.SPLIT,
                                    "ex_date": "2026-09-01", "ratio": 1.5,
                                    "amount_per_share": None}, ex_close=None)
        return (not r["applied"] and book.holdings_map("P")["X"]["shares"] == 5)


def _b89_dividend_cash_and_idempotent():
    with _tmpdb():
        from cio import book, corp_actions
        from cio.db import connect as _c
        book.open_book("P", capital=100000.0, opened_on="2026-08-19")
        with _c() as con:
            _b89_seed(con, shares=100, cost=50.0)
        a = {"kind": corp_actions.DIVIDEND, "ex_date": "2026-09-01",
             "ratio": None, "amount_per_share": 0.75}
        corp_actions.apply_one("P", book.holdings_map("P")["X"], a)
        c1 = book.cash("P")
        again = corp_actions.apply_one("P", book.holdings_map("P")["X"], a)
        return (abs(c1 - 100075.0) < 1e-9 and not again["applied"]
                and abs(book.cash("P") - c1) < 1e-9
                and abs(corp_actions.cash_from_actions("P") - 75.0) < 1e-9)


def _b89_nav_null_when_unpriced():
    """缺价 → nav 为 NULL、complete=0。**曲线上让空白是空白。**"""
    with _tmpdb():
        from cio import book, valuation
        from cio.db import connect as _c
        book.open_book("P", capital=100000.0, opened_on="2026-08-19")
        with _c() as con:
            _b89_seed(con, ticker="A", shares=10, cost=100.0)
            _b89_seed(con, ticker="B", shares=10, cost=100.0)
        m = valuation.mark("P", on="2026-09-01", prices={"A": 110.0}, bench_close=None)
        with _c() as con:
            row = con.execute("SELECT nav, complete, n_unpriced FROM book_nav "
                              "WHERE portfolio_id='P' AND date='2026-09-01'"
                              ).fetchone()
        return (m["nav"] is None and m["unpriced"] == ["B"]
                and row[0] is None and row[1] == 0 and row[2] == 1)


def _b89_day_pnl_none_after_incomplete_day():
    """前一日不完整 → 当日盈亏 None，**不能把多日变动挂在一天上**。"""
    with _tmpdb():
        from cio import book, valuation
        from cio.db import connect as _c
        book.open_book("P", capital=100000.0, opened_on="2026-08-19")
        with _c() as con:
            _b89_seed(con, ticker="A", shares=10, cost=100.0)
            _b89_seed(con, ticker="B", shares=10, cost=100.0)
        valuation.mark("P", on="2026-09-01", prices={"A": 100.0}, bench_close=None)
        m = valuation.mark("P", on="2026-09-02",
                           prices={"A": 110.0, "B": 110.0}, bench_close=None)
        return m["nav"] is not None and m["day_pnl"] is None


def _b89_backfill_recomputes_pnl():
    """补写更早的日子后，后面几行的当日盈亏必须重算。

    `day_pnl` 是**派生量**：不重算的话，先写的那行算的是"相对初始资金"，
    数字完全正常，只是答非所问。顺带：盯市日早于开账日必须被拒绝。
    """
    with _tmpdb():
        from cio import book, valuation
        from cio.db import connect as _c
        book.open_book("P", capital=100000.0, opened_on="2026-08-25")
        with _c() as con:
            _b89_seed(con, ticker="A", shares=10, cost=100.0, opened="2026-08-25")
            _b89_seed(con, ticker="B", shares=10, cost=100.0, opened="2026-08-25")
        first = valuation.mark("P", on="2026-08-31",
                               prices={"A": 110.0, "B": 110.0}, bench_close=None)
        valuation.mark("P", on="2026-08-28",
                       prices={"A": 100.0, "B": 100.0}, bench_close=None)
        valuation.mark("P", on="2026-08-30", prices={"A": 105.0}, bench_close=None)
        rows = {r["date"]: r for r in valuation.series("P")}
        early = valuation.mark("P", on="2026-08-01", prices={"A": 100.0},
                               bench_close=None)
        return (abs((first["day_pnl"] or 0) - 2200.0) < 1e-6
                and rows["2026-08-31"]["day_pnl"] is None
                and abs(rows["2026-08-28"]["day_pnl"] - 2000.0) < 1e-6
                and not early.get("ok"))


def _b89_recon_three_identities():
    """三条恒等式；缺价当天第 1 条是**不适用**而不是通过。"""
    with _tmpdb():
        from cio import book, recon, valuation
        from cio.db import connect as _c
        book.open_book("P", capital=100000.0, opened_on="2026-08-19")
        with _c() as con:
            _b89_seed(con, ticker="A", shares=10, cost=100.0)
            con.execute("INSERT INTO book_trade(run_id,portfolio_id,ticker,side,"
                        "shares,decision_date,decision_price,execution_date,"
                        "execution_price,execution_price_basis,commission,slippage,"
                        "cash_flow,created_at) VALUES('r0','P','A','BUY',10,"
                        "'2026-08-20',100.0,'2026-08-20',100.0,'T+1_OPEN',0,0,"
                        "-1000.0,'2026-08-20')")
            con.execute("UPDATE book_portfolio SET cash=cash-1000 WHERE portfolio_id='P'")
        valuation.mark("P", on="2026-09-01", prices={"A": 100.0}, bench_close=None)
        good = recon.check("P", "2026-09-01")
        if good["status"] != recon.PASS:
            return False
        # 缺价那天
        valuation.mark("P", on="2026-09-02", prices={}, bench_close=None)
        skip = recon.check("P", "2026-09-02")
        c1 = [x for x in skip["checks"] if x["id"] == "nav_identity"][0]
        # 有人动了现金却没有交易
        with _c() as con:
            con.execute("UPDATE book_portfolio SET cash=cash-500 WHERE portfolio_id='P'")
        bad = recon.check("P", "2026-09-01")
        c2 = [x for x in bad["checks"] if x["id"] == "cash_identity"][0]
        ids = {c["id"] for c in good["checks"]}
        return (ids == {"nav_identity", "cash_identity", "position_identity"}
                and c1["status"] == recon.SKIPPED and skip["status"] != recon.PASS
                and c2["status"] == recon.FAIL and bad["status"] == recon.FAIL)


def _b89_no_excess_without_total_return_bench():
    """没有基准 → 超额**不计算**，不用价格收益顶替含息总回报。

    **`use_bench=False` 是这条探针能在任何机器上给出同一个答案的关键。**
    上一版只传 `bench_close=None`，而那个值同时表示"没传"和"没有"：
    有网的机器会真去取 SPY，于是超额算得出来，探针就红了——
    红的不是安装，是探针自己。同一份断言在两台机器上测的是两件事。
    """
    with _tmpdb():
        from cio import book, render_book, valuation
        from cio.db import connect as _c
        book.open_book("P", capital=100000.0, opened_on="2026-08-19")
        with _c() as con:
            _b89_seed(con, ticker="A", shares=10, cost=100.0)
        m = valuation.mark("P", on="2026-09-01", prices={"A": 100.0},
                           use_bench=False)
        st = valuation.statement("P", mark_result=m)
        txt = render_book.render_text(st)
        return ("TOTAL_RETURN" in valuation.BENCH_BASIS
                and m["bench_cum_return"] is None
                and st["excess"] is None and st["invested_pct"] is not None
                and "不计算" in txt)


def _b89_mark_shape_is_stable():
    """`mark()` 的每一条路径都返回同一组键。

    真机上炸过：早退路径只返回 `{"ok": False, "note": ...}`，
    调用方一句 `m['nav']` 直接 KeyError —— 而崩在调用方那一行，
    看起来像调用方写错了。返回形状随执行路径变化，就是给每个调用方埋坑。
    """
    with _tmpdb():
        from cio import book, valuation
        never = valuation.mark("NOPE", on="2026-09-01", use_bench=False)
        book.open_book("P", capital=100000.0, opened_on="2026-08-19")
        before = valuation.mark("P", on="2026-08-01", prices={}, use_bench=False)
        good = valuation.mark("P", on="2026-09-01", prices={}, use_bench=False)
        keys = set(valuation._SHAPE)
        return (keys <= set(never) and keys <= set(before) and keys <= set(good)
                and never["nav"] is None and before["nav"] is None
                and not never["ok"] and not before["ok"] and good["ok"])


def _b89_open_date_cannot_be_future():
    """开账日不能在未来；空账本可以改正，有记录的不许改。"""
    with _tmpdb():
        from cio import book
        from cio.config import market_date
        from datetime import datetime, timedelta
        tomorrow = (datetime.strptime(str(market_date())[:10], "%Y-%m-%d")
                    + timedelta(days=1)).date().isoformat()
        try:
            book.open_book("P", capital=100000.0, opened_on=tomorrow)
            return False
        except ValueError:
            pass
        book.open_book("P", capital=100000.0, opened_on="2026-08-19")
        book.open_book("P", opened_on="2026-08-20", reset_open_date=True)
        if book.portfolio_row("P")["opened_on"] != "2026-08-20":
            return False
        from cio.db import connect as _c
        with _c() as con:
            _b89_seed(con, ticker="A", shares=10, cost=100.0)
        try:
            book.open_book("P", opened_on="2026-08-21", reset_open_date=True)
            return False                     # 有持仓还允许改 → 不合格
        except ValueError:
            return not book.is_untouched("P")


def _b89_book_renderers_agree():
    """文本 / Markdown / HTML 三处内容一致 —— 这个仓库在这上面栽过。"""
    with _tmpdb():
        from cio import book, render_book, valuation
        from cio.db import connect as _c
        book.open_book("P", capital=100000.0, opened_on="2026-08-19")
        with _c() as con:
            _b89_seed(con, ticker="A", shares=10, cost=100.0)
        m = valuation.mark("P", on="2026-09-01", prices={"A": 110.0}, bench_close=None)
        st = valuation.statement("P", mark_result=m)
        outs = [render_book.render_text(st), render_book.render_md(st),
                render_book.render_html(st)]
        return all("A" in s and "复审" in s for s in outs)


def _b89_actions_run_before_marking():
    """`run_book.py` 里公司行为必须排在盯市之前。

    反了的话盯市会拿**拆后价格**乘**拆前股数**，那一天的市值凭空翻几倍
    或掉四分之三 —— 一个完全正常的数字，没有任何报错。
    """
    from pathlib import Path as _P
    src = (_P(__file__).resolve().parents[1] / "run_book.py").read_text(encoding="utf-8")
    a = src.find("corp_actions.sync(")
    b = src.find("valuation.mark(")
    return 0 <= a < b


# ---------------------------------------------------------------- build90
def _b90_substance_judged_before_truncation():
    """**实质度必须在截断之前判。**

    旧顺序：按相关性排序 → 截到 10 条 → 才判实质度。于是一条真有增量事实
    的材料，只要相关性排第 11 位就永远到不了闸门，而报告写「10 条材料，
    实质 0」——读起来是"今天确实没东西"。真机上 TSM/ARM/KLAC/AMD
    四只整整齐齐都是 10 条，那不是巧合，是撞上了上限。
    """
    from cio import material_gate, unit_a

    class N:
        def __init__(s, t, sc):
            s.title_original, s.title_zh, s.score = t, "", sc
    filler = [N(f"Nvidia Stock: What Analysts Expect Ahead of Earnings {i}", 100 - i)
              for i in range(10)]
    real = N("Nvidia announced it has completed the acquisition of Run:ai "
             "for $700 million", 1)
    items = filler + [real]
    ranked, tiers, tier_of = unit_a._rank_by_substance(items)
    kept = ranked[:unit_a.MATERIAL_CAP]
    old_top = sorted(items, key=lambda n: -n.score)[:unit_a.MATERIAL_CAP]
    return (tiers.get(material_gate.SUBSTANTIVE) == 1
            and any(tier_of[id(n)] == material_gate.SUBSTANTIVE for n in kept)
            # 对照：旧顺序确实会把它丢掉，否则这条探针证明不了什么
            and not any(tier_of[id(n)] == material_gate.SUBSTANTIVE
                        for n in old_top)
            and unit_a.MATERIAL_POOL > unit_a.MATERIAL_CAP * 2)


def _b90_ranking_uses_the_gate_classifier():
    """排序调的是 `material_gate.classify` **本身**，不是另写一套近似规则。

    验证办法：把 classify 换掉，看排序是否跟着变。跟着变才说明它真在调。
    两套规则一定会漂移，而这一套只在排序里悄悄生效，漂了没人会发现。
    """
    from cio import material_gate, unit_a

    class N:
        def __init__(s, t, sc):
            s.title_original, s.title_zh, s.score = t, "", sc
    items = [N("aaa", 1), N("zzz", 99)]
    real = material_gate.classify
    try:
        material_gate.classify = lambda t: (
            (material_gate.SUBSTANTIVE, "x") if t == "aaa"
            else (material_gate.EMPTY, "y"))
        ranked, _t, _to = unit_a._rank_by_substance(items)
        return ranked[0].title_original == "aaa"
    finally:
        material_gate.classify = real


def _b90_intake_is_reported():
    """截断必须看得见：采集多少 → 进闸门多少 → 截掉多少。

    没有这一行，「10 条材料，实质 0」和"我们只看了其中 10 条"在页面上
    完全一样。
    """
    from cio import unit_a
    base = {"raw": 60, "scored": 42, "pool": 40, "relevant": 25, "cap": 10,
            "kept": 10, "dropped": 15,
            "tiers_before_cap": {"实质": 2, "背景": 8, "无实质": 15}}
    quiet = unit_a.intake_note({"intake": dict(base, dropped_substantive=0)})
    loud = unit_a.intake_note({"intake": dict(base, dropped_substantive=3)})
    return ("25" in quiet and "截掉 15" in quiet
            and "3 条实质材料" in loud and "⚠" in loud
            and unit_a.intake_note({}) == "")


def _b90_scan_surfaces_intake():
    """`run_scan.py` 把进料口径印出来，且失败行也带这两个键（形状一致）。"""
    import ast
    from pathlib import Path as _P
    src = (_P(__file__).resolve().parents[1] / "run_scan.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "scan_one"), None)
    if fn is None:
        return False
    keys = {k.value for n in ast.walk(fn) if isinstance(n, ast.Dict)
            for k in n.keys if isinstance(k, ast.Constant)}
    return ({"intake", "intake_note"} <= keys
            and "intake_note" in src.split("def main")[-1])


def _b90_tests_block_the_network():
    """每个测试脚本都装了断网闸，且闸门本身两层齐全。

    一条会悄悄联网的断言，在两台机器上就是两个测试 —— build89a 的
    那次误报就是这么来的。curl_cffi 绕过 Python socket，所以除了
    socket 还要用代理环境变量兜住。
    """
    import ast
    from pathlib import Path as _P
    sd = _P(__file__).resolve().parent
    guard = sd / "_no_network.py"
    if not guard.exists():
        return False
    g = guard.read_text(encoding="utf-8")
    two_layers = ("socket.socket.connect" in g
                  and any(v in g for v in ("HTTPS_PROXY", "https_proxy")))
    missing = []
    for f in sorted(sd.glob("test_*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import)
                 for a in n.names}
        if "_no_network" not in names:
            missing.append(f.name)
    return two_layers and not missing


# ---------------------------------------------------------------- build91
def _b91_gate_reads_source_text_not_model_summary():
    """闸门判实质度看 `basis_text`（源头文本），**不看含模型摘要的 `text`**。

    两个理由，都在真机上兑现过：

    一、`text` 里那句摘要是 Ollama 生成的。拿它判定，等于让一个零 LLM 的
        闸门去分类 LLM 的输出——摘要措辞一变结论就可能变，可复算没了；
        而这个闸门决定的正是"要不要启动 LLM 辩论"。
    二、摘要只对**入选后**的材料生成，于是入选的和被截掉的用两种不同的
        输入判定，两边的分档根本不可比——而报告上还印着
        "被截掉的都不是实质"。
    """
    from cio import material_gate, unit_a
    from cio.models import MaterialItem

    class N:
        def __init__(s, t, b=""):
            s.title_original, s.title_zh, s.body, s.score = t, "", b, 1
    vague = "AMD in the spotlight this week"
    body = ("Advanced Micro Devices announced it has completed the acquisition "
            "of ZT Systems for $4.9 billion, the company said Monday.")
    n = N(vague, body)
    display = vague + "：这周值得关注"
    without = MaterialItem(id=1, text=display)
    withb = MaterialItem(id=1, text=display, basis_text=unit_a.basis_text(n))
    return (material_gate.classify(vague)[0] != material_gate.SUBSTANTIVE
            and material_gate.assess([without])["n_sub"] == 0
            and material_gate.assess([withb])["n_sub"] == 1
            and material_gate.basis_of(MaterialItem(id=1, text="x")) == "x")


def _b91_ranker_and_gate_cannot_disagree():
    """同一条材料，排序的判定与闸门的判定必须**逐条相同**。

    上一版排序只看标题、闸门看"标题 + 模型摘要"，于是两个方向的分歧
    都出现了：AVGO 排序判实质而闸门判不是；AMD/LRCX 反过来——
    结果是修完截断反而把它们的实质材料挤掉了。
    """
    from cio import material_gate, unit_a
    from cio.models import MaterialItem

    class N:
        def __init__(s, t, b=""):
            s.title_original, s.title_zh, s.body, s.score = t, "", b, 1
    cases = [("AMD in the spotlight this week",
              "AMD announced it has completed the acquisition of ZT Systems "
              "for $4.9 billion."),
             ("Nvidia announced it has completed the acquisition of Run:ai "
              "for $700 million", ""),
             ("Nvidia Stock: What Analysts Expect Ahead of Q3 Earnings", "")]
    for title, body in cases:
        n = N(title, body)
        rank_tier, _ = material_gate.classify(unit_a.basis_text(n))
        m = MaterialItem(id=1, text=title + "：给人读的摘要",
                         basis_text=unit_a.basis_text(n))
        gate_tier, _ = material_gate.classify(material_gate.basis_of(m))
        if rank_tier != gate_tier:
            return False
    return True


def _b91_body_is_enriched_before_the_cut():
    """补正文发生在**截断之前**，且名额大于最终条数。

    判定要在"哪十条能被看见"这个决定之前完成，名额花在这里才有意义。
    原来的 `enrich_fulltext(raws, top_n=10)` 跑在去重和相关性清洗**之前**，
    那 10 个名额有相当一部分花在了后面会被丢掉的条目上。

    **断言走 AST，不走字符串查找。** 第一版用 `src.find()` 比位置，
    结果 `_rank_by_substance` 的"首次出现"落在上一行那句
    `# 顺序很重要，见 _rank_by_substance` 的注释里，于是顺序判反、误报。
    这个仓库在"探针 grep 到自己的注释"上已经栽过五次，这是第六次——
    **只要还在比字符串，就还会有第七次。**
    """
    import ast
    import inspect as _i
    from cio import collect, unit_a
    if not hasattr(collect, "enrich_news_fulltext"):
        return False
    tree = ast.parse(_i.getsource(unit_a.collect_materials).lstrip())
    calls = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = getattr(f, "attr", None) or getattr(f, "id", None)
            if name in ("enrich_news_fulltext", "_rank_by_substance"):
                calls.setdefault(name, node.lineno)
    # 截断那一步：`ranked[:MATERIAL_CAP]`
    cut = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Subscript)
           and isinstance(n.slice, ast.Slice)
           and isinstance(getattr(n.slice, "upper", None), ast.Name)
           and n.slice.upper.id == "MATERIAL_CAP"]
    if not ({"enrich_news_fulltext", "_rank_by_substance"} <= set(calls)) or not cut:
        return False
    return (calls["enrich_news_fulltext"] < calls["_rank_by_substance"] < min(cut)
            and unit_a.BASIS_ENRICH_N > unit_a.MATERIAL_CAP
            and unit_a.BASIS_BODY_CHARS >= 200)


# ---------------------------------------------------------------- build92
def _b92_unit_a_fetches_edgar():
    """一部要取 SEC EDGAR。**dossier 和 topic 早就在取，唯独一部没取**——
    而一部是最需要"已发生的事实"的那一个。"""
    import ast
    import inspect as _i
    from cio import collect, unit_a
    tree = ast.parse(_i.getsource(unit_a.collect_materials).lstrip())
    names = {getattr(n.func, "attr", None) or getattr(n.func, "id", None)
             for n in ast.walk(tree) if isinstance(n, ast.Call)}
    return ("fetch_edgar_recent" in names and "_get_cik" in names
            and "with_body" in _i.signature(collect.fetch_edgar_recent).parameters)


def _b92_placeholder_cannot_fake_evidence():
    """**采集器自己写的占位串不得被当成外部证据。**

    `fetch_edgar_recent` 给每份公告造了一行 `SEC filing 8-K filed <日期>.`，
    而里面正好有 `8-K`（硬锚点）和 `filed`（完成动作）——纯文本规则据此
    判「实质」。判的不是公司做了什么，是**我们自己写下的那句话**。
    后果：每份公告都自动过闸，哪怕它一个字的内容都没取到，
    于是闸门开了而论据是空存根。
    """
    from cio import material_gate as mg
    stub = "NVIDIA CORP 8-K (2026-08-28)\nSEC filing 8-K filed 2026-08-28."
    filed = stub + "\n" + ("On August 28, 2026, NVIDIA Corporation entered into "
                           "a definitive agreement to acquire " * 6)
    url = "https://www.sec.gov/Archives/edgar/data/1045810/x.htm"
    return (mg.classify(stub)[0] == mg.SUBSTANTIVE           # 纯文本会被骗
            and mg.tier_of(stub, "EDGAR", url)[0] == mg.CONTEXT     # 守住了
            and "正文未取到" in mg.tier_of(stub, "EDGAR", url)[1]
            and mg.tier_of(filed, "EDGAR", url)[0] == mg.SUBSTANTIVE
            and mg.PRIMARY_MIN_CHARS >= 100)


def _b92_news_sources_unchanged():
    """普通新闻源**不因来源被升级也不被降级** —— 老原则没变。"""
    from cio import material_gate as mg
    hot = "Is Nvidia Stock a Buy Ahead of Q2 Earnings?"
    real = ("Reuters: Nvidia announced it has completed the acquisition of "
            "Run:ai for $700 million")
    return (mg.tier_of(hot, "Zacks", "https://zacks.com/x")[0] == mg.classify(hot)[0]
            and mg.tier_of(real, "Reuters", "https://reuters.com/x")[0] == mg.SUBSTANTIVE
            and not mg.is_primary("Reuters", "https://reuters.com/x")
            and mg.is_primary("EDGAR", "") and mg.is_primary("", "https://data.sec.gov/x"))


def _b92_edgar_body_uses_sec_user_agent():
    """抓公告正文必须带 SEC 要求的 UA，否则 403。

    交给 `trafilatura.fetch_url` 会用它自己的 UA 被拒，而拒绝的表现是
    "这份公告没有正文"——和"这份公告确实没内容"长得一模一样。
    """
    import ast
    import inspect as _i
    from cio import collect
    tree = ast.parse(_i.getsource(collect._edgar_body).lstrip())
    consts = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)
              and isinstance(n.value, str)}
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    return "User-Agent" in consts and "SEC_USER_AGENT" in attrs


# ---------------------------------------------------------------- build93
def _b93_filings_bypass_relevance_filter():
    """**按 CIK 取回的公告不过相关性闸。**

    相关性闸认的是"标的名出现在原始标题里"，而公告标题用公司**法定名称**：
    `ADVANCED MICRO DEVICES INC 8-K` 里没有 "AMD"、`KLA CORP` 里没有 "KLAC"。
    真机上 10 只票有 9 只的公告在这一步被全部丢光，只有 ARM 侥幸活下来
    （"ARM" 恰好是 "Arm Holdings plc" 的子串）——而进料行只显示
    "采集 73 条（含 EDGAR 8 条）→ 相关 17"，**看不出那 8 条已经全没了**。

    公告的相关性由 SEC 的公司标识确定，不需要再用字符串猜。
    """
    from cio import topic, unit_a

    class Src:
        def __init__(s, n, u):
            s.name, s.url = n, u

    class NS:
        is_noise = False

        def __init__(s, t, src):
            s.title_original, s.title_zh, s.body, s.score, s.sources = t, "", "", 1, [src]
    sec = Src("EDGAR", "https://www.sec.gov/Archives/edgar/data/2488/x.htm")
    zx = Src("Zacks", "https://zacks.com/x")
    cases = {"AMD": "ADVANCED MICRO DEVICES INC 8-K (2026-08-28)",
             "MU": "MICRON TECHNOLOGY INC 10-Q (2026-08-28)",
             "KLAC": "KLA CORP 8-K (2026-08-28)",
             "NVDA": "NVIDIA CORP 8-K (2026-08-28)"}
    for sym, title in cases.items():
        info = topic.parse_subject(sym)
        if len(unit_a._prefilter([NS(title, sec)], info)) != 1:
            return False
        # 普通新闻仍要过闸 —— 规则本身没被削弱
        if unit_a._prefilter([NS("Intel earnings preview", zx)], info):
            return False
    return True


def _b93_filing_survival_is_reported():
    """公告"取了几条 / 过闸几条 / 进闸门几条"分开报，全军覆没要出 ⚠。"""
    from cio import unit_a
    base = {"raw": 73, "scored": 47, "pool": 40, "relevant": 17, "cap": 10,
            "kept": 10, "dropped": 7, "dropped_substantive": 0, "enriched": 1,
            "tiers_before_cap": {"背景": 15, "实质": 1}}
    ok = unit_a.intake_note({"intake": dict(base, edgar=8, edgar_kept=8,
                                            edgar_in_gate=5)})
    dead = unit_a.intake_note({"intake": dict(base, edgar=8, edgar_kept=0,
                                              edgar_in_gate=0)})
    return ("进闸门 5" in ok and "过相关性 8" in ok
            and "⚠" in dead and "全部未通过相关性闸" in dead)


# ---------------------------------------------------------------- build94
def _b94_edgar_recency_window():
    """**一部只收窗口内提交的公告，否则闸门等于被拆了。**

    SEC 的 submissions 接口返回"最近 8 份"，**和提交日期无关**：一家公司
    只要历史上提交过 8 份文件，就永远能取到 8 份。于是每只票每天都拿到
    8 份公告，闸门每天都判「材料充分」——evidence-triggered 的研究
    退化成每日评论台，而这正是闸门当初要防的东西。

    真机上接入 EDGAR 当天，10 只票**全部**变 SUFFICIENT、实质材料
    从 4% 跳到 57%。**看起来像大成功，其实是闸门被拆了，没有一处报错。**

    默认 `within_days=0`（不筛）保住 dossier / topic 的行为——
    它们要的是公司近况全貌，不是"今天有没有新事"。
    """
    import datetime
    import inspect as _i
    from cio import collect, unit_a
    today = datetime.date.today()
    fresh = (today - datetime.timedelta(days=2)).isoformat()
    stale = (today - datetime.timedelta(days=90)).isoformat()
    payload = {"name": "TESTCO", "filings": {"recent": {
        "form": ["8-K", "10-Q", "8-K"],
        "filingDate": [fresh, stale, stale],
        "accessionNumber": ["0001-26-000001", "0001-26-000002", "0001-26-000003"],
        "primaryDocument": ["a.htm", "b.htm", "c.htm"]}}}

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return payload
    real = collect.httpx.get
    try:
        collect.httpx.get = lambda *a, **k: _Resp()
        st_win, st_all = {}, {}
        win = collect.fetch_edgar_recent("0000000001", st_win, with_body=False,
                                         within_days=7)
        allf = collect.fetch_edgar_recent("0000000001", st_all, with_body=False)
    finally:
        collect.httpx.get = real
    # 一部确实传了窗口
    src = _i.getsource(unit_a.collect_materials)
    return (len(win) == 1 and len(allf) == 3
            and "滤掉 2 份更早的" in st_win["EDGAR"]
            and _i.signature(collect.fetch_edgar_recent
                             ).parameters["within_days"].default == 0
            and "EDGAR_WINDOW_DAYS" in src and unit_a.EDGAR_WINDOW_DAYS >= 3)


# ---------------------------------------------------------------- build95
def _b95_alias_word_boundary():
    """**短 ticker 不能用子串匹配。**

    真机上 ARM 的相关性闸放进来这几条，还判成了实质：

        Venezuelan opposition up in **arms** over US oil stake
        Reality check for China's c**arm**akers
        Ph**arm**a stocks slide / Al**arm** bells for chip supply

    委内瑞拉的石油新闻成了 ARM 的实质材料 —— 真跑一部，辩论会拿它当论据。
    三四个字母的 ticker 全有这问题：ARM / MU / KLA / AI / ON / IT。
    中文别名仍走子串（中文没有空格，加词边界反而会漏）。
    """
    from cio import unit_a
    bad = ["Venezuelan opposition up in arms over oil",
           "Reality check for China's carmakers",
           "Pharma stocks slide on tariff threat",
           "Alarm bells for chip supply chains"]
    good = ["Arm Holdings (ARM) rose 2.8%", "ARM HOLDINGS PLC /UK 144 (2026-08-27)",
            "Arm's royalty revenue grew 12%", "Analysts weigh in on ARM."]
    return (not any(unit_a.alias_hit("ARM", t) for t in bad)
            and all(unit_a.alias_hit("ARM", t) for t in good)
            and unit_a.alias_hit("工商银行", "中国工商银行发布公告"))


def _b95_ownership_forms_do_not_trigger_the_gate():
    """**Form 4 / 144 / SC 13G 不触发闸门。**

    真机上 AMD 判「材料充分」，三条实质材料全是内部人交易申报：
    CFO 的 Form 4、另一位高管的 Form 4、一份 Form 144。
    同一天真正的商业事件（与思科在沙特的 AI 基础设施上线）反倒判成背景。
    **证据层级整个反了。**

    它们不是"无实质"——是依法必须披露的真实文件；但说的是
    *某个人卖了股票*，不是*这家公司发生了什么*。高管按预定计划减持
    每季度都有，当成研究触发器，闸门就退化成一个日历。
    """
    from cio import material_gate as mg
    url = "https://www.sec.gov/Archives/edgar/data/2488/x.htm"
    body = " On August 27, 2026, the company entered into an agreement." * 8
    for form in ("4", "144", "SC 13G"):
        t = f"CO {form} (2026-08-27)\nSEC filing {form} filed 2026-08-27." + body
        tier, why = mg.tier_of(t, "EDGAR", url)
        if tier != mg.CONTEXT or "不触发闸门" not in why:
            return False
    for form in ("8-K", "10-Q", "10-K"):
        t = f"CO {form} (2026-08-27)\nSEC filing {form} filed 2026-08-27." + body
        if mg.tier_of(t, "EDGAR", url)[0] != mg.SUBSTANTIVE:
            return False
    return (mg.filing_form("X\nSEC filing SC 13G filed 2026-08-27.") == "SC 13G"
            and mg.filing_form("no filing") == ""
            and "4" in mg.OWNERSHIP_FORMS and "8-K" in mg.EVENT_FORMS)


def _b96_title_veto():
    """**标题自报是评论文时，正文里的过去式动词顶不上来。**

    build91 把正文加进判定依据，却没有重新校准按标题写的规则。
    后果：任何一篇评论文的正文里都有一个过去式动词和一个百分数，
    于是 `done + pct` 这条最弱通路把评论顶成实质。build95 真机上
    ARM 的三条"实质"全是这么来的，理由清一色「已发生动作 + 具体比例」。
    """
    from cio import material_gate as mg
    cases = [
        ("Arm (ARM) Stock Looks Above Fair Value Even After AI Progress\n"
         "Arm reported royalty revenue growth of 25% and management raised "
         "full-year guidance. Our fair value estimate is $105.", "估值观点文"),
        ("Advanced Micro Devices vs. Arm Holdings: Comparing Revenue Trends\n"
         "AMD reported revenue of $7.7 billion last quarter, up 32%.", "对比文"),
        ("Arm Rises 2.8% as $272 Target Prices the CPU Tollbooth\n"
         "Shares climbed 2.8% after an analyst raised his price target to $272.",
         "目标价"),
    ]
    for text, why_want in cases:
        tier, why = mg.classify(text)
        if tier == mg.SUBSTANTIVE or why != why_want:
            return False
    # 承诺过的通路不能被吃掉：标题里就带真事件的照判实质。
    return (mg.classify("Ahead of earnings, NVDA announced a $50B buyback")[0]
            == mg.SUBSTANTIVE
            and mg.classify(
                "AMD, Cisco and HUMAIN Expand Saudi AI Infrastructure\n"
                "AMD announced its Instinct systems have gone live under a "
                "contract awarded this year, part of a $10 billion buildout."
            )[0] == mg.SUBSTANTIVE)


def _b96_price_move_is_soft():
    """**「股价动了」不参与标题否决 —— 原因可能就写在同一个标题里。**

        Nvidia stock jumped after Beijing approved H20 sales
        AMD rose 12% after announcing a $10 billion Saudi contract

    把这两条按"行情复述"否决掉，丢的是监管放行和一份百亿合同。
    软标记照常打标签、照常堵死最弱通路，但不否决整条材料。

    软标记必须是**显式名单**：靠 `_NEGATIVE` 的排列顺序碰出来的话，
    插一条新规则就可能悄悄改掉别的材料的判定，且不会有任何报错。
    """
    from cio import material_gate as mg
    real = ["AMD rose 12% after announcing a $10 billion Saudi contract",
            "Nvidia stock jumped after Beijing approved H20 sales",
            "Micron shares fell 8% after the company cut its Q4 guidance"]
    noise = ["Micron Rises 4.1% as Analysts Lift Targets",
             "Nvidia stock rose 3% on Tuesday"]
    if not all(mg.classify(t)[0] == mg.SUBSTANTIVE for t in real):
        return False
    if any(mg.classify(t)[0] == mg.SUBSTANTIVE for t in noise):
        return False
    whys = {w for _rx, w in mg._NEGATIVE}
    if not mg._SOFT_WHY or (mg._SOFT_WHY - whys):
        return False
    first, hard = mg._neg_scan("Nvidia stock rose 3% on Tuesday")
    return first in mg._SOFT_WHY and not hard


def _b96_vs_needs_words_on_both_sides():
    """**"$13.3 billion vs $12.9 billion guidance" 是业绩事实，不是对比文。**

    裸的 `vs` 会把"实际 vs 指引"这类最标准的业绩标题整批误杀。
    对比文说的是把两家公司摆一起比 —— 两侧都得是词。
    """
    from cio import material_gate as mg
    tier, _ = mg.classify(
        "Intel Q3 revenue $13.3 billion vs $12.9 billion guidance\n"
        "Intel reported third-quarter revenue of $13.3 billion, above guidance.")
    return (tier == mg.SUBSTANTIVE
            and mg.classify("AMD vs Intel: Which Chip Stock Is the Better Buy?")[0]
            != mg.SUBSTANTIVE
            and mg.classify("Advanced Micro Devices vs. Arm Holdings")[1] == "对比文")


def _b96_regression_corpus():
    """**闸门的回归语料整份重跑。**

    build91 加正文那一改是对的，但它悄悄把最弱那条通路变成了几乎恒真，
    而没有人回去重验旧判例——四轮之后才在真机 `--verbose` 里看见后果，
    中间一次报错都没有，指标反而从 4% 涨到 29%，看起来像在变好。

    所以规则每改一次，**整份语料重跑一次**，不是只跑新加的那几条。
    """
    import _material_corpus as corpus
    from cio import material_gate as mg
    bad = corpus.run(mg)
    if bad:
        for b, head, want, got, why in bad:
            print(f"        [{b}] {head}\n           期望 {want} 实得 {got}·{why}")
        return False
    wants = [w for _b, _t, w, _n in corpus.CASES]
    # 两个方向都要有：只收"应判实质"会漏掉放行过宽，只收"应拦住"会漏掉误杀。
    return (corpus.TOTAL >= 25
            and wants.count(corpus.SUBSTANTIVE) >= 5
            and wants.count(corpus.EMPTY) >= 5)


def _b97_headline_present_tense():
    """**新闻标题的一般现在时表示【已经发生】。**

    真机 8/31：3 只票全 INSUFFICIENT，而当天两条一等一的实质材料就在里面——
    ARM 从授权模式转为自己卖数据中心芯片、AMD 沙特系统上线——
    全判成「背景·相关报道，无可核对的增量事实」，因为标题写的是
    `Shifts` / `Expand` / `Go Live` 而不是过去式。

    放宽的全部风险在于裸词形与不定式同形，所以 to / 情态动词必须挡住。
    """
    from cio import material_gate as mg
    real = ["AMD, Cisco and HUMAIN Expand Saudi Arabia's AI Infrastructure as "
            "AMD Instinct Systems Go Live",
            "Arm Holdings (ARM) Shifts Strategy to Sell Own Data Center Chips",
            "AMD and Cisco Expand AI Infrastructure in Saudi Arabia"]
    sched = ["Nvidia is set to expand capacity next quarter",
             "Analysts expect AMD to win more data center share",
             "AMD could acquire a networking vendor this year",
             "Arm will launch a server chip in 2027",
             "AMD's Saudi AI Bet Is Scaling Toward 1 Gigawatt"]
    return (all(mg.classify(t)[0] == mg.SUBSTANTIVE for t in real)
            and not any(mg.classify(t)[0] == mg.SUBSTANTIVE for t in sched))


def _b97_dictionary_word_ticker():
    """**ARM 本身就是一个英文单词** —— 词边界对它无能为力。

    真机 8/31 ARM 的 10 条材料里 4 条完全无关：浮动利率房贷、
    Glen Arm 的一场火灾、鲨鱼咬掉手臂、某机构的"资产管理部门"。
    每只标的只有 10 个进闸门的名额，这四条**挤掉了真材料**。

    两层：名单内的符号必须出现身份形态；名单之外靠"大小写必须一致"兜底。
    并且要真的接到 `_prefilter` 上——只加函数不接线是本仓库出过多次的缺陷。
    """
    from cio import topic, unit_a
    bad = ["Current ARM mortgage rates report for Aug. 31, 2026 - Fortune",
           "Multiple crews battle 2-alarm fire at small business in Glen Arm",
           "Mom Who Had Arm Amputated After Shark Attack Shuts Down GoFundMe",
           "Guggenheim affiliate buys up debt linked to its asset management arm"]
    good = ["Arm Holdings (ARM) Shifts Strategy to Sell Own Data Center Chips",
            "ARM's Expanding AI Growth Opportunity Goes Beyond Market Hype",
            "ARM stock rose 2.8% on Thursday", "NASDAQ:ARM upgraded by analysts"]
    if any(unit_a.symbol_hit("ARM", t) for t in bad):
        return False
    if not all(unit_a.symbol_hit("ARM", t) for t in good):
        return False
    if not unit_a.symbol_hit("AMD", "AMD lifted to Strong Buy - Investing.com"):
        return False
    # 兜底不依赖名单
    if (unit_a.symbol_hit("ZZZ", "he zzz through the meeting")
            or not unit_a.symbol_hit("ZZZ", "ZZZ posts record revenue")):
        return False
    # 真的接线了吗
    src = type("S", (), {"name": "Zacks", "url": "https://zacks.com/x"})()
    news = type("N", (), {"title_original": bad[0], "title_zh": "",
                          "sources": [src], "is_noise": False})()
    info = topic.parse_subject("ARM")
    return unit_a._prefilter([news], info) == []


def _b98_symbol_drops_are_visible():
    """**符号消歧砍掉了什么，必须能被看见。**

    build97 上线后 ARM 的相关材料 26 → 9：我预期挡掉 4 条噪音，实际 17 条。
    进料行上写的是"相关 9"——和"今天这只票没什么新闻"**一模一样**。

    截断至少有 dropped / dropped_substantive 报了好几个 build；
    相关性闸这一步一直是**完全的盲区**，丢掉的东西不出现在任何输出里。
    过滤器越狠，输出越像"世界很安静"——这是本项目反复撞上的形状。
    """
    import ast
    import inspect

    from cio import topic, unit_a
    src = type("S", (), {"name": "Zacks", "url": "https://zacks.com/x"})()

    def _n(t):
        return type("N", (), {"title_original": t, "title_zh": "",
                              "sources": [src], "is_noise": False})()
    info = topic.parse_subject("ARM")
    drops = {}
    kept = unit_a._prefilter(
        [_n("Current ARM mortgage rates report for Aug. 31, 2026"),
         _n("Multiple crews battle 2-alarm fire in Glen Arm"),
         _n("Bitcoin miners rally as ETF inflows accelerate"),
         _n("Arm Holdings (ARM) Shifts Strategy to Sell Own Data Center Chips")],
        info, drops)
    if len(kept) != 1 or len(drops.get(unit_a.DROP_SYMBOL) or []) != 2:
        return False
    # 裸符号根本没出现的不算在消歧头上，否则这个数没法用来判断砍得对不对
    if "Bitcoin" in " ".join(drops.get(unit_a.DROP_SYMBOL) or []):
        return False
    if unit_a._prefilter([_n("Arm Holdings (ARM) rose")], info) != []:
        pass                                   # 不传 drops 也要能跑
    base = {"raw": 106, "scored": 54, "pool": 40, "relevant": 9, "cap": 10,
            "kept": 9, "dropped": 0, "dropped_substantive": 0, "enriched": 6,
            "tiers_before_cap": {"实质": 1}}
    loud = unit_a.intake_note({"intake": dict(base, dropped_symbol=17)})
    mild = unit_a.intake_note({"intake": dict(base, relevant=20,
                                              dropped_symbol=3)})
    if not ("符号消歧丢弃 17 条" in loud and "⚠" in loud
            and "--verbose" in loud):
        return False
    if "⚠" in mild or "符号消歧丢弃 3 条" not in mild:
        return False
    # **接线检查。** 只加函数不接进 collect_materials / run_scan，
    # 新数据一个字都不会出现在输出里——这个仓库出过多次。
    cm = inspect.getsource(unit_a.collect_materials)
    tree = ast.parse(cm.lstrip())
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and getattr(n.func, "id", "") == "_prefilter"]
    if not any(len(c.args) >= 3 for c in calls):
        return False
    # **查的是 intake 字典的真实键，不是源码里有没有这串字符。**
    # 第一版这里写的是 `"dropped_symbol" in cm` —— 而它是
    # `"dropped_symbol_titles"` 的子串，所以把键改名成别的照样"通过"。
    # 「断言结构，不要断言注释/文本」这条在本仓库已经踩到第七次了。
    keys: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict) \
                and any(getattr(t, "id", "") == "intake" for t in node.targets):
            keys = {k.value for k in node.value.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    if not {"dropped_symbol", "dropped_symbol_titles", "dropped_by"} <= keys:
        return False
    rs = (Path(__file__).resolve().parents[1] / "run_scan.py").read_text("utf-8")
    return "dropped_symbol_titles" in rs and "dropped_symbol" in rs


def _b99_fact_clause_rescue():
    """**破折号前是事实，破折号后是钩子。**

        Arm Holdings Has $2 Billion in Orders It Cannot Fill Yet
            — Is ARM Stock a Buy at $257?

    20 亿在手订单填不满产能是硬事实，被"是否值得买"那半句整条否掉了。
    真机 8/31 一天两条真事实死在这个句式上。

    救援有三道闸：必须是多分句、分句自身不含硬标记、分句自身站得住
    （完成动作 + 锚点/事件，或 锚点 + 事件且无前瞻标记）。
    第三道里那条"无完成动作"的通路是新开的，因为
    `Has $2 Billion in Orders` 是**状态**——它和"签下 20 亿订单"一样可核对。
    """
    from cio import material_gate as mg
    real = ["Arm Holdings Has $2 Billion in Orders It Cannot Fill Yet "
            "— Is ARM Stock a Buy at $257?",
            "AMD Stock Jumps on $10 Billion Contract — Is It Too Late to Buy?",
            "Micron Signs a $4 Billion Supply Deal: Should You Buy the Stock?"]
    fake = ["Analysts See $5 Billion in Orders for AMD — Is It Enough?",
            "AMD Could Win $5 Billion in Orders — Is It Enough?",
            "AMD's $10 Billion Opportunity in Sovereign AI — Is the Market "
            "Underpricing It?",
            "Nvidia Stock Soars to $200 Billion Market Cap — Time to Sell?",
            "In order to compete, AMD must cut prices — Is the Stock a Buy?",
            "KLA Corporation (KLAC): 3 Reasons We Love This Stock",
            "Advanced Micro Devices vs. Arm Holdings: Comparing Revenue Trends",
            # **分句自身带硬标记的不救** —— 否则绕回 build96 那个缺陷：
            # 观点文只要在标题里引一个真数字就能被顶成实质材料。
            "Is AMD a Buy After Its $10 Billion Contract? — Analysts Weigh In",
            "3 Reasons AMD's $10 Billion Saudi Contract Matters — Our Take"]
    if not all(mg.classify(t)[0] == mg.SUBSTANTIVE for t in real):
        return False
    if any(mg.classify(t)[0] == mg.SUBSTANTIVE for t in fake):
        return False
    # 单分句不走救援；逗号不算分隔符（"AMD, Cisco and HUMAIN Expand …" 会拆坏）
    return (mg._fact_clause("AMD wins a $2 billion order") == ""
            and mg._fact_clause(
                "Ahead of earnings, NVDA announced a $50B buyback") == ""
            and mg._fact_clause(
                "Intel Wins a $3 Billion Foundry Order; Should You Buy?"
            ).startswith("Intel Wins"))


def _b100_events_not_articles():
    """**闸门数的必须是事件，不是文章。**

    真机 8/31 AMD 判「材料充分」，三条实质里两条是同一件事——
    一份新闻稿被两家转载，`_SUFFICIENT_N = 3` 就被转载量顶穿了。
    这和 build94「8 份历史公告 = 材料充分」是同一个家族：
    **同一件事被多次计数就能开门**，而开门意味着启动一场完整的多空辩论。

    归并必须写在标签上——不写的话，这一步就是又一个看不见的变换。
    """
    from cio import material_gate as mg
    from cio.models import MaterialItem
    a = ("AMD, Cisco and HUMAIN Expand Saudi Arabia's AI Infrastructure "
         "as AMD Instinct Systems Go Live - TipRanks")
    b = "AMD and Cisco Expand AI Infrastructure in Saudi Arabia"
    c = "Arm Holdings (ARM) Shifts Strategy to Sell Own Data Center Chips"
    g = mg.assess([MaterialItem(id=i, text=t, basis_text=t)
                   for i, t in enumerate((a, b, c), 1)])
    if not (g["n_sub"] == 3 and g["n_sub_events"] == 2
            and g["level"] == mg.THIN):
        return False
    if "同一事件" not in g["labels"][2][1] or "同一事件" in g["labels"][1][1]:
        return False
    # 同一家公司的两件不同的事不能被并掉
    if mg.same_event(mg.event_key("AMD Wins $2 Billion Order From Oracle"),
                     mg.event_key("AMD Wins $3 Billion Order From Meta")):
        return False
    # 指纹去源名后缀、且不含正文（正文每轮抓到的都不一样）
    return (mg.event_key(b) == mg.event_key(b + " - Yahoo Finance")
            == mg.event_key(b + "\nbody text here"))


def _b100_commentary_frames():
    """真机连续三轮的两条误判，以及同类句式。

        AMD Enters a Sovereign AI Showcase, Not a Revenue Windfall
        What KLA (KLAC)'s ... Momentum Means For Shareholders

    第一条整篇的论点就是「这笔生意在财务上不重要」；第二条是
    **build96 那个缺陷的原样复发**——评论体标题被正文里的
    "KLA reported…" 顶成实质，只是句式当时不在硬标记表里。
    """
    from cio import material_gate as mg
    empty = [
        "AMD Enters a Sovereign AI Showcase, Not a Revenue Windfall - Yahoo "
        "Finance\nThe European contract is valued at $1.2 billion and AMD "
        "announced systems went live.",
        "What KLA (KLAC)'s AI-Fueled Advanced Packaging Momentum Means For "
        "Shareholders\nKLA reported a fiscal fourth-quarter revenue of $3.2 "
        "billion, up 12% year over year.",
        "Own KLAC Stock? Here Is How To Collect 21% A Year On It",
        "KLA Corporation (KLAC) Positioned to Benefit from Chip Complexity",
        "KLA (KLAC) Stock Looks Fully Valued After Its Huge Run",
        "Why KLA Corporation (KLAC) Stock Is Down Today",
        "KLA Corporation: Buy The 2027+ Double Tailwind (NASDAQ:KLAC)",
        "(KLAC) Movement as an Input in Quant Signal Sets",
    ]
    # **断言的是「无实质」，不是「不等于实质」。**
    # 这几条都是标题自报家门的评论体，落到默认档「背景·相关报道」也算
    # 不触发闸门——所以 `!= SUBSTANTIVE` 检测不出规则被删掉。
    # 而标签不准就等于规则不可审计：她翻报告只会看见"相关报道"，
    # 看不出系统其实认得这是解读体、荐股、还是量化信号推广。
    if any(mg.classify(t)[0] != mg.EMPTY for t in empty):
        return False
    # **不能误伤**：否定短语在句中是真事实；why 接动作动词是在解释真事件。
    return (mg.classify("AMD, not Intel, won the $10 billion Saudi contract")[0]
            == mg.SUBSTANTIVE
            and mg.classify(
                "Why AMD Cut Its Guidance: CFO Explains\nAMD cut its full-year "
                "guidance to $25 billion, the CFO said.")[0] == mg.SUBSTANTIVE)


def _b101_pool_cut_after_prefilter():
    """**池子那一刀原来切在清洗之前，而且从不打印。**

    老代码：先按相关性砍到 40，再判相关性。真机 8/31 的 ARM——

        去重 55 → 池 40（砍掉 15，没人看过）→ 清洗 → 相关 10

    活着进入清洗的 40 条里有 30 条随即被判不相关：**池子的名额四分之三
    花在了马上要扔的东西上**，同时 15 条从没被检查过的直接没了。
    这和 build91 修的是同一个缺陷（按相关性截断 → 才判实质度），
    只是在管道的另一端。

    也是同日两跑 ARM 实质 2 / 实质 1 的直接原因：55 条挤 40 个位置，
    分数一动，一条实质材料就掉出去了，而没有任何地方说过。
    """
    import ast
    import inspect

    from cio import unit_a
    src = inspect.getsource(unit_a.collect_materials)
    tree = ast.parse(src.lstrip())
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_prefilter"]
    if not calls:
        return False
    # 清洗的输入必须是完整列表，不能是切片
    if any(isinstance(c.args[0], ast.Subscript) for c in calls):
        return False
    pool_lines = [n.lineno for n in ast.walk(tree)
                  if isinstance(n, ast.Name) and n.id == "MATERIAL_POOL"]
    if not pool_lines or min(pool_lines) <= max(c.lineno for c in calls):
        return False
    keys: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict) \
                and any(getattr(t, "id", "") == "intake" for t in node.targets):
            keys = {k.value for k in node.value.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    if not {"pool_cut", "pool_limit"} <= keys:
        return False
    base = {"raw": 107, "scored": 55, "pool": 40, "cap": 10, "kept": 10,
            "dropped": 0, "dropped_substantive": 0, "enriched": 6,
            "relevant": 58, "tiers_before_cap": {"实质": 2}, "pool_limit": 40}
    cut = unit_a.intake_note({"intake": dict(base, pool_cut=18)})
    none = unit_a.intake_note({"intake": dict(base, relevant=13, pool_cut=0)})
    return ("池上限 40" in cut and "18 条" in cut
            and "未经实质度判定" in cut and "池上限" not in none)


def _b102_pinned_context_yields_gate_slots():
    """**按定义不可能开门的材料，不该先占名额。**

    真机 8/31 的 AMD：10 个闸门名额里 3 个给了内部人申报（Form 4/4/144），
    同一行还写着「截掉 20 条」——30% 的窗口花在按设计不可能触发闸门的纸上。
    它们相关性分很高，所以稳稳排在前面。

    普通新闻至少**可能**在补完正文后变成实质（build91 那一类），
    持股申报不会。所以钉死的排在同档最后。
    **不是丢弃**——照常显示、照常可引用，改的只是谁先占名额。
    """
    from cio import material_gate as mg
    from cio import unit_a
    url = "https://www.sec.gov/Archives/edgar/data/2488/x.htm"
    body = " On August 27, 2026, the reporting person sold shares." * 8

    class S:
        def __init__(s, n, u):
            s.name, s.url = n, u

    class N:
        def __init__(s, t, sc, src=None):
            s.title_original, s.title_zh, s.body, s.score = t, "", "", sc
            s.sources = [src] if src else []
    sec = S("EDGAR", url)
    items = [N(f"CO 4 (2026-08-2{i})\nSEC filing 4 filed 2026-08-2{i}.{body}",
               95 - k, sec) for k, i in enumerate((7, 6))]
    news = N("AMD in talks with a hyperscaler on capacity", 10)
    ranked, _t, tier_of = unit_a._rank_by_substance(items + [news])
    if ranked[0] is not news:
        return False
    if any(tier_of[id(n)] != mg.CONTEXT for n in items):
        return False        # 档位不该被改，只是排序靠后
    # 钉死的只有持股申报；**8-K 取不到正文不算钉死**（补上正文还可能是实质）
    for form, want in (("4", True), ("144", True), ("SC 13G", True),
                       ("8-K", False), ("10-Q", False)):
        t = (f"CO {form} (2026-08-27)\nSEC filing {form} filed 2026-08-27."
             + " On August 27, 2026, the company entered an agreement." * 8)
        if mg.never_substantive(t, "EDGAR", url) is not want:
            return False
    stub = "CO 8-K (2026-08-27)\nSEC filing 8-K filed 2026-08-27."
    return (not mg.never_substantive(stub, "EDGAR", url)
            and not mg.never_substantive("AMD wins a $2B order", "Zacks", "z"))


def _b102_all_drop_reasons_printed():
    """**四个丢弃原因全要印，不只是符号消歧。**

    build98 只印了符号消歧那一个，另外三个收进 `dropped_by` 就扔了。
    后果立刻就来：真机 ARM 有 18 条丢弃完全没有说明，而那 18 条里
    到底有没有一条真材料被 `is_noise` 当成标题党杀掉，没有任何地方看得见。
    **收了不印和没收是一回事。**
    """
    from cio import unit_a
    base = {"raw": 107, "scored": 55, "pool": 10, "cap": 10, "kept": 10,
            "dropped": 0, "dropped_substantive": 0, "enriched": 6,
            "relevant": 10, "tiers_before_cap": {"实质": 1},
            "pool_limit": 40, "pool_cut": 0}
    s = unit_a.intake_note({"intake": dict(
        base, dropped_symbol=27,
        dropped_by={unit_a.DROP_SYMBOL: 27, unit_a.DROP_NO_SUBJECT: 12,
                    unit_a.DROP_CLICKBAIT: 4, unit_a.DROP_OFFTOPIC: 2})})
    if "符号消歧丢弃 27 条" not in s or "73%" not in s:
        return False
    for r, c in ((unit_a.DROP_NO_SUBJECT, 12), (unit_a.DROP_CLICKBAIT, 4),
                 (unit_a.DROP_OFFTOPIC, 2)):
        if f"{r} {c}" not in s:
            return False
    s2 = unit_a.intake_note({"intake": dict(
        base, relevant=30, dropped_symbol=0,
        dropped_by={unit_a.DROP_CLICKBAIT: 3})})
    if "清洗丢弃" not in s2 or f"{unit_a.DROP_CLICKBAIT} 3" not in s2:
        return False
    # 样本也要留，并且真的印出来（计数判断不了砍对没砍对）
    src = inspect.getsource(unit_a.collect_materials)
    rs = (Path(__file__).resolve().parents[1] / "run_scan.py").read_text("utf-8")
    return "dropped_samples" in src and "dropped_samples" in rs


def _b103_judge_guardrails():
    """**模型接进来之前，护栏先钉死。**

    2026-08-31 扩样测试给了明确证据：规则在调参集上 67/67，
    在留出集（ON / IT，规则从没见过）上 3/8、相关性 13/20。
    换模型这件事值得认真对待——但要先有护栏，再谈接线。

    三条：引文必须能从原文逐字核对；模型不通要**显式**降级；
    降级结果不进缓存（否则一次网络故障会被永久固化成判定）。
    """
    from cio import judge as J
    from cio import material_gate as mg
    real = ("AMD announced it completed the acquisition of ZT Systems "
            "for $4.9 billion on Monday.")

    def fake(reply):
        return lambda _p: reply
    # 形状一致
    r = J.RuleJudge().judge_one(real)
    m = J.LLMJudge(fake('{"tier":"实质","why":"x","span":"completed the '
                        'acquisition","event":"e"}'), name="f").judge_one(real)
    if set(r.__dict__) != set(m.__dict__) or m.tier != mg.SUBSTANTIVE:
        return False
    # 编造的引文 → 降级
    bad = J.LLMJudge(fake('{"tier":"实质","why":"x",'
                          '"span":"AMD signed a $50 billion deal","event":"e"}'),
                     name="f").judge_one(real)
    if bad.tier != mg.CONTEXT or not bad.degraded:
        return False

    # 模型不通 / 乱回 → 显式降级
    def boom(_p):
        raise RuntimeError("down")
    if not J.LLMJudge(boom, name="f").judge_one(real).degraded:
        return False
    for reply in ("我觉得挺重要", "{}", '{"tier":"很重要"}'):
        if not J.LLMJudge(fake(reply), name="f").judge_one(real).degraded:
            return False
    # 提示词不许出现政策词
    text = J._TIER_PROMPT + J._REL_PROMPT
    if any(w in text for w in ("Form 4", "SUFFICIENT", "材料充分", "闸门", "仓位")):
        return False
    # 政策层没被接上模型
    return "judge" not in inspect.getsource(mg).lower().replace("judgement", "")


def _b103_heldout_is_held_out():
    """**留出集不能和调参集重叠，而且规则在它上面不该是满分。**

    调参集里每一条都来自某个 build 的修复现场——规则见过它、
    而且是为它改的。在那上面接近满分是设计出来的结果，不是能力的证据。
    如果哪天规则在留出集上也满分了，多半是有人拿留出集去调参了。
    """
    import _material_corpus as corpus
    from cio import judge as J
    tuned = {t for _b, t, _w, _n in corpus.CASES}
    held = {t for _b, t, _w, _n in corpus.HELDOUT}
    if not held or (tuned & held):
        return False
    rel = [w for _s, _c, _t, w, _n in corpus.RELEVANCE_CASES]
    if rel.count(True) < 5 or rel.count(False) < 5:
        return False
    jd = J.RuleJudge()
    h_bad = [1 for _b, t, w, _n in corpus.HELDOUT if jd.judge_one(t).tier != w]
    r_bad = [1 for s, c, t, w, _n in corpus.RELEVANCE_CASES
             if bool(jd.judge_relevance(t, s, c)) != bool(w)]
    return bool(h_bad) and bool(r_bad)


def _b104_eval_reports_degradation():
    """**评测必须先报降级率，再报分数。**

    `judge.py` 调用失败会回落到规则（那是设计好的护栏）。于是 API key 错的时候，
    95 条全部降级，三个分数**恰好等于规则基线**——"模型没被调用过"和
    "模型和规则一样好"在输出上长得一模一样。

    这正是本项目一整天在抓的那类缺陷，而它出现在了测量工具本身里。
    """
    import ast
    src = (Path(__file__).resolve().parent / "eval_judge.py").read_text("utf-8")
    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "_score_tier"), None)
    cls = next((n for n in ast.walk(tree)
                if isinstance(n, ast.ClassDef) and n.name == "Score"), None)
    if fn is None or cls is None:
        return False
    # 降级条数必须是**具名字段**，不能混进分数里。
    # （断字段名而不是元组长度：build106 加了 policy / vetoed 两栏统计，
    #   数元素个数会让这条探针因为一件无关的事变红。）
    fields = {t.target.id for t in cls.body if isinstance(t, ast.AnnAssign)}
    if not {"ok", "n", "degraded"} <= fields:
        return False
    rets = [n for n in ast.walk(fn) if isinstance(n, ast.Return)]
    if not rets or not all(isinstance(r.value, ast.Call)
                           and getattr(r.value.func, "id", "") == "Score"
                           for r in rets):
        return False
    # 顺序：降级率必须印在三个分数之前
    if not 0 <= src.index("  降级 ") < src.index("调参集（规则为它改过"):
        return False
    return "降级" in src and "--smoke" in src


def _b105_judge_loads_dotenv():
    """**`judge` 必须自己导入 `config`，不能依赖调用方的导入顺序。**

    `claude_chat()` 从 `CIO_ANTHROPIC_API_KEY` 取密钥，而把 `.env` 读进环境变量的
    是 `cio.config` 的导入副作用。真机踩到：`eval_judge.py --smoke` 那条分支插在
    `from cio.config import MEMORY_DIR` **之前**，于是走 smoke 时 `.env` 从没被读过
    ——一个配置完全正确的 key，报的是"没有 API key"。

    依赖调用方的导入顺序来决定密钥读不读得到，是隐式依赖。
    """
    import ast
    import sys as _sys
    from cio import judge as J
    tree = ast.parse(inspect.getsource(J))
    names = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            names.add(n.module or "")
            names |= {a.name for a in n.names}
    return "config" in names and "cio.config" in _sys.modules


def _b106_policy_beats_the_model():
    """**来源与表单是政策，模型连问都不该被问到。**

    `judge.py` 开头整整一节写着「语言理解 与 政策，必须切开」，
    而 `LLMJudge.judge_one` 的第一版直接把文本丢给了模型——
    `is_primary` / `OWNERSHIP_FORMS` / `PRIMARY_MIN_CHARS` 三条一条都没走。
    **文档里承诺的边界，代码里没有。**

    2026-09-01 首次真机评测抓到了它：语料里的 `SC 13G body=True`
    期望「背景」（持股申报不触发闸门，那是 CEO 定的规则），Claude 判「实质」。
    模型没有错——它读到的是一份真实、有正文、依法必须披露的文件；
    错的是这个问题根本不该由它回答。

    用**被调用就抛异常**的后端测：结果仍然正确，就证明它没被调用。
    """
    import sys as _s
    _s.path.insert(0, str(Path(__file__).resolve().parent))
    import _material_corpus as corpus
    from cio import judge as J
    from cio import material_gate as mg

    def boom(_p):
        raise AssertionError("政策条目不该问模型")
    for form, want in (("SC 13G", mg.CONTEXT), ("4", mg.CONTEXT),
                       ("144", mg.CONTEXT), ("8-K", mg.SUBSTANTIVE)):
        text = corpus.filing_text(form, True)
        for jd in (J.LLMJudge(boom, name="f"),
                   J.HybridJudge(J.LLMJudge(boom, name="f"), name="h")):
            v = jd.judge_one(text, "EDGAR", _SEC_URL_B106)
            if v.tier != want or not v.policy or v.degraded:
                return False
    # 正文没取到的公告仍然降为背景（PRIMARY_MIN_CHARS 那条也要在政策里）
    v = J.LLMJudge(boom, name="f").judge_one(
        corpus.filing_text("8-K", False), "EDGAR", _SEC_URL_B106)
    return v.tier == mg.CONTEXT and v.policy


_SEC_URL_B106 = "https://www.sec.gov/Archives/edgar/data/2488/x.htm"


def _b106_hybrid_veto_only_pushes_down():
    """**混合判定：否决只准往下压，不准往上抬。**

    2026-09-01 首次真机评测（Claude Haiku 4.5，降级 0/75）：
    调参集 规则 67/67 · Claude 51/67；留出集 3/8 · 7/8；相关性 13/20 · 19/20。
    16 条分歧里 13 条是模型更严、3 条更松，三条更松的全是"评论体标题 +
    事实性正文"——规则花了四个 build 收干净的那一类。

    直觉上该取"谁说实质就算实质"，把那 13 条捞回来。**不能。**
    那 13 条里规则的正确，绝大部分是它在**自己的训练数据**上的正确；
    并进来等于把留出集刚量出的过拟合装回系统。

    所以：档位以模型为准，规则只有一票否决，且三个条件缺一不可——
    模型判实质、标题命中硬标记、**规则自己也不判实质**。
    第三条让分句救援继续有效。
    """
    import sys as _s
    _s.path.insert(0, str(Path(__file__).resolve().parent))
    import _material_corpus as corpus
    from cio import judge as J
    from cio import material_gate as mg

    def echo(text):
        span = (text or "").strip().split("\n", 1)[0][:40]
        return lambda _p: ('{"tier":"实质","why":"x","span":"%s","event":"e"}' % span)

    def hy(text):
        return J.HybridJudge(J.LLMJudge(echo(text), name="m"), name="h")
    # 一、评论体标题 + 事实性正文 → 被否决
    down = ("What KLA (KLAC)'s AI-Fueled Advanced Packaging Momentum Means For "
            "Shareholders\nKLA reported a fiscal fourth-quarter revenue of "
            "$3.2 billion, up 12% year over year.")
    v = hy(down).judge_one(down)
    if v.tier != mg.EMPTY or not v.vetoed:
        return False
    # 二、分句救援救出来的，规则自己就判实质 → 不否决
    keep = ("Arm Holdings Has $2 Billion in Orders It Cannot Fill Yet "
            "— Is ARM Stock a Buy at $257?")
    if not mg.hard_marker(keep) or mg.classify(keep)[0] != mg.SUBSTANTIVE:
        return False
    vk = hy(keep).judge_one(keep)
    if vk.tier != mg.SUBSTANTIVE or vk.vetoed:
        return False
    # 二之二、**软标记不参与否决。** "价格动了"经常和真实原因写在同一个标题里，
    #        build96 花了一轮把软硬分开，否决权不能把它又合回去。
    soft = "Micron stock jumped on a supply deal with a hyperscaler"
    if mg.hard_marker(soft) or mg.classify(soft)[0] == mg.SUBSTANTIVE:
        return False                          # 前提不成立，这一步就是空的
    vs = hy(soft).judge_one(soft)
    if vs.tier != mg.SUBSTANTIVE or vs.vetoed:
        return False
    # 三、模型判背景、规则判实质 → **保持背景**，不许被抬上去
    up = "商务部宣布对英伟达 H20 实施出口管制"
    quiet = J.HybridJudge(J.LLMJudge(
        lambda _p: '{"tier":"背景","why":"x","span":"","event":"e"}', name="m"),
        name="h")
    w = quiet.judge_one(up)
    if w.tier != mg.CONTEXT or w.vetoed:
        return False
    # 四、否决在现有语料上一条都不误伤
    for _t, text, want, _n in list(corpus.CASES) + list(corpus.HELDOUT):
        head = text.split("\n", 1)[0]
        if (mg.hard_marker(head) and mg.classify(text)[0] != mg.SUBSTANTIVE
                and want == mg.SUBSTANTIVE):
            return False
    # 五、缓存与 flush：混合和纯模型共用一份缓存，flush 要穿过包装
    if J.cache_stem("hybrid:claude:x") != J.cache_stem("claude:x"):
        return False
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "c.json"
        real = "AMD completed the acquisition of ZT Systems for $4.9 billion."
        j2 = J.HybridJudge(J.LLMJudge(echo(real), name="m", cache_path=p), name="h")
        if j2.judge_one(real).degraded:      # 引文对得上，不该降级
            return False
        j2.flush()
        if not p.exists():                   # 降级的不进缓存，没降级的必须落盘
            return False
    for bad in ("hybrid:rules", "hybrid:"):
        try:
            J.build(bad)
            return False
        except ValueError:
            pass
    return True


def _b107_technical_observer_v1():
    """**技术观察员 v1：只描述、不判断、无未来函数。**

    四条冻结的边界，缺一即红：
      · 卡片上不出现方向判断/操作建议/不可观测主体
      · `observe` 是纯函数，`observe(df[:t])` 逐字段等于 `observe(df, as_of=t)`
      · 价区参数（pivot 5 / 0.5×ATR20 / 间隔 5 / 最少 2 触点）与版本号绑定
      · 算不出来是 null 且必须有原因，**不是 0**
    """
    import math
    import pandas as pd
    from cio.technical import observer as tob
    from cio.technical import price_structure as tps

    def _panel(n):
        bars = []
        for i in range(n):
            c = 100 + 0.05 * i + 8 * math.sin(2 * math.pi * i / 24)
            bars.append((c, c + 1, c - 1, c, 1_000_000.0 * (1 + 0.4 * math.sin(i / 3))))
        return pd.DataFrame({
            "date": pd.bdate_range(start="2024-01-01", periods=n),
            "open": [b[0] for b in bars], "high": [b[1] for b in bars],
            "low": [b[2] for b in bars], "close": [b[3] for b in bars],
            "volume": [b[4] for b in bars]})

    df = _panel(300)
    # 参数与版本绑定
    if tps.params_fingerprint() != tps.FROZEN_FINGERPRINT:
        return False
    if (tps.PIVOT_WINDOW, tps.CLUSTER_ATR_MULT, tps.MIN_TOUCH_GAP,
            tps.MIN_TOUCHES) != (5, 0.5, 5, 2):
        return False
    # 无未来函数：截断 == as_of
    t = 220
    a = tob.observe(df.iloc[:t + 1].reset_index(drop=True), symbol="X").to_dict()
    b = tob.observe(df, as_of=str(df["date"].iloc[t])[:10], symbol="X").to_dict()
    a.pop("as_of"), b.pop("as_of")
    if a != b:
        return False
    # 最近 5 根上的极值还不算 pivot
    ph, pl = tps.swings(df)
    idx = [i for i, _p in ph] + [i for i, _p in pl]
    if idx and max(idx) > len(df) - tps.PIVOT_WINDOW - 1:
        return False
    # null ≠ 0，且每个 null 有原因
    short = tob.observe(_panel(40), symbol="X")
    if short.volatility["atr_percentile_252"] is not None:
        return False
    if tob._check_nulls(short):
        return False
    # 卡片上没有方向判断词与不可观测主体
    import json
    blob = json.dumps(tob.observe(df, symbol="X").to_dict(), ensure_ascii=False)
    blob += "\n".join(tob.describe(tob.observe(df, symbol="X")))
    for w in ("看涨", "看跌", "买入", "卖出", "超买", "超卖", "强势", "弱势",
              "建议", "机构", "institutional", "主力"):
        if w in blob:
            return False
    return "accumulation_pressure_proxy" in blob


def _b107_premarket_fires_in_market_time():
    """**盘前简报按市场时区发车，不按机器时区。**

    2026-09-02 真机：简报在**纽约 09-01 19:49** 送达 —— 收盘四小时之后。
    简报本身完全正确（英文抬头、ET 戳、美股期货、当天真实新闻），
    错的只有发车时间：cron 那行 `0 7 * * 1-5` 是机器本地时间 7 点，
    而机器在北京时区，北京 07:00 = 纽约前一天 19:00。

    cron / launchd 都跟不了另一个国家的夏令时，所以判断挪进 Python。
    """
    import ast
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from cio import schedule as sch

    # **这条探针不能依赖 CIO_MARKET。** check_build 常常在默认 cn 下跑，
    # 而这个缺陷是 us 的 —— 探针跟着环境变量走，就会在最该红的那台机器上变绿。
    bj, ny = ZoneInfo("Asia/Shanghai"), ZoneInfo("America/New_York")
    if sch.PREMARKET_WINDOW.get("us") == sch.PREMARKET_WINDOW.get("cn"):
        return False                  # 两个市场共用一个窗口 = 没有按市场分
    for when in (datetime(2026, 9, 2, 7, 49, tzinfo=bj),      # 那次故障的时刻
                 datetime(2026, 9, 2, 7, 30, tzinfo=ny),
                 datetime(2026, 9, 5, 7, 30, tzinfo=ny)):
        ok, why = sch.is_premarket(when)
        if not isinstance(ok, bool) or not why:
            return False              # 无论真假都必须给出一句说明
    if sch.MARKET == "us":
        if sch.is_premarket(datetime(2026, 9, 2, 7, 49, tzinfo=bj))[0]:
            return False
        if not sch.is_premarket(datetime(2026, 9, 2, 7, 30, tzinfo=ny))[0]:
            return False
        if sch.is_premarket(datetime(2026, 9, 5, 7, 30, tzinfo=ny))[0]:
            return False              # 周末
    # 夏令时会挪动本地小时数 —— 手抄进 crontab 的数字一年错两次。
    # 直接用 zoneinfo 算，不走 local_window（它跟着 MARKET 走）。
    lo_us = sch.PREMARKET_WINDOW["us"][0]
    def _bj_hour(month):
        d = datetime(2026, month, 2, lo_us.hour, lo_us.minute, tzinfo=ny)
        return d.astimezone(bj).strftime("%H:%M")
    if _bj_hour(9) == _bj_hour(12):
        return False
    # 闸门必须跑在取数之前
    src = (Path(__file__).resolve().parents[1] / "run_premarket.py").read_text("utf-8")
    fn = next((n for n in ast.walk(ast.parse(src))
               if isinstance(n, ast.FunctionDef) and n.name == "main"), None)
    if fn is None:
        return False
    gate = work = None
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            nm = getattr(n.func, "attr", getattr(n.func, "id", ""))
            if nm == "is_premarket" and gate is None:
                gate = n.lineno
            if nm in ("collect_premarket", "init_db", "collect_funds") and work is None:
                work = n.lineno
    if gate is None or work is None or gate >= work:
        return False
    # 人手动要简报必须绕过闸门
    csrc = (Path(__file__).resolve().parents[1] / "run_command.py").read_text("utf-8")
    calls = [n for n in ast.walk(ast.parse(csrc))
             if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "premarket_main"]
    return bool(calls) and any(
        any(k.arg == "force" and getattr(k.value, "value", None) is True for k in c.keywords)
        for c in calls)


def _b108_us_mode_drops_the_cn_bucket():
    """**美股盘前不收中国桶，而且过滤掉了什么要印出来。**

    2026-09-02 那份美股简报的 Watch Today 里混进了「浙江宁波…」与几条
    A 股外资流入，共同社和财新还各失败一次——`sources()` 把整份 yaml
    原样返回，六个中国源和三条中国关键词在美股模式下照常抓。

    不是崩溃，是**稀释**：十条 Watch Today 占掉两条，
    当天真正该看的美股条目就少两条。
    """
    import ast
    import importlib
    from cio import config as cfgmod
    old = cfgmod.MARKET
    try:
        for mkt, must_in, must_out in (
                ("us", "BBC World", "Caixin Global"),
                ("cn", "Caixin Global", None)):
            cfgmod.MARKET = mkt
            cfgmod.sources.cache_clear()
            c = cfgmod.sources()
            names = [f.get("name") for f in c["rss"]]
            if must_in not in names:
                return False
            if must_out and must_out in names:
                return False
            other = "us" if mkt == "cn" else "cn"
            if any(f.get("bucket") == other for f in c["rss"]):
                return False
            if any(q.get("bucket") == other
                   for q in c["google_news"]["standing_queries"]):
                return False
            # 关掉一个桶之后关键词不能整段变空（静默失效的采集通道）
            if not c["google_news"]["standing_queries"]:
                return False
            if not (c.get("_bucket_filter") or {}).get("kept"):
                return False
    finally:
        cfgmod.MARKET = old
        cfgmod.sources.cache_clear()
        importlib.reload(cfgmod)
    # 过滤结果必须被抄进采集状态；专题研究必须取全量桶
    src = (Path(__file__).resolve().parents[1] / "src" / "cio" / "collect.py").read_text("utf-8")
    tree = ast.parse(src)
    pre = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                and n.name == "collect_premarket"), None)
    scan = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                 and n.name == "scan_rss_for_subject"), None)
    if pre is None or scan is None:
        return False
    seg = ast.get_source_segment(src, pre) or ""
    if "_bucket_filter" not in seg or "源过滤" not in seg:
        return False
    calls = [n for n in ast.walk(scan) if isinstance(n, ast.Call)
             and getattr(n.func, "id", "") == "sources"]
    return bool(calls) and any(
        k.arg == "all_buckets" and getattr(k.value, "value", None) is True
        for c in calls for k in c.keywords)


def _c_is_linked_not_copied() -> bool:
    """`C_MAX_ATR_TO_ZONE` 必须**引用** `CLUSTER_ATR_MULT`，不能是抄过去的字面量。

    断值不够：抄一个 `0.5` 过去，`== CLUSTER_ATR_MULT` 照样成立，指纹也照样对得上。
    等到哪天价区容差改成 0.4，两个数就悄悄分家了——setup 的 C 还在按 0.5 判，
    而它声称自己"等于聚类容差、不引入新自由度"。

    **这是本项目第九次踩「断言值/文本而不是断言结构」。** 所以走 AST：
    看这行赋值的右边是不是那个名字。
    """
    import ast
    src = (Path(__file__).resolve().parents[1] / "src" / "cio" / "technical"
           / "setups.py").read_text("utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", "") == "C_MAX_ATR_TO_ZONE" for t in node.targets):
            return isinstance(node.value, ast.Name) and node.value.id == "CLUSTER_ATR_MULT"
    return False


def _b109_setup_and_event_are_frozen():
    """**setup 阈值与事件定义在看任何收益之前冻结。**

    先跑收益、再回来调阈值，这个项目吃过两次亏：证券二部的因子搜索空间
    越放越大、样本外全废；材料闸门在调参集 67/67、留出集 3/8。

    还有一条：**一次事件不是一个 stock-day。** 连着三天成立是一个事件，
    不切事件的话回放会造出大量高度重叠的样本——这正是 build100 在材料闸门上
    修过的同一个缺陷（一份新闻稿被转载三次就顶开闸门），换了个模块又出现。
    """
    from cio.technical import price_structure as tps
    from cio.technical import setups as st
    if st.params_fingerprint() != st.FROZEN_FINGERPRINT:
        return False
    if (st.A_MIN_SPIKE_DAYS, st.B_MIN_CMF, st.B_MIN_OBV_SLOPE) != (5, 0.10, 0.0):
        return False
    # C 必须**引用**价区聚类容差，不能是抄过去的字面量（断结构，不断值）
    if st.C_MAX_ATR_TO_ZONE != tps.CLUSTER_ATR_MULT or not _c_is_linked_not_copied():
        return False
    # 算不出来 ≠ 不成立
    short = type("C", (), {"volume": {}, "price_structure": {}})()
    r = st.evaluate(short)
    if r["hit"] is not False or not r["unknown"]:
        return False
    text = "\n".join(st.describe())
    if not all(w in text for w in ("基础率", "不是新参数", "之前定下")):
        return False
    d = [f"2026-01-{i:02d}" for i in range(1, 31)]
    if len(st.derive_events("X", list(zip(d, [False, True, True, True] + [False] * 26)))) != 1:
        return False
    one = st.derive_events("X", list(zip(d, [False, True, False, False, True] + [False] * 25)))
    if len(one) != 1 or not one[0].merged_repeats:
        return False
    two = st.derive_events("X", list(zip(d, [False, True] + [False] * 8 + [True] + [False] * 20)))
    if len(two) != 2:
        return False
    # 存储层：写过的一天不静默重写
    import tempfile
    from cio.technical import observer as tob
    from cio.technical import store as sto
    import math
    import pandas as pd
    bars = [(100 + 8 * math.sin(i / 4), 101 + 8 * math.sin(i / 4),
             99 + 8 * math.sin(i / 4), 100 + 8 * math.sin(i / 4), 1e6)
            for i in range(300)]
    df = pd.DataFrame({"date": pd.bdate_range(start="2024-01-01", periods=300),
                       "open": [b[0] for b in bars], "high": [b[1] for b in bars],
                       "low": [b[2] for b in bars], "close": [b[3] for b in bars],
                       "volume": [b[4] for b in bars]})
    cards = [tob.observe(df, symbol="X")]
    with tempfile.TemporaryDirectory() as tmp:
        old = sto.CARD_DIR
        try:
            sto.CARD_DIR = Path(tmp) / "cards"
            if sto.write_day("2026-01-05", cards)[0] != 1:
                return False
            if sto.write_day("2026-01-05", cards)[0] != 0:
                return False          # 第二次必须拒绝
            if sto.write_day("2026-01-05", cards, force=True)[0] != 1:
                return False          # 显式 force 必须能覆盖
            rows = sto.load_day("2026-01-05")
            if not rows or not rows[0].get("stamps", {}).get("setup_version"):
                return False
        finally:
            sto.CARD_DIR = old
    # 度量模块必须仍然是纯函数（存储层是唯一例外）
    src = (Path(__file__).resolve().parents[1] / "src" / "cio" / "technical"
           / "observer.py").read_text("utf-8")
    return "open(" not in src


def _b110_lineage_pit_and_review():
    """**build110：正式前向采集的三个前提。**

    一、**setup 的身份不只是 setup_version。** 条件 C 依赖价区算法 `sr-1.0.0`；
        `sr-1.0.0 → sr-1.1.0` 之后，三个阈值一个没改，这条 setup 筛的
        已经是另一批东西。事件必须带完整血统，事件研究必须按血统分组。
    二、**PIT 按区间判。** 写死 False 会让人以为永远做不了；写死 True 是撒谎。
        快照覆盖到的那几天是 PIT 的，之前的不是。
    三、**筛子的主 KPI 要有地方记。** "推出来的值不值得研究"今天就能测，
        但前提是有人把判断写下来——否则只能靠印象。
    """
    import tempfile
    from cio import quant_data as q
    from cio.technical import price_structure as tps
    from cio.technical import review as rv
    from cio.technical import setups as st

    lin = st.current_lineage()
    if len(lin) != 4 or lin[2] != tps.ALGO_VERSION or lin[1] != st.params_fingerprint():
        return False
    d = [f"2026-01-{i:02d}" for i in range(1, 21)]
    old = ("setup-1.0.0", "fp", "sr-1.0.0", "signal-card-1.0.0")
    new = ("setup-1.0.0", "fp", "sr-1.1.0", "signal-card-1.0.0")
    evs = st.derive_events("X", [(d[0], True, old), (d[1], True, old),
                                 (d[2], True, new), (d[3], True, new)])
    if len(evs) != 2 or not evs[0].ended_by_version_change:
        return False
    if evs[0].lineage[2] != "sr-1.0.0" or evs[1].lineage[2] != "sr-1.1.0":
        return False

    # PIT 按区间判
    lo, hi, n = q.snapshot_coverage()
    if q.universe_pit_for("2000-01-01", "2030-01-01")[0] is not False:
        return False
    if n and q.universe_pit_for(lo, hi)[0] is not True:
        return False

    # 复核台账：三档、非法值报错、改主意留痕
    with tempfile.TemporaryDirectory() as tmp:
        oldp = rv.REVIEW_PATH
        try:
            rv.REVIEW_PATH = Path(tmp) / "r.jsonl"
            if set(rv.VERDICTS) != {"worth", "skip", "unclear"}:
                return False
            rv.mark("2026-09-01", "A", "worth", "x")
            rv.mark("2026-09-01", "A", "skip", "改主意")
            if rv.latest()[("2026-09-01", "A")]["verdict"] != "skip":
                return False
            if not rv.revisions():
                return False          # 改过必须看得见
            if rv.stats()["all_records"][rv.SETUP_VERSION]["worth"] != 1:
                return False          # 原始记录不能被抹
            try:
                rv.mark("2026-09-02", "B", "会涨")
                return False          # 非法判定必须报错
            except ValueError:
                pass
        finally:
            rv.REVIEW_PATH = oldp
    # 卡片上要盖 setup_fingerprint，否则血统缺一角
    src = (Path(__file__).resolve().parents[1] / "src" / "cio" / "technical"
           / "store.py").read_text("utf-8")
    if "setup_fingerprint" not in src:
        return False
    # **血统必须从卡片里读，不能用当前代码的。** 半年前的卡片是按当时的算法算的，
    # 用今天的版本号给它盖章 = 把历史改写成"一直都是这套定义"。
    import json as _json
    from cio.technical import store as sto
    with tempfile.TemporaryDirectory() as tmp:
        oldd = sto.CARD_DIR
        try:
            sto.CARD_DIR = Path(tmp) / "cards"
            sto.CARD_DIR.mkdir(parents=True)
            (sto.CARD_DIR / "2026-01-05.jsonl").write_text(_json.dumps({
                "symbol": "X", "as_of": "2026-01-05", "setup": {"hit": True},
                "stamps": {"schema_version": "signal-card-0.9.0",
                           "algo_version": "sr-0.9.0",
                           "setup_version": "setup-0.9.0",
                           "setup_fingerprint": "old"}},
                ensure_ascii=False) + "\n", encoding="utf-8")
            ser = sto.hit_series("X")
            if not ser or ser[0][2][2] != "sr-0.9.0" or ser[0][2][0] != "setup-0.9.0":
                return False
        finally:
            sto.CARD_DIR = oldd
    return True


CHECKS = [
    ("build61", "共享计算层 measures 零内部依赖", _b61_measures),
    ("build61", "失效复检用中文字符二元组", _b61_thesis_bigram),
    ("build62", "[面板] 被认作有效溯源", _b62_panel_cite),
    ("build62", "PDF 与 MD 同构（七节新版式）", _b62_pdf_isomorphic),
    ("build63", "材料实质度闸门存在且判得动", _b63_material_gate),
    ("build63", "全前瞻材料 → 横幅 + 提示词约束", _b63_gate_banner),
    ("build63", "约束注入 R1/R2/综合 三处提示词", _b63_constraint_in_prompts),
    ("build63", "MD 与 PDF 都显示材料横幅", _b63_render_gate),
    ("build63", "论点台账记录材料判定", _b63_ledger_columns),
    ("build63", "伪造引述被标 ⚠引述失实", _b63_quote_check),
    ("build64", "markdown 小节标题可解析", _b64_markdown_headers),
    ("build64", "旧【】版式仍可解析（无回归）", _b64_legacy_headers),
    ("build64", "解析失败与模型没写分开报", _b64_parse_warning),
    ("build64", "表头行不算论断", _b64_table_head),
    ("build64", "只讲股价的失效条件被标出", _b64_market_only),
    ("build65", "凭空年份被标出、真实年份不误标", _b65_year_check),
    ("build65", "结论=行不算论据", _b65_verdict_line),
    ("build66", "Evidence Gate 三档判定正确", _b66_gate_tiers),
    ("build66", "未启动路径零 LLM、不建新论点、仍复检", _b66_not_activated_path),
    ("build66", "MD 与 PDF 都按 activated 分流", _b66_render_split),
    ("build66", "--force / UNIT_A_FORCE_RESEARCH 可用", _b66_force),
    ("build66", "无失效条件的论点不进 OPEN", _b66_no_conditions_status),
    ("build67", "一个标的只保留一个 active thesis", _b67_supersede),
    ("build67", "历史无条件论点一次性回填", _b67_backfill),
    ("build68", "六种小节标题版式（含 **【】** 组合）", _b68_bold_wrapped_header),
    ("build68", "重复项目符号 '- - ' 被剥净", _b68_double_bullet),
    ("build69", "加粗转述可识别、伪造转述仍被抓", _b69_restate),
    ("build69", "引用无实质材料被单独标注", _b69_weak_cite),
    ("build70", "无序号加粗小标题不算论据", _b70_bold_label),
    ("build71", "长栏目名的表头行仍算结构", _b71_table_head_len),
    ("build72", "方向漂移复检在登记之前、两个渲染器都显示", _b72_drift),
    ("build72", "漂移按证据分三档，不一律报警", _b72_drift_grading),
    ("build73", "无同业基准的比较被标出", _b73_peer_claim),
    ("build73", "INSUFFICIENT 信心上限为「中」", _b73_conviction_cap),
    ("build74", "催化剂/失效条件同样过同业与口径闸", _b74_lint_items),
    ("build75", "估值口径方向错误被抓出", _b75_sign_error),
    ("build76", "「高估值」不再被误判为「高估」", _b76_no_false_positive),
    ("build77", "波动率 40.74% 不再被 1.50 的否决线误杀", _b77_unit_gate),
    ("build77", "measures 百分数 → CRO 小数的换算点存在", _b77_as_ratio),
    ("build77", "被否决的标的也进 lineage", _b77_veto_recorded),
    ("build77", "同票多账户与台账重复被分开", _b77_ledger_dupes),
    ("build78", "「闸门没跑过」不再折成「闸门判了没材料」", _b78_unrecorded_gate),
    ("build78", "影子账户默认不进风险聚合", _b78_shadow_excluded),
    ("build79", "退役 CRO 默认不再推送 Telegram", _b79_legacy_guard),
    ("build79", "一部不再对外发「目标仓位」", _b79_unit_a_no_position),
    ("build79", "PC 的 Telegram 摘要可用", _b79_pc_telegram),
    ("build80", "采集层可脱离辩论单独调用", _b80_collect_extracted),
    ("build80", "run_scan.py 用同一个闸门判定该不该跑一部", _b80_scan),
    ("build81", "死掉的信息源本次运行跳过，且被报出来", _b81_dead_feed),
    ("build82", "一部结果结构化落盘（界面不必解析 Markdown）", _b82_advice_json),
    ("build82", "阶段事件可被界面捕获", _b82_stage_events),
    ("build82", "--json 输出存在，且不跳过 lineage 落库", _b82_json_flags),
    ("build85", "新鲜度按实测年龄归档，不按品种假设", _b85_freshness_measured),
    ("build85", "取不到的行也保留（不静默省略）", _b85_snapshot_keeps_failures),
    ("build85", "三个渲染器都有市场快照与四分卡图例", _b85_three_renderers_agree),
    ("build83", "run_scan --json 整段 stdout 可一次解析", _b83_scan_json_contract),
    ("build83", "run_pc --json 整段 stdout 可一次解析", _b83_pc_json_contract),
    ("build83", "同一 run_id 重试不产生第二次决策", _b83_ledger_idempotent),
    ("build83", "先序列化后落库最后输出", _b83_pc_serialize_before_record),
    ("build83", "界面层不重新解释闸门规则", _b83_ui_owns_no_rules),
    ("build87", "无目标 ≠ 目标为 0（不会把「没判断」执行成清仓）", _b87_no_target_is_not_zero),
    ("build87", "持有但本轮未复审 → 维持不动", _b87_held_not_evaluated_holds),
    ("build87", "取不到价 ≠ 清仓指令", _b87_unpriced_is_not_liquidation),
    ("build87", "不交易门槛，且清仓不受门槛约束", _b87_band_and_exit_exception),
    ("build87", "权重转股数不因浮点少买一股", _b87_floor_eps_no_lost_share),
    ("build87", "有未评估项时合规绝不报 PASS", _b87_compliance_never_pass_when_unknown),
    ("build87", "成交价基准 T+1_OPEN，批准带有效期", _b87_execution_basis_and_expiry),
    ("build87", "审批状态机拒绝非法跃迁，且提案幂等", _b87_state_machine_rejects_illegal),
    ("build87", "美股账本口径（1 股 / USD / 含息基准 / 拒 A 股代码）", _b87_us_book_conventions),
    ("build87", "持仓缺价时 NAV 不可计算（不按剩下的算）", _b87_nav_unknown_when_unpriced),
    ("build87", "账本用未复权价，与测量口径刻意不同", _b87_book_price_basis_differs_from_measurement),
    ("build87", "提案从已落库的那次决策派生，不重跑 PC", _b87_proposal_derives_from_recorded_run),
    ("build88", "旧库缺列时迁移能跑到（建表→补列→建索引）", _b88_old_schema_migrates),
    ("build88", "db.ensure_columns 存在", _b88_ensure_columns_exists),
    ("build88", "只有已批准的才能成交", _b88_only_approved_executes),
    ("build88", "下一个交易日没到 → 等，不硬成交", _b88_waits_for_session),
    ("build88", "执行幂等，且现金不足不部分成交", _b88_execution_idempotent_and_no_partial),
    ("build88", "平仓留行、置 open=0", _b88_exit_keeps_row),
    ("build88", "同场卖出回款不能当天用来买（T+1 交收）", _b88_no_same_session_proceeds),
    ("build88", "合规破限默认批不了（命令行与 Telegram）", _b88_approval_blocked_on_breach),
    ("build88", "tgbot.send 认 DRYRUN 且按真实结果返回", _b88_tgbot_send_honours_dryrun),
    ("build88", "Telegram 用独立 token 且只听一个 chat", _b88_tgbot_separate_token_and_allowlist),
    ("build89", "4:1 拆股不产生 −75% 的幻觉亏损", _b89_split_no_phantom_loss),
    ("build89", "取不到除权日价 → 拒绝应用拆股", _b89_split_without_price_refused),
    ("build89", "分红入现金且幂等", _b89_dividend_cash_and_idempotent),
    ("build89", "缺价 → NAV 记 NULL、complete=0", _b89_nav_null_when_unpriced),
    ("build89", "前一日不完整 → 当日盈亏不计算", _b89_day_pnl_none_after_incomplete_day),
    ("build89", "回填更早的日子后重算当日盈亏；早于开账日拒绝", _b89_backfill_recomputes_pnl),
    ("build89", "对账三条恒等式（缺价是不适用，不是通过）", _b89_recon_three_identities),
    ("build89", "没有基准 → 超额不计算，且报平均仓位", _b89_no_excess_without_total_return_bench),
    ("build89", "mark() 每条路径返回同一组键（不缺键）", _b89_mark_shape_is_stable),
    ("build89", "开账日不能在未来；空账本可改正、有记录的不许改", _b89_open_date_cannot_be_future),
    ("build89", "盈亏表三个渲染器内容一致", _b89_book_renderers_agree),
    ("build89", "公司行为排在盯市之前", _b89_actions_run_before_marking),
    ("build90", "实质度在截断之前判（实质材料不会被相关性挤掉）",
     _b90_substance_judged_before_truncation),
    ("build90", "排序用闸门自己的分类器，不是另写一套", _b90_ranking_uses_the_gate_classifier),
    ("build90", "进料口径可见（采集 → 进闸门 → 截掉多少）", _b90_intake_is_reported),
    ("build90", "run_scan 把进料口径印出来且行形状一致", _b90_scan_surfaces_intake),
    ("build90", "所有测试脚本都装了断网闸（两层）", _b90_tests_block_the_network),
    ("build91", "闸门判源头文本，不判模型摘要", _b91_gate_reads_source_text_not_model_summary),
    ("build91", "排序与闸门不可能给出不同判定", _b91_ranker_and_gate_cannot_disagree),
    ("build91", "补正文在截断之前，名额大于最终条数", _b91_body_is_enriched_before_the_cut),
    ("build92", "一部接入 SEC EDGAR 一手披露", _b92_unit_a_fetches_edgar),
    ("build92", "采集器的占位串不得被当成外部证据", _b92_placeholder_cannot_fake_evidence),
    ("build92", "普通新闻源不因来源被升降级（老原则未变）", _b92_news_sources_unchanged),
    ("build92", "抓公告正文带 SEC User-Agent", _b92_edgar_body_uses_sec_user_agent),
    ("build93", "按 CIK 取回的公告不过相关性闸", _b93_filings_bypass_relevance_filter),
    ("build93", "公告的存活数分开报（全丢要出 ⚠）", _b93_filing_survival_is_reported),
    ("build94", "一部只收窗口内的公告（否则闸门被拆）", _b94_edgar_recency_window),
    ("build95", "短 ticker 用词边界匹配（arms/pharma/alarm 不算 ARM）", _b95_alias_word_boundary),
    ("build95", "Form 4 / 144 不触发闸门（那是某人卖股票）",
     _b95_ownership_forms_do_not_trigger_the_gate),
    ("build96", "标题自报是评论文时，正文顶不上来", _b96_title_veto),
    ("build96", "股价动了不参与否决（原因可能是真的）", _b96_price_move_is_soft),
    ("build96", "数字之间的 vs 是业绩事实，不是对比文",
     _b96_vs_needs_words_on_both_sides),
    ("build96", "**闸门回归语料整份通过**", _b96_regression_corpus),
    ("build97", "**标题现在时算已发生动作**", _b97_headline_present_tense),
    ("build97", "**与英文词撞车的 ticker 要身份形态**", _b97_dictionary_word_ticker),
    ("build98", "**符号消歧丢了什么必须能被看见**", _b98_symbol_drops_are_visible),
    ("build99", "**破折号前的事实不被后半句的钩子杀掉**", _b99_fact_clause_rescue),
    ("build100", "**闸门数事件不数文章（转载不顶开闸门）**", _b100_events_not_articles),
    ("build100", "**评论体标题不被正文顶成实质**", _b100_commentary_frames),
    ("build101", "**池截断切在清洗之后，且报得出来**", _b101_pool_cut_after_prefilter),
    ("build102", "**持股申报不占闸门名额**", _b102_pinned_context_yields_gate_slots),
    ("build102", "**四个丢弃原因全要印**", _b102_all_drop_reasons_printed),
    ("build103", "**判定器护栏（引文核对/显式降级/不问政策）**", _b103_judge_guardrails),
    ("build103", "**留出集真的没被调参用过**", _b103_heldout_is_held_out),
    ("build104", "**评测先报降级率，再报分数**", _b104_eval_reports_degradation),
    ("build105", "**judge 自己加载 .env，不靠导入顺序**", _b105_judge_loads_dotenv),
    ("build106", "**政策直判：来源/表单不问模型**", _b106_policy_beats_the_model),
    ("build106", "**否决只往下压，不往上抬**", _b106_hybrid_veto_only_pushes_down),
    ("build107", "**技术观察员 v1：只描述、无未来函数、null≠0**",
     _b107_technical_observer_v1),
    ("build107", "**盘前按市场时区发车（纽约19:49那次）**",
     _b107_premarket_fires_in_market_time),
    ("build108", "**美股模式不收 cn 桶，且过滤可见**",
     _b108_us_mode_drops_the_cn_bucket),
    ("build109", "**setup 阈值与事件定义冻结（一次事件≠一个 stock-day）**",
     _b109_setup_and_event_are_frozen),
    ("build110", "**事件带完整血统 / PIT 按区间判 / 复核台账**",
     _b110_lineage_pit_and_review),
]

for _b, _n, _f in CHECKS:
    probe(_b, _n, _f)

print("=" * 68)
print("安装自检 —— 全绿才去跑 run_unit_a.py")
print("=" * 68)
cur = ""
for build, name, ok, detail in ROWS:
    if build != cur:
        cur = build
        print(f"\n[{build}]")
    print(f"  {'OK  ' if ok else 'MISS'}  {name}" + (f"\n          {detail}" if detail else ""))

bad = [(b, n) for b, n, ok, _ in ROWS if not ok]
print("\n" + "=" * 68)
if not bad:
    print(f"全部 {len(ROWS)} 项通过 —— 代码是最新的，可以跑了。")
    raise SystemExit(0)
builds = sorted({b for b, _ in bad})
print(f"{len(bad)} 项缺失，涉及：{'、'.join(builds)}")
print()
print("可能的原因，按概率排序 —— **先看上面每条 MISS 后面的异常信息**：")
print("  1. 文件没落到 src/cio/ 下 —— 重新执行安装命令")
print("     （unzip 与 cp 都要成功；用 && 串起来，任何一步失败就停）")
print("  2. 探针自己写错了 —— 尤其是只有一两项红、而功能实际能跑的时候")
print("     报告标题会随 CIO_MARKET 在中英文间切换，写死某种语言的断言会误报")
print("  3. 环境变量与探针预期不一致（CIO_MARKET / CIO_MOCK_LLM 等）")
print()
print("**全红且异常都是 ImportError/ModuleNotFoundError → 多半是解释器用错了。**")
raise SystemExit(1)
