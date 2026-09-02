# build60 — Leverage 改名为 Liab/Assets（含 build59，不用分开装）

> **累积包**。如果 build59 还没装，直接装这个就够。

```bash
cd ~/.openclaw/workspace/cio-agent
unzip -o ~/Downloads/cio-build60-liab-assets.zip -d ~/Downloads/_b60
cp -R ~/Downloads/_b60/cio-agent/. .
python scripts/test_analytics.py        # 120+ 项
CIO_MARKET=us python run_unit_b.py
```

**不动 SEC 缓存 schema，不会重拉。**

---

## 一、指标名与公式不一致（你标的必修项）

报告写的是 `Leverage = debt / assets`，脚注写的是 `Liabilities = Assets − Equity`，
两者不是同一个东西。总负债里除有息债务外还有应付账款、预收款项、递延收入、
应交税费、租赁负债、养老金负债。**数值算得对，说的不是同一件事。**

按你选的方案 A，把名字改成它的真名。改动是全链条的，不只是标签：

| 层 | 之前 | 现在 |
| --- | --- | --- |
| 快照字段 | `leverage` | `liab_assets` |
| 模型字段 | `AnalyticsRow.leverage` | `AnalyticsRow.liab_assets` |
| 异常类别 | `leverage` | `liab_assets` |
| 异常文案 | `ABBV debt/assets 104%` | `ABBV total liabilities/assets 104%` |
| 报告列头 | `Leverage` | `Liab/Assets`（PDF 里 `Liab/Ast`） |
| 阈值配置 | `leverage_pctile` | `liab_assets_pctile`（旧键仍兼容读取） |
| 红线文案 | `leverage percentile > 90` | `liabilities/assets percentile > 90` |

**代码里的字段名也一起改了**，不只是显示层。留一个叫 `leverage` 的变量在下面，
下一个人照样会把它读成 debt/assets——名字不一致本身就是缺陷的温床。

报告里现在明确写出来：

> **Liab/Assets is TOTAL liabilities ÷ total assets — it is not debt/assets.** Total
> liabilities include payables, deferred revenue, lease and pension obligations and taxes;
> interest-bearing debt is only part of it. A true debt/assets ratio would require pulling
> short-term borrowings and long-term debt separately, and cannot be derived from assets
> minus equity.

最后那句是关键：**debt/assets 不可能由资产减权益倒推出来**，必须单独取
`ShortTermBorrowings + LongTermDebtCurrent + LongTermDebtNoncurrent`。
写在代码注释里，防止以后有人图省事又走回头路。

ABBV 的 104% 现在不需要任何解释性修补——它就是总负债超过总资产、权益为负的真实状态。
超过 100% 那句说明保留，因为对 liabilities/assets 它本来就成立。

`config/analytics_thresholds.yaml` 里旧键 `leverage_pctile` 仍被兼容读取，
所以就算你有一份改过的旧配置也不会静默丢掉这条红线；但请改用新键。

---

## 二、星号改到格子上（你说不是阻塞项，一并做了）

之前 `ABBV*` 挂在 ticker 上，看不出是负债率反推的、毛利率反推的、还是两个都是。

底层原因是 `derived_fields` 存的是公式串（`"Liabilities=Assets-Equity"`），
渲染层无从知道它对应哪一列。现在改存**输出字段名**（`"liab_assets"` / `"gross_margin"`），
星号就能精确打到格子上：

```
| ABBV | 2026-08-03 | 21d | 104%* | 96s | 70% | 25% | ...
```

104% 带星（负债由恒等式反推），70% 不带（毛利率是直接披露的）。

---

## 三、build59 的内容（如果跳过了那个包）

**脚注标记 `⁵` 在 PDF 里渲染成空白。** 实测同一条管线：¹²³⁴ 能出，⁵(U+2075) 不在字体里。
带这个标记的恰好是最需要解释的两行（NVO/TSM）。改成 `[1]`…`[5]` 纯 ASCII，
并加了一条硬约束：脚注标记不允许含非 ASCII 字符。

**ASML 的 `180d !` 是误判。** 它是外国私人发行人，只报年报（20-F），180 天完全正常，
那个 `!` 说的是"它晚了"，而事实不是。现在按每家公司**自己的申报节奏**校准：

| 申报制度 | 自身节奏 | 陈旧线 | 180 天时 |
| --- | ---: | ---: | --- |
| 季报 | ~90 天 | 135 天 | 标记 ✓ |
| 年报（20-F） | ~365 天 | 547 天 | 不标记 ✓ |

数字有多旧照常显示（`180d` 那个信息一点没少），变的只是"这是否反常"的判断。
真拖过 1.5 轮仍然会标。节奏样本不足 3 次时退回固定线，不猜。

---

## 自检

120+ 项。新增第 14 节专门盯这次的改动：快照字段确实叫 `liab_assets`、
异常文案不含 `debt/assets`、表头是 `Liab/Assets`、报告里有那句显式声明、
星号打在 `104%*` 而不是 `ABBV*`、未反推的格子不带星号。
