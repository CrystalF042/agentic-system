# build121 —— Build 3：队列 → 补材料 → 一部，自动调度

```
cd ~/.openclaw/workspace/cio-agent && unzip -o ~/Downloads/cio-build121-scheduler.zip -d . && .venv/bin/python scripts/check_build.py && .venv/bin/python scripts/test_research.py && .venv/bin/python scripts/research_run.py --dry-run
```

`148 项` + `20 项` + 一份预演。**第三条不花钱。**

---

# 这一版接上的是标着"未运行"的第四行

```
[技术快照] 完成
[研究路由] 完成
[研究队列] 完成
[证券一部] 未运行      ← 这一行
[风控与仓位] 未运行
[待你批准] 未运行
```

从今天起它自己会跑：队列排前 K 名 → 补材料 → 一部 → 落队列 → 进心跳。
**K 就是研究预算。**

---

# 第一条：那条规矩在一部门口才真的致命

你定的「Evidence Gate 不许拦 Technical Trigger」，build120 钉了两层
（路由不过滤、trigger 产生时 `evidence_gate` 留空）。跑到这一层才发现
**还有第三层**：

```python
build_unit_a(symbol, force=False)
    Evidence = INSUFFICIENT → 一部不启动，Formal vote: ABSTAIN，0 次模型调用
```

也就是说，路由老老实实把 TECHNICAL trigger 送到了 Unit A，
**一部自己会把它挡回去。** 技术入口照样静默死亡，只是死在更深一层——
队列里那条记录会好端端地走到 `RESEARCHED`，产出是一句 ABSTAIN，
看起来完全正常。

所以调度对技术触发**必须传 `force=True`**，并在产出上写明：

> 技术结构触发研究，目前没有发现新的基本面事实。

这句话是**防编故事**用的：越过 INSUFFICIENT 跑出来的东西，不许长得像
"发现了 AI 订单增加所以上涨"。

**同一条规矩现在在三层各钉一次**：路由不过滤 / 调度传 force / 产出如实说明。

---

# 第二条：预算是数出来的，不是配置出来的

`MAX_UNIT_A_PER_DAY = 5` 写在配置里没有用——**它必须被真的数、真的报。**

Approve 那道闸挡住的是**坏交易**。它挡不住的是：

> 连着三周，自动流水线安静地把研究预算花在垃圾上，
> 而这看起来和正常运行一模一样。

所以每天花了几次、花在谁身上，都落盘（`raw/research/spend/YYYY-MM-DD.json`）、
都进心跳。

两个细节：

```
计数从磁盘上的账读       中途重启不该把预算清零
先记账，再花钱           跑一半崩了，那一次也算花过了
```

第二条尤其要紧。反过来写——先跑、跑成了再记账——的话，
**一条每次都在同一个地方崩溃的记录，会每天吃掉整份研究预算**，
而它在队列里看起来和正常排队一模一样。宁可少跑一次。

---

# 第三条：预演和真跑必须是同一份 plan

```
.venv/bin/python scripts/research_run.py --dry-run
```

`plan()` 是**纯读**的——不改任何状态。`--dry-run` 和真跑调的是同一个
`plan()`，所以"预演说会跑谁"和"真跑跑了谁"不可能不一致。

预演如果走另一条代码路径，那预演本身就没有意义——它验证的是那条
永远不会真跑的路径。

---

# 第四条：开关关掉 ≠ 坏掉

```
CIO_RESEARCH_ENABLED=0 .venv/bin/python scripts/research_run.py
```

关掉是一个正常状态，**但它会出现在心跳里**：

```
[证券一部] 完成　picked 0　done 0　failed 0　deferred 3　budget_used 0　budget 5
    **自动研究被关掉了**（CIO_RESEARCH_ENABLED=0）—— 这不是坏了，是有人关的
```

关掉期间**队列不清空，而且还数得出来有几条在等**（`deferred 3`）。
只保证"条目没被删"是不够的：把等待名单清成空的，条目照样躺在文件里，
可是"关掉这半个月攒了 40 条"这件事**没人看得见**——
那又和"今天本来就没有"长得一模一样。

---

# 变异测试：13 个，第一轮 3 个漏网

```
技术触发不传 force                     ✗抓到
force 按 EVIDENCE 判（判反了）          ✗抓到
越过 INSUFFICIENT 不写明那句话           ○漏网 ←
run() 里按 Evidence 档位拦下来           ✗抓到
预算改成进程内计数（重启清零）             ✗抓到
先花钱后记账（崩了就不算花过）             ✗抓到
超预算的直接丢掉，不转 DEFERRED           ✗抓到
预演也真的跑                           ✗抓到
开关关掉但不说（静默停摆）                ✗抓到
开关关掉时把队列清空                     ○漏网 ←
心跳不记 0                            ○漏网 ←
补材料那一步顺手调模型                    ✗抓到
失败不落 FAILED                       ✗抓到
```

