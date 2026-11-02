"""GD-005 TEST-only advisory routing. No I/O or execution authority.

Inputs are manually verified, synthetic metadata, never raw task content or secrets.
Provider declarations and approval references are caller attestations, not credentials.
Existing runtime gates must independently authorize any subsequent execution.
"""
from dataclasses import dataclass
import hashlib
import json
import re


@dataclass(frozen=True)
class Provider:
    name: str
    available: bool
    capabilities: frozenset[str]
    network: bool = False

    def __post_init__(self):
        object.__setattr__(self, "capabilities", frozenset(self.capabilities))


@dataclass(frozen=True)
class Request:
    task_id: str = ""
    environment: str = ""
    approval_ref: str = ""
    risk_level: str = ""
    required_capabilities: frozenset[str] = frozenset()
    quota_band: str = ""
    network_requirement: str = ""
    source_sha: str = ""
    test_evidence: tuple[str, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "required_capabilities", frozenset(self.required_capabilities))
        object.__setattr__(self, "test_evidence", tuple(self.test_evidence))


@dataclass(frozen=True)
class Decision:
    provider: str
    fallback_provider: str | None
    stop_reason: str | None
    checkpoint_ref: str
    # Empty even for successful selection: routing never grants permission.
    permitted_actions: tuple[str, ...] = ()
    final_submit: bool = False


_ALLOWED = frozenset({"documentation", "review", "design", "python_tests", "fixture_browser"})
_NAMES = frozenset({"work", "codex", "github_ci", "local_macbook"})
_ID = re.compile(r"[A-Za-z0-9_.:-]{1,80}\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z")


def quota_band(remaining_percent: int) -> str:
    """Manual policy thresholds; no account telemetry or official limit claim."""
    if type(remaining_percent) is not int or not 0 <= remaining_percent <= 100:
        raise ValueError("remaining_percent must be an integer in 0..100")
    return ("exhausted" if remaining_percent == 0 else "low" if remaining_percent < 20
            else "economical" if remaining_percent < 50 else "normal")


def route(request: Request, providers: tuple[Provider, ...] = ()) -> Decision:
    """Pure selection; invalid or unverified metadata always requires human review.

    No retry counter, timestamps, provider calls, persistence or mutable defaults.
    The checkpoint commits to the complete routing input and decision, including
    evidence, availability and capabilities; it is not proof tests actually passed.
    """
    providers = tuple(providers)
    reason = None
    if request.environment != "TEST":
        reason = "explicit_test_environment_required"
    elif not _ID.fullmatch(request.task_id) or not _ID.fullmatch(request.approval_ref):
        reason = "task_and_approval_reference_required"
    elif request.risk_level not in {"low", "medium", "high"}:
        reason = "unsupported_risk"
    elif request.quota_band not in {"normal", "economical", "low", "exhausted"}:
        reason = "unknown_quota"
    elif request.network_requirement not in {"offline", "required"}:
        reason = "unknown_network_requirement"
    elif not request.required_capabilities or not request.required_capabilities <= _ALLOWED:
        reason = "unsupported_capability_or_interactive_auth"
    elif not _SHA.fullmatch(request.source_sha) or any(
        not _ID.fullmatch(e) for e in request.test_evidence
    ):
        reason = "invalid_checkpoint_metadata"
    elif (len({p.name for p in providers}) != len(providers)
          or any(p.name not in _NAMES or type(p.available) is not bool
                 or type(p.network) is not bool or not p.capabilities <= _ALLOWED
                 for p in providers)):
        reason = "unverified_provider_configuration"

    candidates = []
    if reason is None:
        # Economical mode reserves models; low/exhausted uses deterministic only.
        order = ("work", "codex", "github_ci", "local_macbook") if (
            request.quota_band == "normal" and request.risk_level == "high"
            and request.required_capabilities <= {"design", "review", "documentation"}
        ) else ("github_ci", "local_macbook")
        by_name = {p.name: p for p in providers}
        candidates = [name for name in order if name in by_name
                      and by_name[name].available
                      and request.required_capabilities <= by_name[name].capabilities
                      and (request.network_requirement == "offline" or by_name[name].network)]
        if not candidates:
            reason = "no_verified_provider"
    selected = candidates[0] if candidates else "human_review"
    fallback = candidates[1] if len(candidates) > 1 else None
    # Hash only: never echo caller-provided metadata into logs or decision fields.
    payload = {
        "version": 1, "request": {**vars(request),
            "required_capabilities": sorted(request.required_capabilities)},
        "providers": sorted(({**vars(p), "capabilities": sorted(p.capabilities)}
                             for p in providers), key=lambda p: p["name"]),
        "provider": selected, "fallback": fallback, "stop_reason": reason,
        "permitted_actions": [], "final_submit": False,
    }
    checkpoint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return Decision(selected, fallback, reason, "sha256:" + checkpoint)
