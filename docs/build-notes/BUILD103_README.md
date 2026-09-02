# build103 —— 把"换模型有没有用"变成一张表

```
cd ~/.openclaw/workspace/cio-agent && unzip -o ~/Downloads/cio-build103.zip -d . && .venv/bin/python scripts/check_build.py
```

`全部 126 项通过` 之后跑：

```
cd ~/.openclaw/workspace/cio-agent && CIO_MARKET=us .venv/bin/python scripts/eval_judge.py
```

**不联网、不花钱、不需要任何 key。** 它评的是现在这套规则。

---

## 基线已经量出来了

```
调参集（规则为它改过，只用来看有没有退化）  67/67（100%）
**留出集（规则从没见过，这个才算数）**        3/8（38%）
**相关性（丢错了不会出现在任何输出里）**      13/20（65%）
```

那个 100% 不说明任何事——**调参集的每一条都来自某个 build 的修复现场，
规则见过它、而且是为它改的**。那是训练数据。

留出集是 8/31 扩样测试里 ON 和 IT 那两只票的材料，规则从来没见过。
判错的五条:

```
期望 无实质 实得 实质   ON Semiconductor (ON) Stock May Be 2% Undervalued As AI Data Center Wins Build
期望 无实质 实得 实质   Stronger Outlook And AI Security Demand Might Change The Case For Investing In Gartner (IT)
期望 无实质 实得 背景   Power Semis Soar Monday: Wolfspeed, STMicro and On Semiconductor Rally…
期望 无实质 实得 背景   Why I've Begun Accumulating ON Semiconductor
期望 无实质 实得 背景   KLA: The AI Yield Bottleneck Is Becoming More Valuable
```

相关性判错的七条里，两个方向都有:

```
期望 相关   实得 不相关  KLA Falls 3.9% as Chip-Equipment Sentiment Remains Fragile
期望 相关   实得 不相关  Gartner Stock Gains 28% in 3 Months
期望 不相关 实得 相关    It's Game Week! - West Virginia University Athletics
期望 不相关 实得 相关    Why it's so hard to access your data from companies
```

**任意一个模型只要能在这两栏做到接近满分，这场讨论就结束了。**

---

## 怎么对比另一个

```
CIO_MARKET=us .venv/bin/python scripts/eval_judge.py --judge ollama:<你的模型名>

CIO_ANTHROPIC_API_KEY=sk-... CIO_MARKET=us .venv/bin/python scripts/eval_judge.py --judge claude:claude-haiku-4-5
```

`--verbose` 会把判错的逐条列出来，不只是给个分数。
判定按内容哈希缓存在 `memory/judge_cache_*.json`，**重跑不会重复调用**。

---

## 框架：语言理解 与 政策，切开了

```
src/cio/judge.py        Verdict 契约 + RuleJudge / LLMJudge（Ollama 与 Claude 同一接口）
scripts/eval_judge.py   评测任意 judge，出分数与错误明细
scripts/test_judge.py   护栏自测（11 项）
```

**`material_gate.assess()` 一行没改。** 政策层照旧是确定性的：

```
模型可以说的              模型永远不许说的
────────────────        ──────────────────────
这条是不是关于这家公司      Form 4 触不触发闸门
它在报告事实还是讲观点      几条算材料充分
它说的是哪件事             同一事件怎么合并
                        公告没正文算不算实质
                        THIN 时信心封到哪一档
```

有一条测试专门钉这件事:`material_gate` 的源码里**不许出现 `judge`**。
以后谁"顺手"把模型接进政策层，探针会红。

另有一条钉提示词:里面不许出现 `Form 4` / `SUFFICIENT` / `材料充分` /
`闸门` / `仓位` 这些词。**只问语言问题。**

---

## 三条护栏（写在代码里，不是写在提示词里）

**一、引文必须能从原文逐字核对。** 模型判「实质」时必须抄出让它这么判的
那半句原话，代码做子串校验——对不上就降级为「背景」，理由写明。
这把"相信模型"变成"核对模型的引文"，而核对是确定性的、离线的。

```
模型说：实质，span="AMD signed a $50 billion deal with Oracle"
原文里没有这句 → 背景·模型判实质但引用的原文对不上，按背景计
```

**二、不通就显式降级。** 模型挂了、回了非 JSON、回了不认识的档位——
一律回落到规则，并且 `degraded=True` 一路带出去。
静默降级会让"今天模型不通"和"今天没新闻"长得一模一样，这个坑在死掉的
RSS 源上已经踩过一次。

**三、降级结果不进缓存。** 否则一次网络故障会被**永久固化**成这条材料的
判定，而且以后再也不会重试。

---

## 关于数据出本机

`claude:` 这条路径会把材料文本发到本机之外。送出去的**只有公开新闻的
标题与正文片段**——不含持仓、论点台账、净值或任何属于账户的东西。
整条链路上，材料闸门恰好是最不敏感的那一段。

要不要用你定，代码只保证送出去的就是这些。**`rules` 与 `ollama:` 两条路径
一个字节都不出本机。**

---

## 一条纪律，写在语料文件里了

**永远不要为了让留出集变绿而去改规则。** 一旦那么做，它就不再是留出集。
要修可以，但修完必须把用到的判例挪进调参集，并换一批新的留出样本。

有两条测试盯着这件事:留出集不许和调参集重叠；**规则在留出集上不许是满分**
——真满分了，多半是有人拿它去调参了。

---

## 这一版刻意没做的

**没有接进主链路。** 先量分数，再决定接不接、接哪个。
先接线后评测，等于拿真机跑分当验收——这个项目已经反复吃过那个亏。

**没有做批量调用。** 生产上一只票 55 条标题应当一次调用送进去，
但那需要序号对齐与错位回退，是没被验证过的复杂度。
评测逐条调用，慢一点但不会静默错位。

---

## 自检

```
CIO_MARKET=us .venv/bin/python scripts/check_build.py     126 项
CIO_MARKET=us .venv/bin/python scripts/test_judge.py       11 项
CIO_MARKET=us .venv/bin/python scripts/eval_judge.py       基线
```
