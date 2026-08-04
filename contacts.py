"""Contact discovery and company verification via Exa.

Two jobs, one API call per lead:

  1. **Contact.** Turn a company into named people with LinkedIn URLs, so the
     digest says "Helena Most, Co-Founder & CEO" instead of "your new sales
     lead". Both a founder/exec and a GTM/BD person are kept when both exist —
     at a 10-20 person company the BD lead is often the better first touch.

  2. **Verification.** Getro's `stage` and `headCount` are softer than they
     look. On the first live digest, heva ranked #3 on "pre-seed, 1-10
     employees" while actually being ~25 people with $6M raised across two
     rounds. Exa's company blurb carries headcount, YoY growth and total
     funding, so we re-check the numbers the score is built on and record a
     conflict when they disagree.

Disambiguation matters here. A search for "Frictionless Technologies" also
returns Frictionless Capital (an unrelated VC firm) and DataWhisper, both of
which merely share a word. A result is only accepted if its text ties back to
this specific company — via the company's LinkedIn slug, its domain, or its
name appearing as an employer.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request

EXA_ENDPOINT = "https://api.exa.ai/search"
EXA_API_KEY = os.environ.get("EXA_API_KEY", "")
TIMEOUT = 45
NUM_RESULTS = 6

# "Resourcly has 10-20 employees (+60% YoY)" / "100,000-200,000 employees"
_HEADCOUNT_RE = re.compile(r"has\s+([\d,]+(?:-[\d,]+)?\+?)\s+employees", re.I)
_GROWTH_RE = re.compile(r"\(([+-]?\d+(?:-\d+)?)%\s*YoY", re.I)
_DECLINE_RE = re.compile(r"\(([\d-]+)%\s*YoY\s*decline", re.I)
_FUNDING_RE = re.compile(r"has\s+\$([\d.]+[KMB]?)\s+in total funding", re.I)
_ROUNDS_RE = re.compile(r"with\s+(\d+)\s+prior funding rounds?", re.I)

# Titles worth surfacing, in priority order within their group.
_LEADER_RE = re.compile(r"\b(co-?founder|founder|ceo|chief executive|coo|president)\b", re.I)
_GTM_RE = re.compile(
    r"\b(head of (bd|business development|sales|growth|revenue|marketing)"
    r"|business development|revenue|sales lead|growth lead|cro"
    r"|chief revenue|vp of (sales|growth|marketing|revenue)"
    r"|founders? associate.*(business development|bd)"
    r"|commercial (director|lead))\b",
    re.I,
)
# A role line looks like "### Co-Founder & CEO - [heva](...)" in Exa's output.
_ROLE_LINE_RE = re.compile(r"#+\s*([^\n\[\]]{3,80}?)\s*[-–]\s*\[", re.M)


def _post(body: dict) -> dict:
    req = urllib.request.Request(
        EXA_ENDPOINT,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "x-api-key": EXA_API_KEY},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode())


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


_MULTIPLIER = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}


def _funding_is_plausible(amount: str, rounds: int | None) -> bool:
    """Suppress obviously-wrong totals rather than print them.

    A live digest showed "hexafarms · $11K raised / 4 rounds" for a 20-30
    person company. Whether the source or the parse was at fault, a figure
    that self-evidently can't be true costs more credibility in the inbox than
    an omitted field does.
    """
    try:
        value = float(amount.rstrip("KMB")) * _MULTIPLIER.get(amount[-1].upper(), 1)
    except (ValueError, IndexError):
        return False
    if rounds and rounds >= 2 and value < 250_000:
        return False
    return value >= 10_000


def _blob(result: dict) -> str:
    parts = [result.get("text") or "", result.get("summary") or ""]
    parts.extend(result.get("highlights") or [])
    return "\n".join(p for p in parts if p)


class ExaEnricher:
    """Set lead.contacts / lead.evidence / lead.verified_* in place.

    Every failure is non-fatal. A lead with no contact is still a lead; it just
    reverts to the pre-enrichment digest entry.
    """

    enabled = bool(EXA_API_KEY)

    def _search(self, query: str) -> list[dict]:
        body = {
            "query": query,
            "numResults": NUM_RESULTS,
            "category": "people",
            "contents": {"highlights": True, "text": {"maxCharacters": 3000}},
        }
        return _post(body).get("results", [])

    def _queries(self, lead) -> list[str]:
        """Primary query by registered name; fallback by brand.

        Registered names and trading names diverge often enough to matter:
        "Frictionless Technologies Ltd" trades as FAM at joinfam.com, and
        searching the legal name returns Frictionless Capital instead. The
        brand in the domain is what LinkedIn profiles actually say.
        """
        role = "founder, CEO or head of sales/business development at"
        desc = f", {lead.description[:120]}" if lead.description else ""
        primary = f"category:people {role} {lead.company}"
        if lead.domain:
            primary += f" ({lead.domain})"
        queries = [primary + desc]

        brand = lead.domain.split(".")[0] if lead.domain else ""
        if brand and _slug(brand) not in _slug(lead.company):
            queries.append(f"category:people {role} {brand} ({lead.domain}){desc}")
        return queries

    def _belongs(self, result: dict, lead) -> bool:
        """Guard against same-word companies (Frictionless Technologies vs
        Frictionless Capital). Require a concrete tie to THIS company."""
        blob = _blob(result)
        packed = _slug(blob)
        needles = [_slug(lead.company)]
        if lead.domain:
            root = lead.domain.split(".")[0]
            needles.append(_slug(root))
            needles.append(_slug(lead.domain))
        # linkedin.com/company/<slug> is the strongest tie available.
        company_slugs = {_slug(s) for s in re.findall(r"linkedin\.com/company/([\w-]+)", blob, re.I)}
        if any(n and n in company_slugs for n in needles):
            return True
        return any(n and len(n) > 3 and n in packed for n in needles)

    def _role_of(self, result: dict, lead) -> str:
        """Pull the role line that belongs to this company, not a past employer."""
        blob = _blob(result)
        for line in _ROLE_LINE_RE.findall(blob):
            role = " ".join(line.split()).strip(" -–|")
            if role and len(role) < 80:
                return role
        head = (result.get("highlights") or [""])[0]
        return " ".join(head.split())[:80]

    def _verify(self, blob: str, lead) -> None:
        m = _HEADCOUNT_RE.search(blob)
        if m:
            lead.verified_headcount = m.group(1).replace(",", "")
        decline = _DECLINE_RE.search(blob)
        m = decline or _GROWTH_RE.search(blob)
        if m:
            # Exa writes ranges ("9-10% YoY decline", "+3-4% YoY"). Keep the
            # outer bound and one sign, so this never renders as "-9-10%".
            raw = m.group(1).lstrip("+-")
            bound = raw.split("-")[-1]
            lead.headcount_growth = f"-{bound}%" if decline else f"+{bound}%"
        m = _ROUNDS_RE.search(blob)
        if m:
            lead.funding_rounds = int(m.group(1))
        m = _FUNDING_RE.search(blob)
        if m and _funding_is_plausible(m.group(1), lead.funding_rounds):
            lead.total_funding = f"${m.group(1)}"

        # Flag the case that caused the mis-rank: board says tiny, reality isn't.
        claimed = (lead.headcount or "").split("-")[-1]
        actual = (lead.verified_headcount or "").split("-")[-1].rstrip("+")
        if claimed.isdigit() and actual.isdigit() and int(actual) > int(claimed) * 2:
            lead.verification_note = (
                f"board said {lead.headcount}, Exa says {lead.verified_headcount} employees"
            )

    def enrich(self, lead) -> bool:
        if not self.enabled:
            return False

        results: list[dict] = []
        for query in self._queries(lead):
            try:
                results = self._search(query)
            except Exception as e:
                print(f"    ! Exa lookup failed for {lead.company}: {e}")
                return False
            # Only pay for the brand fallback when the legal name found nobody.
            if any(self._belongs(r, lead) for r in results):
                break

        leader, gtm, evidence = None, None, ""
        for result in results:
            if not self._belongs(result, lead):
                continue
            blob = _blob(result)
            if not evidence:
                evidence = blob
                self._verify(blob, lead)

            name = " ".join((result.get("title") or "").split())
            url = result.get("url") or ""
            if not name or "linkedin.com/in/" not in url.lower():
                continue
            role = self._role_of(result, lead)
            person = {"name": name, "role": role, "linkedin": url}

            if gtm is None and _GTM_RE.search(role):
                gtm = person
            elif leader is None and _LEADER_RE.search(role):
                leader = person
            if leader and gtm:
                break

        lead.contacts = [p for p in (leader, gtm) if p]
        lead.evidence = evidence[:1500]
        return bool(lead.contacts)
