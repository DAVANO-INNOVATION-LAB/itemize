"""The audit rules.

Design constraints, in priority order:

1. Every finding cites a source the reader can independently check. A hospital
   billing office dismisses an unsourced claim instantly.
2. No rule requires AMA CPT or ADA CDT data. Everything here runs on
   public-domain CMS files or on the structure of the bill itself.
3. We never claim a charge is "wrong" when we can only show it is "high".
   Medicare's allowed amount is not a ceiling a hospital is obliged to honour.
   Only duplicate billing is reported as directly recoverable.
"""
from __future__ import annotations

import re

from collections import defaultdict

from .context import Context
from .model import Finding, REFRESH_DAYS, classify, normalize_drg

# Modifiers that most often carry an unbundling argument with them.
MODIFIER_NOTES = {
    "25": ("Separate evaluation and management service on the same day as a "
           "procedure. Legitimate when a genuinely separate problem was "
           "addressed; also the most common way a routine visit is billed twice."),
    "59": ("Distinct procedural service — the modifier used to bypass a bundling "
           "edit. Ask what made the services distinct: separate session, separate "
           "site, or separate encounter."),
    "XU": ("Unusual non-overlapping service; a more specific successor to "
           "modifier 59 and subject to the same question."),
    "XS": ("Separate structure; a more specific successor to modifier 59."),
    "XE": ("Separate encounter; a more specific successor to modifier 59."),
    "XP": ("Separate practitioner; a more specific successor to modifier 59."),
}

# Revenue codes whose meaning should agree with the HCPCS code on the line.
REVENUE_FAMILIES = {
    "025": ("pharmacy", ("J", "Q", "C", "S")),
    "026": ("IV therapy", ("J", "Q", "C", "S")),
    "027": ("medical/surgical supplies", ("A", "B", "E", "K", "L")),
    "029": ("durable medical equipment", ("E", "K", "L")),
    "030": ("laboratory", ("P", "G")),
    "031": ("laboratory pathology", ("P", "G")),
    "032": ("radiology diagnostic", ("R", "G")),
    "042": ("physical therapy", ("G", "S")),
    "045": ("emergency room", ()),
    "047": ("audiology", ("V",)),
}

# Codes that mean "we did not tell you what this is".
UNCLASSIFIED = {
    "J3490": "Unclassified drugs",
    "J3590": "Unclassified biologics",
    "J9999": "Not otherwise classified, antineoplastic drugs",
    "C9399": "Unclassified drugs or biologicals",
    "A9270": "Non-covered item or service",
    "E1399": "Durable medical equipment, miscellaneous",
    "A4649": "Surgical supply; miscellaneous",
}

# Multiple of the Medicare ASP limit at which we surface a line at all.
ASP_NOTICE_MULTIPLE = 3.0
ASP_HIGH_MULTIPLE = 10.0
DME_NOTICE_MULTIPLE = 3.0
DME_HIGH_MULTIPLE = 10.0
# NADAC is acquisition cost -- what the pharmacy paid the wholesaler -- not an
# allowed amount. A dispensing fee and a real margin sit on top of it legitimately,
# so a multiple that would be alarming against an ASP payment limit is unremarkable
# here. These thresholds are deliberately far higher than the ASP ones.
NADAC_NOTICE_MULTIPLE = 10.0
NADAC_HIGH_MULTIPLE = 50.0
# Multiple of the national average submitted charge for a DRG at which an
# inpatient bill is worth questioning.
DRG_NOTICE_MULTIPLE = 2.0


def _key(line):
    return (line.code.upper(), line.date.strip(), round(line.charge, 2), round(line.units, 3))


