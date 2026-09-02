# build104 —— 评测先报降级率 + `--smoke` 验 key

**这是累积包**（build103 的全部内容 + 本次修正）。上一版我只打了两个文件，
而它们依赖 build103 的 `src/cio/judge.py`——你没装 build103，所以 import 失败。
是我破了这个项目"只发累积包"的规矩。

```
cd ~/.openclaw/workspace/cio-agent && unzip -o ~/Downloads/cio-build104.zip -d . && .venv/bin/python scripts/check_build.py
```

`全部 126 项通过` 之后再往下。

---

## 本次修的：一个我写进测量工具里的静默失败

`judge.py` 调用失败时会**回落到规则**（那是设计好的护栏）。
但如果 API key 是错的，95 条会全部 401 → 全部降级 → 三个分数
**恰好等于规则基线**：

```
留出集  3/8      和规则一模一样
相关性  13/20    和规则一模一样
```

你会据此得出"换模型没用"，**而模型一次都没被调用过**。

现在输出第一行先报降级率，过半直接打断：

```
降级 0/75（模型不通或引文对不上 → 回落到规则）

⚠ **降级过半：下面的分数不是这个模型的分数，是规则的分数。**
```

`scripts/test_judge.py` 里加了一条测试钉住它（用 AST 查 `_score_tier`
是否把降级条数单独返回，不是查源码里有没有某个字符串）。

## 新增 `--smoke`

```
CIO_MARKET=us .venv/bin/python scripts/eval_judge.py --judge claude:<model> --smoke
```

只发一次最小请求就退出。验的是**你自己的配置**——`.env` 加载、
环境变量名、模型 ID、返回是否合法 JSON——比 Console 给的示例更贴近实际路径。

```
✓ key 和模型名都对，可以跑完整评测了
✗ 调不通：HTTPStatusError: 401 …        ← key 的问题
⚠ 通了，但返回的不是 JSON               ← 模型选得不对
```

---

## build103 的内容（你还没装过，一并在这个包里）

`src/cio/judge.py` —— 判定器接口：`Verdict` 契约 + `RuleJudge` / `LLMJudge`
（Ollama 与 Claude 同一接口）。三条护栏写在代码里，不是写在提示词里：

```
引文核对    判「实质」必须能从原文逐字引出，引不出就降级
显式降级    模型不通就回落到规则，degraded=True 一路带出
不固化故障  降级结果不进缓存
```

`scripts/eval_judge.py` —— 评测任意 judge，**分调参集 / 留出集 / 相关性三栏**。
规则基线（2026-08-31 实测）：

```
调参集  67/67（100%）   ← 那是训练数据，不说明任何事
留出集  3/8 （38%）     ← 这个才算数
相关性  13/20（65%）
```

`scripts/test_judge.py` —— 12 项护栏自测（离线，不联网）。

政策层一行没改：`material_gate.assess()` 照旧确定性。有测试钉着
`material_gate` 源码里不许出现 `judge`，提示词里不许出现
`Form 4` / `SUFFICIENT` / `材料充分` / `闸门` / `仓位`。

---

## 自检

```
CIO_MARKET=us .venv/bin/python scripts/check_build.py     126 项
CIO_MARKET=us .venv/bin/python scripts/test_judge.py       12 项
CIO_MARKET=us .venv/bin/python scripts/eval_judge.py       规则基线
```
