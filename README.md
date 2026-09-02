# Agentic Investment Intelligence System

A multi-agent equity research system that runs on one machine. It replaces the
*analyst workflow* — collect, filter, argue, review, size, log — not the analyst's
authority. Every number is computed by deterministic code; language models are
used only where language is the actual problem.

> **Not investment advice.** Research and paper portfolios only. No broker
> integration, no order routing. See [Scope and limits](#scope-and-limits).

---

## Contents

- [Why agents](#why-agents)
- [System map](#system-map)
- [Departments](#departments)
- [End-to-end flow](#end-to-end-flow)
- [Data sources and APIs](#data-sources-and-apis)
- [Repository layout](#repository-layout)
- [Install](#install)
- [Configuration](#configuration)
- [Daily operation](#daily-operation)
- [Testing](#testing)
- [Status and roadmap](#status-and-roadmap)
- [Scope and limits](#scope-and-limits)

---

## Why agents

An investment research desk is a division of labour: someone gathers material,
someone argues the bull case, someone argues the bear case, someone rules on the
argument, someone checks the risk, someone sizes the position, someone keeps the
book. Each role has a different failure mode, and the separation between them is
what makes the output reviewable.

A single language model prompted for "should I buy NVDA" collapses all of those
roles into one pass, with no boundary you can audit. This system keeps the
boundaries and assigns each role to a component with a fixed contract:

| Principle | What it means here |
|---|---|
| **Separation of concerns** | Research agents cannot approve trades. Execution cannot bypass risk. |
| **Adversarial validation** | Bull and bear are separate calls; a judge rules on the transcript. |
| **The model never touches a number** | Volatility, beta, drawdown, risk budget and position size are deterministic code. The model argues; it does not calculate. |
| **Auditability** | Every decision writes a lineage row: inputs, sources, which constraint bound the result. |
| **Human in the loop** | The system produces proposals. Capital moves only after explicit approval. |
| **Provider-agnostic** | Models and data sources sit behind interfaces; swapping either is a config change. |

---

## System map

```
════════════════════════ Data layer ════════════════════════════════════════

  quant_data.py      Daily OHLCV · S&P 500 universe · benchmark
  fundamentals.py    PIT XBRL from SEC (by filing date, not period end)
  collect.py         RSS news · EDGAR filings by CIK
         │
         ├──────────────────┬──────────────────────┐
         ▼                  ▼                      ▼

═══ Unit B measure ═══   ═══ Technical Observer ═══   ═══ CIO collect ═══
      (shipped)               (v1 · new)                  (shipped)

  19 factors            Support/resistance zones      News · 8-K
  cross-sectional z     RVOL · OBV slope · CMF        Earnings · filings
  compute_scores        RS vs SPY / sector ETF               │
  validation            ATR percentile · NR7                 ▼
  HAC + Holm + BH             │                        Evidence Gate
         │                    ▼                        (shipped · 135 probes)
         │            ┌───────────────┐                      │
         │            │  Signal Card  │                      │
         │            │  v1: describe │                      │
         │            │  no score     │                      │
         │            └───────┬───────┘                      │
         │                    ▼                              │
         │            Technical Gate                         │
         │            v2 score · v3 state machine            │
         │            **must be able to say "nothing today"** │
         │                    │                              │
         │                    │ Trigger: TECHNICAL           │ Trigger: EVIDENCE
         │                    └────────────┬─────────────────┘
         │                                 ▼
         └───────────────────────► Research Router
                                   (merges both entrances, tags the trigger)
                                          │
                                          ▼
                                   Unit A  ·  LLM
                            technicals + fundamentals + news
                                          │
                                     Bull / Bear
                                          ▼
                                        Judge
                                          ▼
        ┌─────────────────────────────── CRO ───────────────────────────┐
        │                                 ▼                             │
        │                          PC  produces targets                 │  deterministic
        │                                 ▼                             │  rules layer
        │                     CEO  grants authority ◄── money moves here│
        │                                 ▼                             │
        │                     Execution  next-open fill                 │
        │                                 ▼                             │
        │                       Ledger  produces facts                  │
        └───────────────────────────────────────────────────────────────┘
```

Boxes above the Research Router decide **who is worth researching**. Boxes below it
decide **what to do about it**. Only Unit A calls a language model.

---

## Departments

### CIO — collection and the Evidence Gate

Collects overnight news and filings, deduplicates, and answers one question:
*does any of this contain a completed, checkable fact?* Most days the answer is no,
and the pipeline stops there.

The gate classifies each item into three tiers — **substantive** (a completed,
verifiable company event), **context** (real reporting, no new fact), and
**empty** (forward-looking, price recaps, valuation opinions, listicles). It counts
*events*, not articles, so one press release reposted by three outlets cannot push a
name past the threshold.

```
Example — AMD, 2026-08-31 (real run)

  Three items were classified substantive, but two of them were the same
  press release carried by two outlets:

    "AMD, Cisco and HUMAIN Expand Saudi Arabia's AI Infrastructure
     as AMD Instinct Systems Go Live"
    "AMD and Cisco Expand AI Infrastructure in Saudi Arabia"

  Counting articles: 3 → threshold met → full adversarial debate runs.
  Counting events:   2 → below threshold → the system abstains.

  Deduplication does not catch this; the wordings differ. Event grouping does.
```

**Technical:** `collect.py` (feedparser, httpx), `material_gate.py` (deterministic
rules + optional LLM judge), `judge.py` (Rule / LLM / Hybrid judges behind one
interface), `vectorstore.py` (LanceDB, local embeddings).

---

### Unit B — cross-sectional measurement

Computes 19 factors across the S&P 500 — 11 price-based (momentum, reversal,
low-volatility, trend, volume, 52-week high, illiquidity, downside vol, skew) and
8 fundamental (gross profitability, operating margin, asset growth, leverage,
accruals, free cash flow, earnings growth). Standardises cross-sectionally,
neutralises by sector, and tests significance with Newey-West HAC standard errors
plus Holm and Benjamini-Hochberg corrections.

It is deliberately dull: **zero LLM dependency, fully unit-testable**, and it reports
"candidate anomaly, needs out-of-sample confirmation" rather than "signal found".

```
Example — a factor that survives in-sample IC but fails the non-overlapping
robustness check is reported as failing. The verdict string is fixed by code,
not chosen by a model.
```

**Technical:** `factors.py`, `unit_b.py`, `validation.py`, `quant_data.py`
(pandas/numpy, yfinance, akshare), `fundamentals.py` (SEC XBRL, point-in-time by
filing date).

---

### Technical Observer — chart structure as facts (v1)

Reads daily OHLCV and produces a **Signal Card**: support/resistance zones with
touch counts, relative volume, OBV slope, Chaikin Money Flow, relative strength
against SPY and the sector ETF, ATR percentile, range contraction.

v1 **describes only**. No score, no alert, no watchlist change, no trigger. A
banned-vocabulary probe fails the build if the words "bullish", "buy", "strong",
"institutional" or their Chinese equivalents appear in any field name or emitted
string — daily OHLCV cannot see who is trading, so the volume block is named
`accumulation_pressure_proxy` and returns four parallel numbers rather than one score.

`observe()` is a pure function of `(OHLCV panel, as_of)`. A test asserts
`observe(df[:t]) == observe(df, as_of=t)` field by field, which catches any
look-ahead. Values that cannot be computed are `null` **with a reason**, never `0`.

```
Signal Card fields (one per name per day)

  price_structure    zones above/below · touch count · ATR distance · range position
  volume             rvol_20 · obv_slope_20 · cmf_20 · up_down_volume_ratio_20
  relative_strength  excess vs SPY and sector ETF at 21/63/126d · RS slope
  volatility         atr_14 · atr_pct · atr_percentile_252 · range_pct_20 · NR7
  reasons            why any field is null

Whole-market base rates, 502 names × 6 sampled days (2026-07-27 … 2026-09-01):

  rvol_20 ≥ 1.5                     8.8% of names per day
  20-day spike count ≥ 5            8.9%
  CMF > 0.1 and OBV slope > 0      19.2%
  within 0.5 ATR of a zone above   21.5%
  all three of the above            11 occurrences in 3,012 name-days

The last line is why v1 does not alert: knowing how often a condition fires
comes before deciding whether it deserves an interruption.
```

**Technical:** `technical/{price_structure,volume,relative_strength,volatility,
observer,setups,store,review}.py`. No TA-Lib — ATR, OBV and CMF are written by
hand so every edge case is a decision we made (a limit-up bar where `high == low`
leaves CMF undefined, and the count of skipped days is reported).

---

### Research Router — one entrance, two triggers

Merges the Evidence Gate and the Technical Gate. A name reaches Unit A because
**new facts appeared** or because **its chart structure changed**, and the trigger
source is recorded so the research report says which one fired.

*Status: the Evidence trigger is live. The Technical trigger is deliberately not
connected in v1 — see [Status](#status-and-roadmap).*

---

### Unit A — adversarial research

The only component that calls a language model. Three roles argue over the same
evidence pack: bull, bear, judge. Every claim must cite numbered source material,
and a line-by-line checker runs **after** generation, flagging anything it cannot
trace to a source, plus misquotes, fabricated years, and peer comparisons with no
peer data.

```
Example — a bull argument citing "revenue up 12% YoY" is checked against the
source pack. If the figure appears nowhere, the line is flagged in the report
rather than silently kept.
```

**Technical:** `unit_a.py`, `debate.py`, `factlint.py`, `ollama_client.py`
(local Ollama), optional Anthropic API for the judge path.

---

### CRO — risk review

Deterministic. Checks position size, drawdown, liquidity, sector concentration and
volatility regime against the real portfolio. It can veto; it cannot propose.

**Technical:** `cro.py`, `risk_officer.py`, `regime.py`, `portfolio.py`.

---

### PC — position sizing

Turns an approved thesis into a target weight, and **records which constraint bound
it**. A 3.2% position set by the risk budget, by a sector cap, or by a
portfolio-level rescale points to three completely different corrections — and they
look identical in a holdings table.

**Technical:** `sizing.py`, `pc_ledger.py`.

---

### Execution and Ledger

Execution fills at the next open (paper only). The Ledger writes a 33-field lineage
row per decision: inputs, sources, gate verdict, model calls, binding constraint,
fill price, run id.

**Technical:** `execution.py`, `book.py`, `ledger.py`, `recon.py`, SQLite.

---

## End-to-end flow

```
1  Pre-market window (market timezone)
   collect RSS + filings  →  dedup  →  relevance filter  →  Evidence Gate
   pre-market brief delivered (PDF + Telegram)

2  Gate verdict
   fewer than 3 distinct substantive events  →  ABSTAIN, pipeline stops
   3 or more                                 →  continue

3  Research Router
   assembles the evidence pack, tags the trigger source

4  Unit A
   bull → bear → judge, each claim cited, citations verified after generation

5  CRO
   deterministic risk review against the live book; may veto

6  PC
   target weight + the constraint that bound it

7  CEO approval
   nothing moves without it

8  Execution → Ledger
   next-open fill, 33-field lineage row

After the close, the Technical Observer stores one Signal Card per name.
It feeds nothing yet — it accumulates.
```

---

## Data sources and APIs

Everything is free and keyless except the two marked optional.

| Source | Endpoint | Used for |
|---|---|---|
| **Yahoo Finance** | `yfinance` library | Daily OHLCV (adjusted), SPY benchmark, sector ETFs, index quotes |
| **Stooq** | `stooq.com` | Price fallback when Yahoo fails for a name |
| **AkShare** | `akshare` library | A-share OHLCV, CSI 300 constituents, turnover (CN mode only) |
| **SEC EDGAR — submissions** | `data.sec.gov/submissions/CIK{cik}.json` | Filing feed by CIK (8-K, 10-Q, 10-K, Form 4…) |
| **SEC EDGAR — XBRL** | `data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json` | Point-in-time fundamentals, indexed by filing date |
| **SEC company tickers** | `www.sec.gov/files/company_tickers.json` | Ticker → CIK mapping |
| **Wikipedia** | `en.wikipedia.org` | S&P 500 constituents + GICS sectors (snapshotted daily) |
| **Google News RSS** | `news.google.com/rss` | World/Business/Technology headlines + standing keyword queries |
| **Publisher RSS** | BBC, Guardian, Al Jazeera, DW, France24, NPR, CNBC, MarketWatch, Yonhap, Kyodo | International and business news (`world` bucket) |
| **Yahoo Finance RSS** | `feeds.finance.yahoo.com` | Per-ticker headlines |
| **Ollama** | `127.0.0.1:11434` | Local LLM inference — debate, summarisation, embeddings |
| **Anthropic API** *(optional)* | `api.anthropic.com` | Alternative Evidence-Gate judge. Sends public headlines only — never holdings, thesis ledger or NAV |
| **Telegram Bot API** *(optional)* | `api.telegram.org` | Brief and report delivery |

**No paid data, no API keys required to run.** Every fetch degrades honestly: a
dead source is recorded in the run status and printed in the report header rather
than silently returning nothing.

News buckets are market-aware. In `CIO_MARKET=us` the China bucket is not fetched,
and what was skipped is printed in the collection status — an invisible filter and
no filter at all look the same in a report.

---

## Repository layout

```
.
├── src/cio/                    Library. No entry point imports another entry point.
│   ├── collect.py              RSS + EDGAR fetch, per-source degradation
│   ├── process.py              Dedup → score → summarise → embed
│   ├── material_gate.py        Evidence Gate: tiering, event merging, policy
│   ├── judge.py                Rule / LLM / Hybrid judge behind one interface
│   ├── unit_a.py debate.py     Adversarial research (the only LLM caller)
│   ├── factlint.py             Post-generation citation verification
│   ├── unit_b.py factors.py    19-factor cross-sectional measurement
│   ├── validation.py           HAC, Holm, BH-FDR, IC decay
│   ├── quant_data.py           OHLCV, universe, benchmark, PIT snapshots
│   ├── fundamentals.py         SEC XBRL, point-in-time by filing date
│   ├── technical/              Technical Observer v1
│   │   ├── observer.py         Signal Card assembly (pure function)
│   │   ├── price_structure.py  Swings → ATR-normalised clustering → zones
│   │   ├── volume.py           RVOL · OBV · CMF · accumulation_pressure_proxy
│   │   ├── relative_strength.py  vs SPY, vs sector ETF (date-aligned)
│   │   ├── volatility.py       ATR, ATR percentile, range contraction
│   │   ├── setups.py           Frozen setup + event segmentation
│   │   ├── store.py            Write-once daily cards, version-stamped
│   │   └── review.py           Human review log (the screen's primary KPI)
│   ├── cro.py risk_officer.py  Deterministic risk review
│   ├── sizing.py pc_ledger.py  Position sizing + binding-constraint lineage
│   ├── execution.py book.py    Paper fills, portfolio, reconciliation
│   ├── schedule.py             Market-timezone windows (not machine timezone)
│   ├── render*.py              Markdown / PDF / HTML report renderers
│   └── config.py db.py models.py utils.py
│
├── run_*.py                    Entry points, one per department
│   ├── run_premarket.py        Collect → gate → brief → deliver
│   ├── run_scan.py             Evidence Gate scan across the watchlist
│   ├── run_unit_a.py           Adversarial research on one name
│   ├── run_unit_b.py           Cross-sectional factor run
│   ├── run_cro.py run_pc.py    Risk review, position sizing
│   ├── run_execute.py          Next-open paper fill
│   └── run_book.py             Portfolio and attribution
│
├── scripts/
│   ├── check_build.py          135 install probes — run this first, always
│   ├── test_*.py               Offline test suites (network is hard-blocked)
│   ├── eval_judge.py           Judge evaluation: tuned vs held-out corpus
│   ├── technical_snapshot.py   Daily Signal Card capture + review log
│   └── technical_distribution.py  Whole-market base rates
│
├── config/                     Watchlist, sources, thresholds (YAML)
├── ui/                         Local dashboard (Shiny)
├── docs/build-notes/           One note per build: the defect, why it was
│                               silent, and the probe that now catches it
└── AGENTS.md                   Repository instructions for coding agents
```

Directories holding real data — `raw-data/`, `memory/`, `out/`, `logs/`,
`lancedb/`, `*.db` — are gitignored. See [Configuration](#configuration).

---

## Install

Requires Python 3.9+ and [Ollama](https://ollama.com) for local inference.

```bash
git clone https://github.com/CrystalF042/angelic-system.git
cd angelic-system
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
.venv/bin/python scripts/check_build.py
```

`check_build.py` runs 135 probes and must be green before anything else. It is the
accumulated record of defects that did not raise an error — each probe exists
because something once failed silently.

Pull the local models:

```bash
ollama pull gpt-oss:20b
ollama pull phi4-mini
ollama pull nomic-embed-text-v2-moe
```

PDF rendering of Chinese reports needs two fonts, which are not committed
(33 MB). English-only operation does not need them.

```bash
mkdir -p assets/fonts
curl -L -o assets/fonts/NotoSansSC-Regular.ttf \
  https://github.com/googlefonts/noto-cjk/raw/main/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf
```

---

## Configuration

All settings live in `.env` (never committed) and `config/*.yaml`.

```bash
CIO_MARKET=us                       us | cn — drives timezone, sources, benchmark
SEC_USER_AGENT=you@example.com      required by SEC; they will block a blank UA
OLLAMA_HOST=http://127.0.0.1:11434
CIO_MODEL_BRIEF=gpt-oss:20b
CIO_MODEL_LIGHT=phi4-mini
CIO_MODEL_EMBED=nomic-embed-text-v2-moe

CIO_ANTHROPIC_API_KEY=              optional — alternative gate judge
TELEGRAM_BOT_TOKEN=                 optional — delivery
TELEGRAM_CHAT_ID=
```

`CIO_MARKET` is the switch that matters most. It selects the market timezone,
the benchmark (SPY vs CSI 300), the news buckets, and the trading calendar.
It defaults to `cn`; set it explicitly in `.env` so every entry point agrees.

| File | What to edit |
|---|---|
| `config/watchlist_us.yaml` | Names to monitor, GICS sectors, index anchors |
| `config/sources.yaml` | RSS feeds and keyword queries, tagged by market bucket |
| `config/analytics_thresholds.yaml` | Risk and sizing limits |

---

## Daily operation

Schedule both jobs hourly. Each one checks the **market** clock and exits in
milliseconds outside its window, so daylight-saving changes need no attention.

```bash
0 * * * 1-5  cd ~/angelic-system && .venv/bin/python run_premarket.py
0 * * * 1-5  cd ~/angelic-system && .venv/bin/python scripts/technical_snapshot.py
```

```bash
.venv/bin/python run_premarket.py --when      show the window and next run
.venv/bin/python run_scan.py --verbose        gate scan with full drop accounting
.venv/bin/python scripts/technical_snapshot.py --table    daily events table
.venv/bin/python scripts/technical_snapshot.py --review   review the screen's output
```

---

## Testing

```bash
.venv/bin/python scripts/check_build.py     135 install probes
.venv/bin/python scripts/test_intake.py     57  collection and gate
.venv/bin/python scripts/test_technical.py  34  Technical Observer
.venv/bin/python scripts/test_judge.py      21  judge guardrails
.venv/bin/python scripts/test_book.py       23  portfolio and ledger
```

Tests never touch the network — `scripts/_no_network.py` blocks sockets at two
levels, because an assertion that quietly reaches the internet is a different test
on a different machine.

Two conventions worth knowing before contributing:

**Assert on structure, not text.** Several tests once passed because they searched
the source for a phrase that also appeared in the comment explaining the fix.
Assert on behaviour, signatures, return values, or the AST.

**Mutation-test new rules.** Break each rule deliberately and confirm a test goes
red. A rule that no mutation catches is either not load-bearing, or the fixture is
too weak to exercise it — both have happened here, and both were found this way.

---

## Status and roadmap

| Component | State |
|---|---|
| CIO collection + Evidence Gate | Shipped · 135 probes |
| Unit B factor measurement | Shipped |
| Unit A adversarial research | Shipped |
| CRO / PC / Execution / Ledger | Shipped (paper only) |
| Technical Observer v1 | **Shipped — describe only** |
| Technical Gate v2 (scoring) | Not started — waiting on forward-collected data |
| Research Router — technical trigger | Not connected by design |

The Technical Observer is deliberately inert. Its setup definition is frozen with a
parameter fingerprint bound to a version string, and the thresholds were chosen from
whole-market base rates **before any forward return was examined**. Scoring comes
after enough forward-collected observations exist to test it — not before.

Historical replay is refused while `universe_pit` is false: the constituent list is
today's, so replaying a year of it would only study the names that survived.
Snapshots accumulate daily, and point-in-time status is judged per window rather
than as a global flag.

---

## Scope and limits

- **Paper only.** No broker integration, no order routing, no live capital.
- **Research output, not advice.** The system produces arguments and measurements
  with sources attached. Judgement stays with the operator.
- **Free data has holes.** Delisted names are typically unavailable, which bounds
  what any historical study here can claim. The system reports the hole rather than
  filling it.
- **Local models are weaker than frontier models.** The evaluation harness measures
  this on a held-out corpus rather than assuming it.
- **One market at a time.** `CIO_MARKET` switches wholesale; the two markets do not
  run simultaneously.

---

## License

MIT
