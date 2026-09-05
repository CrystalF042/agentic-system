#!/usr/bin/env python3
"""发车时间自测 —— **回归 2026-09-02 那次"晚上七点发盘前"。**

    python scripts/test_schedule.py

那次的现场：简报本身完全正确（英文抬头、ET 时间戳、美股期货指数、
当天真实新闻），只有发车时间错了 —— 纽约 09-01 **19:49**，收盘四小时之后。

cron、采集、PDF、推送全部成功，日志全绿。**没有任何一层问过
"这份东西是给哪个市场的开盘用的"。**

## 病因我当时断错了，这里如实记着

我写的是"机器时区是 Asia/Shanghai，北京 07:00 = 纽约前一天 19:00"。
**2026-09-05 实测否掉了它**：`date` 返回 `Fri Sep  4 22:19:54 EDT 2026`，
机器就在美东，`0 7 * * 1-5` 在这台机器上本来就是美东 07:00。

真正的病因**至今未知**（`run_premarket.py --doctor` 去读机器上真正装着的排程）。
下面这些用例钉的是**结果**，不是我那个病因：无论排程怎么错，
窗口外都不该发；真要绕过，绕过这件事必须在成品上看得见。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

os.environ.setdefault("CIO_MARKET", "us")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _no_network                                            # noqa: E402,F401

from cio import schedule as S                                 # noqa: E402

OK, BAD = [], []
BJ = ZoneInfo("Asia/Shanghai")
NY = ZoneInfo("America/New_York")


def check(name, fn):
    try:
        fn()
        OK.append(name)
        print(f"  OK    {name}")
    except AssertionError as e:
        BAD.append((name, str(e)))
        print(f"  FAIL  {name}\n          {e}")
    except Exception as e:                                     # noqa: BLE001
        BAD.append((name, f"{type(e).__name__}: {e}"))
        print(f"  ERR   {name}\n          {type(e).__name__}: {e}")


def t_the_actual_failure_is_rejected():
    """**那一刻必须被判成"不该发"。** 这是本文件存在的唯一理由。"""
    ok, why = S.is_premarket(datetime(2026, 9, 2, 7, 49, tzinfo=BJ))
    assert ok is False, "北京 09-02 07:49（纽约 09-01 19:49）被判成了盘前"
    assert "19:49" in why and "EDT" in why, why


def t_the_right_moment_is_accepted():
    """纽约早上 07:30 该发。"""
    ok, why = S.is_premarket(datetime(2026, 9, 2, 7, 30, tzinfo=NY))
    assert ok is True, why


def t_weekend_is_rejected():
    ok, why = S.is_premarket(datetime(2026, 9, 5, 7, 30, tzinfo=NY))
    assert ok is False and "周末" in why, why


def t_edges_are_closed_at_the_top():
    """窗口是左闭右开：06:00 发，09:15 不发。**开盘前必须已经送到。**"""
    assert S.is_premarket(datetime(2026, 9, 2, 6, 0, tzinfo=NY))[0] is True
    assert S.is_premarket(datetime(2026, 9, 2, 9, 14, tzinfo=NY))[0] is True
    assert S.is_premarket(datetime(2026, 9, 2, 9, 15, tzinfo=NY))[0] is False
    assert S.is_premarket(datetime(2026, 9, 2, 5, 59, tzinfo=NY))[0] is False


def t_dst_shifts_the_local_hour_but_not_the_market_hour():
    """**夏令时是"改 cron 小时数"这条路的死因。**

    同一个市场窗口，在北京时间上夏天和冬天差一小时。手抄进 crontab 的
    数字一年会错两次，每次错一小时，而且不会有任何提示。
    """
    summer = S.local_window("Asia/Shanghai", datetime(2026, 9, 2, 12, tzinfo=ZoneInfo("UTC")))
    winter = S.local_window("Asia/Shanghai", datetime(2026, 12, 2, 12, tzinfo=ZoneInfo("UTC")))
    assert summer != winter, (summer, winter)
    assert summer[0] == "18:00" and winter[0] == "19:00", (summer, winter)
    # 但市场本地时间的窗口纹丝不动 —— 这正是判断该放在市场时区的理由
    lo, _hi = S.window()
    assert lo.strftime("%H:%M") == "06:00"


def t_window_follows_the_market_flag():
    """cn 和 us 的窗口不是同一个。"""
    from cio import config
    lo_us, _ = S.window()
    old = config.MARKET
    try:
        config.MARKET = "cn"
        import importlib
        importlib.reload(S)
        lo_cn, _ = S.window()
    finally:
        config.MARKET = old
        import importlib
        importlib.reload(S)
    assert lo_us != lo_cn, (lo_us, lo_cn)


def t_next_window_skips_weekends():
    nxt = S.next_window_start(datetime(2026, 9, 4, 20, tzinfo=NY))   # 周五晚
    assert nxt.weekday() == 0, nxt        # 下一班是周一
    assert nxt.strftime("%H:%M") == "06:00", nxt


def t_gate_runs_before_any_network_call():
    """**闸门必须在取数之前。**

    放在采集之后，就等于"每小时把全网新闻采一遍再决定要不要发"。
    这里断的是源码结构：`is_premarket` 的调用必须出现在
    `main()` 里第一个 collect/db 调用之前。
    """
    import ast
    src = (Path(__file__).resolve().parents[1] / "run_premarket.py").read_text("utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "main")
    gate_line = work_line = None
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            f = n.func
            name = getattr(f, "attr", getattr(f, "id", ""))
            if name == "is_premarket" and gate_line is None:
                gate_line = n.lineno
            if name in ("collect_premarket", "init_db", "collect_funds") and work_line is None:
                work_line = n.lineno
    assert gate_line is not None, "main() 里没有时间闸"
    assert work_line is not None and gate_line < work_line, \
        f"时间闸在第 {gate_line} 行，而取数在第 {work_line} 行 —— 闸门必须在前"


def t_manual_request_bypasses_the_gate():
    """**人开口要，就一定给。** Telegram 那条"生成盘前简报"必须绕过时间闸。

    时间闸是给 cron 的排程规则，不是对 CEO 的拒绝。
    """
    src = (Path(__file__).resolve().parents[1] / "run_command.py").read_text("utf-8")
    import ast
    tree = ast.parse(src)
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "premarket_main"]
    assert calls, "run_command 里找不到 premarket_main 调用"
    assert any(any(k.arg == "force" and k.value.value is True for k in c.keywords)
               for c in calls), "手动路径没有 force=True，CEO 会被时间闸挡住"


def t_us_mode_does_not_fetch_a_share_flows():
    """**美股模式下不再去取 A 股资金面。**

    那三条常驻降级（两市成交额/北向/板块资金）不是故障，是拿 A 股口径
    去问美股。每天必然失败三次、印在抬头上，把真正的降级淹掉。
    """
    from cio import funds
    st: dict = {}
    assert funds.collect_funds(st) == []
    assert "资金面" in st and "us 模式" in st["资金面"]
    for k in ("两市成交额", "北向资金", "板块资金"):
        assert k not in st, f"us 模式下仍在尝试 {k}"



def t_bad_timezone_argument_says_who_and_what():
    """**报错要指向原因，不是指向症状。**

    她把 `PREMARKET_WINDOW`（一个 dict）传进 `local_window()`，
    拿到的是 `ZoneInfo` 内部抛的：

        TypeError: unhashable type: 'dict'

    那句话只讲"dict 不能做弱引用缓存的键"，**没讲是谁把什么传错了**。
    签名写着 `str | None`，但注解在运行时什么都不做。
    """
    from cio import schedule as s
    # 正常路径不许被破坏
    lo, hi = s.local_window()
    assert ":" in lo and ":" in hi, (lo, hi)
    assert s.local_window("Asia/Shanghai") != s.local_window("America/New_York")
    assert s.local_window(None) == s.local_window("")

    try:
        s.local_window(s.PREMARKET_WINDOW)
        raise AssertionError("传 dict 进去居然没报错")
    except TypeError as e:
        msg = str(e)
        assert "machine_tz" in msg, msg          # 是哪个参数
        assert "dict" in msg, msg                # 传的是什么类型
        assert "America/New_York" in msg, msg    # 该传什么
        assert "unhashable" not in msg, "还是那句症状级报错：" + msg

    try:
        s.cron_hint(123)
        raise AssertionError("cron_hint 没做同样的检查")
    except TypeError as e:
        assert "machine_tz" in str(e), str(e)

    try:
        s.local_window("Nowhere/Nothing")
        raise AssertionError("认不出的时区名没报错")
    except ValueError as e:
        assert "Nowhere/Nothing" in str(e), str(e)


def t_bypassing_the_gate_cannot_be_hidden():
    """**绕过闸门是允许的，隐瞒绕过不允许。**

    上次故障的要害不是"发错了时间"，是**发错时间的那份和发对时间的
    长得一模一样**。所以窗口外产出必须在三条路上都带标记：
    Telegram 正文、caption、存档文件名。只标一处，另外两条路照样看不出来。
    """
    import importlib.util
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("rp", root / "run_premarket.py")
    rp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rp)

    # 一、文件名
    inside = rp.archive_base("2026-09-04-0700", False)
    outside = rp.archive_base("2026-09-04-1949", True)
    assert "窗口外" not in inside, inside
    assert "窗口外" in outside, outside
    assert inside != outside

    # 二、Telegram 正文
    class _S:
        fetched = 1
        deduped = 0
        ingested_vectors = 0

    class _B:
        bluf = ["x"]
        fund_flows: list = []
        watchlist_hits: list = []
        status = _S()
        dt_ny = "2026-09-01 19:49 EDT"
        dt_beijing = "2026-09-02 07:49"

    b = _B()
    normal = rp._summary_text(b, False)
    forced = rp._summary_text(b, True)
    assert rp.OUT_OF_WINDOW_MARK not in normal, normal
    assert rp.OUT_OF_WINDOW_MARK in forced.splitlines()[0], forced
    assert "不是在盘前窗口内产出的" in forced, forced
    assert normal != forced

    # 三、caption 也要带。**断结构，不要断文本**——上一版我写的是
    # `"mark" in src[i:i+120]`，而紧邻的 `_market_stamp` 里就含有 "mark"
    # 这四个字母，把 {mark} 删掉照样绿。
    import ast
    src = (root / "run_premarket.py").read_text("utf-8")
    tree = ast.parse(src)
    cap = None
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "caption":
            cap = node.value
    assert cap is not None, "找不到 caption 参数"
    names = {n.id for n in ast.walk(cap) if isinstance(n, ast.Name)}
    assert "mark" in names, f"caption 里没有引用 mark：{sorted(names)}"

    # 五、**标记是不是真的会被打开**：_out_of_window 必须同时看
    # forced 和 in_window。写死成 False，上面所有断言仍然全绿
    # （它们都是直接传 True 进去测的），而线上永远不会标记。
    assigns = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)
               and any(getattr(x, "id", "") == "_out_of_window" for x in n.targets)]
    assert assigns, "找不到 _out_of_window 的赋值"
    for a in assigns:
        used = {n.id for n in ast.walk(a.value) if isinstance(n, ast.Name)}
        assert {"forced", "in_window"} <= used, \
            f"_out_of_window 没有同时看 forced 和 in_window：{sorted(used)}"

    # 四、--doctor 存在，且**不发任何网络请求**（它只读本机排程）
    assert hasattr(rp, "doctor"), "没有 --doctor，就只能靠猜机器上装了什么"
    import ast
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "doctor")
    body = ast.get_source_segment(src, fn) or ""
    for banned in ("collect_premarket", "deliver_brief", "requests", "httpx"):
        assert banned not in body, f"doctor 里出现了 {banned} —— 它该只读本机状态"




def _run_bytes(cmd, env, timeout=180):
    """跑一条命令，**自己按 UTF-8 解码，不交给 locale**。

    她机器上第一次跑这条用例炸的是：

        UnicodeDecodeError: 'utf-8' codec can't decode byte 0xbc in position 62

    `subprocess.run(..., text=True)` 用的是 `locale.getpreferredencoding()`，
    机器 locale 不是 UTF-8 时，同一段中文输出在我这儿能读、在她那儿炸。
    **一个解码问题伪装成了逻辑失败** —— 用例红了，而被测的东西完全正常。

    所以这里：拿字节、显式 UTF-8、`errors="replace"`，并且**把出问题的
    原始字节带进断言消息**，下次不用猜。
    """
    import subprocess
    r = subprocess.run(cmd, capture_output=True, env=env, timeout=timeout)

    def dec(raw, name):
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as e:
            # **当场印出来，不要只塞进断言消息。**
            # 塞进断言消息只有在用例红了的时候才看得到；而加了
            # errors="replace" 之后用例会变绿，那些字节就再也没人看见了——
            # 那正好又是一次"修好了报警，于是问题隐身了"。
            bad = raw[max(0, e.start - 24):e.start + 24]
            print(f"    [注意] {name} 在第 {e.start} 字节不是合法 UTF-8："
                  f"{raw[e.start:e.start + 1]!r}")
            print(f"    [注意] 前后 48 字节：{bad!r}")
            print(f"    [注意] 开头 120 字节：{raw[:120]!r}")
            return raw.decode("utf-8", "replace")

    return r.returncode, dec(r.stdout or b"", "stdout"), dec(r.stderr or b"", "stderr")


def t_the_installer_refuses_to_schedule_outside_the_window():
    """**2026-09-05 查到的现场：plist 排在 19:30 本机时间（EDT）。**

        A 股盘前   07:00–09:15 北京
        北京 07:30  =  EDT 19:30（前一天）

    这个 plist 是按 A 股盘前排的、用机器的美东钟表达。当时没错，
    `CIO_MARKET` 换成 us 之后没人动它，它就一直错着。
    09-01 那份 19:49 送达 = 19:30 触发 + 跑了 19 分钟。

    要害不是"发错时间"——时间闸修好之后，窗口外的任务**什么都不发**，
    每天静悄悄地触发、退出。**一个不发简报的早晨和一个没跑过的早晨长得一样。**

    ## 这条用例是跑安装脚本，不是 grep 它

    第一版我 grep `"拒绝安装" in sh`，而变异只删掉 `exit 1`、留着那句 echo，
    照样绿；grep `"local_window()"`，而**文件开头的注释里就有这个词**，
    照样绿。**断行为，不要断文本**——这一轮第三次栽在同一处。
    """
    import plistlib
    import shutil
    import subprocess
    import tempfile

    root = Path(__file__).resolve().parents[1]
    sh = root / "scripts" / "install_launchd.sh"

    # 北京 07:30 就是美东 19:30（前一天）——那个 19:30 的来历
    b = datetime(2026, 9, 2, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    e = b.astimezone(ZoneInfo("America/New_York"))
    assert (e.hour, e.minute) == (19, 30) and e.day == 1, e

    with tempfile.TemporaryDirectory() as td:
        home = Path(td) / "home"
        (home / "Library" / "LaunchAgents").mkdir(parents=True)
        agent = Path(td) / "agent"
        shutil.copytree(root / "src", agent / "src")
        (agent / "scripts").mkdir(parents=True)
        shutil.copy(sh, agent / "scripts" / "install_launchd.sh")
        venv = agent / ".venv" / "bin"
        venv.mkdir(parents=True)
        # **不能软链解释器**：venv 是靠可执行文件旁边的 pyvenv.cfg 认出来的，
        # 软链到别处就丢了 site-packages（第一次跑这条用例就撞上了：
        # ModuleNotFoundError: No module named 'yaml'）。给一个 exec 包装。
        wrapper = venv / "python"
        wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
        wrapper.chmod(0o755)
        # launchctl / plutil 是 macOS 的，这里给两个空壳
        stub = Path(td) / "bin"
        stub.mkdir()
        for name in ("launchctl", "plutil"):
            f = stub / name
            f.write_text("#!/bin/sh\nexit 0\n")
            f.chmod(0o755)
        env = dict(os.environ, HOME=str(home), CIO_MARKET="us",
                   TZ="America/New_York",
                   PATH=f"{stub}:{os.environ.get('PATH','')}")
        plist = home / "Library" / "LaunchAgents" / "com.crystal.cio.premarket.plist"

        class _R:
            def __init__(self, rc, out, err):
                self.returncode, self.stdout, self.stderr = rc, out, err

        def run(**extra):
            e2 = dict(env)
            e2.update(extra)
            return _R(*_run_bytes(["bash", str(agent / "scripts" / "install_launchd.sh")],
                                  e2, timeout=120))

        # 一、默认：小时数从窗口现算（美东盘前 06:00 起 → 6 点），周一到周五
        r = run()
        assert r.returncode == 0, r.stdout + r.stderr
        assert plist.exists(), "默认安装没写出 plist"
        d = plistlib.loads(plist.read_bytes())
        cal = d["StartCalendarInterval"]
        assert isinstance(cal, list) and len(cal) == 5, cal
        assert {c["Weekday"] for c in cal} == {1, 2, 3, 4, 5}, cal
        assert {c["Hour"] for c in cal} == {6}, \
            f"小时数不是从窗口算的（窗口 06:00 起，装成了 {sorted(c['Hour'] for c in cal)}）"

        # 二、**19 点必须被拒绝，而且不许留下 plist**
        plist.unlink()
        r = run(CIO_PREMARKET_HOUR="19")
        assert r.returncode != 0, "窗口外照装不误：\n" + r.stdout + r.stderr
        assert not plist.exists(), "被拒绝了却还是写出了 plist"
        assert "19" in r.stdout and "06:00" in r.stdout, r.stdout

        # 三、显式放行才可以
        r = run(CIO_PREMARKET_HOUR="19", CIO_PREMARKET_ALLOW_ANY_HOUR="1")
        assert r.returncode == 0, r.stdout + r.stderr
        assert plist.exists()
        assert {c["Hour"] for c in plistlib.loads(plist.read_bytes())
                ["StartCalendarInterval"]} == {19}

        # 四、**算不出窗口时不许静默退出。**
        # `set -e` 下 `WIN=$(失败的命令)` 会当场退出，提示永远打不出来 ——
        # 装的人只看到一个空白的 exit 1。**这正是这一整轮在防的形状。**
        wrapper.write_text('#!/bin/sh\necho "boom: 依赖没装全" >&2\nexit 1\n')
        wrapper.chmod(0o755)
        r = run()
        assert r.returncode != 0, r.stdout
        assert r.stdout.strip(), "算不出窗口却一个字都不说 —— 静默退出"
        assert "算不出盘前窗口" in r.stdout, r.stdout
        assert "boom" in r.stdout, "把 stderr 吞了 —— 等于把「为什么」也吞了：\n" + r.stdout
        assert "--doctor" in r.stdout, "没告诉人下一步查什么：\n" + r.stdout


def t_doctor_says_the_consequence_not_just_the_mismatch():
    """**"对不上"和"所以你收不到简报"是两句话，第二句才是人要的。**

    复刻她机器上那个 plist（19:30、没有 Weekday），跑 `--doctor`，
    断的是**它印出来的东西**，不是源码里有没有那几个字——
    第一版我 grep 函数体，而同样的字在模块注释里也有，变异照样绿。
    """
    import plistlib
    import subprocess
    import tempfile

    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        (home / "Library" / "LaunchAgents").mkdir(parents=True)
        (home / "Library" / "LaunchAgents"
         / "com.crystal.cio.premarket.plist").write_bytes(plistlib.dumps({
             "Label": "com.crystal.cio.premarket",
             "ProgramArguments": ["/x/.venv/bin/python", "/x/run_premarket.py"],
             "StartCalendarInterval": [{"Hour": 19, "Minute": 30}]}))
        env = dict(os.environ, HOME=str(home), CIO_MARKET="us",
                   TZ="America/New_York")
        _rc, out, _err = _run_bytes(
            [sys.executable, str(root / "run_premarket.py"), "--doctor"], env, timeout=120)
    assert "19:30" in out, out
    assert "没有 Weekday" in out, "没说它每天都触发（包括周末）：\n" + out
    assert "对不上" in out, out
    assert "什么都不发" in out, \
        "只说了对不上，没说后果 —— 而后果是静默，不是发错时间：\n" + out
    assert "install_launchd.sh" in out, "没告诉人怎么修：\n" + out


TESTS = [
    ("**那次真实故障（北京07:49=纽约19:49）被拒**", t_the_actual_failure_is_rejected),
    ("纽约早上 07:30 放行", t_the_right_moment_is_accepted),
    ("周末不发", t_weekend_is_rejected),
    ("窗口左闭右开，开盘前必须送到", t_edges_are_closed_at_the_top),
    ("**夏令时会挪动本地小时数（手改 cron 的死因）**", t_dst_shifts_the_local_hour_but_not_the_market_hour),
    ("窗口跟着市场开关走", t_window_follows_the_market_flag),
    ("下一班跳过周末", t_next_window_skips_weekends),
    ("**闸门跑在任何取数之前**", t_gate_runs_before_any_network_call),
    ("**人手动要简报时绕过闸门**", t_manual_request_bypasses_the_gate),
    ("**us 模式不再取 A 股资金面**", t_us_mode_does_not_fetch_a_share_flows),
    ("**时区参数传错要说清是谁传错了什么**", t_bad_timezone_argument_says_who_and_what),
    ("**绕过闸门藏不住（正文/caption/文件名三处）**", t_bypassing_the_gate_cannot_be_hidden),
    ("**安装脚本拒绝把任务排在窗口之外**", t_the_installer_refuses_to_schedule_outside_the_window),
    ("**doctor 要说后果（静默），不只说对不上**", t_doctor_says_the_consequence_not_just_the_mismatch),
]

print("=" * 72)
print("发车时间自测 —— 按市场时区判断盘前，不按机器时区")
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
