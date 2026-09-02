# build97 —— 0% 也不对：漏掉的是语法，不是事实

```
cd ~/.openclaw/workspace/cio-agent && unzip -o ~/Downloads/cio-build97.zip -d . && .venv/bin/python scripts/check_build.py
```

`全部 117 项通过` 之后再扫一轮，还是 `--verbose`。

---

## 先说结论：KLAC 的 0 是对的，ARM 和 AMD 的 0 是错的

我把你贴的 30 条原样跑了一遍。KLAC 那 10 条——量化信号、
"3 Reasons We Love This Stock"、"Up 8% Since Last Earnings Report"——
**确实一条实质都没有，0 是正确答案。**

ARM 和 AMD 不是。当天这两条就在材料里：

```
[4]  Arm Holdings (ARM) Shifts Strategy to Sell Own Data Center Chips
[6]  AMD, Cisco and HUMAIN Expand Saudi Arabia's AI Infrastructure
     as AMD Instinct Systems Go Live
```

一家靠授权收专利费的公司决定自己卖数据中心芯片；一批加速器在沙特
真的上线了。两条都判成了「背景·相关报道，无可核对的增量事实」。

## 为什么

**因为新闻标题用一般现在时表示已经发生的事，而规则只认过去式。**

```
Shifts     已经转了
Expand     已经扩了
Go Live    已经上线了
Introduces 已经发布了
```

模块里原来那句注释写着"只收 reported 不收 report——is set to report
是日程不是事实"。那句话本身没错，但它针对的是**助动词结构**。
裸的标题现在时不是日程，是新闻体。这个区分我当初没做。

放宽的全部风险在于**裸词形和不定式同形**（`to expand` / `Expand`），
所以拆成两组：第三人称单数 `-s` 无需守卫（不定式永远不带 -s），
复数主语的裸词形必须挡住 `to` 和情态动词。这四条现在照旧不是实质：

```
Nvidia is set to expand capacity next quarter
Analysts expect AMD to win more data center share
AMD could acquire a networking vendor this year
AMD's Saudi AI Bet Is Scaling Toward 1 Gigawatt     ← 进行时不是完成时
```

顺带补上了几个事件名词：`go live` / `deployment` / `data center` /
`design win` / `partnership` / `foundry`。原来的事件表里全是法务和财务词，
**没有一个是"东西造出来了、上线了"** ——而那是半导体最主要的事实类型。

---

## 第二件事：ARM 的 10 个名额有 4 个被英文词占了

```
[6]  Current ARM mortgage rates report for Aug. 31, 2026     浮动利率房贷
[7]  2-alarm fire at small business in Glen Arm              地名
[8]  Mom Who Had Arm Amputated After Shark Attack            身体部位
[10] debt linked to its asset management arm                 部门
```

build95 把子串改成词边界，挡住了 `arms` / `pharma` / `carmakers`。
但 **ARM 本身就是一个英文单词**，词边界救不了它。

而且这不只是"多了几条噪音"。每只标的只有 10 个进闸门的名额，
你那一跑 ARM 的进料行写着 **相关 26 条 → 前 10 条进闸门（截掉 16 条）**。
这四条**挤掉了四条真材料**，而被挤掉的那些从来不会出现在任何输出里。

改法两层：

```
一  与常用词撞车的符号（ARM/ON/IT/AI/KEY/CAT…）→ 裸匹配一律不认，
    必须出现 (ARM) / NASDAQ:ARM / ARM's / ARM stock 这类身份形态
二  兜底，不依赖任何名单：大小写对不上的裸匹配一律不认
    ——公司符号写作 ARM，写成 Arm / arm 的多半是普通词
```

第二层是为了名单必然会漏。变异测试里我把名单清空，四条无关材料只有
一条（`ARM mortgage`，大写恰好一致）漏了过去，另外三条被兜底挡住。

**代价我写在代码里了，免得以后当成 bug 去"修"：**
`IBM Introduces New Mainframe Processor With Arm Architecture` 也会被挡掉。
公司全名仍然照常匹配（`Arm Holdings` 是另一个别名），被挡的只是
**只提了裸符号、一次身份形态都没有**的那些——而那条讲的其实是 IBM。

---

## 另外两个标签失准（不影响闸门，影响可审计性）

```
Arm Holdings (ARM) Heads To ..., Is The AI Story Fully Priced? - Yahoo Finance
```
疑问式规则要求问号在行尾，而 RSS 标题几乎都带 ` - Yahoo Finance` 后缀，
build91 加正文之后更是彻底哑掉（问号被推到正文前面）。

```
KLA Corporation (KLAC): 3 Reasons We Love This Stock
```
清单体规则要求数字在行首，前面挂个公司名就漏了。

两条都不改变闸门结论，但标签不准规则就不可审计——你翻报告只会看见
"相关报道，无可核对的增量事实"，看不出系统其实认得这是标题党。

---

## 这一轮预期

```
KLAC   仍然 INSUFFICIENT      ← 这是对的，当天确实没有
ARM    THIN 起步              ← 战略转型那条，加上腾出的 4 个名额可能还有
AMD    THIN 或 SUFFICIENT     ← 沙特系统上线（两条报道）
```

回归语料从 38 条加到 46 条，把今天这批全收进去了，正反两个方向都有。
五条新规则逐条改坏做了变异测试，语料每次都抓到：

```
去掉标题现在时       抓到 3 条判错      清单体退回行首锚   抓到 1 条
去掉不定式守卫       抓到 2 条判错      疑问式退回行尾锚   抓到 1 条
去掉部署里程碑事件    抓到 3 条判错
```

---

## 关于"扫出来是 0"这件事本身

前面几轮我一直说"别看百分比看材料"。这一轮反过来印证了同一件事：
**0% 和 57% 一样，都不能自证。** 57% 那次是闸门被拆了，
这次是三分之二的标的漏判——而两次的日志都完全正常。

真正能判断的只有一件事：把材料逐条读一遍，问它讲的是
**这家公司发生了什么**，还是**作者觉得它值多少钱**。

---

## 自检

```
CIO_MARKET=us .venv/bin/python scripts/check_build.py     117 项
CIO_MARKET=us .venv/bin/python scripts/test_intake.py      40 项
```
