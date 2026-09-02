# Repository instructions for coding agents

This file is read automatically by OpenAI **Codex** and by other coding agents
that follow the `AGENTS.md` convention. Read it before changing anything.

---

## What this repository is

A local, multi-agent equity research system. Five roles, run as separate
modules with hard boundaries between them:

| Role | Question it answers | Calls an LLM? |
| --- | --- | --- |
| CIO (`brief.py`, `collect.py`) | What happened overnight | yes |
| Unit A (`unit_a.py`, `debate.py`) | Why might this name move | **yes — the only one** |
| Unit B (`analytics.py`, `measures.py`) | What is this name's current state | no |
| CRO (`risk_officer.py`) | If Unit A is right, what risk do we take | no |
| PC (`sizing.py`, `pc_ledger.py`) | How much of that risk to take | no |
| Rebalance (`rebalance.py`, `compliance.py`) | Turn target weights into share counts | no |
| Approval (`proposal_store.py`) | Did the CEO authorise it | no |
| Execution (`execution.py`) | Fill the approved share count at T+1 open | no |
| Ledger (`book.py`, `marks.py`) | What actually happened | no |
| Valuation (`valuation.py`, `corp_actions.py`) | What is it worth, and why did it change | no |
| Recon (`recon.py`) | Do the books agree with themselves | no |

**Only Unit A may call a language model.** Everything else is deterministic
code. Do not introduce model calls into `risk_officer.py`, `sizing.py`,
`measures.py`, `analytics.py`, or `pc_ledger.py`.

---

## Rules that must not be broken

These are not style preferences. Each one exists because breaking it caused a
real defect that **did not raise an error** — the program finished normally,
the report rendered normally, and the information was wrong.

1. **Never return `0` for "we could not measure it."** Return `None`.
   `beta = 0` reads as "does not move with the market"; the truth was "unknown".
   The two lead to opposite risk conclusions.

2. **Units are explicit at module boundaries.** `measures.py` returns
   *percent* (`40.74` means 40.74%). `risk_officer.POLICY` thresholds are
   *decimals* (`1.50` means 150%). Convert with `measures.as_ratio()`.
   `risk_officer.check_units()` raises if a value is out of physical range —
   do not "fix" that by auto-dividing; guessing a unit fails just as silently.

3. **Do not compute a derived state in a caller.** If the gate says
   `conviction_cap == "弱"`, use that value. Never re-implement
   `if level == THIN: cap = "weak"` — two copies of a rule always drift, and
   the copy does not raise when the original changes.

4. **All three brief renderers must stay in sync**: `render.render_brief_md`,
   `render.render_brief_pdf` (reportlab), `render_html.render_brief_html`
   (the one that actually produces the PDF). Adding a section to one and not
   the others produces two different reports on the same day, with no error.

5. **`stdout` is a machine contract.** For any `--json` entry point, the entire
   stdout must parse with a single `json.loads()`. Logs and `[STAGE]` progress
   events go to stderr. One stray `print()` breaks the UI silently.

6. **Never skip ledger writes.** `pc_ledger.record()` must run for every
   candidate including vetoed and unsized ones. A run that reports positions but
   records nothing makes `--stats` quietly wrong.

7. **Report what was skipped.** A data source that silently fails looks exactly
   like "no news today". Dropped feeds, unevaluated caps, and missing
   measurements must all appear in the output.

8. **Never collapse "no target" into "target 0".** `targets.get(ticker, 0.0)` is
   the single most dangerous line this repo can contain. A position the run did
   not evaluate must be held, not sold. The only place that maps a recorded
   decision to a target is `rebalance.target_from_decision()`.

9. **A compliance result with unevaluated checks is `PARTIAL`, never `PASS`.**
   `PASS` is read as "risk checked this and it's fine". Four of the six
   pre-trade checks are not wired up yet.

