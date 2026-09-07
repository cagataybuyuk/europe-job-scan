from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from urllib.parse import urlparse

from ejs.contracts.browser import (
    AuthBoundaryType,
    CaptchaState,
    DomSnapshotState,
    RuntimeState,
)
from ejs.domain.template_history import ObservedControl, RuntimeFormObservation


@dataclass(frozen=True)
class BrowserObservedControl:
    control_key: str
    tag_name: str
    control_type: str
    label: str
    name: str = ""
    element_id: str = ""
    required: bool = False
    required_evidence: str = "none"
    visible: bool = True
    disabled: bool = False
    options: tuple[str, ...] = field(default_factory=tuple)
    autocomplete: str = ""
    aria_label: str = ""
    placeholder: str = ""
    max_length: int = -1
    accept: str = ""
    multiple: bool = False

    def to_observed_control(self) -> ObservedControl:
        return ObservedControl(
            label=self.label or self.name or self.element_id or self.control_key,
            control_type=self.control_type,
            required=self.required,
            step=1,
            options=self.options,
            semantic_hint=f"{self.autocomplete or self.name}|maxlength={self.max_length}|accept={self.accept}|multiple={self.multiple}",
        )


@dataclass(frozen=True)
class BrowserActionControl:
    action_key: str
    tag_name: str
    control_type: str
    label: str
    disabled: bool = False
    probable_action: str = "unknown"


@dataclass(frozen=True)
class BrowserInspectionResult:
    bridge_request_id: str
    route_key: str
    opportunity_key: str
    requisition_id: str
    requested_url: str
    final_url: str
    adapter_key: str
    ats_family: str
    runtime_state: RuntimeState
    page_title: str
    page_fingerprint: str
    form_fingerprint: str
    dom_snapshot_state: DomSnapshotState
    auth_boundary_type: AuthBoundaryType
    captcha_state: CaptchaState
    controls: tuple[BrowserObservedControl, ...]
    action_controls: tuple[BrowserActionControl, ...]
    navigation_trace: tuple[str, ...]
    observed_at: str
    browser_engine: str
    browser_version: str
    error_code: str = ""
    error_message: str = ""
    mutation_attempts: int = 0
    file_upload_attempts: int = 0
    submit_attempts: int = 0

    @property
    def read_only_invariant_ok(self) -> bool:
        return (
            self.mutation_attempts == 0
            and self.file_upload_attempts == 0
            and self.submit_attempts == 0
        )


def detect_ats_family(url: str, adapter_key: str = "") -> str:
    key = adapter_key.strip().lower()
    if key.startswith("ats:"):
        return key.split(":", 1)[1]
    host = (urlparse(url).hostname or "").lower()
    if "myworkdayjobs.com" in host or "workdayjobs.com" in host:
        return "workday"
    if host == "jobs.smartrecruiters.com" or host.endswith(".smartrecruiters.com"):
        return "smartrecruiters"
    if "greenhouse.io" in host:
        return "greenhouse"
    if "hirehive.com" in host:
        return "hirehive"
    if host in {"127.0.0.1", "localhost"}:
        return "local-fixture"
    return "generic-form"


def page_structure_fingerprint(
    *,
    ats_family: str,
    title: str,
    controls: tuple[BrowserObservedControl, ...],
    actions: tuple[BrowserActionControl, ...],
) -> str:
    payload = {
        "ats_family": ats_family,
        "title": title.strip().lower(),
        "controls": [
            {
                "tag": c.tag_name,
                "type": c.control_type,
                "label": c.label.strip().lower(),
                "required": c.required,
                "options": [o.strip().lower() for o in c.options],
            }
            for c in controls
        ],
        "actions": [
            {
                "tag": a.tag_name,
                "type": a.control_type,
                "label": a.label.strip().lower(),
                "probable_action": a.probable_action,
            }
            for a in actions
        ],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def runtime_form_fingerprint(
    *,
    opportunity_key: str,
    requisition_id: str,
    apply_url: str,
    ats_family: str,
    observed_at: str,
    controls: tuple[BrowserObservedControl, ...],
) -> str:
    observation = RuntimeFormObservation(
        opportunity_key=opportunity_key,
        requisition_id=requisition_id,
        apply_url=apply_url,
        ats_family=ats_family,
        observed_at=observed_at,
        controls=tuple(c.to_observed_control() for c in controls),
        step_count=1,
    )
    return observation.fingerprint
