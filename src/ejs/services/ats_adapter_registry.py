from __future__ import annotations

from urllib.parse import urlparse

from ejs.contracts.ats_adapter import (
    AdapterCapabilityManifest,
    AdapterDispatchDecision,
    AdapterImplementationState,
    AtsFamily,
    DispatchState,
    HumanReviewItem,
    HumanReviewReason,
    HumanReviewStage,
    LAUNCH_ATS_FAMILIES,
    SessionRequirement,
)

REGISTRY_VERSION = "ATS-REGISTRY-1.0"

_MANIFESTS: dict[AtsFamily, AdapterCapabilityManifest] = {
    AtsFamily.ADP: AdapterCapabilityManifest(
        family=AtsFamily.ADP,
        adapter_key="ats:adp",
        adapter_version="adp-live-canary-v1",
        implementation_state=AdapterImplementationState.LIVE_CANARY,
        inspection_supported=True,
        safe_fill_supported=True,
        document_upload_supported=False,
        multi_step_supported=False,
        conditional_submit_supported=False,
        confirmation_supported=False,
    ),
    AtsFamily.SMARTRECRUITERS: AdapterCapabilityManifest(
        family=AtsFamily.SMARTRECRUITERS,
        adapter_key="ats:smartrecruiters",
        adapter_version="SMARTRECRUITERS-EXEC-0.1",
        implementation_state=AdapterImplementationState.PREPARE,
        inspection_supported=True,
        safe_fill_supported=True,
        document_upload_supported=True,
        multi_step_supported=True,
        conditional_submit_supported=False,
        confirmation_supported=False,
    ),
    AtsFamily.LINKEDIN_EASY_APPLY: AdapterCapabilityManifest(
        family=AtsFamily.LINKEDIN_EASY_APPLY,
        adapter_key="ats:linkedin_easy_apply",
        adapter_version="planned",
        implementation_state=AdapterImplementationState.PLANNED,
        inspection_supported=False,
        safe_fill_supported=False,
        document_upload_supported=False,
        multi_step_supported=False,
        conditional_submit_supported=False,
        confirmation_supported=False,
        session_requirement=SessionRequirement.REQUIRED,
    ),
    AtsFamily.WORKDAY: AdapterCapabilityManifest(
        family=AtsFamily.WORKDAY,
        adapter_key="ats:workday",
        adapter_version="planned",
        implementation_state=AdapterImplementationState.PLANNED,
        inspection_supported=False,
        safe_fill_supported=False,
        document_upload_supported=False,
        multi_step_supported=False,
        conditional_submit_supported=False,
        confirmation_supported=False,
        session_requirement=SessionRequirement.OPTIONAL,
    ),
    AtsFamily.GREENHOUSE: AdapterCapabilityManifest(
        family=AtsFamily.GREENHOUSE,
        adapter_key="ats:greenhouse",
        adapter_version="planned",
        implementation_state=AdapterImplementationState.PLANNED,
        inspection_supported=False,
        safe_fill_supported=False,
        document_upload_supported=False,
        multi_step_supported=False,
        conditional_submit_supported=False,
        confirmation_supported=False,
    ),
    AtsFamily.LEVER: AdapterCapabilityManifest(
        family=AtsFamily.LEVER,
        adapter_key="ats:lever",
        adapter_version="planned",
        implementation_state=AdapterImplementationState.PLANNED,
        inspection_supported=False,
        safe_fill_supported=False,
        document_upload_supported=False,
        multi_step_supported=False,
        conditional_submit_supported=False,
        confirmation_supported=False,
    ),
}

_HOST_RULES: tuple[tuple[AtsFamily, tuple[str, ...]], ...] = (
    (AtsFamily.SMARTRECRUITERS, ("smartrecruiters.com",)),
    (AtsFamily.WORKDAY, ("myworkdayjobs.com", "myworkdaysite.com")),
    (AtsFamily.GREENHOUSE, ("greenhouse.io",)),
    (AtsFamily.LEVER, ("lever.co",)),
    (AtsFamily.ADP, ("workforcenow.adp.com",)),
)


