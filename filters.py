"""Precision layer.

The board's `q` search is fuzzy — it ORs the query terms, so "sales development"
also returns "Software Development Engineer". Every title therefore gets checked
against GTM_TITLE locally before it can become a lead.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone

# A posting only carries the buying signal while it is actually open. The board
# returns the newest 20 *per query*, which is not the same as recent: narrow
# terms like "founding account executive" match only three jobs board-wide, so
# their "newest 20" includes everything ever posted. Without this cutoff the
# digest ranked a 475-day-old founding AE role at #2 and a 358-day-old Head of
# Sales at #4 — both long dead, both scored as live signal.
MAX_AGE_DAYS = int(os.environ.get("MAX_AGE_DAYS", "30"))

# Roles that indicate a company is standing up revenue motion.
GTM_TITLE = re.compile(
    r"""
    \b(
        (head|vp|director|lead|manager|chief)\s+of\s+(sales|growth|revenue|marketing|gtm|business\s+development)
      | (founding|first)\s+(ae|account\s+executive|sales|salesperson|marketer|gtm|growth)
      | account\s+executive
      | sales\s+(development\s+representative|lead|manager|director)
      | (sdr|bdr|ae)\b
      | business\s+development\s+(rep|representative|manager|lead|director)
      | (growth|performance|demand\s+gen\w*|lifecycle|product)\s+marketing
      | demand\s+generation
      | revenue\s+operations
      | (revops|marketops|salesops)
      | go[\s-]?to[\s-]?market
      | \bgtm\b
      | head\s+of\s+partnerships
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Titles that match GTM_TITLE by accident or describe a role with no budget
# authority and no infrastructure gap worth selling into.
DISQUALIFY = re.compile(
    r"""
    \b(
        intern(ship)?
      | co[\s-]?op\b
      | (working\s+)?student
      | apprentice
      | volunteer
      | engineer | developer | scientist | designer   # "sales engineer", "growth engineer"
      | recruiter | talent | people\s+ops
      | customer\s+(support|success)\s+(agent|rep)
      | account\s+manager                              # retention, not new revenue
      | retail | cashier | store
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Stages where the buying signal doesn't hold: too early to have budget, or late
# enough to already own GTM infrastructure. Kept as a backstop — the source
# already filters server-side.
ALLOWED_STAGES = {"pre_seed", "seed", "series_a", "series_unknown", None, ""}


def is_gtm_role(title: str) -> bool:
    return bool(GTM_TITLE.search(title)) and not DISQUALIFY.search(title)


def age_days(lead) -> float | None:
    """None when the posting carries no usable date — treated as too old to
    trust rather than assumed fresh."""
    try:
        posted = datetime.fromisoformat(lead.posted_at)
    except (TypeError, ValueError):
        return None
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - posted).total_seconds() / 86400


def is_current(lead) -> bool:
    age = age_days(lead)
    return age is not None and age <= MAX_AGE_DAYS


def qualifies(lead) -> bool:
    return is_gtm_role(lead.title) and lead.stage in ALLOWED_STAGES and is_current(lead)
