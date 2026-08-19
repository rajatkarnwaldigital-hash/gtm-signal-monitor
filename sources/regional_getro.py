"""GTM buying signals from NZ, UAE and wider ANZ/MENA portfolio boards.

Same signal as the Techstars source — an early-stage company posting a GTM role —
in two ecosystems Techstars and YC barely cover. All seven boards are Getro, so
this is one client against seven network ids.

Why these regions carry signal disproportionately: a Dubai or Auckland startup
standing up its first revenue motion is usually doing it without an in-house GTM
engineer, and without the dense agency market a US company would draw on.

Boards overlap (Halter appears on both Icehouse and Blackbird), so leads are
deduped on Getro's job id across the whole set — roughly a third of fetched rows.

`searchable_locations` is the only location filter Getro honours; the sibling
keys are accepted and ignored. See sources/_getro.py.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from . import _getro
from .base import Lead, Source

# network id, display name, backer description, board URL.
# Verified 2026-08-19 by reading __NEXT_DATA__ on each board.
NETWORKS = [
    (9266, "Hub71",             "Abu Dhabi government accelerator", "https://jobs.hub71.com"),
    (943,  "Icehouse Ventures", "NZ VC",                            "https://jobs.icehouseventures.co.nz"),
    (219,  "Blackbird",         "ANZ VC",                           "https://jobs.blackbird.vc"),
    (1034, "MEVP",              "MENA VC",                          "https://jobs.mevp.com"),
    (7418, "Airtree",           "ANZ VC",                           "https://jobs.airtree.vc"),
    (7715, "Antler",            "Global early-stage VC",            "https://jobs.antler.co"),
    (1676, "GD1",               "NZ VC",                            "https://careers.gd1.vc"),
]

REGIONS = ["New Zealand", "United Arab Emirates"]

GTM_FUNCTIONS = [
    "Sales & Business Development",
    "Marketing & Communications",
    "Operations",  # Getro files RevOps and partnerships here; filters.py does precision
]

# "other" is included here but not in the Techstars source. A large share of UAE
# companies on these boards carry stage "other" rather than a named round —
# excluding it drops most of the UAE pool, including genuinely early companies
# like Qashio and Pemo. The stage backstop in filters.py still applies.
EARLY_STAGE = {"pre_seed", "seed", "series_a", "series_unknown", "other"}

# Orgs that resell third-party listings rather than hiring themselves. Qureos
# alone accounted for 21 of Hub71's 38 UAE GTM results.
ORG_BLOCKLIST = {"qureos", "qureos-2"}


class RegionalGetro(Source):
    name = "regional"

    def __init__(self, networks=None, regions=None):
        self.networks = networks or NETWORKS
        self.regions = regions or REGIONS

    def _to_lead(self, item: dict, backer: str, backer_type: str) -> Lead | None:
        title = _getro.clean_title(item.get("title"))
        org = item.get("organization") or {}
        company = " ".join((org.get("name") or "").split())
        if not title or not company:
            return None

        created = item.get("created_at")
        posted = (datetime.fromtimestamp(created, timezone.utc).isoformat()
                  if isinstance(created, (int, float))
                  else datetime.now(timezone.utc).isoformat())

        locations = item.get("locations") or item.get("searchable_locations") or []
        return Lead(
            key=f"getro:{item.get('id')}",
            source=self.name,
            company=company,
            title=title,
            url=item.get("url", ""),
            posted_at=posted,
            stage=org.get("stage"),
            headcount=_getro.HEADCOUNT_BUCKETS.get(org.get("head_count")),
            location=" / ".join(str(x) for x in dict.fromkeys(locations))[:120],
            company_slug=org.get("slug") or "",
            industry_tags=list(org.get("industry_tags") or [])[:4],
            program=backer,
            cohort_year=None,
        )

    def fetch(self) -> list[Lead]:
        leads: dict[str, Lead] = {}
        dropped = {"stage": 0, "blocked": 0, "dupe": 0}

        for net, backer, backer_type, board_url in self.networks:
            try:
                _getro.validate(net, board_url, GTM_FUNCTIONS)
            except Exception as e:  # noqa: BLE001 - one bad board must not kill the run
                print(f"  ! {backer}: {e}")
                continue

            found = 0
            for region in self.regions:
                try:
                    raw = _getro.walk(net, board_url, {
                        "job_functions": GTM_FUNCTIONS,
                        "searchable_locations": [region],
                    })
                except Exception as e:  # noqa: BLE001
                    print(f"  ! {backer} / {region}: {e}")
                    continue

                for item in raw:
                    org = item.get("organization") or {}
                    if (org.get("slug") or "").lower() in ORG_BLOCKLIST:
                        dropped["blocked"] += 1
                        continue
                    if (org.get("stage") or "").lower() not in EARLY_STAGE:
                        dropped["stage"] += 1
                        continue
                    lead = self._to_lead(item, backer, backer_type)
                    if not lead:
                        continue
                    if lead.key in leads:
                        dropped["dupe"] += 1
                        continue
                    leads[lead.key] = lead
                    found += 1
                time.sleep(_getro.REQUEST_PAUSE)
            print(f"  {backer:<18} {found:>3} early-stage GTM leads")

        # Enrich only what survived — roughly 30 lookups, not the whole portfolio.
        board_by_backer = {n[1]: (n[0], n[3]) for n in self.networks}
        seen_orgs: dict[tuple, dict] = {}
        for lead in leads.values():
            net, board_url = board_by_backer[lead.program]
            ck = (net, lead.company_slug)
            if lead.company_slug and ck not in seen_orgs:
                seen_orgs[ck] = _getro.org(net, lead.company_slug, board_url)
                time.sleep(0.15)
            info = seen_orgs.get(ck, {})
            lead.description = info.get("description", "") or ""
            lead.domain = info.get("domain", "") or ""
            lead.website = info.get("website_url", "") or ""

        print(f"  {len(leads)} unique leads across {len(seen_orgs)} companies "
              f"(dropped: {dropped['stage']} later-stage, {dropped['blocked']} reseller, "
              f"{dropped['dupe']} cross-board dupes)")
        return list(leads.values())
