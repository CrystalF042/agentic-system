# build82 —— 给界面用的三样东西（引擎侧改动，UI 还没开始写）

写 Shiny 之前先把这三样准备好，界面那边就只剩「起进程、读输出、画表」，
**没有一处需要 UI 懂业务逻辑**。

---

## 一、一部结果结构化落盘

`archive_and_render` 现在同时写 `.json`，和 `.md` / `.pdf` 同基名同目录。

```
Topic Archive/AMAT证券一部建议+2026-08-26-2210.md
Topic Archive/AMAT证券一部建议+2026-08-26-2210.pdf
Topic Archive/AMAT证券一部建议+2026-08-26-2210.json   ← 新增
```

界面直接读字段：`direction` / `conviction` / `gate_level` / `material_verdict`
/ `bull_case` / `bear_case` / `audit` / `synthesis` / `catalysts` /
`invalidations` / `materials` / `llm_calls` / `thesis_id` / `panel` …
外加 `_schema`、`_md_path`、`_pdf_path`。

**为什么必须有这个**：md 和 pdf 是给人读的。从 Markdown 反向解析结构，
正是这个项目栽过最多跟头的地方——`**失效条件**` 与 `【失效条件】`
那次，催化剂和失效条件同时解析成 0，报告在同一页上自相矛盾。
**产出端直接给结构化，下游就不必解析。**

两个刻意的决定：

- **返回值仍是 `(md_path, pdf_path)` 二元组，没有变三元组。**
  `beta_corr` 从二元组改三元组那次，调用方按两个解包，抛出的
  `too many values to unpack` 被外层 except 吞成一句"测量取不到"，
  Beta 静默变成 None、报告照常生成。json 路径用
  `unit_a.advice_json_path(md_path)` 推出。
- **`latest_advice(symbol)` 取不到时返回 `{}`，不返回空壳对象。**
  一个所有字段都是默认值的 UnitAAdvice，在界面上看起来就是
  "方向中性、信心中、没有材料"——和一次真实的中性结论长得一模一样。

```python
from cio import unit_a
d = unit_a.latest_advice("AMAT")     # 界面的主要入口
```

## 二、阶段事件（进度条的数据来源）

一部跑一次三到四分钟，其中六次模型调用期间**终端原本完全安静**——
界面上就只剩一个转圈，看起来和卡死一样。现在每一步发一条机器可解析的事件：

```
[STAGE] collect | 12 条材料
[STAGE] gate | THIN（材料偏薄，实质 1/8 条）
[STAGE] panel | 量化证据面板就绪
[STAGE] debate_bull_r1 | 多头独立建案
[STAGE] debate_bear_r1 | 空头独立建案
[STAGE] debate_bull_r2 | 多头反驳并直面不利证据
[STAGE] debate_bear_r2 | 空头反驳并直面不利证据
[STAGE] judge | 裁判做论证审计（不引入新事实）
[STAGE] synthesis | 综合出方向与信心（不给仓位）
[STAGE] done | 看多|弱
```

闸门拦下时走另一条：`collect → gate → panel → gate_blocked → done`。
**这两条必须能分辨**：界面上"停在第 2 步不动"和"第 2 步之后主动结束"
看起来一样，前者是卡死，后者是系统正常工作。

做成**命名事件而不是 n/N 进度**：闸门拦下只走 5 步，完整跑走 10 步，
硬套一个分母就要在两条路径上各维护一套计数。界面自己持有期望顺序，
收到哪个点亮哪个。

事件走 stderr（和其它日志一致），所以 stdout 干净，专门留给 JSON：

```python
p = subprocess.Popen([...], stderr=subprocess.PIPE, text=True)
for line in p.stderr:
    if "[STAGE]" in line: ...
```

**写这段时抓到一个真的 bug**：原来的实现是"第一次调用时若没有 handler 就配置"，
而只要别人先给这个 logger 挂了 handler（界面捕获、测试断言都会这么做），
那个分支就不成立，level 永远没被设过，继承 root 的 WARNING，
**所有 INFO 级阶段事件被无声丢弃**——界面上的表现就是进度条永远不动。
改成导入时配置，并加了自检。

## 三、`--json` 输出

```
CIO_MARKET=us python run_scan.py NVDA AVGO --json
CIO_MARKET=us python run_pc.py --json
```

`run_scan --json` 带 `rows`（含每条材料的判定与理由）、`n_materials`、
`n_substantive`，**以及 `dead_feeds`**——界面只显示"全部无实质"而不显示
"有两个源今天挂了"，用户就会把数据缺失读成市场没消息。

`run_pc --json` 带 `positions`（每只的 gate / 测量 / caps / 风险约束 /
σ_eff / w_raw / w_final / **binding_position_constraint**）、`total_weight`、
`cash_residual`、`regime`。

两个细节：

- **`--json` 不提前 return。** `pc_ledger.record` 照跑——否则界面触发的
  每一次定仓都不进 lineage，而 `--stats` 仍然一切正常地少算。
  关掉的是【打印】，不是【流程】。
- **没有候选时输出的是合法 JSON，不是空 stdout。** 界面拿到空串只能显示
  "出错了"，而"今天没有候选"是这套系统最常见的正常状态，必须能表达。

---

## 写 Shiny 时要知道的两件事

**Shiny 1.7 要 Python ≥ 3.10，你的 venv 是 3.9。** 最后一个支持 3.9 的是
`shiny==1.5.0`（`ExtendedTask` 在这版里已经有了）。

**建议给 UI 单开一个 venv，用新 Python，subprocess 调用引擎而不是 import。**
版本隔离只是顺带好处，主要理由是：UI 层一旦能 import 引擎，就有机会在页面里
重新实现一遍判定逻辑——两套规则一定会漂移。这三样东西就是为"只调用不导入"
准备的。

## 自检

- `scripts/check_build.py`：52 项（新增 3 项 build82）
- `scripts/test_unit_a.py`：新增 18 项
