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

    def __init__(self, hcpcs=None, asp=None, dmepos=None, nadac=None, drg=None):
        self.dir = "<fake>"
        self.dmepos = dmepos if dmepos is not None else {
            "E0114": {"fee": 67.26, "cat": "IN"}}
        self.hcpcs = hcpcs if hcpcs is not None else {"J1885": "Injection, ketorolac"}
        self.asp = asp if asp is not None else {
            "J1885": {"limit": 0.286, "dose": "15 MG", "desc": "Ketorolac"}}
        self.nadac = nadac if nadac is not None else {
            "00093721410": {"p": 0.04231, "u": "EA", "d": "LISINOPRIL 10 MG TABLET", "b": 0}}
        self.drg = drg if drg is not None else {
            "470": {"desc": "MAJOR HIP AND KNEE JOINT REPLACEMENT WITHOUT MCC",
                    "charge": 60000.0, "pay": 13000.0, "mdcr": 12000.0, "n": 400000}}
        self.manifest = {"built": "test", "sources": [
            {"dataset": "asp", "url": "http://example/asp.zip", "member": "asp.csv",
             "sha256": "deadbeef" * 8, "kept": 1},
            {"dataset": "hcpcs", "url": "http://example/h.zip", "member": "h.txt",
             "sha256": "cafe" * 16, "kept": 1},
            {"dataset": "dmepos", "url": "http://example/d.zip", "member": "d.csv",
             "sha256": "beef" * 16, "kept": 1},
            {"dataset": "nadac", "url": "http://example/nadac.csv", "member": "nadac.csv",
             "sha256": "1234" * 16, "kept": 1},
            {"dataset": "drg", "url": "http://example/drg.csv", "member": "drg.csv",
             "sha256": "5678" * 16, "kept": 1}]}


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


class TestNdcNormalisation(unittest.TestCase):
    def test_hyphenated_forms_pad_to_eleven_digits(self):
        from itemize.parse import normalize_ndc
        self.assertEqual(normalize_ndc("00409-3799-01"), "00409379901")
        self.assertEqual(normalize_ndc("0409-3799-01"), "00409379901")

    def test_bare_eleven_digits_pass_through(self):
        from itemize.parse import normalize_ndc
        self.assertEqual(normalize_ndc("00409379901"), "00409379901")

    def test_three_digit_first_segment_is_not_an_ndc(self):
        """Labeler codes are 4 or 5 digits; anything shorter is some other number."""
        from itemize.parse import normalize_ndc
        self.assertEqual(normalize_ndc("409-3799-1"), "")

    def test_bare_ten_digits_are_declined_not_guessed(self):
        """4-4-2, 5-3-2 and 5-4-1 all produce ten digits.

        Padding the wrong segment points at a different drug at a different
        price, and the reader would have no way to see it happened.
        """
        from itemize.parse import normalize_ndc
        self.assertEqual(normalize_ndc("0409379901"), "")

    def test_no_ndc_returns_empty(self):
        from itemize.parse import normalize_ndc
        self.assertEqual(normalize_ndc(""), "")
        self.assertEqual(normalize_ndc("SURGICAL TRAY"), "")
        self.assertEqual(normalize_ndc(None), "")


class TestNadacRule(unittest.TestCase):
    NDC = "00093721410"          # lisinopril 10 mg, $0.04231 each in FakeRef

    def _line(self, charge, units=30, code="PHRM01"):
        return Line(idx=1, code=code, desc="LISINOPRIL", units=units,
                    charge=charge, ndc=self.NDC)

    def test_fires_on_large_multiple_of_acquisition_cost(self):
        f = rules.rule_nadac_benchmark([self._line(412.50)], FakeRef())
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].rule, "nadac_benchmark")
        self.assertEqual(f[0].severity, "high")

    def test_ordinary_pharmacy_margin_does_not_fire(self):
        """NADAC is acquisition cost; a dispensing fee and margin sit on top."""
        acquisition = 0.04231 * 30
        f = rules.rule_nadac_benchmark([self._line(acquisition * 4)], FakeRef())
        self.assertEqual(f, [])

    def test_is_never_recoverable(self):
        f = rules.rule_nadac_benchmark([self._line(412.50)], FakeRef())
        self.assertFalse(f[0].recoverable)

    def test_stands_down_when_asp_already_prices_the_line(self):
        """Both firing would count the same dollars twice in the implicated total."""
        ln = self._line(412.50, code="J1885")     # J1885 has an ASP limit in FakeRef
        self.assertEqual(rules.rule_nadac_benchmark([ln], FakeRef()), [])
        self.assertTrue(rules.rule_asp_benchmark([ln], FakeRef()))

    def test_no_ndc_means_no_finding(self):
        ln = Line(idx=1, code="PHRM01", units=30, charge=412.50)
        self.assertEqual(rules.rule_nadac_benchmark([ln], FakeRef()), [])

    def test_unknown_ndc_is_silent_rather_than_guessing(self):
        ln = Line(idx=1, code="PHRM01", units=1, charge=900.0, ndc="99999999999")
        self.assertEqual(rules.rule_nadac_benchmark([ln], FakeRef()), [])

    def test_detail_names_the_pricing_unit_caveat(self):
        f = rules.rule_nadac_benchmark([self._line(412.50)], FakeRef())
        self.assertIn("not an allowed amount", f[0].detail)
        self.assertIn("Check the units", f[0].detail)


