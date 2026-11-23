"""Browser-controller flow tests with a synthetic fixture and no npm dependencies."""
import shutil
import subprocess
import unittest
from pathlib import Path

NODE = shutil.which("node")
ROOT = Path(__file__).resolve().parent.parent


class ReceiptFlowTests(unittest.TestCase):
    @unittest.skipUnless(NODE, "node is required for browser controller test")
    def test_intake_review_confirmation_and_replacement(self):
        result = subprocess.run(
            [NODE, "tests/receipt_flow_driver.js"], cwd=ROOT,
            capture_output=True, check=False, text=True, timeout=25,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Passed:", result.stdout)
