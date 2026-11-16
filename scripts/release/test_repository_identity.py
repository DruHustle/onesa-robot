"""A differently named checkout keeps the canonical Robot release identity."""

import runpy
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class CheckoutIdentityTests(unittest.TestCase):
    def test_renamed_checkout_keeps_canonical_evidence_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            scripts = (
                Path(directory) / "arbitrary-review-folder" / "scripts" / "release"
            )
            scripts.mkdir(parents=True)
            target = scripts / "evidence.py"
            shutil.copyfile(ROOT / "scripts" / "release" / "evidence.py", target)
            module = runpy.run_path(str(target))
            self.assertEqual(module["PRODUCT"], "onesa-robot")
            self.assertEqual(module["REPOSITORY"], "DruHustle/onesa-robot")
            self.assertEqual(module["POLICY"]["ci"], "scaffold.yml")


if __name__ == "__main__":
    unittest.main()
