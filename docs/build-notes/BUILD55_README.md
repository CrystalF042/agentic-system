# build55 — 证券二部重新定位为 Systematic Analytics

**一句话：** 二部不再宣称 alpha。它每天只做客观状态测量，交给 CRO 判断。
Top N Picks / Model Weight / 正式方向性投票全部停用，状态位写死为 **ABSTAIN**。

```
Unit B: Measure  →  CRO: Assess risk  →  Portfolio Construction: Size  →  CEO: Decide
```

---

## 安装

在 `cio-agent/` 目录下解压覆盖（13 个文件，全部是覆盖或新增，不删任何东西）：

```bash
cd ~/.openclaw/workspace/cio-agent
unzip -o ~/Downloads/cio-build55-systematic-analytics.zip
```

新增：`src/cio/analytics.py`、`src/cio/render_analytics.py`、
`config/analytics_thresholds.yaml`、`scripts/test_analytics.py`
覆盖：`models.py`、`ledger.py`、`fundamentals.py`、`cro.py`、`utils.py`、
`run_unit_b.py`、`run_cro.py`、`run_pilot.py`、`run_gate.py`

## 跑起来

```bash
source .venv/bin/activate

# 0) 自检（不联网，约 3 秒；60+ 项，每项验证一个已知答案）
python scripts/test_analytics.py

# 1) 一次性把研究收尾写进台账（幂等，可重复运行）
python run_gate.py closeout

# 2) 日常运行
CIO_MARKET=us python run_unit_b.py

# 小池子快速验（注意：百分位分母会同步变小，报告里会如实标 n）
CIO_MARKET=us CIO_UB_LIMIT=60 python run_unit_b.py

# 只要风险测量、跳过 SEC
CIO_MARKET=us CIO_AN_NO_FUND=1 python run_unit_b.py
```

**`CIO_MARKET=us` 必须带。** 只有 `watchlist_us.yaml` 里有 `companies:` 锚点公司；
不带的话关注池解析为空、报告 0 行。这种情况报告顶部会打红字警告，不会静默通过。

**SEC 基本面需要 `.env` 里有 `CIO_SEC_UA`**（SEC 合理使用要求带联系方式的 User-Agent）：

```
CIO_SEC_UA=Crystal Guo crystalguo42@gmail.com
```

没设也能跑，第 2 节会说明"为什么没有基本面"，风险测量不受影响。

> ⚠ 本次给 SEC 缓存加了 schema 版本（新增了流动比率、利息保障、资本开支同义名）。
> 首次运行会重新拉取 `raw-data/sec_facts/`，503 家约需 5–8 分钟，之后走缓存。

---

## 报告长什么样

四节，只显示关注池的几十只（但百分位在全 500 只上算）：

1. **Watchlist Risk Snapshot** — Vol_60d / DownVol_60d / Beta_250d / Corr_SPY_60d /
   MaxDD_250d / Px_vs_MA120 / Trail_12-1，每个都带百分位
2. **Fundamental Snapshot** — 杠杆 / 毛利率 / 营业利润率 / FCF margin / FCF/资产 /
   营收增长 / 流动比率 / 利息保障，带 `Filing accepted` 日期与 stale 标记
3. **Style & Exposure** — GICS 行业、主题、是否指数成分、Beta、百分位（有持仓时附组合层）
4. **Exceptions** — 只列越线的，并在区块底部印出**当期阈值原文**

### 六条钉死的规矩

| 规矩 | 实现 |
| --- | --- |
| 百分位必须带口径 | `48s` = 行业内，`91u` = 全域。行业内可用样本 < 15 只自动回退全域并改标 `u`；全域 < 10 只干脆不给 |
| 窗口写进字段名 | `Beta_250d` 不是 `Beta`。且**实际样本必须达到名义窗口的 80%**，否则返回空——不让次新股拿 60 根 K 线冒充"1 年最大回撤" |
| 不做任何平滑 | 无 shrinkage、无 winsorize。一平滑就从测量变成模型 |
| 缺失就是缺失 | 一律显示 `—`，绝不显示 0，绝不用行业中位数填补 |
| 阈值是设定不是结果 | 全部在 `config/analytics_thresholds.yaml`，且报告里原样印出来 |
| 不出现方向性措辞 | 自检里有一条专门扫全文，禁 buy/sell/overweight/outperform/recommend… |

