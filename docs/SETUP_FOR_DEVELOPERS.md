# Setup Guide for Developers

This guide assumes you have **never seen this project before** and are not
especially comfortable with the command line. Every command is meant to be
copy-pasted exactly as written. Read the short explanations — a few of them
prevent mistakes that cannot be undone.

**Repository:** https://github.com/CrystalF042/agentic-system

---

## 0. What this system is, in one minute

It is a multi-agent equity research system that runs on one machine. Every day it:

```
1. Scans the whole US market after the close and writes one "Signal Card" per name
2. Decides which names deserve research time, and queues them
3. Runs an adversarial debate on the top 5:  Bull / Bear / Judge / Synthesis
4. Runs a risk review (CRO), then position sizing (PC)
5. Writes a trade proposal and notifies the owner on Telegram
6. STOPS. A human approves or rejects. Nothing executes without that.
```

Two things you need to internalise before writing any code here:

**Every number is computed by deterministic code.** Language models are used
only where language is the actual problem (reading news, arguing a thesis).
Never let a model produce a number that the system then treats as a measurement.

**The system is designed to say "I don't know."** Most days it produces nothing.
That is the intended behaviour, not a bug. If you "fix" something so it always
produces an answer, you have broken it.

---

## 1. What you need installed

| | Why | How to check |
| --- | --- | --- |
| **Python 3.9** | The owner's machine runs 3.9. Code that only works on 3.10+ will break there. | `python3 --version` |
| **git** | To get the code and send changes back | `git --version` |
| A terminal | macOS: Terminal.app. Windows: use WSL or Git Bash. | |
| **Ollama** (optional) | Only if you will run models locally — see section 6 first | `ollama --version` |

### Python 3.9 is a hard requirement, not a preference

The production machine runs **Python 3.9.23**. If you write

```python
def f(x: int | None) -> str:        # 3.10+ syntax
```

it will pass on your machine and **crash on hers**. There is an automated check
for this (`check_build.py` probe `build113`), but the check only runs if you
run it. Use `from typing import Optional` instead:

```python
from typing import Optional
def f(x: Optional[int]) -> str:     # works on 3.9
```

If your default `python3` is newer than 3.9, install 3.9 (macOS: `brew install python@3.9`,
then use `python3.9` in step 2 below).

---

## 2. Get the code and set up the environment

Pick a folder you like, then:

```
git clone https://github.com/CrystalF042/agentic-system.git
cd agentic-system
```

Create an isolated Python environment. **Everything from here on uses
`.venv/bin/python`, never a bare `python`** — that is how you guarantee you are
running with the right dependencies.

```
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

That last step takes 2–5 minutes. Some yellow `DEPRECATION` warnings are normal.
Confirm the version:

```
.venv/bin/python --version
```

You want `Python 3.9.x`. If you get 3.11 or newer, delete `.venv` and redo it
with `python3.9 -m venv .venv`.

---

## 3. Configuration: the `.env` file

```
cp .env.example .env
```

Now open `.env` in any text editor. For **development**, set these four:

```
CIO_MARKET=us
CIO_MOCK_LLM=1
CIO_TG_DRYRUN=1
CIO_DB=/tmp/dev.db
```

What they do, and why they matter:

| Setting | Effect | Why you want it while developing |
| --- | --- | --- |
| `CIO_MOCK_LLM=1` | No model is ever called; a stub string is returned | Tests run in seconds and cost nothing |
| `CIO_TG_DRYRUN=1` | Telegram messages are printed, not sent | You will not spam the owner's phone |
| `CIO_DB=/tmp/dev.db` | Uses a throwaway database | You cannot corrupt a real portfolio ledger |

> ### The one rule that cannot be undone
>
> **`.env` must never be committed to git.** It will hold a real API key and a
> real Telegram token. Git keeps history: once a file is committed, deleting it
> later does **not** remove it from the repository — anyone who clones gets it.
>
> `.gitignore` already excludes `.env`, and there is a gate (section 9) that
> verifies this before every push. Run the gate. Do not rely on remembering.

---

## 4. Verify the install

Two commands. Both should be green before you write a single line of code.

```
.venv/bin/python scripts/check_build.py
```

Expected last line:

```
全部 151 项通过 —— 代码是最新的，可以跑了。
```

("All 151 checks passed.") These are **installation probes** — they assert that
specific invariants hold in the source, not just that it imports.

```
.venv/bin/python scripts/test_llm.py && .venv/bin/python scripts/test_pipeline.py && .venv/bin/python scripts/test_notify.py
```

Expected: `全部 15 项通过` / `全部 16 项通过` / `全部 15 项通过`.

To run every test suite at once:

```
for f in scripts/test_*.py; do echo "--- $f"; .venv/bin/python "$f" | tail -1; done
```

**If anything is red, stop and report it.** Do not start changing code on top of
a red baseline — you will not be able to tell your bugs from the pre-existing ones.

---

## 5. Run it and see what happens

### 5.1 The safe first run

```
.venv/bin/python scripts/research_run.py --dry-run
```

This is a **rehearsal**: it changes no state, calls no model, sends no message.
On a fresh install you will see something like this (the Chinese is the owner's
working language; the structure is what matters):

```
研究调度　2026-09-05　预算 0/5　（预演，没有真的跑）
  选中 0　完成 0　失败 0　推迟 0　复检 0

