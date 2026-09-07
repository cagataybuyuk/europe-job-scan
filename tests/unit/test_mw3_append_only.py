from __future__ import annotations

import unittest

from ejs.contracts.mw3 import Mw3Authority, service_canary_idempotency_key, source_run_idempotency_key
from ejs.domain.append_only import AppendOnlyDomain, AppendOnlyEvent
from ejs.persistence.append_only import InMemoryAppendOnlyWriteRepository
from ejs.services.mw3_canary import Mw3CanaryService


class Mw3AppendOnlyTests(unittest.TestCase):
    def test_source_run_key_matches_contract(self):
        self.assertEqual(source_run_idempotency_key("20260817-1134", "irishjobs"), "run:20260817-1134:irishjobs")

    def test_canary_key_is_deterministic(self):
        self.assertEqual(
            service_canary_idempotency_key("MW3-CANARY-20260817-01", "service_canary_audit"),
            "canary:MW-3:MW3-CANARY-20260817-01:service_canary_audit",
        )

    def test_duplicate_append_is_suppressed(self):
        authority = Mw3Authority(True, frozenset({AppendOnlyDomain.SERVICE_CANARY_AUDIT}))
        repo = InMemoryAppendOnlyWriteRepository(authority)
        event = AppendOnlyEvent(
            AppendOnlyDomain.SERVICE_CANARY_AUDIT,
            "canary:MW-3:x:service_canary_audit",
            "x",
            {},
        )
        first = repo.append(event)
        second = repo.append(event)
        self.assertTrue(first.appended)
        self.assertFalse(second.appended)
        self.assertTrue(second.duplicate_suppressed)
        self.assertEqual(len(repo.events), 1)

    def test_unreleased_domain_is_blocked(self):
        authority = Mw3Authority(True, frozenset({AppendOnlyDomain.SERVICE_CANARY_AUDIT}))
        repo = InMemoryAppendOnlyWriteRepository(authority)
        with self.assertRaises(PermissionError):
            repo.append(AppendOnlyEvent(AppendOnlyDomain.SOURCE_HEALTH_LOG, "run:x:y", "x", {}))

    def test_business_state_writes_cannot_be_enabled(self):
        authority = Mw3Authority(
            True,
            frozenset({AppendOnlyDomain.SERVICE_CANARY_AUDIT}),
            business_state_writes_enabled=True,
        )
        with self.assertRaises(PermissionError):
            authority.validate()

    def test_external_form_writes_cannot_be_enabled(self):
        authority = Mw3Authority(
            True,
            frozenset({AppendOnlyDomain.SERVICE_CANARY_AUDIT}),
            external_form_writes_enabled=True,
        )
        with self.assertRaises(PermissionError):
            authority.validate()

    def test_canary_passes_and_retry_is_noop(self):
        authority = Mw3Authority(True, frozenset({AppendOnlyDomain.SERVICE_CANARY_AUDIT}))
        repo = InMemoryAppendOnlyWriteRepository(authority)
        service = Mw3CanaryService(repo)
        first = service.run("MW3-CANARY-20260817-01")
        second = service.run("MW3-CANARY-20260817-01")
        self.assertTrue(first.passed)
        self.assertTrue(first.appended)
        self.assertTrue(second.duplicate_suppressed)
        self.assertEqual(first.business_state_mutations, 0)
        self.assertEqual(first.external_form_mutations, 0)


if __name__ == "__main__":
    unittest.main()