def rule_exact_duplicates(lines, ref, ctx=None):
    """Identical code + date + units + charge appearing more than once."""
    groups = defaultdict(list)
    for ln in lines:
        if ln.code or ln.desc:
            groups[_key(ln)].append(ln)
    out = []
    for (code, date, charge, units), grp in groups.items():
        if len(grp) < 2 or charge <= 0:
            continue
        extra = (len(grp) - 1) * charge
        label = code or grp[0].desc[:40]
        out.append(Finding(
            rule="exact_duplicate",
            severity="high",
            title=f"{label} billed {len(grp)}x identically",
            detail=(f"Lines {', '.join(str(l.idx) for l in grp)} are identical: "
                    f"{units:g} unit(s) at ${charge:,.2f}"
                    + (f" on {date}" if date else "")
                    + f". If this was a single service, ${extra:,.2f} is duplicated. "
                      "Ask the billing office to produce the medical record entry "
                      "supporting each occurrence separately."),
            lines=[l.idx for l in grp],
            citation="Structural finding -- derived from the bill itself, no external source.",
            amount=extra,
            recoverable=True,
        ))
    return out


def rule_same_code_same_day(lines, ref, ctx=None):
    """Same code repeated on one date with differing units/charges."""
    groups = defaultdict(list)
    for ln in lines:
        if ln.code and ln.date:
            groups[(ln.code.upper(), ln.date.strip())].append(ln)
    out = []
    for (code, date), grp in groups.items():
        if len(grp) < 2:
            continue
        if len({_key(l) for l in grp}) == 1:
            continue                      # already reported as an exact duplicate
        total = sum(l.charge for l in grp)
        out.append(Finding(
            rule="same_code_same_day",
            severity="notice",
            title=f"{code} appears {len(grp)}x on {date}",
            detail=(f"Lines {', '.join(str(l.idx) for l in grp)} bill {code} multiple "
                    f"times on the same date for a combined ${total:,.2f}. This can be "
                    "legitimate (repeat doses, bilateral procedures) but is also where "
                    "double-posting hides. Ask which record entry supports each line."),
            lines=[l.idx for l in grp],
            citation="Structural finding -- derived from the bill itself, no external source.",
            amount=total,
        ))
    return out


def rule_asp_benchmark(lines, ref, ctx=None):
    """Compare per-unit drug charges against Medicare Part B ASP payment limits."""
    if not ref.asp:
        return []
    cite = ref.cite("asp")
    # Identical priced lines are collapsed into one finding: repeating the same
    # markup paragraph per duplicate line buries the other findings.
    groups = defaultdict(list)
    for ln in lines:
        limit, dose = ref.asp_limit(ln.code)
        if limit is None or limit <= 0 or ln.charge <= 0:
            continue
        allowed = limit * (ln.units or 1)
        if allowed <= 0 or ln.charge / allowed < ASP_NOTICE_MULTIPLE:
            continue
        groups[(ln.code.upper(), round(ln.units, 3), round(ln.charge, 2))].append(ln)

    out = []
    for (code, units, charge), grp in groups.items():
        limit, dose = ref.asp_limit(code)
        allowed = limit * (units or 1)
        mult = charge / allowed
        sev = "high" if mult >= ASP_HIGH_MULTIPLE else "warn"
        desc = ref.describe(code)
        idxs = [l.idx for l in grp]
        where = (f"Line {idxs[0]}" if len(idxs) == 1
                 else f"Lines {', '.join(str(i) for i in idxs)} (each)")
        out.append(Finding(
            rule="asp_benchmark",
            severity=sev,
            title=f"{code} billed at {mult:,.0f}x the Medicare allowed amount",
            detail=(f"{where}: {units:g} unit(s) of {code}"
                    f"{' (' + desc + ')' if desc else ''} "
                    f"billed at ${charge:,.2f}. Medicare's Part B payment limit is "
                    f"${limit:,.4f}{' per ' + dose if dose else ''}, so the same "
                    f"quantity allows ${allowed:,.2f}. "
                    "A hospital is not required to bill Medicare rates, so this is not "
                    "by itself an error -- but a multiple this large is worth disputing, "
                    "and is strong leverage in a request for a self-pay or charity "
                    "adjustment."),
            lines=idxs,
            citation=cite,
            amount=charge * len(grp),
        ))
    return out


