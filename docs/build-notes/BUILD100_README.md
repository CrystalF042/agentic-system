# build100 —— 转载不该顶开闸门，评论体不该被正文扶起来

```
cd ~/.openclaw/workspace/cio-agent && unzip -o ~/Downloads/cio-build100.zip -d . && .venv/bin/python scripts/check_build.py
```

`全部 121 项通过` 之后再扫一轮，`--verbose`。

---

## 先说 build99 那一轮的验收

分句救援落地了，ARM 的 20 亿在手订单被救回来，**而且别的票一条新的
「分句含可核对事实」都没冒出来**。那是我最担心的地方——动的是标题否决的
核心逻辑，误伤一次就会整批丢材料。这次没有。

顺带确认一件事：AMD 那条误判三轮抓到的正文各不相同
（`European contra` / `project value f` / `The European contract`），
**判定却每次都是实质**——说明误判来自标题，和正文无关。诊断坐实了。

---

## 这一轮改两件事

### 一、闸门数事件，不数文章

AMD 那三条实质里有两条是同一件事：

```
AMD, Cisco and HUMAIN Expand Saudi Arabia's AI Infrastructure as AMD Instinct Systems Go Live
AMD and Cisco Expand AI Infrastructure in Saudi Arabia
```

一份新闻稿被两家转载，`_SUFFICIENT_N = 3` 就被转载量顶穿了。
去重那一步拦不住——两家的标题措辞不一样。

**这和 build94 是同一个家族的缺陷**（那次是「8 份历史公告 = 材料充分」）：
同一件事被多次计数就能开门，而开门意味着启动一场完整的多空辩论。

判定用**重合系数**（交集 ÷ 较短的一方），不是 Jaccard：

```
"AMD, Cisco and HUMAIN Expand Saudi Arabia's AI Infrastructure…"  12 词
"AMD and Cisco Expand AI Infrastructure in Saudi Arabia"           7 词
交集 7 → Jaccard 0.58（漏掉），重合系数 1.00（并上）
```

转载常常是长标题的子集，Jaccard 会因为长度差把它们判成两件事。
反过来同一家公司的两件不同的事不会被误并，因为实体和数字都不一样：

```
"AMD Wins $2 Billion Order From Oracle"  vs  "AMD Wins $3 Billion Order From Meta"
交集 3，较短方 5 → 0.60 < 0.7
```

指纹**只看标题**。正文各家自己写、长度不稳定——你也看到了，同一篇文章
三轮抓到三段不同的正文。拿它做指纹，同一件事会时而并时而不并，
而那种不稳定不会报错。

归并的条目照常显示、照常可引用，只是不重复计数，**并且标签上写明**：

```
[3] 实质·已发生动作 + 重大事件（与 #1 同一事件，不重复计数）
```

不写的话，这一步归并就是又一个看不见的变换——这个坑已经在相关性闸上吃过。

### 二、评论体标题不被正文顶成实质

```
AMD Enters a Sovereign AI Showcase, Not a Revenue Windfall     → 转折否定式
What KLA (KLAC)'s ... Momentum Means For Shareholders          → 解读体
```

第一条整篇文章的论点就是「这笔生意在财务上不重要」。
第二条是 **build96 那个缺陷的原样复发**——评论体标题被正文里的
`KLA reported…` 顶成实质，只是这个句式当时不在硬标记表里。

顺带补齐同类的，KLAC 那 10 条基本被这批覆盖了：

```
Here Is How To Collect 21% A Year        原来只收 here's why
Positioned to Benefit from …             有望受益是观点
Stock Looks Fully Valued                 fully valued 不在估值表里
Why KLA Corporation (KLAC) Stock Is Down why 与 is 之间原来只允许一个词
Buy The 2027+ Double Tailwind            祈使句荐股
Movement as an Input in Quant Signal Sets 量化信号推广
Micron Could Be September's Biggest …    could be 一律是推测
```

**没有误伤**（这两条都进了语料）：

```
AMD, not Intel, won the $10 billion Saudi contract      否定短语在句中，是真事实
Why AMD Cut Its Guidance: CFO Explains                  why 接动作动词，是在解释真事件
```

`why` 那条规则只在接 `is/are/was/were/情态动词` 时才算评论——接动作动词的
是新闻在解释一件真发生的事。这个区分是有意的。

---

## 拿今天的材料重跑，三只票全部落到我三轮前说的位置

```
AMD    SUFFICIENT → THIN   （2 条报道归并为 1 个事件，那条误判已剔除）
KLAC   THIN → INSUFFICIENT （唯一那条实质是评论文）
ARM    THIN                （2 件不同的事：战略转型 + 在手订单）
```

**AMD 今天不该开完整辩论。** 它之前是靠「1 条误判 + 1 条转载」凑够 3 条的。

---

## 两个老测试把缺陷写进了自己

改完之后 `test_unit_a` 和 `check_build` 各红了几条。查下去发现，
它们构造「3 条实质 → SUFFICIENT」用的是**同一句话复制三份**：

```python
g3 = MG.assess([M(i, real) for i in range(3)])   # real 是同一个字符串
```

三份完全相同的文本当然是一件事。**旧写法把「转载能顶开闸门」这个缺陷
写进了测试本身**——这类测试不仅测不出问题，还会在你修对的时候变红，
把你劝回去。改成三件不同的事，另加一条断言：同一件事的三份转载只算一件。

---

## 变异测试

```
按事件计数关掉      MISS      去掉转折否定式    MISS
归并标签不写        MISS      去掉解读体        MISS
重合系数→Jaccard   MISS      why 退回单词      MISS
门槛降到 0.3       MISS      去掉 fully valued MISS
指纹带上正文        MISS      去掉量化信号      MISS
                            去掉推测式        MISS
```

**其中一条第一次没抓到。** 探针原来写的是 `!= SUBSTANTIVE`，
而这批规则改的多半是**标签准不准**（无实质 vs 背景），两者都不触发闸门，
所以规则被删掉探针照样绿。改成断言等于「无实质」。

标签不准就等于规则不可审计：你翻报告只会看见"相关报道，无可核对的增量事实"，
看不出系统其实认得这是解读体、荐股、还是量化信号推广。

（另外三条我一开始以为逃掉了，查下去是**我的变异打错了**——
只改了两分支正则的其中一支，行为根本没变。一个没真正移除行为的变异，
什么都证明不了。）

---

## 这一轮之后还剩什么

**产业词捞回**（上次列的第 3 条，还没做）。ARM 的候选仍然被消歧砍掉六成，
而它筛的其实是媒体体例不是相关性——Yahoo/Zacks 习惯写 `(ARM)`，
通讯社和行业媒体只写 `Arm`，而真产业新闻在后者。
`--verbose` 里那条 `IBM Introduces ... With Arm Architecture` 就是这么丢的。

---

## 自检

```
CIO_MARKET=us .venv/bin/python scripts/check_build.py     121 项
CIO_MARKET=us .venv/bin/python scripts/test_intake.py      48 项
```
