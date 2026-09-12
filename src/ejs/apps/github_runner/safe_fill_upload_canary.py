from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys

from ejs.apps.github_runner.canary_policy import validate_canary_approval
from ejs.contracts.browser import BrowserInspectionRequest
from ejs.contracts.file_upload import ApprovedFileAsset, ArtifactType, FileUploadPlan
from ejs.contracts.prefill import FieldOwnership, MappingConfidence, PrefillFieldPlan, ResolverStatus
from ejs.contracts.smartrecruiters_execution import SmartRecruitersExecutionAuthority, SmartRecruitersExecutionRequest
from ejs.contracts.submit import RolloutStage
from ejs.domain.submission import ControlResolution, ResolutionMode
from ejs.domain.template_history import MappingConfidence as HistoryMappingConfidence
from ejs.persistence.tmh_memory import InMemoryTmhRepository
from ejs.services.browser_worker import BrowserRuntimeConfig, PlaywrightBrowserWorker
from ejs.services.smartrecruiters_executor import PlaywrightSmartRecruitersExecutor


ROOT = Path(__file__).resolve().parents[4]
FIXTURE = ROOT / "tests" / "fixtures" / "browser" / "smartrecruiters_e2e_form.html"
FIXTURE_CV = ROOT / "tests" / "fixtures" / "browser" / "test_cv.pdf"


def _url(path: Path) -> str:
    return path.resolve().as_uri()


def _fixture_plans(cv_path: Path) -> tuple[tuple[PrefillFieldPlan, ...], tuple[FileUploadPlan, ...], tuple[ControlResolution, ...]]:
    def fp(key: str, control: str, canonical: str, ctype: str, value: str, ownership=FieldOwnership.AUTO_SAFE, required=True):
        return PrefillFieldPlan(key, control, canonical, ctype, value, f"canary:{canonical}:v1", ownership, ResolverStatus.RESOLVED, MappingConfidence.HIGH, False, required)

    fields = (
        fp("first", "input:firstName", "candidate.first_name", "text", "Canary"),
        fp("last", "input:lastName", "candidate.last_name", "text", "User"),
        fp("email", "input:email", "candidate.email", "email", "canary@example.test"),
        fp("phone", "input:phone", "candidate.phone", "tel", "+90 555 000 0000"),
        fp("linkedin", "input:linkedin", "candidate.linkedin_url", "url", "https://example.test/canary", required=False),
        fp("notice", "select:noticePeriod", "employment.notice_period", "select", "4 weeks"),
        fp("motivation", "textarea:motivation", "application.motivation_text", "textarea", "TEST CANARY ONLY", FieldOwnership.DYNAMIC_AI),
    )
    asset = ApprovedFileAsset(
        ArtifactType.CV, "canary-v1", "fixture:canary-cv", "fixture:canary-cv", str(cv_path), cv_path.name,
        "application/pdf", hashlib.sha256(cv_path.read_bytes()).hexdigest(), cv_path.stat().st_size,
    )
    uploads = (FileUploadPlan("upload:cv", "input:cv", "document.cv", asset, "fixture:canary-cv", MappingConfidence.HIGH, True),)
    def fact(control, label, canonical):
        return ControlResolution(control, label, True, canonical, HistoryMappingConfidence.HIGH, ResolutionMode.VERIFIED_FACT, f"canary:{canonical}:v1")
    resolutions = (
        fact("input:firstName", "First name", "candidate.first_name"), fact("input:lastName", "Last name", "candidate.last_name"),
        fact("input:email", "Email", "candidate.email"), fact("input:phone", "Phone", "candidate.phone"),
        fact("select:noticePeriod", "Notice period", "employment.notice_period"),
        ControlResolution("textarea:motivation", "Motivation", True, "application.motivation_text", HistoryMappingConfidence.HIGH, ResolutionMode.GROUNDED_GENERATED, "canary:motivation:v1", grounded=True),
        ControlResolution("input:cv", "CV", True, "document.cv", HistoryMappingConfidence.HIGH, ResolutionMode.ARTIFACT, artifact_ref="artifact:canary-cv:v1"),
    )
    return fields, uploads, resolutions


