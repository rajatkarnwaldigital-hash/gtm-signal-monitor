#!/usr/bin/env python3
"""GTM signal monitor.

Pulls new GTM/sales postings from pluggable sources, keeps only early-stage
companies with a genuine first-GTM-hire signal, ranks them, and emails a short
ranked digest with a carry-forward reason per lead.

Design constraint that drives everything: this must produce FEWER leads than the
YC monitor, not more. DAILY_CAP is the lever.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import brief
import filters
import state
from companies import CompanyIndex
from score import rank
from sources import SOURCES

# Hard ceiling on leads presented as actionable per day. The failure mode being
# designed against is eight comparative judgments a day and therefore zero
# actions taken, so this is intentionally small.
DAILY_CAP = int(os.environ.get("DAILY_CAP", "5"))

# Below this, a lead goes in the "no action expected" tail rather than the
# ranked list. Tune against the first week of real output.
SCORE_FLOOR = int(os.environ.get("SCORE_FLOOR", "8"))


def main() -> None:
    print("=" * 68)
    print("GTM SIGNAL MONITOR")
    print(f"Run: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 68)

    first_run = not state.SEEN_PATH.exists() or not state.load_seen()
    seen = state.load_seen()

    print("\n[1] Fetching sources")
    raw = []
    for source in SOURCES:
        print(f"  -- {source.name}")
        try:
            raw.extend(source.fetch())
        except Exception as e:
            print(f"  ! source '{source.name}' failed entirely: {e}")
    if not raw:
        print("\nNo postings returned by any source. Exiting without touching state.")
        sys.exit(1)

    print(f"\n[2] Filtering {len(raw)} postings for real GTM roles")
    qualified = [l for l in raw if filters.qualifies(l)]
    print(f"    {len(qualified)} qualified ({len(raw) - len(qualified)} dropped)")

    new = [l for l in qualified if l.key not in seen]
    print(f"    {len(new)} not seen before")

    if first_run:
        state.mark_seen(seen, qualified)
        state.save_seen(seen)
        print(f"\n[baseline] Seeded {len(seen)} postings. No email on the first run.")
        print("Tomorrow's run will report only genuinely new postings.")
        return

    if not new:
        print("\nNothing new today. Done.")
        return

    print("\n[3] Enriching against the Techstars company dataset")
    index = CompanyIndex.load()
    matched = sum(index.enrich(l) for l in new)
    print(f"    {matched}/{len(new)} joined to a company record")

    print("\n[4] Ranking")
    ranked = rank(new)
    top = [l for l in ranked if l.score >= SCORE_FLOOR][:DAILY_CAP]
    top_keys = {l.key for l in top}
    rest = [l for l in ranked if l.key not in top_keys]
    print(f"    {len(top)} above the bar (cap {DAILY_CAP}), {len(rest)} below")

    if not top:
        print("\nNothing cleared the score floor today — no digest sent.")
        print("New postings are still recorded so they won't resurface.")
        state.mark_seen(seen, new)
        state.save_seen(seen)
        return

    print("\n[5] Writing context")
    brief.add_context(top)

    print("\n[6] Delivering")
    if not brief.send(top, rest):
        print("\nEmail did not send, so nothing was marked as seen.")
        print("These leads will be retried on the next run rather than lost.")
        return

    state.append_ledger(top)
    state.mark_seen(seen, new)
    state.save_seen(seen)
    print(f"\nDone. {len(seen)} postings tracked; {len(top)} added to ledger.md.")


if __name__ == "__main__":
    main()
