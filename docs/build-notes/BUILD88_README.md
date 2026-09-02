# build88 —— Build 2：批准 → 成交 → 入账，外加 **Telegram 控制台**

先修了你遇到的那个崩溃，然后把"如何手动操作"这件事补齐了：
命令行有完整的批准 / 否决 / 执行入口，手机上有一个带按钮的控制台。

---

## 安装（单行）

```
cd ~/.openclaw/workspace/cio-agent && unzip -o ~/Downloads/cio-build88.zip -d . && .venv/bin/python scripts/check_build.py
```

看到 `全部 82 项通过` 再往下。

---

## 先说你遇到的那个崩溃

```
sqlite3.OperationalError: no such column: run_id
```

`pc_lineage` 的建表脚本里，`CREATE UNIQUE INDEX ... ON pc_lineage(run_id, ...)`
和 `CREATE TABLE IF NOT EXISTS` 写在同一段。你的库里那张表是旧版建的、没有
`run_id` 列，而 `CREATE TABLE IF NOT EXISTS` 对**已存在**的表什么都不做——
于是紧跟着的建索引直接报"没有这一列"，整段脚本抛异常。

**而补上 `run_id` 的那段迁移代码，就写在这段脚本的下一行。**
它永远执行不到——一段专门用来修这个问题的代码，被它要修的问题挡在门外，
报错信息还指向索引，不指向迁移。

修法是把顺序固定成 **建表 → 补列 → 建索引**，抽成 `db.ensure_columns()`，
`book` / `proposal_store` 也一并改成这个顺序，并加了探针：
用一张缺列的旧表跑 `init()`，必须补上列而不是抛异常。

---

## 现在怎么手动操作（命令行）

一天的完整链路，四条命令：

```
CIO_MARKET=us .venv/bin/python run_pc.py
CIO_MARKET=us .venv/bin/python run_rebalance.py
CIO_MARKET=us .venv/bin/python run_approve.py
CIO_MARKET=us .venv/bin/python run_execute.py
```

第三条不带参数就是**看待批清单**：

```
US_PAPER：待批准 2 条
  #2     AVGO   BUY   0 → 6 股  Δ +6  @ 312.00  决策日 2026-08-31  有效至 2026-09-04  合规 PARTIAL
  #4     QCOM   BUY   0 → 5 股  Δ +5  @ 324.00  决策日 2026-08-31  有效至 2026-09-04  合规 PARTIAL
```

然后：

```
.venv/bin/python run_approve.py --approve AVGO
.venv/bin/python run_approve.py --reject QCOM --reason "估值太贵，等回调"
.venv/bin/python run_approve.py --approve 2 4 7        一次批多条
.venv/bin/python run_approve.py --approve-all
.venv/bin/python run_approve.py --history 4            看这条的全部状态变更
```

`--history` 出来是这样，每一步都有人、有时间、有理由：

```
提案 #4 QCOM　BUY +5 股　决策日 2026-08-31　当前 REJECTED
  2026-08-31T16:59:19Z　PROPOSED → PENDING_APPROVAL　rb-20260831-165919-2af5
  2026-08-31T16:59:24Z　PENDING_APPROVAL → REJECTED　ceo:ap-20260831-165924-433b
      估值太贵，等回调
```

**合规破限的提案默认批不了**，要加 `--force`，而且强批这件事会写进事件日志。
事前合规存在的意义就是在批准之前拦一道；批完再看等于没看。

---

## 执行：`run_execute.py`

批准之后**随时可以跑**，早了也没关系：

```
执行 US_PAPER　2026-08-31　已批准 1 条
  AVGO   等待开盘
         2026-08-31 之后还没有交易日的行情 —— 保持已批准，等下一次执行。
         **不拿今天的价硬成交。**
```

第二天再跑同一条命令：

```
  AVGO   已成交   BUY 6 股 @ 305.50　2026-09-01　跳空 -2.08%
```

那个 `跳空 -2.08%` 是决策日收盘 312.00 和实际成交开盘 305.50 的差。
**记下来但不修正**——它是真实存在的执行成本，抹掉它回测就变好看了。

账本随即更新：

```
账本 US_PAPER　USD　2026-09-01 开账　初始 100,000.00　每手 1 股
现金 98,167.00　持仓市值 1,872.00　NAV 100,039.00
  AVGO      6 股 @ 312.00　市值 1,872.00　成本 305.50　上次复审 2026-09-01
```

放进定时任务每天跑一次最省心——没到日子它就说"等待开盘"，什么都不会做错。

---

## Telegram 控制台（手机就是界面）

### 第一步：**另建一个 bot**

@BotFather → `/newbot` → 拿到 token → 写进 `.env`：

```
CIO_CTRL_BOT_TOKEN=1234567:AAxxxxxxxx
```

