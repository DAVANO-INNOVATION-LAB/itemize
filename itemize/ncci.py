"""Tier 2: NCCI unbundling checks, under the user's own AMA licence.

The NCCI Procedure-to-Procedure edit files list code pairs that must not be
billed together. They are the single best open detector of unbundling -- and
they contain CPT codes, which are copyright the American Medical Association.

CMS distributes them behind a click-through AMA licence. itemize therefore does
NOT ship these files and does NOT fetch them silently. This module shows you
where the licence lives, requires your explicit acceptance, and caches the
result under your own home directory. The licence is yours, not the project's.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import sys
import urllib.request
import zipfile

UA = "itemize/0.1 (+open medical bill auditing)"
PTP_INDEX = ("https://www.cms.gov/medicare/coding-billing/"
             "national-correct-coding-initiative-ncci-edits/"
             "medicare-ncci-procedure-procedure-ptp-edits")
LICENSE_PAGE = PTP_INDEX
CACHE = os.path.expanduser("~/.cache/itemize/ncci")

CONSENT = """\
────────────────────────────────────────────────────────────────────────────
  NCCI Procedure-to-Procedure edit files — AMA licence required
────────────────────────────────────────────────────────────────────────────

The NCCI edit files contain CPT codes, which are:

    "Current Procedural Terminology (CPT) codes, descriptions and other data
     only are copyright American Medical Association. All rights reserved."

CMS distributes them behind a click-through AMA licence agreement. itemize
cannot accept that licence on your behalf, and does not redistribute the files.

To proceed you must read and accept the licence on CMS's own page:

    {page}

By typing "accept" below you confirm you have read that licence and accept it
in your own capacity. The files will be downloaded to:

    {cache}

────────────────────────────────────────────────────────────────────────────
"""


def _consent_path():
    return os.path.join(CACHE, "LICENCE_ACCEPTED")


def has_consent():
    return os.path.exists(_consent_path())


def request_consent(stream=sys.stdin, out=sys.stdout):
    if has_consent():
        return True
    out.write(CONSENT.format(page=LICENSE_PAGE, cache=CACHE))
    out.write('Type "accept" to continue, anything else to abort: ')
    out.flush()
    answer = (stream.readline() or "").strip().lower()
    if answer != "accept":
        out.write("Aborted. No files downloaded.\n")
        return False
    os.makedirs(CACHE, exist_ok=True)
    with open(_consent_path(), "w") as f:
        f.write("User accepted the AMA licence referenced at:\n" + LICENSE_PAGE + "\n")
    return True


def _fetch(url, limit=120_000_000):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read(limit)


def discover_ptp_urls():
    """Return practitioner/hospital PTP zip URLs from the CMS index page.

    Links are published as /license/ama?file=<path>; the licensed asset itself
    lives at <path>. We only ever resolve these after consent is recorded.
    """
    page = _fetch(PTP_INDEX).decode("utf-8", "replace")
    urls = []
    for href in re.findall(r'href="(/license/ama\?file=[^"]+\.zip)"', page, re.I):
        path = href.split("file=", 1)[1]
        url = "https://www.cms.gov" + path
        if "ptp-edits" in url and url not in urls:
            urls.append(url)
    return urls


def load_pairs(refresh=False):
    """Return {(col1, col2): modifier_indicator}. Requires prior consent."""
    if not has_consent():
        raise RuntimeError("AMA licence not accepted; run `itemize ncci --accept` first")
    os.makedirs(CACHE, exist_ok=True)
    cached = os.path.join(CACHE, "ptp_pairs.json")
    if os.path.exists(cached) and not refresh:
        with open(cached) as f:
            return {tuple(k.split("|")): v for k, v in json.load(f).items()}

    pairs = {}
    for url in discover_ptp_urls():
        try:
            z = zipfile.ZipFile(io.BytesIO(_fetch(url)))
        except Exception as e:                       # noqa: BLE001
            print(f"  skip {url.rsplit('/', 1)[-1]}: {type(e).__name__}", file=sys.stderr)
            continue
        for member in z.namelist():
            if not member.lower().endswith((".csv", ".txt")):
                continue
            text = z.read(member).decode("latin-1")
            rows = csv.reader(io.StringIO(text))
            for row in rows:
                if len(row) < 2:
                    continue
                a, b = row[0].strip().upper(), row[1].strip().upper()
                if not re.match(r"^[A-Z0-9]{5}$", a) or not re.match(r"^[A-Z0-9]{5}$", b):
                    continue
                mod = ""
                for cell in row[2:6]:
                    c = cell.strip()
                    if c in ("0", "1", "9"):
                        mod = c
                        break
                pairs[(a, b)] = mod
    with open(cached, "w") as f:
        json.dump({"|".join(k): v for k, v in pairs.items()}, f)
    return pairs


def check(lines, pairs):
    """Return [(line_a, line_b, modifier_indicator)] for billed PTP pairs."""
    by_date = {}
    for ln in lines:
        if ln.code:
            by_date.setdefault(ln.date.strip(), []).append(ln)
    hits = []
    for _, group in by_date.items():
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                ca, cb = a.code.upper(), b.code.upper()
                for k in ((ca, cb), (cb, ca)):
                    if k in pairs:
                        hits.append((a, b, pairs[k]))
                        break
    return hits
