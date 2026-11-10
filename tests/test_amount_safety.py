"""Deterministic, offline regressions and stress tests, with no patient data."""
import json
import os
import random
import shutil
import subprocess
import unittest
from dataclasses import asdict

from itemize import evidence
from itemize.eob import cross_check, parse_eob
from itemize.model import Finding, Line
from itemize.rules import audit, disputable_total, next_actions
from test_itemize import FakeRef

NODE = shutil.which('node')
DRIVER = os.path.join(os.path.dirname(__file__), 'safety_driver.js')


def finding(lines, amount, recoverable=True):
    return Finding('test', 'high', 'Synthetic finding', 'Synthetic only',
                   lines=lines, amount=amount, recoverable=recoverable)


def js(mode, cases=None, **kwargs):
    result = subprocess.run([NODE, DRIVER], input=json.dumps(dict(mode=mode, cases=cases, **kwargs)),
                            capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


def oracle(findings):
    """Independent simple set-merging oracle, not union-find."""
    groups = []
    for f in findings:
        if not f.recoverable or not f.lines or f.amount <= 0:
            continue
        ids, amount = set(f.lines), f.amount
        keep = []
        for old_ids, old_amount in groups:
            if ids & old_ids:
                ids |= old_ids
                amount = max(amount, old_amount)
            else:
                keep.append((old_ids, old_amount))
        # Existing groups are disjoint, so a single pass suffices.
        groups = keep + [(ids, amount)]
    return sum(amount for _, amount in groups)


class TestAmountSafety(unittest.TestCase):
    def test_missing_and_partial_eob_responsibility(self):
        lines = [Line(1, code='J1885', charge=100)]
        for values in ([None], [None, 0], [25, None], [None, None]):
            rows = [dict(code='J1885', patient=p) for p in values]
            self.assertEqual(cross_check(lines, rows, None), [])
        rows = parse_eob('code,patient responsibility\nJ1885,\n')
        self.assertIsNone(rows[0]['patient'])
        self.assertEqual(cross_check(lines, rows, None), [])

    def test_zero_and_credit_remain_known(self):
        lines = [Line(1, code='J1885', charge=100)]
        for value in (0, -10, 25):
            found = cross_check(lines, [dict(code='J1885', patient=value)], None)
            self.assertEqual(found[0].amount, 100-value)
            self.assertIn('eob_total_mismatch', [f.rule for f in found])

    def test_partial_whole_eob_does_not_hide_complete_code(self):
        lines = [Line(1, code='J1885', charge=100), Line(2, code='E0114', charge=100)]
        rows = [dict(code='J1885', patient=25), dict(code='E0114', patient=None)]
        found = cross_check(lines, rows, None)
        self.assertEqual([(f.rule, f.lines, f.amount) for f in found],
                         [('eob_line_mismatch', [1], 75)])

    def test_reproduced_overlap_all_python_outputs(self):
        lines = [Line(i, code='J1885', charge=100) for i in (1, 2)]
        found = audit(lines, FakeRef(), eob_rows=[dict(code='J1885', patient=50)])
        self.assertEqual(disputable_total(found, lines), 150)
        self.assertEqual(next(a for a in next_actions(found, lines=lines) if a['key'] == 'dispute')['amount'], 150)
        payload = json.loads(evidence.to_json(lines, found, FakeRef()))
        self.assertEqual(payload['summary']['disputable_line_items'], 150)
        self.assertEqual(next(a for a in payload['next_actions'] if a['key'] == 'dispute')['amount'], 150)
        self.assertIn('Directly disputable line items: **$150.00**', evidence.render(lines, found, FakeRef()))

    def test_transitive_overlap_disjoint_and_protections(self):
        found = [finding([1], 30), finding([2], 40), finding([1, 2], 50),
                 finding([3], 20), finding([], 1000), finding([4], 90, False)]
        self.assertEqual(disputable_total(found), 70)
        self.assertEqual(disputable_total(found, [Line(1, charge=60)]), 60)
        self.assertEqual(disputable_total(found, [Line(1, charge=-60)]), 0)
        self.assertEqual(disputable_total([finding([1], -20)]), 0)

    def test_arithmetic_overlap_is_not_added(self):
        lines = [Line(i, code='J1885', units=1, unit_price=30, charge=100) for i in (1, 2)]
        found = audit(lines, FakeRef(), eob_rows=[dict(code='J1885', patient=50)])
        self.assertIn('unit_price_arithmetic', [f.rule for f in found])
        self.assertEqual(disputable_total(found), 150)

    @unittest.skipIf(NODE is None, 'node required for JS parity')
    def test_seeded_overlap_properties_and_parity(self):
        rng = random.Random(501)
        cases, expected = [], []
        for _ in range(400):
            found = [finding(rng.sample(range(30), rng.randrange(5)), rng.randrange(-10, 200),
                             rng.choice([True, True, False])) for _ in range(rng.randrange(1, 40))]
            want = oracle(found)
            self.assertEqual(disputable_total(found), want)
            rng.shuffle(found)
            self.assertEqual(disputable_total(found), want)
            self.assertEqual(disputable_total(found + found), want)
            self.assertEqual(disputable_total(found + [finding([], 100000)]), want)
            cases.append(dict(findings=[asdict(f) for f in found]))
            expected.append(want)
        results = js('aggregate', cases)
        self.assertEqual([r['total'] for r in results], expected)
        for result, want in zip(results, expected):
            if result['actions']:
                self.assertEqual(result['actions'][0]['amount'], want)

    @unittest.skipIf(NODE is None, 'node required for JS parity')
    def test_seeded_eob_missing_values_and_parity(self):
        rng = random.Random(502)
        cases, expected = [], []
        for _ in range(400):
            lines = [Line(i, code=f'J{i % 5:04}', charge=rng.randrange(200)) for i in range(15)]
            rows = [dict(code=f'J{i % 5:04}', patient=rng.choice([None, None, 0, -10, 20, 100]))
                    for i in range(rng.randrange(1, 20))]
            found = cross_check(lines, rows, None)
            for f in found:
                if f.rule == 'eob_total_mismatch':
                    self.assertTrue(all(r['patient'] is not None for r in rows))
                if f.rule == 'eob_line_mismatch':
                    code = lines[f.lines[0]].code
                    self.assertTrue(all(r['patient'] is not None for r in rows if r['code'] == code))
            cases.append(dict(lines=[asdict(l) for l in lines], rows=rows))
            expected.append([(f.rule, f.lines, f.amount, f.recoverable) for f in found])
        results = js('eob', cases)
        self.assertEqual([[(f['rule'], f['lines'], f['amount'], f['recoverable']) for f in rows]
                          for rows in results], expected)

    @unittest.skipIf(NODE is None, 'node required for JS parity')
    def test_large_overlap_chain_and_disjoint_stress(self):
        for found, want in (([finding([i, i+1], 100) for i in range(20000)], 100),
                            ([finding([i], 100) for i in range(20000)], 2000000)):
            self.assertEqual(disputable_total(found), want)
            self.assertEqual(js('aggregate', [dict(findings=[asdict(f) for f in found])])[0]['total'], want)

    @unittest.skipIf(NODE is None, 'node required for JS parity')
    def test_large_eob_group_partial_and_explicit_zero(self):
        lines = [Line(i, code='J1885', charge=100) for i in range(10000)]
        rows = [dict(code='J1885', patient=0) for _ in lines]
        complete = cross_check(lines, rows, None)
        self.assertEqual(complete[0].amount, 1000000)
        cases = [dict(lines=[asdict(l) for l in lines], rows=rows)]
        partial = [dict(row) for row in rows]
        partial[-1]['patient'] = None
        self.assertEqual(cross_check(lines, partial, None), [])
        cases.append(dict(lines=cases[0]['lines'], rows=partial))
        actual = js('eob', cases)
        self.assertEqual(actual[0][0]['amount'], 1000000)
        self.assertEqual(actual[1], [])

    @unittest.skipIf(NODE is None, 'node required for output regression')
    def test_browser_outputs_use_same_review_total(self):
        for credit, want in ((0, 150), (-125, 75), (-250, 0)):
            lines = [Line(i, code='J1885', charge=100) for i in (1, 2)]
            if credit:
                lines.append(Line(3, code='CREDIT', charge=credit))
            found = audit(lines, FakeRef(), eob_rows=[dict(code='J1885', patient=50)])
            output = js('outputs', lines=[asdict(l) for l in lines], findings=[asdict(f) for f in found])
            if want:
                for key in ('summary', 'actions', 'announcement', 'letter'):
                    self.assertIn(f'${want:.2f}', output[key], key)
                self.assertIn(f'Directly disputable line items: **${want:.2f}**', output['markdown'])
            else:
                self.assertNotIn('Disputable line items', output['summary'])
                self.assertNotIn('The disputed amount is', output['letter'])
            payload = json.loads(evidence.to_json(lines, found, FakeRef()))
            self.assertEqual(payload['summary']['disputable_line_items'], want)
            self.assertEqual(next(a for a in payload['next_actions'] if a['key'] == 'dispute')['amount'], want)
