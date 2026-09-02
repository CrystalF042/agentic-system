# build110 —— 正式前向采集

```
cd ~/.openclaw/workspace/cio-agent && unzip -o ~/Downloads/cio-build110-forward.zip -d . && ls -l src/cio/technical/review.py && .venv/bin/python scripts/check_build.py && .venv/bin/python scripts/test_technical.py
```

`135 项` + `34 项`。**不加新指标，不做评分。** 你列的五条全在里面。

---

## 1 · SyntaxWarning 修了

你说得对，`is` 比的是对象身份不是数值，那行等于没断言。不过不能照抄成
`is not None`——那条用例里它**本来就该是 None**（前一行刚断言过）。
真正要防的是"被写成了一个数"，所以断类型：

```python
assert v["atr_percentile_252"] is None
assert not isinstance(v["atr_percentile_252"], (int, float)), \
    "一年分位被写成了一个数（多半是 0）——数据不够时必须是 null"
```

这样 0、0.0、`False` 全都会红，不只是 0。

## 2 · `--limit` 不能当正式数字

你抓得准。`get_universe(limit)` 是**先放关注池主题命中的 31 只，再按源顺序补齐**——
偏科技/医药，后半段还是字母序。用它算基础率再外推到 500 只，是把一个有偏样本
当随机样本。

现在带 `--limit` 跑会直接印警告：

```
⚠ **用了 --limit 120：这不是随机抽样**（先关注池、再按源顺序补齐）。
   基础率会偏，**不要拿它外推到 500 只**。正式数字请不带 --limit 跑。
```

**正式那一跑不带 limit：**

```
.venv/bin/python scripts/technical_distribution.py --days 6
```

500 只 × 400 天，第一次取数会久一点（板块 ETF 已经在缓存里了）。

## 3 · 每天存 universe snapshot → PIT 数据库自然长出来

你那句"`universe_pit=False` 不代表今天开始不能积累 PIT 数据"是对的，
而且**这件事已经在发生**：`get_universe` 每次成功抓到成分表就存一份快照，
所以每天跑一次 snapshot 脚本，PIT 历史就自己在长。原来它是个静默副作用，
现在明着报：

```
成分快照覆盖 2026-08-21 … 2026-09-01（6 份）　→ 只有这段区间的成分是 point-in-time 的
```

而且 `universe_pit` 不再是一个全局布尔：

```python
q.universe_pit_for("2026-08-25", "2026-08-29")
  → (True,  "落在快照覆盖内（6 份，2026-08-21…2026-09-01）")
q.universe_pit_for("2026-01-01", "2026-09-01")
  → (False, "超出快照覆盖 —— 超出的那段用的是「今天」的成分，带幸存者偏差")
```

**写死 False 会让人以为这事永远做不了；写死 True 是撒谎。按区间判才是真话。**

## 4 · 事件带完整血统 —— 你这条是本轮最有价值的一条

我漏了。条件 C 是"距上方价区 ≤0.5 ATR"，而价区是 `sr-1.0.0` 算出来的。
`sr-1.0.0 → sr-1.1.0` 之后，setup 三个阈值一个都没改，
**但它筛的已经是另一批东西**——而 `setup_version` 还是 `setup-1.0.0`。
只按它分组，两套定义下的事件会混成一堆，混得毫无痕迹。

现在血统是四元组，事件和卡片都带：

```
(setup_version, setup_fingerprint, zone_algo_version, card_schema_version)
```

还多加了一条你没提但同源的：**一个事件不能横跨两套定义**。
血统一变就截断，并标记 `ended_by_version_change=True`——
前半段按旧价区算法成立、后半段按新的，那它不是一个事件，缝在一起是假的。

另外**血统从卡片里读，不用当前代码的**。半年前的卡片是按当时的算法算的，
用今天的版本号给它盖章，等于把历史改写成"一直都是这套定义"。
（变异测试里我把它改成用当前版本，`check_build` 第一遍没红——补了探针。）

## 5 · 每日报表：`--table`

```
.venv/bin/python scripts/technical_snapshot.py --table
```

就是你画的那张表：

```
日期           universe    命中    新事件    持续中   新事件标的
2026-09-02          2     1      1      0   AMD
2026-09-03          2     2      1      1   NVDA
2026-09-04          2     2      0      2   —

每日新事件数的分布（6 天）
  0 次：4/6　67%
  1 次：2/6　33%

事件持续天数：中位数 3，最长 3，共 2 个
```

**从已存卡片现算，不另建一份汇总存储。** 两份存储迟早对不上：
卡片说 3 条命中、汇总表说 2 条，谁对？只存卡片就没有这个问题。
跑够 10–20 个交易日之后，行业分布那一栏我再加（现在加是空的）。

---

## 顺带补的两件（都是"正式采集"的前提）

**一、快照必须跑在收盘之后。** 盘中跑会把一根**没走完的 K 线**当成当天收盘：
量比、CMF、ATR 全算在半天数据上，而卡片上写的日期是今天。不报错、图上看不出来，
要把两天的卡片摆一起才发现数对不上。现在有窗口闸（美股 16:30–23:59 ET），
窗口外在取数之前就退出。

美股收盘后的窗口换算到北京是**次日上午**——所以：
盘前简报落在你傍晚、卡片快照落在你早晨，**两个都是按市场时间排的**。

**二、筛子的主 KPI 现在测得了。** 你说筛子 KPI 是主 KPI、setup 收益是额外研究——
我同意，但在这一版之前**那个主 KPI 根本没法测**：系统每天推一个名字，
没有任何地方记录"我看了，值/不值"。于是筛子好不好用只能靠印象，
而印象会被最近一次的成败带着走。

```
.venv/bin/python scripts/technical_snapshot.py --review
.venv/bin/python scripts/technical_snapshot.py --mark A worth 财报后放量还没走完
```

三档：`worth` / `skip` / `unclear`。**`unclear` 必须存在**——逼人二选一
会把犹豫记成假的确定。**`worth` 的意思是"值得占用研究时间"，不是"会涨"**；
用涨跌回填这一栏，两个 KPI 就又混成一个了。

改主意就再记一条，旧的留着，`--review` 会把改过的列出来。

---

## 每天要跑的两条

```
0 * * * 1-5  cd ~/.openclaw/workspace/cio-agent && .venv/bin/python run_premarket.py
0 * * * 1-5  cd ~/.openclaw/workspace/cio-agent && .venv/bin/python scripts/technical_snapshot.py
```

两条都是每小时敲一次门，各自的窗口闸决定跑不跑。夏令时切换不用管。

第一次先手动跑一次正式的（不带 limit）：

```
.venv/bin/python scripts/technical_snapshot.py --force
.venv/bin/python scripts/technical_distribution.py --days 6
```

把 `SETUP_V1 = ? / 502` 那一行发我 —— 这才是第一个正式数字，
之前那个"每天 1–2 只"是从有偏的 120 只外推的，作废。

## 还没做的（记账）

- **历史 PIT universe**：维基那张成分增删表能把 membership 倒推回去，
  但**退市票的价格历史大概率取不到**。那一行验证命令还在 BUILD109 里，
  跑一下把结果发我，它决定要不要花力气做倒推。
- **行业分布**：`--table` 里还没有，等跑够 10–20 天再加。
- v2 打分：**不做**。先描述、后评分。
