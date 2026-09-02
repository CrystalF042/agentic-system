# build58 — 三个文件的小补丁（在 build57 之上）

build57 的报告基本对了：日期四处同源、Exceptions 收敛成功、
基本面从一片空白填到基本完整、MRNA 那个数字也问出来了。

但这份真实报告又暴露两件事，都跟基本面有关。改动只有三个文件。

## 安装

```bash
cd ~/.openclaw/workspace/cio-agent
unzip -o ~/Downloads/cio-build58-fundamentals-coverage.zip -d ~/Downloads/_b58
cp -R ~/Downloads/_b58/cio-agent/. .
python scripts/test_analytics.py        # 100+ 项
CIO_MARKET=us python run_unit_b.py
```

> ⚠ SEC 缓存 schema 3→4（改了权益科目的取法），会再重拉一次约 4 分钟。
> 这次是真的最后一次——除非以后再加科目。

---

## 一、`n/a` 标记根本没生效，原因不是标记写错了

build57 报告里 NVO / ARM / ASML / TSM 仍然是一排 `—`，Filed 也是 `—`，
没有出现说好的 `n/a` 和 `20-F / IFRS`。

我以为是渲染问题，查下来不是。**是这四只根本没进基本面流程。**

CIK 来自 S&P 500 成分表。这四只是关注池里的**非成分**标的，
在 `build_analytics` 里是这样造出来的：

```python
Stock(code=tk, name=nm, yahoo=tk, focus_theme=themes)   # 没有 cik
```

没有 CIK → `load_company()` 根本不会被调用 → 它们压根不在 `fund_all` 里
→ `f is None` → `no_us_gaap` 永远是 False → 标记不触发。

**我给这四个名字专门做的标签，恰好对这四个名字不生效。**

修法不是补标记，是补数据入口：接上 SEC 官方的 ticker→CIK 清单
（`https://www.sec.gov/files/company_tickers.json`，免费公开，一次请求，本地缓存 30 天）。
成分表没给 CIK 的标的，用它补齐后再走一遍正常流程。

这样有两种结果，都比现在好：

- 该公司**确实报 us-gaap** → 直接就有真实基本面数据（有些外国发行人是报 us-gaap 的）
- 报 IFRS → 记录取到了但没有任何 us-gaap 事实 → **正确识别为覆盖范围之外**，
  显示 `n/a` 和 `20-F / IFRS`，标记这次会真的触发

顺带把计数改对了。之前是 `成功 503，缺失 4`，现在分三类：

```
us-gaap=... foreign(20-F/IFRS)=... unavailable=...
```

"取到了记录但里面没有 us-gaap 事实"和"取不到"是两件事，不该合并成一个"成功"。

---

## 二、ABBV 杠杆 104% —— 数字大概率是对的，但算法有个系统性偏差

先说结论：**104% 本身不是错误。** 杠杆超过 100% 意味着股东权益为负，
即负债大于资产。这对 AbbVie 这类做过大额回购、或收购带来大量商誉和债务的公司是常态，
不是数据问题。报告现在会在表下写明这一点，免得下次看到又要查一遍。

但反推公式有个偏差需要修。资产负债表恒等式是：

```
资产 = 负债 + 【全部】权益（含少数股东权益 NCI）
```

而 us-gaap 里 `StockholdersEquity` 是**母公司口径**，不含 NCI。
用它反推 `负债 = 资产 − 母公司权益`，等于把少数股东权益也算进了负债，
**杠杆被系统性高估**。NCI 占比大的公司（有大量并表子公司的）偏得更多。

现在优先取 `StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest`，
取不到再退回母公司口径。这一条只影响带 `*` 的反推值，
直接披露 `Liabilities` 的公司不受影响。

自检里加了个具体例子：资产 1000、母公司权益 200、含 NCI 权益 300
→ 正确答案 70%，用错科目会得到 80%。

---

## 顺带确认：build57 这几处在真实数据上是对的

- **MRNA 问出来了**：`+177% on 2026-08-19 accounts for 83% of the 60-day variance
  (Vol_60d reads 229%)`。一天涨 177%。**请你去看 8/19 那天** —— 真事件还是未复权拆股，
  只有你能判断，代码不该替你判断。
- **FCF/Ast 回来了**：AMAT 13.1%、MU 1.2%、AAPL 25.8%、QCOM 22.3%，
  上次这四个全是空的。
- **杠杆和毛利率填上了**：ABBV、AMGN、GILD、LLY、MRK、AMD、INTC、AMZN、META、GOOGL
  等十几只，全部带 `*`（反推）。
- **Exceptions 收敛成功**：Drawdown 17 条 → 5 条 + 一行点名，Beta 16 条 → 5 条 + 一行点名。
- **日期四处同源**：`asof20260824` / `As-of trade date 2026-08-24` /
  `snapshot sp500_2026-08-24` / `run_id an-us-20260824-1932`。

还有一处值得你单独看一眼，**它不是缺陷而是发现**：

```
AMAT–ASML 0.85   AMAT–KLAC 0.91   AMAT–LRCX 0.93
AMD–LRCX  0.85   ASML–KLAC 0.87   ASML–LRCX 0.89
KLAC–LRCX 0.90   LRCX–MU   0.87
```

半导体设备这一组 60 日日收益相关性 0.85–0.93。
它们在报告里是 8 只独立标的，在风险上基本是**一只**。
这正是新二部该交给 CRO 的那类事实 —— 而它是老二部（打分排序）永远看不见的。