def rule_unclassified_codes(lines, ref, ctx=None):
    """Codes that decline to say what was provided."""
    cite = ref.cite("hcpcs")
    out = []
    for ln in lines:
        code = (ln.code or "").upper()
        if code not in UNCLASSIFIED:
            continue
        why = ("This code means the item was not identified. You are entitled to know "
               "what you were charged for; ask for the NDC number (for a drug) or the "
               "manufacturer invoice (for a supply or device).")
        if code == "A9270":
            why = ("A9270 means the payer treats this as non-covered. Confirm you were "
                   "given advance written notice before it was provided -- without it, "
                   "you may not be responsible for the charge.")
        out.append(Finding(
            rule="unclassified_code",
            severity="warn",
            title=f"{code} is an unspecified code ({UNCLASSIFIED[code]})",
            detail=f"Line {ln.idx}, ${ln.charge:,.2f}. {why}",
            lines=[ln.idx],
            citation=cite,
            amount=ln.charge,
        ))
    return out


def rule_missing_code(lines, ref, ctx=None):
    """Charges with no procedure code -- you have a right to an itemized bill."""
    bad = [ln for ln in lines if not ln.code and ln.charge > 0]
    if not bad:
        return []
    total = sum(l.charge for l in bad)
    n = len(bad)
    return [Finding(
        rule="missing_code",
        severity="warn",
        title=(f"{n} charge line carries no procedure code" if n == 1
               else f"{n} charge lines carry no procedure code"),
        detail=(f"{'Line' if n == 1 else 'Lines'} "
                f"{', '.join(str(l.idx) for l in bad[:20])}"
                f"{' ...' if n > 20 else ''} "
                f"{'totals' if n == 1 else 'total'} ${total:,.2f} with no "
                "HCPCS/CPT code. A summary bill is not an itemized bill. Request a "
                "fully itemized statement showing a code, units and charge for every "
                "line before paying -- this alone often changes the total."),
        lines=[l.idx for l in bad],
        citation="Structural finding -- derived from the bill itself, no external source.",
        amount=total,
    )]


def rule_unknown_code(lines, ref, ctx=None):
    """Codes that are neither HCPCS Level II nor CPT/CDT-shaped."""
    if not ref.available:
        return []
    out = []
    for ln in lines:
        code = (ln.code or "").upper()
        if not code or classify(code) != "unknown":
            continue
        out.append(Finding(
            rule="unknown_code",
            severity="notice",
            title=f"'{code}' is not a recognised HCPCS or CPT code",
            detail=(f"Line {ln.idx}, ${ln.charge:,.2f}. This looks like an internal "
                    "chargemaster code rather than a standard billing code. Ask the "
                    "billing office which HCPCS/CPT code was submitted to your insurer "
                    "for this line -- the two should correspond."),
            lines=[ln.idx],
            citation=ref.cite("hcpcs"),
            amount=ln.charge,
        ))
    return out


def rule_licensed_codes(lines, ref, ctx=None):
    """CPT/CDT lines we deliberately cannot benchmark in the open tier."""
    ama = [ln for ln in lines if classify(ln.code) == "ama"]
    ada = [ln for ln in lines if classify(ln.code) == "ada"]
    out = []
    if ama:
        out.append(Finding(
            rule="licensed_cpt",
            severity="info",
            title=f"{len(ama)} line(s) use CPT codes, which this tier cannot benchmark",
            detail=(f"Lines {', '.join(str(l.idx) for l in ama[:20])}"
                    f"{' ...' if len(ama) > 20 else ''} total "
                    f"${sum(l.charge for l in ama):,.2f}. CPT codes and descriptions are "
                    "copyright the American Medical Association, so itemize does not "
                    "redistribute them. To check these for unbundling and units limits, "
                    "run `itemize ncci` locally, which shows you CMS's AMA licence and "
                    "downloads the NCCI edit files under your own acceptance."),
            lines=[l.idx for l in ama],
            citation="HCPC record layout, CMS: CPT-4 codes and descriptions are used "
                     "under the CMS/AMA agreement; other use violates the AMA copyright.",
        ))
    if ada:
        out.append(Finding(
            rule="licensed_cdt",
            severity="info",
            title=f"{len(ada)} line(s) use dental CDT codes",
            detail=("CDT codes are copyright the American Dental Association and are "
                    "likewise excluded from this tool's bundled data."),
            lines=[l.idx for l in ada],
            citation="HCPC record layout, CMS: Level II D-series descriptors are "
                     "copyright the American Dental Association.",
        ))
    return out



