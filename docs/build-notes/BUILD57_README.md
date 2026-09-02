# build57 — 你评审的三条 follow-up，全部做了（含 build56，无需分开装）

> **这个包是累积的**：如果你还没装 build56，直接装这个就够了，build56 的内容都在里面。

## 安装

```bash
cd ~/.openclaw/workspace/cio-agent
unzip -o ~/Downloads/cio-build57-date-semantics.zip -d ~/Downloads/_b57
cp -R ~/Downloads/_b57/cio-agent/. .
python scripts/test_analytics.py        # 90+ 项，应全过
CIO_MARKET=us python run_unit_b.py
```

> ⚠ SEC 缓存 schema 2→3（build56 新增 `CostOfRevenue`），会重拉一次约 5 分钟。之后不再动。

---

## 第一优先（你标的上线阻塞项）：日期语义

**根因不是"哪里少写了一个字段"，而是业务日期用了机器所在时区。**

你在纽约 18:28 EDT 跑盘后分析，代码却用北京时间给成分快照和 run_id 命名——
北京当时已是次日 06:28。于是同一份报告里：

```
as-of trade date  2026-08-24     ← 对
snapshot          sp500_2026-08-25   ← 一个还没发生的交易日
run_id            an-us-2026-08-25-0628
```

数字全对，但审计记录自相矛盾。修法是把这条写成规则而不是打补丁：

**凡是业务凭证的身份（快照名、run_id、归档文件名），一律用市场时区的日期，
最好直接用 `as_of_trade_date`；UTC 时间戳只出现在 metadata。**

新增 `config.market_now()` / `config.market_date()`，从 `MARKET_PROFILE.tz` 取时区
（us → America/New_York，cn → Asia/Shanghai）。改了这些地方：

| 位置 | 之前 | 现在 |
| --- | --- | --- |
| universe 快照命名 | 北京日期 | 市场日期 |
| `run_id` | `file_stamp()`（北京） | `as_of` 交易日 + 市场时区 HHMM |
| 归档文件名 | 已经是 as_of | 不变，现在与 run_id 同源 |
| 身份登记表 first_seen | 北京日期 | 市场日期 |
| 公司行为回看窗 | 北京日期 | 市场日期 |
| 合成行情（冒烟）日期 | 北京日期 | 市场日期 |

最后一条不是洁癖：夹具自己用北京日期生成 K 线，会让离线冒烟出现
`as_of=8/25` 配 `snapshot=8/24`，那是测试自造的日期分歧，**会掩盖或伪造真正的问题**。

修完的实际输出，四处完全同源：

```
归档   UnitB_Systematic_Analytics+us+asof20260824+1911.md
正文   As-of trade date: 2026-08-24 · Generated: 2026-08-24T23:11:22Z / 2026-08-24 19:11 EDT
成分   snapshot sp500_2026-08-24
run_id an-us-20260824-1911
```

**还加了一条主动检查**：快照日期与 as_of 交易日不一致时报出来。
这种不一致是**合法的**（last-known-good 回退、或盘前跑），所以不强行拉平，
但必须可见——不能让它像这次一样悄悄存在。

---

## 第三优先：Exceptions 大面积越线时收敛

按你说的做了，阈值定在 **30%**：

```
**Drawdown**
> ⚠ 16 of 35 displayed names breach this threshold — treated as a broad watchlist
  condition, not 16 separate exceptions. At this breadth the line describes the current
  regime; whether it is still the right line is a setting in analytics_thresholds.yaml,
  not a result. Most extreme shown below.

- AMAT 1Y max drawdown -52%
- JNJ  1Y max drawdown -48%
- REGN 1Y max drawdown -45%
- NVDA 1Y max drawdown -45%
- ASML 1Y max drawdown -45%
- Also breaching: ARM, GILD, GOOGL, LLY, MRK, MRVL, MSFT, MU, NFLX, PFE, VRTX
```

16 行变 7 行，**没丢信息**：最极端的 5 条仍逐条展开，其余仍逐个点名，只是不重复整句。
排序用新增的 `extremity` 字段（每条异常在创建时记下越线幅度），
不是按字母序或出现顺序——"最极端的 5 个"必须真的是最极端的 5 个。

