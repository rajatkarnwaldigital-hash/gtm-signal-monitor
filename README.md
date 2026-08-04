# gtm-signal-monitor

A daily monitor for one buying signal: **an early-stage startup posting its first
GTM/sales role.** Budget exists, urgency exists, and there is no GTM
infrastructure yet for the new hire to inherit — which is the engagement.

This is an experiment. It is meant to be cheap to read and easy to throw away.

## Why this exists separately from `yc-gtm-monitor-actions`

The YC monitor produces ~40 founder-pairs a week and 1–2 get acted on. Two
distinct failure modes, and this repo is built against both:

| Failure mode | What this repo does differently |
|---|---|
| Eight comparative judgments a day is too much friction, so the digest gets skipped | Leads are **scored and ranked**, then hard-capped at `DAILY_CAP` (default 5) with a `SCORE_FLOOR`. Anything below the bar goes in a "no action expected" tail. The digest is a to-do list, not a decision problem. |
| A LinkedIn connection is accepted a week later and there is no memory of why it was sent | Every lead that makes a digest is appended to **`ledger.md`**, committed to the repo, with its score, signals, the "why now", and the exact opener sent. Grep the company name to recover the context. |

It does **not** include Y Combinator as a source — `yc-gtm-monitor-actions`
already covers YC, and duplicate digests would defeat the purpose.

## Sources

No scrapers. The data layer is free and commoditized, so both sources are
adapters over things other people already maintain.

**Job signal — `jobs.techstars.com`** (Getro board, network 89; ~4,800 live jobs
across ~2,900 companies). The board server-renders its results into
`__NEXT_DATA__` and serves the same payload as JSON at
`/_next/data/<buildId>/jobs.json`, honouring its own `q` and `filter` params. No
key, no HTML parsing.

Two constraints shaped the design: the endpoint returns **only 20 results per
query with no pagination** (the paginated `api.getro.com/v2` endpoint is 401),
and results are **strictly newest-first**. So instead of trying to walk 4,800
jobs, the source issues one narrow query per GTM archetype and takes each
slice's newest 20 — ~15 requests total. A slice that comes back full logs a
`[saturated]` warning, which is the cue to split that term.

