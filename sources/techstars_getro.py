"""Techstars portfolio jobs, via Getro's paginated v2 search API.

jobs.techstars.com is a Getro board (network 89, ~4,700 live jobs across ~2,900
companies). No scraping and no API key.

This module previously read the board's `/_next/data/<buildId>/jobs.json`
endpoint, because api.getro.com/v2 appeared to return 401. It does not: it
returns **406 unless you send `Accept: application/json`**. With that header the
paginated endpoint works fine, which removes the constraint the old design was
built around — we no longer need one narrow query per GTM archetype, and there
is no newest-20 ceiling to work under. We now walk the full result set.

The transport, pagination and the Getro filter caveats now live in
sources/_getro.py, which this shares with the regional source. The one that
matters most here: **the `stage` filter is silently ignored**, so stage is
applied locally off `organization.stage`. Do not re-add it to the payload.

Net effect vs. the old slice approach: ~473 early-stage GTM postings across
~213 companies in ~41 requests, instead of at most 20 per archetype with heavy
overlap between slices.

`job_functions` uses an exact vocabulary — "Sales" matches nothing,
"Sales & Business Development" is the real value — so the constants below are
pinned and checked at runtime by `_validate()`.
"""

from __future__ import annotations

from datetime import datetime, timezone

from . import _getro
from .base import Lead, Source

BOARD = "https://jobs.techstars.com"
NETWORK = 89

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

class TechstarsGetro(Source):
    name = "techstars"

    def _to_lead(self, item: dict) -> Lead | None:
        title = _getro.clean_title(item.get("title"))
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
            headcount=_getro.HEADCOUNT_BUCKETS.get(org.get("head_count")),
            location=location,
            company_slug=slug,
            industry_tags=list(org.get("industry_tags") or [])[:4],
        )

    def fetch(self) -> list[Lead]:
        _getro.validate(NETWORK, BOARD, GTM_FUNCTIONS)
        raw = _getro.walk(NETWORK, BOARD, {"job_functions": GTM_FUNCTIONS})

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
