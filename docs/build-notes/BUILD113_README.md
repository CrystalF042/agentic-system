# build113 —— 家族重构 + 覆盖度 + Python 3.9

```
cd ~/.openclaw/workspace/cio-agent && unzip -o ~/Downloads/cio-build113b-coverage.zip -d . && .venv/bin/python scripts/check_build.py && .venv/bin/python scripts/test_technical.py && .venv/bin/python scripts/technical_snapshot.py --force
```

`139 项` + `49 项`。

---

# 零、先说上一版为什么在你那边红

```
AttributeError: module 'cio.technical.setups' has no attribute '__annotations__'
```

那一行是我写的一句**根本没用到的废代码**（`doc = ...` 赋完就再没被读过）。
3.10 起模块对象自带 `__annotations__`，**3.9 不带**。

真正的问题不是那一行，是**我这边的"干净安装模拟"跑在 3.11 上，
而你的 venv 是 3.9。** 那不是干净安装，那是在另一台机器上安装。
两个测试套在我这里全绿，在你那里红——**这正是这个项目一直在防的那类
"看不见的差异"，而我自己就是发生地。**

已经做的两层：

```
1  容器里装了真的 CPython 3.9.23，本次全套（139 + 49 + 11 个测试文件）
   都在 3.9 上跑过一遍才打包
2  新探针 _b113_source_stays_inside_python_39 扫源码里已知的 3.10+ 构造：
   match 语句 / 模块级 __annotations__ / zip(strict=) / pairwise / dataclass(slots=)
```

**探针挡得住语法构造，挡不住语义差异**——后者只有真在 3.9 上跑才知道。
所以第 1 条才是主要的那条，第 2 条只是保险。

---

# 一、你复核的三条，全做了

## 1  族名写死成 `volatility_extremeness`

你说得对：叫 `_strength` 或 `_quality`，早晚有人把 0.9 读成"高波动是利好"。
**分数长得完全一样，名字是这里唯一防得住误读的东西。** 有一条断言钉着
`volatility_strength` / `volatility_quality` 不许出现。

聚合方式不变：`|分位 − 0.5| × 2`，`percentile=0.05` 和 `0.95` 都得 0.9。

## 2  NR7 —— 先说事实：它本来就不在分里，也不在闸门里

我核对过再改的：闸门是 `A_participation ∧ B_accumulation ∧ C_location`，
**没有任何波动条件**；波动族的成员只有 `atr_percentile_252` 和
`range_pct_20_percentile_252`。NR7 只在卡片上显示。

所以这次做的不是"把它拿出来"，是**把这个排除变成明写的、有理由的、
被钉住的**：

```python
EXCLUDED_FROM_SCORE = (
    ("volatility", "is_nr7",
     "NR7 只代表收缩这一端。本族是双边异常，加一个单边证据会让整族"
     "天然偏向 compression —— 混了方向，而且看不出来混了。"
     "要用它就得先拆成 compression / expansion 两个单边量，v1 不做。"
     "**它继续显示在 Signal Card 上，只是不进分。**"),
)
```

**一个字段"碰巧没被加进来"和"经过判断决定不加"，在代码里长得一模一样。**
现在有一条用例：排除名单里的字段出现在任何一族里即红；同时还要求它
**仍然在卡片上**（排除的是"进分"，不是"删掉"）。变异测试里"顺手把 NR7
加进波动族"这一条被两个测试套都抓到了。

你提的 compression / expansion 拆分我写进了模块文档，作为"要用 NR7 的前提"。
v1 不做。

## 3  覆盖度

```
today_line：
今天 2 只通过闸门，按家族分排前 1 只：AMD 0.76/REVIEW（5/5 族）
（另有 1 只通过闸门但覆盖度不足、不报分不排名：NEW0）

describe：
AMD　排名 1　分数 0.7595　REVIEW　覆盖度 5/5（100%）
    （…… **覆盖度不同的分数不能横着比。**）
    structure               55%　　zone_distance 45%、range_position 64%
    volume                  93%　　rvol 91%、spike_days 95%
    accumulation            74%　　cmf 82%、obv_slope 70%、up_down_volume 71%
    relative_strength       64%　　excess_mkt_63 66%、excess_sector_63 61%、rs_slope 64%
    volatility_extremeness  82%　　atr_percentile 86%、range_percentile 77%

NEW0　**没有分数**　覆盖度 2/5 低于下限 3/5
    （**没有分数 ≠ 分数很低。** 它通过了闸门，只是可用的信息不够，
      报不出一个横截面位置。）
```

