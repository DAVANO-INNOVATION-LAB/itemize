#!/usr/bin/env python3
"""Build itemize's redistributable reference data from CMS public-domain sources.

This script is the legal boundary of the project. It downloads CMS files that
contain a MIX of public-domain and third-party-copyrighted content, and emits
ONLY the public-domain subset.

Excluded, deliberately, and never written to disk:
  * CPT / Level I codes (5-digit numeric, plus Category II `nnnnF` and
    Category III `nnnnT`) -- copyright American Medical Association.
  * CDT / dental `Dnnnn` codes -- copyright American Dental Association.

CMS states the rule plainly in HCPC*_recordlayout.txt:
    "CPT-4 codes including both long and short descriptions shall be used in
     accordance with the CMS/AMA agreement. Any other use violates the AMA
     copyright."

What we keep: HCPCS Level II alpha-numeric codes (A,B,C,E,G,H,J,K,L,M,P,Q,R,S,
T,U,V prefixes), which are authored and jointly maintained by CMS and are US
government work product.

Dollar amounts computed and published by CMS (ASP payment limits) are facts
about federal payment policy and are kept for all codes; DESCRIPTIONS are kept
only for the public-domain subset.

This lives inside the package rather than in tools/ so that an installed copy
can refresh its own data (`itemize data --refresh`). Without it, anyone who
pip-installed the tool would have to clone the repository to get current prices,
which is exactly the friction that leaves stale numbers in front of a reader.
`tools/build_data.py` remains as a thin shim so the documented path still works.

Usage:
    python3 -m itemize.build_data --out web/data
    python3 tools/build_data.py --out web/data --hcpcs-url <url>   # pin a quarter
    itemize data --refresh
"""
import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import urllib.request
import zipfile
from datetime import datetime, timezone

UA = "itemize-databuild/0.1 (+open medical bill auditing; https://github.com/)"

HCPCS_INDEX = ("https://www.cms.gov/medicare/coding-billing/"
               "healthcare-common-procedure-system/quarterly-update")
ASP_INDEX = "https://www.cms.gov/medicare/payment/part-b-drugs/asp-pricing-files"
DMEPOS_INDEX = ("https://www.cms.gov/medicare/payment/fee-schedules/dmepos/"
                "dmepos-fee-schedule")
# NADAC: what pharmacies actually pay to acquire a drug, by NDC, surveyed and
# published weekly. Public domain (usa.gov/publicdomain/label/1.0). This is the
# only free national price for the outpatient drug side; Part B ASP covers just
# the ~900 J-codes a physician administers.
NADAC_METASTORE = ("https://data.medicaid.gov/api/1/metastore/schemas/dataset/"
                   "items?show-reference-ids=true")
# MS-DRG average charges by hospital and nationally. MS-DRG and ICD-10-PCS are
# CMS-maintained US government work -- no AMA licence anywhere in the chain,
# which is what makes an inpatient benchmark possible in the open tier at all.
CMS_DATA_JSON = "https://data.cms.gov/data.json"
DRG_DATASET_TITLE = "Medicare Inpatient Hospitals - by Geography and Service"

# ---- code classification ---------------------------------------------------
RE_CPT_I = re.compile(r"^\d{5}$")          # AMA CPT Level I
RE_CPT_II = re.compile(r"^\d{4}F$")        # AMA CPT Category II
RE_CPT_III = re.compile(r"^\d{4}T$")       # AMA CPT Category III
RE_CDT = re.compile(r"^D\d{4}$")           # ADA dental
RE_HCPCS_II = re.compile(r"^[A-CE-Z]\d{4}$")  # public-domain Level II (excludes D)


def classify(code):
    """Return 'public', 'ama', 'ada', or 'unknown'."""
    c = (code or "").strip().upper()
    if RE_CPT_I.match(c) or RE_CPT_II.match(c) or RE_CPT_III.match(c):
        return "ama"
    if RE_CDT.match(c):
        return "ada"
    if RE_HCPCS_II.match(c):
        return "public"
    return "unknown"


# ---- fetch helpers ---------------------------------------------------------
def fetch(url, limit=80_000_000):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=180) as r:
        return r.read(limit)


