"""资金面采集（趋势视角核心）：北向资金 / 行业板块资金净流入。
akshare 接口名各版本有差异、且部分数据源时有中断——本模块全程降级容错，
取不到就在"数据采集状态"如实标注，绝不编造。"""
from __future__ import annotations

from .config import MARKET
from .models import FundFlow
from .utils import get_logger

log = get_logger("cio.funds")


def _try(fnnames: list[str]):
    """按名尝试取 akshare 函数，返回第一个存在的可调用对象或 None。"""
    try:
        import akshare as ak
    except Exception:
        return None
    for n in fnnames:
        fn = getattr(ak, n, None)
        if callable(fn):
            return fn
    return None


def northbound(status: dict) -> list[FundFlow]:
    """北向资金【日度净额】自 2024-08-19 起交易所已停止披露；akshare 现在返回的值时0时负时正、
    不可靠且无从核验。按"零幻觉"原则一律不展示，改用其它在披露的资金面指标（见 collect_funds）。"""
    status["北向资金"] = "已停披露(2024-08，不展示)"
    return []
    # ↓ 历史实现保留备查（不再执行）
    fn = _try(["stock_hsgt_fund_flow_summary_em", "stock_hsgt_north_net_flow_in_em"])
    if fn is None:
        status["北向资金"] = "不可用(接口缺失)"
        return []
    try:
        df = fn()
        if df is None or len(df) == 0:
            status["北向资金"] = "空"
            return []
        # 尽量抽取"净流入/净买额"列，格式化为事实字符串
        txt = None
        numval = None
        for col in df.columns:
            if any(k in str(col) for k in ("净流入", "净买额", "净额", "成交净买额")):
                try:
                    val = df[col].iloc[-1]
                    try:
                        numval = round(float(val), 2)       # 去掉浮点噪声 30.376983000000003 → 30.38
                        txt = f"{col} {numval:,.2f}亿元"
                    except (TypeError, ValueError):
                        txt = f"{col}: {val}"
                    break
                except Exception:
                    continue
        # 北向资金日度净额自 2024-08-19 起已停止披露；取到 0/空一律视为不可用，
        # 避免在简报里显示误导性的"0.00亿元"（那不是真的零流入，而是数据不再公布）。
        if txt is None or (numval is not None and abs(numval) < 0.01):
            status["北向资金"] = "不可用(2024-08起停止披露日度数据)"
            return []
        status["北向资金"] = "ok"
        return [FundFlow(name="北向资金", value=str(txt)[:200], source="akshare", trend_tag="资金面")]
    except Exception as ex:
        status["北向资金"] = f"失败({type(ex).__name__})"
        return []


def sector_flow(status: dict, top: int = 5) -> list[FundFlow]:
    """行业板块资金净流入排行（今日）。取净流入前 top 与净流出前 top。"""
    fn = _try(["stock_sector_fund_flow_rank"])
    if fn is None:
        status["板块资金"] = "不可用(接口缺失)"
        return []
    try:
        try:
            df = fn(indicator="今日", sector_type="行业资金流")
        except TypeError:
            df = fn()
        if df is None or len(df) == 0:
            status["板块资金"] = "空"
            return []
        name_col = next((c for c in df.columns if "名称" in str(c)), df.columns[1] if len(df.columns) > 1 else df.columns[0])
        flow_col = next((c for c in df.columns if "净额" in str(c) or "净流入" in str(c)), None)
        out: list[FundFlow] = []
        if flow_col is not None:
            df2 = df.copy()
            try:
                df2 = df2.sort_values(by=flow_col, ascending=False)
            except Exception:
                pass
            heads = df2.head(top)
            tails = df2.tail(top)
            inflow = ", ".join(f"{r[name_col]}({r[flow_col]})" for _, r in heads.iterrows())
            outflow = ", ".join(f"{r[name_col]}({r[flow_col]})" for _, r in tails.iterrows())
            out.append(FundFlow(name="行业净流入 Top", value=inflow[:220], source="akshare", trend_tag="资金面"))
            out.append(FundFlow(name="行业净流出 Top", value=outflow[:220], source="akshare", trend_tag="资金面"))
        status["板块资金"] = "ok" if out else "降级"
        return out
    except Exception as ex:
        status["板块资金"] = f"失败({type(ex).__name__})"
        return []