**为什么不能用现在这个 token。** Telegram 的一条更新**只会投递给一个**
getUpdates 消费者。你的 OpenClaw 已经在用 `TELEGRAM_BOT_TOKEN` 收消息；
控制台如果用同一个去 poll，两边互相抢，表现是**指令随机丢失**——
你点了批准，那条更新被另一边收走了，手机上转个圈然后什么都不发生，
**没有任何报错**。

不配的话程序会退回共用并在启动时大声警告，不静默共用。

### 第二步：启动

```
CIO_MARKET=us .venv/bin/python run_tgbot.py
```

想让它常驻（开机自启、崩了自动拉起）：

```
bash scripts/install_tgbot_launchd.sh
```

### 手机上能做什么

```
/pending    待批清单（每条带 ✅批准 / ❌否决 按钮）
/book       账本与持仓
/approve 12   也可以 /approve NVDA
/reject 12 估值太贵
/approveall
/execute    执行已批准的
/rebalance  重新出提案
/pc         跑一遍 CRO→PC（较慢）
/stats      提案状态分布
/help
```

按钮点下去调用的是 `proposal_store.transition()`——**和命令行同一个函数**。
不是"给 Telegram 也写一份"：两份规则一定会漂移，而漂移的那份不报错。

### 让提案自己推到手机

```
CIO_MARKET=us .venv/bin/python run_rebalance.py --tg
CIO_MARKET=us .venv/bin/python run_execute.py --tg
```

推送里除了按钮，也附上命令行写法——**因为按钮要有人接才有用**。
控制台没在跑的时候，点按钮就是转个圈然后什么都不发生，没有任何提示。

### 安全

只响应 `TELEGRAM_CHAT_ID` 这一个 chat，其他一律忽略并记日志（不回复——
回复等于确认这个 bot 存在）。合规破限的提案在 Telegram 上**不提供强批**，
必须回电脑用 `--force`。

---

## 执行层的规则（这次冻结）

**只有 APPROVED 能成交。** 其他状态一律拒绝。

**下一个交易日是查出来的，不是算出来的。** "T+1" 不等于"明天"：周五的下一个
交易日是周一，假日要跳过。用日历规则算需要一份美股节假日表，那份表一旦过期
就会安静地错一天。这里去数据里找**第一根晚于决策日的 K 线**；找不到就是
"还没到"，保持已批准等下次,**不拿今天的价硬成交**。

**成交价是那根 K 线的开盘价，未复权**，和账本成本价、盯市价同口径。

**幂等靠唯一键，不靠记性。** `book_trade` 的键是
`(run_id, portfolio_id, ticker)`，和提案同一把。重跑执行 → 命中 → 跳过。

**不做部分成交。** 钱不够就整条不成交——少买一半是一个没人批准过的仓位。

**不做卖空。** 要卖的股数超过账上持有 → 整条失败，需重新提案。

**同场卖出的回款当天不能拿去买。** 美股 T+1 交收。提案阶段就是按这个口径算的
现金需求，执行阶段必须一致——否则会出现"提案说钱不够、执行却成功了"这种
两边都不报错的矛盾。

**平仓是置 `open=0` 并留行，不是删行。** 删掉的那一刻，业绩归因的分母就没了。

**减持不改成本价。** 均价法下已实现盈亏已经单独记账，再改成本价会把同一笔
盈亏算两次。

---

## 一个我自己写出来又测出来的缺陷

`tgbot.send()` 最初**没有认 `CIO_TG_DRYRUN`**。于是自测时调用方照着自己的
DRYRUN 标志印"只打印未真发"，而 `send` 在背后真去调了 API——
**一句报告和一次真实发送同时存在**，不冲突、不报错。

正是这套系统一直在防的那种缺陷，写在了防它的模块里。现在 `send` 认 DRYRUN、
按**真实结果**返回 True/False，调用方照返回值报告，并有探针守着。

---

## 自检

```
CIO_MARKET=us .venv/bin/python scripts/check_build.py      82 项
CIO_MARKET=us .venv/bin/python scripts/test_execution.py   18 项
CIO_MARKET=us .venv/bin/python scripts/test_rebalance.py   35 项
```

---

## 下一步（Build 3）

- 每日盯市、NAV 曲线、P&L 与超额（基准换含息总回报）
- **公司行为**：拆股记股数调整，分红记现金入账。不做的话，4:1 拆股当天
  账本显示 −75%，**没有任何报错**
- **每日对账**三条恒等式，任一不成立就红字停报：
  1. `cash + Σ(shares × mark) == NAV`
  2. `初始资金 + Σ(交易现金流) + Σ(分红) == 当前 cash`
  3. 每个 open position 都能被 trades 表解释出来

第 2 条现在已经成立并有测试守着（`t_cash_identity`）。