def index_links(url):
    """Return absolute .zip links from a CMS landing page, page order preserved."""
    page = fetch(url).decode("utf-8", "replace")
    out = []
    for href in re.findall(r'href="([^"]+\.zip)"', page, re.I):
        if href.startswith("/"):
            href = "https://www.cms.gov" + href
        if href.startswith("https://www.cms.gov") and href not in out:
            out.append(href)
    return out


def sha256(b):
    return hashlib.sha256(b).hexdigest()


# ---- HCPCS -----------------------------------------------------------------
def parse_record_layout(text):
    """Parse CMS RIF layout into {field_name_lower: (beg, end)} (1-indexed, inclusive).

    We parse the layout rather than hardcoding offsets so a quarterly format
    change surfaces as a clean failure instead of silently shifted columns.
    """
    fields, current = {}, None
    for line in text.split("\n"):
        m = re.match(r"^\s*(\d+)\.\s+(.+?)\s*$", line)
        if m:
            current = m.group(2).strip().lower()
            continue
        m = re.match(r"^\s+(\d+)\s+(\d+)\s+(\d+)\s+(CHAR|NUM)\s*$", line)
        if m and current:
            length, beg, end = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if length == (end - beg + 1) and current not in fields:
                fields[current] = (beg, end)
            current = None
    return fields


def pick(fields, *needles):
    """Find the first layout field whose name contains all needles."""
    for name, span in fields.items():
        if all(n in name for n in needles):
            return span
    return None


def build_hcpcs(url):
    raw = fetch(url)
    z = zipfile.ZipFile(io.BytesIO(raw))
    layout_name = next(n for n in z.namelist() if "recordlayout" in n.lower())
    data_name = next(n for n in z.namelist()
                     if n.lower().endswith(".txt") and "recordlayout" not in n.lower()
                     and "proc_notes" not in n.lower())

    fields = parse_record_layout(z.read(layout_name).decode("latin-1"))
    code_span = pick(fields, "procedure coding system code") or (1, 5)
    long_span = pick(fields, "long", "description")
    short_span = pick(fields, "short", "description")
    if not (long_span or short_span):
        raise SystemExit(f"could not locate description fields in {layout_name}; "
                         f"parsed {len(fields)} fields -- CMS layout may have changed")

    def cut(line, span):
        if not span:
            return ""
        return line[span[0] - 1:span[1]].strip()

    # A single HCPCS code may span several physical records: the long
    # description wraps, with a sequence number in cols 6-8 giving the order.
    # e.g. E0114 record 001 ends "...with pads, tips" and record 002 carries
    # "and handgrips". Overwriting instead of joining silently truncates.
    frags, shorts, counts = {}, {}, {"public": 0, "ama": 0, "ada": 0, "unknown": 0}
    for line in z.read(data_name).decode("latin-1").split("\n"):
        if len(line) < 6:
            continue
        code = cut(line, code_span).upper()
        if not code:
            continue
        kind = classify(code)
        if code not in frags:
            counts[kind] += 1            # count distinct codes, not records
        if kind != "public":
            continue                     # <-- the legal boundary
        try:
            seq = int(line[5:8])
        except ValueError:
            seq = 1
        long_d = cut(line, long_span)
        if long_d:
            frags.setdefault(code, []).append((seq, long_d))
        short_d = cut(line, short_span)
        if short_d and code not in shorts:
            shorts[code] = short_d

    codes = {}
    for code, parts in frags.items():
        desc = " ".join(t for _, t in sorted(parts, key=lambda p: p[0])).strip()
        codes[code] = re.sub(r"\s{2,}", " ", desc) or shorts.get(code, "")
    for code, s in shorts.items():
        codes.setdefault(code, s)
    return codes, counts, raw, data_name


