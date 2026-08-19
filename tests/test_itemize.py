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
