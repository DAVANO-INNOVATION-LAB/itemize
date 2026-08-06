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
}

RE_MONEY = re.compile(r"-?\$?\s*([\d,]+\.\d{2}|[\d,]+)")
RE_CODE = re.compile(r"\b(\d{5}|[A-Za-z]\d{4})\b")
RE_DATE = re.compile(r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})\b")


def _money(s):
    if s is None:
        return 0.0
    m = RE_MONEY.search(str(s))
    if not m:
        return 0.0
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return 0.0


def _num(s, default=1.0):
    try:
        v = float(str(s).strip().replace(",", ""))
        return v if v > 0 else default
    except (ValueError, AttributeError):
        return default


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

    lines = []
    for row in rows[hdr_i + 1:]:
        def g(f, d=""):
            i = cols.get(f)
            return row[i].strip() if i is not None and i < len(row) else d

        charge_raw = g("charge")
        code, desc = g("code").upper(), g("desc")
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
            unit_price=_money(g("unit_price")), raw=",".join(row)[:300],
        ))
    return lines


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
    if charge <= 0:
        return None

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
        monies = RE_MONEY.findall(s)
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
                                  units=units, charge=charge, date=date, raw=s[:300]))
                continue
        charge = _money("$" + monies[-1])
        if charge <= 0:
            continue
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
        desc = re.sub(r"[\d,]*\.?\d*\s*$", "", desc).strip(" .\t-|")
        lines.append(Line(idx=len(lines) + 1, code=code, desc=desc[:160],
                          units=units, charge=charge,
                          date=dm.group(1) if dm else "", raw=s[:300]))
    return lines


def parse(text):
    """Try delimited first, fall back to loose text. Returns [Line]."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = parse_delimited(text)
    if len(lines) >= 1:
        return lines
    return parse_text(text)
