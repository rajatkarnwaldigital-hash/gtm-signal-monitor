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
6. **Context** (`brief.py`) — Claude (`claude-opus-5`) writes a one-line "why
   now" and a one-line opener, **only for leads that make the cut**.
7. **Deliver** — Gmail SMTP digest, then append to `ledger.md`.

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

## Setup

Repository secrets (Settings → Secrets and variables → Actions):
`ANTHROPIC_API_KEY`, `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `RECIPIENT_EMAIL`.
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

`DAILY_CAP` (default 5) and `SCORE_FLOOR` (default 8) are environment
variables. If the digest feels thin or noisy after a week of real output, move
`SCORE_FLOOR` before anything else.

## Verified / not verified

Run end to end against live data on 2026-08-04: source fetch, filtering,
company join, scoring, ranking and digest rendering all confirmed working.

**The Claude context step has not been exercised against a live API key** — no
credential was available in the build environment. The request shape was
confirmed valid (the SDK accepted the parameters and the call was rejected only
at authentication, HTTP 401, not 400). Hook/opener generation degrades to empty
strings on any failure, and the digest still sends without them.
