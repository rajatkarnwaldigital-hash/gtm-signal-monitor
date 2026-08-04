"""Company enrichment from the open Techstars dataset.

github.com/yigitmeteozcan/startups publishes accelerator portfolios as static
JSON on jsDelivr, rebuilt daily by a GitHub Action, no key required. We pull the
Techstars slice (~5,100 companies) and join it to board postings by normalised
company name — measured at a 99% hit rate on a sample of GTM postings.

The join is what makes a lead actionable: the job board gives stage and
headcount but no website and no description. This gives us the domain (so the
lead is contactable) and the cohort year (a ranking signal — a 2024 cohort
company is a far better fit than a 2013 one still on the board).

Deliberately Techstars-only. Antler / EF / Z Fellows / EWOR / Thiel companies
are pre-product and cannot buy a build engagement; Plug and Play and Google for
Startups admit at a volume that carries no selection signal.
"""

from __future__ import annotations

import json
import re
import urllib.request
from urllib.parse import urlparse

DATASET = "https://cdn.jsdelivr.net/gh/yigitmeteozcan/startups@main/data/by-source/techstars.json"
TIMEOUT = 60

_norm_re = re.compile(r"[^a-z0-9]")
# Suffixes that differ between the board and the dataset for the same company.
_suffix_re = re.compile(r"(inc|llc|ltd|limited|corp|corporation|co|gmbh|bv|ai|io|hq)$")


def normalise(name: str) -> str:
    n = _norm_re.sub("", (name or "").lower())
    stripped = _suffix_re.sub("", n)
    return stripped or n


def domain_of(website: str) -> str:
    if not website:
        return ""
    parsed = urlparse(website if "//" in website else f"https://{website}")
    return (parsed.hostname or "").removeprefix("www.")


class CompanyIndex:
    def __init__(self, rows: list[dict]):
        self._by_name: dict[str, dict] = {}
        for row in rows:
            # First writer wins: the dataset is ordered with the better-known
            # company first, which is the one a name collision should resolve to.
            self._by_name.setdefault(normalise(row.get("name", "")), row)

    @classmethod
    def load(cls) -> "CompanyIndex":
        print("[*] Loading Techstars company dataset …")
        req = urllib.request.Request(DATASET, headers={"User-Agent": "gtm-signal-monitor"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            rows = json.loads(resp.read().decode())
        print(f"    {len(rows)} Techstars companies indexed")
        return cls(rows)

    def enrich(self, lead) -> bool:
        """Attach website/description/program/cohort year. True if matched."""
        row = self._by_name.get(normalise(lead.company))
        if not row:
            return False
        lead.website = row.get("website") or ""
        lead.domain = domain_of(lead.website)
        # Dataset descriptions carry embedded newlines, which break the digest's
        # indented single-line layout.
        lead.description = " ".join((row.get("description") or "").split())
        lead.program = row.get("program") or ""
        year = row.get("year")
        lead.cohort_year = int(year) if isinstance(year, int) else None
        return True
