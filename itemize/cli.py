"""itemize command line interface."""
from __future__ import annotations

import argparse
import os
import sys

from . import letters as letters_mod
from . import ncci as ncci_mod
from .context import Context
from .eob import parse_eob
from .evidence import render
from .model import Reference
from .parse import parse
from .rules import audit


def cmd_audit(args):
    with open(args.bill) as f:
        text = f.read()
    lines = parse(text)
    if not lines:
        print("Could not parse any charge lines from that file.", file=sys.stderr)
        print("Expected a CSV/TSV with code, units and charge columns, or bill "
              "text pasted from a PDF.", file=sys.stderr)
        return 2

    ctx = Context(
        insured=(None if args.insured is None else args.insured),
        deductible_met=(None if args.deductible_met is None else args.deductible_met),
        emergency=args.emergency or None,
        out_of_network=args.out_of_network or None,
        in_network_facility=args.in_network_facility or None,
        nonprofit_hospital=args.nonprofit or None,
        ground_ambulance=args.ambulance or None,
        good_faith_estimate=args.gfe,
        state=args.state or "",
        drg=args.drg or "",
        plan_funding=args.plan_funding,
    )

    eob_rows = None
    if args.eob:
        with open(args.eob) as f:
            eob_rows = parse_eob(f.read())
        if not eob_rows:
            print(f"warning: could not read any rows from {args.eob}", file=sys.stderr)
        else:
            print(f"read {len(eob_rows)} EOB rows from {args.eob}", file=sys.stderr)

    ref = Reference(args.data) if args.data else Reference()
    if not ref.available:
        print(f"warning: no reference data in {ref.dir}; "
              "run `python3 tools/build_data.py` first. "
              "Structural checks will still run.\n", file=sys.stderr)

    findings = audit(lines, ref, ctx, eob_rows)

    if args.ncci:
        if not ncci_mod.has_consent():
            print("NCCI checks need the AMA licence; run `itemize ncci --accept`.",
                  file=sys.stderr)
        else:
            pairs = ncci_mod.load_pairs()
            hits = ncci_mod.check(lines, pairs)
            from .model import Finding
            for a, b, mod in hits:
                findings.append(Finding(
                    rule="ncci_ptp",
                    severity="high" if mod == "0" else "warn",
                    title=f"{a.code} and {b.code} are an NCCI edit pair",
                    detail=(f"Lines {a.idx} and {b.idx} bill {a.code} and {b.code} "
                            f"together. CMS's NCCI edits list this pair as not "
                            f"separately reportable"
                            + (" and allow no modifier override (indicator 0)."
                               if mod == "0" else
                               " unless an appropriate modifier applies "
                               f"(indicator {mod or 'unknown'}).")
                            + " Ask which modifier was submitted and why."),
                    lines=[a.idx, b.idx],
                    citation="CMS NCCI PTP edit files, retrieved under your accepted "
                             "AMA licence (not redistributed by itemize).",
                    amount=min(a.charge, b.charge),
                    recoverable=(mod == "0"),
                ))
            from .rules import SEV_ORDER
            findings.sort(key=lambda f: (SEV_ORDER.get(f.severity, 9), -f.amount))

    if args.mrf:
        from . import mrf as mrf_mod
        from .rules import SEV_ORDER
        codes = {l.code for l in lines if l.code}
        print(f"reading {args.mrf} …", file=sys.stderr)
        try:
            index, scanned = mrf_mod.index_for(args.mrf, codes)
        except Exception as exc:                       # noqa: BLE001 -- report, don't crash
            print(f"warning: could not read the machine-readable file: {exc}",
                  file=sys.stderr)
        else:
            print(f"scanned {scanned:,} records; matched {len(index)} of "
                  f"{len(codes)} codes on your bill", file=sys.stderr)
            if not index and scanned:
                print("note: none of your codes appear in that file. Check it is the "
                      "right hospital and the right setting (inpatient vs outpatient).",
                      file=sys.stderr)
            findings.extend(mrf_mod.compare(lines, index, args.mrf, ctx))
            findings.sort(key=lambda f: (SEV_ORDER.get(f.severity, 9), -f.amount))

    out = render(lines, findings, ref, title=args.title, ctx=ctx)
    if args.out:
        with open(args.out, "w") as f:
            f.write(out)
        print(f"wrote {args.out} ({len(findings)} findings)")
    else:
        print(out)
    return 0


