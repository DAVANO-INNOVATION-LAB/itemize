"""Hospital price transparency machine-readable files.

Since 1 January 2026 (enforced from 1 April 2026) the CY2026 OPPS/ASC rule
requires hospitals to publish payer-specific rates as ACTUAL DOLLAR AMOUNTS
rather than estimates or formulas like "120% of Medicare", alongside a gross
charge and a discounted cash price, under a named attestation. That change is
what makes this file useful to a patient: before it, a cash price could be an
algorithm you could not evaluate.

For a self-pay reader this is the strongest finding itemize can produce. "Your
hospital publishes a cash price of $X for this code and billed you $Y" is not a
Medicare comparison that a billing office can wave away as "we don't bill
Medicare rates" -- it is the hospital's own published number, under its own
attestation.

Why this is not in rules.py
---------------------------
Every rule in rules.py is mirrored in web/rules.js and diffed by the parity
suite. This is not a rule: it needs a multi-gigabyte file the reader supplies,
so it is an optional tier the CLI appends, exactly like `itemize ncci`.

On CPT
------
An MRF contains CPT codes, because the hospital publishes them. itemize reads
the reader's own hospital's file on the reader's own machine and holds those
codes in memory to compare against their bill. Nothing from an MRF is ever
written into this repository or shipped with it. See NOTICE.md.

No third-party dependencies: the JSON reader below is an incremental brace
scanner, because these files do not fit in memory and `json.load` would try.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import urllib.request

from .model import Finding

UA = "itemize/0.1 (+open medical bill auditing)"
CHUNK = 1 << 20            # 1 MiB


# ---------------------------------------------------------------- opening
def _open(src):
    """Return a binary file-like for a URL or a local path."""
    if re.match(r"^https?://", src, re.I):
        req = urllib.request.Request(src, headers={"User-Agent": UA})
        return urllib.request.urlopen(req, timeout=300)
    return open(src, "rb")


def _looks_json(src, head):
    if src.lower().endswith(".json"):
        return True
    if src.lower().endswith((".csv", ".txt")):
        return False
    return head.lstrip()[:1] in ("{", "[")


# ------------------------------------------------------------ json reader
def _iter_json_objects(stream, head, key="standard_charge_information"):
    """Yield each object of the named top-level array, one at a time.

    These files run to gigabytes. We scan for the array, then walk it tracking
    brace depth and string state, decoding one element at a time so peak memory
    stays at a single record rather than the whole document.
    """
    buf = head
    # Find the array opening bracket that follows the key.
    while True:
        m = re.search(r'"%s"\s*:\s*\[' % re.escape(key), buf)
        if m:
            buf = buf[m.end():]
            break
        more = stream.read(CHUNK)
        if not more:
            return
        buf += more.decode("utf-8", "replace")
        # Keep the tail only; the key cannot straddle more than a little.
        if len(buf) > 4 * CHUNK:
            buf = buf[-2 * CHUNK:]

    depth, start, in_str, esc = 0, None, False, False
    i = 0
    while True:
        while i < len(buf):
            ch = buf[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and start is not None:
                    chunk = buf[start:i + 1]
                    try:
                        yield json.loads(chunk)
                    except ValueError:
                        pass
                    buf = buf[i + 1:]
                    i, start = -1, None
            elif ch == "]" and depth == 0:
                return
            i += 1
        more = stream.read(CHUNK)
        if not more:
            return
        # Drop the consumed prefix when we are between records, so the buffer
        # does not grow without bound on a multi-gigabyte file.
        if depth == 0 and start is None and i > 0:
            buf, i = buf[i:], 0
        buf += more.decode("utf-8", "replace")


def _from_json_record(rec):
    """Flatten one standard_charge_information object into per-code rows.

    ONE ROW PER standard_charges ENTRY, not one per record. A hospital publishes
    a separate price for each setting and billing class, and folding them
    together silently mixes them: an earlier version of this took the last
    non-null value of each field while keeping the FIRST setting label, so a
    knee arthroscopy came back tagged `outpatient` carrying the inpatient cash
    price. A reader having day surgery would have been told, with a citation,
    that their hospital's published price was the inpatient one.
    """
    # Everything here is coerced rather than trusted. These files are written by
    # hundreds of different vendors and a large share fail CMS's own validator, so
    # a number where a string belongs is an ordinary Tuesday -- and it must not
    # take the whole audit down.
    desc = str(rec.get("description") or "").strip()
    codes = []
    info = rec.get("code_information")
    for ci in (info if isinstance(info, list) else []):
        if not isinstance(ci, dict):
            continue
        code = str(ci.get("code") or "").strip().upper()
        if code:
            codes.append((code, str(ci.get("type") or "").strip().upper()))
    if not codes:
        return []

    out = []
    charges = rec.get("standard_charges")
    for sc in (charges if isinstance(charges, list) else []):
        if not isinstance(sc, dict):
            continue
        gross = _num(sc.get("gross_charge"))
        cash = _num(sc.get("discounted_cash"))
        lo = _num(sc.get("minimum"))
        hi = _num(sc.get("maximum"))
        # Payer-specific dollar amounts became mandatory under the CY2026 rule;
        # they bound min/max when a hospital omits those.
        pinfo = sc.get("payers_information")
        payers = [p for p in (
            _num(pi.get("standard_charge_dollar"))
            for pi in (pinfo if isinstance(pinfo, list) else [])
            if isinstance(pi, dict)) if p]
        if payers:
            lo = min([lo] + payers) if lo else min(payers)
            hi = max([hi] + payers) if hi else max(payers)
        if gross is None and cash is None and lo is None:
            continue
        for c, t in codes:
            out.append({
                "code": c, "type": t, "desc": desc, "gross": gross, "cash": cash,
                "min": lo, "max": hi,
                "setting": str(sc.get("setting") or "").strip().lower(),
                "billing_class": str(sc.get("billing_class") or "").strip().lower(),
                "payers": len(payers),
            })
    return out


def _num(v, default=None):
    if v is None or v == "":
        return default
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if f > 0 else default


# ------------------------------------------------------------- csv reader
CSV_CODE = re.compile(r"^code\s*\|\s*(\d+)$", re.I)
CSV_CODE_TYPE = re.compile(r"^code\s*\|\s*(\d+)\s*\|\s*type$", re.I)


def _iter_csv_rows(stream, head):
    """CMS 'tall'/'wide' CSV MRFs.

    The header sits under two preamble rows of hospital metadata, so we find it
    rather than assuming a row number.
    """
    text = io.TextIOWrapper(stream, encoding="utf-8", errors="replace", newline="")
    try:
        header, cols = None, {}
        for row in csv.reader(_prefixed(head, text)):
            if header is None:
                low = [c.strip().lower() for c in row]
                if any(CSV_CODE.match(c) for c in low) and any(
                        c.startswith("standard_charge") for c in low):
                    header = low
                    cols = {c: i for i, c in enumerate(header)}
                continue
            yield _from_csv_row(row, header, cols)
    finally:
        # The wrapper owns the underlying stream once created; leaving it to the
        # garbage collector leaks the descriptor and warns under -W error.
        text.close()


def _prefixed(head, rest):
    """Re-feed the sniffed head, then the remainder of the stream."""
    for line in head.splitlines(True):
        yield line
    for line in rest:
        yield line


def _from_csv_row(row, header, cols):
    def get(name):
        i = cols.get(name)
        return row[i].strip() if i is not None and i < len(row) else ""

    codes = []
    for name in header:
        m = CSV_CODE.match(name)
        if not m:
            continue
        code = get(name).upper()
        if code:
            codes.append((code, get(f"code|{m.group(1)}|type").upper()))
    if not codes:
        return []
    desc = get("description")
    gross = _num(get("standard_charge|gross"))
    cash = _num(get("standard_charge|discounted_cash"))
    lo = _num(get("standard_charge|min"))
    hi = _num(get("standard_charge|max"))
    if gross is None and cash is None:
        return []
    return [{"code": c, "type": t, "desc": desc, "gross": gross, "cash": cash,
             "min": lo, "max": hi,
             "setting": get("setting").strip().lower(),
             "billing_class": get("billing_class").strip().lower(),
             "payers": 0} for c, t in codes]


# ------------------------------------------------------------------ index
def index_for(src, wanted, setting=None):
    """Best published prices for `wanted` codes. Returns (index, scanned).

    Only the codes on the reader's bill are retained -- an MRF holds tens of
    thousands of rows and we have no reason to keep any of the rest.

    `setting` ("inpatient" / "outpatient") restricts to prices published for
    that setting. Without it, prices from every setting compete and the cheapest
    wins, but the row records which setting it came from and whether others
    existed, so a finding can say so instead of quietly comparing an outpatient
    line against an inpatient price.
    """
    want = {str(c).strip().upper() for c in wanted if str(c or "").strip()}
    keep = (setting or "").strip().lower()
    index, scanned = {}, 0
    stream = _open(src)
    try:
        head = stream.read(CHUNK).decode("utf-8", "replace")
        rows = (_json_rows(stream, head) if _looks_json(src, head)
                else _iter_csv_rows(stream, head))
        for group in rows:
            scanned += 1
            for row in group:
                code = row["code"]
                if code not in want:
                    continue
                if keep and row.get("setting") and row["setting"] != keep:
                    continue
                cur = index.get(code)
                if cur is None:
                    row = dict(row, settings_seen={row.get("setting") or ""})
                    index[code] = row
                    continue
                cur["settings_seen"].add(row.get("setting") or "")
                # Keep the lowest published cash price for the code: it is the
                # number the reader can actually ask to be charged.
                if _better(row, cur):
                    seen = cur["settings_seen"]
                    index[code] = dict(row, settings_seen=seen)
    finally:
        try:
            stream.close()
        except Exception:
            pass
    return index, scanned


def _json_rows(stream, head):
    for rec in _iter_json_objects(stream, head):
        yield _from_json_record(rec)


def _better(new, cur):
    a, b = new.get("cash"), cur.get("cash")
    if a is not None and b is not None:
        return a < b
    if a is not None:
        return True
    return False


# --------------------------------------------------------------- findings
# A bill above the hospital's own published cash price is the finding; below it
# is not. The margin absorbs rounding and per-unit vs per-case differences.
CASH_MARGIN = 1.05


def _group(lines, index, pick):
    """Collapse identical priced lines, as the ASP and DMEPOS rules do.

    Without this, a line billed twice produces two identical paragraphs, which
    buries every other finding under a repeated one.
    """
    groups = {}
    for ln in lines:
        row = index.get((ln.code or "").upper())
        if not row or ln.charge <= 0:
            continue
        rate = pick(row)
        if not rate:
            continue
        expected = rate * (ln.units or 1)
        if expected <= 0 or ln.charge <= expected * CASH_MARGIN:
            continue
        key = ((ln.code or "").upper(), round(ln.units, 3), round(ln.charge, 2))
        groups.setdefault(key, []).append(ln)
    return groups


def _where(idxs):
    return (f"Line {idxs[0]}" if len(idxs) == 1
            else f"Lines {', '.join(str(i) for i in idxs)} (each)")


def compare(lines, index, source, ctx=None):
    """Findings from the reader's bill against their hospital's published prices."""
    out = []
    self_pay = bool(ctx and ctx.self_pay)
    cite = (f"Hospital price transparency machine-readable file: {source}. "
            "Published by the hospital under 45 CFR 180.50 and the CY2026 "
            "OPPS/ASC final rule, under its own attestation.")

    for (code, units, charge), grp in _group(lines, index,
                                             lambda r: r.get("cash")).items():
        row = index[code]
        cash = row["cash"]
        expected = cash * (units or 1)
        idxs = [l.idx for l in grp]
        out.append(Finding(
            rule="mrf_cash_price",
            severity="high" if self_pay else "warn",
            title=(f"{code}: billed {_money(charge)}, but this hospital publishes a "
                   f"cash price of {_money(cash)}"),
            detail=(f"{_where(idxs)}: {units:g} unit(s) of {code}"
                    f"{' (' + row['desc'] + ')' if row.get('desc') else ''} billed at "
                    f"{_money(charge)}. The hospital's own machine-readable file lists "
                    f"a discounted cash price of {_money(cash)} per unit, so the same "
                    f"quantity lists at {_money(expected)} — a difference of "
                    f"{_money(charge - expected)}. "
                    + ("You told us you are paying cash, so ask to be charged the "
                       "published cash price by name. It is the hospital's own number, "
                       "attested to under federal rule, not a Medicare comparison they "
                       "can wave away. "
                       if self_pay else
                       "The cash price generally applies to self-pay patients rather "
                       "than to insured claims, so check which applies to you before "
                       "relying on it. ")
                    + _setting_note(row)
                    + "This is the strongest question on this page, but it is still a "
                      "question: a published cash price may carry conditions such as "
                      "paying in full or not billing insurance. It is not counted as "
                      "directly recoverable for that reason. Quote the file and the "
                      "date you retrieved it."),
            lines=idxs,
            citation=cite,
            amount=(charge - expected) * len(grp),
            # Deliberately NOT recoverable. The cash price is leverage, not proof
            # of an error, and a duplicated line already reports its own
            # recoverable amount -- summing both would claim the same dollars twice.
            recoverable=False,
        ))

    # Billed above the hospital's OWN published gross charge is a different animal:
    # not a negotiation but a discrepancy against their own chargemaster.
    for (code, units, charge), grp in _group(
            lines, index,
            lambda r: None if r.get("cash") else r.get("gross")).items():
        gross = index[code]["gross"]
        expected = gross * (units or 1)
        idxs = [l.idx for l in grp]
        out.append(Finding(
            rule="mrf_above_gross",
            severity="high",
            title=f"{code}: billed above the hospital's own published gross charge",
            detail=(f"{_where(idxs)}: {units:g} unit(s) billed at {_money(charge)}, but "
                    f"the hospital's published gross charge is {_money(gross)} per unit "
                    f"— {_money(expected)} for this quantity. A bill above the "
                    "published chargemaster price is a discrepancy against their own "
                    "attested file, and the billing office should explain it in "
                    "writing."),
            lines=idxs,
            citation=cite,
            amount=(charge - expected) * len(grp),
            recoverable=True,
        ))
    return out


def _setting_note(row):
    """Say which setting a published price came from, and if others existed.

    A hospital publishes a different price for inpatient and outpatient, and
    comparing across them is meaningless. Naming the setting is what lets the
    reader notice a mismatch we cannot detect from the bill alone.
    """
    setting = row.get("setting") or ""
    seen = {s for s in (row.get("settings_seen") or set()) if s}
    bits = []
    if setting:
        bits.append(f"This price is the one published for **{setting}** care")
        if len(seen) > 1:
            others = ", ".join(sorted(seen - {setting}))
            bits.append(f"and the file also publishes {others} prices for this code, "
                        "which are different numbers -- check that you are comparing "
                        "the setting you were actually treated in")
        bits.append(". ")
        return "".join(bits[:-1]) + bits[-1]
    return ""


def _money(n):
    return f"${n:,.2f}"
