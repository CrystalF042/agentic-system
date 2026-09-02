# build102 —— 收了不印和没收是一回事

```
cd ~/.openclaw/workspace/cio-agent && unzip -o ~/Downloads/cio-build102.zip -d . && .venv/bin/python scripts/check_build.py
```

`全部 124 项通过` 之后再扫一轮，`--verbose`。

---

## 先认一个错

build101 我预测「ARM 的相关材料会多几条」。**一条没多，还是 10。**

那 15 条原来没被检查过的，去向是这样：

```
旧：池 40 → 相关 10 ＋ 消歧丢 16 ＋ 其他丢 14
新：全部 55 → 相关 10 ＋ 消歧丢 27 ＋ 其他丢 18
                  ↑ +0        ↑ +11      ↑ +4
```

**15 条里一条相关的都没有。** 池子那一刀虽然是盲砍，但它砍掉的确实全是垃圾——
按相关性排序在这里是有效的。修还是该修（"没看过就扔"这件事本身不能留），
但**它不是损失发生的地方**，我把因果搞错了。

连带着，那条 `$2 Billion in Orders` 也没回来。所以它当初不是被池子挤掉的。
而我**分不出来**它是被源轮走了、还是被某道清洗闸杀了——因为 ARM 那 18 条
"其他丢弃"完全没有说明。

---

## 一、四个丢弃原因全部打印，并且各留样本

build98 我把四个原因都收进了 `dropped_by`，**却只把「符号消歧」那一个印出来**，
另外三个收了就扔。这一轮立刻付出代价：18 条无说明的丢弃，
挡住了上一个问题的诊断。

**收了不印和没收是一回事。**

```
相关 10（符号消歧丢弃 27 条，占 73%　⚠；另：标题无标的 12、标题党 4、跨域噪音 2）
```

`--verbose` 里每一类都给样本：

```
── 标题党丢掉的 4 条（下列 2 条为样本）：
   ✗ Arm Holdings Has $2 Billion in Orders It Cannot Fill Yet — Is ARM Stock a Buy at $257?
```

**「标题党」那一闸是 `is_noise` 判的**，它有可能把一条真材料当标题党杀掉——
这一轮你就能直接看到它有没有干这件事。上面那行如果真的出现，
那条 20 亿订单的去向当场就有答案了。

---

## 二、按定义不可能开门的材料，不再先占名额

AMD 那 10 个闸门名额：

```
[3] 背景·一手披露（4 持股/交易申报）    ← 不触发闸门
[4] 背景·一手披露（4 持股/交易申报）    ← 不触发闸门
[5] 背景·一手披露（144 持股/交易申报）  ← 不触发闸门
…同一行还写着「截掉 20 条」
```

**30% 的窗口花在按设计不可能触发闸门的纸上，门外还有 20 条相关材料。**
它们相关性分很高（EDGAR 直取），所以稳稳排在前面。
ARM 更极端：Form 4、Form 144、外加一篇报道同一笔减持的新闻——
同一个 CFO 的同一次卖出占了三个名额。

区别在这里：**普通新闻还可能变成实质，持股申报不会。**

```
AMD in the spotlight this week              标题看是背景
  ＋正文「…以 49 亿美元完成收购 ZT Systems」 → 实质      ← build91 那一类

ADVANCED MICRO DEVICES INC 4 (2026-08-27)
  正文再全，说的也是某个人卖了股票            → 永远是背景
```

`tier_of` 一看表单号就短路返回背景，正文写什么都不影响。
所以排序上：**档位被规则钉死的，排在还可能改变的后面。**

排序验证（持股申报相关性分 95/94，新闻只有 10）：

```
1. 实质  AMD, Cisco and HUMAIN Expand Saudi AI Infrastructure…
2. 背景  Cathie Wood Just Swapped $95 Million of AMD…
3. 背景  AMD's Saudi AI Bet Is Scaling Toward 1 Gigawatt
…
6. 背景  ADVANCED MICRO DEVICES INC 4 (2026-08-27)   ← 沉底
7. 背景  ADVANCED MICRO DEVICES INC 4 (2026-08-26)   ← 沉底
8. 背景  ADVANCED MICRO DEVICES INC 144 (2026-08-25) ← 沉底
```

**不是丢弃**——照常显示、照常可引用、判定理由照常打印。改的只是谁先占名额。

**8-K 取不到正文的不算钉死**，这条我专门钉了测试：它补上正文还可能是实质，
一起沉底就会误伤真正的事件性披露。

---

## 这一轮预期

```
AMD   闸门里会多出 3 条普通背景材料（原来被三份内部人申报占着）
      档位不变，仍是 THIN（实质 2 → 1 个事件）
ARM   相关材料只有 10 条、没有截断，名额不紧张 → 列表基本不变
      但 --verbose 里会多出三段被丢掉的标题
KLAC  仍然 INSUFFICIENT
```

**判定档位这一轮基本不会动。** 改的是"什么东西有资格被看见"。

---

## 变异测试

```
排序里去掉 pinned            MISS      只印符号消歧        MISS
never_substantive 恒 False   MISS      无符号时不印其余     MISS
把 8-K 也钉死                MISS      intake 不留样本     MISS
                                      run_scan 不印样本   MISS
```

---

## 还剩

**产业词捞回** —— ARM 已经 73%，`IBM Introduces … With Arm Architecture` 还在被丢。
**跨轮对比** + 那个"新证据算本次快照还是过去 24 小时"的口径决定，等你拍板。

---

## 自检

```
CIO_MARKET=us .venv/bin/python scripts/check_build.py     124 项
CIO_MARKET=us .venv/bin/python scripts/test_intake.py      54 项
```