def _manifest_request(manifest_path: Path, *, cv_path: Path, target_url: str, source_sha: str, run_id: str, inspected_fp: str) -> SmartRecruitersExecutionRequest:
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if str(data.get("source_sha", "")) != source_sha:
        raise ValueError("live canary manifest source_sha must match the immutable checkout SHA")
    if data.get("target_url") and str(data["target_url"]) != target_url:
        raise ValueError("live canary manifest target_url does not match workflow target")
    def field(item: dict) -> PrefillFieldPlan:
        return PrefillFieldPlan(
            str(item["field_plan_key"]), str(item["control_key"]), str(item["canonical_field"]), str(item["control_type"]), str(item["value"]),
            str(item["provenance_ref"]), FieldOwnership(item.get("ownership", "AUTO_SAFE")), ResolverStatus(item.get("resolver_status", "Resolved")),
            MappingConfidence(item.get("mapping_confidence", "High")), bool(item.get("review_required", False)), bool(item.get("required", True)),
        )
    def upload(item: dict) -> FileUploadPlan:
        asset = ApprovedFileAsset(
            ArtifactType(item.get("artifact_type", "cv")), str(item["artifact_version"]), str(item["source_ref"]), str(item["asset_ref"]), str(cv_path),
            str(item.get("expected_file_name", cv_path.name)), str(item.get("expected_mime_type", "application/pdf")),
            str(item["expected_sha256"]), int(item["expected_size_bytes"]), bool(item.get("approved", True)),
        )
        return FileUploadPlan(str(item["upload_plan_key"]), str(item["control_key"]), str(item.get("canonical_field", "document.cv")), asset, str(item["provenance_ref"]), MappingConfidence(item.get("mapping_confidence", "High")), bool(item.get("required", True)))
    def resolution(item: dict) -> ControlResolution:
        return ControlResolution(
            str(item["control_key"]), str(item.get("label", item["control_key"])), bool(item.get("required", True)), str(item["canonical_field"]),
            HistoryMappingConfidence(item.get("mapping_confidence", "High")), ResolutionMode(item.get("resolution_mode", "Verified Fact")),
            str(item.get("provenance_ref", "")), str(item.get("policy_ref", "")), str(item.get("artifact_ref", "")), bool(item.get("grounded", False)),
            bool(item.get("explicit_user_fact", False)), bool(item.get("option_available", True)), str(item.get("notes", "")),
        )
    return SmartRecruitersExecutionRequest(
        execution_id=f"execution:gd004:safe-fill-upload:{run_id}", bridge_request_id=f"bridge:gd004:canary:{run_id}",
        route_key="route:gd004:safe-fill-upload-canary", opportunity_key=str(data["opportunity_key"]), requisition_id=str(data["requisition_id"]),
        application_url=target_url, observed_at=str(data.get("observed_at") or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")),
        expected_form_fingerprint=str(data.get("expected_form_fingerprint") or inspected_fp), candidate_profile_version=str(data["candidate_profile_version"]),
        application_status="To Apply", readiness_confidence="High", unresolved_required=0,
        field_plan=tuple(field(x) for x in data["field_plan"]), upload_plan=tuple(upload(x) for x in data["upload_plan"]),
        control_resolutions=tuple(resolution(x) for x in data["control_resolutions"]), rollout_stage=RolloutStage.R1_PREFILL,
    )


def run(args: argparse.Namespace) -> int:
    source_sha = args.source_sha
    validate_canary_approval(phrase=args.approval_phrase, source_sha=source_sha, expected_sha=source_sha, target_mode=args.target_mode)
    if args.target_mode == "live":
        if not args.target_url or not args.target_url.startswith("https://"):
            raise ValueError("live canary target must be an HTTPS URL")
        if not args.manifest:
            raise ValueError("live canary requires an approved manifest JSON")
        cv_path = Path(args.cv_path).resolve() if args.cv_path else None
        if not cv_path or not cv_path.exists():
            raise FileNotFoundError("live canary requires an approved CV asset on the runner")
        target_url = args.target_url
    else:
        cv_path = Path(args.cv_path or FIXTURE_CV).resolve()
        target = Path(args.fixture or FIXTURE).resolve()
        if not target.exists():
            raise FileNotFoundError("canary fixture is missing")
        target_url = _url(target)
    if not cv_path.exists():
        raise FileNotFoundError("canary CV asset is missing")
    config = BrowserRuntimeConfig(executable_path=args.executable_path or (shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome") or ""), navigation_timeout_ms=10_000, settle_timeout_ms=50)
    inspector = PlaywrightBrowserWorker(config)
    observed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    inspected = inspector.inspect(BrowserInspectionRequest(
        bridge_request_id=f"bridge:gd004:canary:{args.run_id}", route_key="route:gd004:safe-fill-upload-canary",
        opportunity_key="fixture:gd004:safe-fill-upload" if args.target_mode == "fixture" else "live:gd004:safe-fill-upload",
        requisition_id="gd004-canary-001", application_url=target_url, adapter_key="ats:smartrecruiters", observed_at=observed_at,
    ))
    if args.target_mode == "live":
        request = _manifest_request(Path(args.manifest).resolve(), cv_path=cv_path, target_url=target_url, source_sha=source_sha, run_id=args.run_id, inspected_fp=inspected.form_fingerprint)
    else:
        fields, uploads, resolutions = _fixture_plans(cv_path)
        request = SmartRecruitersExecutionRequest(
            execution_id=f"execution:gd004:safe-fill-upload:{args.run_id}", bridge_request_id=f"bridge:gd004:canary:{args.run_id}",
            route_key="route:gd004:safe-fill-upload-canary", opportunity_key="fixture:gd004:safe-fill-upload", requisition_id="gd004-canary-001",
            application_url=target_url, observed_at=observed_at, expected_form_fingerprint=inspected.form_fingerprint,
            candidate_profile_version="canary-profile-v1", application_status="To Apply", readiness_confidence="High", unresolved_required=0,
            field_plan=fields, upload_plan=uploads, control_resolutions=resolutions, rollout_stage=RolloutStage.R1_PREFILL,
        )
    result = PlaywrightSmartRecruitersExecutor(config, artifact_repository=InMemoryTmhRepository()).execute(request, authority=SmartRecruitersExecutionAuthority())
    output = {
        "source_sha": source_sha, "target_mode": args.target_mode, "runtime_state": result.runtime_state.value,
        "form_value_write_attempts": result.form_value_write_attempts, "file_upload_attempts": result.file_upload_attempts,
        "submit_attempts": result.submit_attempts, "observed_form_fingerprint": result.observed_form_fingerprint,
        "final_form_fingerprint": result.final_form_fingerprint, "review_gate_reached": result.review_gate_reached,
        "approved_asset": all(item.expected_mime_type == "application/pdf" for item in result.file_results),
        "error_code": result.error_code, "evidence": "safe-fill and one approved CV upload; final submit disabled",
    }
    print(json.dumps(output, sort_keys=True))
    return 0 if result.runtime_state.value == "pre_submit_ready" and result.submit_attempts == 0 and result.file_upload_attempts == 1 else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="TEST safe-fill + CV upload canary; submit is structurally disabled")
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--approval-phrase", default="")
    parser.add_argument("--target-mode", choices=("fixture", "live"), default="fixture")
    parser.add_argument("--target-url", default="")
    parser.add_argument("--manifest", default="", help="Approved live target manifest JSON")
    parser.add_argument("--fixture", default="")
    parser.add_argument("--cv-path", default="")
    parser.add_argument("--executable-path", default="")
    parser.add_argument("--run-id", default="manual")
    return run(parser.parse_args())


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"GD004_CANARY_ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
