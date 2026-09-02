# build108 —— 关掉 cn 桶 + 基础率跨天与组合

```
cd ~/.openclaw/workspace/cio-agent && unzip -o ~/Downloads/cio-build108-nocn-baserate.zip -d . && ls -l src/cio/technical/observer.py && .venv/bin/python scripts/check_build.py && .venv/bin/python scripts/test_intake.py
```

`133 项` + `57 项`。**这个包是累计的**，build107 的技术观察员和盘前发车时间都在里面。

---

## 关掉了什么

```
CIO_MARKET=us 下不再抓：
  RSS      SCMP China · SCMP Economy · Caixin Global · Sixth Tone · 东方财富-要闻 · 新华网财经
  关键词    China economy · China stocks · PBOC
  数据锚定  上证综指 · 沪深300
```

`CIO_MARKET=cn` 下一切照旧，一个源都没少。

## 三件你要知道的事

**一、关掉之后 `standing_queries` 会整段变空。** 原来那三条全是 cn 桶的，
剩下就是一个**静默失效的采集通道**——配置里有这一段，运行时什么都不抓，
不报错。所以我补了三条美股宏观关键词：

```yaml
- {q: "Federal Reserve OR FOMC", bucket: us, region: us}
- {q: "US inflation OR CPI",     bucket: us, region: us}
- {q: "Treasury yields",         bucket: us, region: us}
```

这三条是**我加的，不是你要的**。不想要就把 `config/sources.yaml` 里那三行删掉，
不用改代码。

**二、HK 和 Japan/Korea 我留着了。** 恒生、恒生科技、日经、KOSPI 是
**隔夜亚洲收盘**，美股开盘前的常规参照，和"中国财经报道"不是一回事——
你那份 PDF 里它们在「海外市场」那一栏，跟欧洲斯托克50 并排。
上证和沪深300 我删了，那两个是实打实的 A 股。想连港股一起去掉，
`config/watchlist_us.yaml` 里删两行就行，我在文件里标了位置。

**三、过滤掉了什么会印在采集状态里：**

```
源过滤: us 模式只收 us+world 桶；跳过 6 个 RSS（SCMP China、SCMP Economy、Caixin Global…）与 3 条关键词
```

**这行不是装饰。** 少了六个源之后，"今天中国没新闻"和"今天根本没抓中国"
在报告上会长得一模一样——这正是这个项目一整条主线在防的形状。

## 专题报告不受影响

`scan_rss_for_subject`（你点名要某个主题的深度报告时走的那条）**取全量桶**。
理由和上面相反但同源：你点名要一份中国主题的报告，系统却静默摘掉中文源，
是同一类缺陷。要不要连这条也关，你说。

## 改法在配置里，不在代码里

```python
MARKET_BUCKETS = {"us": ("world", "us"), "cn": ("world", "cn")}
```

以后加源只要标对 `bucket`，不用碰代码。

---

# 你跑出来的那张表，我读了

120 只、405 天、全部取到，板块 ETF 11/11。**内部是自洽的**：
`days_rvol_over_1_5_of_20` 中位数 2（20 天里 2 天放量 = 10%），
而"今日量比≥1.5"是 8% —— 两个数互相对得上，这是代码没算错的弱证据。

把你朋友说的三件事翻成这张表：

```
"成交量连续加大"   近20日≥5天放量        2%   ← 真稀有，可以做提醒
"资金持续流入"     CMF>0.1 且 OBV↑      22%   ← 每天 110 只，做提醒等于没有
"到了那个高度"     距上方价区≤0.5ATR     25%   ← 每天 125 只，同上
```

**后两条单独拿出来都不能做提醒。** 这正是我要先看基础率的原因——
"资金持续流入"听起来像个稀有事件，实际上五只票里就有一只。

还有两个数值得你知道：

- `atr_percentile_252` 中位数 **0.67** —— 一半以上的票正处在自己一年里
  波动偏高的位置。这直接影响价区：0.5×ATR20 的聚类容差今天比平静期更宽。
- `excess_mkt_63` 中位数 **+5%** —— 这 120 只的中位数跑赢了 SPY。
  可能是宽度好、也可能是这 120 只的取样偏了（不是全 500 只）。
  **我不下结论**，但值得你留意。

## 所以这一版加了两样东西

**一、`--days N`：基础率要跨天。** 一天的横截面不是基础率——今天可能整体放量、
可能刚好财报季。`observe` 是纯函数、面板已在内存里，多取几个 as_of 只花 CPU，
不再取一次数。默认采样 5 天（每隔 5 个交易日），报**均值/最低/最高**。

**二、组合命中率。** 单条都不稀有的时候，值钱的是交集：

```
.venv/bin/python scripts/technical_distribution.py --limit 120 --days 6
```

会多印两段：所有两两组合的命中率（按高到低排），以及你那三条放在一起
逐日的命中名单。**那份名单每天几只，决定了这条提醒做不做得成。**

---

## 顺带回你刚才那个报错

```
zsh: permission denied: scripts/technical_distribution.py
```

要用 venv 的解释器，不能直接执行文件：

```
.venv/bin/python scripts/technical_distribution.py --limit 120
```

直接 `./scripts/...` 就算加了执行权限也会走系统 python3，装的包不在那儿。
这个项目所有脚本都是 `.venv/bin/python` 开头。

120 只票要取 400 天日线，第一次跑几分钟起步；`--limit 40` 快很多，
先看形状够用了。

---

## 装完的顺序

```
1  .venv/bin/python scripts/check_build.py                      133 项
2  .venv/bin/python scripts/test_intake.py                      57 项
3  .venv/bin/python scripts/technical_distribution.py --limit 120 --days 6
4  .venv/bin/python run_premarket.py --when                      看发车时间对不对
```

第 3 步跑完，把**「CEO 那三条放在一起」**那几行发我。
如果每天是 0–3 只，这条提醒就能做；如果每天二三十只，
就得再加条件或者换条件——**先看这个数，再写任何提醒代码。**
