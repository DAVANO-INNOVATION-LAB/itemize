"""Parse itemized bills into Line records.

Two input shapes, because that is what people actually have:
  * delimited (CSV/TSV) exported from a portal
  * loose text pasted out of a PDF

Both are best-effort. Anything we cannot confidently read becomes a line with
no code, which the rules engine then flags -- silence is never treated as
"nothing wrong".
"""
from __future__ import annotations

import csv
import io
import re

from .model import Line

# Header aliases seen across hospital/insurer exports.
ALIASES = {
    "code": ("code", "hcpcs", "cpt", "cpt/hcpcs", "hcpcs/cpt", "procedure code",
             "proc code", "service code", "billing code", "cpt code"),
    "desc": ("description", "desc", "service", "service description", "item",
             "procedure", "detail", "charge description"),
    "units": ("units", "unit", "qty", "quantity", "svc units", "days/units"),
    "charge": ("charge", "charges", "amount", "billed", "billed amount",
               "total", "total charges", "line total", "amt"),
    "date": ("date", "service date", "date of service", "dos", "svc date"),
    "modifiers": ("modifier", "modifiers", "mod"),
    "revenue_code": ("revenue code", "rev code", "rev cd", "revcode"),
    "unit_price": ("unit price", "price", "rate", "unit charge", "charge per unit",
                   "unit cost", "each"),
    "ndc": ("ndc", "ndc code", "ndc number", "national drug code", "drug code"),
}

RE_NDC_HYPHEN = re.compile(r"\b(\d{4,5})-(\d{3,4})-(\d{1,2})\b")
RE_NDC_BARE = re.compile(r"\b(\d{11})\b")


def normalize_ndc(s):
    """Normalise a National Drug Code to the 11-digit 5-4-2 form CMS prices in.

    Hyphenated NDCs are zero-padded segment-wise, which is unambiguous. A bare
    10-digit run is NOT: it could be 4-4-2, 5-3-2 or 5-4-1, and choosing wrong
    silently points at a different drug at a different price. We decline those
    rather than guess. Mirrors normalizeNdc in web/rules.js.
    """
    t = str(s or "").strip()
    if not t:
        return ""
    m = RE_NDC_HYPHEN.search(t)
    if m:
        return m.group(1).zfill(5) + m.group(2).zfill(4) + m.group(3).zfill(2)
    m = RE_NDC_BARE.search(t)
    return m.group(1) if m else ""

# The sign is CAPTURED, not skipped. Real statements carry credits -- payments,
# insurance adjustments, write-offs -- and reading "-500.00" as +500.00 inflates
# the total and, worse, lets two identical credit lines be reported as a
# duplicate CHARGE the reader should dispute. Accounting parentheses count too.
RE_MONEY = re.compile(r"(-|\()?\s*\$?\s*([\d,]+\.\d{2}|[\d,]+)")
RE_CODE = re.compile(r"\b(\d{5}|[A-Za-z]\d{4})\b")
RE_DATE = re.compile(r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})\b")


def _money(s):
    if s is None:
        return 0.0
    m = RE_MONEY.search(str(s))
    return _money_from(m)


def _money_from(m):
    """Signed value of an RE_MONEY match, or 0.0."""
    if not m:
        return 0.0
    try:
        v = float(m.group(2).replace(",", ""))
    except ValueError:
        return 0.0
    return -v if m.group(1) else v


# A quantity, optionally followed by a short unit label: "2", "2.5", "2 EA",
# "3 doses". Deliberately anchored, because a prefix parse turns the NDC
# 00409-3799-01 into the number 409 -- which is what JavaScript's parseFloat
# did, giving the browser a different unit count from the CLI on the same bill.
RE_QTY = re.compile(r"^([+-]?\d*\.?\d+)\s*[A-Za-z]{0,12}$")


def _num(s, default=1.0):
    """Positive finite quantity, or `default`.

    Two ways this used to go wrong, both fixed here:

    * `float("Infinity")` is greater than zero, so an infinite unit count sailed
      through -- and it makes every price benchmark compute an infinite allowed
      amount, silently disabling all of them for that line. A gate that fails
      open is worse than one that fails loudly.
    * The two engines disagreed. Python's `float()` refused "2 EA" and fell back
      to 1, while JavaScript's `parseFloat()` read the prefix and got 2, so the
      browser and the CLI reported different multiples for the same line. Both
      now read the quantity and ignore a trailing unit label.

    Mirrors num() in web/rules.js.
    """
    m = RE_QTY.match(str(s if s is not None else "").strip().replace(",", ""))
    if not m:
        return default
    try:
        v = float(m.group(1))
    except ValueError:
        return default
    if v != v or v in (float("inf"), float("-inf")):   # NaN or +/-inf
        return default
    return v if v > 0 else default


