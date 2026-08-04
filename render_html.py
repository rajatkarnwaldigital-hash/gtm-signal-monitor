"""HTML rendering for the digest.

Constraints that shape everything here:

  * **Inline styles only.** Gmail strips or partially honours <style> blocks
    depending on client. Every rule that matters is on the element.
  * **No external anything.** No web fonts, no CDN, no images — they trigger
    the "display images?" prompt and half the design disappears until clicked.
  * **Tables for the outer shell**, divs inside. Outlook ignores div widths.
  * **Colour carries meaning, never decoration.** Score tier, a data conflict,
    and headcount direction are the only things that get colour. If everything
    is highlighted, the ranking stops being readable at a glance.

The layout is built around one question: what does he actually do with this?
He reads the rank, decides yes/no, and copies the opener into LinkedIn. So the
opener gets its own bordered, high-contrast block — the one thing on the card
you can select cleanly on a phone — and everything else supports that decision.
"""

from __future__ import annotations

from html import escape

# Restrained palette. Deliberately not brand-y: this is a working document.
INK = "#161b22"
MUTED = "#5b6b7c"
FAINT = "#8b99a7"
BORDER = "#e1e6eb"
BG = "#f4f6f8"
CARD = "#ffffff"
ACCENT = "#0b5fd0"
GOOD = "#12704f"
GOOD_BG = "#e7f4ee"
WARN = "#9a3412"
WARN_BG = "#fdf0e7"
MID_BG = "#fff8e6"
MID = "#8a6100"

FONT = (
    "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
)


def _score_style(score: int) -> tuple[str, str]:
    """Colour the score badge by tier so the list is scannable without reading."""
    if score >= 18:
        return GOOD, GOOD_BG
    if score >= 14:
        return MID, MID_BG
    return MUTED, BG


def _chip(text: str, colour: str = MUTED, bg: str = BG) -> str:
    return (
        f'<span style="display:inline-block;padding:3px 9px;margin:0 6px 6px 0;'
        f'font-size:12px;line-height:16px;color:{colour};background:{bg};'
        f'border-radius:11px;white-space:nowrap;">{escape(text)}</span>'
    )


def _company_chips(lead) -> str:
    chips = []
    if lead.stage:
        chips.append(_chip(lead.stage.replace("_", " ")))
    headcount = lead.verified_headcount or lead.headcount
    if headcount:
        chips.append(_chip(f"{headcount} people"))
    if lead.headcount_growth:
        shrinking = lead.headcount_growth.startswith("-")
        chips.append(
            _chip(
                f"{lead.headcount_growth} YoY",
                WARN if shrinking else GOOD,
                WARN_BG if shrinking else GOOD_BG,
            )
        )
    if lead.total_funding:
        rounds = f" · {lead.funding_rounds} rounds" if lead.funding_rounds else ""
        chips.append(_chip(f"{lead.total_funding} raised{rounds}"))
    if lead.cohort_year:
        chips.append(_chip(f"Techstars {lead.cohort_year}"))
    if lead.location:
        chips.append(_chip(lead.location))
    return "".join(chips)


def _contacts(lead) -> str:
    if not lead.contacts:
        return (
            f'<div style="font-size:13px;color:{FAINT};padding:10px 0;">'
            f"No contact found for this one.</div>"
        )
    rows = []
    for contact in lead.contacts:
        rows.append(
            f'<div style="padding:7px 0;border-bottom:1px solid {BORDER};">'
            f'<a href="{escape(contact["linkedin"])}" '
            f'style="color:{ACCENT};text-decoration:none;font-weight:600;font-size:14px;">'
            f'{escape(contact["name"])}</a>'
            f'<span style="color:{MUTED};font-size:13px;"> — {escape(contact["role"])}</span>'
            f"</div>"
        )
    return "".join(rows)


def _opener(lead) -> str:
    """The copy-paste target. Given its own block, wide line-height and a left
    rule so it can be selected cleanly on a phone without catching the labels
    above or below it."""
    if not lead.opener:
        return ""
    return (
        f'<div style="margin:14px 0 4px;">'
        f'<div style="font-size:11px;font-weight:700;letter-spacing:.7px;'
        f'text-transform:uppercase;color:{FAINT};margin-bottom:6px;">'
        f"Opener — copy into LinkedIn</div>"
        f'<div style="border-left:3px solid {ACCENT};background:{BG};'
        f'padding:12px 14px;font-size:14px;line-height:22px;color:{INK};'
        f'border-radius:0 6px 6px 0;">{escape(lead.opener)}</div>'
        f"</div>"
    )