**Company enrichment — [`yigitmeteozcan/startups`](https://github.com/yigitmeteozcan/startups)**
via jsDelivr. The Techstars slice is ~5,100 companies with website, description,
program and cohort year. Joined to postings by normalised company name —
**measured at 99% hit rate** (103/104) on a live GTM sample. This is what makes
a lead contactable; the job board has no website field.

### Source health, checked 2026-08-04

| Repo | Status |
|---|---|
| `yigitmeteozcan/startups` | **Healthy.** Daily automated dataset commits, most recent the same day. Safe to depend on. |
| `sohan-shingade/jobslop` | **Stale as a project** — last substantive commit 2026-04-10, ~4 months ago. **Not depended on at runtime.** Its Consider/Getro approach was read and reused; its code is not imported. Its Getro adapter also falls back to `api.getro.com/v2`, which now returns 401 and yields only 20 jobs — the `_next/data` route used here is the working path. |

Because jobslop is stale, nothing here imports it. The Techstars board is queried
directly, so a jobslop bitrot cannot break this repo.

### Accelerator scope

Techstars only, deliberately. Excluded: **Antler, EF, Z Fellows, EWOR, Thiel**
(pre-product; cannot buy a build engagement) and **Plug and Play, Google for
Startups** (admit at a volume that carries no selection signal).

## Adding a source

Implement `sources/base.Source`, return `Lead` objects, register in
`sources/__init__.py`. Filtering, scoring, enrichment and delivery are all
downstream and need no changes.

```python
class MySource(Source):
    name = "my-source"
    def fetch(self) -> list[Lead]: ...
```

## How a posting becomes a lead

1. **Fetch** — narrow GTM slices per source, server-side filtered to
   pre-seed / seed / Series A.
2. **Filter** (`filters.py`) — `q` search is fuzzy and ORs terms, so every title
   is re-checked against a strict GTM regex locally. Disqualifies interns,
   "sales engineer", account managers (retention, not new revenue), etc. Last
   run: 155 postings → 55 qualified.
3. **Diff** against `seen.json`.
4. **Enrich** (`companies.py`) — join to the Techstars dataset for domain,
   description, cohort year.
5. **Rank** (`score.py`) — see below.
6. **Contacts + verification** (`contacts.py`) — Exa, on the top `SHORTLIST`
   (10) only. See below.
7. **Re-rank** the shortlist on verified numbers, then cut to `DAILY_CAP`.
8. **Context** (`brief.py`) — Claude (`claude-opus-5`) writes a one-line "why
   now" and a one-line opener, **only for leads that make the cut**.
9. **Deliver** — plain-text Gmail SMTP digest, then append to `ledger.md`.

The digest is deliberately plain text, formatted like `yc-gtm-monitor-actions`:
aligned labels, prose wrapped at 78 columns, URLs never broken, and the opener
flush-left under its own heading so it copies into LinkedIn without dragging
indentation along. An HTML version was built and rejected — it was more design
than the job needed.

### Contacts and verification (`contacts.py`)

One Exa call per shortlisted lead, doing two jobs:

**Who to talk to.** Returns a founder/exec and a GTM/BD person when both exist,
each with title and LinkedIn URL — so the digest names a person instead of
saying "your new sales lead". Hit rate on a live sample was 5/5 for a named
founder. Results are disambiguated against the company's LinkedIn slug and
domain, because a search for "Frictionless Technologies" also returns
Frictionless Capital and DataWhisper, unrelated companies sharing a word.

**Whether the board is telling the truth.** Getro's `stage` and `headCount` are
softer than they look. On the first live digest, **heva ranked #3 on "pre-seed,
1-10 employees" while actually being ~25 people with $6M raised across two
rounds** — its CEO was publicly posting a $1M revenue run rate. Exa's company
blurb carries headcount, YoY growth and total funding, so those numbers get
re-checked and a conflict costs the lead 6 points. Re-running that case drops
heva from 19 to 14 and out of the sent list.

Two passes on purpose: verification changes the score, so it must run before the
final cut — but enriching all ~50 qualified postings would be 50 Exa calls a day
for 5 slots. Ranking on board data first and enriching only the top 10 bounds
the cost while still fact-checking everything that gets sent.

Without `EXA_API_KEY` the step is skipped and the digest sends as before.

### Scoring

Blunt on purpose — the ordering matters, the numbers don't.

| Signal | Weight |
|---|---|
| Seed / Series A / pre-seed stage | +5 / +4 / +3 |
| 1–10 or 11–50 employees | +5 / +4 |
| "Founding" / "first" in title | +6 — no GTM function exists yet |
| "Head/VP/Chief/Director of" | +5 — owns the budget |
| Only GTM role open at the company | +3 — likely their first |
| 4+ GTM roles open | −3 — team already exists |
| Techstars cohort within 3 years | +3 (−2 if 8+ years old) |
| Posted within 3 days | +2 |
| No domain resolved | −4 — unreachable is unactionable |
| Board headcount contradicted by Exa | **−6** — the data the score rests on is wrong |
| Headcount shrinking YoY | −2 |

## Setup

Repository secrets (Settings → Secrets and variables → Actions):
`ANTHROPIC_API_KEY`, `EXA_API_KEY`, `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`,
`RECIPIENT_EMAIL`. Both `ANTHROPIC_API_KEY` and `EXA_API_KEY` are optional —
without them the digest still sends, with less context.
`GMAIL_APP_PASSWORD` is an [App Password](https://myaccount.google.com/apppasswords),
not the account password.

Runs at **05:30 UTC daily** — deliberately clear of the YC monitor's 03:30.

```bash
pip install -r requirements.txt && python3 monitor.py
```

**`seen.json` ships empty.** The first run seeds the baseline and sends no
email; the second run onward reports only genuinely new postings.

If the digest fails to send, nothing is marked as seen — leads are retried on
the next run rather than silently lost.

## Tuning

`DAILY_CAP` (5), `SCORE_FLOOR` (8), `TAIL_CAP` (8), `TAIL_FLOOR` (5) and
`SHORTLIST` (10, how many leads get an Exa call) are environment variables. If the digest feels thin or noisy after a week of real output, move
`SCORE_FLOOR` before anything else.

## Verified / not verified

Run end to end against live data on 2026-08-04: source fetch, filtering,
company join, scoring, ranking and digest rendering all confirmed working.

The Claude context step was confirmed working by a live send on 2026-08-04.

**The Exa step in `contacts.py` has not run against a live `EXA_API_KEY`** — no
credential was available in the build environment. What *was* verified: the
underlying searches (run through a separate Exa integration) returned a named
founder with a LinkedIn URL for 5/5 sampled companies, and every parser in
`contacts.py` — headcount, YoY growth, funding, rounds, role lines, conflict
detection — was unit-tested against those real response strings. What is
unverified is the HTTP call itself: endpoint, auth header and response envelope
are written to Exa's published REST spec but have not been exercised. Any
failure is caught and the lead falls back to its pre-enrichment digest entry.
