"""Source plugin contract.

A source's only job is to return raw Lead candidates. Qualification, scoring,
enrichment and delivery all happen downstream, so adding a source never means
touching the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict


@dataclass
class Lead:
    """One job posting that might be a buying signal.

    `key` is the identity used for the seen-state diff. It must be stable
    across runs even if the company edits the role title later.
    """

    key: str
    source: str
    company: str
    title: str
    url: str
    posted_at: str  # ISO 8601

    # Qualification signals — whatever the source can supply. Missing is fine.
    stage: str | None = None
    headcount: str | None = None
    location: str | None = None
    company_slug: str | None = None
    industry_tags: list[str] = field(default_factory=list)

    # Filled in downstream by companies.py / contacts.py / score.py / brief.py
    website: str = ""
    domain: str = ""
    description: str = ""
    program: str = ""
    cohort_year: int | None = None

    # From contacts.py (Exa): who to talk to, and a second opinion on the
    # company facts the score depends on.
    contacts: list[dict] = field(default_factory=list)
    evidence: str = ""
    verified_headcount: str = ""
    headcount_growth: str = ""
    total_funding: str = ""
    funding_rounds: int | None = None
    verification_note: str = ""

    score: int = 0
    reasons: list[str] = field(default_factory=list)
    hook: str = ""
    opener: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class Source:
    """Subclass, set `name`, implement `fetch()`, register in sources/__init__.py."""

    name = "base"

    def fetch(self) -> list[Lead]:
        raise NotImplementedError