风控与仓位　2026-09-05　US_PAPER　（预演，没有真的跑）
  待处理 0　目标 0　无仓位 0　**否决 0**　测量不可用 0　失败 0
  对账：队列待批 0　提案库待批 0

待批提醒　2026-09-05　US_PAPER
  待批 0　挂太久 0　快过期 0　本次未推送
  没有待批的提案

研究队列：空 —— 两条入口都还没有触发过。

CIO 流水线心跳　2026-09-05
[技术快照] 未运行
[研究路由] 未运行
[研究队列] 完成　open_items 0　queued 0　deferred 0
[证券一部] 完成　picked 0　done 0　failed 0　budget_used 0　budget 5
[风控与仓位] 完成　picked 0　targets 0　vetoed 0　proposals 0　pending 0
[待你批准] 完成　queue_pending 0 ... notified 0
    没有等你批的。**自动化到这一步为止，它自己过不去。**
```

**Everything is 0 and that is correct.** The queue is empty because nothing has
scanned the market yet. Read that last block carefully — it is called the
**heartbeat**, and it is the single most important design idea in this codebase:

> Every stage is **declared in advance**. A stage that did not run prints
> "未运行" (did not run). Zero prints as `0`, never as blank.
>
> The reason: "nothing happened today" and "the pipeline died silently" must
> never look the same. This system once went three days without sending its
> morning brief, and nothing anywhere said so — the log just said "skipped."

### 5.2 What a real day looks like

Two commands, in this order:

```
CIO_MARKET=us .venv/bin/python scripts/technical_snapshot.py
CIO_MARKET=us .venv/bin/python scripts/research_run.py
```

The first scans ~500 US names, writes one Signal Card each, applies the gate,
and pushes anything that triggers into the research queue. The second takes the
top 5 out of the queue, runs the debate, risk review, sizing, writes proposals,
and notifies.

**On a dev machine you normally do not run these** — the first needs live market
data and takes several minutes. Use `--dry-run` and the test suites instead.

### 5.3 Useful read-only commands

```
.venv/bin/python scripts/research_run.py --status      engine, budget, spend, queue
.venv/bin/python scripts/notify_run.py --text          exactly what would be sent
.venv/bin/python scripts/heartbeat.py --last 7         did it run each of the last 7 days
.venv/bin/python scripts/technical_snapshot.py --status  how many days of cards exist
```

---

## 6. Local models (Ollama)

### 6.1 Do you actually need this?

Read the table before installing 14 GB of anything.

| What you are doing | Ollama needed? |
| --- | --- |
| Reading code, writing code, running the test suites | **No.** `CIO_MOCK_LLM=1` handles it |
| `research_run.py --dry-run` | **No.** A rehearsal never calls a model |
| Running the real pipeline with `CIO_DEBATE_ENGINE=claude:...` | **Partly** — see 6.2 |
| Running the real pipeline with no `CIO_DEBATE_ENGINE` set | **Yes** — the debate runs locally |
| Running the morning brief (`run_premarket.py`) | **Yes** |
| Reproducing the owner's machine exactly | **Yes** |

Most development work needs none of this. Install it when you are working on
something that genuinely exercises a model, not before.

### 6.2 "Partly" — the part people miss

Switching the debate to Claude does **not** remove every local model call.
`unit_a.build_unit_a()` calls `process.hydrate()`, which uses the **light** model
to write one-sentence summaries of the news it collected. That call is separate
from the debate engine and still goes to Ollama.

And here is the part worth knowing: **if Ollama is not running, that call fails
quietly.** It falls back to returning the original headline. Nothing crashes,
nothing turns red, and the report just contains raw headlines instead of
summaries. If you ever see summaries that are word-for-word the headline, that is
what happened — check whether Ollama is up before looking for a bug in the code.

(The debate path is different: it is `strict`, so it raises. Section 7 explains
why those two paths deliberately behave differently.)

### 6.3 What the three models are for

| Env var | Default model | Used for | Download |
| --- | --- | --- | --- |
| `CIO_MODEL_BRIEF` | `gpt-oss:20b` | Local debate; the morning brief's lead items | **~14 GB** |
| `CIO_MODEL_LIGHT` | `phi4-mini` | Translation, one-line summaries, classification | ~2.5 GB |
| `CIO_MODEL_EMBED` | `nomic-embed-text-v2-moe` | Embeddings for topic search and dedup | ~1 GB |

**Hardware reality check for `gpt-oss:20b`:** ~14 GB download, and it needs
roughly that much memory to run. On an Apple Silicon Mac, 16 GB unified memory is
the tight minimum, 24 GB is comfortable. If your machine has 8 GB, do not pull it
— use `CIO_MOCK_LLM=1` for development and Claude for the debate.

### 6.4 Install Ollama

**macOS** — either download the app from https://ollama.com/download, or:

```
brew install ollama
```

Then start it. The app version runs in the menu bar automatically. If you
installed via brew, start the server in its own terminal window and leave it open:

```
ollama serve
```

**Linux:**

```
curl -fsSL https://ollama.com/install.sh | sh
```

**Windows:** download the installer from https://ollama.com/download, or run the
whole project under WSL2 and follow the Linux instructions.

Confirm the server is up:

```
curl http://127.0.0.1:11434/api/tags
```

You want JSON back (probably `{"models":[]}` at this point). If you get
`Connection refused`, Ollama is not running.

### 6.5 Pull the models — small ones first

Start with the two small ones. They cover the summary and embedding paths, which
is what most development actually touches:

```
ollama pull phi4-mini
ollama pull nomic-embed-text-v2-moe
```

Only pull the big one if you are going to run the debate locally:

```
ollama pull gpt-oss:20b
```

That is a ~14 GB download. It will take a while.

> If any `ollama pull` fails with a 404, the model name has moved. Do not
> guess — search https://ollama.com/library for the current tag, pull that, and
> point the matching `CIO_MODEL_*` variable in `.env` at it. Every model name in
> this project is an env var precisely so this is a config change, not a code change.

Check what you have:

```
ollama list
```

### 6.6 Verify it works from inside the project

This calls the light model through the project's own client, in **strict** mode,
so a failure is loud instead of silent:

```
.venv/bin/python -c "
import sys; sys.path.insert(0, 'src')
from cio.ollama_client import get_ollama
print(get_ollama().chat('Reply with the two characters OK', model='phi4-mini', strict=True)[:80])
"
```

Expect a short reply containing `OK`. If you get an exception mentioning
`Connection refused`, Ollama is not running. If you get a 404, that model is not
pulled.

To confirm which engine the debate will use:

```
.venv/bin/python scripts/research_run.py --status
```

```
辩论引擎 ollama:gpt-oss:20b　—— 本地，材料不出本机
```

("Debate engine ollama:gpt-oss:20b — local, material does not leave this machine.")

### 6.7 Remember to turn mock mode off

`CIO_MOCK_LLM=1` short-circuits **every** model call, local and remote. With it
on, you can install Ollama, pull 14 GB, and never touch it — and everything will
appear to work. When you want to actually exercise a model, set:

```
CIO_MOCK_LLM=0
```

and turn it back to `1` when you go back to writing code.

---

## 7. Connecting the Claude API key

The owner will give you a key that looks like `sk-ant-api03-...`.

### Where it goes

**Only** in `.env`. Never in code, never in a commit, never in a chat message,
never in a screenshot.

```
CIO_DEBATE_ENGINE=claude:claude-sonnet-5
CIO_ANTHROPIC_API_KEY=sk-ant-api03-...
CIO_MAX_USD_PER_DAY=5
```

Then **remove or comment out `CIO_MOCK_LLM=1`**, because mock mode short-circuits
every model call — with it on, the key is never used and you will think it works
when it has not been tested at all.

### Verify it took effect

```
.venv/bin/python scripts/research_run.py --status
```

You should see:

```
辩论引擎 claude:claude-sonnet-5　—— **材料会发到本机之外**（...）
今日花费：估算 $0.0000 / 上限 $5.00　（in 0 / out 0）
```

That first line says, in Chinese, "the research material leaves this machine."
It is printed deliberately every time, so that fact is never invisible.

With the engine left unset you get instead:

```
辩论引擎 ollama:gpt-oss:20b　—— 本地，材料不出本机
```

### Cost

One name costs roughly **$0.10** on Sonnet 5 (~26k input + ~5k output tokens
across six calls). Five names a day is about **$0.50/day**. The daily cap
(`CIO_MAX_USD_PER_DAY`) is enforced from a **spend ledger on disk**, so restarting
the process does not reset it.

**Do not run the full pipeline repeatedly just to see if your change works.**
Use `CIO_MOCK_LLM=1` and the test suites. Spend real money only when you are
testing something that genuinely requires a real model.

### If a call fails

It **raises**. It does not fall back to the local model, and it does not return
a truncated copy of the prompt. That item is marked `FAILED` and retried
tomorrow, and the heartbeat says why.

This is deliberate and it is worth understanding, because the old behaviour was
the opposite and it was dangerous: on failure the code returned the first 240
characters of the prompt, which then became the "Bull case" — no exception, no
error in the log, and a report that read like real analysis all the way to the
owner. If you ever feel tempted to add a `try/except` that returns *something*
so the pipeline "keeps going," this is the failure mode you would be recreating.

---

## 8. Connecting Telegram

Telegram is how "N proposals awaiting your approval" reaches the owner's phone.
**You almost certainly do not need to set this up** — keep `CIO_TG_DRYRUN=1` and
messages are printed to your terminal instead of sent. Set it up only if you are
specifically working on the notification code.

### If you do need it, use your OWN bot, not the owner's

1. Open Telegram, search for **@BotFather**, start a chat.
2. Send `/newbot`. Give it any name and a username ending in `bot`.
3. BotFather replies with a token like `8123456789:AAH...`. Copy it.
4. Get your own numeric chat id: search for **@userinfobot**, press Start.
   It replies with your `Id`.
5. Put both in `.env`:

```
TELEGRAM_BOT_TOKEN=8123456789:AAH...
TELEGRAM_CHAT_ID=123456789
CIO_TG_DRYRUN=0
```

6. Send your own bot any message once (Telegram will not let a bot message you
   until you have messaged it first).
7. Test:

```
.venv/bin/python scripts/notify_run.py --text
```

`--text` prints the message without sending. To actually send:

```
.venv/bin/python scripts/notify_run.py --force
```

### One trap worth knowing

Telegram delivers each update to **exactly one** `getUpdates` consumer. If two
programs poll with the same token, commands are randomly lost — with **no error
anywhere**. That is why there is a separate `CIO_CTRL_BOT_TOKEN` for the
interactive console (`run_tgbot.py`). If you need both, create a second bot.

### Never put the owner's token in your `.env`

If you are ever given it by accident, tell her so she can revoke it
(@BotFather → `/mybots` → the bot → API Token → Revoke current token).

---

## 9. Sending your changes back — the workflow

### 9.1 Always start from the latest code

```
cd agentic-system
git pull origin main
```

### 9.2 Work on a branch, not on `main`

```
git checkout -b your-change-name
```

Use a name that says what it is: `fix-sector-headroom`, `add-liquidity-cap`.

### 9.3 Before every commit, run three things

This is not optional and it is not a formality. In order:

```
.venv/bin/python scripts/check_build.py
```

151 installation probes. Red means you broke an invariant.

```
for f in scripts/test_*.py; do echo "--- $f"; .venv/bin/python "$f" | tail -1; done
```

Every behavioural test suite. All must say `全部 ... 通过`.

```
python3 scripts/git_preflight.py
```

The secrets gate. It checks five things:

```
[1] Is .gitignore actually blocking .env, *.db, raw-data/ ...
[2] Do any files about to be committed match the deny list
[3] Does any file's CONTENT look like a real key
[4] Has any of this been committed in the PAST (history, not just working tree)
[5] Any files over 5 MB
```

**If [4] is ever red, stop and tell the owner immediately.** Deleting the file
does not help — the key must be rotated and the repository rebuilt.

### 9.4 Commit and push

```
git add -A
git status
```

**Read the `git status` output before continuing.** Confirm you do not see
`.env`, anything ending in `.db`, or anything under `raw-data/`, `logs/`,
`memory/`, `lancedb/`.

```
git commit -m "short description of what changed and why"
git push -u origin your-change-name
```

Then open a Pull Request on GitHub so the owner can review before it lands on
`main`.

### 9.5 If push is rejected

```
! [rejected]  main -> main (non-fast-forward)
```

means the remote has commits you do not. **Never use `git push --force`** — it
permanently deletes whatever was on the remote. Instead:

```
git pull --rebase origin main
git push
```

If that reports `CONFLICT`, you can always back all the way out with no damage:

```
git rebase --abort
```

and ask before proceeding.

---

## 10. How to work on this codebase without breaking it

The invariants below are not style preferences. Each one exists because the
opposite behaviour shipped once and caused a real, silent failure.

### 10.1 Never let a failure return something that looks like a result

The rule: on failure, **raise**. Let the caller record it and move on. A
degraded return value that is shaped like a success is the single most expensive
class of bug in this system, because nothing anywhere reports it.

### 10.2 Zero is a conclusion; blank is not

`0 triggers` is information. An empty line is ambiguous. Every counter prints
even when it is zero.

### 10.3 A warning that is always on is the same as no warning

If an alert would fire on the most common normal day, it is not an alert. Before
adding one, ask: what fraction of days does this fire on? If the answer is
"most," people will learn to ignore it, and it will also be ignored on the day
it matters.

### 10.4 Two copies of the same logic means one of them is untested

If you find yourself writing the same decision in two places, extract it. The
copy your test happens to exercise will stay correct; the other will drift, and
it will be the one that actually runs.

### 10.5 Assert on structure, not on text

`assert "engine" in source_code` can be satisfied by a **comment**. Use the `ast`
module and assert on the actual call. This mistake has been made repeatedly here,
including in code written last week.

### 10.6 A test that cannot fail is worse than no test

It reports "this is covered" when nothing is. When you add a test, break the
code on purpose and confirm the test goes red. If it stays green, the test is
decorative.

### 10.7 Never change the meaning of a stored field without bumping its version

Fields carry `SCHEMA_VERSION` constants. Adding a field is fine. Changing what an
existing field means requires a version bump, otherwise old and new records mix
in the same table and no one can tell which is which.

---

## 11. Where things live

```
src/cio/
  llm.py                 which model runs the debate (Ollama or Claude)
  debate.py              Bull / Bear / Judge / Synthesis prompts and flow
  unit_a.py              the research agent: collect -> gate -> debate -> thesis
  risk_officer.py        CRO: risk constraints and hard vetoes
  sizing.py              PC: position sizing, caps, binding constraint
  propose.py             turns a sizing decision into a trade proposal
  notify.py              "N proposals awaiting your approval"
  heartbeat.py           the daily report every stage writes into
  proposal_store.py      the approval state machine
  book.py                the portfolio ledger
  research/
    trigger.py           what counts as a reason to research something
    router.py            dedupe, merge, ageing, priority
    queue.py             the research queue state machine
    scheduler.py         daily budget: how many names, how much money
    pipeline.py          RESEARCHED -> CRO -> PC -> proposal
  technical/             the daily market scan and Signal Cards

