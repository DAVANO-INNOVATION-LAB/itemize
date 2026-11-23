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


def cid_pdf():
    """A synthetic text layer whose PDF character codes are not ASCII."""
    rows = [
        [(48, "101 Example Street")],
        [(48, "Example City, VA 20190")],
        [(48, "Description"), (260, "Procedure"), (420, "Qty"), (475, "Total")],
        [(48, "Example service"), (260, "99283"), (420, "1"), (475, "$1.00")],
    ]
    mapping = sorted(set("".join(text for row in rows for _, text in row)))
    codes = {char: n + 0x0100 for n, char in enumerate(mapping)}
    cmap = ("/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
            "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
            "/CMapName /SyntheticBillUnicode def\n/CMapType 2 def\n"
            "1 begincodespacerange\n<0000> <ffff>\nendcodespacerange\n"
            + str(len(codes)) + " beginbfchar\n"
            + "".join("<%04X> <%04X>\n" % (n, ord(c)) for c, n in codes.items())
            + "endbfchar\nendcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n")
    content = ""
    for i, row in enumerate(rows):
        for x, text in row:
            content += "BT /F1 10 Tf 1 0 0 1 %d %d Tm <%s> Tj ET\n" % (
                x, 740 - 18*i, "".join("%04X" % codes[c] for c in text))

    def stream(s):
        return ("<< /Length %d >>\nstream\n%sendstream" % (len(s.encode("ascii")), s))

    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        "/Resources << /Font << /F1 4 0 R >> >> /Contents 7 0 R >>",
        "<< /Type /Font /Subtype /Type0 /BaseFont /SyntheticBill /Encoding /Identity-H "
        "/DescendantFonts [5 0 R] /ToUnicode 6 0 R >>",
        "<< /Type /Font /Subtype /CIDFontType2 /BaseFont /SyntheticBill "
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> "
        "/FontDescriptor 8 0 R /DW 600 /CIDToGIDMap /Identity >>",
        stream(cmap),
        stream(content),
        "<< /Type /FontDescriptor /FontName /SyntheticBill /Flags 4 /FontBBox [0 -200 1000 900] "
        "/Ascent 900 /Descent -200 /CapHeight 700 /ItalicAngle 0 /StemV 80 >>",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, body in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf.extend(("%d 0 obj\n%s\nendobj\n" % (i, body)).encode("ascii"))
    xref = len(pdf)
    pdf.extend(("xref\n0 %d\n0000000000 65535 f \n" % len(offsets)).encode("ascii"))
    for offset in offsets[1:]:
        pdf.extend(("%010d 00000 n \n" % offset).encode("ascii"))
    pdf.extend(("trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
                % (len(offsets), xref)).encode("ascii"))
    return bytes(pdf)


@unittest.skipUnless(NODE, "node is required for browser PDF layout tests")
class PreparedPdfTextLayout(unittest.TestCase):
    def node(self, code, data=b""):
        return json.loads(subprocess.run(
            [NODE, "--input-type=module", "-e", code], input=data,
            cwd=ROOT, capture_output=True, check=True, timeout=25,
        ).stdout)

    def test_baseline_positions_preserve_columns(self):
        out = self.node("""
            import {assemblePage} from './web/pdf_text.mjs';
            const box=(str,x,y,width)=>({str,transform:[1,0,0,1,x,y],width});
            console.log(JSON.stringify(assemblePage([
              box('$1.00',140,30,18), box('Address',10,50,37),
              box('Code',10,30,20), box('99283',35,30,22),
              box('Next row',10,10,30)
            ])));
        """)
        self.assertEqual(out, "Address\nCode 99283  $1.00\nNext row")

    def test_unprepared_reader_refuses_raw_cid_codes(self):
        code = SCRIPT.replace("console.error(err); process.exitCode = 1;",
                              "process.stdout.write(JSON.stringify(err.code));")
        result = subprocess.run([NODE, "-e", code], input=cid_pdf(), cwd=ROOT,
                                capture_output=True, check=True, timeout=15)
        self.assertEqual(json.loads(result.stdout), "PDF_ASSETS_REQUIRED")

    @unittest.skipUnless((ROOT / "web/vendor/ocr/pdfjs/pdf.mjs").exists(),
                         "run tools/prepare_ocr.py for prepared CID decoding test")
    def test_prepared_reader_uses_tounicode_and_preserves_rows(self):
        out = self.node("""
            import {readFileSync} from 'node:fs';
            import {extractPdfText} from './web/pdf_text.mjs';
            const bytes = new Uint8Array(readFileSync(0));
            console.log(JSON.stringify(await extractPdfText(bytes)));
        """, cid_pdf())
        self.assertEqual(out.splitlines(), ["101 Example Street", "Example City, VA 20190",
                                           "Description  Procedure  Qty  Total",
                                           "Example service  99283  1  $1.00"])
