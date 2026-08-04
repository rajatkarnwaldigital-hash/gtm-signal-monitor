"""Precision layer.

The board's `q` search is fuzzy — it ORs the query terms, so "sales development"
also returns "Software Development Engineer". Every title therefore gets checked
against GTM_TITLE locally before it can become a lead.
"""

from __future__ import annotations

import re

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


def qualifies(lead) -> bool:
    return is_gtm_role(lead.title) and (lead.stage in ALLOWED_STAGES)