scripts/
  check_build.py         151 installation probes  <- run this constantly
  test_*.py              behavioural test suites
  research_run.py        the daily pipeline entry point
  technical_snapshot.py  the daily market scan entry point
  notify_run.py          notification entry point
  git_preflight.py       the secrets gate

docs/
  RUNBOOK.md             how the owner runs it day to day
  build-notes/           one file per version: what changed and why
```

**Read `docs/build-notes/` before changing anything substantial.** Each note
explains not just what was built but which specific failure it was preventing.
Most of the surprising-looking code is there because something broke.

---

## 12. The one boundary you must not cross

There is exactly one hard gate in this system:

```
... -> proposal -> PENDING_APPROVAL -> [ HUMAN: approve or reject ] -> EXECUTED
```

The automation runs all the way to `PENDING_APPROVAL` and stops. It cannot
approve anything. This is enforced three ways:

1. The queue state machine: `APPROVED` has exactly one legal predecessor
2. An AST probe over seven modules: none of them may contain a call that
   transitions anything to `APPROVED`
3. A runtime test that runs the whole chain and asserts the end state

**If you add a new automated module, you must add it to that probe's list**
(`scripts/check_build.py`, function `_b124_...`, and `scripts/test_pipeline.py`).
A module that is not on the list is an unwatched bypass, and the probe stays green.

This is a governance boundary, not an efficiency question. It stays even if every
proposal is approved for a year. Do not "streamline" it.

---

## 13. If you get stuck

Report these three things and someone can usually tell you what happened:

```
.venv/bin/python --version
.venv/bin/python scripts/check_build.py 2>&1 | tail -20
```

plus the exact command you ran and its full output.

**Never paste the contents of `.env` when asking for help.**
