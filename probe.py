#!/usr/bin/env python3
"""Liveness + shape spike for CMS coding/pricing data sources.

Unlike openstacks/probe.py, this validates the RESPONSE SHAPE, not just the
status code. A 200 serving an SPA shell is a FAIL here.

Run:  python3 probe.py --json probe-results.json
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request

UA = "itemize-probe/0.1 (+open medical bill auditing research; contact: marquell.scott96@gmail.com)"
TIMEOUT = 30

# expect: json | zip | csv | html_links (page must link to a downloadable file)
T = [
    # ---- the catalog ------------------------------------------------------
    dict(id="cms.data.catalog", grp="catalog", expect="json",
         what="CMS open data DCAT catalog",
         url="https://data.cms.gov/data.json"),

    # ---- NCCI: the coding-error detector (the actual v1 payload) ----------
    dict(id="medicaid.ncci.page", grp="ncci", expect="html_links",
         what="Medicaid NCCI edit files (no login expected)",
         url="https://www.medicaid.gov/medicaid/program-integrity/national-correct-coding-initiative/medicaid-ncci-edit-files/index.html"),
    dict(id="cms.ncci.ptp", grp="ncci", expect="html_links",
         what="Medicare NCCI PTP edits landing",
         url="https://www.cms.gov/medicare/coding-billing/national-correct-coding-initiative-ncci-edits/medicare-ncci-procedure-procedure-ptp-edits"),
    dict(id="cms.ncci.mue", grp="ncci", expect="html_links",
         what="Medically Unlikely Edits (units-of-service caps)",
         url="https://www.cms.gov/medicare/coding-billing/national-correct-coding-initiative-ncci-edits/medicare-ncci-medically-unlikely-edits"),

    # ---- Medicare benchmark rates ----------------------------------------
    dict(id="cms.pfs.rvu", grp="rates", expect="html_links",
         what="PFS Relative Value Files (RVU + GPCI)",
         url="https://www.cms.gov/medicare/payment/fee-schedules/physician/pfs-relative-value-files"),
    dict(id="cms.opps.addb", grp="rates", expect="html_links",
         what="OPPS Addendum A/B (hospital outpatient rates)",
         url="https://www.cms.gov/medicare/payment/prospective-payment-systems/hospital-outpatient/addendum-a-b-updates"),
    dict(id="cms.clfs", grp="rates", expect="html_links",
         what="Clinical Lab Fee Schedule",
         url="https://www.cms.gov/medicare/payment/fee-schedules/clinical-laboratory-fee-schedule/clinical-laboratory-fee-schedule-files"),

    # ---- code sets --------------------------------------------------------
    dict(id="cms.hcpcs", grp="codes", expect="html_links",
         what="HCPCS Level II quarterly update (public domain)",
         url="https://www.cms.gov/medicare/coding-billing/healthcare-common-procedure-system/quarterly-update"),
    dict(id="cms.icd10cm", grp="codes", expect="html_links",
         what="ICD-10-CM files (public domain)",
         url="https://www.cms.gov/medicare/coding-billing/icd-10-codes"),

    # ---- provider identity -------------------------------------------------
    dict(id="npi.registry", grp="provider", expect="json",
         what="NPPES NPI Registry API (free, keyless)",
         url="https://npiregistry.cms.hhs.gov/api/?version=2.1&number=1003000126"),

    # ---- utilization / real paid amounts -----------------------------------
    dict(id="cms.hospital.general", grp="provider", expect="json",
         what="CMS Provider Data hospital directory",
         url="https://data.cms.gov/provider-data/api/1/datastore/query/xubh-q36u/0?limit=1"),
]


def sniff(body, ctype, expect):
    """Return (ok, note). Validates shape, not status."""
    head = body[:512]
    if expect == "json":
        try:
            json.loads(body.decode("utf-8", "replace"))
            return True, "parsed as json"
        except Exception as e:
            return False, f"not json ({type(e).__name__}); ctype={ctype}"
    if expect == "zip":
        return (head[:2] == b"PK", "zip magic" if head[:2] == b"PK" else f"not a zip; ctype={ctype}")
    if expect == "csv":
        line = head.split(b"\n")[0]
        return (line.count(b",") >= 2, "csv-ish header" if line.count(b",") >= 2 else "no csv header")
    if expect == "html_links":
        low = body.lower()
        if b"<html" not in low[:2000] and b"<!doctype" not in low[:2000]:
            return False, f"not html; ctype={ctype}"
        hits = sum(low.count(ext) for ext in (b".zip", b".xlsx", b".csv", b".txt"))
        if hits == 0:
            return False, "html but zero downloadable-file links (SPA shell or moved?)"
        return True, f"{hits} file links on page"
    return False, "unknown expectation"


def probe(t):
    req = urllib.request.Request(t["url"], headers={"User-Agent": UA, "Accept": "*/*"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read(600_000)
            ms = int((time.time() - t0) * 1000)
            ctype = r.headers.get("Content-Type", "")
            ok, note = sniff(body, ctype, t["expect"])
            return dict(status=r.status, ms=ms, ok=ok, note=note, bytes=len(body))
    except urllib.error.HTTPError as e:
        return dict(status=e.code, ms=int((time.time() - t0) * 1000), ok=False,
                    note="http error", bytes=0)
    except Exception as e:
        return dict(status=None, ms=int((time.time() - t0) * 1000), ok=False,
                    note=f"{type(e).__name__}: {str(e)[:80]}", bytes=0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    args = ap.parse_args()

    out, grp = [], None
    for t in T:
        if t["grp"] != grp:
            grp = t["grp"]
            print(f"\n[{grp}]")
        r = probe(t)
        out.append({**t, **r})
        mark = "PASS" if r["ok"] else "FAIL"
        print(f"  {mark}  {t['id']:<22} {str(r['status'] or '-'):>4} {r['ms']:>6}ms  {t['what']}")
        print(f"        └─ {r['note']}")

    good = sum(1 for r in out if r["ok"])
    print(f"\n{good}/{len(out)} passed shape validation.")
    if args.json:
        json.dump(out, open(args.json, "w"), indent=2)
        print(f"wrote {args.json}")


if __name__ == "__main__":
    main()