def _card(lead, rank: int) -> str:
    colour, bg = _score_style(lead.score)

    warning = ""
    if lead.verification_note:
        warning = (
            f'<div style="margin:10px 0;padding:9px 12px;background:{WARN_BG};'
            f'border-radius:6px;font-size:13px;color:{WARN};">'
            f"<strong>Check this:</strong> {escape(lead.verification_note)}</div>"
        )

    hook = ""
    if lead.hook:
        hook = (
            f'<div style="font-size:14px;line-height:22px;color:{INK};'
            f'margin:10px 0 12px;">{escape(lead.hook)}</div>'
        )

    product = ""
    if lead.description:
        product = (
            f'<div style="font-size:13px;line-height:20px;color:{MUTED};'
            f'margin:2px 0 10px;">{escape(lead.description)}</div>'
        )

    links = []
    if lead.website:
        links.append(
            f'<a href="{escape(lead.website)}" style="color:{ACCENT};'
            f'text-decoration:none;">Site</a>'
        )
    links.append(
        f'<a href="{escape(lead.url)}" style="color:{ACCENT};'
        f'text-decoration:none;">Job posting</a>'
    )

    return f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
       style="background:{CARD};border:1px solid {BORDER};border-radius:10px;
              margin:0 0 16px;">
  <tr><td style="padding:18px 20px;">

    <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
      <tr>
        <td style="vertical-align:top;">
          <div style="font-size:12px;font-weight:700;color:{FAINT};
                      letter-spacing:.5px;margin-bottom:3px;">#{rank}</div>
          <div style="font-size:18px;font-weight:700;color:{INK};
                      line-height:24px;">{escape(lead.company)}</div>
          <div style="font-size:15px;color:{MUTED};line-height:21px;
                      margin-top:2px;">{escape(lead.title)}</div>
        </td>
        <td width="70" style="vertical-align:top;text-align:right;">
          <span style="display:inline-block;padding:5px 11px;background:{bg};
                       color:{colour};border-radius:13px;font-size:13px;
                       font-weight:700;">{lead.score}</span>
        </td>
      </tr>
    </table>

    {hook}{product}
    <div style="margin:10px 0 4px;">{_company_chips(lead)}</div>
    {warning}

    <div style="font-size:11px;font-weight:700;letter-spacing:.7px;
                text-transform:uppercase;color:{FAINT};margin:14px 0 2px;">
      Who to contact</div>
    {_contacts(lead)}

    {_opener(lead)}

    <div style="margin-top:14px;font-size:13px;color:{FAINT};">
      {' &nbsp;·&nbsp; '.join(links)}
    </div>

  </td></tr>
</table>"""


def _tail(rest: list) -> str:
    if not rest:
        return ""
    rows = []
    for lead in rest:
        site = (
            f'<a href="{escape(lead.website)}" style="color:{ACCENT};'
            f'text-decoration:none;">{escape(lead.company)}</a>'
            if lead.website
            else escape(lead.company)
        )
        rows.append(
            f'<tr>'
            f'<td style="padding:6px 0;font-size:13px;color:{INK};">{site}'
            f'<span style="color:{MUTED};"> — {escape(lead.title)}</span></td>'
            f'<td width="34" style="text-align:right;font-size:12px;color:{FAINT};">'
            f"{lead.score}</td></tr>"
        )
    return f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
       style="background:{CARD};border:1px solid {BORDER};border-radius:10px;
              margin:8px 0 16px;">
  <tr><td style="padding:16px 20px;">
    <div style="font-size:11px;font-weight:700;letter-spacing:.7px;
                text-transform:uppercase;color:{FAINT};margin-bottom:8px;">
      Peripheral vision — {len(rest)} that missed the bar</div>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
      {''.join(rows)}
    </table>
  </td></tr>
</table>"""


def build_html(top: list, rest: list, date_str: str) -> str:
    cards = "".join(_card(lead, i + 1) for i, lead in enumerate(top))
    plural = "lead" if len(top) == 1 else "leads"
    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light">
<title>GTM signals</title></head>
<body style="margin:0;padding:0;background:{BG};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
       style="background:{BG};padding:24px 12px;">
  <tr><td align="center">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
           style="max-width:640px;font-family:{FONT};">

      <tr><td style="padding:0 0 18px;">
        <div style="font-size:22px;font-weight:700;color:{INK};">
          {len(top)} {plural} worth acting on</div>
        <div style="font-size:14px;color:{MUTED};margin-top:4px;">
          Ranked — work down the list and stop when you stop. &nbsp;·&nbsp; {escape(date_str)}</div>
      </td></tr>

      <tr><td>{cards}</td></tr>
      <tr><td>{_tail(rest)}</td></tr>

      <tr><td style="padding:6px 4px 0;font-size:12px;line-height:19px;color:{FAINT};">
        Every lead above is appended to <strong>ledger.md</strong> in the repo,
        with its score, signals and the opener sent. Grep the company or the
        contact's name there when a connection gets accepted.
      </td></tr>

    </table>
  </td></tr>
</table>
</body></html>"""