def cmd_letter(args):
    ctx = Context(good_faith_estimate=args.gfe)
    text = letters_mod.render(args.kind, ctx, [], args.total)
    if args.out:
        with open(args.out, "w") as f:
            f.write(text)
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


def cmd_mrf(args):
    """Look up published prices for specific codes, without auditing a bill."""
    from . import mrf as mrf_mod
    codes = [c.strip().upper() for c in (args.codes or "").split(",") if c.strip()]
    if not codes:
        print("give at least one code: --codes J1885,A4550", file=sys.stderr)
        return 2
    print(f"reading {args.source} …", file=sys.stderr)
    index, scanned = mrf_mod.index_for(args.source, codes)
    print(f"scanned {scanned:,} records\n", file=sys.stderr)
    if not index:
        print("No published price found for any of those codes in that file.")
        return 1
    width = max(len(c) for c in index)
    print(f"{'CODE'.ljust(width)}  {'GROSS':>12}  {'CASH':>12}  {'MIN':>12}  "
          f"{'MAX':>12}  DESCRIPTION")
    for code in codes:
        row = index.get(code)
        if not row:
            print(f"{code.ljust(width)}  {'—':>12}  {'—':>12}  {'—':>12}  "
                  f"{'—':>12}  (not published in this file)")
            continue

        def col(v):
            return f"{v:,.2f}" if v else "—"
        print(f"{code.ljust(width)}  {col(row['gross']):>12}  {col(row['cash']):>12}  "
              f"{col(row['min']):>12}  {col(row['max']):>12}  {row['desc'][:44]}")
    print("\nPublished by the hospital under 45 CFR 180.50. A cash price generally "
          "applies to self-pay patients; check which applies to you.")
    return 0


def cmd_teach(args):
    """Practice bills with seeded errors, and the key."""
    from . import teaching
    from .eob import parse_eob as _parse_eob

    if args.action == "list":
        print(f"{'ID':<20} {'LEVEL':<14} TITLE")
        for c in teaching.CASES:
            print(f"{c['id']:<20} {c['level']:<14} {c['title']}")
        print("\nitemize teach show <id>     the bill, for the student")
        print("itemize teach key <id>      what is wrong with it, for the instructor")
        print("itemize teach score <id>    what this engine actually catches")
        return 0

    case = teaching.get(args.case or "")
    if not case:
        print(f"No such case: {args.case!r}. Try `itemize teach list`.", file=sys.stderr)
        return 2

    if args.action == "show":
        if args.out:
            with open(args.out, "w") as f:
                f.write(case["bill"])
            print(f"wrote {args.out}")
            if case.get("eob"):
                eob_path = os.path.splitext(args.out)[0] + "-eob.csv"
                with open(eob_path, "w") as f:
                    f.write(case["eob"])
                print(f"wrote {eob_path}")
        else:
            print(case["bill"], end="")
            if case.get("eob"):
                print("\n--- EOB ---")
                print(case["eob"], end="")
        return 0

    if args.action == "key":
        print(f"# {case['title']}\n")
        print(f"{case['setup']}\n")
        print(f"Seeded errors ({len(case['seeded'])}):\n")
        for i, (rule, lines, why) in enumerate(case["seeded"], 1):
            where = f"line{'s' if len(lines) != 1 else ''} {', '.join(map(str, lines))}" \
                if lines else "whole bill"
            print(f"{i}. [{rule}] {where}")
            print(f"   {why}\n")
        print("Remember: only duplicate billing, an EOB overage and a Good Faith "
              "Estimate overage are directly disputable. Everything else is a "
              "question to ask.")
        return 0

    # score
    ref = Reference(args.data) if args.data else Reference()
    lines = parse(case["bill"])
    ctx = teaching.context_for(case)
    eob_rows = _parse_eob(case["eob"]) if case.get("eob") else None
    findings = audit(lines, ref, ctx, eob_rows)
    hits, misses, extra = teaching.score(case, findings)

    print(f"# {case['title']}\n")
    print(f"caught {len(hits)} of {len(hits) + len(misses)} seeded errors\n")
    for rule, lines_, _why in hits:
        print(f"  found    {rule}")
    for rule, lines_, _why in misses:
        print(f"  MISSED   {rule}")
    if extra:
        print("\nalso reported, not seeded:")
        for rule in extra:
            print(f"  {rule}")
    if misses and not ref.available:
        print("\nSome misses are because no reference data is built. "
              "Run tools/build_data.py.", file=sys.stderr)
    return 0 if not misses else 1