class TestDrgRule(unittest.TestCase):
    def _lines(self, total):
        return [Line(idx=1, code="", desc="ROOM AND BOARD", units=1, charge=total)]

    def test_no_drg_in_context_means_no_finding(self):
        self.assertEqual(rules.rule_drg_benchmark(self._lines(50000), FakeRef(),
                                                  Context()), [])

    def test_bill_far_above_average_is_raised_as_a_question(self):
        f = rules.rule_drg_benchmark(self._lines(200000), FakeRef(), Context(drg="470"))
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].rule, "drg_benchmark")
        self.assertEqual(f[0].severity, "notice")

    def test_bill_near_average_is_reported_as_context_not_alarm(self):
        f = rules.rule_drg_benchmark(self._lines(60000), FakeRef(), Context(drg="470"))
        self.assertEqual(f[0].severity, "info")

    def test_never_contributes_a_recoverable_amount(self):
        """A whole-bill comparison summed with line disputes once produced a
        'disputable' figure larger than the bill itself. Amount stays zero."""
        for total in (60000, 200000):
            f = rules.rule_drg_benchmark(self._lines(total), FakeRef(), Context(drg="470"))
            self.assertEqual(f[0].amount, 0)
            self.assertFalse(f[0].recoverable)
            self.assertEqual(f[0].lines, [])

    def test_unpadded_and_noisy_drg_input_still_matches(self):
        for given in ("470", "0470", "DRG 470", " 470 "):
            f = rules.rule_drg_benchmark(self._lines(60000), FakeRef(), Context(drg=given))
            self.assertEqual(f[0].rule, "drg_benchmark", given)

    def test_unknown_drg_says_so_rather_than_staying_silent(self):
        f = rules.rule_drg_benchmark(self._lines(60000), FakeRef(), Context(drg="999"))
        self.assertEqual(f[0].rule, "drg_unknown")
        self.assertIn("form locator 71", f[0].detail)

    def test_explains_that_submitted_charges_are_not_what_is_paid(self):
        f = rules.rule_drg_benchmark(self._lines(200000), FakeRef(), Context(drg="470"))
        self.assertIn("list prices that almost nobody pays", f[0].detail)


class TestShippedNewData(unittest.TestCase):
    """The shipped files must actually be present and shaped as the rules expect."""

    @classmethod
    def setUpClass(cls):
        cls.ref = Reference(os.path.join(os.path.dirname(FIX), "..", "web", "data"))
        if not cls.ref.nadac or not cls.ref.drg:
            raise unittest.SkipTest("nadac/drg not built; run tools/build_data.py")

    def test_nadac_records_carry_a_price_and_pricing_unit(self):
        for ndc, rec in list(self.ref.nadac.items())[:500]:
            self.assertRegex(ndc, r"^\d{11}$")
            self.assertGreater(rec["p"], 0)
            self.assertIn(rec["u"], ("EA", "ML", "GM"))

    def test_drg_keys_are_three_digit_and_carry_a_charge(self):
        for drg, rec in self.ref.drg.items():
            self.assertRegex(drg, r"^\d{3}$")
            self.assertGreater(rec["charge"], 0)
            self.assertTrue(rec["desc"])

    def test_no_cpt_shaped_code_leaked_into_the_new_tables(self):
        """DRG codes are 3-digit and NDCs 11-digit; neither namespace can collide
        with a 5-digit CPT code, but assert it rather than assume it."""
        for drg in self.ref.drg:
            self.assertNotEqual(classify(drg), "ama")
        for ndc in list(self.ref.nadac)[:2000]:
            self.assertNotEqual(classify(ndc), "ama")


