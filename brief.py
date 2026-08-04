"""Context generation and delivery.

Two jobs:
  1. Ask Claude for a one-line "why now" and a one-line opener per lead. Only
     for leads that actually make the digest — the whole point is fewer, better.
  2. Send the digest by Gmail SMTP, same as yc-gtm-monitor-actions (GitHub's
     hosted runners allow outbound SMTP, so no Resend/domain requirement).
"""

from __future__ import annotations

import os
import smtplib
import textwrap
from datetime import datetime, timezone
from email.mime.text import MIMEText

MODEL = "claude-opus-5"
MAX_TOKENS = 8000

GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

SYSTEM = """You help a GTM engineer decide which early-stage startups to approach.

He sells short contract engagements: roughly one-week builds of outbound and GTM \
infrastructure (signal systems, enrichment pipelines, automated sequences, GTM agents) \
for early-stage startups. His buying signal is a startup posting its first GTM/sales \
role — budget exists, urgency exists, and there is no GTM infrastructure yet for the \
new hire to inherit.

He is NOT job hunting. He is not applying to these roles. He is selling to the company \
that posted them.

Some entries include a "contacts" line and a "background" block containing profile text, \
funding news and recent LinkedIn posts. Use them. A dated, specific detail the person \
actually published beats anything you infer.

For each company you are given, write:
  - "hook": one sentence on why THIS company is worth approaching THIS week. Prefer a \
concrete, recent fact from the background (a raise, a growth number, a post about hiring \
or about a GTM problem) over general reasoning about the stage. If the background \
contradicts the stage or team size given, trust the background and say so.
  - "opener": one LinkedIn message opener, max two sentences, addressed to the first \
listed contact by first name if one is given. Reference the specific role and what he \
could stand up in a week. Plain text, no em dashes, no ampersands, no buzzwords \
(seamless, robust, leverage, streamline, innovative, comprehensive). It must sound like \
a person typed it. Do not fabricate any detail that is not in the input.

Return the same number of items, in the same order as the input."""

SCHEMA = {
    "type": "object",
    "properties": {
        "leads": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "company": {"type": "string"},
                    "hook": {"type": "string"},
                    "opener": {"type": "string"},
                },
                "required": ["company", "hook", "opener"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["leads"],
    "additionalProperties": False,
}


def add_context(leads: list) -> None:
    """Fill lead.hook and lead.opener. Degrades to empty strings on any failure —
    a digest without openers is still worth sending."""
    if not leads:
        return
    if not ANTHROPIC_API_KEY:
        print("[!] ANTHROPIC_API_KEY not set — sending digest without hooks/openers")
        return

    import anthropic

    def block(i: int, l) -> str:
        parts = [
            f"{i + 1}. {l.company} — hiring: {l.title}",
            f"   stage: {l.stage or 'unknown'} | team: "
            f"{l.verified_headcount or l.headcount or 'unknown'}"
            + (f" ({l.headcount_growth} YoY)" if l.headcount_growth else "")
            + (f" | raised {l.total_funding}" if l.total_funding else "")
            + f" | Techstars {l.cohort_year or '?'} | {l.location or 'location unknown'}",
            f"   product: {l.description or 'no description available'}",
            f"   why it ranked: {'; '.join(l.reasons)}",
        ]
        if l.contacts:
            who = "; ".join(f"{c['name']} ({c['role']})" for c in l.contacts)
            parts.append(f"   contacts: {who}")
        if l.evidence:
            # Recent posts and funding news live here — the raw material for a
            # dated, specific hook rather than a generic one.
            parts.append(f"   background: {l.evidence[:900]}")
        return "\n".join(parts)

    payload = "\n\n".join(block(i, l) for i, l in enumerate(leads))

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM,
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": payload}],
        )
        if resp.stop_reason == "refusal":
            print("[!] Claude declined to generate context — sending digest without it")
            return

        import json

        text = next(b.text for b in resp.content if b.type == "text")
        items = json.loads(text)["leads"]
        for lead, item in zip(leads, items):
            lead.hook = (item.get("hook") or "").strip()
            lead.opener = (item.get("opener") or "").strip()
        print(f"    context written for {len(items)} lead(s)")
    except Exception as e:
        print(f"[!] Context generation failed ({e}) — sending digest without it")


# Plain text, formatted the way yc-gtm-monitor-actions does it: aligned labels,
# no indentation to strip, one blank line before the opener so it can be
# selected and pasted cleanly.
LABEL = 9  # "Posting:" / "Warning:" plus one space
WIDTH = 78


