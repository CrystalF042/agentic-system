# 怎么跑 —— 每天两条命令，加一件你自己做的事

> 所有命令都用 `.venv/bin/python`，不要用 `python`。
> 先 `cd ~/.openclaw/workspace/cio-agent`。

---

## 〇、只做一次：装 + 自检 + 开账

```
cd ~/.openclaw/workspace/cio-agent && unzip -o ~/Downloads/cio-build124-engine.zip -d . && .venv/bin/python scripts/check_build.py && .venv/bin/python scripts/test_notify.py && .venv/bin/python scripts/test_pipeline.py
```

要看到 `全部 151 项通过` + `全部 15 项通过` + `全部 16 项通过`。**有红的先别往下走。**

然后开账（只做一次；已经开过就跳过）：

```
CIO_MARKET=us .venv/bin/python run_rebalance.py --open-book --capital 100000 --opened-on 2026-09-05
```

**开账日和初始资金是写进去的事实**，不从最早一笔交易倒推。
不确定开没开过就跑一次 `CIO_MARKET=us .venv/bin/python run_book.py`。

---

## 一、每天第一条：收盘快照（喂料）

```
CIO_MARKET=us .venv/bin/python scripts/technical_snapshot.py
```

它做四件事：全市场 Signal Card → 技术闸门 → 家族分 → **触发进研究队列**。

**只在收盘窗口内才真的存**（美东 16:30–23:59，默认排 18:00 让 yfinance 尾行落定）。
不在窗口它会印一行「跳过」并**照样留下一份心跳**——
"因为不在窗口所以跳过"和"根本没跑"必须分得开。

想现在就存一份：加 `--force`（报告上会标 `⚠窗口外`）。

看积累情况：

```
CIO_MARKET=us .venv/bin/python scripts/technical_snapshot.py --status
```

---

## 二、每天第二条：跑流水线（研究 → 风控 → 仓位 → 提案 → 通知）

**先预演。不花钱、不改状态、不发消息：**

```
CIO_MARKET=us .venv/bin/python scripts/research_run.py --dry-run
```

它会告诉你今天会研究谁、谁超预算被推迟。名单没问题再真跑：

```
CIO_MARKET=us .venv/bin/python scripts/research_run.py
```

这一条会：

```
队列前 5 名 → 一部（每只 6 次模型调用）→ CRO → PC → 落提案 → 推送给你
```

**它自己走不到 Approve。** 队列的状态机里 `APPROVED` 只能从 `PENDING_APPROVAL` 来，
而这条链上的模块里连一个把状态改成 `APPROVED` 的调用都没有（探针钉着）。

预算和队列状态：

```
CIO_MARKET=us .venv/bin/python scripts/research_run.py --status
```

---

## 三、你做的那一件事：批 / 不批

手机上会收到带按钮的消息。按钮要有人接——**控制台得在跑**：

```
CIO_MARKET=us .venv/bin/python run_tgbot.py
```

没跑控制台的话，在电脑上：

```
CIO_MARKET=us .venv/bin/python run_approve.py --pending
CIO_MARKET=us .venv/bin/python run_approve.py --approve 12
CIO_MARKET=us .venv/bin/python run_approve.py --reject 12 --reason "估值太贵，等回调"
```

**批准的是那个整数股数**，不是权重。批完它就固定了，T+1 开盘按实际开盘价成交它。
**有效期 4 个自然日**，过期自动作废——过期不是拒绝，是这次决定被时间吃掉了。

---

## 四、批完之后：成交

第二天开盘之后：

```
CIO_MARKET=us .venv/bin/python run_execute.py
```

只有 `APPROVED` 的才会成交。

---

## 五、查东西

```
CIO_MARKET=us .venv/bin/python scripts/notify_run.py --text       要发给你的原文，什么都不发
CIO_MARKET=us .venv/bin/python scripts/notify_run.py --status     上次真的送到是什么时候
CIO_MARKET=us .venv/bin/python scripts/heartbeat.py --last 7      最近 7 天跑没跑
CIO_MARKET=us .venv/bin/python scripts/heartbeat.py --missing 14  哪几天没跑
CIO_MARKET=us .venv/bin/python run_book.py                        账本
CIO_MARKET=us .venv/bin/python run_rebalance.py --stats           提案状态分布
CIO_MARKET=us .venv/bin/python run_pc.py --stats                  仓位是被谁决定的
```

`--status` 里那句「上次真送到 **从来没有**」是有意留的：
**一个从来没成功过的推送通道，和一个昨天刚推过的，绝不能在报告上长得一样。**

---

## 六、已经自动跑的

```
com.crystal.cio.premarket   周一–周五 06:00 美东   run_premarket.py（盘前简报）
com.crystal.cio.snapshot    每天 18:00 美东        technical_snapshot.py（要装）
```

装收盘快照的定时：

```
bash scripts/install_snapshot_launchd.sh
```

查排得对不对：

```
CIO_MARKET=us .venv/bin/python run_premarket.py --doctor
```

**`scripts/research_run.py` 还没有定时**——第二条命令目前得你自己敲。

---

## 七、每天会收到什么

```
06:00  盘前简报
18:00  收盘快照的心跳（今天扫了多少、几只过闸、几条进队列）
       流水线的心跳 + 有待批时的那条带按钮的消息
```

心跳**每天都发，包括全 0 的日子**——一份"今天全 0"的报告，
和一份根本没来的报告，是两件完全不同的事。

告警印在报告最上方，目前有五件事会点灯：

```
CRO 否决了某只票
算出了目标权重但提案一条都没落成
队列待批数和提案库待批数对不上
有提案等你批而提醒没送到
有提案即将过期作废
```

---

## 八、出问题先看这三条

```
1  .venv/bin/python scripts/check_build.py          有红的说明装的不是最新代码
2  .venv/bin/python scripts/heartbeat.py --missing 14   哪几天根本没跑
3  .venv/bin/python run_premarket.py --doctor       定时排在几点、跟窗口对不对得上
```

第 2 条是**盘前简报静默失踪三天**之后加的：
磁盘上有没有那一天的报告，就是"那天到底跑没跑"的答案。

---

## 八点五、辩论跑在哪个模型上

默认本地 `gpt-oss:20b`，**装了 build124 也不会自己变**。

想换到 Claude，`.env` 里加两行：

```
CIO_ANTHROPIC_API_KEY=sk-ant-...
CIO_DEBATE_ENGINE=claude:claude-sonnet-5
```

改完先确认：

```
CIO_MARKET=us .venv/bin/python scripts/research_run.py --status
```

它会明确告诉你**材料出不出本机**，以及今天花了多少、上限是多少。
想退回本地就删掉 `CIO_DEBATE_ENGINE` 那一行。

日花费上限：`CIO_MAX_USD_PER_DAY`（默认 5）。Sonnet 5 每天约 $0.50。

**调用失败不会退回本地** —— 那只票转 FAILED、明天重来，心跳里写明原因。
退回的话，论点台账里会同时存在两个引擎写的论点而没人知道哪条是哪个。

---

## 九、想让它停下来

```
CIO_RESEARCH_ENABLED=0 .venv/bin/python scripts/research_run.py
```

关掉是一个正常状态，**但它会出现在心跳里**，而且队列不清空、还数得出来有几条在等——
一个被关掉的流水线和一个坏掉的流水线，不许长得一样。
