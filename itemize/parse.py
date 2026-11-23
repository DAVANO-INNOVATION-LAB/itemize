"""Parse itemized bills into Line records.

Two input shapes, because that is what people actually have:
  * delimited (CSV/TSV) exported from a portal
  * loose text pasted out of a PDF

Both are best-effort. A readable charge can lack a billing code; non-charge
metadata and ambiguous text are never converted to money by guessing.
"""
from __future__ import annotations

import csv
import io
import math
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


def parse_delimited(text, stats=None):
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

    if stats is not None:
        stats.update(mode="delimited", raw=len(rows), ignored=hdr_i + 1)
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
        if not code and (_metadata(desc) or re.search(r"^balance\b", desc, re.I)):
            if stats is not None:
                stats["ignored"] += 1
            continue
        if charge_raw and not RE_MONEY_FIELD.fullmatch(charge_raw):
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


# A charge must be a complete money field, never a substring of a date,
# house number, ZIP, barcode or dosage. Cap digits before numeric conversion.
_AMOUNT = r"(?:[0-9]{1,12}|[0-9]{1,3}(?:,[0-9]{3}){1,3})(?:\.[0-9]{2})?"
RE_MONEY_FIELD = re.compile(r"(?:-?\$?[ \t]?" + _AMOUNT + r"|\(\$?[ \t]?" + _AMOUNT + r"\))")
RE_CHARGE_END = re.compile(r"(?:^|[ \t])(" + RE_MONEY_FIELD.pattern + r")$")
RE_CODEISH = re.compile(r"^[A-Za-z]{0,4}[0-9]{3,6}$")
RE_STREET = re.compile(
    r"^\s*[0-9]+[A-Za-z]?(?:[-/][0-9]+)?\s+.+?\b(?:street|st\.?|avenue|ave\.?|"
    r"road|rd\.?|boulevard|blvd\.?|drive|dr\.?|lane|ln\.?|way|court|ct\.?|"
    r"place|pl\.?|circle|cir\.?|parkway|pkwy\.?|terrace|ter\.?|highway|hwy\.?)"
    r"(?=\s|,|\.|$)", re.I)
RE_POSTAL = re.compile(
    r"^[A-Za-z][A-Za-z .'-]*,?\s+(?:AL|AK|AZ|AR|CA|CO|CT|DE|DC|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|AS|GU|MP|PR|VI|AA|AE|AP)\s+[0-9]{5}(?:-[0-9]{4})?$", re.I)
RE_METADATA = re.compile(
    r"^(?:subtotal\b|grand\s+total\b|total(?:\s+(?:charges?|billed|amount|due|payments?|"
    r"credits?|balance)\b|\s*[:$]|\s+[0-9]|$)|(?:balance|amount)\s+due\b|"
    r"(?:statement|current|previous|remaining|new|patient)\s+balance\b|"
    r"(?:patient\s+)?(?:account|member|policy|claim|invoice|reference|statement|visit|record)"
    r"\s*(?:number\b|no\.?\b|id\b|#|:|[0-9])|"
    r"(?:patient name|address|billing address|mailing address|date of birth|dob|mrn|phone|fax)"
    r"\s*(?::|#|\b)|"
    r"(?:statement date|invoice date|date printed|page\s+[0-9]+)\b|"
    r"(?:p\.?\s*o\.?\s+box|suite|ste\.?|apt\.?|apartment|unit|floor)\s*[#:]*\s*[0-9]|"
    r"(?:date(?: of service)?\s+)?(?:cpt\s+|code\s+)?(?:description|service description)\s+"
    r"(?:(?:qty|quantity|units)\s+)?(?:charge[s]?|amount)\b)", re.I)


def _metadata(s):
    return bool(RE_METADATA.search(s) or RE_STREET.search(s) or RE_POSTAL.fullmatch(s)
                or RE_DATE.fullmatch(s))


def _text_line(s, idx):
    # PDF/OCR layout is frequently a single run-on string. Accept only a
    # trailing standalone amount and independent service-like text. A bare
    # integer additionally needs a billing code at the start (after any date).
    match = RE_CHARGE_END.search(s)
    if not match:
        return None
    field = match.group(1)
    charge = _money(field)
    if not math.isfinite(charge) or charge == 0:
        return None
    left = s[:match.start(1)].rstrip()
    cols = re.split(r"\s{2,}", left)
    positional = len(cols) >= 2
    dm = RE_DATE.match(left)
    date = dm.group(1) if dm else ""
    prefix = left[dm.end():].lstrip() if dm else left
    first = prefix.split(maxsplit=1)
    head = first[0] if first else ""
    # Numeric identifiers wider/shorter than a CPT are not evidence in an
    # undelimited line. Custom alphanumeric codes are supported in aligned rows.
    code = head.upper() if (RE_CODE.fullmatch(head) or
                            (positional and head[:1].isalpha() and RE_CODEISH.fullmatch(head))) else ""
    if "." not in field and "$" not in field and not code:
        return None
    desc = first[1] if code and len(first) > 1 else ("" if code else prefix)
    parts = desc.rsplit(maxsplit=1)
    units = 1.0
    if len(parts) == 2 and (code or positional) and re.fullmatch(r"[0-9]{1,4}(?:\.[0-9]+)?", parts[1]):
        units = _num(parts[1])
        desc = parts[0]
    desc = desc.strip(" .\t-|")
    if not re.search(r"[A-Za-z]{2}", desc) or _metadata(desc):
        return None
    return Line(idx=idx, code=code, desc=desc[:160], units=units, charge=charge,
                date=date, ndc=normalize_ndc(s), raw=s[:300])


def parse_text(text, stats=None):
    """Extract plausible charges, retaining a count of ignored and unread rows."""
    lines = []
    raw_count = ignored = 0
    for raw in text.split("\n"):
        s = raw.strip()
        if not s:
            continue
        raw_count += 1
        if _metadata(s):
            ignored += 1
            continue
        line = _text_line(s, len(lines) + 1)
        if line is not None:
            lines.append(line)
    if stats is not None:
        stats.update(mode="text", raw=raw_count, ignored=ignored)
    return lines


def parse_details(text):
    """Return itemized lines and transparent counts, without collecting text."""
    text = _denul(text.replace("\r\n", "\n").replace("\r", "\n")).lstrip("\ufeff")
    stats = {}
    lines = parse_delimited(text, stats)
    if not stats:
        lines = parse_text(text, stats)
    stats["parsed"] = len(lines)
    stats["unread"] = max(0, stats["raw"] - len(lines) - stats["ignored"])
    return lines, stats


def parse(text):
    """The stable public API returns only itemized Line records."""
    return parse_details(text)[0]
