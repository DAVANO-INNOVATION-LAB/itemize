"""Practice bills with seeded errors, and a key.

Why this is in the tool
-----------------------
Clinicians are not taught what they bill. Outpatient coding error rates have
been reported as high as 91% in Medicare audits, and the standing critique of
health-systems-science teaching is that it is didactic and short on hands-on
practice. The framework exists; the practice instrument does not.

itemize is already the awkward half of that instrument -- free, offline, no
account, every finding sourced -- and it needs no patient data to be useful in
a classroom. What was missing was bills to practise on. Real ones are PHI and
cannot be shipped; these are synthetic, hand-written, and every error in them
is deliberate and recorded in `seeded`.

It doubles as a coverage check. `itemize teach score` runs the real engine over
each case and reports which seeded errors it actually catches -- so a rule that
quietly stops firing shows up as a case the tool can no longer solve. The test
suite asserts that, which is why the expected counts are not written down here:
the key records what is wrong with each bill, not what the tool happens to find.
"""
from __future__ import annotations

CASES = (
    {
        "id": "ed-visit",
        "title": "Emergency department visit, self-pay",
        "level": "introductory",
        "setup": ("An uninsured patient is billed after a single emergency visit. "
                  "Nothing here is exotic; it is the ordinary shape of the problem."),
        "context": {"insured": False, "nonprofit_hospital": True, "emergency": True},
        "bill": """Date of Service,Code,NDC,Description,Qty,Unit Price,Charges
2026-03-14,99283,,EMERGENCY DEPT VISIT LEVEL 3,1,1842.00,1842.00
2026-03-14,J1885,00409-3799-01,KETOROLAC TROMETHAMINE INJ 15MG,2,90.00,180.00
2026-03-14,J1885,00409-3799-01,KETOROLAC TROMETHAMINE INJ 15MG,2,90.00,180.00
2026-03-14,A4550,,SURGICAL TRAY,1,68.00,68.00
2026-03-14,A9270,,SELF ADMIN DRUG - NON COVERED,1,142.50,142.50
2026-03-14,,,ROOM AND BOARD SEMI PRIVATE,1,2310.00,2310.00
""",
        "seeded": [
            ("exact_duplicate", [2, 3],
             "The ketorolac line is posted twice, identically. This is the only kind "
             "of finding here that is directly recoverable."),
            ("asp_benchmark", [2, 3],
             "Ketorolac is billed far above the Medicare Part B payment limit. "
             "Leverage, not an error -- a hospital may bill what it likes."),
            ("missing_code", [6],
             "Room and board carries no procedure code, so it cannot be checked "
             "against anything. Asking for a full itemisation outranks everything."),
            ("unclassified_code", [5],
             "A9270 means the payer treats the item as non-covered. Ask whether "
             "advance written notice was given before it was provided."),
            ("right_charity_care", [],
             "A non-profit hospital must have a financial assistance policy and cap "
             "what it charges patients who qualify. This covers the whole balance."),
        ],
    },
    {
        "id": "summary-statement",
        "title": "A summary statement pretending to be an itemized bill",
        "level": "introductory",
        "setup": ("Departmental totals with no codes. The teaching point is that the "
                  "correct first move is to reject the document, not to analyse it."),
        "context": {"insured": False},
        "bill": """Date,Code,Description,Qty,Charges
2026-04-02,,PHARMACY,1,4820.15
2026-04-02,,LABORATORY,1,2310.00
2026-04-02,,RADIOLOGY,1,3990.00
2026-04-02,,SUPPLIES,1,1204.55
""",
        "seeded": [
            ("missing_code", [1, 2, 3, 4],
             "Every line is a department total. No check downstream of this is "
             "meaningful until an itemized statement arrives."),
        ],
    },
    {
        "id": "pharmacy-markup",
        "title": "Outpatient pharmacy lines priced by NDC",
        "level": "intermediate",
        "setup": ("Drug lines carrying NDCs rather than J-codes -- the outpatient side "
                  "that Part B ASP cannot see. Requires NADAC to be built."),
        "context": {"insured": True, "deductible_met": False},
        "bill": """Date,Code,NDC,Description,Qty,Unit Price,Charges
2026-05-02,PHRM1002,00172-3761-70,LISINOPRIL 40 MG TABLET,30,13.75,412.50
2026-05-02,PHRM1044,00093-7214-10,LISINOPRIL 10 MG TABLET,30,0.09,2.70
2026-05-02,A4550,,SURGICAL TRAY,2,50.00,400.00
""",
        "seeded": [
            ("nadac_benchmark", [1],
             "Billed at a large multiple of what pharmacies pay to acquire the drug. "
             "NADAC is acquisition cost, so an ordinary margin is expected -- this "
             "is well past ordinary."),
            ("unit_price_arithmetic", [3],
             "2 units at $50.00 is $100.00, not $400.00. Arithmetic, not pricing: "
             "the kind of thing a billing office corrects rather than debates."),
            ("unknown_code", [1, 2],
             "PHRM1002 and PHRM1044 are internal chargemaster codes. Ask which "
             "HCPCS/CPT code was submitted to the plan."),
        ],
    },
    {
        "id": "eob-mismatch",
        "title": "Provider billing more than the EOB allows",
        "level": "intermediate",
        "setup": ("The strongest check for an insured patient, and one no federal "
                  "data file is needed for. The plan has already decided."),
        "context": {"insured": True, "deductible_met": True},
        "bill": """Date,Code,Description,Qty,Charges
2026-06-11,J1885,KETOROLAC TROMETHAMINE INJ 15MG,1,90.00
2026-06-11,A4550,SURGICAL TRAY,1,68.00
2026-06-11,E0114,CRUTCHES UNDERARM PAIR,1,124.00
""",
        "eob": """Code,Description,Allowed,Plan Paid,Patient Responsibility
J1885,KETOROLAC,42.00,37.00,5.00
A4550,SURGICAL TRAY,30.00,24.00,6.00
""",
        "seeded": [
            ("eob_line_mismatch", [1, 2],
             "The provider is billing the full charge on lines your plan already "
             "adjudicated to a much smaller patient responsibility."),
            ("eob_line_absent", [3],
             "The crutches never appear on the EOB at all -- either never submitted, "
             "or submitted under a different code."),
        ],
    },
    {
        "id": "equipment",
        "title": "Durable medical equipment and a mis-posted revenue code",
        "level": "advanced",
        "setup": ("Equipment markups and a charge posted to the wrong department. "
                  "Both are questions to ask, not errors to assert."),
        "context": {"insured": False},
        "bill": """Date,Code,Description,Qty,Charges,Rev Code,Mod
2026-07-08,E0114,CRUTCHES UNDERARM PAIR,1,890.00,0290,
2026-07-08,J1885,KETOROLAC TROMETHAMINE INJ 15MG,1,140.00,0300,
2026-07-08,99213,OFFICE VISIT LEVEL 3,1,320.00,0510,25
""",
        "seeded": [
            ("dmepos_benchmark", [1],
             "Crutches billed at many times the Medicare DMEPOS allowance. Ask "
             "whether the item was rented or purchased and whether a cap applies."),
            ("revenue_code_mismatch", [2],
             "A drug code posted under a laboratory revenue code. Sometimes "
             "clerical, sometimes a charge posted to the wrong department."),
            ("modifier_flag", [3],
             "Modifier 25 on the same day as a procedure: legitimate when a "
             "genuinely separate problem was addressed, and also the most common "
             "way a routine visit is billed twice."),
            ("licensed_cpt", [3],
             "The office visit is CPT-coded, so the open tier cannot benchmark it "
             "at all. Knowing what you cannot see is part of reading a bill."),
        ],
    },
)

BY_ID = {c["id"]: c for c in CASES}


def get(case_id):
    return BY_ID.get(str(case_id).strip().lower())


def seeded_rules(case):
    return [r for r, _lines, _why in case["seeded"]]


def context_for(case):
    from .context import Context
    return Context(**case.get("context", {}))


def score(case, findings):
    """Which seeded errors the engine actually found.

    Returns (hits, misses, extra) where extra is every rule that fired without
    being seeded. Extras are not failures -- a real engine notices things the
    author of a practice bill did not plan -- but they belong in the report so
    an instructor is never surprised by the output in front of a room.
    """
    fired = {f.rule for f in findings}
    hits, misses = [], []
    for rule, lines, why in case["seeded"]:
        (hits if rule in fired else misses).append((rule, lines, why))
    extra = sorted(fired - set(seeded_rules(case)))
    return hits, misses, extra