class TestMrf(unittest.TestCase):
    """Hospital price transparency files: streaming readers and the comparison."""

    JSON = os.path.join(FIX, "mrf_sample.json")
    CSV = os.path.join(FIX, "mrf_sample.csv")

    def setUp(self):
        from itemize import mrf
        self.mrf = mrf

    # ---- readers
    def test_json_reader_finds_every_record(self):
        idx, scanned = self.mrf.index_for(
            self.JSON, ["J1885", "A4550", "99283", "E0114", "Q9999"])
        self.assertEqual(scanned, 5)
        self.assertEqual(set(idx), {"J1885", "A4550", "99283", "E0114", "Q9999"})

    def test_json_reader_is_not_confused_by_a_brace_inside_a_string(self):
        """A naive depth counter ends the record early on a '}' in a description."""
        idx, _ = self.mrf.index_for(self.JSON, ["Q9999"])
        self.assertIn("BRACE IN THE TEXT", idx["Q9999"]["desc"])

    def test_csv_reader_finds_the_header_under_the_preamble(self):
        idx, scanned = self.mrf.index_for(self.CSV, ["J1885", "E0114"])
        self.assertEqual(scanned, 4)
        self.assertAlmostEqual(idx["J1885"]["cash"], 24.50)

    def test_json_and_csv_of_the_same_data_agree(self):
        codes = ["J1885", "A4550", "99283", "E0114"]
        a, _ = self.mrf.index_for(self.JSON, codes)
        b, _ = self.mrf.index_for(self.CSV, codes)
        for c in codes:
            self.assertEqual((a[c]["gross"], a[c]["cash"]),
                             (b[c]["gross"], b[c]["cash"]), c)

    def test_only_requested_codes_are_retained(self):
        idx, _ = self.mrf.index_for(self.JSON, ["J1885"])
        self.assertEqual(list(idx), ["J1885"])

    def test_missing_cash_price_is_none_not_zero(self):
        idx, _ = self.mrf.index_for(self.JSON, ["E0114"])
        self.assertIsNone(idx["E0114"]["cash"])
        self.assertAlmostEqual(idx["E0114"]["gross"], 88.00)

    # ---- comparison
    def _idx(self, codes=("J1885", "A4550", "99283", "E0114")):
        return self.mrf.index_for(self.JSON, list(codes))[0]

    def test_charge_above_published_cash_price_is_reported(self):
        lines = [Line(idx=1, code="J1885", units=2, charge=180.0)]
        f = self.mrf.compare(lines, self._idx(), "src", Context(insured=False))
        self.assertEqual([x.rule for x in f], ["mrf_cash_price"])

    def test_charge_at_or_below_the_cash_price_is_silent(self):
        lines = [Line(idx=1, code="J1885", units=2, charge=49.0)]
        self.assertEqual(self.mrf.compare(lines, self._idx(), "src"), [])

    def test_cash_price_finding_is_never_recoverable(self):
        """It is leverage, not proof of an error -- and a duplicated line already
        reports its own recoverable amount. Counting both claims the same dollars
        twice, which is how a 'disputable' total grows larger than the bill."""
        lines = [Line(idx=1, code="J1885", units=2, charge=180.0)]
        for ctx in (Context(insured=False), Context(insured=True), None):
            f = self.mrf.compare(lines, self._idx(), "src", ctx)
            self.assertFalse(f[0].recoverable)

    def test_self_pay_raises_severity_but_not_recoverability(self):
        lines = [Line(idx=1, code="J1885", units=2, charge=180.0)]
        self.assertEqual(
            self.mrf.compare(lines, self._idx(), "s", Context(insured=False))[0].severity,
            "high")
        self.assertEqual(
            self.mrf.compare(lines, self._idx(), "s", Context(insured=True))[0].severity,
            "warn")

    def test_identical_lines_collapse_into_one_finding(self):
        lines = [Line(idx=1, code="J1885", units=2, charge=180.0),
                 Line(idx=2, code="J1885", units=2, charge=180.0)]
        f = self.mrf.compare(lines, self._idx(), "src", Context(insured=False))
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].lines, [1, 2])

    def test_billed_above_published_gross_is_recoverable(self):
        """Above the hospital's own chargemaster is a discrepancy, not a negotiation."""
        lines = [Line(idx=1, code="E0114", units=1, charge=124.0)]   # gross 88.00
        f = self.mrf.compare(lines, self._idx(), "src")
        self.assertEqual([x.rule for x in f], ["mrf_above_gross"])
        self.assertTrue(f[0].recoverable)
        self.assertAlmostEqual(f[0].amount, 36.0)

    def test_code_absent_from_the_file_produces_nothing(self):
        lines = [Line(idx=1, code="ZZZZZ", units=1, charge=9999.0)]
        self.assertEqual(self.mrf.compare(lines, self._idx(), "src"), [])

    def test_citation_names_the_file_and_the_rule(self):
        lines = [Line(idx=1, code="J1885", units=2, charge=180.0)]
        f = self.mrf.compare(lines, self._idx(), "myfile.json", Context(insured=False))
        self.assertIn("myfile.json", f[0].citation)
        self.assertIn("45 CFR 180.50", f[0].citation)


class TestTeachingCases(unittest.TestCase):
    """The practice bills double as a coverage check.

    Each case records what is deliberately wrong with it. If a rule quietly
    stops firing, the case it was written for stops being solvable and this
    fails -- which is a more legible signal than a rule-level unit test, because
    it names the thing a reader would no longer be told.
    """

    @classmethod
    def setUpClass(cls):
        from itemize import teaching
        cls.teaching = teaching
        cls.ref = Reference(os.path.join(os.path.dirname(FIX), "..", "web", "data"))
        if not cls.ref.available:
            raise unittest.SkipTest("reference data missing; run tools/build_data.py")

    def _findings(self, case):
        from itemize.eob import parse_eob
        lines = parse(case["bill"])
        eob = parse_eob(case["eob"]) if case.get("eob") else None
        return lines, rules.audit(lines, self.ref, self.teaching.context_for(case), eob)

    def test_every_case_parses_to_the_expected_line_count(self):
        for case in self.teaching.CASES:
            lines, _ = self._findings(case)
            expected = len([l for l in case["bill"].strip().split("\n")[1:] if l.strip()])
            self.assertEqual(len(lines), expected, case["id"])

    def test_every_seeded_error_is_caught(self):
        for case in self.teaching.CASES:
            _, findings = self._findings(case)
            _hits, misses, _extra = self.teaching.score(case, findings)
            self.assertEqual(misses, [], f"{case['id']} no longer detects: "
                                         f"{[m[0] for m in misses]}")

    def test_seeded_line_numbers_exist_on_the_bill(self):
        """A key that points at line 7 of a six-line bill teaches the wrong thing."""
        for case in self.teaching.CASES:
            lines, _ = self._findings(case)
            valid = {l.idx for l in lines}
            for rule, seeded_lines, _why in case["seeded"]:
                for idx in seeded_lines:
                    self.assertIn(idx, valid, f"{case['id']}/{rule} cites line {idx}")

    def test_case_ids_are_unique_and_url_safe(self):
        ids = [c["id"] for c in self.teaching.CASES]
        self.assertEqual(len(ids), len(set(ids)))
        for i in ids:
            self.assertRegex(i, r"^[a-z0-9-]+$")

    def test_every_case_carries_a_why_for_each_seeded_error(self):
        for case in self.teaching.CASES:
            self.assertTrue(case["seeded"], case["id"])
            for rule, _lines, why in case["seeded"]:
                self.assertTrue(rule and why.strip(), case["id"])