# ---- ASP drug payment limits ----------------------------------------------
def build_asp(url):
    raw = fetch(url)
    z = zipfile.ZipFile(io.BytesIO(raw))
    name = next((n for n in z.namelist()
                 if n.lower().endswith(".csv") and "payment limit" in n.lower()), None)
    if not name:
        raise SystemExit("no 508 CSV payment-limit member found in ASP zip")

    text = z.read(name).decode("latin-1")
    rows = list(csv.reader(io.StringIO(text)))
    hdr_i = next(i for i, r in enumerate(rows)
                 if r and r[0].strip().lower().startswith("hcpcs code"))
    hdr = [h.strip().lower() for h in rows[hdr_i]]
    col = {k: hdr.index(k) for k in hdr if k}

    def g(row, key):
        i = col.get(key)
        return row[i].strip() if i is not None and i < len(row) else ""

    out, counts = {}, {"public": 0, "ama": 0, "ada": 0, "unknown": 0}
    for row in rows[hdr_i + 1:]:
        if not row or not row[0].strip():
            continue
        code = row[0].strip().upper()
        kind = classify(code)
        if kind == "unknown":
            continue
        counts[kind] += 1
        try:
            limit = float(g(row, "payment limit"))
        except ValueError:
            continue
        rec = {"limit": round(limit, 4), "dose": g(row, "hcpcs code dosage")}
        # Descriptions only for the public-domain subset; amounts for all.
        if kind == "public":
            d = g(row, "short description")
            if d:
                rec["desc"] = d
        else:
            rec["licensed"] = kind      # UI shows "description withheld (AMA/ADA)"
        out[code] = rec
    return out, counts, raw, name



# ---- DMEPOS: durable medical equipment, prosthetics, orthotics, supplies ----
def _median(vals):
    v = sorted(vals)
    n = len(v)
    if not n:
        return None
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2.0


def discover_dmepos_url():
    """The fee amounts live on per-quarter sub-pages, not the landing page."""
    page = fetch(DMEPOS_INDEX).decode("utf-8", "replace")
    subs = []
    for href in re.findall(r'href="(/medicare/payment/fee-schedules/dmepos/'
                           r'dmepos-fee-schedule/[^"#?]+)"', page):
        if href not in subs:
            subs.append(href)
    for sub in subs[:6]:
        sp = fetch("https://www.cms.gov" + sub).decode("utf-8", "replace")
        for z in re.findall(r'href="([^"]+\.zip)"', sp, re.I):
            url = "https://www.cms.gov" + z if z.startswith("/") else z
            if re.search(r"/dme\d{2}", url):
                return url
    return None


def build_dmepos(url):
    raw = fetch(url)
    z = zipfile.ZipFile(io.BytesIO(raw))
    name = next((n for n in z.namelist()
                 if n.lower().endswith(".csv")
                 and re.match(r"DMEPOS\d{2}_", os.path.basename(n), re.I)), None)
    if not name:
        raise SystemExit("no DMEPOS fee-schedule CSV member found")

    rows = list(csv.reader(io.StringIO(z.read(name).decode("latin-1"))))
    hdr_i = next(i for i, r in enumerate(rows)
                 if r and r[0].strip().upper() == "HCPCS")
    hdr = [h.strip() for h in rows[hdr_i]]
    idx = {h.lower(): i for i, h in enumerate(hdr)}
    # Per-state columns look like "AL (NR)" / "AL (R)". The non-rural column is
    # the ordinary case; a national median across states gives one comparable
    # reference figure without asking the reader for their state.
    nr_cols = [i for i, h in enumerate(hdr) if re.match(r"^[A-Z]{2} \(NR\)$", h)]

    out, counts = {}, {"public": 0, "ama": 0, "ada": 0, "unknown": 0}
    for row in rows[hdr_i + 1:]:
        if not row or not row[0].strip():
            continue
        code = row[0].strip().upper()
        kind = classify(code)
        if code not in out:
            counts[kind] += 1
        if kind != "public":
            continue                     # <-- the legal boundary
        # Prefer the unmodified row; a modifier-specific row is a special case.
        mod = (row[idx["mod"]].strip() if "mod" in idx and idx["mod"] < len(row) else "")
        if code in out and mod:
            continue
        vals = []
        for i in nr_cols:
            if i < len(row):
                try:
                    v = float(row[i])
                    if v > 0:
                        vals.append(v)
                except ValueError:
                    pass
        fee = _median(vals)
        def num(key):
            i = idx.get(key)
            if i is None or i >= len(row):
                return None
            try:
                v = float(row[i])
                return v if v > 0 else None
            except ValueError:
                return None
        ceiling, floor = num("ceiling"), num("floor")
        if fee is None and ceiling is None:
            continue
        rec = {"fee": round(fee, 2) if fee is not None else round(ceiling, 2)}
        if ceiling is not None:
            rec["ceiling"] = round(ceiling, 2)
        if floor is not None:
            rec["floor"] = round(floor, 2)
        cat = idx.get("catg")
        if cat is not None and cat < len(row) and row[cat].strip():
            rec["cat"] = row[cat].strip()
        out[code] = rec
    return out, counts, raw, name


