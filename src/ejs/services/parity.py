from __future__ import annotations

from dataclasses import dataclass

from ejs.domain.models import ContractFreeze, WorkspaceSnapshot


@dataclass(frozen=True)
class ParityCheck:
    name: str
    expected: object
    observed: object

    @property
    def passed(self) -> bool:
        return self.expected == self.observed


@dataclass(frozen=True)
class Mw1ParityReport:
    capture_id: str
    checks: tuple[ParityCheck, ...]
    write_attempts: int = 0

    @property
    def passed(self) -> bool:
        return self.write_attempts == 0 and all(check.passed for check in self.checks)

    @property
    def pass_rate(self) -> float:
        if not self.checks:
            return 1.0
        return sum(check.passed for check in self.checks) / len(self.checks)


def build_mw1_parity_report(snapshot: WorkspaceSnapshot, freeze: ContractFreeze) -> Mw1ParityReport:
    cfg = snapshot.config
    rule = cfg["rule_registry"]
    apps = cfg["application_metrics"]
    sources = cfg["source_targeting"]
    cvs = cfg["cv_base_library"]
    answers = cfg["application_answer_library"]
    candidate = cfg["candidate_profile"]

    checks = (
        ParityCheck("spreadsheet_id", freeze_source_id(freeze), snapshot.source_id),
        ParityCheck("active_rule_bundle", freeze.active_rule_bundle, rule["active_rule_bundle"]),
        ParityCheck("active_rule_count", freeze.active_contract_count, rule["active_rule_count"]),
        ParityCheck("regression_fixture_count", freeze.regression_fixture_count, rule["regression_fixture_count"]),
        ParityCheck("application_count", apps["total_tracked"], len(snapshot.applications)),
        ParityCheck("configured_source_count", 11, sources["configured_source_count"]),
        ParityCheck("source_key_uniqueness", sources["configured_source_count"], len(set(sources["sources"]))),
        ParityCheck("cv_base_count", 4, cvs["base_count"]),
        ParityCheck("ready_pdf_count", 4, cvs["ready_pdf_count"]),
        ParityCheck("answer_question_type_count", 24, answers["question_type_count"]),
        ParityCheck("answer_key_uniqueness", answers["question_type_count"], len(set(answers["question_types"]))),
        ParityCheck("candidate_current_title", "Synthetic Test Title", candidate["Current Title"]),
        ParityCheck("candidate_current_location", "Synthetic Test Location", candidate["Current Location"]),
        ParityCheck("candidate_notice_period", "Synthetic Test Notice", candidate["Notice Period"]),
        ParityCheck("candidate_english", "Synthetic Test English", candidate["English"]),
        ParityCheck("write_authority", "NONE", freeze.write_authority),
        ParityCheck("browser_execution_enabled", False, freeze.browser_execution_enabled),
    )
    return Mw1ParityReport(capture_id=cfg["capture_id"], checks=checks, write_attempts=0)


def freeze_source_id(freeze: ContractFreeze) -> str:
    # The frozen production spreadsheet ID is fixed by MW-0. Kept explicit in v0.1
    # until ContractFreeze carries source_id in the domain model.
    return "1_HuYScTMsVmr29SBeaMRbZqOFiZ3gZLUcFm05xnaYIg"
