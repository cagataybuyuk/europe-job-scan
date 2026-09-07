import unittest
from pathlib import Path

from ejs.contracts.freeze import load_contract_freeze


class FreezeTests(unittest.TestCase):
    def test_freeze_is_read_only(self):
        path = Path(__file__).parents[2] / "config" / "contract_freeze_2026-08-17.json"
        freeze = load_contract_freeze(path)
        self.assertEqual(freeze.write_authority, "NONE")
        self.assertFalse(freeze.browser_execution_enabled)
        self.assertEqual(freeze.active_rule_bundle, "EJS-BUNDLE-1.4")
        self.assertEqual(freeze.regression_fixture_count, 60)


if __name__ == "__main__":
    unittest.main()
