"""Test suite. Run: python3 -m unittest discover -s tests -v"""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from itemize.context import Context                          # noqa: E402
from itemize.model import Line, Reference, classify          # noqa: E402
from itemize.parse import parse, parse_delimited, parse_text  # noqa: E402
from itemize import rules                                     # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


class FakeRef(Reference):
    """Reference with hand-set tables so rule tests don't depend on a build."""

    def __init__(self, hcpcs=None, asp=None, dmepos=None):
        self.dir = "<fake>"
        self.dmepos = dmepos if dmepos is not None else {
            "E0114": {"fee": 67.26, "cat": "IN"}}
        self.hcpcs = hcpcs if hcpcs is not None else {"J1885": "Injection, ketorolac"}
        self.asp = asp if asp is not None else {
            "J1885": {"limit": 0.286, "dose": "15 MG", "desc": "Ketorolac"}}
        self.manifest = {"built": "test", "sources": [
            {"dataset": "asp", "url": "http://example/asp.zip", "member": "asp.csv",
             "sha256": "deadbeef" * 8, "kept": 1},
            {"dataset": "hcpcs", "url": "http://example/h.zip", "member": "h.txt",
             "sha256": "cafe" * 16, "kept": 1},
            {"dataset": "dmepos", "url": "http://example/d.zip", "member": "d.csv",
             "sha256": "beef" * 16, "kept": 1}]}


class TestClassify(unittest.TestCase):
    def test_cpt_level_i_is_ama(self):
        self.assertEqual(classify("99283"), "ama")

    def test_cpt_category_ii_and_iii_are_ama(self):
        self.assertEqual(classify("0500F"), "ama")
        self.assertEqual(classify("0075T"), "ama")

    def test_dental_is_ada(self):
        self.assertEqual(classify("D1110"), "ada")

    def test_level_two_is_public(self):
        for c in ("J1885", "A4550", "E0114", "Q4101", "V2020", "T1015"):
            self.assertEqual(classify(c), "public", c)

    def test_d_prefix_never_public(self):
        # The ADA boundary is the one most likely to be broken by a lazy regex.
        self.assertNotEqual(classify("D0120"), "public")

    def test_chargemaster_code_is_unknown(self):
        self.assertEqual(classify("CHG40021"), "unknown")
        self.assertEqual(classify(""), "unknown")


class TestParse(unittest.TestCase):
    def test_parses_fixture_csv(self):
        with open(os.path.join(FIX, "sample_bill.csv")) as f:
            lines = parse(f.read())
        self.assertEqual(len(lines), 11)
        self.assertEqual(lines[1].code, "J1885")
        self.assertEqual(lines[1].units, 2)
        self.assertAlmostEqual(lines[1].charge, 180.00)

    def test_parses_comma_thousands(self):
        lines = parse_delimited(
            'Code,Description,Qty,Charges\n99283,ED VISIT,1,"1,842.00"\n')
        self.assertAlmostEqual(lines[0].charge, 1842.00)

    def test_header_alias_variants(self):
        lines = parse_delimited(
            "CPT/HCPCS,Service Description,Days/Units,Total Charges\n"
            "J1885,KETOROLAC,2,180.00\n")
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].code, "J1885")
        self.assertEqual(lines[0].units, 2)

    def test_skips_total_rows(self):
        lines = parse_delimited(
            "Code,Description,Qty,Charges\n"
            "J1885,KETOROLAC,2,180.00\n"
            ",TOTAL CHARGES,,180.00\n")
        self.assertEqual(len(lines), 1)

    def test_loose_text_fallback(self):
        text = ("03/14/2026  J1885  KETOROLAC TROMETHAMINE INJ   2    180.00\n"
                "03/14/2026  A4550  SURGICAL TRAY                1     68.00\n")
        lines = parse_text(text)
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0].code, "J1885")
        self.assertAlmostEqual(lines[1].charge, 68.00)

    def test_column_split_line_from_pdf_text_layer(self):
        # Two-space column gaps are what the PDF extractor emits.
        text = ("2026-03-14  99283  EMERGENCY DEPT VISIT LEVEL 3  1  1842.00\n"
                "2026-03-14  CHG40021  PHARMACY GENERAL CLASSIFICATION  1  412.75\n")
        lines = parse_text(text)
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0].code, "99283")
        self.assertEqual(lines[0].units, 1)
        self.assertAlmostEqual(lines[0].charge, 1842.00)
        # A chargemaster code the loose regex would miss entirely.
        self.assertEqual(lines[1].code, "CHG40021")
        self.assertAlmostEqual(lines[1].charge, 412.75)

    def test_column_split_does_not_mistake_units_for_code(self):
        lines = parse_text("2026-03-14  J1885  KETOROLAC INJ 15MG  2  180.00\n")
        self.assertEqual(lines[0].code, "J1885")
        self.assertEqual(lines[0].units, 2)
        self.assertAlmostEqual(lines[0].charge, 180.00)

    def test_column_header_row_is_skipped(self):
        self.assertEqual(parse_text("Date  Code  Description  Qty  Charges\n"), [])

    def test_line_with_no_code_is_kept_not_dropped(self):
        # Silence must never be read as "nothing wrong".
        lines = parse_delimited(
            "Code,Description,Qty,Charges\n,ROOM AND BOARD,1,2310.00\n")
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].code, "")