改阈值只改 yaml，改完把 `version:` 也改一下（报告会印版本号）。

---

## 台账：CLOSED_FAIL 与 VOID 是两回事

`run_gate.py closeout` 写入两条收尾：

- **UB-US-008 → `CLOSED_FAIL`** — 量尺是好的，模型没通过（IC=−0.0176, t(HAC)=1.12, p=0.2642, n=44）。
  这是一条**证据**：它告诉未来"这个方向试过了，没有"。
- **UB-US-002 → `VOID`** — 那次检验用的验证器本身有缺陷，结论**无效**。
  既不算通过也不算证伪，因为我们根本没真正测过。
  记 VOID 而不是删除，是为了保留"我们曾经用坏尺子量过"这条记录。

已收尾的研究不能被后续批次改回 PASS/FAIL（只增不改，也包括不被改回去）。
纯净窗口 2016–2021 **仍然 pristine，一次都没花过**。

### 二部什么时候会恢复投票

不是"有任何一个因子通过闸门"就恢复。恢复要求
**生产集与实际驱动打分的因子集完全一致**（`ledger.alpha_vote_allowed`）。

原因：`unit_b.build_unit_b()` 永远按写死的五因子等权打分，它不读台账。
若只要一个因子通过就恢复，被复活的不是那个通过的因子，
而是那个**整体已被证伪的五因子合成模型**。不一致时弃权并说清差在哪。

---

## 本次修掉的 20 个缺陷

上一轮的教训是"七个验证器 bug，每一个都让结果显得更好"。这次先写代码、
再用两个独立审计跑了一遍，果然又抓出一批。**每一条都会产出一个看起来完全正常的错数**，
不会报错、不会崩溃——这才是它们危险的地方。全部已补回归测试。

### 会算出错数的（最严重）

1. **SEC 别名 `break` 把营收冻结在 2018 年之前。** 大量公司采用 ASC 606 后把
   `Revenues` 换成 `RevenueFromContractWithCustomer…`，旧标签只剩历史数据。
   命中一个就停 → 营收停在 2017 年、毛利取 2025 年 → **毛利率算成 180%**，
   而 `Filing accepted` 显示最新、不触发 stale，完全看不出问题。现在遍历全部同义名并按期合并。
2. **分子分母跨期无检查。** 2026 年的资产 ÷ 2022 年的收入，算出来是个正常百分数。
   现在两端期末相差超过 200 天即返回空。
3. **资本开支缺标签被当成 0** → FCF 直接等于经营现金流，重资产公司虚高一倍以上，
   还稳稳排进 FCF 百分位前列。现在缺就是缺，并补了同义名。
4. **`max_dd_250d` 可以用 60 根 K 线算出来。** 回撤在短窗口天然更浅 →
   次新股显示成全行业最抗跌，且永远触发不了回撤红线。现在样本不足 80% 即返回空。
   `beta_250d` 同理（原来 83 根就出数）。
5. **一个 NaN 价格让 Beta 变成 NaN。** NaN 通过 `is not None` 混进横截面，
   `_rank_pct` 把它算成 **0 分位**，还因为"永远不满足 v < value"而
   **把所有干净标的的百分位一起压低**（示例：90th → 56th）。
   现在脏价格整行剔除，所有测量值收敛为有限数或 None。
6. **比率封顶值当成测量结果。** 1000% 的封顶杠杆是捏造的数字，且会稳居百分位榜首。现在触顶即返回空。
7. **组合区块选错账户后整块消失。** 取 `pos[0]` 拿到的是 SQLite 返回顺序（通常是一部），
   于是二部持有的美股被判"本市场无可定价持仓"，组合层完全不渲染，还宣称只有 1 笔持仓。
   现在排除影子盘、选可定价持仓最多的账户，并说明还有哪些账户没显示。
