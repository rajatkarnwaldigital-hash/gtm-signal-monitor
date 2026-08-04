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
from contacts import ExaEnricher
from score import rank
from sources import SOURCES

# Hard ceiling on leads presented as actionable per day. The failure mode being
# designed against is eight comparative judgments a day and therefore zero
# actions taken, so this is intentionally small.
DAILY_CAP = int(os.environ.get("DAILY_CAP", "5"))

# Below this, a lead goes in the "no action expected" tail rather than the
# ranked list. Tune against the first week of real output.
SCORE_FLOOR = int(os.environ.get("SCORE_FLOOR", "8"))

# The tail is a peripheral-vision aid, not a second list to work. Left uncapped
# it grew to 50 lines and buried the 5 leads that mattered — which is the exact
# failure this repo exists to prevent, so it gets its own hard limits.
TAIL_CAP = int(os.environ.get("TAIL_CAP", "8"))
TAIL_FLOOR = int(os.environ.get("TAIL_FLOOR", "5"))

# How many top-ranked leads get an Exa call. Bounds spend: only the shortlist
# is verified, not every qualified posting.
SHORTLIST = int(os.environ.get("SHORTLIST", "10"))


def dedupe(leads: list) -> list:
    """Collapse the same role posted twice under different job IDs.

    Companies re-post (ivee's BDR role, OneInbox's GTM Lead), and the board
    issues a fresh id each time, so the seen-diff can't catch it. Keep the
    first — the source returns newest-first.
    """
    out, seen_pairs = [], set()
    for lead in leads:
        pair = (lead.company.lower(), " ".join(lead.title.lower().split()))
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        out.append(lead)
    return out


def main() -> None:
    # --send-now exists to prove the delivery path end to end without waiting a
    # day for the baseline to age. It ignores the seen-diff, ranks whatever is
    # on the board right now, and sends — but deliberately writes NO state, so a
    # test run can't consume tomorrow's real leads or pollute the ledger.
    send_now = "--send-now" in sys.argv

    print("=" * 68)
    print("GTM SIGNAL MONITOR" + ("  [--send-now: test send, no state written]" if send_now else ""))
    print(f"Run: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 68)

    first_run = (not state.SEEN_PATH.exists() or not state.load_seen()) and not send_now
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
    deduped = dedupe(qualified)
    print(
        f"    {len(deduped)} qualified "
        f"({len(raw) - len(qualified)} dropped, {len(qualified) - len(deduped)} duplicate postings)"
    )
    qualified = deduped

    new = qualified if send_now else [l for l in qualified if l.key not in seen]
    print(f"    {len(new)} {'to rank (send-now ignores the diff)' if send_now else 'not seen before'}")

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

    # Two passes on purpose. Verification changes the score, so it has to run
    # before the final cut — but enriching all ~50 qualified leads would be
    # ~50 Exa calls a day for 5 slots. So: rank on board data, enrich only the
    # shortlist, then re-rank that shortlist on verified data. Bounded cost,
    # and the leads that actually get sent are the ones whose numbers were
    # checked.
    enricher = ExaEnricher()
    if enricher.enabled:
        shortlist = ranked[:SHORTLIST]
        print(f"\n[4b] Verifying + finding contacts for the top {len(shortlist)} (Exa)")
        found = 0
        for lead in shortlist:
            if enricher.enrich(lead):
                found += 1
            names = ", ".join(c["name"] for c in lead.contacts) or "no contact"
            flag = f"  ⚠ {lead.verification_note}" if lead.verification_note else ""
            print(f"    {lead.company:<28} {names}{flag}")
        print(f"    contacts found for {found}/{len(shortlist)}")
        ranked = rank(shortlist) + ranked[SHORTLIST:]
    else:
        print("\n[4b] EXA_API_KEY not set — skipping contacts and verification")

    top = [l for l in ranked if l.score >= SCORE_FLOOR][:DAILY_CAP]
    top_keys = {l.key for l in top}
    rest = [
        l for l in ranked if l.key not in top_keys and l.score >= TAIL_FLOOR
    ][:TAIL_CAP]
    print(f"    {len(top)} above the bar (cap {DAILY_CAP}), {len(rest)} shown in the tail")

    if not top:
        print("\nNothing cleared the score floor today — no digest sent.")
        if not send_now:
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
        sys.exit(1)

    if send_now:
        print("\nTest send complete. No state written — seen.json and ledger.md are untouched,")
        print("so the real first run still gets a clean baseline.")
        return

    state.append_ledger(top)
    state.mark_seen(seen, new)
    state.save_seen(seen)
    print(f"\nDone. {len(seen)} postings tracked; {len(top)} added to ledger.md.")


if __name__ == "__main__":
    main()
