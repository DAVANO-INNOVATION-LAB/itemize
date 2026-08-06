"""Parity between itemize/rules.py and web/rules.js.

Two rule engines, one asserted to mirror the other, with nothing enforcing it,
is how a CLI and a web app end up telling the same person different things
about their bill. This runs both over identical input and diffs the findings.

Text is deliberately NOT compared -- the two differ in punctuation (-- vs em
dash). What must agree is the structure: which rules fired, at what severity,
over which lines, for how much, and whether the amount is recoverable.

Skips cleanly when Node is unavailable so the suite still runs.
"""
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
        py = [{"idx": l.idx, "code": l.code, "units": l.units,
               "charge": round(l.charge, 2), "date": l.date} for l in parse(text)]
        p = subprocess.run([NODE, DRIVER], input=json.dumps({"mode": "parse", "text": text}),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr[:1000])
        self.assertEqual(py, json.loads(p.stdout), "parsers disagree on the fixture CSV")

    def test_parsers_agree_on_pdf_style_columns(self):
        text = ("2026-03-14  99283  EMERGENCY DEPT VISIT LEVEL 3  1  1842.00\n"
                "2026-03-14  CHG40021  PHARMACY GENERAL CLASSIFICATION  1  412.75\n"
                "2026-03-14  J1885  KETOROLAC INJ 15MG  2  180.00\n")
        py = [{"idx": l.idx, "code": l.code, "units": l.units,
               "charge": round(l.charge, 2), "date": l.date} for l in parse(text)]
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