# ---- NADAC: national average drug acquisition cost, by NDC ----------------
def discover_nadac_url():
    """Latest yearly NADAC dataset's CSV distribution, from the DKAN metastore."""
    items = json.loads(fetch(NADAC_METASTORE).decode("utf-8", "replace"))
    best = None
    for it in items:
        m = re.match(r"^NADAC \(National Average Drug Acquisition Cost\) (\d{4})$",
                     it.get("title", ""))
        if m and (best is None or int(m.group(1)) > best[0]):
            best = (int(m.group(1)), it)
    if not best:
        return None
    for dist in best[1].get("distribution", []):
        d = dist.get("data", dist)
        if (d.get("format") or "").lower() == "csv" and d.get("downloadURL"):
            return d["downloadURL"]
    return None


def build_nadac(url):
    """Latest surveyed price per NDC.

    The yearly file is an append-only log of weekly surveys, so one NDC appears
    many times. We keep only the most recent effective date per NDC -- carrying
    a stale row forward would price a drug at last winter's cost and call it
    current.
    """
    raw = fetch(url, limit=400_000_000)
    rows = csv.DictReader(io.StringIO(raw.decode("utf-8", "replace")))
    latest = {}
    for r in rows:
        ndc = (r.get("NDC") or "").strip()
        if not ndc.isdigit() or len(ndc) != 11:
            continue
        try:
            price = float((r.get("NADAC Per Unit") or "").strip())
        except ValueError:
            continue
        if price <= 0:
            continue
        eff = (r.get("Effective Date") or "").strip()
        try:
            when = datetime.strptime(eff, "%m/%d/%Y")
        except ValueError:
            continue
        prev = latest.get(ndc)
        if prev is not None and prev[0] >= when:
            continue
        latest[ndc] = (when, {
            "p": round(price, 5),
            "u": (r.get("Pricing Unit") or "").strip(),          # EA | ML | GM
            "d": (r.get("NDC Description") or "").strip()[:44],
            "b": 1 if (r.get("Classification for Rate Setting") or "").strip().upper()
                 .startswith("B") else 0,                        # brand vs generic
        })
    out = {ndc: rec for ndc, (_when, rec) in latest.items()}
    newest = max((w for w, _ in latest.values()), default=None)
    return out, (newest.strftime("%Y-%m-%d") if newest else ""), raw


# ---- MS-DRG national average charges ---------------------------------------
def discover_drg_url():
    """Newest CSV distribution of the CMS inpatient by-geography dataset."""
    req = urllib.request.Request(CMS_DATA_JSON, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=180) as r:
        cat = json.load(r)
    for ds in cat.get("dataset", []):
        if ds.get("title") != DRG_DATASET_TITLE:
            continue
        for dist in ds.get("distribution", []):
            if (dist.get("format") or "").upper() == "CSV" and dist.get("downloadURL"):
                return dist["downloadURL"]          # first CSV is the newest year
    return None


