"""State-law findings, and the honesty rules that go with them.

Three things make this module different from every other rule source here:

1. **The data is hand-curated, not fetched.** `web/data/states.json` has no
   upstream federal file behind it, so every entry carries its own citations
   and a `verified` date.
2. **Absence means "not researched", never "no protection".** A state we have
   not looked at gets an explicit "we do not know" finding. Rendering silence
   as "you have no rights here" would be the single most damaging thing this
   project could do.
3. **State protections are gated on plan funding.** State insurance law
   generally does not reach self-funded (ERISA) employer plans, which cover
   roughly two-thirds of workers with employer coverage. A protection the
   reader cannot actually use is not a protection.

Entries go stale. Legislatures amend these every session, and a stale entry
that answers confidently is worse than no entry at all -- so staleness is
surfaced in the finding itself, not buried in a manifest.
"""
from __future__ import annotations

import datetime
import json
import os

from .model import Finding

DEFAULT_STALE_DAYS = 365


def _today():
    return datetime.date.today()


def _age_days(verified):
    try:
        d = datetime.date.fromisoformat(verified)
    except (ValueError, TypeError):
        return None
    return (_today() - d).days


class StateData:
    def __init__(self, data_dir):
        path = os.path.join(data_dir, "states.json")
        raw = {}
        if os.path.exists(path):
            with open(path) as f:
                raw = json.load(f)
        self.meta = raw.get("_meta", {})
        self.states = raw.get("states", {})
        self.stale_after = self.meta.get("stale_after_days", DEFAULT_STALE_DAYS)

    def get(self, code):
        return self.states.get((code or "").strip().upper())

    def is_stale(self, entry):
        age = _age_days((entry or {}).get("verified"))
        return age is not None and age > self.stale_after, age


def _stale_suffix(sd, entry):
    stale, age = sd.is_stale(entry)
    if not stale:
        return ""
    years = age / 365.0
    return (f" **This entry was last verified {age} days ago (about "
            f"{years:.1f} years). State law changes every legislative session — "
            "re-check it before relying on it.**")


def _cites(entry, extra=""):
    urls = "; ".join(entry.get("citations", []) or [])
    base = f"Hand-curated state entry for {entry.get('name')}, verified {entry.get('verified')}."
    if urls:
        base += f" Sources: {urls}"
    return base + (f" {extra}" if extra else "")


