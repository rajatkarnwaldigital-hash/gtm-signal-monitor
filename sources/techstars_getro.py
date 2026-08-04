"""Techstars portfolio jobs, via the board's own Next.js data endpoint.

jobs.techstars.com is a Getro board (network 89, ~4,800 live jobs across ~2,900
companies). No scraping and no API key: the board server-renders its result set
into `__NEXT_DATA__`, and the same payload is served as JSON at
`/_next/data/<buildId>/jobs.json`, which honours the board's own `q` and
`filter` query params.

Two things about that endpoint shape the design here:

  * It returns only the first 20 results per query, and there is no pagination
    (`page`/`offset` are ignored; the paginated api.getro.com/v2 endpoint is
    401). So we cannot walk the whole board.
  * Results are sorted strictly newest-first.

Rather than fight that, we lean on it: we issue one narrow query per GTM role
archetype and take each slice's newest 20. Since we only care about roles posted
since the last run, 20-per-slice-per-day is ample headroom for narrow terms —
and it keeps the whole run to ~15 requests. `SATURATION_WARN` flags any slice
that came back full, which is the signal to split that term in two.

`q` is fuzzy (it ORs terms), so it is a recall net only. Precision comes from
GTM_TITLE in filters.py, applied to titles locally.
"""

from __future__ import annotations

import base64
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from .base import Lead, Source

BOARD = "https://jobs.techstars.com"
BUILD_ID_RE = re.compile(r'"buildId"\s*:\s*"([^"]+)"')

# Stage filter applied server-side. Series B+ companies have GTM leadership and
# usually an ops team already, which is the opposite of the buying signal.
EARLY_STAGE = ["pre_seed", "seed", "series_a", "series_unknown"]

# One narrow slice per GTM archetype. Narrow beats broad here: "head of sales"
# returns 21 total matches, so its newest-20 covers essentially all of it,
# whereas "sales" alone returns 1,547 and its newest-20 is mostly noise.
GTM_QUERIES = [
    "head of sales",
    "founding account executive",
    "account executive",
    "head of growth",
    "growth marketing",
    "demand generation",
    "revenue operations",
    "sales development",
    "business development",
    "gtm",
    "go to market",
    "marketing lead",
]

PAGE_SIZE = 20
SATURATION_WARN = 20
REQUEST_PAUSE = 0.3
TIMEOUT = 30
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
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


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode())


class TechstarsGetro(Source):
    name = "techstars"

    def __init__(self, queries: list[str] | None = None):
        self.queries = queries or GTM_QUERIES
        self._build_id: str | None = None

    def _build(self) -> str:
        """The buildId rotates whenever Getro redeploys, so resolve it per run."""
        if self._build_id:
            return self._build_id
        req = urllib.request.Request(f"{BOARD}/jobs", headers={"User-Agent": HEADERS["User-Agent"]})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            html = resp.read().decode()
        m = BUILD_ID_RE.search(html)
        if not m:
            raise RuntimeError(
                "Could not resolve the Techstars board buildId — the board's page "
                "structure changed and this source needs revisiting."
            )
        self._build_id = m.group(1)
        return self._build_id

    def _slice(self, term: str) -> tuple[list[dict], int]:
        filt = base64.b64encode(json.dumps({"stage": EARLY_STAGE}).encode()).decode()
        url = (
            f"{BOARD}/_next/data/{self._build()}/jobs.json"
            f"?q={urllib.parse.quote(term)}&filter={urllib.parse.quote(filt)}"
        )
        data = _get(url)
        jobs = data["pageProps"]["initialState"]["jobs"]
        return jobs.get("found", []), jobs.get("total", 0)

    def _to_lead(self, item: dict) -> Lead | None:
        title = (item.get("title") or "").strip()
        org = item.get("organization") or {}
        company = (org.get("name") or "").strip()
        if not title or not company:
            return None

        created = item.get("createdAt")
        posted = (
            datetime.fromtimestamp(created, timezone.utc).isoformat()
            if isinstance(created, (int, float))
            else datetime.now(timezone.utc).isoformat()
        )

        slug = org.get("slug") or ""
        job_slug = item.get("slug") or str(item.get("id") or "")
        # Prefer the board's own permalink: the raw `url` is the company's ATS
        # link, which rots as soon as the role closes.
        board_url = f"{BOARD}/companies/{slug}/jobs/{job_slug}" if slug and job_slug else item.get("url", "")

        locations = item.get("locations") or []
        location = " / ".join(str(x) for x in locations[:2]) if locations else ""

        return Lead(
            key=f"techstars:{item.get('id')}",
            source=self.name,
            company=company,
            title=title,
            url=board_url,
            posted_at=posted,
            stage=org.get("stage"),
            headcount=HEADCOUNT_BUCKETS.get(org.get("headCount")),
            location=location,
            company_slug=slug,
            industry_tags=list(org.get("industryTags") or [])[:4],
        )

    def fetch(self) -> list[Lead]:
        leads: dict[str, Lead] = {}
        for term in self.queries:
            try:
                found, total = self._slice(term)
            except Exception as e:
                print(f"  ! slice '{term}' failed: {e}")
                continue

            new_here = 0
            for item in found:
                lead = self._to_lead(item)
                if lead and lead.key not in leads:
                    leads[lead.key] = lead
                    new_here += 1

            note = ""
            if len(found) >= SATURATION_WARN:
                note = f"  [saturated — {total} total match; consider splitting this term]"
            print(f"  {term:<28} {len(found):>2} returned, {new_here:>2} new{note}")
            time.sleep(REQUEST_PAUSE)

        print(f"  {len(leads)} unique postings across {len(self.queries)} slices")
        return list(leads.values())