def rule_dmepos_benchmark(lines, ref, ctx=None):
    """Compare equipment and supply charges against the Medicare DMEPOS schedule."""
    if not ref.dmepos:
        return []
    cite = ref.cite("dmepos")
    groups = defaultdict(list)
    for ln in lines:
        fee, _cat = ref.dmepos_fee(ln.code)
        if not fee or fee <= 0 or ln.charge <= 0:
            continue
        allowed = fee * (ln.units or 1)
        if allowed <= 0 or ln.charge / allowed < DME_NOTICE_MULTIPLE:
            continue
        groups[(ln.code.upper(), round(ln.units, 3), round(ln.charge, 2))].append(ln)

    out = []
    for (code, units, charge), grp in groups.items():
        fee, cat = ref.dmepos_fee(code)
        allowed = fee * (units or 1)
        mult = charge / allowed
        desc = ref.describe(code)
        idxs = [l.idx for l in grp]
        where = (f"Line {idxs[0]}" if len(idxs) == 1
                 else f"Lines {', '.join(str(i) for i in idxs)} (each)")
        out.append(Finding(
            rule="dmepos_benchmark",
            severity="high" if mult >= DME_HIGH_MULTIPLE else "warn",
            title=f"{code} billed at {mult:,.0f}x the Medicare equipment allowance",
            detail=(f"{where}: {units:g} unit(s) of {code}"
                    f"{' (' + desc + ')' if desc else ''} billed at ${charge:,.2f}. "
                    f"Medicare's DMEPOS allowance is ${fee:,.2f} per unit (median "
                    f"across states), so the same quantity allows ${allowed:,.2f}. "
                    "Equipment and supplies are frequently marked up far above the "
                    "schedule; ask whether the item was rented or purchased, and "
                    "whether a rental cap applies."),
            lines=idxs,
            citation=cite,
            amount=charge * len(grp),
        ))
    return out


def rule_nadac_benchmark(lines, ref, ctx=None):
    """Compare drug lines carrying an NDC against what pharmacies pay to buy them.

    This reaches the outpatient pharmacy side that Part B ASP cannot: ASP covers
    roughly 900 physician-administered J-codes, NADAC covers ~32,000 NDCs. Where
    a line has both, ASP wins and this rule stands down -- reporting the same
    dollars twice would inflate the implicated total.
    """
    if not ref.nadac:
        return []
    cite = ref.cite("nadac")
    groups = defaultdict(list)
    for ln in lines:
        if not ln.ndc:
            continue
        if ref.asp_limit(ln.code)[0]:
            continue                      # already priced against ASP
        rec = ref.nadac_price(ln.ndc)
        if not rec:
            continue
        price = rec[0]
        if not price or price <= 0 or ln.charge <= 0:
            continue
        allowed = price * (ln.units or 1)
        if allowed <= 0 or ln.charge / allowed < NADAC_NOTICE_MULTIPLE:
            continue
        groups[(ln.ndc, round(ln.units, 3), round(ln.charge, 2))].append(ln)

    out = []
    for (ndc, units, charge), grp in groups.items():
        price, unit, desc, brand = ref.nadac_price(ndc)
        allowed = price * (units or 1)
        mult = charge / allowed
        idxs = [l.idx for l in grp]
        where = (f"Line {idxs[0]}" if len(idxs) == 1
                 else f"Lines {', '.join(str(i) for i in idxs)} (each)")
        unit_label = {"EA": "each", "ML": "per mL", "GM": "per gram"}.get(unit, unit or "per unit")
        out.append(Finding(
            rule="nadac_benchmark",
            severity="high" if mult >= NADAC_HIGH_MULTIPLE else "warn",
            title=f"NDC {ndc} billed at {mult:,.0f}x what pharmacies pay for it",
            detail=(f"{where}: {units:g} unit(s) of "
                    f"{desc or 'this drug'} (NDC {ndc}) billed at ${charge:,.2f}. "
                    f"The national average acquisition cost is ${price:,.5f} "
                    f"{unit_label}, so the same quantity costs about ${allowed:,.2f} "
                    "to buy. NADAC is what a pharmacy pays a wholesaler, not an "
                    "allowed amount -- a dispensing fee and a real margin belong on "
                    "top of it, so this is not an error. A multiple this large is "
                    "still worth asking about, and is strong support for a "
                    "financial-assistance request. "
                    "Check the units: NADAC prices " + unit_label + ", and a bill "
                    "does not always count units the same way."),
            lines=idxs,
            citation=cite,
            amount=charge * len(grp),
        ))
    return out


