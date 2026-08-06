"""Explanation of Benefits cross-check.

This is the highest-confidence check in the project and it needs no federal
data at all: if the provider bills you more than the EOB says you owe, that is
a straightforward, directly disputable error.

Insurers adjudicate the *claim*. You receive the *itemized statement*. They are
different documents, and errors that survive adjudication show up here.
"""
from __future__ import annotations

import csv
import io
import re

from .model import Finding

# Header aliases seen on EOB exports and printed EOBs.
EOB_ALIASES = {
    "code": ("code", "hcpcs", "cpt", "procedure", "service code", "proc"),
    "billed": ("billed", "charged", "charges", "amount billed", "provider charge",
               "submitted", "billed amount"),
    "allowed": ("allowed", "allowed amount", "plan allowance", "eligible",
                "negotiated", "contracted"),
    "plan_paid": ("plan paid", "paid", "insurance paid", "payer paid", "we paid",
                  "benefit"),
    "patient": ("patient responsibility", "you owe", "your responsibility",
                "member responsibility", "patient resp", "you may owe",
                "responsibility", "patient"),
    "date": ("date", "service date", "date of service", "dos"),
}

RE_MONEY = re.compile(r"-?\$?\s*([\d,]+\.\d{2}|[\d,]+)")


def _money(s):
    m = RE_MONEY.search(str(s or ""))
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _map(header):
    out = {}
    for i, h in enumerate(header):
        k = (h or "").strip().lower()
        for field, names in EOB_ALIASES.items():
            if field in out:
                continue
            if k in names:
                out[field] = i
                break
    for i, h in enumerate(header):
        k = (h or "").strip().lower()
        for field, names in EOB_ALIASES.items():
            if field in out:
                continue
            if any(n in k for n in names):
                out[field] = i
                break
    return out


def parse_eob(text):
    """Return [{code, date, billed, allowed, plan_paid, patient}]."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",\t|;")
    except csv.Error:
        dialect = csv.excel
    rows = [r for r in csv.reader(io.StringIO(text), dialect) if any(c.strip() for c in r)]
    if not rows:
        return []

    hdr_i, cols = None, {}
    for i, row in enumerate(rows[:25]):
        m = _map(row)
        if "patient" in m and ("code" in m or "allowed" in m):
            hdr_i, cols = i, m
            break
    if hdr_i is None:
        return []

    out = []
    for row in rows[hdr_i + 1:]:
        def g(f):
            i = cols.get(f)
            return row[i].strip() if i is not None and i < len(row) else ""
        code = g("code").upper()
        patient = _money(g("patient"))
        if patient is None and not code:
            continue
        if re.search(r"\b(total|subtotal)\b", " ".join(row), re.I) and not code:
            continue
        out.append({
            "code": code,
            "date": g("date"),
            "billed": _money(g("billed")),
            "allowed": _money(g("allowed")),
            "plan_paid": _money(g("plan_paid")),
            "patient": patient,
        })
    return out


def cross_check(lines, eob_rows, ref, tolerance=0.01):
    """Compare an itemized bill against EOB patient-responsibility figures."""
    if not eob_rows:
        return []

    by_code = {}
    for r in eob_rows:
        if r["code"]:
            by_code.setdefault(r["code"].upper(), []).append(r)

    findings = []
    billed_total = sum(l.charge for l in lines)
    eob_patient_total = sum(r["patient"] for r in eob_rows if r["patient"] is not None)

    # Line-level: provider billing you more than the EOB says you owe.
    seen = set()
    for ln in lines:
        code = (ln.code or "").upper()
        rows = by_code.get(code)
        if not rows or code in seen:
            continue
        seen.add(code)
        resp = sum(r["patient"] for r in rows if r["patient"] is not None)
        charged = sum(l.charge for l in lines if (l.code or "").upper() == code)
        if resp is None:
            continue
        over = charged - resp
        if over > max(tolerance, resp * 0.01):
            findings.append(Finding(
                rule="eob_line_mismatch",
                severity="high",
                title=f"{code}: billed ${charged:,.2f} but your EOB says you owe ${resp:,.2f}",
                detail=(
                    f"The itemized bill charges ${charged:,.2f} for {code}, while your "
                    f"Explanation of Benefits puts your responsibility at ${resp:,.2f} — "
                    f"a difference of ${over:,.2f}. If your plan has already adjudicated "
                    "this line, the provider generally may not bill you above the "
                    "patient-responsibility figure. Send the EOB page with this line "
                    "highlighted and ask them to adjust or to explain the difference in "
                    "writing."),
                lines=[l.idx for l in lines if (l.code or "").upper() == code],
                citation="Cross-check between your itemized bill and your own EOB; "
                         "no external data source.",
                amount=over,
                recoverable=True,
            ))

    # Whole-bill: the statement asks for more than the EOB totals.
    over_total = billed_total - eob_patient_total
    if eob_patient_total > 0 and over_total > max(1.0, eob_patient_total * 0.02):
        findings.append(Finding(
            rule="eob_total_mismatch",
            severity="high",
            title=(f"Statement totals ${billed_total:,.2f}; EOB patient responsibility "
                   f"totals ${eob_patient_total:,.2f}"),
            detail=(
                f"Across every line, your EOB puts your responsibility at "
                f"${eob_patient_total:,.2f} but this statement asks for "
                f"${billed_total:,.2f}, a difference of ${over_total:,.2f}. A statement "
                "showing full charges rather than your post-adjudication balance is "
                "common and is not always an error — but you should not pay from the "
                "statement until the two agree. Ask for a statement that reflects the "
                "processed claim."),
            lines=[],
            citation="Cross-check between your itemized bill and your own EOB; "
                     "no external data source.",
            amount=over_total,
        ))

    # Lines on the bill that the insurer never saw.
    unseen = [l for l in lines if l.code and (l.code or "").upper() not in by_code]
    if by_code and unseen:
        total = sum(l.charge for l in unseen)
        findings.append(Finding(
            rule="eob_line_absent",
            severity="warn",
            title=f"{len(unseen)} billed line(s) do not appear on your EOB",
            detail=(
                f"Lines {', '.join(str(l.idx) for l in unseen[:20])}"
                f"{' …' if len(unseen) > 20 else ''} totalling ${total:,.2f} are on the "
                "bill but not on the EOB. Either they were never submitted to your "
                "plan, or they were submitted under different codes. Ask which — if a "
                "claim was never filed, ask them to file it before billing you."),
            lines=[l.idx for l in unseen],
            citation="Cross-check between your itemized bill and your own EOB; "
                     "no external data source.",
            amount=total,
        ))
    return findings