def evaluate(lines, ref, ctx, data_dir=None):
    """Findings that depend on the reader's state. Requires ctx.state."""
    code = (getattr(ctx, "state", "") or "").strip().upper()
    if not code:
        return []

    sd = StateData(data_dir or getattr(ref, "dir", "") or "")
    if not sd.states:
        return []

    entry = sd.get(code)
    total = sum(l.charge for l in lines)

    # Unresearched state: say so, loudly and specifically.
    if entry is None:
        return [Finding(
            rule="state_not_researched",
            severity="notice",
            title=f"We have not researched {code} state law",
            detail=(
                f"itemize does not yet have a verified entry for {code}. That is "
                "**not** the same as saying you have no state protections — it "
                "means we do not know, and we would rather say so than guess. "
                "Federal protections in this packet still apply everywhere. For "
                "state law, contact your state department of insurance or attorney "
                "general's consumer line, both of which handle billing complaints "
                "for free."),
            lines=[],
            citation=("No entry in web/data/states.json. "
                      + (sd.meta.get("note", "") or "")),
        )]

    out = []
    self_funded = getattr(ctx, "plan_funding", None) == "self_funded"
    gate = ("" if not self_funded else
            " **You said your plan is self-funded, so state insurance law "
            "generally does not reach it — this protection probably does not "
            "apply to you.** Federal protections still do.")

    # --- ground ambulance ---
    if ctx.ground_ambulance is True:
        amb = entry.get("ambulance_balance_billing")
        note = entry.get("ambulance_note") or ""
        if amb is True:
            out.append(Finding(
                rule="state_ambulance_protected",
                severity="high" if not self_funded else "notice",
                title=f"{entry['name']} has ground-ambulance balance-billing protections",
                detail=(f"Ground ambulance is carved out of the federal No Surprises "
                        f"Act, but {entry['name']} has enacted its own protection. "
                        + (note + " " if note else "")
                        + "These state laws reach state-regulated (fully insured) "
                          "plans only." + gate),
                lines=[], citation=_cites(entry) + _stale_suffix(sd, entry), amount=0.0))
        elif amb is False:
            out.append(Finding(
                rule="state_ambulance_unprotected",
                severity="warn",
                title=f"{entry['name']} has no ground-ambulance protection we could find",
                detail=("Ground ambulance is excluded from the federal No Surprises "
                        f"Act, and we found no {entry['name']} law filling that gap. "
                        "You may be exposed to a balance bill. It is still worth "
                        "negotiating directly and asking the service for its "
                        "hardship policy — many have one and do not advertise it."),
                lines=[], citation=_cites(entry) + _stale_suffix(sd, entry), amount=0.0))
        else:
            out.append(Finding(
                rule="state_ambulance_unknown",
                severity="notice",
                title=f"We have not verified ground-ambulance law for {entry['name']}",
                detail=("Around 22 states have enacted ground-ambulance protections; "
                        "we have confirmed 16 of them by name and deliberately left "
                        "the rest unrecorded rather than guess. Check with your state "
                        "department of insurance."),
                lines=[], citation=_cites(entry) + _stale_suffix(sd, entry), amount=0.0))

    # --- charity care beyond federal 501(r) ---
    #
    # An entry can be PARTIALLY researched. Ground-ambulance law is recorded for
    # all 51 jurisdictions; charity-care and state-programme law is recorded for
    # 24. Having an entry at all used to be the signal that a state was
    # researched, so once every state had one, silence on financial assistance
    # would have read as "no such law here" -- which is precisely the inference
    # this file's rules exist to prevent. Say the narrower thing instead.
    if entry.get("charity_care") is None and entry.get("state_program") is None:
        out.append(Finding(
            rule="state_assistance_not_researched",
            severity="notice",
            title=(f"We have not researched {entry['name']}'s hospital "
                   "financial-assistance law"),
            detail=(f"We have a verified entry for {entry['name']} covering ground "
                    "ambulance, but we have not researched whether the state "
                    "imposes charity-care duties beyond federal 501(r), or runs an "
                    "assistance programme of its own. That is **not** the same as "
                    "saying there are none — it means we do not know, and we would "
                    "rather say so than guess. Ask the hospital for its financial "
                    "assistance policy regardless: a tax-exempt hospital must have "
                    "one under federal law. Your state department of insurance or "
                    "attorney general's consumer line will know the state rules, "
                    "and both handle billing complaints for free."),
            lines=[], citation=_cites(entry) + _stale_suffix(sd, entry)))

    cc = entry.get("charity_care") or {}

    # A SOURCED NEGATIVE is not a gap, and it is not "you have no rights" either.
    # Most states set no minimum standards of their own -- but federal 501(r)
    # still binds every tax-exempt hospital, and that is the thing a reader in
    # one of these states needs told, because otherwise silence here reads as
    # "nothing to ask for".
    if cc.get("state_minimum_standards") is False:
        out.append(Finding(
            rule="state_no_assistance_standard",
            severity="info",
            title=(f"{entry['name']} sets no financial-assistance standards of its "
                   "own — the federal floor still applies"),
            detail=("Most states do not set minimum standards for hospital financial "
                    "assistance, and this is one of them. That is a researched "
                    "finding, not a gap in our data — and it does **not** mean there "
                    "is nothing to ask for. Federal law (IRC 501(r)) requires every "
                    "tax-exempt hospital to have a written financial assistance "
                    "policy, to publicise it, and to limit what it charges patients "
                    "who qualify. Ask this hospital for its policy by name and apply "
                    "in writing. "
                    + (cc.get("note") or "")
                    + " State legislatures move quickly on this; if the reading "
                      "below is old, check with your state department of insurance "
                      "before treating it as current."),
            lines=[], citation=_cites(entry) + _stale_suffix(sd, entry)))

    if cc.get("applies_to") == "all":
        out.append(Finding(
            rule="state_charity_all_hospitals",
            severity="high",
            title=f"{entry['name']} requires financial assistance at *all* hospitals",
            detail=("Federal 501(r) only binds tax-exempt hospitals. "
                    f"{entry['name']} is reported to impose financial-assistance "
                    "requirements on all hospitals, so you can ask for the policy "
                    "even if this facility is for-profit. "
                    + (cc.get("note") or "")
                    + (f" Free care is reported below {cc['free_care_fpl']}% of the "
                       "federal poverty level"
                       if cc.get("free_care_fpl") else "")
                    + (f", with discounts up to {cc['discount_fpl']}%."
                       if cc.get("discount_fpl") else ".")),
            lines=[], citation=_cites(entry) + _stale_suffix(sd, entry), amount=total))
    elif cc.get("free_care_fpl"):
        out.append(Finding(
            rule="state_charity_threshold",
            severity="high",
            title=(f"{entry['name']} sets a financial-assistance threshold "
                   f"at {cc['free_care_fpl']}% FPL"),
            detail=((cc.get("note") or "")
                    + f" If your household income is at or below {cc['free_care_fpl']}% "
                      "of the federal poverty level, ask for free care by name."),
            lines=[], citation=_cites(entry) + _stale_suffix(sd, entry), amount=total))

    # --- state-run assistance programme ---
    prog = entry.get("state_program")
    if prog:
        ceiling = prog.get("fpl_ceiling")
        out.append(Finding(
            rule="state_assistance_program",
            severity="high",
            title=f"{entry['name']} runs a state assistance programme: {prog['name']}",
            detail=((prog.get("note") or "")
                    + (f" Reported to cover residents up to {ceiling}% of the federal "
                       "poverty level." if ceiling else "")
                    + " Ask the hospital's financial counsellor to screen you for it "
                      "by name — this is separate from the hospital's own charity "
                      "care, and you may qualify for both."),
            lines=[], citation=_cites(entry) + _stale_suffix(sd, entry), amount=total))

    # --- all-payer rate setting changes what a benchmark means ---
    if entry.get("all_payer_rate_setting"):
        out.append(Finding(
            rule="state_all_payer_rates",
            severity="info",
            title=f"{entry['name']} regulates hospital rates on an all-payer basis",
            detail=((entry.get("all_payer_note") or "")
                    + " Treat any Medicare-benchmark finding in this packet with "
                      "extra caution here: the relationship between a charge and a "
                      "Medicare rate is not what it is in other states."),
            lines=[], citation=_cites(entry) + _stale_suffix(sd, entry), amount=0.0))

    # --- statute of limitations ---
    sol = entry.get("debt_sol_years")
    if sol:
        out.append(Finding(
            rule="state_debt_sol",
            severity="notice",
            title=f"{entry['name']} limits how long this debt can be sued on: {sol} years",
            detail=(f"The statute of limitations on this kind of debt is reported to be "
                    f"{sol} years in {entry['name']}. Note that making a payment or "
                    "acknowledging the debt in writing can restart that clock in many "
                    "states — take advice before paying anything on an old balance."),
            lines=[], citation=_cites(entry) + _stale_suffix(sd, entry), amount=0.0))

    return out
