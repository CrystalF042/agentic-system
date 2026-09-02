# build105 —— 你那个 "没有 API key" 是我的 bug

```
cd ~/.openclaw/workspace/cio-agent && unzip -o ~/Downloads/cio-build105.zip -d . && ls -l src/cio/judge.py && .venv/bin/python scripts/check_build.py
```

`全部 128 项通过`。中间那句 `ls` 是故意的——**它报 No such file 就说明解压没落到位，
后面不用看了**。

---

## 真因

`claude_chat()` 从 `CIO_ANTHROPIC_API_KEY` 取密钥。而把 `.env` 读进环境变量的，
是 `cio.config` 的**导入副作用**（`config._load_dotenv()`）。

`judge.py` 没有导入 `cio.config`。

而我在 build104 里把 `--smoke` 分支插在了 `from cio.config import MEMORY_DIR`
**之前**：

```python
if "--smoke" in argv:
    chat = J.claude_chat(...)          ← 这里读 env，但 .env 还没被加载
    ...
cache = None
if spec != "rules":
    from cio.config import MEMORY_DIR  ← .env 在这一行才被读，太晚了
```

所以你的 key 配得完全正确（验证脚本也确实读到了 108 字符），
但走 smoke 这条路时环境变量里什么都没有。

**"配置错了"和"代码没去读配置"，报的是同一句话。**

## 改法

修在 `judge.py` 里，不是只修 smoke 分支——因为任何调用 `claude_chat` 的人
都会踩同一个坑：

```python
from . import config as _config      # noqa: F401
"""这个 import 看起来没用，但删不得。……"""
```

那段 docstring 写了为什么，免得以后有人当无用 import 清理掉。

## 顺带补上 build104 缺的探针

你指出的：`check_build` 里只有 build103，没有 build104。对——build104 改的是
`eval_judge.py`，验收只写在 `test_judge.py` 里，`check_build` 完全不覆盖。
按这个项目自己的规矩不合格，补了：

```
build104  评测先报降级率，再报分数（AST 查 _score_tier 是否四元组返回）
build105  judge 自己加载 .env，不靠导入顺序
```

---

## 装完的顺序

```
1  .venv/bin/python scripts/check_build.py                    128 项
2  CIO_MARKET=us .venv/bin/python scripts/eval_judge.py       规则基线 67/67 · 3/8 · 13/20
3  CIO_MARKET=us .venv/bin/python scripts/eval_judge.py --judge claude:claude-haiku-4-5 --smoke
4  第 3 步 ✓ 之后再跑完整评测 --verbose
```