def rule_drg_benchmark(lines, ref, ctx=None):
    """Compare an inpatient bill against the national average charge for its DRG.

    Whole-bill context, never a line-item dispute: amount stays zero so this can
    never be added to a recoverable total.
    """
    if ctx is None or not getattr(ctx, "drg", "") or not ref.drg:
        return []
    stats = ref.drg_stats(ctx.drg)
    code = normalize_drg(ctx.drg)
    if not stats:
        return [Finding(
            rule="drg_unknown",
            severity="notice",
            title=f"DRG {code} is not in the national averages we ship",
            detail=("The CMS file covers DRGs with enough Medicare discharges to "
                    "publish without identifying patients. Yours is not in it, so "
                    "no comparison is possible here. Confirm the DRG with the "
                    "billing office -- it is form locator 71 on a UB-04."),
            lines=[],
            citation=ref.cite("drg"),
        )]

    total = sum(l.charge for l in lines)
    avg = stats.get("charge") or 0
    if total <= 0 or avg <= 0:
        return []
    mult = total / avg
    pay = stats.get("pay")
    high = mult >= DRG_NOTICE_MULTIPLE
    body = (f"Your bill totals ${total:,.2f}. Across {stats.get('n', 0):,} Medicare "
            f"discharges nationally, hospitals submitted an average charge of "
            f"${avg:,.2f} for DRG {code} ({stats.get('desc', '')})"
            + (f", and were paid an average of ${pay:,.2f}." if pay else ".")
            + " Submitted charges are list prices that almost nobody pays; the gap "
              "between the two columns is the ordinary state of hospital billing, "
              "not evidence of an error.")
    if high:
        body += (f" Yours is {mult:,.1f}x the national average charge, which is "
                 "worth asking about — but confirm first that this statement covers "
                 "the whole stay and nothing else, because a partial bill or an "
                 "added professional fee will skew the comparison.")
    else:
        body += (f" Yours is {mult:,.2f}x that average, which is unremarkable.")
    return [Finding(
        rule="drg_benchmark",
        severity="notice" if high else "info",
        title=(f"Bill is {mult:,.1f}x the national average charge for DRG {code}"
               if high else f"Bill is in line with the national average for DRG {code}"),
        detail=body,
        lines=[],
        citation=ref.cite("drg"),
    )]


def rule_stale_reference_data(lines, ref, ctx=None):
    """Say out loud when the shipped reference data has gone out of date.

    Every price finding on this page is only as good as the file behind it, and
    a stale file fails silently: the numbers still look authoritative. CMS
    updates HCPCS, ASP and DMEPOS quarterly and NADAC weekly. This is the same
    discipline the state-law layer already applies to itself.
    """
    stale = ref.stale_datasets()
    if not stale:
        return []
    worst = max(age - lim for _n, age, lim in stale)
    names = ", ".join(f"{n} ({age} days old)" for n, age, lim in stale)
    return [Finding(
        rule="stale_reference_data",
        severity="warn" if worst > 180 else "notice",
        title=f"{len(stale)} reference dataset(s) are out of date",
        detail=(f"{names}. Any price comparison drawn from these is being made "
                "against figures that CMS has since replaced. Re-run "
                "`python3 tools/build_data.py --out web/data` before relying on a "
                "benchmark, and re-check any finding you have already sent. "
                "Structural findings — duplicates, arithmetic, missing codes — do "
                "not depend on this data and are unaffected."),
        lines=[],
        citation=("Ages computed from the `retrieved` date each source records in "
                  "web/data/manifest.json, against the refresh cadence CMS "
                  "publishes on: " + ", ".join(f"{k} {v}d" for k, v in
                                               sorted(REFRESH_DAYS.items())) + "."),
    )]


