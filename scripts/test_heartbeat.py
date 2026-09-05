#!/usr/bin/env python3
"""心跳自测 —— **"今天没有"和"今天没跑"必须长得不一样。**

    python scripts/test_heartbeat.py

这份报告是被两次真实故障逼出来的：盘前简报静默失踪三天（日志里每天一行
"跳过"，没有人会看），以及全市场大盘超额一起变 null（502 张卡片各写一句
"该字段是 null"，一个正确的事实说了 502 遍，没有变成一个结论）。

两次的形状是同一个：**系统在正常运行的外观下什么也没做，或者做错了。**
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("CIO_MARKET", "us")
os.environ.setdefault("CIO_TG_DRYRUN", "1")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _no_network                                             # noqa: E402,F401

from cio import heartbeat as hbmod                             # noqa: E402

OK: list = []
BAD: list = []


def check(name, fn):
    try:
        fn()
        OK.append(name)
        print(f"  OK    {name}")
    except Exception as e:                                     # noqa: BLE001
        BAD.append((name, f"{type(e).__name__}: {e}"))
        print(f"  ERR   {name}\n          {type(e).__name__}: {e}")


def t_declared_stages_appear_even_when_they_never_run():
    """**阶段表是声明的，不是跑出来的。**

    一个死在中途的流水线，如果报告只是"短一点"，那和"今天本来就没什么事"
    看起来一样。所以没跑到的阶段必须印出来，标"未运行"。
    """
    rep = hbmod.Report("2026-09-04")
    with rep.stage("technical_snapshot") as st:
        st.count(scanned=502, triggers=0)
    text = rep.render()
    # **每个阶段要有自己那一行。**
    # 上一版我断的是 `label in text` —— 而报告底部那句"未运行的阶段：…"
    # 里也有这些标签，于是"只印跑过的阶段"这个变异照样绿：
    # 断言从另一条代码路径被满足了。
    for _key, label in hbmod.PIPELINE:
        assert f"[{label}]" in text, f"{label} 没有自己那一行：\n{text}"
    assert text.count("[") >= len(hbmod.PIPELINE), text
    assert "未运行" in text, text
    assert len(rep.never_ran()) == len(hbmod.PIPELINE) - 1, rep.never_ran()
    # **未声明的阶段不许临时冒出来**：它不跑的时候就不会出现在报告里
    try:
        rep.stage("没声明过的阶段")
        raise AssertionError("未声明的阶段被收下了")
    except KeyError as e:
        assert "PIPELINE" in str(e), str(e)


def t_zero_is_a_conclusion_not_a_blank():
    """**`0 triggers` 要印出来。** 空白和 0 是两件事。"""
    rep = hbmod.Report("2026-09-04")
    with rep.stage("technical_snapshot") as st:
        st.count(scanned=502, gate_passed=0)
    text = rep.render()
    assert "gate_passed 0" in text, text
    assert "scanned 502" in text, text


def t_a_failed_stage_is_recorded_and_does_not_stop_the_rest():
    """一个阶段炸了不拖垮别的，**但绝不吞掉**。"""
    rep = hbmod.Report("2026-09-04")
    with rep.stage("technical_snapshot") as st:
        st.count(scanned=1)
        raise RuntimeError("取数全挂")
    # 上面那句异常必须被吃进报告，而不是冒到这里
    with rep.stage("research_router") as st2:
        st2.count(triggers=3)
    assert rep.stages["technical_snapshot"].status == hbmod.FAILED
    assert "RuntimeError" in rep.stages["technical_snapshot"].error
    assert rep.stages["research_router"].status == hbmod.OK
    assert rep.exit_code() == 1, "有阶段失败，退出码却是 0"
    text = rep.render()
    assert "失败" in text and "取数全挂" in text, text


def t_skip_needs_a_reason():
    """**没有理由的跳过，和没跑到分不开。**"""
    rep = hbmod.Report("2026-09-04")
    with rep.stage("technical_snapshot") as st:
        try:
            st.skip("")
            raise AssertionError("没理由的跳过被收下了")
        except ValueError:
            pass
        st.skip("不在收盘窗口：周末")
    assert rep.stages["technical_snapshot"].status == hbmod.SKIPPED
    assert "周末" in rep.render()
    # **跳过不算失败**：它是一个有理由的、正常的结果
    assert rep.exit_code() == 0


def t_missing_day_means_it_never_ran():
    """**磁盘上有没有那一天的报告，就是"那天到底跑没跑"的答案。**

    这是整个模块的理由：全 0 的报告说"跑了，没事"；没有报告说"根本没跑"。
    在这之前这两件事从磁盘上、从收件箱里都分不出来（都是什么都没有）。
    """
    import datetime as dt
    with tempfile.TemporaryDirectory() as td:
        old = hbmod.REPORT_DIR
        try:
            hbmod.REPORT_DIR = Path(td)
            assert hbmod.dates() == []
            today = dt.date.today()
            rep = hbmod.Report(today.isoformat())
            with rep.stage("technical_snapshot") as st:
                st.count(scanned=0)
            p = rep.save()
            assert p.exists()
            assert hbmod.dates() == [today.isoformat()]
            back = hbmod.load(today.isoformat())
            assert back["as_of"] == today.isoformat()
            assert back["schema_version"] == hbmod.SCHEMA_VERSION
            # 今天有报告 → 今天不在缺失名单里
            assert today.isoformat() not in hbmod.missing_days(back=5)
            # 昨天（如果是工作日）没报告 → 必须被列出来
            for i in range(1, 6):
                d = today - dt.timedelta(days=i)
                if d.weekday() in (0, 1, 2, 3, 4):
                    assert d.isoformat() in hbmod.missing_days(back=6), d
                    break
            # **非日期文件不许被当成一天**（卡片目录那个坑的同一形状）
            (Path(td) / "notes.json").write_text("{}", "utf-8")
            assert hbmod.dates() == [today.isoformat()], hbmod.dates()
        finally:
            hbmod.REPORT_DIR = old


def t_the_snapshot_writes_a_heartbeat_even_when_it_skips():
    """**窗口外退出也要留下一份报告。**

    先前那道闸是直接 `return 0` 的，于是"因为不在窗口所以跳过"和
    "根本没跑"在磁盘上一模一样 —— 而那正是盘前简报静默失踪三天时的形状。
    """
    import ast
    src = (Path(__file__).resolve().parent / "technical_snapshot.py"
           ).read_text("utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "main")
    body = ast.get_source_segment(src, fn) or ""
    # Report 必须在闸门判断**之前**建起来
    assert body.index("heartbeat.Report(") < body.index("is_snapshot_time()"), \
        "心跳建在闸门之后 —— 跳过的那天就不会留下报告"
    assert "hb.skip(" in body, "跳过没有记进心跳"
    assert "rep.save()" in body and "rep.push()" in body, body[-400:]
    # 后面几节必须被显式标注，而不是悄悄空着
    assert "research_router" in body and "unit_a" in body, body

    # 一张卡都没出 = 失败，不是"今天没事"
    sb = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_snapshot_body")
    sbody = ast.get_source_segment(src, sb) or ""
    i = sbody.index("if not cards:")
    seg = sbody[i:i + 500]
    assert "raise" in seg, "一张卡都没出却只是 return —— 那和安静的一天一样"
    assert "hb.count(" in seg, "失败时没记计数"


def t_the_installer_refuses_outside_the_snapshot_window():
    """收盘快照的定时也要**从窗口现算小时数**，并拒绝装在窗口外。

    盘前那个 19:30 就是写死小时数的后果：按 A 股盘前排的，用机器的美东钟
    表达，换成美股之后没人动它。**写死的小时数记不住自己为哪个市场写的。**
    """
    import plistlib
    import shutil
    import subprocess
    root = Path(__file__).resolve().parents[1]
    sh = root / "scripts" / "install_snapshot_launchd.sh"
    assert sh.exists(), "收盘快照没有定时安装脚本"

    with tempfile.TemporaryDirectory() as td:
        home = Path(td) / "home"
        (home / "Library" / "LaunchAgents").mkdir(parents=True)
        agent = Path(td) / "agent"
        shutil.copytree(root / "src", agent / "src")
        (agent / "scripts").mkdir(parents=True)
        shutil.copy(sh, agent / "scripts" / "install_snapshot_launchd.sh")
        venv = agent / ".venv" / "bin"
        venv.mkdir(parents=True)
        w = venv / "python"
        w.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
        w.chmod(0o755)
        stub = Path(td) / "bin"
        stub.mkdir()
        for name in ("launchctl", "plutil"):
            f = stub / name
            f.write_text("#!/bin/sh\nexit 0\n")
            f.chmod(0o755)
        env = dict(os.environ, HOME=str(home), CIO_MARKET="us",
                   TZ="America/New_York",
                   PATH=f"{stub}:{os.environ.get('PATH','')}")
        plist = home / "Library" / "LaunchAgents" / "com.crystal.cio.snapshot.plist"

        def run(**extra):
            e2 = dict(env)
            e2.update(extra)
            r = subprocess.run(
                ["bash", str(agent / "scripts" / "install_snapshot_launchd.sh")],
                capture_output=True, env=e2, timeout=120)
            return r.returncode, (r.stdout or b"").decode("utf-8", "replace")

        # 默认：收盘窗口 16:30 起 → 排在 18:00（**让 yfinance 尾行落定**）
        rc, out = run()
        assert rc == 0, out
        assert plist.exists(), out
        cal = plistlib.loads(plist.read_bytes())["StartCalendarInterval"]
        assert {c["Weekday"] for c in cal} == {1, 2, 3, 4, 5}, cal
        assert {c["Hour"] for c in cal} == {18}, \
            f"默认小时不是窗口起点+2：{sorted(c['Hour'] for c in cal)}"

        # 盘中 11 点必须被拒绝，而且不留下 plist
        plist.unlink()
        rc, out = run(CIO_SNAPSHOT_HOUR="11")
        assert rc != 0, "盘中也照装：\n" + out
        assert not plist.exists(), "被拒绝了却还是写出了 plist"
        assert "16:30" in out, out

        # 显式放行才可以
        rc, out = run(CIO_SNAPSHOT_HOUR="11", CIO_SNAPSHOT_ALLOW_ANY_HOUR="1")
        assert rc == 0, out
        assert {c["Hour"] for c in plistlib.loads(plist.read_bytes())
                ["StartCalendarInterval"]} == {11}

        # **小时数真的是算出来的,不是写死的 18。**
        # 在美东机器上窗口起点 16:30 → 默认 18,和写死 18 一模一样,
        # 这个夹具没有判别力。把机器时区掰到上海:收盘窗口变成
        # 04:30–11:59,默认应当是 6 —— 而写死的 18 会落在窗口外被拒。
        plist.unlink()
        rc, out = run(TZ="Asia/Shanghai")
        assert rc == 0, "上海时区下装不上：\n" + out
        hours = {c["Hour"] for c in plistlib.loads(plist.read_bytes())
                 ["StartCalendarInterval"]}
        assert hours == {6}, \
            f"小时数没跟着窗口走（上海应为 6,拿到 {sorted(hours)}）：\n{out}"
        assert "04:30" in out, out


def t_the_dst_warning_only_fires_when_it_is_true():
    """**一句不成立的警告，和不印一样没用。**

    `cron_hint` 原来无条件印"一年两次夏令时切换要手动改"。
    在她那台美东机器上那句话是假的：本机 06:00 就是市场 06:00，
    launchd/cron 跟着本机 DST 走，自己就对了。
    """
    from cio import schedule as s
    same = "\n".join(s.cron_hint("America/New_York"))
    diff = "\n".join(s.cron_hint("Asia/Shanghai"))
    assert "不用手动改" in same, same[-200:]
    assert "要手动改" in diff and "不用手动改" not in diff, diff[-200:]
    assert same != diff


def t_snapshot_window_can_be_converted_separately():
    """`local_window` 要能换算收盘窗口，否则安装脚本只能写死小时数。"""
    from cio import schedule as s
    pre = s.local_window("America/New_York", win=s.PREMARKET_WINDOW)
    snap = s.local_window("America/New_York", win=s.SNAPSHOT_WINDOW)
    assert pre == ("06:00", "09:15"), pre
    assert snap == ("16:30", "23:59"), snap
    assert s.local_window("America/New_York") == pre, "默认不再是盘前窗口了"
    assert s.window(s.SNAPSHOT_WINDOW) != s.window(), "两个窗口返回了同一个"


TESTS = [
    ("**声明过的阶段，没跑也要出现**", t_declared_stages_appear_even_when_they_never_run),
    ("**0 是一个结论，不是空白**", t_zero_is_a_conclusion_not_a_blank),
    ("**一个阶段失败不拖垮别的，但绝不吞掉**", t_a_failed_stage_is_recorded_and_does_not_stop_the_rest),
    ("跳过必须写理由，且不算失败", t_skip_needs_a_reason),
    ("**没有报告的那天 = 那天没跑**", t_missing_day_means_it_never_ran),
    ("**快照跳过时也要留下心跳**", t_the_snapshot_writes_a_heartbeat_even_when_it_skips),
    ("**收盘定时从窗口现算，窗口外拒装**", t_the_installer_refuses_outside_the_snapshot_window),
    ("**夏令时警告只在成立时才印**", t_the_dst_warning_only_fires_when_it_is_true),
    ("收盘窗口可以单独换算", t_snapshot_window_can_be_converted_separately),
]

print("=" * 72)
print("心跳自测 —— 今天没有 ≠ 今天没跑")
print("=" * 72)
for _n, _f in TESTS:
    check(_n, _f)

print("\n" + "=" * 72)
if BAD:
    print(f"{len(BAD)} 项失败 / 共 {len(TESTS)}")
    for n, e in BAD:
        print(f"  · {n}\n      {e}")
    raise SystemExit(1)
print(f"全部 {len(OK)} 项通过。")
raise SystemExit(0)