8. **价格陈旧完全没有检查。** 基本面有 stale 一整类异常，价格却没有——
   一条停更两周的序列会让波动率/回撤/Beta 全部停在两周前，而 px_last 还被组合市值直接乘上去。
   新增 `price_stale` 异常类（按交易日数，不按自然日）。
9. **尾随收益差一天。** 起点应是 `-(lookback+1)`。若那天正好是财报跳空，整个字段平移一个跳空幅度。

### 会让整条日链停摆的

10. **台账文件损坏 → `run_cro` / `run_pilot` / `run_unit_b` 全部 exit 1。** 一个记账文件
    能杀掉当天所有报告，而二部本来就已经弃权。现在 `production_factors()` 失败方向是**弃权**
    （返回空 + 大声报错），日链继续；且原因会如实报"台账不可读"，不会伪装成"生产集为空"。
11. **`ledger.save()` 非原子写。** 崩在半路会留下一个让此后每次运行都失败的半截 YAML，
    而它不可重建。现在 tmp + replace，并留 `.bak`。
12. **`load()` 返回模块全局的浅拷贝。** 删掉台账后新注册的研究里仍带着上一次的研究。现在深拷贝。
13. **`windows:` 为空会 KeyError。**

### 会让报告自相矛盾的

14. **生产集非空时，报告仍印"ABSTAIN / 无已验证模型"**，而同一天 CRO 已经在投方向性票——
    公然矛盾，且正好发生在最要紧的分支。状态位现在由台账推导。
15. **已 VOID 的研究能被后续批次改回 PASS** 并重新进入生产集。已加守卫。
16. **PDF 印 ¹²³ 但没有图例。** 推送到 Telegram 的是 PDF，Markdown 只留在磁盘上——
    带标记的恰好是最需要说明的那几行。现在两边图例一致。
17. **饱和度提示把"配对数"当"标的数"**：12 只标的的 66 个相关性配对被印成
    "66 of 35 displayed names"。
18. **`CIO_MARKET` 没设时报告 0 行、且看起来一切正常。** 现在顶部红字警告。
19. **`run_gate.py library` 把五个已被证伪的因子标成 `[生产集]`**，同屏下一行又说生产集为空。
20. **`CIO_QUANT_MOCK=1` 的冒烟跑会把合成价真的写进财务台账**，而当日幂等保护
    随后让**真正那次运行**以为今天已记过账。现在直接拒绝执行（需显式 `CIO_PILOT_ALLOW_MOCK_BOOK=1`）。

顺带修的：CRO 在两线都无输入时报"分歧偏大（风险信号）"——从空集合里造风险信号；
组合市值没有货币单位；相关性列头写死 SPY；同一分钟内 us/cn 两份报告文件名相撞互相覆盖。

---

## 自检覆盖什么

`python scripts/test_analytics.py` —— 60+ 项，每项验证一个**已知答案**，不是"跑通就算过"：

- 年化波动、下行半标准差、−20% 回撤、尾随收益口径，全部对已知真值
- Beta 日期对齐：真值 1.5；**在测量窗口内挖掉 40 个交易日仍算出 1.49**，
  并同时验证按位置对齐确实会算错（对照组）
- PIT：财报公布前后取值切换、重述前后取值切换、stale 判定
- 百分位：升序口径、同分中点、行业最小样本回退、NaN 不进分布
- 阈值确实来自配置（放宽后不再触发）
- 渲染层全文扫描禁用的方向性措辞
- 第 9 节是上面 20 条缺陷的回归测试

---

## 还没做（有意留着）

- **UB-US-009 Universe Experiment（中小盘）** — roadmap 上标 `dormant`，不执行。
  中小盘会带来历史成分、退市、流动性、价差、交易成本、更差的数据质量、更多公司行为，
  很容易再投进去几周。
- **SEC 重复 load** — 已加同一次运行内的内存缓存，够用。
- 组合层的 factor/style 集中度 —— 等 Portfolio Construction 真有组合了再说。