def _field(label: str, value: str) -> str:
    """Wrap prose under a left-aligned label, continuation lines hanging to
    match. Long hooks are paragraphs — unwrapped they render as one endless
    line in most mail clients."""
    return textwrap.fill(
        value,
        width=WIDTH,
        initial_indent=f"{label + ':':<{LABEL}}",
        subsequent_indent=" " * LABEL,
        # Never split a hyphenated term ("long-cycle") or a long token. URLs
        # must survive intact or they stop being clickable, which matters more
        # than staying inside the margin.
        break_on_hyphens=False,
        break_long_words=False,
    )


def _company_line(lead) -> str:
    bits = [lead.stage.replace("_", " ") if lead.stage else "?"]
    bits.append(f"{lead.verified_headcount or lead.headcount or '?'} people")
    if lead.headcount_growth:
        bits.append(f"{lead.headcount_growth} YoY")
    if lead.total_funding:
        rounds = f" ({lead.funding_rounds} rounds)" if lead.funding_rounds else ""
        bits.append(f"{lead.total_funding} raised{rounds}")
    bits.append(f"Techstars {lead.cohort_year or '?'}")
    if lead.location:
        bits.append(lead.location)
    return " · ".join(bits)


def _format(lead, rank: int) -> str:
    lines = [f"{rank}. {lead.company} — {lead.title}  [{lead.score}]", ""]

    if lead.hook:
        lines.append(_field("Why now", lead.hook))
    lines.append(_field("Company", _company_line(lead)))
    if lead.verification_note:
        # The label does the shouting; uppercasing the sentence too reads as
        # an alarm rather than a caveat.
        lines.append(_field("Warning", lead.verification_note))
    if lead.description:
        lines.append(_field("Product", lead.description))

    if lead.contacts:
        for i, contact in enumerate(lead.contacts):
            label = "Contact" if i == 0 else ""
            lines.append(f"{label + ':' if label else '':<{LABEL}}{contact['name']} — {contact['role']}")
            lines.append(f"{'':<{LABEL}}{contact['linkedin']}")
    else:
        lines.append(f"{'Contact:':<{LABEL}}(none found)")

    if lead.website:
        lines.append(f"{'Site:':<{LABEL}}{lead.website}")
    lines.append(f"{'Posting:':<{LABEL}}{lead.url}")

    if lead.opener:
        # Flush left, no indent — so it copies into LinkedIn without dragging
        # leading spaces along.
        lines.append("")
        lines.append("Opener:")
        lines.append(textwrap.fill(lead.opener, width=WIDTH))

    return "\n".join(lines)


def build_body(top: list, rest: list) -> str:
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    plural = "lead" if len(top) == 1 else "leads"
    parts = [
        f"{len(top)} {plural} worth acting on — {date_str}",
        "Ranked. Work down the list and stop when you stop.",
        "",
    ]
    for i, lead in enumerate(top):
        parts.append("-" * WIDTH)
        parts.append("")
        parts.append(_format(lead, i + 1))
        parts.append("")

    if rest:
        parts.append("-" * WIDTH)
        parts.append("")
        parts.append(f"Missed the bar ({len(rest)}) — no action expected:")
        parts.append("")
        for lead in rest:
            parts.append(f"  [{lead.score:>2}]  {lead.company} — {lead.title}")
            if lead.website:
                parts.append(f"        {lead.website}")
        parts.append("")

    parts.append("-" * WIDTH)
    parts.append("")
    parts.append("Every lead above is appended to ledger.md in the repo, with its score,")
    parts.append("signals and the opener sent. Grep the company or contact name there")
    parts.append("when a connection gets accepted.")
    return "\n".join(parts)


def send(top: list, rest: list) -> bool:
    """True only if delivery actually succeeded — the caller uses this to decide
    whether it is safe to mark these leads as seen."""
    if not (GMAIL_ADDRESS and GMAIL_APP_PASSWORD and RECIPIENT_EMAIL):
        print("[!] Gmail credentials not set — cannot send")
        return False

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    subject = f"GTM signals — {len(top)} to act on ({len(rest)} below bar) — {date_str}"

    msg = MIMEText(build_body(top, rest), "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = RECIPIENT_EMAIL

    print(f"[*] Sending: {subject}")
    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
            server.starttls()
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_ADDRESS, [RECIPIENT_EMAIL], msg.as_string())
        print("    sent")
        return True
    except Exception as e:
        print(f"[!] Send failed: {e}")
        return False
