"""Render an evidence packet you can hand to a billing office.

The output is deliberately plain and sourced. Every finding carries the file,
URL and checksum it came from, because the first thing a billing office does
with an unsourced claim is ignore it.
"""
from __future__ import annotations

from . import __version__
from .rules import SEV_ORDER

LABEL = {"high": "HIGH", "warn": "REVIEW", "notice": "QUESTION", "info": "NOTE"}

DISCLAIMER = """\
## What this is, and what it is not

This packet was produced by `itemize`, an open-source tool. It is **information,
not advice** -- it is not legal, medical or financial advice, and it does not
tell you what you owe.

Two limits matter when you read it:

* A **Medicare benchmark is not a price cap.** Hospitals are not required to
  bill Medicare rates, and commercial rates are legitimately higher. A large
  multiple is grounds to ask questions and to request a self-pay or financial
  assistance adjustment. It is not proof of an error.
* Only findings marked **recoverable** represent a directly disputable amount.
  Everything else is a question to ask, not a conclusion.

Verify anything here before relying on it. The sources are listed so you can.
"""


def render(lines, findings, ref, title="Itemized bill review", ctx=None):
    total = sum(l.charge for l in lines)
    # Line-level disputes and whole-bill protections must NOT be added together.
    # An entitlement (charity care, a prohibited balance bill) is scoped to the
    # whole balance, so summing it with a duplicate charge produces a "disputable"
    # figure larger than the bill -- which is the fastest way to lose a billing
    # office's attention.
    line_level = [f for f in findings if f.recoverable and f.lines]
    whole_bill = [f for f in findings if f.recoverable and not f.lines]
    recoverable = min(sum(f.amount for f in line_level), total) if line_level else 0.0
    counts = {}
    for f in findings:
        counts[f.severity] = counts.get(f.severity, 0) + 1

    out = [f"# {title}", ""]
    out.append(f"- Lines parsed: **{len(lines)}**")
    out.append(f"- Total charges: **${total:,.2f}**")
    out.append(f"- Findings: **{len(findings)}**"
               + (f" ({', '.join(f'{LABEL[k]} {counts[k]}' for k in sorted(counts, key=lambda s: SEV_ORDER.get(s, 9)))})"
                  if counts else ""))
    if recoverable > 0:
        out.append(f"- Directly disputable line items: **${recoverable:,.2f}**")
    if whole_bill:
        out.append(f"- Whole-bill protections that may apply: **{len(whole_bill)}** "
                   "(see findings; these cover the balance, and are not added to the "
                   "line-item figure above)")
    out.append("")

    if ctx is not None:
        stated = []
        if ctx.insured is not None:
            stated.append("uninsured / self-pay" if ctx.insured is False else "insured")
        if ctx.deductible_met is not None:
            stated.append("deductible met" if ctx.deductible_met else "deductible not met")
        for flag, label in ((ctx.emergency, "emergency care"),
                            (ctx.out_of_network, "out-of-network provider"),
                            (ctx.in_network_facility, "in-network facility"),
                            (ctx.nonprofit_hospital, "non-profit hospital"),
                            (ctx.ground_ambulance, "includes ground ambulance")):
            if flag is True:
                stated.append(label)
        if ctx.good_faith_estimate is not None:
            stated.append(f"Good Faith Estimate ${ctx.good_faith_estimate:,.2f}")
        if ctx.plan_funding:
            stated.append(ctx.plan_funding.replace("_", " "))
        if ctx.state:
            stated.append(ctx.state)
        if stated:
            out.append("**You told us:** " + "; ".join(stated) + ".")
            out.append("")

    if not findings:
        out.append("No findings from the open ruleset. This does **not** mean the bill "
                   "is correct -- it means these particular checks did not fire. "
                   "CPT-coded lines are not benchmarked in this tier; see NOTICE.md.")
        out.append("")
    else:
        out.append("## Findings")
        out.append("")
        for i, f in enumerate(findings, 1):
            out.append(f"### {i}. [{LABEL.get(f.severity, f.severity.upper())}] {f.title}")
            out.append("")
            out.append(f.detail)
            out.append("")
            if f.amount:
                kind = "disputable" if f.recoverable else "implicated"
                out.append(f"*Amount {kind}: ${f.amount:,.2f}*")
                out.append("")
            out.append(f"> **Source:** {f.citation}")
            out.append("")

    out.append("## Lines reviewed")
    out.append("")
    out.append("| # | Date | Code | Description | Units | Charge |")
    out.append("|---|------|------|-------------|-------|--------|")
    for ln in lines:
        desc = (ln.desc or ref.describe(ln.code) or "").replace("|", "/")[:60]
        out.append(f"| {ln.idx} | {ln.date or '-'} | {ln.code or '-'} | {desc} | "
                   f"{ln.units:g} | ${ln.charge:,.2f} |")
    out.append("")

    out.append("## Data sources")
    out.append("")
    srcs = ref.manifest.get("sources", [])
    if srcs:
        for s in srcs:
            out.append(f"- **{s.get('dataset')}** — `{s.get('member')}`  ")
            out.append(f"  {s.get('url')}  ")
            out.append(f"  sha256 `{s.get('sha256','')[:32]}…`, "
                       f"{s.get('kept')} records retained")
        out.append(f"- Reference data built: {ref.manifest.get('built','unknown')}")
    else:
        out.append("- No manifest found; run `python3 tools/build_data.py` to "
                   "regenerate reference data with provenance.")
    out.append("")
    if ctx is not None:
        from .letters import LETTERS, suggest
        picks = suggest(ctx, findings)
        if picks:
            out.append("## Suggested next letters")
            out.append("")
            for k in picks:
                out.append(f"- **{k}** — {LETTERS[k][0]}  ")
                out.append(f"  `python3 -m itemize.cli letter {k}`")
            out.append("")

    out.append(DISCLAIMER)
    return "\n".join(out)


