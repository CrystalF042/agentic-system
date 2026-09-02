#!/usr/bin/env python3
"""判定器评测 —— **把"换模型有没有用"从一句判断变成一张表。**

    python scripts/eval_judge.py                        规则（基线，离线，零成本）
    python scripts/eval_judge.py --judge ollama:qwen3   本地模型
    python scripts/eval_judge.py --judge claude:claude-haiku-4-5   需要 API key
    python scripts/eval_judge.py --judge hybrid:claude:claude-haiku-4-5
                                                        模型判语言 + 规则一票否决
    python scripts/eval_judge.py --judge claude:<model> --smoke    只发一次最小请求，验 key
    python scripts/eval_judge.py --judge rules --verbose           逐条列出判错的

`hybrid:` 与被它包住的那个模型**共用缓存**：先评 `claude:<m>` 再评
`hybrid:claude:<m>`，第二次一个请求都不会发，两栏分数的差异因此
只可能来自否决逻辑本身。

## 两个分数，只有第二个算数

    调参集 CASES     每一条都来自某个 build 的修复现场 —— 规则见过它，
                     而且是**为它改的**。规则在这上面接近满分是设计出来的
                     结果，不是能力的证据。
    留出集 HELDOUT   2026-08-31 扩样测试里 ON / IT 那两只票的材料，
                     规则从来没见过。真机上规则在这里是 0/2。

**看留出集。** 调参集只用来确认没有退化。

## 还有一张更贵的表：相关性

相关性闸失手比闸门本身贵——被它丢掉的材料不会出现在任何输出里。
真机上 Gartner 十个名额里六个是委内瑞拉石油和橄榄球赛程（`It's` 被当成
ticker `IT` 的所有格），同时 `KLA Falls 3.9%` 这种真材料被判成"标题里没有这只票"。

## 不联网也能跑

不带 `--judge` 就是评规则，纯离线、零成本。这条路径任何时候都能跑，
也是 `check_build` 里那条探针用的路径。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _material_corpus as corpus                            # noqa: E402
from cio import judge as J                                   # noqa: E402
from cio import material_gate                                # noqa: E402

_SEC = "https://www.sec.gov/Archives/edgar/data/2488/x.htm"


class Score(NamedTuple):
    """一栏的成绩。**`degraded` 必须是独立字段，不能折进分数里。**

    key 错了 / 模型不通时 `judge.py` 会回落到规则（那是设计好的护栏），
    于是分数恰好等于规则基线——"模型没被调用过"和"模型和规则一样好"
    在三个数字上长得一模一样。

    `policy` 和 `vetoed` 同理，但含义完全不同，所以也各占一个字段：
    `policy` 是"这条按来源/表单直接定档，没问模型"（设计），
    `vetoed` 是"模型判了实质、被规则硬标记压回去"（分歧）。
    把它们并进 `degraded`，一份正常工作的公告会让评测报出"模型不通"。
    """

    ok: int
    n: int
    bad: list
    degraded: int
    policy: int = 0
    vetoed: int = 0


def _score_tier(jd, cases, is_filing=False) -> Score:
    bad, degraded, policy, vetoed = [], 0, 0, 0
    for row in cases:
        if is_filing:
            form, with_body, want, _note = row
            text = corpus.filing_text(form, with_body)
            got = jd.judge_one(text, "EDGAR", _SEC)
            head = f"{form} body={with_body}"
        else:
            tag, text, want, _note = row
            got = jd.judge_one(text)
            head = f"[{tag}] " + text.split("\n", 1)[0][:62]
        degraded += bool(got.degraded)
        policy += bool(getattr(got, "policy", False))
        vetoed += bool(getattr(got, "vetoed", False))
        if got.tier != want:
            bad.append((head, want, got.tier, got.why, got.degraded))
    return Score(len(cases) - len(bad), len(cases), bad, degraded, policy, vetoed)


def _score_relevance(jd):
    bad = []
    for sym, company, title, want, _note in corpus.RELEVANCE_CASES:
        got = jd.judge_relevance(title, sym, company)
        if bool(got) != bool(want):
            bad.append((f"{sym}: {title[:58]}", "相关" if want else "不相关",
                        "相关" if got else "不相关", "", False))
    n = len(corpus.RELEVANCE_CASES)
    return n - len(bad), n, bad


def _pct(a, b):
    return f"{a}/{b}" + (f"（{a / b:.0%}）" if b else "")


def _dump(title, bad, verbose):
    if not bad:
        return
    print(f"\n  ── {title}判错 {len(bad)} 条" + ("" if verbose else "（--verbose 看全部）"))
    for head, want, got, why, degraded in (bad if verbose else bad[:5]):
        mark = "（降级）" if degraded else ""
        print(f"     期望 {want} 实得 {got}{mark}　{head}")
        if why:
            print(f"        理由：{why[:64]}")


def main() -> int:
    argv = sys.argv[1:]
    verbose = "--verbose" in argv
    spec = "rules"
    argv = [a for a in argv]
    if "--judge" in argv:
        i = argv.index("--judge")
        if i + 1 >= len(argv):
            print("--judge 后面要跟 rules / ollama:<model> / claude:<model>")
            return 2
        spec = argv[i + 1]

    # --smoke：**发一次最小请求就退出。** 用你自己的配置（.env / 环境变量名 /
    # 模型 ID）走真实代码路径，比 Console 给的示例更贴近实际会用到的东西。
    if "--smoke" in argv:
        base = spec.split(":", 1)[1] if spec.startswith("hybrid:") else spec
        if base == "rules":
            print("--smoke 要配 --judge claude:<model> 或 ollama:<model>")
            return 2
        try:
            chat = (J.claude_chat(base.split(":", 1)[1]) if base.startswith("claude:")
                    else J.ollama_chat(base.split(":", 1)[1]))
            raw = chat('只回这个 JSON，不要别的：{"ok": true}')
        except Exception as e:                                   # noqa: BLE001
            print(f"✗ 调不通：{type(e).__name__}: {e}")
            return 2
        obj = J._first_json(raw)
        print(f"模型原样返回：{raw.strip()[:200]!r}")
        print("✓ key 和模型名都对，可以跑完整评测了" if obj
              else "⚠ 通了，但返回的不是 JSON —— 这个模型可能不适合做结构化判定")
        return 0 if obj else 1

    cache = None
    if spec != "rules":
        from cio.config import MEMORY_DIR
        # **`hybrid:` 不进文件名。** 混合判定问模型的问题和纯模型判定一模一样，
        # 共用缓存 → 评完 claude:<m> 再评 hybrid:claude:<m> 不再花一分钱，
        # 而且两栏的差异只可能来自否决逻辑。见 `judge.cache_stem`。
        cache = Path(MEMORY_DIR) / f"judge_cache_{J.cache_stem(spec)}.json"
    try:
        jd = J.build(spec, cache_path=cache)
    except Exception as e:                                       # noqa: BLE001
        print(f"判定器建不起来：{type(e).__name__}: {e}")
        return 2

    print("=" * 74)
    print(f"判定器评测　{jd.name}")
    print("=" * 74)

    t = _score_tier(jd, corpus.CASES)
    f = _score_tier(jd, corpus.FILING_CASES, is_filing=True)
    h = _score_tier(jd, corpus.HELDOUT)
    r_ok, r_n, r_bad = _score_relevance(jd)
    dg, dg_n = t.degraded + f.degraded + h.degraded, t.n + f.n + h.n
    pol = t.policy + f.policy + h.policy
    veto = t.vetoed + f.vetoed + h.vetoed

    # **先报降级率，再报分数。** 降级高的时候，下面三个数不是模型的分数。
    if spec != "rules":
        print(f"\n  降级 {_pct(dg, dg_n)}"
              + "（模型不通或引文对不上 → 回落到规则）")
        if dg >= dg_n * 0.5:
            print("\n  ⚠ **降级过半：下面的分数不是这个模型的分数，是规则的分数。**")
            print("     最常见原因：API key 无效/未生效、模型 ID 写错、网络不通。")
            print("     先用 --smoke 发一次最小请求确认 key 和模型名。")
        # 降级是故障，这两个是设计 —— 分开印，不合并计数。
        if pol:
            print(f"  政策直判 {_pct(pol, dg_n)}"
                  "（来源/表单由代码定档，**没有问模型**）")
        if veto:
            print(f"  规则否决 {_pct(veto, dg_n)}"
                  "（模型判实质、标题命中硬标记 → 压回规则的档）")
    print(f"\n  调参集（规则为它改过，只用来看有没有退化）  {_pct(t.ok + f.ok, t.n + f.n)}")
    print(f"  **留出集（规则从没见过，这个才算数）**        {_pct(h.ok, h.n)}")
    print(f"  **相关性（丢错了不会出现在任何输出里）**      {_pct(r_ok, r_n)}")

    _dump("调参集", t.bad + f.bad, verbose)
    _dump("留出集", h.bad, verbose)
    _dump("相关性", r_bad, verbose)

    jd.flush()

    print("\n" + "-" * 74)
    print("留出集与相关性两栏，是回答『换模型有没有用』的全部依据。")
    print("调参集接近满分不说明任何事 —— 那些判例是规则的训练数据。")
    if spec == "rules":
        print("\n对比另一个：")
        print("  python scripts/eval_judge.py --judge ollama:<你的模型名>")
        print("  CIO_ANTHROPIC_API_KEY=... python scripts/eval_judge.py "
              "--judge claude:claude-haiku-4-5")
    elif not spec.startswith("hybrid:"):
        print("\n再评一次混合判定（模型判语言 + 规则保留一票否决），"
              "**共用缓存，不再花钱**：")
        print(f"  python scripts/eval_judge.py --judge hybrid:{spec} --verbose")
    return 0 if (h.ok == h.n and r_ok == r_n) else 1


if __name__ == "__main__":
    raise SystemExit(main())
