"""证券一部 —— 论点台账与失效条件复检（Thesis Ledger）。

**这个模块存在的理由：让一部的观点可以被后续事实证伪。**

一部每天产出"投资论点 + 失效条件"。写下失效条件很容易，
但如果没有人回来检查，它就只是一段好看的文字——
一部会变成每天重新编一个故事、永远不会被检验的东西。
那正是 LLM 系统最容易退化成的样子：看着很厉害，实则无法评估。

所以这里做一个最小但真实的回路：

    今日观点 → 失效条件落库（OPEN）
                    ↓
    此后每天：把仍 OPEN 的失效条件取出来，与当日新事实逐条比对
                    ↓
    命中 → 标记 INVALIDATED，连同触发它的那条材料一起推给 CEO

**比对为什么用确定性关键词而不是再叫一次 LLM：**
让模型判断"这条失效条件是否已被触发"，等于给了它一个可以自由解释的裁量权，
而它有强烈的倾向去维护自己昨天写下的论点（sycophancy 与一致性偏好）。
确定性匹配会漏掉一些（召回率不高），但它**不会替你把已经触发的失效条件说成没触发**——
在这个位置，漏报远好过瞒报。命中的是提示，不是判决，最终仍由 CEO 看材料决定。
"""
from __future__ import annotations

import json
import re

from .db import connect
from .utils import get_logger

