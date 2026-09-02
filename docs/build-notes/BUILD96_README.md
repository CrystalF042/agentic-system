# build96 —— 那三条 ARM「实质材料」，和它们暴露的一整类缺陷

```
cd ~/.openclaw/workspace/cio-agent && unzip -o ~/Downloads/cio-build96.zip -d . && .venv/bin/python scripts/check_build.py
```

`全部 115 项通过` 之后再扫一轮。

---

## 你贴的那三条

```
[1] 实质·已发生动作 + 具体比例
    Arm (ARM) Stock Looks Above Fair Value Even After AI Progress
[2] 实质·已发生动作 + 具体比例
    Advanced Micro Devices vs. Arm Holdings: Comparing Revenue Trends
[3] 实质·已发生动作 + 具体比例
    Arm Rises 2.8% as $272 Target Prices the CPU Tollbooth
```

一条估值观点、一条对比文、一条行情复述加目标价。**没有一条讲了 ARM
这家公司发生了什么。** 而三条的判定理由一模一样，这就是线索。

## 是我在 build91 埋的

build91 把**正文**加进判定依据，修的是"标题含糊、事实在正文里"的漏判
（AMD 沙特那条）。那一改是对的。

问题在于规则本身是**按标题**校准的。最弱那条通路是

```
已发生动作 + 具体比例 → 实质
```

标题里同时出现过去式动词和百分数，通常真的是条事实。但正文不是——
**随便一篇评论文的正文里都有一个过去式动词和一个百分数：**

```
Arm Holdings reported royalty revenue growth of 25% ...
                ↑ 完成动作           ↑ 具体比例
```

作者引这句是为了论证自己"估值偏高"的观点。它成了通行证。

**加了新的输入，却没有回去重验旧的判例。** 中间四轮没有任何报错，
实质占比反而从 4% 涨到 29%，看起来一直在变好。

## 改了什么

**一、标题自报家门的，正文顶不上来。** 标题命中"估值观点/对比文/清单体/
买卖建议"这类标记时，正文里的过去式动词不再能把它扶成事实材料。

模块文档承诺过的那条通路刻意保留：`Ahead of earnings, NVDA announced a
$50B buyback` 标题里既有前瞻词也有 announced + $50B，照判实质。

**二、"股价动了"不参与否决。** 这条是修上一条时差点犯的错。我先写成
"标题命中任何负向标记就否决"，然后拿真实标题跑了一遍，误杀了这些：

```
AMD rose 12% after announcing a $10 billion Saudi contract   → 判成了行情复述
Nvidia stock jumped after Beijing approved H20 sales         → 判成了行情复述
Intel Q3 revenue $13.3 billion vs $12.9 billion guidance     → 判成了对比文
```

丢的是一份百亿合同、一次监管放行、一条最标准的业绩标题。

区别在于**负向标记说的不是同一件事**：

```
硬标记  说的是【作者的姿态】——前瞻、日程、清单、荐股、估值、对比
        标题一旦自报是这一类，整条就是空的

软标记  说的是【价格动了】——行情复述、股价预测
        价格动是结果，真实原因经常就写在同一个标题的后半句
```

所以软标记照常打标签、照常堵死最弱通路，但**不否决整条材料**。

**三、`vs` 两侧必须都是词。** `$13.3 billion vs $12.9 billion guidance`
是"实际 vs 指引"，最标准的业绩标题；`Advanced Micro Devices vs. Arm
Holdings` 才是对比文。裸的 `vs` 会把前一类整批误杀。

**四、词形补齐。** 查上面几条时顺手发现的一类老毛病：

```
acquired  命不中 acquisition      approved  命不中 approval
resigned  没算完成动作            opened a probe  没有主动完成时动词
```

于是从 build63 起，下面这些一直被判「无实质·行情复述」：

```
Intel stock slid after the company halted its Ohio fab
KLA shares fell after the CFO resigned
AMD stock rose after it acquired ZT Systems
Nvidia shares tumbled as Beijing opened an antitrust probe
```

**闸门判定没错**——它们本来也进不了实质档。但标签是错的，而标签错了
规则就不可审计：你翻报告只会看见"行情复述"，看不出系统其实认得那半句里的
停产、辞任、收购、立案。现在它们回到该在的档位。

**五、删掉一个没人读的阈值。** `material_gate` 里有两个常量写着同一件事：
`_SUFFICIENT_N = 3` 和 `_ENOUGH = 3`，而 `assess()` 只读前一个。
把 `_ENOUGH` 调成 2 会看起来像放宽了闸门，实际什么都不会发生——
不报错、日志正常、行为不变。删掉，阈值只留一处。

---

## 真正的修复是第六条：回归语料

前五条是补漏。第六条是补**为什么会漏**。

`scripts/_material_corpus.py` —— 真机上出现过的 38 条材料，
连同它每一条**应该**被判成什么、从哪个 build 来的、为什么。
规则每改一次，整份重跑一次，不是只跑新加的那几条。

期望值写的是"该判成什么"，不是"当前会判成什么"。照当前行为回填的语料
是实现的复印件：规则改错时它跟着一起改错，永远不会红。

我拿它做了变异测试——把这次加的四条规则逐条改坏，看语料抓不抓得到：

```
去掉标题否决          语料抓到 2 条判错
去掉软标记            语料抓到 2 条判错
软标记也压到无实质      语料抓到 2 条判错
vs 退回裸匹配         语料抓到 1 条判错
```

四条规则全是**在承重的**，而且写错了会当场红。一个抓不到任何变异的
测试，和没有测试是一回事。

---

## 这一轮预期

ARM 那 3 条会归零 → 大概率 INSUFFICIENT。
词形补齐会**捡回**一些原来被误标的真事件，方向是加。
两个方向相抵，整体实质占比大概在 10–20%，不会像上一轮那样只往下掉。

**看的还是 `--verbose`，不是百分比。** 前面五轮里有四轮，
百分比往哪个方向动都不能说明规则对不对——57% 那轮是最漂亮的数字，
也是唯一一次闸门被彻底拆掉。

---

## 自检

```
CIO_MARKET=us .venv/bin/python scripts/check_build.py     115 项
CIO_MARKET=us .venv/bin/python scripts/test_intake.py      37 项
```
