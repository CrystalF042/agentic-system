# build109 —— 冻结 setup 与事件定义 + 卡片落盘

```
cd ~/.openclaw/workspace/cio-agent && unzip -o ~/Downloads/cio-build109-setup-freeze.zip -d . && ls -l src/cio/technical/setups.py && .venv/bin/python scripts/check_build.py && .venv/bin/python scripts/test_technical.py
```

`134 项` + `29 项`。累计包。

---

# 先说我错在哪

**「三个条件接近独立 → 只是凑巧一起 → 不构成形态」这个推论是错的，你说得对。**

我拿 `P(A∩B∩C) > P(A)P(B)P(C)` 去回答 `P(结果 | A∩B∩C)`。这是两个问题，
第二个才是要问的，而第一个答不了第二个。

更要命的是方向也反了：**如果三者高度相关，那才是问题**——那说明把同一个信息
数了三遍。participation / accumulation proxy / location 本来就该是三个独立的信息面，
接近独立是**设计意图**。我把它当成缺陷报了。

**1.93× 那条我也认。** 我从表里挑了最大的数字说它"唯一像样"——
这正是我整段时间在警告的行为。而且它很可能连市场事实都不是：

```
CMF 每根 K 线的乘数 = ((C−L) − (H−C)) / (H−L)
                    收盘越靠近当日最高，越接近 +1
```

**CMF 本身就在测"收盘落在日内区间的什么位置"**，而贴近年内高位的票，
本来就更常收在日内高位。两个度量共享一段构造。1.93× 可能是算术，不是经济。

**幸存者偏差那条更不该由我漏。** `universe_pit = False` 是我自己在这个 session
里列进待办的硬约束，然后我提了一个会直接骑过去的方案。

**事件定义那条是 build100 的复发。** 我在材料闸门里刚修完"同一件事被转载三次
就顶开闸门"，转头在新模块里提了一个没有事件定义的研究方案。同一个缺陷，换个地方。

**720 只日不是 720 个独立样本，我那个置信区间是装饰不是推断。** 6 个采样日
每隔 5 个交易日，而指标用的是 20 日窗口——**相邻两个采样日共享 15/20 天的输入**。

---

# 我查了一件事，它会改变 PIT 的做法

`universe_pit = True` **不是改一个 flag，是一个数据问题。** 现状：

```
raw-data/quant_snapshots/  只有 6 份 sp500 快照，跨 2026-08-21 … 2026-09-01
这 12 天里成分变动 0 次
```

代码自己的注释写着"累积快照后由回测区间判定升级"——也就是说今天把它置 True，
只对**这 12 天**是诚实的。往前两条路：

**往前存**（快照每天存，一年后就有一年的 PIT 成分）——这条一定成立。
**往后补**（用维基那张 "Selected changes to the S&P 500 component stocks" 的
增删日期表，从今天的名单倒推回去）——membership 能补回来。

但**倒推补不回来的是被剔除/退市那些票的价格历史**。免费源大概率取不到，
而对"贴近价区、量能改善"这类形态，**那批名字恰恰是信息最多的**。

**这一条我没能验证。** 我在容器里试了 FISV/ATVI/SIVB，全都失败——
但同一批里 AAPL 也是 0 行，说明失败的是网络（代理 403），不是退市。
（这就是为什么要放对照组。）你机器上一行就能验：

```
.venv/bin/python -c "
import yfinance as yf
for t in ['AAPL','SIVB','ATVI','FISV']:
    print(t, len(yf.Ticker(t).history(period='2y', auto_adjust=True)))"
```

`AAPL` 有几百行而后三个是 0 → 免费源确实取不到退市数据，
那 PIT event study 就必须带一句"PIT 名单里有 N 只无法取价"的明确说明，
而不是假装样本完整。

---

# 这一版做了什么

## 一、冻结 setup（`src/cio/technical/setups.py`）

