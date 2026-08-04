"""Committed state, same approach as yc-gtm-monitor-actions.

seen.json is the diff baseline; the workflow commits it back after every run, so
state survives without a database. ledger.md is the durable "why did I contact
this person" record — see monitor.py.

seen.json ships empty on purpose. The first run establishes a clean baseline
against this repo's own sources and sends nothing.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

SEEN_PATH = Path("seen.json")
LEDGER_PATH = Path("ledger.md")


def load_seen() -> dict:
    if not SEEN_PATH.exists():
        return {}
    try:
        return json.loads(SEEN_PATH.read_text() or "{}")
    except json.JSONDecodeError as e:
        # Never silently reset: an unreadable state file would re-flag every
        # posting on the board as new and blast a digest of hundreds.
        raise SystemExit(f"seen.json is corrupt ({e}) — fix or delete it deliberately.")


def save_seen(seen: dict) -> None:
    SEEN_PATH.write_text(json.dumps(seen, indent=2, sort_keys=True) + "\n")


def mark_seen(seen: dict, leads: list) -> None:
    now = datetime.now(timezone.utc).isoformat()
    for lead in leads:
        seen[lead.key] = {"company": lead.company, "title": lead.title, "first_seen": now}


def append_ledger(leads: list) -> None:
    """Append one durable card per lead sent.

    This exists for the moment a connection request is accepted a week later and
    the context is gone. Grep the company name here and the whole reason —
    role, stage, score, and the exact opener sent — comes back.
    """
    if not leads:
        return
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [f"\n## {today}\n"]
    for lead in leads:
        lines.append(f"### {lead.company} — {lead.title}")
        lines.append(f"- **Score** {lead.score} · {' · '.join(lead.reasons)}")
        lines.append(f"- **Site** {lead.website or 'unknown'} · **Posting** {lead.url}")
        if lead.description:
            lines.append(f"- **What they do** {lead.description}")
        if lead.hook:
            lines.append(f"- **Why now** {lead.hook}")
        if lead.opener:
            lines.append(f"- **Opener sent** {lead.opener}")
        lines.append("")
    with LEDGER_PATH.open("a") as fh:
        fh.write("\n".join(lines))