def all_launch_manifests() -> tuple[AdapterCapabilityManifest, ...]:
    manifests = tuple(_MANIFESTS[family] for family in LAUNCH_ATS_FAMILIES)
    for manifest in manifests:
        manifest.validate()
    return manifests


def manifest_for_family(family: AtsFamily) -> AdapterCapabilityManifest:
    if family is AtsFamily.UNSUPPORTED:
        raise KeyError("unsupported ATS has no launch manifest")
    manifest = _MANIFESTS[family]
    manifest.validate()
    return manifest


def _host_matches(host: str, suffix: str) -> bool:
    normalized = host.lower().rstrip(".")
    suffix = suffix.lower().rstrip(".")
    return normalized == suffix or normalized.endswith("." + suffix)


def _known_family_for_host(host: str) -> AtsFamily | None:
    for family, suffixes in _HOST_RULES:
        if any(_host_matches(host, suffix) for suffix in suffixes):
            return family
    return None


def _decision_for_family(family: AtsFamily) -> AdapterDispatchDecision:
    manifest = manifest_for_family(family)
    if not manifest.inspection_supported:
        decision = AdapterDispatchDecision(
            state=DispatchState.HUMAN_REVIEW,
            family=family,
            reason_code="ADAPTER_NOT_READY",
            adapter_key=manifest.adapter_key,
            adapter_version=manifest.adapter_version,
            implementation_state=manifest.implementation_state,
        )
    else:
        decision = AdapterDispatchDecision(
            state=DispatchState.DISPATCH,
            family=family,
            reason_code="ATS_ADAPTER_IDENTIFIED",
            adapter_key=manifest.adapter_key,
            adapter_version=manifest.adapter_version,
            implementation_state=manifest.implementation_state,
        )
    decision.validate()
    return decision


def classify_application_target(
    url: str,
    *,
    linkedin_resolution_state: str = "",
) -> AdapterDispatchDecision:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("application target must be an absolute HTTPS URL")
    host = parsed.hostname.lower().rstrip(".")

    if _host_matches(host, "linkedin.com"):
        if linkedin_resolution_state == "linkedin_easy_apply":
            return _decision_for_family(AtsFamily.LINKEDIN_EASY_APPLY)
        decision = AdapterDispatchDecision(
            state=DispatchState.HUMAN_REVIEW,
            family=AtsFamily.LINKEDIN_EASY_APPLY,
            reason_code="LINKEDIN_ROUTE_REQUIRES_SOURCE_RESOLUTION",
            adapter_key="ats:linkedin_easy_apply",
            adapter_version=_MANIFESTS[AtsFamily.LINKEDIN_EASY_APPLY].adapter_version,
            implementation_state=_MANIFESTS[AtsFamily.LINKEDIN_EASY_APPLY].implementation_state,
        )
        decision.validate()
        return decision

    family = _known_family_for_host(host)
    if family is not None:
        return _decision_for_family(family)

    decision = AdapterDispatchDecision(
        state=DispatchState.UNSUPPORTED,
        family=AtsFamily.UNSUPPORTED,
        reason_code="UNSUPPORTED_ATS_HOST",
    )
    decision.validate()
    return decision


def human_review_from_dispatch(
    *,
    execution_id: str,
    opportunity_key: str,
    decision: AdapterDispatchDecision,
    evidence_ref: str = "",
) -> HumanReviewItem:
    decision.validate()
    if decision.state is DispatchState.DISPATCH:
        raise ValueError("dispatch-ready decision does not require human review")

    if decision.state is DispatchState.UNSUPPORTED:
        reason = HumanReviewReason.UNSUPPORTED_ATS
    elif decision.reason_code == "ADAPTER_NOT_READY":
        reason = HumanReviewReason.ADAPTER_NOT_READY
    else:
        reason = HumanReviewReason.ROUTE_UNRESOLVED

    item = HumanReviewItem(
        execution_id=execution_id,
        opportunity_key=opportunity_key,
        stage=HumanReviewStage.ROUTING,
        reason=reason,
        ats_family=decision.family,
        detail_code=decision.reason_code,
        evidence_ref=evidence_ref,
        retryable=reason in {
            HumanReviewReason.ADAPTER_NOT_READY,
            HumanReviewReason.ROUTE_UNRESOLVED,
        },
    )
    item.validate()
    return item
