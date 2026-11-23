"""Exercise the actual browser PDF decoder with synthetic line operators."""

import json
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
SCRIPT = r"""
const fs = require('fs'), vm = require('vm');
const ctx = {TextDecoder, Uint8Array, Blob, DecompressionStream, Response};
const PDF = vm.runInNewContext(fs.readFileSync('web/pdf.js', 'utf8') + '\nPDF', ctx);
const bytes = fs.readFileSync(0);
PDF.extractText(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength))
  .then((text) => process.stdout.write(JSON.stringify(text)))
  .catch((err) => { console.error(err); process.exitCode = 1; });
"""


@unittest.skipUnless(NODE, "node is required for browser PDF layout tests")
class BrowserPdfTextLayout(unittest.TestCase):
    def extract(self, stream):
        # Decoder scans content streams; all content is invented and local.
        body = stream.encode("ascii")
        pdf = (b"%PDF-1.4\n1 0 obj\n<< /Length " + str(len(body)).encode()
               + b" >>\nstream\n" + body + b"\nendstream\nendobj\n%%EOF\n")
        return json.loads(subprocess.run(
            [NODE, "-e", SCRIPT], input=pdf, cwd=ROOT, capture_output=True,
            check=True, timeout=15,
        ).stdout)

    def test_recurring_leading_keeps_address_and_charges_on_own_rows(self):
        out = self.extract(
            "BT /F1 10 Tf 13 TL 46 740 Td "
            "(1 Example Street) Tj T* (Example City, VA 20190) Tj "
            "T* (2026-03-14  99283  EXAMPLE SERVICE  1  $1.00) Tj ET"
        )
        self.assertEqual(out.splitlines(), [
            "1 Example Street", "Example City, VA 20190",
            "2026-03-14  99283  EXAMPLE SERVICE  1  $1.00",
        ])

    def test_repeated_relative_moves_preserve_distinct_rows(self):
        out = self.extract(
            "BT 48 740 Td (Invoice) Tj 0 -13 Td (First row) Tj "
            "0 -13 Td (Next row) Tj 150 0 Td (180.00) Tj ET"
        )
        self.assertEqual(out.splitlines(), ["Invoice", "First row", "Next row  180.00"])

    def test_quote_only_stream_keeps_text_on_the_advanced_rows(self):
        out = self.extract(
            """BT 48 740 Td 0 -13 TD (First) ' 4 1 (Second) " (Third) ' ET"""
        )
        self.assertEqual(out.splitlines(), ["First", "Second", "Third"])
