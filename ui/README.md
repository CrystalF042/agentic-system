# CIO 投研 Agent · Shiny 演示界面

## 一次性准备（**给 UI 单开一个 venv，别动引擎那个**）

Shiny 1.6 起要 Python ≥ 3.10，而引擎跑在 3.9 上。两边分开，互不干扰：

```
cd ~/.openclaw/workspace/cio-agent
python3 -m venv .venv-ui
.venv-ui/bin/pip install "shiny==1.5.0"
```

机器上若有 3.10+，也可以直接 `pip install shiny` 用新版。

## 启动

```
CIO_HOME=$HOME/.openclaw/workspace/cio-agent \
CIO_PY=$HOME/.openclaw/workspace/cio-agent/.venv/bin/python \
.venv-ui/bin/shiny run ui/app.py --port 8000
```

浏览器打开 `http://127.0.0.1:8000`。

- `CIO_HOME` —— 引擎目录（默认取 `ui/` 的上一级）
- `CIO_PY` —— **跑引擎用的解释器**，指向引擎那个 3.9 venv

局域网里用手机看：加 `--host 0.0.0.0`，访问 `http://<Mac的IP>:8000`。

## 演示动线

```
① 扫描证据     今天哪几只真的有增量事实（零模型调用，10 只约 2–3 分钟）
② 研究选中标的  完整一部，六次本地模型调用，约 3–4 分钟；阶段逐个点亮
③ 重算组合仓位  CRO 约束 + PC 定仓，秒级；每个仓位标出被谁绑定
```

七个页签对应职责链：证据扫描 / 研究进度 / 一部观点 / 二部测量 / CRO 风险 /
PC 仓位 / 历史归因。

## 架构纪律（**改这个界面时必须守住**）

```
engine.py   只起进程、读契约。一行业务逻辑都没有。
app.py      只画。没有一句 if THIN: cap = "弱"。
```

消费 build83 冻结的三份契约：

| 契约 | 形式 |
| --- | --- |
| Result | stdout **整段** 一次 `json.loads`；顶层 `schema_version` / `run_id` / `kind` / `status` |
| Progress | stderr 的 `[STAGE] name \| detail` |
| Execution | subprocess 调 `run_*.py`，**不 import cio** |

**为什么不 import 引擎。** 界面一旦能 import，就有机会在页面里重新实现一遍判定
逻辑——"THIN 就是信心封顶为弱"这种规则会被抄第二遍，而闸门哪天改了，抄的那份
不会报错，只会开始说假话。这个坑在 `run_scan.py` 自己身上真的发生过一次。

所以档位、上限、封顶、绑定项**全部由引擎算好后原样交出**：
`activate` / `conviction_cap` / `banner` / `binding_position_constraint`。
页面只做 状态 → 显示 的映射。

## 三个刻意的界面设计

**一、「今天没有」是设计好的状态，不是空白页。** 扫描页顶部固定显示
`3 / 87　7 只系统选择不研究`。任何 Dashboard 都有空状态，而空状态在产品直觉里
等于 bug——不把沉默做成一个可以指着讲的数字，就迟早会忍不住往里填
"今日市场速览""AI 观察"，那等于把 Evidence Gate 从 UI 层重新拆掉。

**二、闸门拦下走的是另一条进度条，不是同一条走到一半。**
`采集 → 闸门 → 面板 → 主动弃权 → 结束`，六步全部打勾，然后明确写
「一部主动弃权，未产生新观点」。界面上"停在第 3 步不动"和"第 3 步之后主动结束"
看起来一样，前者是卡死，后者是系统正常工作。

**三、跳过的信息源必须显示。** 一个悄悄挂掉的 RSS 源，表现形式恰好就是
"今天这只票没有新材料"。扫描页顶部会列出本次跳过了谁、为什么。

## 一个界面层的判断

勾选「强制复研」时，候选池会**放宽到全部扫描标的**——否则闸门拦下的那几只根本
选不到，而 `--force` 存在的意义恰恰是研究被拦下的标的（首次建仓、季度复审、
论点到期）。放宽的同时页面写明：报告会标注依据的是既有证据集，
且 Evidence Gate 仍为 INSUFFICIENT，**PC 依然不会给仓位**。

## 已知边界

- 没有登录、没有多用户、没有定时任务——**这是给你自己演示用的**
- 长任务用后台线程 + 轮询，不是 `ExtendedTask`（1.5.0 上更省事，且版本兼容）
- 关掉浏览器不影响已经在跑的引擎进程，但页面状态不保留（存在内存里）
- 引擎进程的 stderr 会被完整留住；任一步失败时页面直接显示日志末尾与 stdout 原文