def to_json(lines, findings, ref, ctx=None, title="Itemized bill review"):
    """Machine-readable review.

    Same numbers as the markdown packet and the same refusal to conflate them:
    `disputable_line_items` counts only recoverable LINE findings, and whole-bill
    protections are reported separately rather than summed into it.
    """
    import json as _json
    from .rules import next_actions

    total = sum(l.charge for l in lines)
    line_level = [f for f in findings if f.recoverable and f.lines]
    whole_bill = [f for f in findings if f.recoverable and not f.lines]
    payload = {
        "tool": "itemize",
        "version": __version__,
        "title": title,
        "disclaimer": ("Information, not advice. A Medicare benchmark is not a price "
                       "cap and NADAC is acquisition cost, not an allowed amount. "
                       "Only findings with recoverable=true represent a directly "
                       "disputable amount."),
        "summary": {
            "lines": len(lines),
            "total_charges": round(total, 2),
            "findings": len(findings),
            "disputable_line_items": round(
                min(sum(f.amount for f in line_level), total), 2),
            "whole_bill_protections": len(whole_bill),
        },
        "context": ctx.to_dict() if ctx else None,
        "next_actions": next_actions(findings, ctx),
        "findings": [f.to_dict() for f in findings],
        "lines": [
            {"idx": l.idx, "date": l.date, "code": l.code, "ndc": l.ndc,
             "description": l.desc or ref.describe(l.code), "units": l.units,
             "unit_price": l.unit_price, "charge": round(l.charge, 2),
             "revenue_code": l.revenue_code, "modifiers": list(l.modifiers or [])}
            for l in lines
        ],
        "reference_data": {
            "built": ref.manifest.get("built"),
            "sources": ref.manifest.get("sources", []),
            "stale": [{"dataset": n, "age_days": a, "refresh_days": lim}
                      for n, a, lim in ref.stale_datasets()],
        },
    }
    return _json.dumps(payload, indent=2, sort_keys=False)