def market_turnover(status: dict) -> list[FundFlow]:
    """两市成交额（沪市+深市上一交易日总成交额）——北向停更后的资金活跃度替代指标。
    复用已验证可用的 A股指数日线接口取 成交额：沪=上证综指(覆盖全沪市)、深=深证综指(覆盖全深市)。
    单位口径不确定时按'元'折算亿元，并用合理区间(1000~60000亿)兜底——
    只有落在区间内才展示，否则不显示、如实标注，绝不糊一个错量级的数。"""
    try:
        import akshare as ak
    except Exception:
        status["两市成交额"] = "akshare 未安装"
        return []
    fn = getattr(ak, "stock_zh_index_daily_em", None) or getattr(ak, "stock_zh_index_daily", None)
    if not callable(fn):
        status["两市成交额"] = "接口缺失"
        return []

    def _last_amount(code: str):
        try:
            df = fn(symbol=code)
            acol = next((c for c in ("amount", "成交额", "成交金额") if c in df.columns), None)
            dcol = next((c for c in ("date", "日期") if c in df.columns), None)
            if not acol:
                return None
            if dcol:
                df = df.sort_values(by=dcol)
            vals = []
            for v in df[acol].tolist():
                try:
                    f = float(v)
                    if f == f and f > 0:      # 非 NaN、正数
                        vals.append(f)
                except Exception:
                    pass
            return vals[-1] if vals else None
        except Exception:
            return None

    sh = _last_amount("sh000001")                      # 上证综指=全沪市
    sz = _last_amount("sz399106") or _last_amount("sz399001")  # 深证综指(全深市)，退而求其次深证成指
    parts = [x for x in (sh, sz) if x]
    if not parts:
        status["两市成交额"] = "取数失败"
        return []
    raw = sum(parts)
    yi = raw / 1e8                                     # 假设原始单位为元 → 亿元
    if not (1000 <= yi <= 60000):                      # 落在两市成交额合理区间才展示
        status["两市成交额"] = f"单位待校准(raw={raw:.4g})"
        return []
    label = "两市成交额" if len(parts) == 2 else "沪市成交额(深市暂缺)"
    status["两市成交额"] = "ok"
    return [FundFlow(name=label, value=f"{yi:,.0f}亿元", source="akshare", trend_tag="资金面")]


def collect_funds(status: dict) -> list[FundFlow]:
    """盘前资金面汇总。**先看市场：这三个指标都是 A 股口径的。**

    2026-09-02 那份美股简报的降级栏里挂着三条：

        两市成交额取数失败
        北向资金 已停披露（2024-08，不再计）
        板块资金会失败（ConnectionError）

    三条都不是故障 —— 是**在美股模式下去取 A 股的资金面**。
    akshare 那几个接口本来就不认美股，于是每天必然失败三次，
    然后作为"降级"印在报告抬头上。

    这比静默失败好一点（至少它说了），但仍然是噪音：真正的降级
    （比如某个新闻源死了）会被这三条常驻的假降级淹掉。
    而且它掩盖了一个真问题——**美股这一栏我们目前根本没有指标**。

    所以 us 模式如实返回空，并把"为什么空"写进 status，
    而不是每天失败三次假装在尝试。
    """
    if MARKET == "us":
        status["资金面"] = ("us 模式：A股口径的两市成交额/北向/板块资金不适用；"
                           "尚未接入免费的美股资金面指标（**这是缺口，不是故障**）")
        return []
    flows: list[FundFlow] = []
    flows += market_turnover(status)      # 资金面主指标：两市成交额（活跃度）
    flows += northbound(status)           # 北向已停披露：返回空，仅记状态
    flows += sector_flow(status)          # 板块资金：可能降级
    return flows