log = get_logger("cio.thesis")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS unit_a_thesis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT, as_of_date TEXT, subject TEXT, symbol TEXT,
    direction TEXT, conviction TEXT,
    thesis TEXT, catalysts TEXT, invalidations TEXT, panel TEXT,
    unverified INTEGER DEFAULT 0, status TEXT DEFAULT 'OPEN',
    closed_at TEXT, closed_reason TEXT, closed_evidence TEXT
);
CREATE INDEX IF NOT EXISTS ix_thesis_status ON unit_a_thesis(status, symbol);
"""

# 后加的列。**用 ALTER 而不是改 _SCHEMA**：CREATE TABLE IF NOT EXISTS 对
# 已存在的表是空操作，改 _SCHEMA 只会让新库有列、旧库永远没有，
# 而且不报错——又是一个静默失败。
# 记这两列是为了一个纵向问题：【我那些高信心的论点，是不是都建立在薄材料上？】
# 不落库就永远问不出来。
_ADD_COLUMNS = [
    ("material_verdict", "TEXT DEFAULT ''"),
    ("material_substantive", "INTEGER DEFAULT 0"),
    # build124：辩论可以跑在本地，也可以跑在 Claude。**台账必须记是谁写的。**
    # 不记的话，半年后这张表里一半论点出自另一个模型而没人知道是哪一半，
    # 于是「换 Claude 收益最大」永远停在判断，变不成事实。
    ("engine", "TEXT DEFAULT ''"),
]

# 复检时的停用词：这些词在任何一条新闻里都可能出现，
# 用它们做匹配会让每条失效条件天天"触发"，很快就没人看了。
_STOP = set("的 了 是 在 和 与 及 对 为 上 下 中 已 将 会 可能 预计 公司 股票 市场 投资 分析 报告 "
            "the a an of to in on for and or is are was were will be that this with from".split())
_NUM = re.compile(r"\d+(?:\.\d+)?%?")


def init() -> None:
    # db.connect() 是【上下文管理器】，退出时自动 commit + close。
    # 直接当连接对象用会拿到 _GeneratorContextManager，方法全都不存在。
    with connect() as con:
        con.executescript(_SCHEMA)
        have = {r[1] for r in con.execute("PRAGMA table_info(unit_a_thesis)")}
        for col, decl in _ADD_COLUMNS:
            if col not in have:
                con.execute(f"ALTER TABLE unit_a_thesis ADD COLUMN {col} {decl}")
                log.info("论点台账已补列 %s", col)
        # 一次性回填。NO_CONDITIONS 规则是后加的，此前记录的无条件论点
        # 仍挂在 OPEN 里，会永远出现在"仍在监控中"列表却永远不可能被命中。
        # 幂等：改完就没有符合条件的行了。
        n = con.execute("UPDATE unit_a_thesis SET status='NO_CONDITIONS' "
                        "WHERE status='OPEN' AND (invalidations IS NULL OR "
                        "invalidations IN ('', '[]'))").rowcount
        if n:
            log.info("台账回填：%d 条无失效条件的论点移出 OPEN（它们永远不可能被复检命中）", n)


def record(*, as_of_date: str, subject: str, symbol: str, direction: str,
           conviction: str, thesis: str, catalysts: list, invalidations: list,
           panel: dict, unverified: int = 0, material_verdict: str = "",
           material_substantive: int = 0, engine: str = "") -> int:
    """登记今日观点。

    **没有失效条件的论点不进 OPEN。** 它按定义永远不可能被复检命中
    （复检就是逐条比对失效条件，没有条件就没有可比的东西），
    留在 OPEN 里只会年复一年地堆积，把真正在被监控的论点淹掉。
    所以状态记为 NO_CONDITIONS：**仍然入库可审计**（要看得见一部写过什么），
    但不参与每日复检、不出现在"仍在监控中"的列表里。
    这正是"没有新实质信息就不应产生新 thesis"的另一半：
    产生了但不可证伪的，也不该冒充在监控中。
    """
    from .utils import stamp_utc
    init()
    with connect() as con:
        # **一个标的同时只应该有一个 active thesis。**
        # 不这样做的后果在真机第五跑上直接看见了：同一天调试跑了四次，
        # 台账里就并排躺着 4 条 NVDA 看多|中，
        # "仍在监控中的既有论点"变成一份重复清单——
        # 它们不是四个观点，是同一个观点的四份草稿。
        #
        # thesis 是【当前观点】，不是【观点日志】。历史留在库里（status=SUPERSEDED，
        # 可审计、可回看一部改过几次主意），但只有最新那条参与每日失效复检。
        key, col = (symbol, "symbol") if symbol else (subject, "subject")
        old = con.execute(f"SELECT id, direction, conviction FROM unit_a_thesis "
                          f"WHERE status='OPEN' AND {col}=?", (key,)).fetchall()
        if old:
            # **方向翻了这件事要写进台账。** 只写"被取代"，历史里就看不出
            # 一部改过几次主意、每次是在什么证据条件下改的。
            # 有了这条，日后可以直接问：我有多少次方向改变是在零实质材料的日子发生的？
            flip = next((r for r in old if r["direction"] != direction), None)
            note = f"被 {as_of_date} 的新论点取代"
            if flip is not None:
                note += (f"（方向 {flip['direction']} → {direction}；"
                         f"本轮材料判定：{material_verdict or '未判定'}，"
                         f"实质 {material_substantive} 条）")
            con.execute(f"UPDATE unit_a_thesis SET status='SUPERSEDED', closed_at=?, "
                        f"closed_reason=? WHERE status='OPEN' AND {col}=?",
                        (stamp_utc(), note, key))
            log.info("取代 %d 条同标的旧论点（#%s）%s",
                     len(old), "、#".join(str(r["id"]) for r in old),
                     f"——方向 {flip['direction']} → {direction}" if flip is not None
                     else "——一个标的只保留一个 active thesis")
        cur = con.execute(
            "INSERT INTO unit_a_thesis(created_at,as_of_date,subject,symbol,direction,conviction,"
            "thesis,catalysts,invalidations,panel,unverified,status,"
            "material_verdict,material_substantive,engine) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (stamp_utc(), as_of_date, subject, symbol or "", direction, conviction,
             thesis[:8000], json.dumps(catalysts, ensure_ascii=False),
             json.dumps(invalidations, ensure_ascii=False),
             json.dumps(panel, ensure_ascii=False)[:20000], int(unverified),
             "OPEN" if invalidations else "NO_CONDITIONS",
             material_verdict, int(material_substantive), engine or ""))
        tid = cur.lastrowid
    log.info("一部论点已登记 #%d %s %s|%s，失效条件 %d 条（状态 %s），材料判定 %s（实质 %d 条）",
             tid, subject, direction, conviction, len(invalidations),
             "OPEN" if invalidations else "NO_CONDITIONS",
             material_verdict or "未判定", material_substantive)
    return tid


def open_theses(symbol: str = "") -> list:
    init()
    with connect() as con:
        if symbol:
            rows = con.execute("SELECT * FROM unit_a_thesis WHERE status='OPEN' AND symbol=? "
                               "ORDER BY id DESC", (symbol,)).fetchall()
        else:
            rows = con.execute("SELECT * FROM unit_a_thesis WHERE status='OPEN' "
                               "ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]


_CJK_RUN = re.compile(r"[一-鿿]+")
_LATIN = re.compile(r"[A-Za-z][A-Za-z\-]{2,}")


def _keywords(text: str) -> tuple:
    """把一段文字拆成 (特征集合, 数字集合)。

    **中文必须用字符二元组，不能用"标点之间的整段短语"。**
    中文没有空格，`[一-鿿]{2,}` 是贪婪匹配，抓到的是整句：
        条件  "下季度数据中心营收同比转负"      → 一个 token
        新闻  "…，数据中心营收同比转负，…"      → 另一个 token
    两个 token 字符串不相等，重合永远是 0——复检会**永远报"无失效"**，
    而且完全看不出错：日志正常、流程正常、只是这个回路从来没工作过。
    这正是本项目最忌讳的静默失败，所以改成二元组：
        "毛利率跌破" → 毛利 / 利率 / 率跌 / 跌破
    新闻里出现"英伟达毛利率跌破 40%"时四个全中。

    数字单独拿出来：'毛利率跌破 40%' 里的 40% 是这条条件最硬的锚点。
    """
    feats = set()
    for run in _CJK_RUN.findall(text or ""):
        if run in _STOP:
            continue
        for i in range(len(run) - 1):
            bg = run[i:i + 2]
            if bg not in _STOP:
                feats.add(bg)
        if len(run) == 1:
            feats.add(run)
    for w in _LATIN.findall(text or ""):
        wl = w.lower()
        if wl not in _STOP:
            feats.add(wl)
    return feats, set(_NUM.findall(text or ""))


def check(new_facts: list, symbol: str = "", min_cover: float = 0.55) -> list:
    """用当日新事实复检仍 OPEN 的失效条件。

    new_facts: [{"text": ..., "url": ..., "source": ...}]
    命中判据（刻意保守）：该条件的特征有 ≥ min_cover 比例出现在这条新事实里；
    覆盖率略低（≥0.4）但**数字也对上**时同样算命中——
    "毛利率跌破 40%" 里的 40% 对上，本身就是很强的证据。
    用覆盖【比例】而不是绝对个数：条件长短差别很大，
    固定个数会让长条件几乎必中、短条件几乎不中。
    返回 [{thesis_id, subject, condition, fact, url, overlap}]，只提示不自动关闭——
    是否真的失效由 CEO 看材料决定，机器不替人下这个判断。
    """
    out = []
    theses = open_theses(symbol)
    if not theses or not new_facts:
        return out
    prepared = []
    for f in new_facts:
        t = (f.get("text") or "")
        if not t:
            continue
        kws, nums = _keywords(t)
        prepared.append((f, kws, nums))
    for th in theses:
        try:
            conds = json.loads(th.get("invalidations") or "[]")
        except Exception:
            conds = []
        for cond in conds:
            ckw, cnum = _keywords(cond)
            if not ckw:
                continue
            for f, fkw, fnum in prepared:
                ov = ckw & fkw
                cover = len(ov) / max(len(ckw), 1)
                num_hit = bool(cnum & fnum)
                if cover >= min_cover or (cover >= 0.40 and num_hit):
                    out.append({"thesis_id": th["id"], "subject": th["subject"],
                                "direction": th["direction"], "condition": cond,
                                "fact": (f.get("text") or "")[:200],
                                "url": f.get("url", ""), "source": f.get("source", ""),
                                "coverage": round(cover, 2), "number_matched": num_hit})
                    break                      # 一条条件命中一次即可，不重复刷屏
    return out


def close(thesis_id: int, reason: str, evidence: str = "") -> None:
    """把论点标记为已失效。**只在 CEO 确认后调用**——机器负责提示，人负责判定。"""
    from .utils import stamp_utc
    init()
    with connect() as con:
        con.execute("UPDATE unit_a_thesis SET status='INVALIDATED', closed_at=?, "
                    "closed_reason=?, closed_evidence=? WHERE id=?",
                    (stamp_utc(), reason[:500], evidence[:1000], thesis_id))
    log.info("论点 #%d 已标记 INVALIDATED：%s", thesis_id, reason[:60])


def summary(limit: int = 20) -> str:
    init()
    with connect() as con:
        rows = con.execute("SELECT as_of_date,subject,direction,conviction,status,"
                           "invalidations FROM unit_a_thesis ORDER BY id DESC LIMIT ?",
                           (limit,)).fetchall()
    if not rows:
        return "（一部论点台账为空）"
    L = [f"{'日期':12} {'标的':16} {'方向':6} {'信心':4} {'状态':12} 失效条件数"]
    for r in rows:
        try:
            n = len(json.loads(r["invalidations"] or "[]"))
        except Exception:
            n = 0
        L.append(f"{r['as_of_date']:12} {str(r['subject'])[:16]:16} {r['direction']:6} "
                 f"{r['conviction']:4} {r['status']:12} {n}")
    return "\n".join(L)


def open_brief(symbol: str = "", limit: int = 5) -> list:
    """仍 OPEN 的论点摘要，供【一部未启动】时的报告展示。

    未启动不等于没有观点——既有论点仍然有效、仍在被每日复检。
    报告把它们列出来，读者才知道"今天没有新研究"和"今天没有观点"是两回事。
    """
    out = []
    for th in open_theses(symbol)[:limit]:
        try:
            conds = json.loads(th.get("invalidations") or "[]")
        except Exception:
            conds = []
        out.append({"id": th["id"], "as_of": th.get("as_of_date", ""),
                    "subject": th.get("subject", ""), "direction": th.get("direction", ""),
                    "conviction": th.get("conviction", ""),
                    "material_verdict": th.get("material_verdict", "") or "",
                    "invalidations": conds})
    return out


# ---------------------------------------------------------------- 方向漂移复检
# 失效条件复检问的是：**新事实是否推翻了旧论点。**
# 这里问的是相反的一半：**旧论点是否在没有新事实的情况下自己变了。**
#
# 真机上真的发生了：8/25 与 8/26 两次 --force 复研，基本面一格未变
# （毛利率、营业利润率、FCF/营收、ROE、营收同比连截止日都相同），
# 只有一天的价格波动，结论从「中性」翻成「看多」，
# 而两轮的 Evidence Gate 都判 INSUFFICIENT —— 零实质材料。
# 那不是市场变化，是采样噪声穿上了研究报告的外衣。
#
# **注意分档，不要狼来了。** 有新证据支撑的方向改变是正常的研究更新，
# 把它和无证据的翻转印成同一句警告，警告本身就会被忽略。
_CONV_RANK = {"强": 3, "中": 2, "弱": 1}


def drift_check(symbol: str, subject: str, direction: str, conviction: str,
                gate_level: str = "", n_substantive: int = 0) -> dict:
    """把本轮结论与仍 OPEN 的既有论点比对。无既有论点或无变化时返回 {}。"""
    key = symbol or subject
    if not key:
        return {}
    rows = open_theses(symbol) if symbol else [
        t for t in open_theses("") if t.get("subject") == subject]
    if not rows:
        return {}
    prev = rows[0]
    d_changed = (prev.get("direction") or "") != direction
    c_gap = abs(_CONV_RANK.get(prev.get("conviction") or "", 2) - _CONV_RANK.get(conviction, 2))
    # **门槛随证据分档。** 固定"跨两档才报"太松：真机上 11:17 与 11:35 相隔 18 分钟、
    # 零实质材料、面板未动，信心从「中」升到「强」——只差一档，于是没报。
    # 但在零证据的一轮里，**任何**变化都没有依据；有证据的一轮里，
    # 一档的调整本来就是正常的研究更新。所以门槛应该跟着证据走。
    c_min = 1 if gate_level == "INSUFFICIENT" else 2
    if not d_changed and c_gap < c_min:
        return {}

    if gate_level == "INSUFFICIENT":
        severity = "no_evidence"
    elif gate_level == "THIN":
        severity = "thin"
    else:
        severity = "supported"

    head = (f"本轮方向「{direction}」与既有论点 #{prev['id']}"
            f"（{prev.get('as_of_date', '')} 登记，{prev.get('direction', '')}｜"
            f"{prev.get('conviction', '')}）不一致"
            if d_changed else
            f"方向未变，但信心从「{prev.get('conviction', '')}」变为「{conviction}」"
            f"（既有论点 #{prev['id']}，{prev.get('as_of_date', '')} 登记）")

    # 方向翻转与信心跳档是两件事，尾句不能共用——
    # 对一次只是信心变化的记录说"翻不翻由 CEO 判断"，读起来就像模板。
    tail = ("翻不翻由 CEO 判断——但在零实质材料的一天里改变方向，"
            "通常是采样噪声，不是市场变化。" if d_changed else
            "同一批没有变化的数字上，信心自己变了——本轮没有任何新证据支持这个调整。")
    if severity == "no_evidence":
        text = (f"⚠ {head}，而本轮 Evidence Gate = INSUFFICIENT："
                f"**没有新证据支持这次改变**。{tail}")
    elif severity == "thin":
        text = (f"⚠ {head}。本轮仅 {n_substantive} 条实质材料，"
                f"支撑这次改变的证据有限。")
    else:
        text = (f"{head}。本轮有 {n_substantive} 条实质材料——"
                f"方向改变有新证据支撑，属于正常的研究更新。")

    return {"prev_id": prev["id"], "prev_date": prev.get("as_of_date", ""),
            "prev_direction": prev.get("direction", ""),
            "prev_conviction": prev.get("conviction", ""),
            "prev_material_verdict": prev.get("material_verdict", "") or "",
            "direction": direction, "conviction": conviction,
            "direction_changed": d_changed, "gate_level": gate_level,
            "n_substantive": int(n_substantive), "severity": severity, "text": text}
