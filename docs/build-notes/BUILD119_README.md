# build119 —— Build 1：收盘快照自动定时 + 心跳报告格式

```
cd ~/.openclaw/workspace/cio-agent && unzip -o ~/Downloads/cio-build119-heartbeat.zip -d . && .venv/bin/python scripts/check_build.py && .venv/bin/python scripts/test_heartbeat.py && bash scripts/install_snapshot_launchd.sh && .venv/bin/python scripts/heartbeat.py
```

`146 项` + `9 项`（技术套 `56 项` 不变）。

---

# 一、心跳格式：这一版真正的产物

后面每个 build 加一节流水线，**都接进同一份报告**。所以格式先于那些 build 存在。

```
CIO 流水线心跳　2026-09-05
[技术快照] 完成　universe 503　cards 502　written 502　observe_failed 1　gate_passed 2　rankable 2　unscorable 0　review_pending 1
    有 1 只没出卡片（数据不足或观察失败）
    universe_pit=False —— 回放历史仍带幸存者偏差
[研究路由] 未运行
    Build 1 还没接上这一节
[研究队列] 未运行
[证券一部] 未运行
[风控与仓位] 未运行
[待你批准] 未运行

**未运行的阶段**：研究路由、研究队列、证券一部、风控与仓位、待你批准
（未运行 ≠ 今天没事。这几节现在还没接上流水线。）
```

## 五条规矩

**一、阶段表是声明的，不是跑出来的。** 六节写在 `heartbeat.PIPELINE` 里，
Build 1 只有第一节真的会跑，**其余五节照样出现、标"未运行"**。
一个死在中途的流水线如果报告只是"短一点"，那和"今天本来没事"长得一样。

**二、`0` 要印。** `gate_passed 0` 是一个结论，不是空白。

**三、每天都发，包括全 0 的日子。** 一份"今天全 0"的报告和一份根本没来的报告，
是两件完全不同的事。

**四、报告落盘，一天一份。**

> **磁盘上有没有那一天的文件，就是"那天到底跑没跑"的答案。**

这就是 `scripts/heartbeat.py --missing` 回答的问题。在它存在之前，
"今天没事"和"今天没跑"在磁盘上、在收件箱里都是同一个样子：什么都没有。

**五、一个阶段炸了不拖垮别的，但绝不吞掉。** 记成 `失败` + 异常类型，
继续跑下一节，**并且进退出码**。

---

# 二、收盘快照的定时

```
bash scripts/install_snapshot_launchd.sh
```

**小时数从窗口现算，不写死。** 盘前那个 19:30 就是写死的后果——
按 A 股盘前排的，用机器的美东钟表达，换成美股之后没人动它。

默认排在**收盘窗口起点 + 2 小时**（美东 18:00），理由很具体：

> 2026-09-04 我们亲眼见过 SPY 最后一根收盘价是 NaN（yfinance 尾行），
> 结果全市场 502 只票的大盘超额同时变 null。**刚收盘那会儿数据还没落定。**

排在窗口外会被拒绝（`CIO_SNAPSHOT_ALLOW_ANY_HOUR=1` 显式放行）。

---

# 三、三个跑起来才发现的洞

**一、跳过的那天不会留下报告。** 收盘闸原来直接 `return 0`，
于是"因为不在窗口所以跳过"和"根本没跑"在磁盘上一模一样——
**那正是盘前简报静默失踪三天时的形状，而它就长在这次要修它的代码里。**

心跳现在**建在闸门之前**，跳过记成 `skip` + 理由。

**二、一张卡都没出，报告写"完成"。** 我这边网络被挡，跑出来是：

```
[技术快照] 完成
```

计数空着、状态是完成——**和一个正常的安静日子长得一模一样**，而那次跑
其实是全市场取数全挂了。现在它是：

```
[技术快照] **失败**　universe 31　cards 0　written 0
    RuntimeError: universe 31 只，卡片 0 张：取数全挂
```

**三、`cron_hint` 那句夏令时警告在你机器上是假的。**
它无条件印"一年两次夏令时切换要手动改"，而你的机器就在美东：
本机 06:00 就是市场 06:00，launchd 跟着本机 DST 走，自己就对了。
现在它先比较两个时区的偏移量再决定印哪一句。

**一句不成立的警告和不印一样没用**——人学会忽略它之后，
真正需要手动改的那天也会被忽略。

---

# 四、变异测试：18 个，第一轮 2 个漏网

```
只印跑过的阶段（没跑的悄悄消失）    test○ check○  ← 漏
计数为 0 不印                     test✗ check✗
未声明的阶段也放行                 test✗ check○
阶段异常被吞掉                     test✗ check✗
失败不进退出码                     test✗ check✗
跳过不要理由 / 跳过被当成失败        test✗ check✗
非日期文件也算一天                  test✗ check✗
缺失日不报                        test✗ check✗
心跳建在闸门之后                   test✗ check✗
跳过不记进心跳                     test✗ check✗
一张卡都没出只 return              test✗ check✗
心跳不落盘                        test✗ check✗
夏令时警告又变无条件                test✗ check✗
local_window 忽略 win             test✗ check○
收盘定时把小时数写死                test○ check○  ← 漏
窗口外照装不误                     test✗ check○
取数提到 main 里（绕开收盘闸）       test✗ check○
```

两条漏网，**又是这一轮反复出现的同两个毛病**：

**一、断言从另一条代码路径被满足。** 我断的是"每个阶段的标签出现在报告里"，
而报告**底部那句"未运行的阶段：…"汇总里也有这些标签**——
于是"只印跑过的阶段"这个变异照样绿。改成断每个阶段有**自己那一行**（`[标签]`）。

**二、夹具没有判别力。** 美东机器上收盘窗口 16:30 起 → 默认 18 点，
**和写死 18 一模一样**。改成把机器时区掰到上海（那里收盘窗口是 04:30–11:59，
默认应当是 6），写死的 18 会落在窗口外被拒绝。

---

# 五、顺带被现有用例抓到的一次

重构把取数搬进 `_snapshot_body` 之后，`t_snapshot_runs_after_close`
立刻红了——它是按旧的单函数结构写的。

**修法是把断言改强，不是改弱**：现在跨两个函数断
「`main` 里根本够不到取数，唯一入口是那个被闸门守着的函数」。
新加一条变异（把 `get_universe` 提到 `main` 里）验证它确实拦得住。

---

# 六、装完跑这四条

```
1  .venv/bin/python scripts/check_build.py           146 项
2  .venv/bin/python scripts/test_heartbeat.py          9 项
3  bash scripts/install_snapshot_launchd.sh          ← **收盘快照终于自动了**
4  .venv/bin/python scripts/heartbeat.py             ← 今天的心跳 + 最近缺的日子
```

第 4 条现在会告诉你 **09-04 之前那几个工作日一份报告都没有**——
那不是那几天没事，是那几天根本没跑。**从今天起这件事再也不会看不见。**

其它命令：

```
scripts/heartbeat.py --last 10        最近 10 天，一天一行
scripts/heartbeat.py --day 2026-09-04
scripts/heartbeat.py --missing 30     只回答"哪些工作日根本没跑"
```

---

**Build 2（Trigger schema + Router + 持久化 Research Queue）接着做。**
它加的那两节会直接出现在同一份心跳报告里——现在标着"未运行"的那两行。
