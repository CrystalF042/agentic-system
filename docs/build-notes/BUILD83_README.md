# build83 —— 冻结三份 machine-facing contract

你提的五条全部实现了，其中第 5 条我在自己代码里就违反了（见下）。

---

## 1. `run_id`：每次运行的身份

`latest_advice()` 在单用户串行敲命令时永远正确——"最近一次"就是"我刚才那次"。
界面一进来这个等式就不成立：两个浏览器窗口、连点两次、定时任务撞上人工请求，
`latest` 会把**别人那一次**的结果交给这个页面，**而且长得完全正常**：
字段齐全、时间戳新鲜、没有任何一处报错。

新增 `src/cio/runid.py`：

```
ua-20260826-231945-a3f2      一部
pc-20260826-232011-7c1d      定仓
sc-20260826-231902-b4e0      扫描
```

`UnitAAdvice` 加了 `run_id` 字段，`pc_lineage` 加了 `run_id` 列。
`latest_advice()` 保留为便利入口（命令行抽查），**但不再是界面的身份**。

## 2. `schema_version` + 统一信封

所有 `--json` 输出顶层固定四个字段：

```json
{
  "schema_version": "1.0",
  "run_id": "pc-20260826-232011-7c1d",
  "kind": "pc",
  "status": "completed",
  ...
}
```

`status` 取值：`completed` / `no_candidates` / `gate_blocked` / `no_evidence` / `failed`。

**这个字段比想象中重要**：「今天没有候选」和「跑挂了」都表现为 `positions` 为空，
但含义完全相反。界面靠数组空不空去猜，就会把系统的正常状态显示成故障。

版本号的改动规则写在 `runid.py` 里：加字段不动，**改名或改含义必须升主版本**。
理由正是你说的——字段改名不会让 Shiny 报错，只会让它显示空值。

## 3. `stdout = JSON` 成为硬契约

自检里加了两个探针，做法按你说的：**整段 stdout 一次 `json.loads()`**，
不是"从输出里寻找 JSON"。

```
[build83]
  OK    run_scan --json 整段 stdout 可一次解析
  OK    run_pc --json 整段 stdout 可一次解析
```

探针在进程内跑真正的 `main()`、用 `redirect_stdout` 整段捕获，
所以任何一个偷偷混进 stdout 的 `print("starting...")` 都会当场让自检变红。

## 4. 先序列化 → 再落库 → 最后输出

你描述的那条路径是真的：ledger 记了 → JSON 序列化炸了 → 界面判定失败 →
用户重试 → 台账里两条同样的决策。

两道防线：

**顺序**。`payload` 在 `pc_ledger.record` **之前**构造完成。
序列化要炸就在写库之前炸，整次运行干净地失败，重试是安全的。
自检用 AST 断言这三者的行号顺序。

**幂等键 `(run_id, portfolio_id, ticker)`**。同一 run_id 重试 → 返回原记录的 id，
不写第二条；新的 run_id → 正常新增。

```
四次 record（pc-A, pc-A, pc-A, pc-B）→ 返回 id [1, 1, 1, 2]，台账 2 条
```

这里有个语义要点：**一次 PC 运行不是"一个投资决策"，是"在这批输入下算出来的结果"。**
regime 变了、测量更新了，就该有新的一条——那不是重复。要防的只有一种：
同一次执行因下游报错被重试，在台账里留下两条一模一样的决策。

老库里 run_id 为 NULL 的历史行不受影响（SQLite 唯一索引把 NULL 视为互不相同）。
没传 run_id 的调用方自动领一个一次性 id，**不会因为省略参数就悄悄开始去重**。

## 5. Engine owns truth —— 我自己违反了这条

`run_scan.py` 里原来写着：

```python
_MARK = {THIN: "◐ 可跑（信心将封顶为弱）", ...}
```

**这就是把 `material_gate` 的规则又抄了一遍。** 闸门哪天改了封顶档，
这一行不会报错，只会开始说假话。

现在 `scan_one()` 把闸门算好的派生状态原样交出去：

```json
{"level": "THIN", "activate": true, "conviction_cap": "弱", "banner": "⚠ 材料偏薄：..."}
```

`_MARK` 只剩 状态 → 显示 的映射，里面不出现任何信心档位词，自检守着。
**Shiny 应当照同样的规矩：拿 `conviction_cap` 直接显示，永远不要自己写
`if THIN: cap = "弱"`。**

顺带修了扫描的失败行：原来采集失败的行缺 `conviction_cap` 等键，
界面在那几行会取到 `None`，而 `None` 在页面上和"没有封顶"长得一样。
**契约里不许有形状不同的行。**

---

## 写 Shiny 时的三份契约

```
Result   contract  →  stdout 一次 json.loads，顶层有 schema_version / run_id / kind / status
Progress contract  →  stderr 的 [STAGE] xxx | detail
Execution contract →  subprocess 调 run_*.py，不 import 引擎
```

三样都有自检守着。字段改了、print 混进 stdout、record 被绕过、
界面重新解释规则——四种情况现在都会让 `check_build.py` 变红，
而不是等到页面上显示了错东西才发现。

## 自检

`scripts/check_build.py`：57 项（新增 5 项 build83）。
其中 `_b83_ui_owns_no_rules` 是我第五次栽在"grep 到自己的注释"上，
已经改成断言结构（`_MARK` 的值里不许出现档位词），不再匹配文本。