# U+FEFF is in here on purpose. Excel writes a byte-order mark at the start of
# every CSV it exports, and JavaScript's String.trim() strips U+FEFF while
# Python's str.strip() does not -- so the same exported file produced a
# description with a leading BOM in the CLI and without one in the browser.
RE_CONTROL = re.compile(r"[\x00-\x1f\x7f\ufeff]+")


def _clean(text):
    """Collapse a field to a single line of printable text.

    A quoted CSV field may legally contain newlines, and the evidence packet is
    markdown that a reader hands to a billing office. A description carrying
    "\n> **Source:** ..." forged a citation line into that packet and broke the
    line table; control characters did the same to the terminal. Descriptions
    are one line, so make them one line at the boundary rather than escaping at
    each of the several places they are rendered.
    """
    return RE_CONTROL.sub(" ", str(text or "")).strip()


def _denul(text):
    """Replace NUL bytes with a space before anything else touches the text.

    Python's csv module raised `_csv.Error: line contains NUL` up to 3.10, so a
    bill carrying a NUL -- ordinary in text extracted from a PDF, and in some
    Windows exports -- crashed the parser outright on the interpreter floor this
    project claims to support. Sanitising per field was too late: the csv reader
    chokes first.

    A SPACE, not nothing: `_clean` turns the remaining control characters into
    spaces too, and JavaScript has no NUL restriction, so dropping the byte here
    would leave the two engines reading "nulbyte" and "nul byte" for the same
    input.
    """
    return text.replace("\x00", " ")


def _map_headers(header):
    out = {}
    for i, h in enumerate(header):
        k = (h or "").strip().lower()
        for field, names in ALIASES.items():
            if field in out:
                continue
            if k in names or any(k == n for n in names):
                out[field] = i
                break
    # second pass: substring match for headers like "total charges (usd)"
    for i, h in enumerate(header):
        k = (h or "").strip().lower()
        for field, names in ALIASES.items():
            if field in out:
                continue
            if any(n in k for n in names):
                out[field] = i
                break
    return out


def parse_delimited(text):
    text = _denul(text)
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t|;")
    except csv.Error:
        dialect = csv.excel
    rows = [r for r in csv.reader(io.StringIO(text), dialect) if any(c.strip() for c in r)]
    if not rows:
        return []

    # Find the header row: the first row mapping at least a code-ish and a money-ish column.
    hdr_i, cols = None, {}
    for i, row in enumerate(rows[:25]):
        m = _map_headers(row)
        if "charge" in m and ("code" in m or "desc" in m):
            hdr_i, cols = i, m
            break
    if hdr_i is None:
        return []

    width = len(rows[hdr_i])
    lines = []
    for row in rows[hdr_i + 1:]:
        def g(f, d=""):
            i = cols.get(f)
            return row[i].strip() if i is not None and i < len(row) else d

        charge_raw = g("charge")
        code, desc = _clean(g("code")).upper(), _clean(g("desc"))
        if not charge_raw and not code and not desc:
            continue
        # Skip obvious total/subtotal rows.
        if not code and re.search(r"\b(total|balance|subtotal|amount due)\b", desc, re.I):
            continue
        mods = [m.strip().upper() for m in re.split(r"[,\s/]+", g("modifiers")) if m.strip()]
        lines.append(Line(
            idx=len(lines) + 1, code=code, desc=desc,
            units=_num(g("units")), charge=_money(charge_raw),
            date=g("date"), modifiers=mods, revenue_code=g("revenue_code"),
            unit_price=_money(g("unit_price")),
            ndc=normalize_ndc(g("ndc")) or normalize_ndc(code) or normalize_ndc(desc),
            suspect_columns=len(row) > width,
            raw=",".join(row)[:300],
        ))
    return lines


