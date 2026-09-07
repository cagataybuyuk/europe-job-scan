import unittest
from pathlib import Path

from ejs.contracts.freeze import load_contract_freeze
from ejs.persistence.connector_capture import ConnectorCaptureWorkspaceReadRepository
from ejs.services.parity import build_mw1_parity_report


class ConnectorCaptureTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).parents[2]
        self.capture = root / "config" / "live_connector_capture_2026-08-17.json"
        self.freeze = root / "config" / "contract_freeze_2026-08-17.json"

    def test_capture_maps_deterministically(self):
        repo = ConnectorCaptureWorkspaceReadRepository(self.capture)
        first = repo.read_snapshot()
        second = repo.read_snapshot()
        self.assertEqual(first, second)
        self.assertEqual(len(first.applications), 52)
        self.assertEqual(sum(r["status"] == "Active" for r in first.rules), 14)

    def test_mw1_critical_parity_is_100_percent(self):
        repo = ConnectorCaptureWorkspaceReadRepository(self.capture)
        snapshot = repo.read_snapshot()
        freeze = load_contract_freeze(self.freeze)
        report = build_mw1_parity_report(snapshot, freeze)
        self.assertTrue(report.passed)
        self.assertEqual(report.pass_rate, 1.0)
        self.assertEqual(report.write_attempts, 0)


if __name__ == "__main__":
    unittest.main()
