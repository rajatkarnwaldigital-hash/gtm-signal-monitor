"""Ranking.

The point of this repo is to remove comparative judgment from the daily loop.
Eight roughly-equal leads a day is a decision problem; three ranked ones with a
stated reason is a to-do list. So every lead gets a score and — more importantly
— a `reasons` list, because the reason is what has to survive until a LinkedIn
connection is accepted a week later.

Weights are deliberately blunt. This is a throwaway experiment; the ordering
matters, the absolute numbers don't.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

# Budget exists, GTM infrastructure almost certainly doesn't.
STAGE_POINTS = {"seed": 5, "pre_seed": 3, "series_a": 4, "series_unknown": 1}

# Small team + a GTM hire = the hire will be building from nothing.
HEADCOUNT_POINTS = {"1-10": 5, "11-50": 4, "51-200": 1}

# A first/founding/head-of hire is the strongest form of the signal: there is no
# GTM function yet, and this person will be handed the job of inventing one.
SENIORITY = [
    (re.compile(r"\b(founding|first)\b", re.I), 6, "founding hire — no GTM function exists yet"),
    (re.compile(r"\b(head|vp|chief|director)\s+of\b", re.I), 5, "GTM leadership hire — owns the budget"),
    (re.compile(r"\b(lead|manager)\b", re.I), 2, "mid-level GTM hire"),
]

RECENT_COHORT_YEARS = 3
FRESH_DAYS = 3


def _bucket(headcount: str) -> str:
    """Map a verified range like '20-30' onto the board's coarser buckets so
    both sources score on the same scale."""
    try:
        upper = int(headcount.split("-")[-1].rstrip("+"))
    except (ValueError, AttributeError):
        return headcount
    for edge, label in ((10, "1-10"), (50, "11-50"), (200, "51-200")):
        if upper <= edge:
            return label
    return "201+"


def _age_days(posted_at: str) -> float | None:
    try:
        posted = datetime.fromisoformat(posted_at)
    except (ValueError, TypeError):
        return None
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - posted).total_seconds() / 86400


def score_lead(lead, gtm_roles_at_company: int = 1) -> None:
    """Set lead.score and lead.reasons in place."""
    points = 0
    reasons: list[str] = []

    if lead.stage in STAGE_POINTS:
        points += STAGE_POINTS[lead.stage]
        reasons.append(f"{lead.stage.replace('_', ' ')} stage")

    # Prefer Exa's headcount over the board's when they disagree — the board's
    # field is stale often enough to have mis-ranked a ~25-person company as
    # a 1-10 person one on the first live digest.
    headcount = _bucket(lead.verified_headcount) if lead.verified_headcount else lead.headcount
    if headcount in HEADCOUNT_POINTS:
        points += HEADCOUNT_POINTS[headcount]
        reasons.append(f"{headcount} employees")

    if lead.verification_note:
        points -= 6
        reasons.append(f"stage data unreliable — {lead.verification_note}")

    if lead.headcount_growth and lead.headcount_growth.startswith("-"):
        points -= 2
        reasons.append(f"headcount shrinking ({lead.headcount_growth} YoY)")

    for pattern, pts, why in SENIORITY:
        if pattern.search(lead.title):
            points += pts
            reasons.append(why)
            break

    # A single GTM opening reads as "starting a function". Five at once reads as
    # "scaling one that already exists, with people who own the tooling".
    if gtm_roles_at_company == 1:
        points += 3
        reasons.append("only GTM role open — likely their first")
    elif gtm_roles_at_company >= 4:
        points -= 3
        reasons.append(f"{gtm_roles_at_company} GTM roles open — team already exists")

    if lead.cohort_year:
        age = datetime.now(timezone.utc).year - lead.cohort_year
        if age <= RECENT_COHORT_YEARS:
            points += 3
            reasons.append(f"Techstars {lead.cohort_year} — recent cohort")
        elif age >= 8:
            points -= 2
            reasons.append(f"Techstars {lead.cohort_year} — old cohort")

    age_days = _age_days(lead.posted_at)
    if age_days is not None and age_days <= FRESH_DAYS:
        points += 2
        reasons.append(f"posted {int(age_days)}d ago")

    # Unreachable is unactionable, whatever else is true about the company.
    if not lead.domain:
        points -= 4
        reasons.append("no domain resolved")

    lead.score = points
    lead.reasons = reasons


def rank(leads: list) -> list:
    counts: dict[str, int] = {}
    for lead in leads:
        counts[lead.company] = counts.get(lead.company, 0) + 1
    for lead in leads:
        score_lead(lead, counts[lead.company])
    return sorted(leads, key=lambda l: (-l.score, l.company))
