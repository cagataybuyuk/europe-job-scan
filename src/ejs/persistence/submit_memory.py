from __future__ import annotations

from dataclasses import asdict

from ejs.contracts.submit import ApprovedSubmitPolicy
from ejs.domain.submission import PreSubmitValidationResult, SubmissionAttempt


class InMemorySubmitRepository:
    """Small event/registry store used to prove SUBMIT-1 immutability and idempotency."""

    def __init__(self) -> None:
        self.policies: dict[str, ApprovedSubmitPolicy] = {}
        self.validations: dict[str, PreSubmitValidationResult] = {}
        self.attempts: dict[str, SubmissionAttempt] = {}

    @staticmethod
    def _put_immutable(store: dict, key: str, value) -> bool:
        if key in store:
            if asdict(store[key]) == asdict(value):
                return False
            raise ValueError(f"immutable key collision: {key}")
        store[key] = value
        return True

    def register_policy(self, policy: ApprovedSubmitPolicy) -> bool:
        policy.validate()
        return self._put_immutable(self.policies, policy.version, policy)

    def append_validation(self, validation: PreSubmitValidationResult) -> bool:
        if not validation.validation_key:
            raise ValueError("validation_key is required")
        return self._put_immutable(self.validations, validation.validation_key, validation)

    def reserve_attempt(self, attempt: SubmissionAttempt) -> bool:
        if not attempt.submit_key:
            raise ValueError("submit_key is required")
        return self._put_immutable(self.attempts, attempt.submit_key, attempt)

    def has_submit_key(self, submit_key: str) -> bool:
        return submit_key in self.attempts
