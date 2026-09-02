# build78 —— 真机跑通之后暴露的两个静默失败

build77 的四个缺陷都修好了，真机验证：

```
- 测量（小数口径）：σ60 40.74%　σ252 36.77%　Beta 1.90　近一年最大回撤 -20.21%
```

σ 与回撤是小数口径、Beta 算出来了（三元组解包修好）、没有假否决、
`--stats` 的否决计数是真的。**这一段链路可以认为已经跑通。**

但这次输出又暴露了两个新缺陷，都属于同一类：**两件含义相反的事被折成同一个标签，
而报告读起来完全正常。**

---

## 一、「闸门没跑过」被折成「闸门判了没有实质材料」

`run_pc.py` 原来就地手写映射：

```python
gate = ("SUFFICIENT" if verdict == "材料充分"
        else "THIN" if verdict == "材料偏薄" else "INSUFFICIENT")
```

那个 `else` 把三种情况折成了一档：

| material_verdict | 真实含义 | 原来的判定 |
|---|---|---|
| `无实质材料` / `无材料` | 闸门跑过，判定确实没有实质材料 | INSUFFICIENT ✓ |
| `""`（空） | **闸门根本没跑过**（论点早于 build63） | INSUFFICIENT ✗ |
| 认不出的字符串 | 不知道 | INSUFFICIENT ✗ |

后两种会让报告印出「Evidence Gate = INSUFFICIENT：一部未产出观点」——
**一句假话**。一部产出过观点，只是当时没有材料判定这个字段。

危险的地方在于这个方向是"安全"的：不给仓位。所以它可以永远不被发现。
**一条永远不给仓位的链路，和一条工作正常的链路，输出长得一模一样。**

**修法**：换算点收到 `material_gate.level_from_verdict()`（唯一定义点），
新增第四档 `UNRECORDED`。两档都不给仓位，但**理由必须分开写**：

```
- **无仓位**：Evidence Gate = INSUFFICIENT：一部未产出观点，不进候选池
- **无仓位**：Evidence Gate 未记录：该论点没有材料判定字段——**不等于「一部未产出观点」**，重跑一次一部才能定档
```

`run_pc.py` 遇到 UNRECORDED 会打 WARNING 并点名论点号。

你这轮的 NVDA 是 `无实质材料` → INSUFFICIENT，**是真判定，闸门工作正常**——
那次 `--force` 复研零实质材料，PC 拒绝给仓位是对的。

---

## 二、影子账户在两个模块里被当成不同的东西

你的输出显示：

```
账户 二部: 3 笔　688012、002371、300308
账户 二部_shadow: 3 笔　688012、002371、300308
```

6 行不是台账重复，是 `run_pilot.py` 给每个部门额外记的一套**纸面镜像**
（`cfo._ALL = _MAIN + ["一部_shadow", "二部_shadow"]`）。

问题是：`analytics.py:327` 早就把影子账户排除了

```python
real = [p for p in pos if not str(p["account"]).endswith("_shadow")] or pos
```

而 `portfolio.open_positions()` 没有。**同一批行在两个模块里被当成不同的东西**，
两边算出的集中度会各自"正常"地给出不同的数字，谁都不报错。

今天还看不出来，因为影子仓在 A 股组合里、没进美股风险计算。
**等 `sector_used` / `theme_used` 开始从持仓算，每一项暴露都会正好翻倍**——
而翻倍之后的数字仍然是个正常数字。

**修法**：`open_positions(pid, include_shadow=False)` 默认排除，
**且排除要出声**（静默少算一半和静默多算一倍一样危险）：

```
[WARNING] LEGACY_A_SHARE_PAPER：排除 3 笔影子账户持仓（二部_shadow）
          ——影子账户是纸面镜像，计入聚合会让集中度与行业占用成倍虚增
```

`summary()` 增加"实盘口径笔数"，报告里逐账户标出影子账户。

---

## 自检

- `scripts/check_build.py`：43 项（新增 2 项 build78）
- `scripts/test_cro_pc.py`：107 项（新增 17 项）
- 一部 / sizing / 二部回归：全绿
