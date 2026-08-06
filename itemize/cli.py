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

    n = sub.add_parser("ncci", help="manage the AMA-licensed NCCI edit files")
    n.add_argument("--accept", action="store_true", help="show and accept the AMA licence")
    n.add_argument("--refresh", action="store_true", help="re-download cached edits")
    n.set_defaults(func=cmd_ncci)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