class TestPackaging(unittest.TestCase):
    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def test_version_matches_pyproject(self):
        """Two version strings that can drift will drift, and a release tagged
        from one of them then lies about the other."""
        import re
        import itemize
        with open(os.path.join(self.ROOT, "pyproject.toml")) as f:
            text = f.read()
        m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
        self.assertIsNotNone(m, "pyproject.toml has no version")
        self.assertEqual(itemize.__version__, m.group(1))

    def test_declares_no_dependencies(self):
        """The zero-dependency promise is load-bearing: this tool reads people's
        medical bills, and every dependency is one more thing a deployer must
        audit and one more way the 'nothing leaves your machine' claim breaks."""
        import re
        with open(os.path.join(self.ROOT, "pyproject.toml")) as f:
            text = f.read()
        m = re.search(r"^dependencies\s*=\s*\[(.*?)\]", text, re.M | re.S)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1).strip(), "")

    def test_only_stdlib_is_imported(self):
        import re
        import sys
        stdlib = getattr(sys, "stdlib_module_names", None)
        if not stdlib:
            self.skipTest("stdlib_module_names needs Python 3.10+")
        pat = re.compile(r"^\s*(?:import|from)\s+([a-zA-Z_][\w.]*)", re.M)
        for sub in ("itemize", "tools"):
            base = os.path.join(self.ROOT, sub)
            for root, _dirs, files in os.walk(base):
                for name in files:
                    if not name.endswith(".py"):
                        continue
                    with open(os.path.join(root, name)) as f:
                        for mod in pat.findall(f.read()):
                            top = mod.split(".")[0]
                            if top in ("itemize", "__future__") or top in stdlib:
                                continue
                            self.fail(f"{sub}/{name} imports non-stdlib {top!r}")

    def test_builder_is_importable_from_the_package(self):
        """An installed copy must refresh its own data; if the builder only
        exists under tools/ then `itemize data --refresh` cannot work."""
        from itemize import build_data
        self.assertTrue(callable(build_data.main))

    def test_service_worker_caches_every_shipped_dataset(self):
        """The page promises it works offline. A dataset missing from the shell
        makes that promise false for the findings built on it -- which is how
        nadac and drg were lost offline the day they were added."""
        with open(os.path.join(self.ROOT, "web", "sw.js")) as f:
            shell = f.read()
        for name in ("hcpcs", "asp", "dmepos", "nadac", "drg", "states", "manifest"):
            self.assertIn(f"data/{name}.json", shell, name)


class TestDataDirResolution(unittest.TestCase):
    def test_env_var_wins(self):
        from itemize import model
        old = os.environ.get("ITEMIZE_DATA")
        os.environ["ITEMIZE_DATA"] = "/tmp/explicit-itemize-data"
        try:
            self.assertEqual(model.default_data_dir(), "/tmp/explicit-itemize-data")
        finally:
            if old is None:
                del os.environ["ITEMIZE_DATA"]
            else:
                os.environ["ITEMIZE_DATA"] = old

    def test_checkout_is_preferred_over_the_user_dir(self):
        from itemize import model
        old = os.environ.pop("ITEMIZE_DATA", None)
        try:
            self.assertTrue(model.default_data_dir().endswith(os.path.join("web", "data")))
        finally:
            if old is not None:
                os.environ["ITEMIZE_DATA"] = old

    def test_user_data_dir_is_absolute_and_named(self):
        from itemize.model import user_data_dir
        d = user_data_dir()
        self.assertTrue(os.path.isabs(d))
        self.assertTrue(d.endswith("itemize"))


class TestStaleness(unittest.TestCase):
    def _ref(self, stamp, datasets=("hcpcs", "nadac")):
        r = FakeRef()
        r.manifest = {"built": stamp, "sources": [
            {"dataset": d, "retrieved": stamp, "member": f"{d}.csv",
             "url": "http://example/x", "sha256": "ab" * 32, "kept": 1}
            for d in datasets]}
        return r

    def test_fresh_data_produces_no_finding(self):
        ref = self._ref(datetime.date.today().isoformat())
        self.assertEqual(rules.rule_stale_reference_data([], ref), [])

    def test_old_data_is_reported(self):
        f = rules.rule_stale_reference_data([], self._ref("2020-01-01"))
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].rule, "stale_reference_data")
        self.assertEqual(f[0].severity, "warn")

    def test_each_dataset_has_its_own_window(self):
        """NADAC is weekly and DRG annual; one threshold for both would either
        nag about the DRG file or stay quiet on a year-old NADAC."""
        from itemize.model import REFRESH_DAYS
        self.assertLess(REFRESH_DAYS["nadac"], REFRESH_DAYS["hcpcs"])
        self.assertLess(REFRESH_DAYS["hcpcs"], REFRESH_DAYS["drg"])

    def test_a_source_keeps_its_own_date_not_the_build_date(self):
        """A dataset carried forward by --skip was fetched earlier. Dating it by
        the manifest build would make stale files look fresh."""
        ref = FakeRef()
        ref.manifest = {"built": datetime.date.today().isoformat(), "sources": [
            {"dataset": "nadac", "retrieved": "2020-01-01", "member": "n.csv",
             "url": "http://example/n", "sha256": "cd" * 32, "kept": 1,
             "skipped_at": datetime.date.today().isoformat()}]}
        self.assertTrue(ref.stale_datasets())
        self.assertIn("2020-01-01", ref.cite("nadac"))

    def test_finding_carries_no_amount(self):
        f = rules.rule_stale_reference_data([], self._ref("2020-01-01"))
        self.assertEqual(f[0].amount, 0)
        self.assertFalse(f[0].recoverable)

    def test_undated_source_is_ignored_rather_than_assumed_fresh(self):
        ref = FakeRef()
        ref.manifest = {"sources": [{"dataset": "nadac", "member": "n.csv"}]}
        self.assertEqual(ref.dataset_ages(), [])


