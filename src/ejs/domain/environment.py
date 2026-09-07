from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Environment(StrEnum):
    LOCAL_DEV = "local-dev"
    INTEGRATION_TEST = "integration-test"
    STAGING_SHADOW = "staging-shadow"
    PRODUCTION = "production"


@dataclass(frozen=True)
class RuntimePolicy:
    environment: Environment
    production_spreadsheet_id: str
    configured_spreadsheet_id: str
    allow_business_writes: bool = False
    allow_external_form_writes: bool = False

    def validate(self) -> None:
        is_prod_target = self.configured_spreadsheet_id == self.production_spreadsheet_id
        if self.environment != Environment.PRODUCTION and is_prod_target and self.allow_business_writes:
            raise PermissionError("Non-production runtime cannot write production business state")
        if self.allow_external_form_writes:
            raise PermissionError("External form writes are not released in MW-0/MW-1")
