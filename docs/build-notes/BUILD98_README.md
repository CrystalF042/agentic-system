# build98 —— 把消歧砍掉的那 17 条露出来

```
cd ~/.openclaw/workspace/cio-agent && unzip -o ~/Downloads/cio-build98.zip -d . && .venv/bin/python scripts/check_build.py
```

`全部 118 项通过` 之后再扫一轮，**必须带 `--verbose`**——这一轮新加的东西
只有 `--verbose` 里才有。

---

## 为什么先修这条

build97 上线后，ARM 的进料行是这样的：

```
上一轮  采集 106 → 去重 54 → 相关 26 → 进闸门 10（截掉 16）
这一轮  采集 106 → 去重 54 → 相关  9 → 进闸门 9
```

我预期符号消歧挡掉 4 条噪音，**实际挡掉 17 条**。而这一行只写了"相关 9"——
读起来和"今天这只票没什么新闻"一模一样。

截断这一步至少还报着 `截掉 16 条`、`其中 N 条是实质材料`。
**相关性闸这一步一直是完全的盲区**：被它丢掉的东西不会出现在任何输出里，
不进日志、不进报告、不进 JSON。

而它现在是整条链路上**最凶的一刀**——它在闸门之前，砍的是候选池本身。
所以后面三条（产业词捞回、按事件计数、补评论句式）在这条修完之前都没法验收：
改完之后数字变了，我分不清是规则改对了，还是这一刀换了个地方砍。

---

## 三个层次都能看见

**一、进料行里的计数**（每次都印）

```
→ 相关 9（符号消歧丢弃 17 条，占 65%　⚠）（补正文 6 条） → 前 10 条进闸门
    ⚠ **这只票的候选主要是被符号消歧决定的**（裸符号撞上了英文词）
      —— `--verbose` 里有被丢掉的标题，扫一眼是不是丢了真材料。
```

比例过半才喊那第二句。丢 3 条只会安静地写一句"符号消歧丢弃 3 条，占 13%"。

**二、`--verbose` 里的标题**（这才是能判断的那部分）

```
── 符号消歧丢掉的 17 条（下列 8 条为样本）：
   ✗ Current ARM mortgage rates report for Aug. 31, 2026 - Fortune
   ✗ Multiple crews battle 2-alarm fire at small business in Glen Arm
   ✗ Mom Who Had Arm Amputated After 'Horrific' Shark Attack
   ✗ Guggenheim affiliate buys up debt linked to its asset management arm
   ✗ IBM Introduces New Mainframe Processor ... With Arm Architecture
   ...
```

**计数只能告诉你丢了 17 条，判断不了这一刀砍对没砍对——那必须看标题。**
这一轮你要做的就是扫这几行：前四条砍对了，第五条是我文档里写明的已知代价
（那篇讲的是 IBM）。如果里面出现了"Arm 拿下某客户""Arm 发布某架构"，
那就是砍错了，第二条修法（产业词捞回）要提前。

**三、汇总行**

```
符号消歧另丢弃 17 条（裸 ticker 撞上英文词，如 ARM / ON / IT）
　⚠ **ARM 丢掉的比留下的还多**
```

---

## 一个细节：什么才算"消歧丢的"

只有**裸符号确实作为一个词出现过**的才计进这个数——也就是旧规则会留下、
build97 之后被砍掉的那些。一条压根没提 ARM 的加密货币新闻仍然算普通的
"顺带提一句"噪音，不进这个计数。

不这么分的话，这个数就永远是个大数字，**也就永远不能用来判断消歧砍得对不对**。
`dropped_by` 里四个原因分开记：`符号消歧` / `标题无标的` / `跨域噪音` / `标题党`。

---

## 变异测试

七种改坏方式，探针全部抓到：

```
不把 drops 传给 _prefilter    MISS      普通无关也算进消歧      MISS
intake 键改名                MISS      run_scan 不印被丢标题   MISS
titles 键改名                MISS      警告线抬到 200%        MISS
进料行不报消歧                MISS
```

**其中第二条第一次没抓到。** 探针原来写的是 `"dropped_symbol" in 源码`，
而它是 `"dropped_symbol_titles"` 的**子串**——键改名照样"通过"。
改成用 AST 取 `intake` 字典的真实键。
「断言结构，不要断言文本」这条在这个仓库已经踩到第七次了，我把这次也写进注释了。

---

## 这一轮之后

看完 `--verbose` 里那几行被丢掉的标题，再定第二条怎么做：

- 如果丢掉的全是房贷/火灾/手臂/部门 → 消歧砍得对，直接做第三条（按事件计数）
- 如果里面有真的产业报道 → 先做产业词捞回，把 `Arm CPU / IP / 授权 / royalty`
  这类词与裸符号共现时也算相关

顺带一提，上一轮那两条误判（AMD 的 `Enters a Sovereign AI Showcase,
Not a Revenue Windfall`、KLAC 的 `What ... Means For Shareholders`）
**这一轮还在**，我没动——它们属于第四条，等前面的口径稳了再一起改，
免得又出现"数字变了但分不清是哪一刀造成的"。

---

## 自检

```
CIO_MARKET=us .venv/bin/python scripts/check_build.py     118 项
CIO_MARKET=us .venv/bin/python scripts/test_intake.py      43 项
```