class TestStateLayer(unittest.TestCase):
    """The state layer's honesty rules are the point of it, so test those."""

    def setUp(self):
        import json
        import tempfile
        from itemize import states as states_mod
        self.states_mod = states_mod
        self.tmp = tempfile.mkdtemp()
        fresh = (datetime.date.today() - datetime.timedelta(days=10)).isoformat()
        old = (datetime.date.today() - datetime.timedelta(days=900)).isoformat()
        payload = {
            "_meta": {"stale_after_days": 365, "note": "test fixture"},
            "states": {
                "ZZ": {"name": "Freshland", "ambulance_balance_billing": True,
                       "charity_care": {}, "citations": ["http://example/z"],
                       "verified": fresh},
                "YY": {"name": "Staleland", "ambulance_balance_billing": True,
                       "charity_care": {}, "citations": ["http://example/y"],
                       "verified": old},
            },
        }
        with open(os.path.join(self.tmp, "states.json"), "w") as f:
            json.dump(payload, f)
        self.ref = FakeRef()

    def _run(self, code, **kw):
        ctx = Context(state=code, ground_ambulance=True, **kw)
        return self.states_mod.evaluate([Line(idx=1, charge=100.0)], self.ref, ctx, self.tmp)

    def test_unknown_state_says_not_researched_not_unprotected(self):
        f = self._run("WY")
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].rule, "state_not_researched")
        # The wording must not imply an absence of rights.
        self.assertIn("not** the same as", f[0].detail)

    def test_fresh_entry_carries_no_staleness_warning(self):
        f = self._run("ZZ")
        self.assertTrue(f)
        self.assertNotIn("last verified", f[0].citation)

    def test_stale_entry_announces_its_own_age(self):
        f = self._run("YY")
        self.assertTrue(f)
        self.assertIn("last verified", f[0].citation)
        self.assertIn("re-check it", f[0].citation)

    def test_self_funded_plan_downgrades_state_protection(self):
        full = self._run("ZZ", plan_funding="fully_insured")
        self_f = self._run("ZZ", plan_funding="self_funded")
        pick = lambda fs: next(x for x in fs if x.rule == "state_ambulance_protected")
        self.assertEqual(pick(full).severity, "high")
        self.assertEqual(pick(self_f).severity, "notice")
        self.assertIn("self-funded", pick(self_f).detail)

    def test_no_state_given_produces_nothing(self):
        ctx = Context()
        self.assertEqual(
            self.states_mod.evaluate([Line(idx=1, charge=100.0)], self.ref, ctx, self.tmp), [])


