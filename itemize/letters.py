"""Letter templates.

The evidence packet ends exactly where the reader needs to act. These are the
four letters that actually move a balance, written to be pasted into email or
printed, with the findings folded in.

They are drafts, not legal documents. Every one leaves the reader's own facts
in square brackets so nothing is asserted on their behalf that they have not
checked.
"""
from __future__ import annotations

HEADER = """[Your name]
[Your address]
[Account or statement number]
[Date]

{recipient}

Re: Account [number] — [patient name], date(s) of service [dates]
"""

CLOSE = """
I am asking for a written response. Please treat this letter as a request to
place the account on hold, and not to refer it to collections, while the
question is open.

Sincerely,
[Your name]
[Phone] / [Email]
"""


def _findings_block(findings, limit=12):
    if not findings:
        return "  [No specific line items — see attached statement.]\n"
    out = []
    for i, f in enumerate(findings[:limit], 1):
        lines = (", ".join(str(x) for x in f.lines)) if f.lines else "—"
        out.append(f"  {i}. {f.title}\n"
                   f"     Line(s): {lines}\n"
                   f"     Source: {f.citation}\n")
    if len(findings) > limit:
        out.append(f"  …and {len(findings) - limit} further item(s) in the attached packet.\n")
    return "".join(out)


def itemized_request():
    return HEADER.format(recipient="[Hospital/provider billing department]") + """
I am requesting a fully itemized statement for this account.

The statement I received shows summary or departmental totals. Please send an
itemized bill showing, for every line: date of service, the HCPCS or CPT code
billed, any modifiers, the revenue code, the number of units, and the charge
per unit.

If any line was billed to my insurer, please also state the claim number and the
date it was submitted.

I am not refusing to pay. I am asking to see what I am being asked to pay for
before I do.
""" + CLOSE


def dispute(findings, total=None):
    body = HEADER.format(recipient="[Hospital/provider billing department]") + """
I have reviewed the itemized statement for this account and I am disputing the
items below in writing.

"""
    body += _findings_block(findings)
    body += """
For each item, please either correct the charge or send me a written explanation
that identifies the medical record entry supporting it.

Where I have cited a federal file above, I have attached the relevant page. I am
happy to be shown that I have misread it.
"""
    if total:
        body += f"\nThe disputed amount is ${total:,.2f} of the balance.\n"
    return body + CLOSE


def financial_assistance():
    return HEADER.format(recipient="[Hospital financial assistance office]") + """
I am requesting a copy of your Financial Assistance Policy and the application
form, and I am applying for assistance on this account.

Please confirm in writing:

  1. The income and asset thresholds used to determine eligibility.
  2. The documents you need from me, and the deadline for submitting them.
  3. The "amounts generally billed" figure that applies to this account if I am
     found eligible.
  4. That collection activity on this account is suspended while my application
     is pending.

If this facility is not a tax-exempt hospital, please tell me so and send me
your charity care or self-pay discount policy instead.
""" + CLOSE


def gfe_dispute(estimate, billed):
    over = billed - estimate
    return HEADER.format(recipient="[Hospital/provider billing department]") + f"""
I received a Good Faith Estimate of ${estimate:,.2f} for this care. The bill I
have received is ${billed:,.2f} — ${over:,.2f} more than the estimate.

Please send me, in writing:

  1. An itemised explanation of each charge that was not in the Good Faith
     Estimate.
  2. The name and contact details of the person who prepared the estimate.

I am aware of the federal patient-provider dispute resolution process available
to uninsured and self-pay patients when a bill substantially exceeds a Good
Faith Estimate, and I intend to use it if this is not resolved.

I would prefer to resolve it directly with you first.
""" + CLOSE


def appeal_denial():
    return HEADER.format(recipient="[Insurance company — appeals department]") + """
I am appealing the denial of the claim referenced above.

Please provide, as part of this appeal:

  1. The specific plan provision relied on for the denial.
  2. The clinical criteria applied, and the credentials of the reviewer.
  3. All documents, records and other information relevant to my claim.

If this appeal is denied, I am requesting the denial letter state my right to an
independent external review, the deadline for requesting it, and how to do so.

Fewer than one per cent of denied claims are appealed. I am appealing this one.
""" + CLOSE


LETTERS = {
    "itemized": ("Request a fully itemized bill", lambda ctx, f, t: itemized_request()),
    "dispute": ("Dispute specific line items", lambda ctx, f, t: dispute(f, t)),
    "assistance": ("Apply for hospital financial assistance",
                   lambda ctx, f, t: financial_assistance()),
    "gfe": ("Dispute a bill that exceeds a Good Faith Estimate",
            lambda ctx, f, t: gfe_dispute(ctx.good_faith_estimate or 0.0, t or 0.0)),
    "appeal": ("Appeal an insurance denial", lambda ctx, f, t: appeal_denial()),
}


def render(kind, ctx=None, findings=(), total=None):
    if kind not in LETTERS:
        raise KeyError(f"unknown letter '{kind}'; choose from {', '.join(LETTERS)}")
    from .context import Context
    return LETTERS[kind][1](ctx or Context(), list(findings), total)


def suggest(ctx, findings):
    """Which letters are worth offering, given context and findings."""
    out = ["itemized"] if any(f.rule == "missing_code" for f in findings) else []
    if any(f.recoverable for f in findings):
        out.append("dispute")
    if ctx.nonprofit_hospital is True or ctx.self_pay:
        out.append("assistance")
    if ctx.good_faith_estimate is not None:
        out.append("gfe")
    if ctx.insured is True:
        out.append("appeal")
    seen, uniq = set(), []
    for k in out:
        if k not in seen:
            seen.add(k)
            uniq.append(k)
    return uniq
