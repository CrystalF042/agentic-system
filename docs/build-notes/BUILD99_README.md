# build99 —— 破折号前是事实，破折号后是钩子

```
cd ~/.openclaw/workspace/cio-agent && unzip -o ~/Downloads/cio-build99.zip -d . && .venv/bin/python scripts/check_build.py
```

`全部 119 项通过` 之后再扫一轮，`--verbose`。

---

## 一天两条真事实死在同一个句式上

```
Arm Holdings Has $2 Billion in Orders It Cannot Fill Yet — Is ARM Stock a Buy at $257?
↑ 20 亿在手订单填不满产能，硬事实                          ↑ 荐股钩子

IBM Just Opened Its Mainframes to Arm — Is the Market Missing the Shift?
↑ Arm 架构打进大型机                     ↑ 观点问句
```

标题否决看的是**整条标题**，所以后半句的钩子杀掉了前半句的事实。

这个结构在财经标题里极常见，而且它**恰好把最有价值的材料挑出来杀**——
写手会把最硬的事实放前面当噱头，再挂一个钩子。

## 改法：分句判定

整条标题被硬标记否决时，先看事实是不是只在其中一个分句里。三道闸：

```
一  必须是多分句      破折号 / 冒号 / 分号 / 带空格的连字符。
                    **不拆逗号** ——"AMD, Cisco and HUMAIN Expand …" 会被拆坏
二  分句自身不含硬标记  否则就绕回 build96 那个缺陷：观点文只要在标题里
                    引一个真数字就能被顶成实质
三  分句自身站得住     完成动作 + (锚点 | 事件)，
                    或 锚点 + 事件 且不含前瞻标记
```

第三道里那条"没有完成动作也算"的通路是新开的，因为
`Has $2 Billion in Orders` 是**状态**不是动作——而它和"签下 20 亿订单"
一样可核对。完成动作原本就只是"不是前瞻"的一个近似；这里改用更直接的判据：

```
Analysts See $5 Billion in Orders for AMD — Is It Enough?    ← See，别人的估计
AMD Could Win $5 Billion in Orders — Is It Enough?           ← 情态动词
AMD's $10 Billion Opportunity in Sovereign AI — …            ← 「机会」是估算
```

判定理由会写清楚是靠哪半句过的：

```
实质·分句含可核对事实「Arm Holdings Has $2 Billion in Orders It Can…」（同标题另一半是买卖建议观点文）
```

## 顺带补的两个词形

```
orders   原来只收 order book，于是"$2 Billion in Orders"命不中任何事件词
         **只收复数** —— 单数 order 会被 "in order to" 命中
see      前瞻表里只有 sees，而标题写的是 "Analysts See"
```

**这已经是本模块第四次栽在词形上**（acquired/acquisition、approved/approval、
order/orders、see/sees）。前瞻表这次改成了词干加后缀写法，注释里记了。

---

## 我在这个 build 里犯的两个错，都是探针抓出来的

**一、`Analysts See $5 Billion in Orders` 一度被判成实质。** 前瞻表里写的是
`sees`，而标题用的是裸词形 `See`。这条不修的话，新开的通路会把
"分析师预计"整类顶成事实——正是这个模块存在的全部理由所反对的。

**二、变异测试发现"分句自身不含硬标记"这道闸没有被任何用例覆盖。**
我把它改坏，56 条语料一条都没红。原因是我没有一个
**分句里同时有硬标记和真数字**的判例。补了两条：

```
Is AMD a Buy After Its $10 Billion Contract? — Analysts Weigh In
3 Reasons AMD's $10 Billion Saudi Contract Matters — Our Take
```

两条都必须判「无实质」——合同是真的，但这半句本身就是荐股问句和清单体。
现在改坏那道闸，语料立刻红。

变异测试结果：

```
去掉分句救援    MISS      逗号也当分隔符   MISS
分句不查硬标记   MISS      orders 收单数   MISS
去掉前瞻守卫    MISS      单分句也救      MISS
```

## 又删了一个不承重的常量

`_CLAUSE_MIN_CHARS = 12`（跳过太短的碎片）改成 0 之后，**一条判定都没变**——
碎片本来就不含证据，它什么也没在守。而且它会反向误伤：`AMD — $2B orders`
这样的短分句是合格的事实，却会被长度下限挡掉。删了。

和之前删掉的 `_ENOUGH` 一样：**不承重的常量只会让人以为调它有用。**

---

## 这一轮预期

ARM 的 `$2 Billion in Orders` 那条会从「无实质」变成「实质」→ ARM 到 2 条。
其余不该有变化——这次动的是标题否决的**例外**，没有动否决本身。

**如果别的票也冒出新的「分句含可核对事实」，请把那几条发我。**
这条规则是这个模块里最容易误伤的一处，我只在真机上验过两条。

上一轮列的另外两项照旧没动：
判定错误（AMD `Not a Revenue Windfall`、KLAC `What … Means For Shareholders`）
和按事件计数，那是下一个 build。

---

## 自检

```
CIO_MARKET=us .venv/bin/python scripts/check_build.py     119 项
CIO_MARKET=us .venv/bin/python scripts/test_intake.py      45 项
```
