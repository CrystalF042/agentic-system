# build79 —— CRO/PC 推送 Telegram，并清掉频道上的三套仓位口径

## 一、`run_pc.py --tg`

```
CIO_MARKET=us python run_pc.py --tg                    真发
CIO_TG_DRYRUN=1 CIO_MARKET=us python run_pc.py --tg    只打印不发（先看效果）
```

推送内容只放**决策**与**理由**，中间计算留在终端和 lineage 里：

```
PC 定仓 · US_PAPER（2026-08-26）
市场 regime：risk_on（3 个信号投票，净票 +2）

· NVDA　看多|中　Gate INSUFFICIENT
    无仓位：Evidence Gate = INSUFFICIENT：一部未产出观点，不进候选池
· AVGO　看多|强　Gate SUFFICIENT
    仓位 5.00%　σ_eff 15.00%　绑定 single_name

合计权重 5.00%　现金残差 95.00%（不归一化到 100%）
候选 2 只：定仓 1，无仓位 1
CRO 给约束，PC 给权重，两者都不判断论点对错。执行与否由 CEO 决定。
```

两个细节：摘要在出口处剥掉 `**`（Telegram 的 Markdown 解析会因未配对的 `*`
丢掉整条消息的排版）；DRYRUN 不再报告成"已推送"——`send_text` 在 DRYRUN 下
返回 True 表示"这条本来会发出去"，照着它印"已推送"是在说一件没发生的事。

---

## 二、演示前必须先修的：同一个频道上有三套互相矛盾的仓位口径

这是这次接 Telegram 时才发现的，**和代码质量无关，是演示会当场翻车的那种问题**。
往你的频道里推送仓位结论的地方有三处：

| 来源 | 推送内容 | 问题 |
| --- | --- | --- |
| `run_unit_a.py` | `目标仓位：中仓` | 一部给仓位，**违反架构冻结**（PC 是唯一给仓位的地方） |
| `run_cro.py` | `建议总仓位：中仓` | 退役模块 `cro.build_cro` |
| `run_pilot.py` | `总仓位：{rating.target_position}` | 同上 |
| `run_pc.py`（新） | `CRO 给约束，PC 给权重` | 新链路 |

**四条消息都不报错，看起来都像正式结论，只是互相矛盾。**
对方往上翻两条就会问"到底谁说了算"，而这恰恰是你整套架构最想讲清楚的那件事。

修法：

1. **一部不再对外发仓位。** `run_unit_a.py` 的推送改成 Evidence Gate ——
   那才是一部这一轮真正产出的东西：

   ```
   方向：看多 ｜ 信心：中
   Evidence Gate：INSUFFICIENT（无实质材料，8 条材料实质 0 条）｜ Formal vote: ABSTAIN
   （…研究观点，非投资指令。仓位由 PC 决定，一部不给仓位。）
   ```

2. **退役 CRO 默认不再推送。** 新增 `src/cio/legacy_guard.py`：
   `run_cro.py` 与 `run_pilot.py` 里老 CRO 那部分的推送需要显式打开

   ```
   CIO_ALLOW_LEGACY_CRO=1 python run_cro.py
   ```

   本地照跑不误（历史数据、对照、调试都还要它），只是不再自己发到群里。
   显式打开是一个人的决定、且在命令行上留痕；默认推送则谁都不知道它发过。
   `run_pilot.py` 的**财务部盈亏表照常推送**——它不是退役模块。

---

## 三、演示前的检查清单

```
python scripts/check_build.py                          46 项，全绿再往下
CIO_TG_DRYRUN=1 CIO_MARKET=us python run_pc.py --tg    看 Telegram 摘要长什么样
CIO_MARKET=us python run_unit_a.py "某个今天真有新闻的票"   让台账里至少有一条 SUFFICIENT
CIO_MARKET=us python run_pc.py --tg                    真发一条
```

第三步是**内容问题不是代码问题**：现在台账里只有 NVDA 一条 OPEN 论点，
且它是 `--force` 复研留下的「无实质材料」，所以 PC 正确地拒绝给仓位——
但演示时整页 `合计权重 0.00%` 会被读成"这系统不出结果"。
**至少要有一只走完全流程拿到仓位的票**，让"给仓位"与"拒绝给仓位"同屏出现，
对比才是这套系统最有说服力的地方。

---

## 自检

- `scripts/check_build.py`：46 项（新增 3 项 build79）
- `scripts/test_cro_pc.py`：120 项（新增 13 项）
- 一部 / sizing / 二部回归：全绿