10. **The four layers never merge.** PC produces targets, the CEO produces
    authorisation, execution produces trades, the ledger produces facts. If
    targets could write positions directly, "what did we hold on 3 March"
    would have no answer.

### Execution rules frozen in Build 1

- **What is approved is a share count, not a weight.** Shares are computed at
  decision time from the T-close NAV and price, frozen at approval, and filled
  at the next session's open. Re-deriving shares at execution means the CEO
  approved one thing and a different thing traded.
- **`execution_price_basis = "T+1_OPEN"`**, recorded on every proposal and
  trade. Filling at the decision day's close is look-ahead.
- **Approvals expire** (`rebalance.MAX_SESSION_GAP_DAYS`). A four-day-old share
  count trades perfectly legally and is wrong.
- **Trade the delta only**, with a no-trade band; a full exit ignores the band.
- **Book at raw, unadjusted prices** (`marks.PRICE_BASIS`). Adjusted prices are
  correct for measurement and wrong for a book of record: they change
  retroactively, so a cost basis recorded today stops matching the source.

### Valuation rules frozen in Build 3

- **Corporate actions are applied before marking**, always. Marking post-split
  prices against pre-split shares produces a perfectly normal number that is
  wrong by a factor of the split ratio, with no error.
- **A split that cannot be priced on its ex-date is not applied.** Rounding the
  fractional share away silently discards value; leaving the book un-split is
  worse. Refuse, flag, and let the mark report the ticker as unpriced.
- **`day_pnl` is a derived value.** `valuation.recompute_pnl()` rebuilds the
  whole series after every mark, because backfilling an earlier date changes
  what "the previous day" means for every row after it.
- **A day with any unpriced holding gets `nav = NULL`, `complete = 0`.** Never
  "value the rest" — that shows up as a phantom loss day followed by a phantom
  gain, both looking entirely normal.
- **Never mark a date before the book's `opened_on`.**
- **The benchmark is total return** (`valuation.BENCH_BASIS`, adjusted close).
  This is the one place on the ledger side where adjusted prices are correct.
  If the benchmark cannot be fetched, excess return is not computed — never
  substitute price return.
- **Excess return is always printed next to the invested percentage.** A 3%
  invested portfolio "underperforming" a fully invested benchmark says nothing
  about stock selection.
- **Recon failure blocks the P&L statement entirely.** A statement with a
  warning still gets read and believed.

---

## Before you finish any change

```bash
python scripts/check_build.py      # 92 install probes — must be all green
python scripts/test_unit_a.py
python scripts/test_cro_pc.py
python scripts/test_sizing.py
python scripts/test_analytics.py
python scripts/test_rebalance.py
python scripts/test_execution.py
python scripts/test_book.py
```

Set `CIO_DB` to a scratch path when running anything that writes proposals, so
a demo does not leave fake pending approvals in the real book.

On Windows use `python` from the activated venv (see README).

**When you fix a defect, add a probe to `scripts/check_build.py` that fails on
the old behaviour.** Every check in that file corresponds to a bug that actually
happened.

### Writing tests: assert structure, not comment text

A recurring mistake in this repo: a test greps the source for a phrase, and the
comment explaining the fix contains that same phrase, so the test can never
pass. Assert on behaviour, function signatures, return values, or the AST —
never on the presence of a word in a comment.

---

## Style

- Python 3.9 compatible (`from __future__ import annotations` where needed).
- Comments explain **why**, especially why an obvious-looking alternative is
  wrong. Do not remove the explanatory comments; they are the record of what
  went wrong before.
- Chinese comments are intentional and should stay Chinese.
- No new runtime dependencies without a reason stated in the pull request.

---

## Things not to do

- Do not add live trading, broker APIs, or order execution. The system is
  research and paper portfolios only.
- Do not add paid data sources. Free sources only.
- Do not normalise portfolio weights to 100%. The residual is cash by design.
- Do not make the system produce output on days when the evidence gate finds
  nothing. "No opinion today" is a feature, and it is tested.
