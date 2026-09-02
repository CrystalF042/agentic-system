"""证券二部研究台账（机器可读）—— 记录假设、检验、以及【样本窗口的消耗】。

为什么要代码化：人是记不住"哪段数据已经看过"的。一旦某段历史被用来挑因子、
看方向、比相关性，它就永久变成 development data；此后任何基于这些观察构造的模型，
再用同一段数据都不能声称 out-of-sample。台账把这件事变成硬约束，而不是自律。

结构（research/ledger.yaml，人可读、可审计、只增不改）：
  windows:  每个时间窗的状态（pristine 未接触 / burned 已烧毁）与消耗记录
  studies:  每个研究 ID 的假设、预注册的通过标准、各阶段结果
"""
from __future__ import annotations

from datetime import datetime, timezone

from .config import BASE
from .utils import get_logger

log = get_logger("cio.ledger")

LEDGER_DIR = BASE / "research"
LEDGER_PATH = LEDGER_DIR / "ledger.yaml"

# 窗口划分：holdout 是稀缺资源，只在最终确认时一次性使用。
DEFAULT_LEDGER = {
    "version": 1,
    "windows": {
        "holdout": {"from": "", "to": "2021-12-31", "status": "pristine",
                    "note": "从未参与任何计算；仅供 Admission Gate 批次确认一次性使用",
                    "consumed_by": [], "use_count": 0},
        "development": {"from": "2022-01-01", "to": "", "status": "burned",
                        "note": "UB-US-001 已在此窗口上观察过因子方向与相关结构",
                        "consumed_by": ["UB-US-001"]},
    },
    "studies": {},
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load() -> dict:
    """读台账。**必须深拷贝**：曾经返回 dict(DEFAULT_LEDGER)（浅拷贝），
    studies / windows 指向模块全局对象，一次写入就永久污染进程内的缺省台账——
    表现为"删掉 ledger.yaml 后新注册的研究里仍带着上一次的研究"，
    在一个以'只增不改、可审计'为立身之本的模块里，这是最不能有的错误。"""
    import copy
    import yaml
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    if not LEDGER_PATH.exists():
        d = copy.deepcopy(DEFAULT_LEDGER)
        save(d)
        return d
    try:
        d = yaml.safe_load(LEDGER_PATH.read_text(encoding="utf-8")) or {}
        if not isinstance(d, dict):
            raise ValueError("台账根节点不是映射")
        w = d.get("windows")
        if not isinstance(w, dict) or not w:
            d["windows"] = copy.deepcopy(DEFAULT_LEDGER["windows"])
        else:                       # 单个窗口缺失也要补齐，否则 windows[...] 直接 KeyError
            for k, v in DEFAULT_LEDGER["windows"].items():
                if not isinstance(w.get(k), dict):
                    w[k] = copy.deepcopy(v)
        if not isinstance(d.get("studies"), dict):
            d["studies"] = {}
        return d
    except Exception as e:
        log.error("台账读取失败（拒绝继续，避免误判窗口状态）：%s", e)
        raise


def save(d: dict) -> None:
    """原子写入：先写临时文件再 replace，并保留一份 .bak。
    台账是唯一记录"哪段数据已被消耗"的地方；写到一半崩溃留下的半截 YAML
    会让之后每一次运行都读取失败，而它恰恰是不可重建的。"""
    import os as _os
    import yaml
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    text = ("# 证券二部研究台账（只增不改；windows 记录样本窗口消耗，studies 记录各研究）\n"
            + yaml.safe_dump(d, allow_unicode=True, sort_keys=False))
    tmp = LEDGER_PATH.with_suffix(".yaml.tmp")
    tmp.write_text(text, encoding="utf-8")
    if LEDGER_PATH.exists():
        try:
            LEDGER_PATH.replace(LEDGER_PATH.with_suffix(".yaml.bak"))
        except Exception:
            pass
    _os.replace(str(tmp), str(LEDGER_PATH))


def register(study_id: str, hypothesis: str, factors: list, horizons: list,
             criteria: dict, note: str = "") -> dict:
    """预注册一个研究。必须在任何检验【之前】登记——先定后测。
    重复注册同一 ID 会被拒绝（防止事后改假设或改标准）。"""
    d = load()
    if study_id in d["studies"]:
        raise ValueError(f"{study_id} 已注册，不可覆盖（先定后测：假设与标准一经登记不得修改）")
    d["studies"][study_id] = {
        "registered_at": _now(),
        "hypothesis": hypothesis,
        "factors": list(factors),
        "horizons": list(horizons),
        "criteria": dict(criteria),          # 预注册的通过标准，事后不可改
        "note": note,
        "development": None,
        "confirmation": None,
        "status": "REGISTERED",
    }
    save(d)
    log.info("已注册研究 %s", study_id)
    return d["studies"][study_id]


def get(study_id: str) -> "dict | None":
    return load()["studies"].get(study_id)


def record_development(study_id: str, result: dict) -> None:
    d = load()
    s = d["studies"].get(study_id)
    if not s:
        raise ValueError(f"{study_id} 未注册")
    s["development"] = {"at": _now(), **result}
    s["status"] = "DEVELOPED"
    save(d)


def holdout_consumed_by(study_id: str) -> bool:
    return study_id in (load()["windows"]["holdout"].get("consumed_by") or [])


def holdout_use_count(window: str = "holdout") -> int:
    """纯净窗口已被【批次】使用过几次。每多用一次，多重性就多一层，阈值必须相应收紧。"""
    return int(load()["windows"][window].get("use_count", 0) or 0)


def register_batch(batch_id: str, candidates: list, criteria: dict, note: str = "") -> dict:
    """预注册一个【确认批次】：把所有待确认候选一次性登记，之后一次性花掉 holdout。

    这是为了堵住"每个候选各自去 confirm 一次"的后门——那等于在 holdout 上做了 N 次检验，
    多重检验又从后门回来了，而且发生在最不该发生的地方。
    candidates: [{"study": .., "factor": .., "horizon": .., "dev_ic": ..}, ...]
    """
    d = load()
    d.setdefault("batches", {})
    if batch_id in d["batches"]:
        raise ValueError(f"批次 {batch_id} 已注册，不可覆盖（先定后测）")
    d["batches"][batch_id] = {
        "registered_at": _now(), "candidates": list(candidates),
        "criteria": dict(criteria), "note": note,
        "holdout_use_index": holdout_use_count() + 1,   # 这是第几次动用纯净窗口
        "confirmation": None, "status": "REGISTERED",
    }
    save(d)
    log.info("已注册确认批次 %s（%d 个候选，纯净窗口第 %d 次使用）",
             batch_id, len(candidates), d["batches"][batch_id]["holdout_use_index"])
    return d["batches"][batch_id]


def get_batch(batch_id: str) -> "dict | None":
    return load().get("batches", {}).get(batch_id)


def record_batch_confirmation(batch_id: str, result: dict, window: str = "holdout") -> None:
    """记录批次确认；把窗口使用次数 +1，并登记所有涉及的研究。"""
    d = load()
    b = d.setdefault("batches", {}).get(batch_id)
    if not b:
        raise ValueError(f"批次 {batch_id} 未注册")
    b["confirmation"] = {"at": _now(), "window": window, **result}
    b["status"] = "DONE"
    w = d["windows"][window]
    w["use_count"] = int(w.get("use_count", 0) or 0) + 1
    w["status"] = "burned"
    w["last_consumed_at"] = _now()
    lst = w.get("consumed_by") or []
    for c in b["candidates"]:
        sid = c.get("study")
        if sid and sid not in lst:
            lst.append(sid)
        s = d["studies"].get(sid)
        if s and s.get("closed"):
            # 已收尾（CLOSED_FAIL / VOID）的研究不得被后续批次复活。
            # 否则一个被判 VOID 的研究混进候选列表，就能翻回 PASS 并重新进入生产集，
            # 而 VOID 的含义恰恰是"那次检验根本不作数"。只增不改也包括不被改回去。
            log.warning("%s 已收尾（%s），批次结论不回写", sid, s.get("status"))
            s = None
        if s:                       # 把批次结论回写到各研究
            passed = any(t.get("passed") and t.get("study") == sid
                         for t in result.get("tests", []))
            s["confirmation"] = {"at": _now(), "window": window, "batch": batch_id,
                                 "passed": passed}
            s["status"] = "PASS" if passed else "FAIL"
    w["consumed_by"] = lst
    save(d)
    log.info("批次 %s 确认完成；纯净窗口累计使用 %d 次", batch_id, w["use_count"])


# ---------------- 研究收尾（CLOSED / VOID）----------------
# 为什么 VOID 必须与 FAIL 分开，不能合并、更不能删除：
#   FAIL = 量尺是好的，模型没通过。这是一条【证据】，它告诉未来"这个方向试过了，没有"。
#   VOID = 量尺本身有缺陷，那次检验的结论【无效】——既不能算通过，也不能算"证伪"，
#          因为我们根本没有真正测过。把 VOID 记成 FAIL 会让未来误以为某个假设已被排除。
#   删除 = 最糟。台账只增不改；删掉记录等于抹掉"我们曾经用坏尺子量过"这件事，
#          而这恰恰是最该留下的教训。
VALID_CLOSE = ("CLOSED_FAIL", "CLOSED_PASS", "VOID")


def close_study(study_id: str, status: str, reason: str, at: str = "") -> dict:
    """给一个研究收尾。status ∈ VALID_CLOSE。已收尾的研究不可再改（只增不改）。"""
    if status not in VALID_CLOSE:
        raise ValueError(f"非法收尾状态 {status}，只允许 {VALID_CLOSE}")
    d = load()
    s = d["studies"].get(study_id)
    if not s:
        raise ValueError(f"{study_id} 未注册，无法收尾")
    if s.get("closed"):
        log.info("%s 已收尾（%s），保持不变", study_id, s.get("status"))
        return s
    s["closed"] = {"at": at or _now(), "status": status, "reason": reason}
    s["status"] = status
    save(d)
    log.info("%s 收尾：%s —— %s", study_id, status, reason)
    return s


def void_study(study_id: str, reason: str) -> dict:
    """把一个研究标记为 VOID：那次检验所用的验证器有缺陷，结论无效。
    注意它**不释放**已消耗的样本窗口——数据看过就是看过，缺陷不能让历史回滚。"""
    return close_study(study_id, "VOID", reason)


def alpha_vote_allowed(model_factors: list) -> tuple:
    """二部今天可不可以投【方向性票】？返回 (可以吗, 原因)。

    这里刻意不是"生产集非空就恢复投票"。原来那样写有一个很难看见的后果：
    `unit_b.build_unit_b()` 根本不读台账，它永远按写死的五因子等权打分。
    于是只要**任何一个**因子通过闸门（比如只有低波过了），
    二部就会把那个【整体已被证伪的五因子合成模型】原样搬回决策链——
    被复活的不是那个通过的因子，而是那个失败的模型。
    自动恢复因此必须要求：生产集与实际驱动打分的因子集**完全一致**。
    不一致时弃权，并说清楚差在哪，等人来决定怎么建新模型。
    """
    try:
        load()
    except Exception as e:
        # 台账不可读时的答案是"不投票"，但**原因必须说对**。
        # 报成"生产集为空"会让人以为一切正常、只是没有因子通过——
        # 而实际情况是记账文件坏了，需要人去修。
        return False, f"research ledger is unreadable ({e}) — abstaining until it is repaired"
    prod = set(production_factors())
    if not prod:
        return False, "Production Factor Set is empty — no factor has passed the Admission Gate"
    want = set(model_factors or [])
    if prod != want:
        return False, (f"Production Factor Set {sorted(prod)} does not match the factor set that "
                       f"actually drives scoring {sorted(want)} — the scoring model was never "
                       f"validated as a whole. Build a model from the admitted factors first")
    return True, f"Production Factor Set {sorted(prod)} matches the scoring model"


def research_status() -> dict:
    """研究职能的当前状态。Production Factor Set 为空 → dormant（休眠，不驱动资金）。"""
    prod = production_factors()
    d = load()
    open_studies = [k for k, s in (d.get("studies") or {}).items() if not s.get("closed")]
    return {"production_factor_set": prod,
            "status": "active" if prod else "dormant",
            "open_studies": open_studies,
            "holdout_status": (d["windows"].get("holdout") or {}).get("status", ""),
            "holdout_use_count": holdout_use_count()}


def production_factors() -> list:
    """已通过准入闸的因子集（Production Factor Set）。未通过者永远留在 Research Library。
    收尾状态 CLOSED_FAIL / VOID 一律不进生产集。"""
    try:
        d = load()
    except Exception as e:
        # **失败方向必须是弃权，不是崩溃。**
        # 这个函数的唯一用途是回答"二部今天能不能投方向性票"。
        # 台账不可读时，正确答案是"不能"（返回空集），而不是让 CRO / Pilot
        # 整条链因为一个记账文件而停摆——尤其是二部本来就已经弃权。
        log.error("台账不可读，按【无已验证因子】处理（二部弃权）：%s", e)
        return []
    out = []
    for _sid, s in d.get("studies", {}).items():
        if s.get("status") in ("PASS", "CLOSED_PASS"):
            for f in s.get("factors", []):
                if f not in out:
                    out.append(f)
    return out


def record_confirmation(study_id: str, result: dict, window: str = "holdout") -> None:
    """记录最终确认，并把该窗口对本研究标记为已消耗（不可再用）。"""
    d = load()
    s = d["studies"].get(study_id)
    if not s:
        raise ValueError(f"{study_id} 未注册")
    if s.get("closed"):
        raise ValueError(f"{study_id} 已收尾为 {s.get('status')}，不得再写确认结果"
                         f"（只增不改；VOID/FAIL 不可被后续检验翻案）")
    s["confirmation"] = {"at": _now(), "window": window, **result}
    s["status"] = "PASS" if result.get("passed") else "FAIL"
    w = d["windows"][window]
    lst = w.get("consumed_by") or []
    if study_id not in lst:
        lst.append(study_id)
    w["consumed_by"] = lst
    w["status"] = "burned"                    # 一旦被任何研究确认过，即不再纯净
    w["last_consumed_at"] = _now()
    save(d)
    log.info("%s 最终确认完成：%s（窗口 %s 已标记消耗）",
             study_id, s["status"], window)


def window(name: str) -> dict:
    return load()["windows"][name]


# 2026-08 定稿的收尾决定。幂等：已收尾的不动，缺失的研究跳过并如实报告。
# 放在代码里而不是让人手改 yaml，是为了让"为什么这样收尾"和结论一起进版本库。
CLOSEOUT = [
    ("UB-US-008", "CLOSED_FAIL",
     "Quality Composite development FAIL: IC=-0.0176, t(HAC)=1.12, p=0.2642, n=44 (fwd20). "
     "Composite was weaker than its components; requiring all four fields narrows the "
     "cross-section to complete-reporting mature companies. Holdout not spent."),
    ("UB-US-002", "VOID",
     "Infrastructure test, not a research result. Registered and executed while the validation "
     "harness still contained defects (composite basis mismatch, position-indexed cross-sections). "
     "The measurement instrument was wrong, so the conclusion is neither a pass nor a refutation — "
     "the hypothesis was never actually tested. Recorded as VOID rather than deleted so the "
     "record of having measured with a faulty instrument survives."),
]


def apply_closeout() -> list:
    """幂等地应用已决定的收尾。返回 [(study, 结果)] 供调用方打印。"""
    out = []
    d = load()
    for sid, st, reason in CLOSEOUT:
        if sid not in (d.get("studies") or {}):
            out.append((sid, "未注册，跳过（本机台账没有这个研究）"))
            continue
        s = d["studies"][sid]
        if s.get("closed"):
            out.append((sid, f"已收尾：{s.get('status')}（保持不变）"))
            continue
        close_study(sid, st, reason)
        d = load()
        out.append((sid, f"已收尾 → {st}"))
    return out


def summary() -> str:
    d = load()
    L = ["窗口状态："]
    for k, w in d["windows"].items():
        L.append(f"  {k:12} {w.get('from') or '(起始)'} → {w.get('to') or '(至今)'}  "
                 f"[{w.get('status')}]  已消耗于：{', '.join(w.get('consumed_by') or []) or '无'}")
    L.append("研究：")
    for k, s in d["studies"].items():
        mark = "  ←已收尾" if s.get("closed") else ""
        L.append(f"  {k:14} {str(s.get('status')):12} {s.get('hypothesis','')[:46]}{mark}")
    if not d["studies"]:
        L.append("  （暂无）")
    rs = research_status()
    L.append(f"\n研究职能：{rs['status'].upper()}　·　Production Factor Set："
             f"{', '.join(rs['production_factor_set']) or '∅（空）'}")
    L.append(f"纯净窗口：{rs['holdout_status']}　·　已批次使用 {rs['holdout_use_count']} 次")
    if rs["status"] == "dormant":
        L.append("→ 二部不驱动任何资金配置；日常只出 Systematic Analytics（测量），"
                 "正式方向性投票 ABSTAIN。")
    return "\n".join(L)
