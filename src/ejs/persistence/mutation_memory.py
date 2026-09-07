from __future__ import annotations

from dataclasses import replace

from ejs.contracts.mw5 import opportunity_key
from ejs.domain.mutation import OpportunityState
from ejs.services.guarded_mutation import current_fingerprint


class InjectedWriteFailure(RuntimeError):
    pass


class ConcurrentMutationError(RuntimeError):
    pass


class InMemoryMutationRepository:
    """Stateful MW-5 test repository with deterministic failure injection.

    It models the ordered Workspace write-set without pretending to be a Google
    Sheets transport. The production connector remains a separate boundary.
    """

    def __init__(
        self,
        states: list[OpportunityState],
        *,
        outcome_keys: set[str] | None = None,
        pipeline_keys: set[str] | None = None,
        fail_once_after: str | None = None,
    ) -> None:
        self._states = {
            opportunity_key(s.company, s.role, s.country): s for s in states
        }
        self.outcome_keys = set(outcome_keys or set())
        self.pipeline_keys = set(pipeline_keys or set())
        self.derived_reconciled: set[str] = set()
        self.fail_once_after = fail_once_after
        self._failure_used = False
        self.application_mutation_count = 0
        self.outcome_append_count = 0
        self.pipeline_append_count = 0
        self.derived_reconcile_count = 0

    def _maybe_fail(self, boundary: str) -> None:
        if self.fail_once_after == boundary and not self._failure_used:
            self._failure_used = True
            raise InjectedWriteFailure(f"injected failure after {boundary}")

    def read_state(self, opportunity_key_value: str) -> OpportunityState | None:
        return self._states.get(opportunity_key_value)

    def has_outcome(self, outcome_key: str) -> bool:
        return outcome_key in self.outcome_keys

    def has_pipeline(self, pipeline_key: str) -> bool:
        return pipeline_key in self.pipeline_keys

    def append_outcome(self, outcome_key: str) -> bool:
        if outcome_key in self.outcome_keys:
            return False
        self.outcome_keys.add(outcome_key)
        self.outcome_append_count += 1
        self._maybe_fail("outcome")
        return True

    def update_application(
        self,
        opportunity_key_value: str,
        *,
        expected_fingerprint: str,
        target_values: dict[str, str],
    ) -> bool:
        current = self._states.get(opportunity_key_value)
        if current is None:
            raise KeyError(opportunity_key_value)
        if current_fingerprint(current) != expected_fingerprint:
            raise ConcurrentMutationError("Applications fingerprint changed after plan")

        mapped: dict[str, str] = {}
        for field in ("status", "applied_date", "last_update"):
            if field in target_values:
                mapped[field] = target_values[field]
        # OpportunityState intentionally contains only the concurrency-critical
        # subset. Non-fingerprint business metadata is validated separately by
        # the Workspace patch builder.
        new_state = replace(current, **mapped)
        if new_state == current:
            return False
        self._states[opportunity_key_value] = new_state
        self.application_mutation_count += 1
        self._maybe_fail("application")
        return True

    def append_pipeline(self, pipeline_key: str) -> bool:
        if pipeline_key in self.pipeline_keys:
            return False
        self.pipeline_keys.add(pipeline_key)
        self.pipeline_append_count += 1
        self._maybe_fail("pipeline")
        return True

    def reconcile_derived(self, opportunity_key_value: str) -> bool:
        if opportunity_key_value in self.derived_reconciled:
            return False
        self.derived_reconciled.add(opportunity_key_value)
        self.derived_reconcile_count += 1
        self._maybe_fail("derived")
        return True
