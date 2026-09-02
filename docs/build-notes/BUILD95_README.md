# build95 —— 验收没通过:委内瑞拉石油新闻成了 ARM 的实质材料

```
cd ~/.openclaw/workspace/cio-agent && unzip -o ~/Downloads/cio-build95.zip -d . && .venv/bin/python scripts/check_build.py
```

`全部 111 项通过` 之后再扫,**数字还会掉,那是对的**。

---

`--verbose` 把两个真问题露出来了。只看计数永远看不到,这就是为什么要读实际材料。

## 问题一:短 ticker 的子串误匹配

ARM 那 6 条"实质材料"里有两条**根本不是关于 ARM 的**:

```
[3] 实质  Venezuelan opposition up in arms over reports US wants big stake in oil and gas
[4] 实质  Reality check for China's carmakers as authorities tell them to focus on quality
```

相关性闸用的是子串匹配 `"arm" in title.lower()`:

```
up in arms            ← arms
c a r m a k e r s     ← c·arm·akers
Pharma                ← Ph·arm·a
Alarm                 ← Al·arm
```

我把这四条跑了一遍,**全部命中**。委内瑞拉的石油新闻被当成 ARM 的实质材料——
真跑一部,多空辩论会拿它当论据写进报告。

三四个字母的 ticker 全有这毛病:**ARM / MU / KLA / AI / ON / IT**。
对它们来说子串匹配等于没有匹配。

改成**词边界**匹配:`ARM` 认 " ARM "、"(ARM)"、"ARM's"、"ARM.",
不认 "arms"、"charm"、"pharma"。中文别名仍走子串——中文没有空格,
加词边界反而会漏。

## 问题二:Form 4 / 144 不该触发闸门

AMD 判「材料充分」,三条实质材料是:

```
[1] ADVANCED MICRO DEVICES INC 4   (2026-08-27)  CFO 的股份交易
[2] ADVANCED MICRO DEVICES INC 4   (2026-08-26)  另一位高管的交易
[3] ADVANCED MICRO DEVICES INC 144 (2026-08-25)  拟出售登记
```

**全是内部人交易申报。** 而同一天真正的商业事件——

```
[7] 背景  AMD, Cisco and HUMAIN Expand Saudi Arabia's AI Infrastructure as AMD Instinct Systems Go Live
[8] 背景  Cathie Wood Just Swapped $95 Million of AMD for Nvidia and Broadcom
```

——判成了背景。**证据层级整个反了。**

Form 4 不是"无实质",它是依法必须披露的真实文件。但它说的是
**某个人卖了股票**,不是**这家公司发生了什么**。高管按预定计划减持每季度
都有;把它当成"今天值得重新研究这家公司",闸门就退化成了一个日历。

所以按表单分级:

```
事件性披露  8-K / 10-Q / 10-K / 20-F / 6-K / S-1 / DEF 14A …  → 实质
持股与交易  3 / 4 / 5 / 144 / SC 13D / SC 13G / 13F-HR        → 背景,不触发闸门
```

**仍然显示、仍然可被引用**,只是不再让闸门开门。判定理由会写清楚:

```
[1] 背景·一手披露（4 持股/交易申报）—— 说的是某人买卖了股票,不是公司发生了什么,**不触发闸门**
```

表单号是从 `SEC filing {form} filed {date}.` 这句里取的——**那句格式由我们
自己控制**,不去猜公司法定名称后面那截是什么。

---

## 预期

AMD 的 3 条实质会归零(全是 Form 4/144)→ 大概率回到 INSUFFICIENT。
ARM 会掉 2–3 条(误匹配 + Form 144)。整体实质占比会从 29% 再降一截。

**这次降是对的。** 前面几轮降是因为修了统计口径,这一轮降是因为
**把不该算的东西剔出去了**。

---

## 还没修的一条,留给你判断

ARM 那条:

```
[6] 实质·已发生动作 + 具体比例
    Arm Rises 2.8% as $272 Target Prices the CPU Tollbooth
```

"涨了 2.8%" 是行情复述(面板里有更准的),"$272 目标价"是分析师观点。
按现在的规则它算实质,我觉得不该算。

没顺手改是因为**规则里正向证据优先于负向标记**,得先看清是哪个词把它
判成"已发生动作"才好动——改错了会误伤真材料。等你下一轮扫描
`--verbose` 的输出出来,能看到完整正文,再定。

---

## 自检

```
CIO_MARKET=us .venv/bin/python scripts/check_build.py     111 项
CIO_MARKET=us .venv/bin/python scripts/test_intake.py     30 项
```