def rule_modifier_flags(lines, ref, ctx=None):
    """Modifiers are parsed off the bill; these are the ones worth asking about."""
    out = []
    for ln in lines:
        for mod in (ln.modifiers or []):
            m = mod.strip().upper()
            if m not in MODIFIER_NOTES:
                continue
            out.append(Finding(
                rule="modifier_flag",
                severity="notice",
                title=f"Line {ln.idx} carries modifier {m}",
                detail=(f"{ln.code or 'This line'} was billed with modifier {m}. "
                        f"{MODIFIER_NOTES[m]} Ask the billing office to state, in "
                        "writing, what justified the modifier on this line."),
                lines=[ln.idx],
                citation="Structural finding -- read from the modifier field on your "
                         "own bill; no external source.",
                amount=ln.charge,
            ))
    return out


def rule_revenue_code_mismatch(lines, ref, ctx=None):
    """A revenue code and its HCPCS code should describe the same kind of thing."""
    out = []
    for ln in lines:
        rev = re.sub(r"\D", "", ln.revenue_code or "")
        code = (ln.code or "").upper()
        if len(rev) < 3 or not code or classify(code) != "public":
            continue
        fam = REVENUE_FAMILIES.get(rev[:3])
        if not fam:
            continue
        label, prefixes = fam
        if not prefixes or code[0] in prefixes:
            continue
        out.append(Finding(
            rule="revenue_code_mismatch",
            severity="notice",
            title=f"Line {ln.idx}: revenue code {rev} ({label}) does not match code {code}",
            detail=(f"Revenue code {rev} describes {label}, but {code} is not a code "
                    "normally billed under it. This is sometimes a clerical mismatch "
                    "and sometimes a charge posted to the wrong department. Ask which "
                    "department provided the item and what code was sent to your plan."),
            lines=[ln.idx],
            citation="Structural finding -- compares the revenue code and HCPCS code "
                     "on your own bill; no external source.",
            amount=ln.charge,
        ))
    return out


def rule_unit_price_arithmetic(lines, ref, ctx=None):
    """units x unit price should equal the line charge."""
    out = []
    for ln in lines:
        if ln.unit_price <= 0 or ln.units <= 0 or ln.charge <= 0:
            continue
        expected = ln.unit_price * ln.units
        diff = ln.charge - expected
        if abs(diff) <= max(0.02, expected * 0.005):
            continue
        out.append(Finding(
            rule="unit_price_arithmetic",
            severity="warn",
            title=f"Line {ln.idx} does not add up",
            detail=(f"{ln.units:g} unit(s) at ${ln.unit_price:,.2f} each is "
                    f"${expected:,.2f}, but the line is charged at ${ln.charge:,.2f} — "
                    f"a difference of ${abs(diff):,.2f}. Ask them to recalculate the "
                    "line."),
            lines=[ln.idx],
            citation="Structural finding -- arithmetic on your own bill; no external "
                     "source.",
            amount=abs(diff),
            recoverable=diff > 0,
        ))
    return out


RULES = (
    rule_exact_duplicates,
    rule_asp_benchmark,
    rule_nadac_benchmark,
    rule_dmepos_benchmark,
    rule_drg_benchmark,
    rule_stale_reference_data,
    rule_modifier_flags,
    rule_revenue_code_mismatch,
    rule_unit_price_arithmetic,
    rule_unclassified_codes,
    rule_missing_code,
    rule_same_code_same_day,
    rule_unknown_code,
    rule_licensed_codes,
)

SEV_ORDER = {"high": 0, "warn": 1, "notice": 2, "info": 3}


# Findings whose importance depends on who is actually paying.
PRICE_RULES = {"asp_benchmark", "dmepos_benchmark", "nadac_benchmark"}


def apply_context(findings, ctx):
    """Reweight findings for who is exposed to the charge.

    An insured reader past their deductible pays the plan's negotiated rate, so
    a "300x Medicare" headline is close to irrelevant to them -- while for a
    self-pay reader it is the most useful thing on the page. Showing everyone
    the same ordering overstates the tool's case to the people it helps least.
    """
    if ctx is None:
        return findings
    for f in findings:
        if f.rule not in PRICE_RULES:
            continue
        if ctx.insurer_is_paying:
            f.severity = "info"
            f.detail += (" Because you are insured and have met your deductible, you "
                         "pay your plan's negotiated rate rather than this charge, so "
                         "this comparison is context rather than a bill to dispute.")
        elif ctx.pays_full_allowed:
            f.detail += (" You told us you are exposed to the full amount, so this "
                         "gap is money you would actually pay.")
    return findings


