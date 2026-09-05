# build116 —— 我违反了自己定的血统纪律

```
cd ~/.openclaw/workspace/cio-agent && unzip -o ~/Downloads/cio-build116-schema.zip -d . && .venv/bin/python scripts/check_build.py && .venv/bin/python scripts/test_technical.py && .venv/bin/python scripts/technical_snapshot.py --force
```

`143 项` + `56 项`。

---

# 一、你的 2026-09-04.jsonl 被三个版本的代码各写过一遍，三次盖的是同一个章

卡片模块开头这句是我自己写的：

> **加字段可以不动，改语义必须升版本。**

build114 改了 `align()`：非有限的配对整对丢掉。于是

```
rs_mkt_samples     405  →  404      同一份输入，不同的数
                   含义也变了：从「对齐了几天」→「几天能用」
```

**那是语义变了。我没升 `SCHEMA_VERSION`。**

后果具体到你的机器上：`2026-09-04.jsonl` 你 `--force` 重写了三次
（build113 一次、114 一次、115 一次），三次的内容都不一样，
而三次盖的都是 `signal-card-1.0.0`。**`version_drift()` 会告诉你"全是同一版"。**

这正是 build109/110 那条纪律要防的事——**内容不同，图章相同**——
而我是在自己新加的代码上违反的。

## 修了两件

```
SCHEMA_VERSION      1.0.0 → 1.1.0，并写清楚是哪个字段的语义变了
字段名指纹          card_fields_fingerprint()，冻结在 FROZEN_FIELDS_FINGERPRINT
```

**语义变了没法自动检测**——`rs_mkt_samples` 一个字母都没改。
但**字段集变了可以**。加或删任何一个字段，指纹就红；
红了不是让你去改那个常量，是让你回答一句：**这次要不要升 `SCHEMA_VERSION`？**

指纹刻意**不含 `reasons`**：那个字典的键随每张卡片变（哪个字段算不出来记哪个），
放进去会让同一版代码在不同的票上给出不同的指纹——**每天都红的探针等于没有探针**。
有一条用例专门钉这一点。

## 你要做的

装完再跑一次 `--force`。今天这份 09-04 会重新盖上 `signal-card-1.1.0` 的章。
**从明天起 "写过就不再写" 的规矩会自己保护你**——今天要 `--force` 三次，
只是因为我们在同一天里反复改代码。

---

# 二、`sweep.py` 的模块文档里写着我那个错的诊断

它开头那段立论原文是：

```
按 relative_strength 的代码，这只有一种解释：
**SPY 面板和个股对齐后只剩 20–63 天。** 板块基准是好的，大盘基准是短的。
```

**那是错的**——`rs_mkt_samples` 报 405，对齐没有任何问题。
真正的原因是尾行 NaN。

我把错的诊断写进了一个模块的立论段，而那段话会比这次对话活得久得多。
现在改成写真正的原因，**并且把"我第一次诊断错了、以及为什么被骗"留在那里**——
因为这个模块存在的理由，恰恰是"一个错的原因比没有原因更糟"。

有一条探针扫这个文件：那句错的诊断再出现即红。

---

# 三、扫描的收尾句印在了中间

你那份输出里：

```
  **扫描只数不修。今天这份数据能不能用，是人的决定。**   ← 收尾句
  基准最后一根收盘是 NaN（yfinance 未落定的尾行，常见）…    ← 还有内容
  基准面板：yfinance:SPY(adjusted)　1255 行…
```

收尾句在中间，读起来像扫描已经结束了。改成 `sweep.closing_line()`，
由快照在最后印。探针检查两者在源码里的先后顺序。

---

# 四、你那份扫描里两条值得看的数

## `大盘截止 2026-08-31　板块截止 2026-09-04　1 张卡片`

其他 501 张都是 `09-03 / 09-04`（SPY 尾行 NaN，差一天）。
**这一张差了整整 4 天**，而 SPY 对所有票是同一份——所以差异来自这只票自己：
它在 09-01～09-03 的收盘价不可用（NaN 或 ≤0），只有 09-04 是好的。

这是**单只票的数据质量问题**，扫描刚刚把它捞出来了。要看是哪只：

```
.venv/bin/python -c "import sys;sys.path.insert(0,'src');from cio.technical import store;rows=store.load_day('2026-09-04');[print(r['symbol'], (r.get('relative_strength') or {}).get('rs_mkt_as_of'), (r.get('relative_strength') or {}).get('rs_sector_as_of'), (r.get('panel_health') or {})) for r in rows if (r.get('relative_strength') or {}).get('rs_mkt_as_of') not in ('2026-09-03', None)]"
```

## `20.5% price_structure.atr_to_nearest_zone_above（103/502）`

**这不是数据缺口，是一个结构事实**：这 103 只票**上方没有价区**——
多半在创新高、头顶没有成交密集区。

而 C 条件是"距上方价区 ≤ 0.5 ATR"，`None` 判 False。所以：

> **正在创新高的票，按构造永远通不过这个 setup。**

对这个 setup 而言这是**对的**（它整个就是在描述逼近上方套牢盘），
但它是**结构性排除，不是偶然缺数据**，而且排除掉的是全市场五分之一。
`evaluate()` 已经把它记进 `unknown`，我只是把这件事讲明白，
免得以后有人看到 20.5% 当成数据问题去"修"。

**这一条没有改任何代码。**

---

# 五、变异测试

```
schema_version 退回 1.0.0          test✗ check✗
字段指纹把 reasons 也算进去          test✗ check✗
字段指纹只看顶层不看块内             test✗ check✗
模块文档留着那个错的诊断             test○ check✗
收尾句印在中间                      test○ check✗
```

加上 113/114/115 的 37 个，一共 42 个，全被抓到。

---

# 装完跑这三条

```
1  .venv/bin/python scripts/check_build.py              143 项
2  .venv/bin/python scripts/test_technical.py            56 项
3  .venv/bin/python scripts/technical_snapshot.py --force
```
