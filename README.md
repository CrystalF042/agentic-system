# CIO Agent — a local multi-agent equity research system

An evidence-gated research pipeline that runs entirely on one machine: local
language models, free data sources, paper portfolios only.

Its most unusual property is that **it refuses to produce an opinion on most
days.** In a recent scan of ten semiconductor names, 76 collected articles
yielded 3 with substantive new facts; the system declined to research the other
seven. That is the design, not a failure mode.

> **Not investment advice.** Research output only. The system does not place
> orders and has no broker integration. See [Scope and limits](#scope-and-limits).

---

## Contents

- [What it does](#what-it-does)
- [Architecture](#architecture)
- [Install — Windows](#install--windows)
- [Install — macOS / Linux](#install--macos--linux)
- [Daily use](#daily-use)
- [Web UI](#web-ui)
- [Scheduling](#scheduling)
- [Working with Codex or another coding agent](#working-with-codex-or-another-coding-agent)
- [Machine-facing contracts](#machine-facing-contracts)
- [Repository layout](#repository-layout)
- [Scope and limits](#scope-and-limits)

---

## What it does

```
07:00 ET   collect overnight news + futures/macro snapshot  →  pre-market brief
           ↓
           evidence gate: which names actually have new, checkable facts?
           ↓  (most days: none)
           adversarial research on the ones that do — 6 model calls
           ↓
           deterministic risk review  →  deterministic position sizing
           ↓
           every decision written to a 33-field lineage table
```

Four properties distinguish it from asking a chat model for a daily briefing:

**It can decline.** If no collected material contains a completed, checkable
fact, Unit A does not run at all — zero model calls, `Formal vote: ABSTAIN`.

**The model never touches a number.** Volatility, beta, drawdown, risk budget
and position size are computed by deterministic code. The language model
argues; it does not calculate.

**Every claim carries a citation, verified after generation.** Arguments must
cite numbered source material. A line-by-line checker marks anything it cannot
trace, and separately flags misquotes, fabricated years, peer comparisons with
no peer data, and valuation statements pointing the wrong way.

**Every sizing decision records which constraint bound it.** A 3.2% position
may be set by the risk budget, a sector cap, or a portfolio-level rescale.
Those three point to completely different corrections, and they look identical
in a holdings table.

---

## Architecture

Five roles with hard boundaries. **Only one of them may call a language model.**

| Role | Question | LLM | Key modules |
| --- | --- | --- | --- |
| **CIO** | What happened overnight | yes | `brief.py` `collect.py` `market_now.py` |
| **Unit A** | Why might this name move | **yes** | `unit_a.py` `debate.py` `material_gate.py` |
| **Unit B** | What is its current state | no | `analytics.py` `measures.py` `factors.py` |
| **CRO** | If Unit A is right, what risk is that | no | `risk_officer.py` `regime.py` |
| **PC** | How much of that risk to take | no | `sizing.py` `pc_ledger.py` |
| *CEO* | Do it or not | *human* | — |

The separation is enforced in code, not by convention. `risk_officer.assess_one()`
has **no parameter** for Unit A's written argument — it receives only structured
fields (direction, conviction, gate level, invalidation conditions). Give a risk
reviewer the full bull and bear case and it will start re-litigating whether the
view is correct, which makes it a second investment committee rather than an
independent risk check.

### The evidence gate

Collected material is classified deterministically — a completed action verb
plus either a hard numeric anchor or a named material event:

| Gate | Meaning | Effect |
| --- | --- | --- |
| `SUFFICIENT` | ≥3 substantive items | full 6-call adversarial research |
| `THIN` | 1–2 substantive items | research runs, **conviction capped** → half risk budget |
| `INSUFFICIENT` | 0 substantive items | **does not run.** No new thesis recorded |
| `UNRECORDED` | gate never ran for this thesis | no position, **and says so distinctly** |

The last row matters: "the gate ran and found nothing" and "the gate never ran"
are different facts. Collapsing them prints a false statement in a direction
that looks safe, so it can survive indefinitely.

### Position sizing

```
RB      = 1.5% × conviction multiplier × market-regime multiplier
σ_eff   = max(σ₆₀, 0.75·σ₆₀ + 0.25·σ₂₅₂, 15% floor)
w_raw   = RB / σ_eff
w_final = min(w_raw, single-name cap, sector headroom, theme headroom, …)
```

`σ_eff` is deliberately one-directional: it can only raise the volatility
estimate, so the rule can only shrink a position. The 15% floor is a **risk
policy, not a fitted parameter** — `w ∝ 1/σ` diverges as σ → 0, and volatility
is mean-reverting, so quiet names would otherwise receive their largest weight
immediately before expansion.

Weights are **not normalised to 100%**. The residual is cash. Normalising would
inflate back exactly the positions the risk rules just reduced.

---

## Install — Windows

Tested target: Windows 10/11, PowerShell, Python 3.9–3.12.

### 1. Prerequisites

- [Python](https://www.python.org/downloads/) — during setup tick
  **"Add python.exe to PATH"**
- [Git](https://git-scm.com/download/win)
- [Ollama for Windows](https://ollama.com/download/windows) — the local models

### 2. Clone and create the environment

```powershell
git clone https://github.com/<your-account>/cio-agent.git
cd cio-agent

python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If PowerShell refuses to run the activation script:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

### 3. Pull the local models

```powershell
ollama pull gpt-oss:20b
ollama pull phi4-mini
ollama pull nomic-embed-text-v2-moe
```

`gpt-oss:20b` needs roughly 16 GB of RAM. On a smaller machine substitute a
smaller model and set `CIO_MODEL_BRIEF` accordingly.

### 4. Configure

```powershell
Copy-Item .env.example .env
notepad .env
```

Minimum for Telegram delivery — create a bot with
[@BotFather](https://t.me/botfather), then message it once and read your chat id
from [@userinfobot](https://t.me/userinfobot):

```ini
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_CHAT_ID=123456789
```

Leave both blank to run without delivery — reports are still written to disk.

### 5. Verify the install

```powershell
python scripts\check_build.py
```

60 probes. **All green before running anything else.** Each probe calls the
function and checks its behaviour, because a wrong version of this codebase
does not fail loudly — it runs to completion and produces a plausible report
from stale code.

### 6. First run, offline

```powershell
$env:CIO_TG_DRYRUN=1; $env:CIO_MOCK_LLM=1
python run_premarket.py
```

### Windows notes

| | macOS / Linux | Windows |
| --- | --- | --- |
| venv python | `.venv/bin/python` | `.venv\Scripts\python.exe` |
| activate | `source .venv/bin/activate` | `.\.venv\Scripts\Activate.ps1` |
| set a variable | `CIO_MARKET=us python x.py` | `$env:CIO_MARKET="us"; python x.py` |
| scheduling | `launchd` | Task Scheduler |
| path separator in commands | `/` | `\` (Python code uses `pathlib`, so paths inside the code are already portable) |

---

## Install — macOS / Linux

```bash
git clone https://github.com/<your-account>/cio-agent.git
cd cio-agent

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

ollama pull gpt-oss:20b && ollama pull phi4-mini && ollama pull nomic-embed-text-v2-moe

cp .env.example .env && ${EDITOR:-nano} .env
python scripts/check_build.py
```

---

## Daily use

```bash
# Pre-market brief — news + futures/macro snapshot, every number timestamped
python run_premarket.py

# Which names actually have new evidence today? Zero model calls, ~2–3 min / 10 names
CIO_MARKET=us python run_scan.py NVDA AVGO AMD MU TSM AMAT LRCX ARM MRVL QCOM
CIO_MARKET=us python run_scan.py NVDA --verbose      # per-item gate reasoning
CIO_MARKET=us python run_scan.py NVDA --json         # structured output

# Full adversarial research on one name — 6 local model calls, ~3–4 min
CIO_MARKET=us python run_unit_a.py "AMAT"
CIO_MARKET=us python run_unit_a.py "NVDA" --force    # deliberate human override

# Risk review + position sizing — deterministic, seconds
CIO_MARKET=us python run_pc.py
CIO_MARKET=us python run_pc.py --tg                  # also push to Telegram

# Which constraint has been setting position sizes historically?
python run_pc.py --stats

# Verify market-data symbols still return data
python scripts/check_market_now.py
```

### Where output goes

| What | Where |
| --- | --- |
| Pre-market brief | `out/*.pdf`, Markdown in `Topic Archive/` |
| Unit A report | `Topic Archive/` as `.md`, `.pdf`, **and `.json`** |
| Open theses, invalidation conditions | `cio.db` → `theses` |
| Every sizing decision | `cio.db` → `pc_lineage` (33 fields) |

---

## Web UI

An optional Shiny dashboard for driving the pipeline by hand. It **shells out to
the CLI and never imports the engine**, so the UI cannot re-implement or bypass
a rule.

Shiny 1.6+ requires Python ≥ 3.10; on Python 3.9 pin `shiny==1.5.0`.
Use a **separate** virtual environment from the engine.

<details>
<summary><b>Windows</b></summary>

```powershell
python -m venv .venv-ui
.\.venv-ui\Scripts\Activate.ps1
pip install "shiny==1.5.0"

$env:CIO_HOME=(Get-Location).Path
$env:CIO_PY="$env:CIO_HOME\.venv\Scripts\python.exe"
shiny run ui\app.py --port 8000
```
</details>

<details>
<summary><b>macOS / Linux</b></summary>

```bash
python3 -m venv .venv-ui
.venv-ui/bin/pip install "shiny==1.5.0"

CIO_HOME=$PWD CIO_PY=$PWD/.venv/bin/python \
  .venv-ui/bin/shiny run ui/app.py --port 8000
```
</details>

Open `http://127.0.0.1:8000`. Seven tabs follow the responsibility chain:
scan → research progress → Unit A view → Unit B measurements → CRO risk →
PC sizing → historical attribution.

---

## Scheduling

The pre-market brief should arrive **before the US open**, around 07:00 ET.
Schedule in **local machine time**, never hard-coded UTC — otherwise every task
silently shifts by an hour when daylight saving changes.

<details>
<summary><b>Windows — Task Scheduler</b></summary>

```powershell
$py  = "$PWD\.venv\Scripts\python.exe"
$act = New-ScheduledTaskAction -Execute $py -Argument "run_premarket.py" -WorkingDirectory $PWD
$trg = New-ScheduledTaskTrigger -Daily -At 7:00am
Register-ScheduledTask -TaskName "CIO PreMarket" -Action $act -Trigger $trg
```

Weekdays only: use `-Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday`.
</details>

<details>
<summary><b>macOS — launchd / cron</b></summary>

```
0 7 * * 1-5  cd $HOME/cio-agent && .venv/bin/python run_premarket.py
```
</details>

---

## Working with Codex or another coding agent

The repository ships an **[`AGENTS.md`](AGENTS.md)** at the root. OpenAI Codex
and several other coding agents read this file automatically and follow it as
repository instructions — no configuration needed.

It states the boundaries that must not be crossed (which modules may call a
model, the unit contract between measurement and policy, the stdout contract,
the requirement that all three renderers stay in sync) and the test commands to
run before finishing a change.

**Suggested workflow**

1. Point Codex at the repository.
2. Ask for a change in plain language, e.g. *"add a liquidity cap to the CRO
   using 20-day average dollar volume."*
3. Require that `python scripts/check_build.py` passes before the change is
   accepted.
4. For a bug fix, ask it to **add a probe to `scripts/check_build.py` that fails
   on the old behaviour** — that file is the accumulated record of defects that
   did not raise errors.

A note on tests: several tests in this repository once failed because they
searched the source for a phrase that also appeared in the comment explaining
the fix. Assert on behaviour, signatures, return values, or the AST — never on
whether a word appears in a comment. `AGENTS.md` says this too.

---

## Machine-facing contracts

Three contracts are frozen so a UI, a scheduler, or another agent can drive the
system without knowing anything about investing.

| Contract | Form |
| --- | --- |
| **Result** | The **entire** stdout of any `--json` command parses in one `json.loads()`. Top level always carries `schema_version`, `run_id`, `kind`, `status`. |
| **Progress** | stderr emits `[STAGE] <name> \| <detail>` as the pipeline advances. Named events, not `n/N` — a gate-blocked run legitimately takes a different path. |
| **Execution** | Callers invoke `run_*.py` as subprocesses. They do not import the engine. |

`status` is deliberately explicit: `completed`, `no_candidates`, `gate_blocked`,
`no_evidence`, `failed`. "Nothing today" and "the run crashed" both produce an
empty result array and must remain distinguishable.

`check_build.py` enforces all three, including that stdout parses in a single
call — one stray debug `print()` turns the probe red instead of silently
breaking every consumer.

---

## Repository layout

```
src/cio/
  material_gate.py    evidence classification — the gate
  unit_a.py           adversarial research pipeline
  debate.py           6-call debate + citation verification
  measures.py         shared deterministic statistics (percent units)
  analytics.py        Unit B cross-sectional measurement
  risk_officer.py     CRO — constraints and vetoes, never a weight
  sizing.py           PC — the only place a position size is produced
  pc_ledger.py        33-field lineage per sizing decision
  thesis_store.py     thesis ledger + invalidation re-checks
  market_now.py       futures / macro snapshot with measured freshness
  render*.py          Markdown, reportlab PDF, HTML→PDF
run_*.py              entry points
scripts/              check_build.py (60 probes) + test suites
ui/                   optional Shiny dashboard
config/               source lists, watchlist — edit these, not the code
```

---

## Scope and limits

- **Research only.** No orders, no broker API, no live trading — and none planned.
- **Paper portfolios.** Sizing produces target weights for a paper book.
- **The production alpha set is empty, deliberately.** 19 candidate factors
  across 5 forward-return horizons gave 46 tests; 7 were nominally significant
  against ~2.3 expected by chance at α = 0.05, and **none survived Holm or
  Benjamini–Hochberg correction.** Nothing was promoted. The factor library
  informs risk measurement, not stock selection.
- **Free data sources only** — Google News RSS, Yahoo Finance, Stooq, akshare,
  SEC EDGAR. No paid subscriptions.
- **Local models by default.** Nothing leaves the machine unless you explicitly
  enable a cloud provider.
- Single-user. No authentication, no multi-tenancy.

---

## License

Add a license before making the repository public. Without one, others have no
rights to use the code.