class TestJsonOutput(unittest.TestCase):
    def setUp(self):
        from itemize import evidence
        self.evidence = evidence
        with open(os.path.join(FIX, "sample_bill.csv")) as f:
            self.lines = parse(f.read())
        self.ref = FakeRef()
        self.ctx = Context(insured=False, nonprofit_hospital=True)
        self.findings = rules.audit(self.lines, self.ref, self.ctx)

    def _payload(self):
        import json
        return json.loads(self.evidence.to_json(
            self.lines, self.findings, self.ref, self.ctx))

    def test_is_valid_json_with_the_expected_shape(self):
        d = self._payload()
        for key in ("tool", "version", "summary", "findings", "lines",
                    "next_actions", "reference_data"):
            self.assertIn(key, d)
        self.assertEqual(d["tool"], "itemize")

    def test_summary_never_exceeds_the_bill(self):
        d = self._payload()
        self.assertLessEqual(d["summary"]["disputable_line_items"],
                             d["summary"]["total_charges"])

    def test_findings_carry_their_citation(self):
        for f in self._payload()["findings"]:
            self.assertTrue(f["citation"], f["rule"])

    def test_carries_the_disclaimer(self):
        self.assertIn("not advice", self._payload()["disclaimer"])

    def test_agrees_with_the_markdown_packet_on_the_count(self):
        d = self._payload()
        self.assertEqual(d["summary"]["findings"], len(self.findings))
        self.assertEqual(d["summary"]["lines"], len(self.lines))


class TestMrfSettings(unittest.TestCase):
    """A hospital publishes a different price per setting. Mixing them produces a
    sourced, confident, wrong number -- which is the failure this project exists
    to avoid, so it gets its own tests."""

    REC = {
        "description": "KNEE ARTHROSCOPY",
        "code_information": [{"code": "29881", "type": "CPT"}],
        "standard_charges": [
            {"setting": "outpatient", "billing_class": "facility",
             "gross_charge": 8000.00, "discounted_cash": 4000.00,
             "minimum": 1500, "maximum": 6000},
            {"setting": "inpatient", "billing_class": "facility",
             "gross_charge": 22000.00, "discounted_cash": 15000.00,
             "minimum": 9000, "maximum": 19000},
        ],
    }

    def setUp(self):
        from itemize import mrf
        self.mrf = mrf

    def test_each_setting_becomes_its_own_row(self):
        rows = self.mrf._from_json_record(self.REC)
        self.assertEqual(len(rows), 2)
        by = {r["setting"]: r for r in rows}
        self.assertAlmostEqual(by["outpatient"]["cash"], 4000.00)
        self.assertAlmostEqual(by["inpatient"]["cash"], 15000.00)

    def test_a_settings_price_is_never_attached_to_another_settings_label(self):
        """The regression: the row said `outpatient` and carried the inpatient
        cash price, so a day-surgery patient was shown $15,000 as their
        hospital's published price for the procedure they had."""
        for row in self.mrf._from_json_record(self.REC):
            if row["setting"] == "outpatient":
                self.assertAlmostEqual(row["gross"], 8000.00)
                self.assertAlmostEqual(row["cash"], 4000.00)
            else:
                self.assertAlmostEqual(row["gross"], 22000.00)
                self.assertAlmostEqual(row["cash"], 15000.00)

    def test_payer_rates_widen_the_min_max_band(self):
        rec = {
            "description": "X", "code_information": [{"code": "AAA", "type": "HCPCS"}],
            "standard_charges": [{
                "setting": "outpatient", "gross_charge": 100.0,
                "payers_information": [
                    {"payer_name": "A", "standard_charge_dollar": 20.0},
                    {"payer_name": "B", "standard_charge_dollar": 80.0}],
            }],
        }
        row = self.mrf._from_json_record(rec)[0]
        self.assertAlmostEqual(row["min"], 20.0)
        self.assertAlmostEqual(row["max"], 80.0)
        self.assertEqual(row["payers"], 2)

    def test_index_records_which_settings_it_saw(self):
        import json
        import tempfile
        path = os.path.join(tempfile.mkdtemp(), "mrf.json")
        with open(path, "w") as f:
            json.dump({"standard_charge_information": [self.REC]}, f)
        idx, _ = self.mrf.index_for(path, ["29881"])
        self.assertEqual(idx["29881"]["settings_seen"], {"outpatient", "inpatient"})
        # Cheapest cash wins when no setting is requested.
        self.assertAlmostEqual(idx["29881"]["cash"], 4000.00)

    def test_setting_filter_restricts_the_lookup(self):
        import json
        import tempfile
        path = os.path.join(tempfile.mkdtemp(), "mrf.json")
        with open(path, "w") as f:
            json.dump({"standard_charge_information": [self.REC]}, f)
        idx, _ = self.mrf.index_for(path, ["29881"], setting="inpatient")
        self.assertAlmostEqual(idx["29881"]["cash"], 15000.00)
        self.assertEqual(idx["29881"]["setting"], "inpatient")

    def test_finding_names_the_setting_and_warns_when_others_exist(self):
        import json
        import tempfile
        path = os.path.join(tempfile.mkdtemp(), "mrf.json")
        with open(path, "w") as f:
            json.dump({"standard_charge_information": [self.REC]}, f)
        idx, _ = self.mrf.index_for(path, ["29881"])
        f = self.mrf.compare([Line(idx=1, code="29881", units=1, charge=30000.0)],
                             idx, "src", Context(insured=False))
        self.assertEqual(len(f), 1)
        self.assertIn("outpatient", f[0].detail)
        self.assertIn("inpatient", f[0].detail)
        self.assertIn("setting you were actually treated in", f[0].detail)


