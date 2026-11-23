"""Receipt intake regressions: addresses are never amounts or procedure codes."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

from itemize.parse import parse, parse_details

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")

MIXED = """Example Medical Center
1 Main Street
Suite 7
Springfield, va 22150-0013
Account # 112233
Claim 52525.00
Date of birth 01/01/1980
2026-03-14
Description Qty Charges
2026-03-14  J1885  Ketorolac  2  180.00
2026-03-14 J1885 Ketorolac 2 180.00
2026-03-14 A4550 Dressing 1 1
Supplies $1
Medical records 1.00
Total knee replacement 12.00
Unclear item 4
Service reference AB12345
TOTAL $375
"""

EXPECTED = [
    ("J1885", "Ketorolac", 2, 180, "2026-03-14"),
    ("J1885", "Ketorolac", 2, 180, "2026-03-14"),
    ("A4550", "Dressing", 1, 1, "2026-03-14"),
    ("", "Supplies", 1, 1, ""),
    ("", "Medical records", 1, 1, ""),
    ("", "Total knee replacement", 1, 12, ""),
]


def shape(lines):
    return [(line.code, line.desc, line.units, line.charge, line.date) for line in lines]


def browser(text):
    lint = '''const I=require('./web/rules.js');
      let s=''; process.stdin.on('data', c=>s+=c);
      process.stdin.on('end',()=>process.stdout.write(JSON.stringify(I.parseBillDetails(JSON.parse(s)))));'''
    run = subprocess.run([NODE, "-e", lint], cwd=ROOT, input=json.dumps(text),
                         text=True, capture_output=True, timeout=15, check=True)
    resp = json.loads(run.stdout)
    return ([tuple(row[k] for k in ("code", "desc", "units", "charge", "date"))
             for row in resp["lines"]], resp["stats"])


class TestReceiptInput(unittest.TestCase):
    def test_mixed_receipt_keeps_only_evidenced_charges(self):
        lines, stats = parse_details(MIXED)
        self.assertEqual(shape(lines), EXPECTED)
        self.assertEqual(stats, {"mode": "text", "raw": 18, "parsed": 6, "ignored": 9, "unread": 3,
                                 "uncertain": 1, "status": "read"})

    def test_address_only_and_unlabeled_bare_amount_are_not_charges(self):
        for s in ["1 Main Street", "123 Main St\nReston, VA 20190", "PO BOX 1234",
                  "123 NEW PARK ST  SUITE 20", "Ref AB12345", "Office number 12345",
                  "Vitamin B12", "Medication 5", "2026-03-14  J1885  No price  REF AB12345",
                  "2026-03-14 J1885 Draft six-digit typo 4.123456"]:
            with self.subTest(s=s):
                self.assertEqual(parse(s), [])

    def test_structured_charge_still_uses_explicit_amount_cell(self):
        lines, stats = parse_details(
            'Code,Description,Charges\nA4550,Supply,1\n,1 Main Street,1\n,Total,$1\n'
            'A4550,Invalid 1,Reference 12345\n')
        self.assertEqual(shape(lines), [("A4550", "Supply", 1, 1, "")])
        self.assertEqual(stats, {"mode": "delimited", "raw": 5, "ignored": 3, "parsed": 1, "unread": 1,
                                 "uncertain": 1, "status": "read"})

    def test_loose_signed_money_and_accepted_alignment(self):
        text = "2026-03-14  CHG40021  Pharmacy handling  1  412.75\nRefund ($1.00)"
        self.assertEqual(shape(parse(text)), [
            ("CHG40021", "Pharmacy handling", 1, 412.75, "2026-03-14"),
            ("", "Refund", 1, -1, ""),
        ])

    def test_aligned_headings_select_real_charge_not_last_amount(self):
        text = ("101 Main Street\nService Date  Procedure  Charges  Allowed Amount  Patient Responsibility\n"
                "03/14/2026  99283  US$125.00  $80.00  $30.00\n"
                "03/14/2026  J1885  $1.00  $0.75  $0.25")
        lines, stats = parse_details(text)
        self.assertEqual(shape(lines), [("99283", "", 1, 125, "03/14/2026"),
                                        ("J1885", "", 1, 1, "03/14/2026")])
        self.assertEqual((stats["ignored"], stats["unread"], stats["status"]), (2, 0, "read"))
        if NODE:
            self.assertEqual(browser(text), (shape(lines), stats))

    def test_portal_export_with_address_preamble_and_tsv_charges(self):
        text = "Example Clinic\nReston, VA 20190\nDescription\tTotal Charges\nSupplies\t1\nX-Ray\t111.10"
        lines, stats = parse_details(text)
        self.assertEqual([row.charge for row in lines], [1, 111.10])
        self.assertEqual(stats["mode"], "delimited")
        if NODE:
            self.assertEqual(browser(text), (shape(lines), stats))

    def test_distinguishes_unsupported_table_from_no_text(self):
        samples = [("", "no-text"), ("Example Clinic\nAmount due $500", "no-itemized-lines"),
                   ("03/14/2026 J1885 Injection $120.00 $80.00 $30.00", "uncertain-layout"),
                   ("Service Date  Description  Allowed Amount\n03/14/2026  Clinic Visit  $75.00 $20.00",
                    "uncertain-layout")]
        for text, status in samples:
            with self.subTest(text=text):
                lines, stats = parse_details(text)
                self.assertFalse(lines)
                self.assertEqual(stats["status"], status)
                if NODE:
                    self.assertEqual(browser(text), ([], stats))

    @unittest.skipIf(not NODE, "node unavailable")
    def test_browser_and_python_share_charge_evidence_and_diagnostics(self):
        for text in (MIXED, "1 Main Street", "123 Main St\nReston, VA 20190",
                     "PO BOX 1234\n2026-03-14 J1885 Refund 1 ($1.00)",
                     "Date,Code,Description,Charges\n,99283,Visit,1\n,,PO BOX 1234,1234\n"):
            with self.subTest(text=text):
                lines, stats = parse_details(text)
                self.assertEqual(browser(text), (shape(lines), stats))
