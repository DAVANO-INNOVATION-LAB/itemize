"""Parity between itemize/rules.py and web/rules.js.

Two rule engines, one asserted to mirror the other, with nothing enforcing it,
is how a CLI and a web app end up telling the same person different things
about their bill. This runs both over identical input and diffs the findings.

Text is deliberately NOT compared -- the two differ in punctuation (-- vs em
dash). What must agree is the structure: which rules fired, at what severity,
over which lines, for how much, and whether the amount is recoverable.

Skips cleanly when Node is unavailable so the suite still runs.
"""
import datetime
import json
import os
import shutil
import subprocess
import sys
import unittest
from dataclasses import asdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from itemize.context import Context            # noqa: E402
from itemize.model import Line, Reference      # noqa: E402
from itemize.eob import parse_eob             # noqa: E402
from itemize.parse import parse                # noqa: E402
from itemize.rules import audit                # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRIVER = os.path.join(ROOT, "tests", "parity_driver.js")
DATA = os.path.join(ROOT, "web", "data")
NODE = shutil.which("node")


def parse_shape(l):
    """Every Line field the rules can read.

    Comparing a subset is not a parity check: `unit_price` was parsed by the
    Python engine and dropped by the JS one for as long as this harness only
    diffed idx/code/units/charge/date, which silently disabled
    unit_price_arithmetic in the browser while the CLI kept firing it.
    """
    return {
        "idx": l.idx, "code": l.code, "desc": l.desc, "units": l.units,
        "charge": round(l.charge, 2), "date": l.date,
        "modifiers": list(l.modifiers or []), "revenue_code": l.revenue_code,
        "unit_price": round(l.unit_price or 0, 2), "ndc": l.ndc,
        "suspect_columns": bool(l.suspect_columns),
    }


def py_shape(findings):
    return [{
        "rule": f.rule,
        "severity": f.severity,
        "lines": sorted(f.lines or []),
        "amount": round(f.amount, 2),
        "recoverable": bool(f.recoverable),
    } for f in findings]