class TestAdversarialParsing(unittest.TestCase):
    """Regressions from a differential fuzz of the two engines.

    Each of these was a real defect found by generating adversarial bills and
    diffing the Python parser against the JavaScript one.
    """

    # ---- signed amounts
    def test_credits_keep_their_sign(self):
        """A statement's payments and adjustments are negative. Reading them as
        positive inflated the total by twice the credit."""
        lines = parse("Date,Code,Description,Qty,Charges\n"
                      "2026-03-14,J1885,KETOROLAC,1,180.00\n"
                      "2026-03-14,,PATIENT PAYMENT,1,-500.00\n"
                      "2026-03-14,,INSURANCE ADJUSTMENT,1,\"(1,200.00)\"\n")
        self.assertEqual([l.charge for l in lines], [180.0, -500.0, -1200.0])
        self.assertAlmostEqual(sum(l.charge for l in lines), -1520.0)

    def test_accounting_parentheses_are_negative(self):
        from itemize.parse import _money
        self.assertEqual(_money("(500.00)"), -500.0)
        self.assertEqual(_money("-1,200.00"), -1200.0)
        self.assertEqual(_money("1,842.00"), 1842.0)

    def test_duplicated_credits_are_not_reported_as_a_disputable_charge(self):
        """The sharpest consequence of the sign bug: a payment recorded twice was
        reported as a duplicate CHARGE worth disputing, which would have sent a
        reader to a billing office demanding money back for a credit."""
        lines = parse("Date,Code,Description,Qty,Charges\n"
                      "2026-03-14,,PATIENT PAYMENT,1,-500.00\n"
                      "2026-03-14,,PATIENT PAYMENT,1,-500.00\n")
        found = rules.audit(lines, FakeRef(), Context(insured=False))
        self.assertFalse([f for f in found if f.recoverable and f.lines],
                         "a duplicated credit must never be recoverable")

    def test_eob_responsibility_keeps_its_sign(self):
        from itemize.eob import parse_eob
        rows = parse_eob("Code,Allowed,Patient Responsibility\nJ1885,42.00,-5.00\n")
        self.assertEqual(rows[0]["patient"], -5.0)

    def test_eob_absent_column_is_none_not_zero(self):
        """A missing column and a genuine $0.00 responsibility mean different
        things to the cross-check."""
        from itemize.eob import parse_eob
        rows = parse_eob("Code,Allowed,Patient Responsibility\nJ1885,42.00,6.00\n")
        self.assertIsNone(rows[0]["plan_paid"])

    # ---- quantities
    def test_infinite_quantity_is_refused(self):
        """float('Infinity') > 0, so it sailed through -- and an infinite unit
        count makes every price benchmark compute an infinite allowed amount,
        silently disabling all of them for that line."""
        from itemize.parse import _num
        for bad in ("Infinity", "-Infinity", "NaN", "inf"):
            self.assertEqual(_num(bad), 1.0, bad)

    def test_quantity_with_a_unit_label_is_read(self):
        from itemize.parse import _num
        self.assertEqual(_num("2 EA"), 2.0)
        self.assertEqual(_num("3 doses"), 3.0)

    def test_quantity_is_not_prefix_parsed_from_an_ndc(self):
        """JavaScript's parseFloat reads a prefix, so '00409-3799-01' became 409
        in the browser and 1 in the CLI -- different unit counts, and therefore
        different overcharge multiples, for the same line."""
        from itemize.parse import _num
        self.assertEqual(_num("00409-3799-01"), 1.0)

    def test_negative_quantity_falls_back_to_the_default(self):
        from itemize.parse import _num
        self.assertEqual(_num("-3"), 1.0)
        self.assertEqual(_num("-0.5"), 1.0)

    # ---- resource exhaustion
    def test_long_numeric_run_parses_in_reasonable_time(self):
        """A regex whose quantifiers could both match bare digits made a long
        numeric run -- an account number, a barcode off a PDF text layer -- take
        1.9s at 1,000 digits and hang at 20,000. It runs in the reader's browser."""
        import time
        t0 = time.time()
        parse("x " + "9" * 20000 + " 1.00")
        self.assertLess(time.time() - t0, 2.0, "parser is backtracking again")

    # ---- injection and control characters
    def test_description_is_collapsed_to_one_line(self):
        """A quoted CSV field may contain newlines, and the evidence packet is
        markdown handed to a billing office. A description carrying
        '\\n> **Source:** ...' forged a citation line into that packet."""
        lines = parse('Date,Code,Description,Qty,Charges\n'
                      '2026-03-14,,"A\n> **Source:** FORGED\n### Fake",1,100.00\n')
        self.assertNotIn("\n", lines[0].desc)
        self.assertNotIn("\x00", parse(
            'Date,Code,Description,Qty,Charges\n2026-03-14,,nul\x00byte,1,1.00\n')[0].desc)

    def test_forged_citation_cannot_reach_the_evidence_packet(self):
        from itemize.evidence import render
        lines = parse('Date,Code,Description,Qty,Charges\n'
                      '2026-03-14,,"A\n> **Source:** FORGED\n### 99. Fake",1,100.00\n'
                      '2026-03-14,,"A\n> **Source:** FORGED\n### 99. Fake",1,100.00\n')
        ref = FakeRef()
        out = render(lines, rules.audit(lines, ref), ref)
        self.assertEqual(0, sum(1 for l in out.split("\n")
                                if l.startswith("> **Source:** FORGED")))

    # ---- quoting
    def test_inch_marks_do_not_swallow_the_rest_of_the_row(self):
        """`5" CATHETER TUBING` is ordinary on a supply line. Treating a quote
        anywhere in a field as a delimiter made the browser read the whole row as
        one field with no charge, silently dropping it from the total."""
        lines = parse('Date,Code,Description,Qty,Charges\n'
                      '2026-03-14,A4550,5" CATHETER TUBING,2,120.00\n')
        self.assertEqual(lines[0].code, "A4550")
        self.assertEqual(lines[0].units, 2.0)
        self.assertAlmostEqual(lines[0].charge, 120.00)

    def test_multiline_quoted_field_is_one_record(self):
        lines = parse('Date,Code,Description,Qty,Charges\n'
                      '2026-03-14,A4550,"WOUND CARE\nKIT",1,50.00\n')
        self.assertEqual(len(lines), 1)
        self.assertAlmostEqual(lines[0].charge, 50.00)

    def test_leading_byte_order_mark_is_ignored(self):
        """Excel writes a BOM on every CSV it exports."""
        plain = "Date,Code,Description,Qty,Charges\n2026-03-14,J1885,KETOROLAC,2,180.00\n"
        self.assertEqual([parse_shape_lite(l) for l in parse(plain)],
                         [parse_shape_lite(l) for l in parse("﻿" + plain)])

    # ---- ragged rows
    def test_unquoted_thousands_separator_is_flagged(self):
        """`1,842.00` without quotes splits into two fields, everything shifts
        left and the charge reads as $1.00 -- silently wrong by 1800x."""
        lines = parse("Date,Code,Description,Qty,Charges\n"
                      "2026-03-14,J1885,KETOROLAC,2,180.00\n"
                      "2026-03-14,99283,ED VISIT,1,1,842.00\n")
        self.assertFalse(lines[0].suspect_columns)
        self.assertTrue(lines[1].suspect_columns)
        found = rules.rule_ragged_columns(lines, FakeRef())
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].severity, "warn")
        self.assertEqual(found[0].lines, [2])

    def test_well_formed_bill_raises_no_ragged_finding(self):
        with open(os.path.join(FIX, "sample_bill.csv")) as f:
            lines = parse(f.read())
        self.assertEqual(rules.rule_ragged_columns(lines, FakeRef()), [])