`MIN_FAMILIES = 3`：少于 3/5 族 → `score = None`、无 band、无排名，
但**仍然通过闸门、仍然显示、仍然带着各族分**。

**这个下限是我做的第二个判断，和 `UNUSUAL` 一样请你复核。**
理由：五族里少于三族，缺掉的已经不是少数派证据。

### 这条不是构造出来的边界，它天天会遇到

**上市不满一年的票就是 2/5。** 252 日分位算不出来 →
structure、volatility_extremeness、relative_strength 三族全缺。
2.0.0 会给这种票一个看起来很精确的分数并把它排进队列；2.1.0 说"说不出"。

### 顺手修的一个数错

`today_line` 原来把"没分数"的票算进了"超出注意力预算"。**两回事**——
合成一个数就说不清"没进队列"是今天太忙，还是这只票信息本来就不够。

---

# 二、版本升到 2.1.0，因为语义变了

```
1.0.0 → 2.0.0   结构变了（四个平铺指标 → 五族等权）
2.0.0 → 2.1.0   语义变了（族名进指纹；"什么时候敢报分"变了）
```

2.0.0 下会给出一个数的票，2.1.0 下可能没有分数。**这不是数变了。**
指纹也跟着变（`MIN_FAMILIES` 和排除名单都进指纹）：`518a85faa7ed1209`。

---

# 三、变异测试：十二个，三轮

第一轮六个（家族层）**两个从 check_build 漏网**，第二轮六个（本次新增）
**又有三个漏网**。三个漏网的原因**没有一个是断言写弱了**：

```
覆盖度不足照样报分        夹具里每只票都是 4/5，那个分支根本没走到
覆盖度只存字段不印出来     我写了 `if scored is not None:` —— 夹具没造出
                        通过闸门的票时，这条断言压根没执行
没有分数被折成 0 分       "一族都算不出来"这个分支在任何正常夹具下都是死代码
```

第二条是我这个项目里的老毛病又犯了一次：**条件断言等于没有断言。**
改成直接构造 `Ranked` 调 `describe()`，不依赖闸门是否开。
第三条改成造一张把所有 block 清空的卡片走一遍，外加一条不依赖夹具的
恒等式：**`score is None` ⟺ `no_score_reason` 非空**——它同时拦住
"折成 0 分"和"静默变 None"两种写法。

补完之后**十二个全被两个测试套抓到**：

```
族权重跟着成员个数走        test✗ check✗      NR7 顺手加进波动族      test✗ check✗
波动族被赋了方向            test✗ check✗      族名改回 _strength      test✗ check✗
分档当闸门用               test✗ check✗      覆盖度不足照样报分       test✗ check✗
缺的成员补 0.5             test✗ check✗      覆盖度只存不印          test✗ check✗
距价区方向写反             test✗ check✗      覆盖度按全部族算         test✗ check✗
字段名打错一个字母          test✗ check✗      没分数折成 0 分          test✗ check✗
```

---

# 四、Unit B：架构冻结，代码不动

按你说的办。写进项目文档
`claude/CIO_打分层语义冻结_Triage_vs_Profile.md`：

```
Technical Triage Score   →  谁优先研究
Unit B Profile           →  被研究的公司相对市场是什么状态
```

**不可比、不相加、不排在同一张表里。** 代码里的名字等下次本来就要改
Unit B 报告时一起改——为了一个名字单独动一个稳定部门不值得。

同一份文档里也冻结了：那份事件研究否定的是**机械交易 setup**，
没有否定**人工研究筛子**；**不调阈值，也不换 setup 去搜下一个漂亮结果。**
现在唯一还没有数据的那一半是复核台账：

```
.venv/bin/python scripts/technical_snapshot.py --review      看待复核队列
.venv/bin/python scripts/technical_snapshot.py --mark SYM    标记复核结论
```

**推出来的票，人工看完之后有多少确实值得 Unit A 花时间——这才是筛子的 KPI。**

---

# 装完跑这三条

```
1  .venv/bin/python scripts/check_build.py              139 项
2  .venv/bin/python scripts/test_technical.py            49 项
3  .venv/bin/python scripts/technical_snapshot.py --force
```

**"今天没有"是一个正常的、常见的输出。**