def run_js(lines, ref, ctx=None, eob=None):
    payload = {
        "lines": [asdict(l) for l in lines],
        "ref": {"hcpcs": ref.hcpcs, "asp": ref.asp, "dmepos": ref.dmepos,
                "nadac": ref.nadac, "drg": ref.drg,
                "states": ref.states_raw, "manifest": ref.manifest},
        "context": ctx.to_dict() if ctx else None,
        "eob": eob,
    }
    p = subprocess.run([NODE, DRIVER], input=json.dumps(payload),
                       capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        raise AssertionError(f"node driver failed: {p.stderr[:2000]}")
    return json.loads(p.stdout)


@unittest.skipIf(NODE is None, "node not installed; parity cannot be checked")
class TestParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ref = Reference(DATA)
        if not cls.ref.available:
            raise unittest.SkipTest("reference data missing; run tools/build_data.py")

    def assertParity(self, lines, ctx=None, eob=None, label=""):
        py = py_shape(audit(lines, self.ref, ctx, eob))
        js = run_js(lines, self.ref, ctx, eob)
        self.assertEqual(py, js, f"engines disagree{(' on ' + label) if label else ''}")
        return py

    def test_fixture_bill_no_context(self):
        with open(os.path.join(ROOT, "tests", "fixtures", "sample_bill.csv")) as f:
            lines = parse(f.read())
        got = self.assertParity(lines, label="fixture bill")
        self.assertTrue(got, "expected at least one finding to compare")

    def test_self_pay_context_adds_rights(self):
        with open(os.path.join(ROOT, "tests", "fixtures", "sample_bill.csv")) as f:
            lines = parse(f.read())
        ctx = Context(insured=False, nonprofit_hospital=True, emergency=True)
        got = self.assertParity(lines, ctx, label="self-pay context")
        rules = {g["rule"] for g in got}
        self.assertIn("right_gfe_missing", rules)
        self.assertIn("right_charity_care", rules)
        self.assertIn("right_nsa_emergency", rules)

    def test_insured_post_deductible_demotes_price_findings(self):
        lines = [Line(idx=1, code="J1885", units=2, charge=180.0, date="2026-03-14")]
        ctx = Context(insured=True, deductible_met=True)
        got = self.assertParity(lines, ctx, label="insured post-deductible")
        asp = [g for g in got if g["rule"] == "asp_benchmark"]
        self.assertEqual(len(asp), 1)
        self.assertEqual(asp[0]["severity"], "info")

    def test_pre_deductible_keeps_price_findings_loud(self):
        lines = [Line(idx=1, code="J1885", units=2, charge=180.0, date="2026-03-14")]
        ctx = Context(insured=True, deductible_met=False)
        got = self.assertParity(lines, ctx, label="pre-deductible")
        asp = [g for g in got if g["rule"] == "asp_benchmark"]
        self.assertEqual(asp[0]["severity"], "high")

    def test_dmepos_and_modifiers_and_revenue_codes(self):
        lines = [
            Line(idx=1, code="E0114", units=1, charge=900.0, date="d"),
            Line(idx=2, code="J1885", units=1, charge=50.0, date="d",
                 modifiers=["59"]),
            Line(idx=3, code="E0601", units=1, charge=60.0, date="d",
                 revenue_code="0250"),
            Line(idx=4, code="A4550", units=2, charge=100.0, date="d",
                 unit_price=20.0),
        ]
        got = self.assertParity(lines, label="mixed new rules")
        rules = {g["rule"] for g in got}
        self.assertIn("dmepos_benchmark", rules)
        self.assertIn("modifier_flag", rules)
        self.assertIn("revenue_code_mismatch", rules)
        self.assertIn("unit_price_arithmetic", rules)

    def test_eob_cross_check(self):
        lines = [Line(idx=1, code="J1885", units=1, charge=180.0, date="d"),
                 Line(idx=2, code="A4550", units=1, charge=68.0, date="d")]
        eob = [{"code": "J1885", "date": "d", "billed": 180.0, "allowed": 40.0,
                "plan_paid": 30.0, "patient": 10.0}]
        got = self.assertParity(lines, None, eob, label="EOB cross-check")
        rules = {g["rule"] for g in got}
        self.assertIn("eob_line_mismatch", rules)
        self.assertIn("eob_line_absent", rules)

    def test_parsers_agree_on_csv(self):
        with open(os.path.join(ROOT, "tests", "fixtures", "sample_bill.csv")) as f:
            text = f.read()
        py = [parse_shape(l) for l in parse(text)]
        p = subprocess.run([NODE, DRIVER], input=json.dumps({"mode": "parse", "text": text}),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr[:1000])
        self.assertEqual(py, json.loads(p.stdout), "parsers disagree on the fixture CSV")

    def assertActionParity(self, lines, ctx=None, eob=None, label=""):
        from itemize.rules import next_actions
        py = [{"key": a["key"], "letter": a["letter"], "amount": round(a["amount"], 2),
               "lines": a["lines"]}
              for a in next_actions(audit(lines, self.ref, ctx, eob), ctx)]
        payload = {
            "mode": "actions",
            "lines": [asdict(l) for l in lines],
            "ref": {"hcpcs": self.ref.hcpcs, "asp": self.ref.asp,
                    "dmepos": self.ref.dmepos, "nadac": self.ref.nadac,
                    "drg": self.ref.drg, "states": self.ref.states_raw,
                    "manifest": self.ref.manifest},
            "context": ctx.to_dict() if ctx else None,
            "eob": eob,
        }
        p = subprocess.run([NODE, DRIVER], input=json.dumps(payload),
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr[:2000])
        self.assertEqual(py, json.loads(p.stdout),
                         f"engines disagree on next actions{(' for ' + label) if label else ''}")
        return py

    def test_next_actions_agree(self):
        """The action plan is the first thing the reader reads. If the CLI packet
        and the browser rank it differently they are two different tools."""
        with open(os.path.join(ROOT, "tests", "fixtures", "sample_bill.csv")) as f:
            lines = parse(f.read())
        for ctx, label in (
            (None, "no context"),
            (Context(insured=False, nonprofit_hospital=True), "self-pay non-profit"),
            (Context(insured=False, good_faith_estimate=100.0), "GFE exceeded"),
            (Context(insured=True, deductible_met=True, emergency=True), "insured ER"),
            (Context(insured=False, state="MA"), "self-pay Massachusetts"),
        ):
            got = self.assertActionParity(lines, ctx, label=label)
            self.assertLessEqual(len(got), 3, "action list must stay capped")

    def test_itemized_bill_action_outranks_everything(self):
        """Nothing below can be checked without an itemized statement."""
        from itemize.rules import next_actions
        lines = parse("Code,Description,Qty,Charges\n"
                      ",ROOM AND BOARD,1,9000.00\n"
                      ",PHARMACY,1,2000.00\n")
        ctx = Context(insured=False, nonprofit_hospital=True, good_faith_estimate=10.0)
        acts = next_actions(audit(lines, self.ref, ctx), ctx)
        self.assertEqual(acts[0]["key"], "itemized")
        self.assertActionParity(lines, ctx, label="itemized first")

    def test_stale_reference_data_agrees(self):
        """Both engines must date the same manifest the same way.

        A staleness banner that fires in the CLI and not the browser would mean
        one of them quietly vouches for data the other flags.
        """
        import copy
        from itemize.model import Reference as Ref

        for stamp, expect_stale in (("2020-01-01", True),
                                    (datetime.date.today().isoformat(), False)):
            ref = copy.copy(self.ref)
            ref.manifest = {
                "built": stamp,
                "sources": [
                    {"dataset": d, "retrieved": stamp, "member": f"{d}.csv",
                     "url": "http://example/x", "sha256": "ab" * 32, "kept": 1}
                    for d in ("hcpcs", "asp", "dmepos", "nadac", "drg")
                ],
            }
            self.assertEqual(bool(ref.stale_datasets()), expect_stale, stamp)

            lines = [Line(idx=1, code="A4550", desc="TRAY", units=1, charge=68.0)]
            py = py_shape(audit(lines, ref, None, None))
            payload = {
                "lines": [asdict(l) for l in lines],
                "ref": {"hcpcs": ref.hcpcs, "asp": ref.asp, "dmepos": ref.dmepos,
                        "nadac": ref.nadac, "drg": ref.drg,
                        "states": ref.states_raw, "manifest": ref.manifest},
                "context": None, "eob": None,
            }
            p = subprocess.run([NODE, DRIVER], input=json.dumps(payload),
                               capture_output=True, text=True, timeout=120)
            self.assertEqual(p.returncode, 0, p.stderr[:2000])
            self.assertEqual(py, json.loads(p.stdout),
                             f"engines disagree on staleness at {stamp}")
            fired = any(f["rule"] == "stale_reference_data" for f in py)
            self.assertEqual(fired, expect_stale, stamp)

    def test_stale_finding_never_carries_a_disputable_amount(self):
        import copy
        ref = copy.copy(self.ref)
        ref.manifest = {"built": "2020-01-01", "sources": [
            {"dataset": "nadac", "retrieved": "2020-01-01", "member": "n.csv",
             "url": "http://example/n", "sha256": "cd" * 32, "kept": 1}]}
        for f in audit([Line(idx=1, code="A4550", units=1, charge=68.0)], ref):
            if f.rule == "stale_reference_data":
                self.assertEqual(f.amount, 0)
                self.assertFalse(f.recoverable)
                self.assertEqual(f.lines, [])

    def test_nadac_benchmark_agrees(self):
        """Needs a real NDC from the shipped table, so pick one at runtime."""
        ndc = next((k for k, v in self.ref.nadac.items()
                    if v.get("u") == "EA" and 0 < v.get("p", 0) < 0.5), None)
        if not ndc:
            self.skipTest("no suitable NADAC record in the shipped table")
        price = self.ref.nadac[ndc]["p"]
        lines = [
            Line(idx=1, code="PHRM01", desc="TABLET", units=30,
                 charge=round(price * 30 * 300, 2), ndc=ndc),          # ~300x
            Line(idx=2, code="PHRM02", desc="TABLET", units=10,
                 charge=round(price * 10 * 2, 2), ndc=ndc),            # ordinary margin
        ]
        got = self.assertParity(lines, label="NADAC benchmark")
        self.assertTrue(any(f["rule"] == "nadac_benchmark" for f in got))
        self.assertFalse(any(f["rule"] == "nadac_benchmark" and f["recoverable"]
                             for f in got))

    def test_nadac_stands_down_when_asp_prices_the_line_in_both_engines(self):
        ndc = next(iter(self.ref.nadac), None)
        asp_code = next((c for c, v in self.ref.asp.items() if v.get("limit", 0) > 0), None)
        if not ndc or not asp_code:
            self.skipTest("shipped tables missing nadac/asp records")
        lines = [Line(idx=1, code=asp_code, desc="DRUG", units=5,
                      charge=50000.0, ndc=ndc)]
        got = self.assertParity(lines, label="NADAC/ASP precedence")
        self.assertFalse(any(f["rule"] == "nadac_benchmark" for f in got))

    def test_drg_benchmark_agrees(self):
        drg = next(iter(sorted(self.ref.drg)), None)
        if not drg:
            self.skipTest("drg table not built")
        avg = self.ref.drg[drg]["charge"]
        lines = [Line(idx=1, code="", desc="ROOM AND BOARD", units=1, charge=avg * 3)]
        got = self.assertParity(lines, Context(drg=drg), label="DRG above average")
        self.assertTrue(any(f["rule"] == "drg_benchmark" for f in got))
        # Whole-bill context must never carry a disputable amount.
        for f in got:
            if f["rule"] == "drg_benchmark":
                self.assertEqual(f["amount"], 0)
                self.assertFalse(f["recoverable"])
                self.assertEqual(f["lines"], [])

        lines = [Line(idx=1, code="", desc="ROOM AND BOARD", units=1, charge=avg)]
        self.assertParity(lines, Context(drg=drg), label="DRG at average")

    def test_unknown_and_zero_padded_drg_agree(self):
        drg = next(iter(sorted(self.ref.drg)), None)
        if not drg:
            self.skipTest("drg table not built")
        lines = [Line(idx=1, code="", desc="ROOM", units=1, charge=100000.0)]
        self.assertParity(lines, Context(drg="999"), label="unknown DRG")
        # "0470" must resolve the same way "470" does in both engines.
        self.assertParity(lines, Context(drg="0" + drg), label="zero-padded DRG")

    def test_parsers_agree_on_unit_price_and_ndc_columns(self):
        """Regression: the JS parser had no `unit_price` or `ndc` header alias.

        The Python engine read a unit-price column and fired
        unit_price_arithmetic; the browser read nothing and stayed silent on the
        same bill. Both columns are now parsed and both are diffed here.
        """
        with open(os.path.join(ROOT, "tests", "fixtures", "sample_bill_ndc.csv")) as f:
            text = f.read()
        lines = parse(text)
        self.assertTrue(any(l.unit_price > 0 for l in lines), "fixture must carry unit prices")
        self.assertTrue(any(l.ndc for l in lines), "fixture must carry NDCs")

        py = [parse_shape(l) for l in lines]
        p = subprocess.run([NODE, DRIVER], input=json.dumps({"mode": "parse", "text": text}),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr[:1000])
        self.assertEqual(py, json.loads(p.stdout), "parsers disagree on unit price / NDC")

        # ...and the rules built on those fields agree too.
        self.assertParity(lines, label="unit price + NDC bill")

    def test_parsers_agree_on_adversarial_bills(self):
        """A battery drawn from real divergences a differential fuzzer found.

        Each pattern below made the CLI and the browser read the same bill
        differently -- inch marks silently dropping a charge in one engine, a
        unit label read as a quantity in one and not the other, and so on.
        """
        cases = {
            "inch marks": 'Date,Code,Description,Qty,Charges\n'
                          '2026-03-14,A4550,5" CATHETER TUBING,2,120.00\n'
                          '2026-03-14,A4649,2" GAUZE PAD,10,45.00\n',
            "credits": "Date,Code,Description,Qty,Charges\n"
                       "2026-03-14,J1885,KETOROLAC,1,180.00\n"
                       "2026-03-14,,PATIENT PAYMENT,1,-500.00\n"
                       '2026-03-14,,ADJUSTMENT,1,"(1,200.00)"\n',
            "unit labels": "Date,Code,Description,Qty,Charges\n"
                           "2026-03-14,J1885,KETOROLAC,2 EA,180.00\n"
                           "2026-03-14,A4550,TRAY,3 doses,60.00\n",
            "ndc in qty": "Date,Code,Description,Qty,Charges\n"
                          "2026-03-14,J1885,KETOROLAC,00409-3799-01,180.00\n",
            "infinite qty": "Date,Code,Description,Qty,Charges\n"
                            "2026-03-14,A4550,TRAY,Infinity,100.00\n",
            "ragged row": "Date,Code,Description,Qty,Charges\n"
                          "2026-03-14,99283,ED VISIT,1,1,842.00\n",
            "multiline field": 'Date,Code,Description,Qty,Charges\n'
                               '2026-03-14,A4550,"WOUND CARE\nKIT",1,50.00\n',
            "byte order mark": "\ufeffDate,Code,Description,Qty,Charges\n"
                               "2026-03-14,J1885,KETOROLAC,2,180.00\n",
            "control chars": "Date,Code,Description,Qty,Charges\n"
                             "2026-03-14,J1885,nul\x00byte\ttab,1,10.00\n",
            "long digit run": "x " + "9" * 5000 + " 1.00\n",
            "embedded quotes": 'Date,Code,Description,Qty,Charges\n'
                               '2026-03-14,A4550,PT SAID "OUCH",1,10.00\n',
        }
        for label, text in cases.items():
            lines = parse(text)
            py = [parse_shape(l) for l in lines]
            p = subprocess.run([NODE, DRIVER],
                               input=json.dumps({"mode": "parse", "text": text}),
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(p.returncode, 0, p.stderr[:1000])
            self.assertEqual(py, json.loads(p.stdout), f"parsers disagree on {label}")
            if lines:
                self.assertParity(lines, label=label)

    def test_ragged_columns_rule_agrees(self):
        lines = parse("Date,Code,Description,Qty,Charges\n"
                      "2026-03-14,J1885,KETOROLAC,2,180.00\n"
                      "2026-03-14,99283,ED VISIT,1,1,842.00\n")
        got = self.assertParity(lines, label="ragged columns")
        ragged = [f for f in got if f["rule"] == "ragged_columns"]
        self.assertEqual(len(ragged), 1)
        self.assertEqual(ragged[0]["amount"], 0)
        self.assertFalse(ragged[0]["recoverable"])

    def test_eob_parsers_agree_on_signed_amounts(self):
        text = ("Code,Billed,Allowed,Patient Responsibility\n"
                "J1885,180.00,42.00,-5.00\n"
                "A4550,68.00,30.00,6.00\n")
        py = parse_eob(text)
        p = subprocess.run([NODE, DRIVER],
                           input=json.dumps({"mode": "parse_eob", "text": text}),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr[:1000])
        self.assertEqual(py, json.loads(p.stdout), "EOB parsers disagree on signs")
        self.assertEqual(py[0]["patient"], -5.0)

    def test_parsers_agree_on_pdf_style_columns(self):
        text = ("2026-03-14  99283  EMERGENCY DEPT VISIT LEVEL 3  1  1842.00\n"
                "2026-03-14  CHG40021  PHARMACY GENERAL CLASSIFICATION  1  412.75\n"
                "2026-03-14  J1885  KETOROLAC INJ 15MG  2  180.00\n")
        py = [parse_shape(l) for l in parse(text)]
        p = subprocess.run([NODE, DRIVER], input=json.dumps({"mode": "parse", "text": text}),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(py, json.loads(p.stdout), "parsers disagree on column-split text")

    def test_eob_parsers_agree(self):
        text = ("Code,Billed,Allowed,Plan Paid,Patient Responsibility\n"
                "J1885,180.00,40.00,30.00,10.00\n"
                "A4550,68.00,20.00,20.00,0.00\n")
        py = parse_eob(text)
        p = subprocess.run([NODE, DRIVER],
                           input=json.dumps({"mode": "parse_eob", "text": text}),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(py, json.loads(p.stdout), "EOB parsers disagree")

    def test_state_layer_agrees_massachusetts(self):
        lines = [Line(idx=1, code="J1885", units=1, charge=180.0, date="d")]
        ctx = Context(insured=False, state="MA", ground_ambulance=True)
        got = self.assertParity(lines, ctx, label="MA state layer")
        rules = {g["rule"] for g in got}
        self.assertIn("state_assistance_program", rules)
        self.assertIn("state_ambulance_unknown", rules)

    def test_state_layer_agrees_south_carolina(self):
        lines = [Line(idx=1, code="J1885", units=1, charge=180.0, date="d")]
        ctx = Context(insured=False, state="SC", ground_ambulance=True)
        got = self.assertParity(lines, ctx, label="SC state layer")
        rules = {g["rule"] for g in got}
        self.assertIn("state_ambulance_unprotected", rules)
        self.assertIn("state_debt_sol", rules)

    def test_unresearched_state_agrees_and_says_so(self):
        lines = [Line(idx=1, code="J1885", units=1, charge=180.0, date="d")]
        ctx = Context(insured=False, state="WY")
        got = self.assertParity(lines, ctx, label="unresearched state")
        self.assertIn("state_not_researched", {g["rule"] for g in got})

    def test_self_funded_plan_downgrades_state_protection(self):
        lines = [Line(idx=1, code="J1885", units=1, charge=180.0, date="d")]
        fully = Context(insured=True, state="WA", ground_ambulance=True,
                        plan_funding="fully_insured")
        selff = Context(insured=True, state="WA", ground_ambulance=True,
                        plan_funding="self_funded")
        a = self.assertParity(lines, fully, label="WA fully insured")
        b = self.assertParity(lines, selff, label="WA self-funded")
        sev = lambda g: next(x["severity"] for x in g if x["rule"] == "state_ambulance_protected")
        self.assertEqual(sev(a), "high")
        self.assertEqual(sev(b), "notice")

    def test_empty_bill_agrees(self):
        self.assertParity([], Context(insured=True), label="empty bill")


if __name__ == "__main__":
    unittest.main()