def parse_shape_lite(l):
    return (l.idx, l.code, l.desc, l.units, round(l.charge, 2), l.date)


class TestMrfMalformed(unittest.TestCase):
    """Roughly 40% of hospital files fail CMS's own validator, so a type
    violation must degrade rather than take the audit down."""

    def setUp(self):
        from itemize import mrf
        self.mrf = mrf

    def _write(self, obj):
        import json
        import tempfile
        p = os.path.join(tempfile.mkdtemp(), "mrf.json")
        with open(p, "w") as f:
            json.dump(obj, f)
        return p

    def test_non_string_description_does_not_crash(self):
        p = self._write({"standard_charge_information": [
            {"description": 123, "code_information": [{"code": "AAA"}],
             "standard_charges": [{"gross_charge": 10, "discounted_cash": 5}]}]})
        idx, scanned = self.mrf.index_for(p, ["AAA"])
        self.assertEqual(scanned, 1)
        self.assertAlmostEqual(idx["AAA"]["cash"], 5.0)

    def test_wrong_shaped_collections_are_skipped(self):
        p = self._write({"standard_charge_information": [
            {"description": "x", "code_information": "notalist",
             "standard_charges": "alsonotalist"},
            {"description": "y", "code_information": [{"code": "BBB"}],
             "standard_charges": [{"gross_charge": 7}]}]})
        idx, scanned = self.mrf.index_for(p, ["AAA", "BBB"])
        self.assertEqual(scanned, 2)
        self.assertIn("BBB", idx)

    def test_truncated_and_binary_files_are_survivable(self):
        import tempfile
        d = tempfile.mkdtemp()
        for name, content in (("t.json", '{"standard_charge_information":[{"desc'),
                              ("b.json", "\x00\x01\xff" * 50),
                              ("e.json", "")):
            p = os.path.join(d, name)
            with open(p, "w", errors="ignore") as f:
                f.write(content)
            idx, _ = self.mrf.index_for(p, ["AAA"])
            self.assertEqual(idx, {}, name)


class TestNulBytes(unittest.TestCase):
    """Python's csv module raised `_csv.Error: line contains NUL` up to 3.10.

    A bill carrying a NUL -- ordinary in text extracted from a PDF, and in some
    Windows exports -- therefore crashed the parser outright on the interpreter
    floor this project claims to support, while passing on 3.11+. Sanitising per
    field was too late: the csv reader chokes before any field is seen.
    """

    def test_nul_in_a_bill_does_not_raise(self):
        lines = parse("Date,Code,Description,Qty,Charges\n"
                      "2026-03-14,J1885,nul\x00byte,1,10.00\n")
        self.assertEqual(len(lines), 1)
        self.assertAlmostEqual(lines[0].charge, 10.00)

    def test_nul_in_an_eob_does_not_raise(self):
        from itemize.eob import parse_eob
        rows = parse_eob("Code,Allowed,Patient Responsibility\n"
                         "J1885,42.00\x00,5.00\n")
        self.assertEqual(len(rows), 1)

    def test_nul_becomes_a_space_not_nothing(self):
        """JavaScript has no NUL restriction, so dropping the byte here would
        leave the two engines reading 'nulbyte' and 'nul byte' for one input."""
        lines = parse("Date,Code,Description,Qty,Charges\n"
                      "2026-03-14,J1885,nul\x00byte,1,10.00\n")
        self.assertEqual(lines[0].desc, "nul byte")

    def test_nul_heavy_input_still_parses_the_charge(self):
        lines = parse("Date,Code,Description,Qty,Charges\n"
                      "\x002026-03-14\x00,\x00J1885\x00,\x00TRAY\x00,\x001\x00,\x0010.00\x00\n")
        self.assertEqual(len(lines), 1)
        self.assertAlmostEqual(lines[0].charge, 10.00)


