"""Entitlements — what the reader may not have to pay, regardless of coding.

Every other rule in this project answers "is this charge wrong?". These answer
"do you have to pay it?", which is usually where the larger sum sits. A correct
charge you are legally entitled to have reduced or waived is worth more than a
coding error worth $180.

Citations here are to statute and regulation rather than to a data file. They
are quoted conservatively and should be checked against the current text at
eCFR.gov and irs.gov before being relied on; regulations are amended, and a
citation that has moved is worse than none.
"""
from __future__ import annotations

from .model import Finding

# Threshold at which a self-pay patient may invoke patient-provider dispute
# resolution against a Good Faith Estimate.
GFE_DISPUTE_THRESHOLD = 400.0

CITE_GFE = ("No Surprises Act good-faith-estimate rules for uninsured and "
            "self-pay individuals, 45 CFR 149.610; patient-provider dispute "
            "resolution, 45 CFR 149.620. Verify current text at eCFR.gov.")
CITE_501R = ("Internal Revenue Code 501(r)(4) (written financial assistance "
             "policy required of tax-exempt hospitals) and 501(r)(5) "
             "(limitation on charges to FAP-eligible individuals to amounts "
             "generally billed); 26 CFR 1.501(r)-5. Verify at irs.gov.")
CITE_NSA_EMERGENCY = ("No Surprises Act balance-billing protections for "
                      "emergency services, 45 CFR 149.410. Verify at eCFR.gov.")
CITE_NSA_FACILITY = ("No Surprises Act protections for non-emergency services "
                     "by non-participating providers at participating "
                     "facilities, 45 CFR 149.420. Verify at eCFR.gov.")
CITE_AMBULANCE = ("Ground ambulance services are excluded from the federal No "
                  "Surprises Act; protection depends on state law. See the "
                  "federal Advisory Committee on Ground Ambulance and Patient "
                  "Billing (GAPB), cms.gov.")


def rule_good_faith_estimate(lines, ref, ctx):
    """Self-pay readers are entitled to a GFE, and to dispute overruns."""
    if not ctx.self_pay:
        return []
    total = sum(l.charge for l in lines)
    if ctx.good_faith_estimate is None:
        return [Finding(
            rule="right_gfe_missing",
            severity="high",
            title="You were entitled to a Good Faith Estimate before this care",
            detail=(
                "You told us you are uninsured or self-pay. Providers must give "
                "uninsured and self-pay patients a written Good Faith Estimate of "
                "expected charges in advance of scheduled care. If you never "
                "received one, say so in writing and ask for it now. "
                f"This bill totals ${total:,.2f}. If a Good Faith Estimate exists "
                f"and the bill exceeds it by ${GFE_DISPUTE_THRESHOLD:,.0f} or more, "
                "you can start the federal patient-provider dispute resolution "
                "process rather than negotiating alone. Almost nobody uses this "
                "route, and it costs the provider more than settling."),
            lines=[],
            citation=CITE_GFE,
            amount=total,
        )]

    over = total - ctx.good_faith_estimate
    if over >= GFE_DISPUTE_THRESHOLD:
        return [Finding(
            rule="right_gfe_exceeded",
            severity="high",
            title=(f"Bill exceeds your Good Faith Estimate by ${over:,.2f} — "
                   "you can dispute it federally"),
            detail=(
                f"Your Good Faith Estimate was ${ctx.good_faith_estimate:,.2f} and "
                f"this bill totals ${total:,.2f}, a difference of ${over:,.2f}. "
                f"Because that is at least ${GFE_DISPUTE_THRESHOLD:,.0f}, you may "
                "use the federal patient-provider dispute resolution process. "
                "Start it within the deadline stated in your estimate paperwork, "
                "and keep the estimate — it is the evidence."),
            lines=[],
            citation=CITE_GFE,
            amount=over,
            recoverable=True,
        )]
    return []


def rule_charity_care(lines, ref, ctx):
    """Non-profit hospitals must have a financial assistance policy."""
    if ctx.nonprofit_hospital is not True:
        return []
    total = sum(l.charge for l in lines)
    return [Finding(
        rule="right_charity_care",
        severity="high",
        title="This hospital is required to have a financial assistance policy",
        detail=(
            "Tax-exempt hospitals must maintain a written Financial Assistance "
            "Policy, must publicise it, and may not charge patients who qualify "
            "under it more than the amounts generally billed to insured patients. "
            "Ask for the Financial Assistance Policy and the application by name — "
            "eligibility is often far higher up the income scale than people "
            "assume, and applying can reduce or erase the balance regardless of "
            "whether any individual line is coded correctly. "
            "Ask them to place the account on hold while your application is "
            "pending."),
        lines=[],
        citation=CITE_501R,
        amount=total,
    )]


def rule_no_surprises(lines, ref, ctx):
    """Balance billing may simply be prohibited here."""
    out = []
    total = sum(l.charge for l in lines)
    if ctx.emergency is True:
        out.append(Finding(
            rule="right_nsa_emergency",
            severity="high",
            title="Emergency care: balance billing above in-network cost sharing is prohibited",
            detail=(
                "You indicated this was emergency care. For emergency services, "
                "you generally cannot be billed more than your plan's in-network "
                "cost sharing, even if the provider or facility is out of network, "
                "and your cost sharing must count toward your in-network deductible "
                "and out-of-pocket maximum. If this bill charges you the difference "
                "between the provider's charge and what your plan paid, say in "
                "writing that you believe it is a prohibited balance bill and ask "
                "them to rebill."),
            lines=[], citation=CITE_NSA_EMERGENCY, amount=total, recoverable=True))

    if ctx.out_of_network is True and ctx.in_network_facility is True:
        out.append(Finding(
            rule="right_nsa_facility",
            severity="high",
            title="Out-of-network provider at an in-network facility: balance billing restricted",
            detail=(
                "You indicated an out-of-network provider treated you at an "
                "in-network facility. For most such services you cannot be balance "
                "billed beyond in-network cost sharing unless you gave written "
                "consent in advance on the required federal notice form. If you "
                "did not sign that specific form, ask them to produce it — if they "
                "cannot, the protection applies."),
            lines=[], citation=CITE_NSA_FACILITY, amount=total, recoverable=True))

    if ctx.ground_ambulance is True:
        state = f" in {ctx.state}" if ctx.state else ""
        out.append(Finding(
            rule="right_ambulance_gap",
            severity="warn",
            title="Ground ambulance is not covered by the federal surprise-billing law",
            detail=(
                "Ground ambulance rides were deliberately excluded from the federal "
                "No Surprises Act, so federal balance-billing protection does not "
                f"apply. Roughly 22 states have enacted their own protections for "
                f"people in fully-insured (not self-funded) plans. Check your "
                f"state's rules{state} before paying, and check whether your plan "
                "is fully insured or self-funded — the answer decides whether state "
                "law reaches you at all."),
            lines=[], citation=CITE_AMBULANCE, amount=0.0))
    return out


RIGHTS_RULES = (
    rule_good_faith_estimate,
    rule_charity_care,
    rule_no_surprises,
)


def evaluate(lines, ref, ctx):
    out = []
    for rule in RIGHTS_RULES:
        out.extend(rule(lines, ref, ctx))
    return out