# What to do first.
#
# Findings arrive sorted by severity, which is a reading order, not an action
# plan: a reader with nine findings still cannot tell which one to act on. These
# are ordered by what actually moves a balance, and capped -- a list of nine
# "next steps" is the same problem again.
#
# Order matters and is not severity order. Asking for an itemized bill outranks
# everything because the rest cannot be checked without one.
ACTION_SPECS = (
    ("itemized", ("missing_code",), "itemized",
     "Ask for a fully itemized bill first",
     "Some lines carry no procedure code, so there is nothing to check them "
     "against. Every other question here is worth more once you have a "
     "statement showing a code, a quantity and a charge on every line."),
    ("eob", ("eob_line_mismatch", "eob_total_mismatch", "eob_line_absent"), "dispute",
     "Challenge the bill against your own EOB",
     "Your plan has already decided what you owe. A provider billing more than "
     "that is the clearest error there is, and your insurer will not catch it "
     "for you."),
    ("gfe", ("right_gfe_exceeded",), "gfe",
     "Start the federal dispute process",
     "This bill exceeds your Good Faith Estimate by $400 or more, which opens "
     "patient-provider dispute resolution. Almost nobody uses it, and it costs "
     "the provider more than settling."),
    ("dispute", (), "dispute",
     "Dispute the duplicated and mis-added lines",
     "These are arithmetic and duplication, not price arguments — the kind of "
     "finding a billing office corrects rather than debates."),
    ("nsa", ("right_nsa_emergency", "right_nsa_facility"), None,
     "Assert your surprise-billing protection",
     "Federal law may prohibit this balance bill outright, which outranks every "
     "coding question on this page."),
    ("charity", ("right_charity_care", "state_charity_all_hospitals",
                 "state_charity_threshold", "state_assistance_program"), "assistance",
     "Apply for financial assistance",
     "A financial assistance policy can cover the whole balance rather than a "
     "line of it, and applying does not stop you disputing anything else."),
    ("cash", ("mrf_cash_price",), None,
     "Ask for the hospital's own published cash price",
     "This is the hospital's own attested number rather than a Medicare "
     "comparison, so it is the hardest one for a billing office to wave away."),
)

MAX_ACTIONS = 3


def next_actions(findings, ctx=None, limit=MAX_ACTIONS):
    """The few things worth doing first, most valuable first.

    Returns dicts with a stable `key` so the browser and the evidence packet
    present the same plan. Mirrors nextActions in web/rules.js.
    """
    fired = {f.rule for f in findings}
    out = []
    for key, rules_, letter, title, why in ACTION_SPECS:
        if key == "dispute":
            hits = [f for f in findings if f.recoverable and f.lines]
            if not hits:
                continue
            amount = sum(f.amount for f in hits)
            out.append({"key": key, "title": title, "why": why, "letter": letter,
                        "amount": round(amount, 2),
                        "lines": sorted({i for f in hits for i in f.lines})})
            continue
        if not (fired & set(rules_)):
            continue
        hits = [f for f in findings if f.rule in rules_]
        out.append({"key": key, "title": title, "why": why, "letter": letter,
                    "amount": 0.0,
                    "lines": sorted({i for f in hits for i in f.lines})})
    return out[:limit]


def audit(lines, ref, ctx=None, eob_rows=None):
    findings = []
    for rule in RULES:
        findings.extend(rule(lines, ref, ctx))

    if ctx is not None:
        from . import rights, states
        findings.extend(rights.evaluate(lines, ref, ctx))
        findings.extend(states.evaluate(lines, ref, ctx, getattr(ref, "dir", None)))

    if eob_rows:
        from . import eob as eob_mod
        findings.extend(eob_mod.cross_check(lines, eob_rows, ref))

    findings = apply_context(findings, ctx)
    findings.sort(key=lambda f: (SEV_ORDER.get(f.severity, 9), -f.amount))
    return findings
