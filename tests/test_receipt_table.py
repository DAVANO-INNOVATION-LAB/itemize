"""Synthetic layout checks for receipts with quantity, price, discounts and total."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

from itemize.parse import parse_details


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = """1 Example Street
Description  Catalog Ref  Qty  Price  Discount  Total
Walking boot  X992  2  $20.00  $40.00
Packing tape  ABC99 3  $10.00  $5.00  $25.00
Loose Parts  1  $1.00  —  $1.00
3  $5.00  $15.00
Other  2  $3.00  $999.00
Subtotal  $65.00
Tax  $1.00
Total  $66.00
Paid  $66.00
Due  $0.00
After summary  1  $5.00  $5.00"""
EXPECTED = [
    {"desc": "Walking boot", "charge": 40, "units": 2, "code": ""},
    {"desc": "Packing tape", "charge": 25, "units": 3, "code": ""},
    {"desc": "Loose Parts", "charge": 1, "units": 1, "code": ""},
]


class ReceiptTableTests(unittest.TestCase):
    @staticmethod
    def snapshot(text):
        lines, stats = parse_details(text)
        return {
            "lines": [{k: getattr(l, k) for k in ("desc", "charge", "units", "code")} for l in lines],
            "stats": stats,
        }

    def test_labeled_receipt_stays_distinct_from_metadata(self):
        result = self.snapshot(FIXTURE)
        self.assertEqual(result["lines"], EXPECTED)
        self.assertEqual(result["stats"]["uncertain"], 3)

    def test_no_receipt_layout_guess_from_unlabeled_total(self):
        s = "Description  Total\nA service  1  $5.00  $5.00"
        self.assertEqual(self.snapshot(s)["lines"], [])

    @unittest.skipUnless(shutil.which("node"), "node required for browser parser parity")
    def test_browser_python_parity(self):
        script = r"""
const fs = require('fs');
const {lines,stats} = require('./web/rules.js').parseBillDetails(fs.readFileSync(0,'utf8'));
process.stdout.write(JSON.stringify({lines:lines.map(({desc,charge,units,code})=>({desc,charge,units,code})),stats}));
"""
        node = subprocess.run(["node", "-e", script], cwd=ROOT, input=FIXTURE,
                              text=True, capture_output=True, check=True, timeout=15)
        self.assertEqual(json.loads(node.stdout), self.snapshot(FIXTURE))

    @unittest.skipUnless(shutil.which("node"), "node required for browser parser parity")
    def test_duplicate_needs_same_description_for_uncoded_items(self):
        from itemize.rules import rule_exact_duplicates

        rows = [
            {"idx": 1, "desc": "Blue | tape", "code": "", "date": "", "charge": 1, "units": 1},
            {"idx": 2, "desc": "Green tape", "code": "", "date": "", "charge": 1, "units": 1},
            {"idx": 3, "desc": "Blue |    TAPE", "code": "", "date": "", "charge": 1, "units": 1},
            {"idx": 4, "desc": "Laboratory", "code": "11111", "date": "", "charge": 2, "units": 1},
            {"idx": 5, "desc": "Lab", "code": "11111", "date": "", "charge": 2, "units": 1},
        ]
        from itemize.model import Line
        findings = rule_exact_duplicates([Line(**r) for r in rows], None)
        self.assertEqual([f.lines for f in findings], [[1, 3], [4, 5]])
        script = r"""
const fs = require('fs'), I = require('./web/rules.js');
const input = JSON.parse(fs.readFileSync(0,'utf8'));
const findings = I.audit(input, new I.Reference({}));
process.stdout.write(JSON.stringify(findings.filter(f=>f.rule === 'exact_duplicate').map(f=>f.lines)));
"""
        node = subprocess.run(["node", "-e", script], cwd=ROOT, input=json.dumps(rows),
                              text=True, capture_output=True, check=True, timeout=15)
        self.assertEqual(sorted(json.loads(node.stdout)), [[1, 3], [4, 5]])