def cmd_ncci(args):
    if args.accept:
        ok = ncci_mod.request_consent()
        if not ok:
            return 1
    if not ncci_mod.has_consent():
        print("No AMA licence acceptance recorded. Run: itemize ncci --accept")
        return 1
    print("Licence acceptance on file. Downloading NCCI PTP edits…")
    pairs = ncci_mod.load_pairs(refresh=args.refresh)
    print(f"{len(pairs):,} procedure-to-procedure pairs cached in {ncci_mod.CACHE}")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="itemize",
        description="Audit an itemized medical bill against public CMS data.")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("audit", help="review a bill and write an evidence packet")
    a.add_argument("bill", help="CSV/TSV export, or text pasted from a PDF")
    a.add_argument("-o", "--out", help="write markdown here (default: stdout)")
    a.add_argument("--data", help="reference data directory (default: web/data)")
    a.add_argument("--title", default="Itemized bill review")
    a.add_argument("--ncci", action="store_true",
                   help="also run NCCI unbundling checks (requires accepted AMA licence)")
    a.add_argument("--eob", help="EOB export to cross-check against (CSV)")
    a.add_argument("--mrf", metavar="URL_OR_PATH",
                   help="your hospital's price transparency machine-readable file. "
                        "Compares each line against the hospital's own published "
                        "cash price. Streamed, so a multi-gigabyte file is fine; "
                        "nothing from it is written to disk.")

    c = a.add_argument_group(
        "coverage context",
        "Who is exposed to the charge changes which findings matter. Omit these "
        "and nothing is assumed.")
    c.add_argument("--insured", dest="insured", action="store_true", default=None)
    c.add_argument("--self-pay", dest="insured", action="store_false",
                   help="you are uninsured or paying cash")
    c.add_argument("--deductible-met", dest="deductible_met", action="store_true",
                   default=None)
    c.add_argument("--deductible-unmet", dest="deductible_met", action="store_false",
                   help="you have not met your deductible")
    c.add_argument("--emergency", action="store_true", help="care was emergency")
    c.add_argument("--out-of-network", action="store_true")
    c.add_argument("--in-network-facility", action="store_true")
    c.add_argument("--nonprofit", action="store_true",
                   help="billed by a tax-exempt (501(c)(3)) hospital")
    c.add_argument("--ambulance", action="store_true",
                   help="bill includes a ground ambulance ride")
    c.add_argument("--gfe", type=float, help="dollar amount of your Good Faith Estimate")
    c.add_argument("--state", help="two-letter state code")
    c.add_argument("--drg", help="MS-DRG for an inpatient stay, from form locator 71 "
                                 "of a UB-04 (e.g. 470). Compares the bill against "
                                 "CMS's national average charge for that DRG.")
    c.add_argument("--plan-funding", choices=["fully_insured", "self_funded"],
                   help="fully insured plans are reached by state law; self-funded "
                        "(ERISA) plans generally are not")
    a.set_defaults(func=cmd_audit)

    lt = sub.add_parser("letter", help="draft a letter to send")
    lt.add_argument("kind", choices=sorted(letters_mod.LETTERS),
                    help="; ".join(f"{k}: {v[0]}" for k, v in sorted(letters_mod.LETTERS.items())))
    lt.add_argument("--gfe", type=float, help="Good Faith Estimate amount")
    lt.add_argument("--total", type=float, help="bill total")
    lt.add_argument("-o", "--out")
    lt.set_defaults(func=cmd_letter)

    t = sub.add_parser("teach", help="practice bills with seeded errors, and the key")
    t.add_argument("action", choices=["list", "show", "key", "score"])
    t.add_argument("case", nargs="?", help="case id, from `itemize teach list`")
    t.add_argument("-o", "--out", help="write the bill here instead of stdout")
    t.add_argument("--data", help="reference data directory (default: web/data)")
    t.set_defaults(func=cmd_teach)

    m = sub.add_parser("mrf", help="look up codes in a hospital's published price file")
    m.add_argument("source", metavar="URL_OR_PATH",
                   help="the hospital's machine-readable file (JSON or CSV)")
    m.add_argument("--codes", required=True,
                   help="comma-separated codes to look up, e.g. J1885,A4550")
    m.set_defaults(func=cmd_mrf)

    n = sub.add_parser("ncci", help="manage the AMA-licensed NCCI edit files")
    n.add_argument("--accept", action="store_true", help="show and accept the AMA licence")
    n.add_argument("--refresh", action="store_true", help="re-download cached edits")
    n.set_defaults(func=cmd_ncci)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
