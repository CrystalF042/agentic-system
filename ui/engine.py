"""引擎调用层 —— 只起进程、读输出，**一行业务逻辑都没有**。

架构纪律（build83 冻结的三份契约）：

    Result    contract   stdout 整段一次 json.loads，顶层有
                         schema_version / run_id / kind / status
    Progress  contract   stderr 的 `[STAGE] name | detail`
    Execution contract   subprocess 调 run_*.py，**不 import cio**

**为什么不 import。** 界面一旦能 import 引擎，就有机会在页面里重新实现一遍
判定逻辑——"THIN 就是信心封顶为弱"这种规则会被抄第二遍，而闸门哪天改了，
抄的那份不会报错，只会开始说假话。这个坑在 `run_scan.py` 自己身上就发生过一次。

顺带的好处：Shiny 要 Python ≥3.10，引擎跑在 3.9 的 venv 上，两边互不干扰。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

# 引擎根目录与解释器。**都从环境变量读，不猜。**
CIO_HOME = Path(os.environ.get("CIO_HOME", Path(__file__).resolve().parents[1]))
CIO_PY = os.environ.get("CIO_PY") or str(CIO_HOME / ".venv" / "bin" / "python")
if not Path(CIO_PY).exists():                # 没有 venv 就退回当前解释器
    CIO_PY = sys.executable

_STAGE = re.compile(r"\[STAGE\]\s+(\S+)(?:\s+\|\s+(.*))?$")

# 期望的阶段顺序。**界面自己持有这张表**，引擎发的是命名事件而不是 n/N——
# 闸门拦下只走 5 步、完整跑走 12 步，硬套一个分母就要维护两套计数。
UNIT_A_STAGES = [
    ("start", "接到指令"),
    ("collect", "采集事实材料"),
    ("gate", "Evidence Gate 判定"),
    ("panel", "量化证据面板"),
    ("debate_bull_r1", "多头独立建案"),
    ("debate_bear_r1", "空头独立建案"),
    ("debate_bull_r2", "多头反驳"),
    ("debate_bear_r2", "空头反驳"),
    ("judge", "裁判论证审计"),
    ("synthesis", "综合出观点"),
    ("done", "完成"),
]
# 闸门拦下时走的另一条。**必须是另一条，不是同一条走一半**：
# 界面上"停在第 3 步不动"和"第 3 步之后主动结束"看起来一样，
# 前者是卡死，后者是系统正常工作。
UNIT_A_BLOCKED_STAGES = [
    ("start", "接到指令"),
    ("collect", "采集事实材料"),
    ("gate", "Evidence Gate 判定"),
    ("panel", "量化证据面板"),
    ("gate_blocked", "无实质材料，一部主动弃权"),
    ("done", "结束"),
]

JOBS: dict = {}
_LOCK = threading.Lock()


def _new_job(kind: str, label: str) -> str:
    jid = uuid.uuid4().hex[:8]
    with _LOCK:
        JOBS[jid] = {"kind": kind, "label": label, "run_id": "", "stages": [],
                     "seen": set(), "status": "running", "result": None,
                     "error": "", "log": [], "started": time.time(),
                     "finished": None, "returncode": None}
    return jid


def _upd(jid: str, **kw):
    with _LOCK:
        JOBS[jid].update(kw)


def get(jid: str) -> dict:
    with _LOCK:
        j = JOBS.get(jid)
        return dict(j) if j else {}


def elapsed(jid: str) -> float:
    j = get(jid)
    if not j:
        return 0.0
    return (j.get("finished") or time.time()) - j["started"]


def _drain_stderr(jid: str, proc):
    """逐行读 stderr，认出阶段事件；其余当日志留着。

    **日志要留住。** 跑挂的时候界面只显示"失败"是没法查的，
    而这些行里常常就写着原因（"基准取不到"、"RSS 失败"、"CRO 否决"）。
    """
    for raw in proc.stderr:
        line = raw.rstrip("\n")
        m = _STAGE.search(line)
        if m:
            name, detail = m.group(1), (m.group(2) or "")
            with _LOCK:
                j = JOBS[jid]
                if name == "run_id":
                    j["run_id"] = detail.strip()
                else:
                    j["stages"].append({"name": name, "detail": detail,
                                        "at": time.time()})
                    j["seen"].add(name)
        else:
            with _LOCK:
                JOBS[jid]["log"].append(line)
                if len(JOBS[jid]["log"]) > 400:
                    del JOBS[jid]["log"][:200]


def _run(jid: str, args: list, env_extra: dict):
    env = dict(os.environ)
    env.setdefault("PYTHONUNBUFFERED", "1")       # 不缓冲，阶段事件才是"实时"的
    env.update(env_extra or {})
    try:
        proc = subprocess.Popen([CIO_PY] + args, cwd=str(CIO_HOME),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, env=env, bufsize=1)
    except Exception as e:                        # noqa: BLE001
        _upd(jid, status="failed", error=f"起不了进程：{e}")
        return
    t = threading.Thread(target=_drain_stderr, args=(jid, proc), daemon=True)
    t.start()
    out = proc.stdout.read()
    proc.wait()
    t.join(timeout=3)

    # **契约：整段 stdout 一次 json.loads。** 不做"从输出里找 JSON"这种清洗——
    # 那会把一个偷偷混进 stdout 的 print 变成"看起来能用"，
    # 于是接口坏了却没人知道。解析失败就是失败，把原文留给人看。
    try:
        result = json.loads(out) if out.strip() else None
    except json.JSONDecodeError as e:
        _upd(jid, status="failed", finished=time.time(), returncode=proc.returncode,
             error=f"stdout 不是合法 JSON（契约被打破了）：{e}",
             result={"_raw_stdout": out[:4000]})
        return
    if result is None:
        _upd(jid, status="failed", finished=time.time(), returncode=proc.returncode,
             error=f"进程没有任何 stdout 输出（退出码 {proc.returncode}）")
        return
    _upd(jid, status=result.get("status") or "completed", result=result,
         finished=time.time(), returncode=proc.returncode,
         run_id=result.get("run_id") or JOBS[jid]["run_id"])


def start(kind: str, args: list, label: str, env_extra: dict = None) -> str:
    jid = _new_job(kind, label)
    threading.Thread(target=_run, args=(jid, args, env_extra or {}),
                     daemon=True).start()
    return jid


# ---------------------------------------------------------------- 三个入口
def scan(symbols: list) -> str:
    return start("scan", ["run_scan.py"] + list(symbols) + ["--json"],
                 f"扫描 {len(symbols)} 只")


def research(symbol: str, force: bool = False) -> str:
    args = ["run_unit_a.py", symbol, "--json"] + (["--force"] if force else [])
    return start("unit_a", args, f"研究 {symbol}" + ("（强制复研）" if force else ""))


def portfolio(pid: str = "") -> str:
    args = ["run_pc.py", "--json"] + (["--portfolio", pid] if pid else [])
    return start("pc", args, "重算组合")


def stats() -> dict:
    """历史归因。这个是同步的——它只读库，毫秒级。"""
    try:
        r = subprocess.run([CIO_PY, "run_pc.py", "--stats"], cwd=str(CIO_HOME),
                           capture_output=True, text=True, timeout=60)
        return {"ok": r.returncode == 0, "text": r.stdout or r.stderr}
    except Exception as e:                        # noqa: BLE001
        return {"ok": False, "text": f"取不到：{e}"}


def engine_info() -> dict:
    return {"home": str(CIO_HOME), "python": CIO_PY,
            "ok": (CIO_HOME / "run_scan.py").exists()}
