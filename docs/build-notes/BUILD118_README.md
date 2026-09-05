# build118 —— 复核台账记得住「什么时候判的」

```
cd ~/.openclaw/workspace/cio-agent && unzip -o ~/Downloads/cio-build118-review-lag.zip -d . && .venv/bin/python scripts/check_build.py && .venv/bin/python scripts/test_technical.py && .venv/bin/python scripts/technical_snapshot.py --review
```

`145 项` + `56 项`。

**不改 setup、不改分数、不改任何筛选条件。** 只动台账。

---

# 为什么现在做

你正要开始攒这个数。等攒到 30 条再补，那 30 条全都说不清是什么时候判的。

一句话说清它防的是什么：

> "Technical Gate 筛出来的股票有 63% 人工认为值得研究。"
> —— **你是什么时候判断值得的？**

在这一版之前，台账**答不上来**。`mark()` 的签名里有 `reviewed_at`，
但 `--mark` 从来不传，所以它永远是空字符串——**当天判的和两周后补判的，
在文件里长得一模一样**。

---

# 五条

## 一、`reviewed_at` 自动填，跟市场时区

```
2026-09-04T20:54:09-04:00
```

带偏移量的 ISO，时区跟 `CIO_MARKET=us` 的 `America/New_York` 走，
**不用机器本地时间**（机器搬个时区，同一批复核的口径就变了，而台账上看不出来），
**也不让 CLI 传**（让人自己填时间就是多开一个人为错误入口，
而这个字段的全部价值在于它是自动的、没法事后凑的）。

## 二、延迟按**交易日**，不是日历天

```
周五信号 2026-09-04 + 周一复核 2026-09-07  →  1     不是 3
当天                                        →  0
```

**不含节假日**，而且这一层刻意做成**保守**的：感恩节那种情况会把真实的
lag=1 算成 2，于是那条复核被推进**更严格**的桶。宁可把干净的算成脏的，
不能反过来——这个数存在的唯一理由就是防止污染混进主 KPI。

复核日期早于信号日期时返回 `None`，**不猜**。

## 三、分桶统计，主 KPI 只看当天

```
复核统计　setup-1.0.1
  当天判的（进主 KPI）          值得 1 / 不值得 1 / 看不出来 1　→ 值得率 33%（n=3）
  隔一个交易日（次要口径）        值得 1 / 不值得 0 / 看不出来 0　→ 值得率 100%（n=1）
  事后补判（不进主 KPI）         —　（没有进分母的记录）
      另有 1 条 excluded（**不进分母**）
  没有复核时间戳（老记录，不进主 KPI） 值得 1 / …　→ 值得率 100%（n=1）
  **主 KPI：33%（n=3）** —— 信号当天、在不知道后续走势的情况下判的
```

`CLEAN_MAX_LAG = 0`。为什么不是 1：隔一个交易日，那一天的走势已经看得见了。
T+1 单独展示、可以当次要口径，**但不许并进主值得率**。

**老记录（没有 `reviewed_at`）单独一桶，不许并进 clean。**
我们没有任何证据说它们是当天判的；把它们算进主 KPI，
等于用一个不知道的东西去撑一个要人相信的数。
——你现在台账里那条 BBY 就是这种。

分母是 0 时返回 `None` 而**不是 0%**：没有样本和"一条都不值得"
是两件完全不同的事，而 `0%` 会把前者说成后者。

## 四、`excluded` 独立一档

```
.venv/bin/python scripts/technical_snapshot.py --mark A excluded retrospective_contamination --on 2026-09-01
```

它**既不算 worth，也不算 skip，也不进分母**——把错过时机的复核记成 skip，
等于凭空造出一个"当时不值得研究"的结论。

**必须写理由**，没理由的 `excluded` 会被拒绝：一条没有理由的排除，
和一条被悄悄丢掉的记录没有区别。

标了之后它离开待复核队列，不再碍眼。

## 五、重复 mark 幂等

```
--mark BBY worth  →  记下了
--mark BBY worth  →  已经复核过，判定未变 —— 没有写入新记录
--mark BBY skip   →  改判：…（原来是 值得研究）
```

原来是照写第二行、靠 `stats()` 在读的时候去重——**那是一种静默行为**：
台账里躺着两条一样的记录，而"去重了"只发生在统计阶段。
**台账本身应该是干净的，不该靠统计阶段收拾。**

改判定才追加，新那条带 `previous_verdict`。

## 顺带修的一个解析 bug

`--on <日期>` 里那个日期会掉进"理由"里——原来只过滤以 `--` 开头的词。
不报错，只是理由末尾多一个日期，**而真正想指定的那一天没生效**。

---

# 你现在该做的两件事

```
1  .venv/bin/python scripts/technical_snapshot.py --mark A excluded retrospective_contamination --on 2026-09-01
2  .venv/bin/python scripts/technical_snapshot.py --review
```

第 1 条把那个已经污染的 09-01 A 清出队列。
第 2 条会告诉你主 KPI 现在是"还没有样本"——**因为 09-04 那两条是在
build118 之前记的，没有时间戳，落在 unknown 桶里。**

**清账从下一个命中开始。** 这不是浪费：三条说不清来历的记录，
不如零条加一个说得清的开始。

---

# 变异测试：13 个，第一轮 2 个漏网

```
reviewed_at 又变回不填            test✗ check✗
时间戳用机器本地时间               test○ check○  ← 漏
延迟按日历天算                    test✗ check✗
复核早于信号也照算                 test✗ check✗
同判定照写第二行                   test✗ check✗
改判不记 previous_verdict         test✗ check✗
excluded 不要理由也收下            test✗ check✗
excluded 进分母                   test✗ check✗
没样本报成 0%                     test✗ check✗
没时间戳的老记录并进 clean          test✗ check✗
CLEAN_MAX_LAG 改成 1              test✗ check✗
CLI 不再分桶印 KPI                test○ check○  ← 漏
--on 的日期又掉进理由里             test○ check✗
```

两条漏网，又是这一轮反复出现的同两个毛病：

**一、夹具没有判别力。** 跑测试的机器时区正好是美东，
所以"用机器时间"和"用市场时间"给出同一个偏移量。
改成**把机器时区掰到 `Asia/Shanghai` 再断**才红。

**二、子串撞上了定义本身。** 我断的是 `"_print_kpi" in snap`，
而那个函数的 `def` 就在同一个文件里——把调用点删掉照样绿。
改成走 AST：要求 `_review()` 里真的有一个对 `_print_kpi` 的调用。

补完 13 个全被抓到。