class TestStateCoverage(unittest.TestCase):
    """Every jurisdiction now has an entry, which changed what silence means."""

    @classmethod
    def setUpClass(cls):
        import json
        with open(os.path.join(os.path.dirname(FIX), "..", "web", "data",
                               "states.json")) as f:
            cls.data = json.load(f)
        cls.states = cls.data["states"]

    def test_all_fifty_one_jurisdictions_present(self):
        self.assertEqual(len(self.states), 51)

    def test_no_ambulance_field_is_unknown(self):
        """The original file left ~6 protected states unnamed. The Commonwealth
        Fund map resolved every one."""
        unknown = [k for k, e in self.states.items()
                   if e.get("ambulance_balance_billing") is None]
        self.assertEqual(unknown, [])

    def test_protected_count_matches_the_source(self):
        n = sum(1 for e in self.states.values() if e["ambulance_balance_billing"])
        self.assertEqual(n, 22)

    def test_every_entry_is_cited_and_dated(self):
        for code, e in self.states.items():
            self.assertTrue(e.get("citations"), code)
            self.assertTrue(e.get("verified"), code)
            datetime.date.fromisoformat(e["verified"])

    def test_protected_states_explain_the_protection(self):
        for code, e in self.states.items():
            if e["ambulance_balance_billing"]:
                self.assertTrue((e.get("ambulance_note") or "").strip(), code)

    def test_unresearched_fields_are_null_never_false(self):
        """`false` is an assertion that there is no such law. Only `null` means
        we did not look -- filling the gap with false is the specific mistake
        this file's own rules forbid."""
        for code, e in self.states.items():
            self.assertIn(type(e.get("charity_care")), (dict, type(None)), code)
            self.assertIn(type(e.get("state_program")), (dict, type(None)), code)

    def test_unresearched_charity_care_still_says_so(self):
        """No shipped state has a null charity_care any more, but the guard has to
        stay: the next entry someone adds from a partial source must not deliver
        its silence as "there is no such law here"."""
        import tempfile
        import json
        from itemize import states as states_mod
        tmp = tempfile.mkdtemp()
        payload = json.loads(json.dumps(self.data))
        payload["states"]["ZZ"] = {
            "name": "Partialia", "ambulance_balance_billing": True,
            "ambulance_note": "x", "charity_care": None, "state_program": None,
            "citations": ["http://example/z"],
            "verified": datetime.date.today().isoformat()}
        with open(os.path.join(tmp, "states.json"), "w") as f:
            json.dump(payload, f)
        found = states_mod.evaluate([Line(idx=1, charge=100.0)], FakeRef(),
                                    Context(state="ZZ"), tmp)
        self.assertIn("state_assistance_not_researched", {x.rule for x in found})

    def test_sourced_negative_is_distinct_from_unresearched(self):
        """`state_minimum_standards: false` says we looked and there is none;
        null says we did not look. They must not produce the same finding."""
        import tempfile
        import json
        from itemize import states as states_mod
        tmp = tempfile.mkdtemp()
        with open(os.path.join(tmp, "states.json"), "w") as f:
            json.dump(self.data, f)
        neg = next(k for k, e in self.states.items()
                   if (e.get("charity_care") or {}).get(
                       "state_minimum_standards") is False)
        found = states_mod.evaluate([Line(idx=1, charge=100.0)], FakeRef(),
                                    Context(state=neg), tmp)
        fired = {x.rule for x in found}
        self.assertIn("state_no_assistance_standard", fired, neg)
        self.assertNotIn("state_assistance_not_researched", fired)
        text = next(x for x in found if x.rule == "state_no_assistance_standard").detail
        # The whole point: a negative must not read as "nothing to ask for".
        self.assertIn("501(r)", text)
        self.assertIn("not** mean there is nothing to ask for", text)

    def test_financial_assistance_recorded_for_every_jurisdiction(self):
        nulls = [k for k, e in self.states.items() if e.get("charity_care") is None]
        self.assertEqual(nulls, [])
        std = sum(1 for e in self.states.values()
                  if (e.get("charity_care") or {}).get("state_minimum_standards"))
        self.assertEqual(std, 4, "4 of the newly added states set minimum standards")

    def test_fully_researched_state_does_not_get_the_partial_notice(self):
        import tempfile
        import json
        from itemize import states as states_mod
        tmp = tempfile.mkdtemp()
        with open(os.path.join(tmp, "states.json"), "w") as f:
            json.dump(self.data, f)
        found = states_mod.evaluate([Line(idx=1, charge=100.0)], FakeRef(),
                                    Context(state="MA"), tmp)
        self.assertNotIn("state_assistance_not_researched", {x.rule for x in found})

    def test_sunsetting_laws_are_recorded(self):
        """Texas, Utah, Mississippi and Washington's fallback all expire. An entry
        that silently outlives its statute is the failure this file warns about."""
        for code in ("TX", "UT", "MS"):
            self.assertIn("EXPIRES", self.states[code]["ambulance_note"], code)
        self.assertIn("expires", self.states["WA"]["ambulance_note"])