## 漏网一：我的假夹具自己提供了我要断言的那句话

最难看的一条。测试里把 `_research` 换成假的（不能真调模型），
而**那个假的自己 return 了 `NO_NEW_FACTS_NOTE`**：

```python
def _r(it, tr, dry_run):                       # 我的假实现
    return {..., "note": sc.NO_NEW_FACTS_NOTE if ... else ""}
...
assert sc.NO_NEW_FACTS_NOTE in "\n".join(sc.describe(res))   # 断的是我自己
```

把真实现里那行删掉，用例照样绿。**用实现自己的输出验证实现，
永远验证不出东西**——这次是它的变种：**用夹具的输出验证实现。**

改法分两步。第一步，直接调**真的** `_research`（`dry_run=True`，零模型调用），
只喂 trigger_types 和 tier，看它自己吐什么：

```
TECHNICAL + INSUFFICIENT  →  force=True   带那句话
EVIDENCE  + INSUFFICIENT  →  force=False  不带
TECHNICAL + SUFFICIENT    →             不带     ← 常亮的灯 = 不亮的灯
```

第二步才是真正的修复。写完第一步我发现**那句话在 `_research` 里判了两次**
——预演一处、真跑一处。上面那条断言走的是预演那处，
**删掉真跑那处它照样绿，而删掉的偏偏是真的会跑起来的那处。**

所以把判断收成一个出处 `_note_for(force, tier)`，并加一条结构断言：
`_research` 的函数体里**不许再出现 `NO_NEW_FACTS_NOTE` 这个名字**。

两处等价的代码 = 一个测得到、一个测不到。

## 漏网二：只断"条目没被删"

见上面第四条。补断 `deferred` 计数和 `plan().deferred` 的名单。

## 漏网三：变异本身写错了，根本没变异

那条变异写成了 `hb.count(...) if False else hb.count(原样...)`——
**永远走 else 分支，代码等价，当然抓不到。**

一条抓不到的变异，和一条写错的测试是同一种东西：
**它给的是"这里测过了"的假信号。** 重写成
`if hb is not None:` → `if hb is not None and res["picked"]:`。

那句话拆出唯一出处之后，又补了两条只有拆出来才写得出来的变异
（「那句话变成常亮」「那句话不看是不是技术触发」）。
15 条**两套用例各自都抓到**。

---

# 顺手修掉的：两条探针在你机器上是绿的，在别的机器上是红的

build118 / build119 的两条探针，装到这一版时红了。查下来**不是代码坏了，
是探针自己写死了环境**：

```
b118  断言时间戳"不等于 +08:00"          CIO_MARKET=cn 时市场本来就是 +08:00
b119  断言 cron_hint("America/New_York")  市场是 cn 时这句正好反过来
        说"不用手动改"
```

它们在交付时是绿的，只因为那台机器恰好在美东、而且恰好设了 `CIO_MARKET=us`。
**又是夹具没有判别力**，只是这回它把正确实现判成了错的。

改成从市场配置**算**出该对齐哪个时区、再挑一个**此刻偏移量确实不同**的
时区来做对照。现在 6 种（机器时区 × 市场）组合全绿。

这条值得记一笔：**一条只在一种机器上成立的断言，等于没有断言**——
它哪天变红，第一反应会是"装错了"，而不是"代码错了"。

---

# 装完跑这四条

```
1  .venv/bin/python scripts/check_build.py              148 项
2  .venv/bin/python scripts/test_research.py             20 项
3  .venv/bin/python scripts/research_run.py --dry-run    预演，不花钱
4  .venv/bin/python scripts/research_run.py --status     今天花了多少、队列什么状态
```

第 3 条会告诉你今天会跑谁、谁超预算被推迟。确认名单没问题再去掉 `--dry-run`。

---

# 现在这条链

```
[技术快照] 完成
[研究路由] 完成
[研究队列] 完成
[证券一部] 完成　picked 2　done 2　failed 0　budget_used 2/5     ← 新
[风控与仓位] 未运行
[待你批准] 未运行
```

**Build 4（Unit A → CRO → PC → Proposal 自动串起来，每一步都报备）接着做。**
它接上第五行。Build 5 把第六行接上 Telegram 推送，然后自动化就停在
`Approve / Reject` 那道闸前面——**那道闸由代码保证绕不过去。**
