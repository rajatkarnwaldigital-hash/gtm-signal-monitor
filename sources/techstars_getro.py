"""Techstars portfolio jobs, via Getro's paginated v2 search API.

jobs.techstars.com is a Getro board (network 89, ~4,700 live jobs across ~2,900
companies). No scraping and no API key.

This module previously read the board's `/_next/data/<buildId>/jobs.json`
endpoint, because api.getro.com/v2 appeared to return 401. It does not: it
returns **406 unless you send `Accept: application/json`**. With that header the
paginated endpoint works fine, which removes the constraint the old design was
built around — we no longer need one narrow query per GTM archetype, and there
is no newest-20 ceiling to work under. We now walk the full result set.

Two things about this endpoint still shape the design:

  * `hitsPerPage` is capped at 20 server-side. Larger values are silently
    clamped, so pagination is mandatory rather than optional.
  * **The `stage` filter is silently ignored.** Passing it returns the
    unfiltered count (4,689 either way) and happily includes series_b+
    companies. Only `job_functions` actually filters server-side. Stage is
    therefore applied locally, off `organization.stage`, which is present on
    every row. Do not re-add it to the request payload believing it works.

Net effect vs. the old slice approach: ~473 early-stage GTM postings across
~213 companies in ~41 requests, instead of at most 20 per archetype with heavy
overlap between slices.

`job_functions` uses an exact vocabulary — "Sales" matches nothing,
"Sales & Business Development" is the real value — so the constants below are
pinned and checked at runtime by `_validate()`.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from .base import Lead, Source

BOARD = "https://jobs.techstars.com"
NETWORK = 89
API = f"https://api.getro.com/api/v2/collections/{NETWORK}/search/jobs"

# Pinned Getro job_functions vocabulary. Getro files RevOps, partnerships and
# growth-engineering roles under Operations, so it is pulled too; filters.py
# GTM_TITLE does the precision pass on titles locally.
GTM_FUNCTIONS = [
    "Sales & Business Development",
    "Marketing & Communications",
    "Operations",
]

# Applied locally — see the module docstring on why this cannot be a filter.
# Series B+ companies have GTM leadership and usually an ops team already,
# which is the opposite of the buying signal.
EARLY_STAGE = {"pre_seed", "seed", "series_a", "series_unknown"}

PAGE_SIZE = 20  # server-side cap; larger values are silently clamped
MAX_PAGES = 200  # safety rail against an unbounded loop if `count` misbehaves
REQUEST_PAUSE = 0.25
TIMEOUT = 30
HEADERS = {
    "Content-Type": "application/json",
    # Without this the API returns 406, which is what previously read as "401".
    "Accept": "application/json",
    "Origin": BOARD,
    "Referer": f"{BOARD}/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}

# Getro encodes headcount as a bucket ordinal, not a real employee count.
HEADCOUNT_BUCKETS = {
    1: "1-10",
    2: "11-50",
    3: "51-200",
    4: "201-500",
    5: "501-1000",
    6: "1001-5000",
    7: "5000+",
}

# The board renders status badges adjacent to the title, and they arrive
# concatenated onto it with no separator ("...(Vet Sales)NewOn-Site").
_BADGE_RE = re.compile(r"(?:New|Hybrid|On-?Site|Remote|Urgent|Featured)+$")


def _clean_title(title: str) -> str:
    title = " ".join(title.split())
    prev = None
    while prev != title:
        prev = title
        title = _BADGE_RE.sub("", title).strip()
    return title


def _post(payload: dict, attempts: int = 4) -> dict:
    last = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(
                API, data=json.dumps(payload).encode(), headers=HEADERS
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            last = e
            if e.code < 500 and e.code != 429:
                raise
        except Exception as e:  # noqa: BLE001 - retry any transport fault
            last = e
        if i < attempts - 1:
            time.sleep(2 ** i)
    raise RuntimeError(f"Getro request failed after {attempts} attempts: {last}")


def _results(body: dict) -> tuple[list[dict], int]:
    """Unwrap the response and fail loudly if its shape moved."""
    res = (body or {}).get("results")
    if not isinstance(res, dict) or "jobs" not in res or "count" not in res:
        raise RuntimeError(
            "Getro response shape changed — expected results.jobs and results.count, "
            f"got {list(res) if isinstance(res, dict) else type(res).__name__}"
        )
    return res["jobs"], res["count"]


class TechstarsGetro(Source):
    name = "techstars"

    def _validate(self) -> None:
        """Catch silent breakage before it looks like a quiet day of no postings.

        A renamed job_functions value would match nothing and emit zero leads
        without erroring, which is indistinguishable from a genuinely quiet run.
        """
        _, unfiltered = _results(_post({"hitsPerPage": 0, "page": 0}))
        _, filtered = _results(
            _post({"hitsPerPage": 0, "page": 0, "filters": {"job_functions": GTM_FUNCTIONS}})
        )
        if unfiltered and filtered == 0:
            raise RuntimeError(
                f"job_functions {GTM_FUNCTIONS} matched 0 of {unfiltered} jobs — "
                "Getro's vocabulary likely changed; re-derive it before trusting output."
            )
        if unfiltered and filtered == unfiltered:
            raise RuntimeError(
                "job_functions returned the unfiltered count — server-side filtering "
                "is no longer being applied."
            )

    def _walk(self) -> list[dict]:
        """Page through every GTM-function posting on the board."""
        jobs: list[dict] = []
        for page in range(MAX_PAGES):
            body = _post({
                "hitsPerPage": PAGE_SIZE,
                "page": page,
                "filters": {"job_functions": GTM_FUNCTIONS},
            })
            batch, total = _results(body)
            jobs.extend(batch)
            if len(batch) < PAGE_SIZE or len(jobs) >= total:
                break
            time.sleep(REQUEST_PAUSE)
        return jobs

    def _to_lead(self, item: dict) -> Lead | None:
        title = _clean_title(item.get("title") or "")
        org = item.get("organization") or {}
        company = " ".join((org.get("name") or "").split())
        if not title or not company:
            return None

        created = item.get("created_at")
        posted = (
            datetime.fromtimestamp(created, timezone.utc).isoformat()
            if isinstance(created, (int, float))
            else datetime.now(timezone.utc).isoformat()
        )

        slug = org.get("slug") or ""
        job_slug = item.get("slug") or str(item.get("id") or "")
        # Prefer the board's own permalink: the raw `url` is the company's ATS
        # link, which rots as soon as the role closes.
        board_url = (
            f"{BOARD}/companies/{slug}/jobs/{job_slug}"
            if slug and job_slug else item.get("url", "")
        )

        locations = item.get("locations") or item.get("searchable_locations") or []
        location = " / ".join(str(x) for x in locations[:2]) if locations else ""

        return Lead(
            key=f"techstars:{item.get('id')}",
            source=self.name,
            company=company,
            title=title,
            url=board_url,
            posted_at=posted,
            stage=org.get("stage"),
            headcount=HEADCOUNT_BUCKETS.get(org.get("head_count")),
            location=location,
            company_slug=slug,
            industry_tags=list(org.get("industry_tags") or [])[:4],
        )

    def fetch(self) -> list[Lead]:
        self._validate()
        raw = self._walk()

        leads: dict[str, Lead] = {}
        skipped_stage = 0
        for item in raw:
            stage = ((item.get("organization") or {}).get("stage") or "").lower()
            if stage not in EARLY_STAGE:
                skipped_stage += 1
                continue
            lead = self._to_lead(item)
            if lead and lead.key not in leads:
                leads[lead.key] = lead

        print(
            f"  {len(raw)} GTM postings walked, "
            f"{skipped_stage} dropped as later-stage, {len(leads)} early-stage leads "
            f"across {len({l.company_slug for l in leads.values()})} companies"
        )
        return list(leads.values())
