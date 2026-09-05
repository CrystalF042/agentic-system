# build111 —— NaN 是第三种状态

```
cd ~/.openclaw/workspace/cio-agent && unzip -o ~/Downloads/cio-build111-nan.zip -d . && ls -l src/cio/technical/numbers.py && .venv/bin/python scripts/check_build.py && .venv/bin/python scripts/test_technical.py
```

`136 项` + `38 项`。**不加指标、不改阈值、不打分。**

---

## 缺陷

整个 v1 建立在一条规矩上：**算不出来必须是 `null`，而且必须有原因**。
我处理了 `None`。**漏了 `NaN`。**

你那次 502 只的全市场跑把它印出来了：

```
cmf_20            p10 -0.031  p25 -0.326  p50 -0.355   ← 分位数不单调
atr_pct_14        p10 0.0247  p25 0.0166  p50 0.0153   ← 同上
rs_mkt_slope_20   p50 nan                              ← 直接是 nan
```

分位数在数学上必须单调递增。机制我验过：

```
sorted([0.5, nan, 0.1, 0.9, 0.3, 0.7, 0.2])
→ [0.5, nan, 0.1, 0.2, 0.3, 0.7, 0.9]
```

NaN 和任何数比较都返回 False，`sorted()` 的不变量直接失效，**不报错**。

## 更严重的一半

NaN 一路穿到了冻结的 setup 判定里。一根缺量的 K 线：

```
cmf_20 / obv_slope_20 / up_down_volume_ratio_20   全变 NaN
reasons 里什么都不写         NaN 不是 None，`is None` 检查漏过
NaN > 0.10 → False          静默判成"不成立"
unknown = []                于是"算不出来"被记成了"不成立"
```

**任何一只票只要有一根坏 K 线，就被无声地排除在命中之外，而卡片看起来完整。**
所以 `1 / 502` 是**下界，不是计数**——`11 次 / 3012 只日` 同理。

这正是 v1 全部设计要防的形状，只是换了一种数据类型进来。

---

## 改了四处

**一、`numbers.py`（新）—— NaN / inf / None 收成同一个出口。**

```python
finite(nan) → None      finite(inf) → None      finite(0.0) → 0.0
```

`0.0` 必须留着——它是一个真实的数，不是"算不出来"。布尔字段（`is_nr7`）
也不当数处理。

**二、`scrub()` 统一洗，而不是逐个函数包。**

逐个去写 `round(x, 4) if x is not None`，**加一个新度量就会漏一次，而漏了不报错**。
所以每个 `measure()` 末尾调一次 `scrub(vals, why)` 洗整块（递归一层，
`accumulation_pressure_proxy` 里的数也洗），`observe()` 再兜一道底。

**三、面板进门体检，只数不修。**

```python
panel_health(df) → {"rows": 300, "nan_rows": 1, "nonpositive_volume": 0,
                    "nonpositive_close": 0, "inverted_bars": 0}
```

四项各对应一种真实见过的脏数据：yfinance 缺行、停牌日零成交量、复权异常的负价、
源数据错位的 high < low。结果进卡片，问题进 `reasons`。

**刻意不修。** 补一根插值出来的 K 线会让所有度量都算得出来、而且看不出是补的——
那比留一个 null 糟得多。

**四、分位数排除 NaN。** `_q()` 先剔再排序。

---

## `setup-1.0.0 → setup-1.0.1`：三个阈值一个都没改

**这是你那条血统论证的第一次实际应用。** 参数指纹没变（阈值确实没动），
但同一天同一只票在两个版本下可能给出不同结果——1.0.0 会把 NaN 静默判成
"不成立"，1.0.1 判成"算不出来"。

所以版本必须动，两版的事件不能混在一起统计。有一条探针钉住这件事：
`SETUP_VERSION == "setup-1.0.1"` 且指纹不变 且升版本的理由写在源码里。

**你机器上 2026-09-01 那份卡片盖的是 `setup-1.0.0`，按"写过不重写"留着。**
不用管它——血统机制会把它和新版的事件自动分开，`--status` 的版本分布那栏
会显示成两个版本，那是对的。

---

## 变异测试

五个变异，**五个全被抓**：

```
finite() 放 NaN 过去            test✗ check✗
去掉 observe 的兜底 scrub       test✗ check✗
scrub 不洗嵌套 dict             test✗ check✗
行为变了却不升版本               test✗ check✗
体检漏掉零成交量                 test✗ check✗
```

---

## 装完跑这几条

```
1  .venv/bin/python scripts/check_build.py                     136 项
2  .venv/bin/python scripts/test_technical.py                  38 项
3  .venv/bin/python scripts/technical_distribution.py --days 6  全市场，不带 limit
4  .venv/bin/python scripts/technical_snapshot.py --force       今天的卡片（1.0.1 版）
```

**第 3 步这次的数才是干净的。** 上一次那三列（cmf_20 / atr_pct_14 /
rs_mkt_slope_20）作废，重跑之后分位数应该单调递增了。

命中数可能会变——之前被 NaN 静默排除的票现在会走 `unknown`，
所以 `SETUP_V1 = ? / 502` 这个数以这次为准。把它和分位数表发我。

---

## v1 到此为止

这是 v1 最后一个已知缺陷。修完之后该做的是**让它跑 10–20 个交易日**，
不是继续加东西。v2（打分 + 接 Router）要等样本，不是等代码。