```
SETUP_VOLUME_ACCUMULATION_AT_ZONE_V1   setup-1.0.0   指纹 3b61f7d65bc7d9b2

A 参与度    近20日量比≥1.5 的天数 ≥ 5      基础率约 8%（中位数 2、p90 4）
B 量能代理   CMF20 > 0.10 且 OBV 斜率 > 0   约 23%（CMF 的 p75 是 0.095）
C 位置      距上方价区 ≤ 0.5 个 ATR20      约 22%
```

每个数字的来历都写进模块了，而且**全部在看任何远期收益之前定下**。

C 那个 0.5 **不是新参数**——它是 `CLUSTER_ATR_MULT`，价区算法自己的聚类容差，
"距离不到一个聚类容差"等于"已经贴在这个价区上"。不引入新的自由度。

代码里它是 `C_MAX_ATR_TO_ZONE = CLUSTER_ATR_MULT`，**引用，不是抄一个 0.5**。
有一条探针走 AST 检查这一点——变异测试里我把它换成字面量 `0.5`，
断值的那条断言照样通过（0.5 == 0.5），改成断 AST 结构才红。
**这是这个项目第九次踩「断值不断结构」。**

## 二、事件定义

```
False → True    事件开始（t=0）
持续  True      同一事件，不是新样本
转    False     复位
复位后 5 个交易日内再 True   仍并入上一个事件，并留痕（merged_repeats）
```

冷却期取 5，理由和 `MIN_TOUCH_GAP` 同源：量能指标用 20 日重叠窗口，
相隔一两天的两次触发共享 18–19 天输入，当成两个独立样本是自欺。

## 三、卡片落盘（`store.py` + `scripts/technical_snapshot.py`）

```
.venv/bin/python scripts/technical_snapshot.py --limit 120   存今天
.venv/bin/python scripts/technical_snapshot.py --status      看积累了多少天、版本有没有混
.venv/bin/python scripts/technical_snapshot.py --events      看已推导出的事件
```

三条规矩：

- **一天一个文件，写过就不再写。** 参数改了之后重跑历史，会把过去每一天
  按新参数改写——而新参数下的历史当然更好看，因为它本来就是拿这段历史调的。
  要覆盖必须显式 `--force`，并且日志里会说明覆盖了哪一天。
- **每行盖三个版本号**（schema / algo / setup）。混版本不是错，看不出来才是。
- **只存卡片，事件从卡片流里推导。** 筛子的 KPI 和 setup 的 KPI 要的是同一份
  数据的两种切法；只存事件，筛子那半边就没了。

## 四、变异测试

七个变异，五个当场被抓。**两个漏网的**：C 抄成字面量（上面说了），
以及"不区分算不出来/不成立"——check_build 那条没查 `unknown`。两条都补了。
还有一条是我的探针误报：它把 `store.py` 的 `open()` 当成污染，
而存储层的职责就是写文件——改成显式白名单 `IO_ALLOWED = {"store.py"}`，
并断言这个白名单只有它一个，新增一个会读写文件的模块必须专门来改一行。

---

# 我同意你的排序，加两条

**筛子 KPI 是近期主 KPI，交易 setup 的收益是额外研究**——同意，
而且这一版的存储结构就是按这个分的。

补充两件：

**一、matched control 不能匹配 setup 自己的成分。** 同日、行业、波动率、市值
都该匹配；但**不能匹配"距上方价区的距离"或"近20日放量天数"**——
那等于把要检验的东西匹配掉了。四个里**同日匹配最重要**，
它把"牛市某个月"这个混淆按构造消掉。

**二、时间表要说清楚。** 全市场约每天 1–2 只命中，往前存到 300 个事件
大概要大半年（`--status` 会按实际积累速度算给你看）。
所以近期能回答的只有筛子那一栏；setup 那一栏要么等，要么等 PIT 倒推做成。

---

# 装完的顺序

```
1  .venv/bin/python scripts/check_build.py                            134 项
2  .venv/bin/python scripts/test_technical.py                         29 项
3  .venv/bin/python scripts/technical_snapshot.py --limit 120         开始积累
4  .venv/bin/python scripts/technical_snapshot.py --status            看一眼
5  上面那段 yfinance 退市数据的一行验证                                 决定 PIT 怎么做
```

第 5 步的结果发我 —— 它决定 PIT event study 能做成什么样。
