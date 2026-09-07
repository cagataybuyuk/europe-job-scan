import unittest

from ejs.domain.models import WorkspaceSnapshot
from ejs.persistence.in_memory import DisabledProductionWriteRepository, InMemoryWorkspaceReadRepository
from ejs.services.shadow import ShadowRunService


class ShadowRunTests(unittest.TestCase):
    def test_shadow_run_never_writes(self):
        snapshot = WorkspaceSnapshot(
            source_id="test",
            captured_at="2026-08-17T10:00:00+03:00",
            applications=({"id": 1}, {"id": 2}),
            rules=({"id": "A", "status": "Active"}, {"id": "B", "status": "In Development"}),
        )
        service = ShadowRunService(InMemoryWorkspaceReadRepository(snapshot))
        result = service.run("shadow-001")
        self.assertEqual(result.application_count, 2)
        self.assertEqual(result.active_rule_count, 1)
        self.assertEqual(result.write_attempts, 0)

    def test_write_repository_is_hard_disabled(self):
        writer = DisabledProductionWriteRepository()
        with self.assertRaises(PermissionError):
            writer.write({"status": "Applied"})


if __name__ == "__main__":
    unittest.main()