你那份 5 页的报告，这样应该能压回 3 页左右。

---

## 第二优先：外国发行人——做了一半，另一半在 roadmap

**做了的**：把"公司没披露"和"我们没覆盖"在视觉上分开。以前两者都是 `—`，看不出区别。

| 显示 | 含义 |
| --- | --- |
| `—` | 公司确实没标这个科目（申报我们读得到，这个行项目它不报） |
| `n/a` | **我们的 parser 覆盖不到这类申报**——20-F / IFRS 外国发行人 |

NVO / ARM / ASML / TSM 的 `Filed` 列现在直接写 `20-F / IFRS`，行首带 `⁵`，
表下写明：前者是公司的选择，**后者是我们的缺口，且在 roadmap 上**。

**没做的**：真正去解析 IFRS 分类的基本面。这需要读 `ifrs-full` 命名空间、
处理 IFRS 与 US GAAP 的科目映射（比如 IFRS 没有完全对应的 `GrossProfit`），
不是一个 build 能干净做完的事。ASML、TSM、ARM、NVO 在你的关注池里确实不是边缘资产，
所以这条我认同应该排进来，但要单独做，不该塞进这次。

顺带修的：build55 的 `成功 503，缺失 4` 计数偏乐观——加了 `_schema` 键之后，
空记录也变成非空字典从而被算作成功。现在按"有没有 us-gaap 事实"判定。

---

## build56 的内容（如果你跳过了那个包）

第一份真实报告暴露的四件事：

1. **`FCF/Assets` 在半数公司上被系统性抹掉** —— 流量÷存量的期末天然错开
   （年度 FCF 配最近一季资产负债表，可差近一年），而我用了统一的 200 天容差。
   现在按配对类型分开：同期口径收紧到 45 天，流量÷存量放宽到 400 天但仍不许资产早于流量。
2. **杠杆、毛利率两整列在很多公司上是空的** —— `Liabilities` 和 `GrossProfit`
   在 us-gaap 里都是可选标签。现在按恒等式反推（资产−权益、营收−成本），
   是精确值不是估算，报告里带 `*` 标出。
3. **MRNA 那个 229% 波动率** —— 一次 +100% 跳空就能把年化波动单独推到 140% 以上。
   新增测量：最大单日涨跌、发生在哪天、占窗口方差多少；超过 50% 出异常点名那一天。
   **代码不判断是真事件还是脏数据**，那是你的判断。**请去看一眼 MRNA 那天。**
4. **缓存复用** —— 长档位可截尾满足短请求（反向仍禁止，那是静默降级）。
   你现有的 `10y` 缓存会被日报直接复用，省掉每天一次全池 507 只下载。

---

## 关于 Beta 阈值：我按你的建议准备了，但没启用

你提的 `Beta > 1.8 AND > sector 90th percentile` 是对的方向——
不是"它是一只高 beta 半导体股"，而是"它在半导体内部也异常高 beta"。

我没有在这个 build 里改，原因是：**这是一次判断口径的变更，不是缺陷修复。**
改了之后 Beta 异常会从 16 条掉到大概 2–3 条，而这两三只是不是你真正想被提醒的，
只有你自己知道。现在的 breadth summary 已经把误导性消除了，所以不着急。

你说改我就改，一行配置的事——在 `analytics_thresholds.yaml` 里把 `beta: 1.80`
换成 `beta_abs: 1.80` + `beta_sector_pctile: 90`（两个条件同时满足才算异常）。

---

## 自检

`python scripts/test_analytics.py` 现在 90+ 项，新增两节：

**第 10 节（build56）**：期末配对三种情形、恒等式反推、单日主导波动识别、
外国发行人识别、缓存长短档位方向性。

**第 11 节（build57）**：`market_date` 确实取市场时区、快照命名不再用 `now_beijing`、
`run_id` 以 as_of 交易日为前缀、run_id 的日期段等于 as_of、
以及 breadth collapse（17 条收敛成 5 条 + 12 只点名，且最极端的确实排在最前）。