def _strip_trailing_number(desc):
    """Remove a trailing quantity/amount from a description.

    Done as a backward scan rather than a regex on purpose. Every regex spelling
    of "optional digits, optional dot, optional digits, at the end" makes the
    engine retry from each start position, which is quadratic on a long numeric
    run -- an account number or a barcode off a PDF text layer -- and it runs in
    the reader's browser. This is linear and cannot backtrack.
    """
    t = desc.rstrip()
    j = len(t)
    while j > 0 and (t[j - 1].isdigit() or t[j - 1] in ",."):
        j -= 1
    return t[:j]


RE_CODEISH = re.compile(r"^[A-Za-z]{0,4}\d{3,6}$")


def _from_columns(parts):
    """Read a line already split into columns (PDF text layers, aligned text).

    Returns (code, desc, units, charge, date) or None when the row does not
    look like a charge line.
    """
    charge_i = None
    for i in range(len(parts) - 1, -1, -1):
        if RE_MONEY.fullmatch(parts[i].strip().replace("$", "").replace(",", "")) \
                or re.fullmatch(r"\$?[\d,]+\.\d{2}", parts[i].strip()):
            charge_i = i
            break
    if charge_i is None:
        return None
    charge = _money(parts[charge_i])
    if charge == 0:
        return None            # a credit is a real line; only zero is noise

    date = ""
    for s in parts:
        m = RE_DATE.search(s)
        if m:
            date = m.group(1)
            break

    code = ""
    for i, s in enumerate(parts):
        if i == charge_i:
            continue
        t = s.strip()
        if t == date or RE_DATE.search(t):
            continue
        if RE_CODEISH.match(t):
            code = t.upper()
            break

    units = 1.0
    for i in range(charge_i - 1, -1, -1):
        t = parts[i].strip()
        if re.fullmatch(r"\d{1,4}(\.\d+)?", t) and t.upper() != code:
            units = _num(t)
            break

    desc = ""
    for i, s in enumerate(parts):
        t = s.strip()
        if i == charge_i or t.upper() == code or t == date:
            continue
        if len(t) > len(desc) and re.search(r"[A-Za-z]{3}", t):
            desc = t
    return code, desc[:160], units, charge, date


def parse_text(text):
    """Loose per-line extraction for bills pasted out of a PDF."""
    lines = []
    for raw in text.split("\n"):
        s = raw.strip()
        if not s or len(s) < 8:
            continue
        if re.search(r"\b(total|balance due|subtotal|amount due|page \d)\b", s, re.I):
            continue
        monies = list(RE_MONEY.finditer(s))
        if not monies:
            continue

        # A line with real column gaps (PDF text layers, aligned statements)
        # can be read positionally, which is far more reliable than pattern
        # matching against one run-on string.
        cols = re.split(r"\s{2,}", s)
        if len(cols) >= 3:
            got = _from_columns(cols)
            if got:
                code, desc, units, charge, date = got
                lines.append(Line(idx=len(lines) + 1, code=code, desc=desc,
                                  units=units, charge=charge, date=date,
                                  ndc=normalize_ndc(s), raw=s[:300]))
                continue
        charge = _money_from(monies[-1])
        if charge == 0:
            continue           # a credit is a real line; only zero is noise
        cm = RE_CODE.search(s)
        code = cm.group(1).upper() if cm else ""
        dm = RE_DATE.search(s)
        # Units: a small standalone integer sitting between the code and the money.
        units = 1.0
        um = re.search(r"\b(\d{1,3})\b(?=[^\d]*[\d,]+\.\d{2}\s*$)", s)
        if um:
            units = _num(um.group(1))
        desc = s
        if cm:
            desc = s[cm.end():]
        # `[\d,]*\.?\d*\s*$` let two quantifiers both match bare digits, so a long
        # numeric run (an account number, a barcode off a PDF text layer) made the
        # engine explore every split: ~1.9s at 1,000 digits, unbounded at 20,000,
        # and it runs in the reader's browser. The decimal part is now only
        # reachable through a required dot, which removes the ambiguity.
        desc = _strip_trailing_number(desc).strip(" .\t-|")
        lines.append(Line(idx=len(lines) + 1, code=code, desc=desc[:160],
                          units=units, charge=charge,
                          date=dm.group(1) if dm else "",
                          ndc=normalize_ndc(s), raw=s[:300]))
    return lines


def parse(text):
    """Try delimited first, fall back to loose text. Returns [Line]."""
    text = _denul(text.replace("\r\n", "\n").replace("\r", "\n")).lstrip("\ufeff")
    lines = parse_delimited(text)
    if len(lines) >= 1:
        return lines
    return parse_text(text)
