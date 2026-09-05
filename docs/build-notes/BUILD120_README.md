# build120 —— Build 2：Trigger + Router + 持久化 Research Queue

```
cd ~/.openclaw/workspace/cio-agent && unzip -o ~/Downloads/cio-build120-queue.zip -d . && .venv/bin/python scripts/check_build.py && .venv/bin/python scripts/test_research.py && .venv/bin/python scripts/technical_snapshot.py --force
```

`147 项` + `12 项`。

---

# 第一条不变量：Evidence Gate 不许拦 Technical Trigger

你定的这一条是这一版的第一条用例，因为**写错它的后果不是"技术入口变弱"**：

```
TECHNICAL → run_scan → INSUFFICIENT → STOP
```

队列照跑、简报照发、日志全绿，**而那条路上永远出不来一个名字**。
和盘前简报失踪三天是同一个形状。

所以钉了三层：

```
技术 trigger 产生时 evidence_gate **留空**   没问过 ≠ 问过没有
补跑 Evidence Scan 判成 INSUFFICIENT 之后    仍然进队列、仍然走到 Unit A
router 的 merge/route/dedupe 里              **不许出现 evidence_gate 这个词**
                                             （走 AST 检查函数体）
```

---

# 第二条：一次事件 = 一个任务，不是一天一个

**闸门是一个状态，不是一个脉冲。** AMD 连着 6 天满足同一个形态是**一件事**。
按 (日期, 标的) 去重，它会连着 6 天吃掉研究预算。

去重键是 `event_id = hash(标的, 事件起始日, 血统)`：

```
连续 6 天的同一形态   → 同一个 event_id → 一个任务
换了 setup 版本      → 不同的 event_id → **不该被去重掉**
                       （1.0.1 的命中和 1.1.0 的不是同一种东西）
```

事件起始日从 `store.events()` 推导，**不在队列这层重新发明一套**。

---

# 第三条：Trigger 是正式对象了

```
trigger_id     同一事件同一入口 → 同一个 id（幂等，重跑不多出一条）
run_id / symbol / trigger_type / triggered_at / as_of
event_id       去重键
reason_codes   机器可读，用来统计（给人看的句子在 note 里）
priority + priority_parts
evidence_gate / technical_gate
lineage        setup / 指纹 / 价区算法 / score 版本 + 指纹
schema_version
```

**`priority_parts` 和 `priority` 必须对得上**，有一条校验函数钉着：

```
AMD  EVIDENCE + TECHNICAL  159
     {'evidence_tier': 60, 'score': 87, 'both_entrances': 12}
```

一个说不出来历的 `159`，半年后没人能回答它是家族分给的、
还是双入口加成给的。**在这个项目里，说不出来历的数等于没有。**

顺带：**没有分数就是 0，不是补一个中性的 50。** 补 50 会让说不出分的票
排到中游——那是凭空造出来的位置。

---

# 第四条：两条入口合并，而且分岔"新论点 / 复检"

```
AMD 今天既有 8-K 又有技术触发
  → 一个任务，标 EVIDENCE + TECHNICAL，优先级 +12
  → **不是让 Unit A 跑两遍**
```

**已有 OPEN thesis 的票，来了新触发是复检，不是重跑 Bull/Bear/Judge。**
不分岔的话，要么白花一次钱重跑，要么 supersede 把昨天的论点冲掉。

---

# 第五条：防饿死，而且防饿死不能变成另一种饿死

每天只跑前 K 个。只看当天优先级的话，一个中等分数的票**可能永远排第 6**——
而"被推迟"和"被丢弃"又长得一样了。

```
每等一个交易日  +8 分   （单独记在 priority_parts[queue_age]）
上限            10 天    否则一个老条目会把所有新触发压下去
```

---

# 第六条：队列状态机

```
QUEUED → ENRICHING → RESEARCHING → RESEARCHED → RISK_REVIEW
                                                   ├→ VETOED（CRO 否决）
                                                   └→ PC_COMPLETE
                                                        → PENDING_APPROVAL
                                                            ├→ APPROVED → EXECUTED
                                                            ├→ REJECTED（CEO 否决）
                                                            └→ STALE
失败分支：FAILED（可回 QUEUED，最多 3 次，超了转 STALE）  DEFERRED
```

**CRO 否决和 CEO 否决分开记。** 合成一个 `REJECTED`，以后就答不出
"那道闸到底拦下过什么"——而那正是判断要不要拆闸的唯一依据。

**非法跃迁抛异常**，不静默通过。**失败不消失**：一次 API 超时不能让一只票
从世界上消失；但也不能无限重试——一条坏掉的记录会每天消耗一次研究预算，
而它看起来和正常排队一模一样。

**卡住的条目要看得见**：同一状态超过 2 个交易日就报出来。
一条卡住三天的记录和一条正常排队的，在计数上长得一样。

---

# 第七条：两节接进同一份心跳

```
[技术快照] 完成　universe 503　cards 502　gate_passed 2　rankable 2　review_pending 1
[研究路由] 完成　raw_triggers 2　unique_symbols 2　merged 0　both_entrances 0　rechecks 0
[研究队列] 完成　queued 2　exists 0　open_items 2　pending_approval 0
[证券一部] 未运行
    Build 3–5 还没接上这一节
[风控与仓位] 未运行
[待你批准] 未运行
```

**没接上的三节照样出现、标"未运行"**——那不是噪音，是"这条链还有三节
没接上"这个事实本身。

---

# 变异测试：17 个，第一轮 3 个漏网

```
路由按 evidence_gate 过滤          test✗ check✗
event_id 按当天算                  test○ check○  ← 漏
event_id 不含血统                  test✗ check✗
两条入口不合并                     test✗ check✗
双入口加成混进 score               test✗ check✗
priority 不校验来历                test✗ check✗
没分数补中性的 50                  test✗ check✗
老化没有上限 / 不做等待加成         test✗ check✗
有 OPEN 论点也当新论点跑            test✗ check○
非法跃迁静默通过                    （锚点过时）
CRO 否决并进 CEO 否决              test✗ check✗
入队不幂等 / 失败无限重试           test✗ check✗
counts 只报非零                    test✗ check✗
卡住的条目不报                     test✗ check○
路由/入队不记进心跳                 test○ check○  ← 漏
```

两条漏网**又是那两个老毛病**：

**一、夹具没有判别力。** `event_id` 那几条断言全用同一个 `start`，
而"改成按今天算"的变异在**同一次运行里 today 也是同一个值**——
两种实现给出同样的结果。改成断
`make_event_id(AMD, 09-04) != make_event_id(AMD, 08-01)` 才红。

**二、子串从另一个调用被满足。** 我断的是
`"hb.count(" in _route_technical 的函数体`，而那个函数里**有两处** `hb.count`，
删掉第一处照样绿。改成**真的跑一遍**：构造 `sc.Ranked`、跑路由和入队、
断 `hb.counts["raw_triggers"] == 1`。

补完 17 个全被抓到。

---

# 装完跑这三条

```
1  .venv/bin/python scripts/check_build.py        147 项
2  .venv/bin/python scripts/test_research.py       12 项
3  .venv/bin/python scripts/technical_snapshot.py --force
```

第 3 条现在会多印两段：**研究队列（今天几条、优先级来源）**和
**队列状态**（各状态几条、有没有卡住的）。

---

**Build 3（Queue → Enrichment → Unit A 自动调度 + 研究预算 + 重试 + 幂等）接着做。**
它接上的正是现在标着"未运行"的第四行。