class TestShippedStateData(unittest.TestCase):
    """Guard the data file's own invariants."""

    def setUp(self):
        import json
        path = os.path.join(os.path.dirname(FIX), "..", "web", "data", "states.json")
        if not os.path.exists(path):
            self.skipTest("states.json not present")
        with open(path) as f:
            self.data = json.load(f)

    def test_every_entry_has_citations_and_a_verified_date(self):
        for code, e in self.data["states"].items():
            self.assertTrue(e.get("citations"), f"{code} has no citations")
            self.assertTrue(e.get("verified"), f"{code} has no verified date")
            datetime.date.fromisoformat(e["verified"])

    def test_no_entry_is_currently_stale(self):
        limit = self.data["_meta"]["stale_after_days"]
        for code, e in self.data["states"].items():
            age = (datetime.date.today() - datetime.date.fromisoformat(e["verified"])).days
            self.assertLessEqual(age, limit, f"{code} entry is {age} days old; re-verify it")


class TestRules(unittest.TestCase):
    def setUp(self):
        self.ref = FakeRef()

    def test_exact_duplicate_is_recoverable(self):
        ls = [Line(idx=1, code="J1885", units=2, charge=180.0, date="2026-03-14"),
              Line(idx=2, code="J1885", units=2, charge=180.0, date="2026-03-14")]
        f = rules.rule_exact_duplicates(ls, self.ref)
        self.assertEqual(len(f), 1)
        self.assertTrue(f[0].recoverable)
        self.assertAlmostEqual(f[0].amount, 180.0)

    def test_single_line_is_not_a_duplicate(self):
        ls = [Line(idx=1, code="J1885", units=2, charge=180.0, date="2026-03-14")]
        self.assertEqual(rules.rule_exact_duplicates(ls, self.ref), [])

    def test_asp_benchmark_collapses_identical_lines(self):
        ls = [Line(idx=1, code="J1885", units=2, charge=180.0, date="d"),
              Line(idx=2, code="J1885", units=2, charge=180.0, date="d")]
        f = rules.rule_asp_benchmark(ls, self.ref)
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].lines, [1, 2])
        self.assertAlmostEqual(f[0].amount, 360.0)
        self.assertFalse(f[0].recoverable)   # markup is not recoverable

    def test_asp_below_threshold_not_flagged(self):
        # 2 units allowed = 0.572; charge 1.50 is ~2.6x, under the 3x floor.
        ls = [Line(idx=1, code="J1885", units=2, charge=1.50)]
        self.assertEqual(rules.rule_asp_benchmark(ls, self.ref), [])

    def test_asp_severity_escalates(self):
        low = rules.rule_asp_benchmark([Line(idx=1, code="J1885", units=1, charge=1.0)], self.ref)
        high = rules.rule_asp_benchmark([Line(idx=1, code="J1885", units=1, charge=100.0)], self.ref)
        self.assertEqual(low[0].severity, "warn")
        self.assertEqual(high[0].severity, "high")

    def test_unknown_code_flagged(self):
        ls = [Line(idx=1, code="CHG40021", charge=412.75)]
        f = rules.rule_unknown_code(ls, self.ref)
        self.assertEqual(len(f), 1)
        self.assertIn("CHG40021", f[0].title)

    def test_cpt_lines_reported_as_unbenchmarkable(self):
        ls = [Line(idx=1, code="99283", charge=1842.0)]
        f = rules.rule_licensed_codes(ls, self.ref)
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].severity, "info")

    def test_findings_sorted_worst_first(self):
        ls = [Line(idx=1, code="99283", charge=1842.0),
              Line(idx=2, code="J1885", units=2, charge=180.0, date="d"),
              Line(idx=3, code="J1885", units=2, charge=180.0, date="d")]
        f = rules.audit(ls, self.ref)
        self.assertEqual(f[0].severity, "high")
        self.assertEqual(f[-1].severity, "info")

    def test_no_reference_data_still_runs_structural_rules(self):
        empty = FakeRef(hcpcs={}, asp={}, dmepos={})
        ls = [Line(idx=1, code="J1885", units=2, charge=180.0, date="d"),
              Line(idx=2, code="J1885", units=2, charge=180.0, date="d")]
        f = rules.audit(ls, empty)
        self.assertTrue(any(x.rule == "exact_duplicate" for x in f))


if __name__ == "__main__":
    unittest.main()