def build_drg(url):
    """National average submitted charge and Medicare payment, per MS-DRG.

    The source carries national, state and provider rows; we keep the national
    ones, which is ~760 records and small enough to ship to the browser. DRG
    descriptions are CMS's own -- no AMA content, so nothing is filtered here
    for licensing the way HCPCS descriptions are.
    """
    raw = fetch(url, limit=200_000_000)
    rows = csv.DictReader(io.StringIO(raw.decode("utf-8", "replace")))
    out = {}
    for r in rows:
        if (r.get("Rndrng_Prvdr_Geo_Lvl") or "").strip() != "National":
            continue
        drg = (r.get("DRG_Cd") or "").strip()
        if not drg:
            continue

        def f(key):
            try:
                return float((r.get(key) or "").strip())
            except ValueError:
                return None

        charge, total, mdcr = (f("Avg_Submtd_Cvrd_Chrg"), f("Avg_Tot_Pymt_Amt"),
                               f("Avg_Mdcr_Pymt_Amt"))
        if charge is None:
            continue
        try:
            n = int(float((r.get("Tot_Dschrgs") or "0").strip()))
        except ValueError:
            n = 0
        out[drg.zfill(3)] = {
            "desc": (r.get("DRG_Desc") or "").strip(),
            "charge": round(charge, 2),
            "pay": round(total, 2) if total is not None else None,
            "mdcr": round(mdcr, 2) if mdcr is not None else None,
            "n": n,
        }
    return out, raw


