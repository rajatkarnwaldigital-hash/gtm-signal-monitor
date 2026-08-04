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

For each company you are given, write:
  - "hook": one sentence on why THIS company is worth approaching THIS week. Be concrete \
about what the new hire will be walking into given the company's stage, size and product. \
No generic filler.
  - "opener": one LinkedIn message opener, max two sentences, that references the specific \
role and what he could stand up in a week. Plain text, no em dashes, no ampersands, no \
buzzwords (seamless, robust, leverage, streamline, innovative, comprehensive). It must \
sound like a person typed it.

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

    payload = "\n\n".join(
        f"{i + 1}. {l.company} — hiring: {l.title}\n"
        f"   stage: {l.stage or 'unknown'} | team: {l.headcount or 'unknown'} | "
        f"Techstars {l.cohort_year or '?'} | {l.location or 'location unknown'}\n"
        f"   product: {l.description or 'no description available'}\n"
        f"   why it ranked: {'; '.join(l.reasons)}"
        for i, l in enumerate(leads)
    )

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


def _format(lead, rank: int) -> str:
    lines = [
        f"{rank}. {lead.company} — {lead.title}   [score {lead.score}]",
        f"   Why now: {lead.hook}" if lead.hook else "",
        f"   Signals: {' · '.join(lead.reasons)}",
        f"   Stage:   {lead.stage or '?'} · {lead.headcount or '?'} employees · "
        f"Techstars {lead.cohort_year or '?'} · {lead.location or 'location unknown'}",
        f"   Product: {lead.description}" if lead.description else "",
        f"   Site:    {lead.website}" if lead.website else "   Site:    (not resolved)",
        f"   Posting: {lead.url}",
        f"\n   Opener:\n   {lead.opener}" if lead.opener else "",
    ]
    return "\n".join(x for x in lines if x)


def build_body(top: list, rest: list) -> str:
    parts = [
        f"{len(top)} lead(s) worth acting on today.",
        "Ranked. Work down the list and stop when you stop.",
        "",
        "=" * 68,
        "",
    ]
    parts.extend(_format(l, i + 1) + "\n\n" + "-" * 68 + "\n" for i, l in enumerate(top))

    if rest:
        parts.append(f"\nPeripheral vision — nearest {len(rest)} that missed the bar:\n")
        parts.extend(
            f"  · {l.company} — {l.title} [score {l.score}] {l.website or ''}" for l in rest
        )
        parts.append("")

    parts.append("\nEvery lead above is appended to ledger.md in the repo.")
    parts.append("Grep the company name there when a connection gets accepted.")
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