def main(argv=None):
    ap = argparse.ArgumentParser(prog="build_data")
    ap.add_argument("--out", default="web/data")
    ap.add_argument("--hcpcs-url", help="pin a specific quarterly HCPCS zip")
    ap.add_argument("--asp-url", help="pin a specific ASP payment-limit zip")
    ap.add_argument("--dmepos-url", help="pin a specific DMEPOS fee-schedule zip")
    ap.add_argument("--nadac-url", help="pin a specific NADAC csv")
    ap.add_argument("--drg-url", help="pin a specific CMS inpatient by-geography csv")
    ap.add_argument("--skip", default="",
                    help="comma-separated datasets to skip: hcpcs,asp,dmepos,nadac,drg. "
                         "A skipped dataset keeps the file already on disk and carries "
                         "its old provenance entry forward, so the manifest never claims "
                         "a build date the data does not have. NADAC is ~90 MB.")
    args = ap.parse_args(argv)
    skip = {s.strip().lower() for s in args.skip.split(",") if s.strip()}

    os.makedirs(args.out, exist_ok=True)
    prev = load_json(os.path.join(args.out, "manifest.json"), {})
    prev_src = {s.get("dataset"): s for s in prev.get("sources", [])}
    prov, written = [], []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def record(**kw):
        """Every source carries its OWN retrieval date.

        The manifest's `built` is when the script last ran, which is not when a
        carried-forward dataset was fetched. Staleness is judged per dataset, so
        it has to be recorded per dataset or a partial rebuild silently makes
        stale files look fresh.
        """
        prov.append(dict(kw, retrieved=now))

    def carry(name):
        """Keep a skipped dataset's existing provenance rather than dropping it."""
        old = prev_src.get(name)
        if old:
            # Keep the original `retrieved`; only note that we skipped it today.
            old = dict(old, skipped_at=now)
            old.setdefault("retrieved", prev.get("built", ""))
            prov.append(old)
        print(f"{name.upper():<6} -- skipped; keeping existing file and provenance")

    # ---- HCPCS Level II ---------------------------------------------------
    if "hcpcs" in skip:
        carry("hcpcs")
        codes = load_json(os.path.join(args.out, "hcpcs.json"), {})
    else:
        hcpcs_url = args.hcpcs_url
        if not hcpcs_url:
            links = index_links(HCPCS_INDEX)
            hcpcs_url = next((l for l in links if "alpha-numeric-hcpcs" in l), None)
            if not hcpcs_url:
                raise SystemExit("could not locate HCPCS zip on CMS index page")
        print(f"HCPCS  <- {hcpcs_url}")
        codes, ccounts, craw, cmember = build_hcpcs(hcpcs_url)
        print(f"       kept {len(codes)} public-domain Level II; "
              f"excluded {ccounts['ama']} CPT / {ccounts['ada']} CDT")
        record(dataset="hcpcs", url=hcpcs_url, member=cmember,
                         sha256=sha256(craw), bytes=len(craw),
                         kept=len(codes), excluded=ccounts)
        write(os.path.join(args.out, "hcpcs.json"), codes)
        written.append("hcpcs.json")

    # ---- Part B ASP payment limits ---------------------------------------
    if "asp" in skip:
        carry("asp")
    else:
        asp_url = args.asp_url
        if not asp_url:
            links = index_links(ASP_INDEX)
            asp_url = next((l for l in links if "payment-limit" in l), None)
            if not asp_url:
                raise SystemExit("could not locate ASP payment-limit zip on CMS index page")
        print(f"ASP    <- {asp_url}")
        asp, acounts, araw, amember = build_asp(asp_url)
        pub = sum(1 for v in asp.values() if "licensed" not in v)
        print(f"       {len(asp)} payment limits ({pub} with descriptions, "
              f"{len(asp) - pub} amount-only under AMA/ADA)")
        record(dataset="asp", url=asp_url, member=amember,
                         sha256=sha256(araw), bytes=len(araw),
                         kept=len(asp), excluded=acounts)
        write(os.path.join(args.out, "asp.json"), asp)
        written.append("asp.json")

    # ---- DMEPOS fee schedule ---------------------------------------------
    if "dmepos" in skip:
        carry("dmepos")
    else:
        dmepos_url = args.dmepos_url or discover_dmepos_url()
        if not dmepos_url:
            print("WARNING: could not locate a DMEPOS fee-schedule zip; skipping. "
                  "Durable medical equipment will have no benchmark.", file=sys.stderr)
        else:
            print(f"DMEPOS <- {dmepos_url}")
            dmepos, dcounts, draw, dmember = build_dmepos(dmepos_url)
            print(f"       {len(dmepos)} public-domain DMEPOS fee lines "
                  f"(excluded {dcounts['ama']} CPT / {dcounts['ada']} CDT)")
            record(dataset="dmepos", url=dmepos_url, member=dmember,
                             sha256=sha256(draw), bytes=len(draw),
                             kept=len(dmepos), excluded=dcounts)
            write(os.path.join(args.out, "dmepos.json"), dmepos)
            written.append("dmepos.json")

    # ---- NADAC drug acquisition cost -------------------------------------
    if "nadac" in skip:
        carry("nadac")
    else:
        nadac_url = args.nadac_url or discover_nadac_url()
        if not nadac_url:
            print("WARNING: could not locate a NADAC csv; skipping. Drugs identified "
                  "by NDC will have no acquisition-cost benchmark.", file=sys.stderr)
        else:
            print(f"NADAC  <- {nadac_url}")
            nadac, nadac_asof, nraw = build_nadac(nadac_url)
            print(f"       {len(nadac)} NDCs at latest surveyed price "
                  f"(most recent effective date {nadac_asof or 'unknown'})")
            record(dataset="nadac", url=nadac_url,
                             member=os.path.basename(nadac_url),
                             sha256=sha256(nraw), bytes=len(nraw), kept=len(nadac),
                             excluded={"public": len(nadac), "ama": 0, "ada": 0,
                                       "unknown": 0},
                             asof=nadac_asof)
            write(os.path.join(args.out, "nadac.json"), nadac)
            written.append("nadac.json")

    # ---- MS-DRG national averages ----------------------------------------
    if "drg" in skip:
        carry("drg")
    else:
        drg_url = args.drg_url or discover_drg_url()
        if not drg_url:
            print("WARNING: could not locate the CMS inpatient by-geography csv; "
                  "skipping. Inpatient stays will have no DRG benchmark.",
                  file=sys.stderr)
        else:
            print(f"DRG    <- {drg_url}")
            drg, graw = build_drg(drg_url)
            print(f"       {len(drg)} national MS-DRG averages")
            record(dataset="drg", url=drg_url,
                             member=os.path.basename(drg_url),
                             sha256=sha256(graw), bytes=len(graw), kept=len(drg),
                             excluded={"public": len(drg), "ama": 0, "ada": 0,
                                       "unknown": 0})
            write(os.path.join(args.out, "drg.json"), drg)
            written.append("drg.json")

    write(os.path.join(args.out, "manifest.json"), dict(
        built=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        note=("Public-domain subset only. CPT (AMA) and CDT (ADA) codes and "
              "descriptions are excluded by tools/build_data.py. See NOTICE.md."),
        sources=prov))
    print(f"\nwrote {', '.join(written + ['manifest.json'])} to {args.out}")


def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f, separators=(",", ":"), sort_keys=True)
    print(f"  {path}  {os.path.getsize(path):,} bytes")


if __name__ == "__main__":
    sys.exit(main())
