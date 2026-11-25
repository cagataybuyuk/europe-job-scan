"""Sequential, inspection-only SmartRecruiters application preparation."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError

from ejs.persistence.policy_submission_ledger import requisition_hashes
from ejs.services.application_bridge import (
    MAX_DETAIL_REQUESTS, observe_posting_detail, prepare_application_report,
)
from ejs.services.execution_manifest import CONTROLS, MAX_OBSERVATION_AGE, POLICY_VERSION
from ejs.services.smartrecruiters_live_inspector import LiveInspectionRequest
from ejs.services.smartrecruiters_live_route import route_live_inspection

SCHEMA = "EJS-APPLICATION-BATCH-1"
_INSPECTOR_ERRORS = {
    "AUTH_BOUNDARY", "CAPTCHA_BOUNDARY", "CHROMIUM_NOT_FOUND", "FORM_STRUCTURE_NOT_DISCOVERED",
    "FRAME_OBSERVATION_INCOMPLETE", "JOB_EXPIRED", "KNOWN_CHALLENGE_DESTINATION_DENIED",
    "NETWORK_FENCE_BLOCKED", "PLAYWRIGHT_UNAVAILABLE", "POSTING_NAVIGATION_FAILED",
    "WEBSOCKET_FENCE_UNAVAILABLE", "ENTRY_NAVIGATION_FAILED", "BROWSER_LAUNCH_FAILED",
}
_DENIALS = {
    "NON_GET_REQUEST", "UNTRUSTED_NAVIGATION", "UNSUPPORTED_EXTERNAL_RESOURCE",
    "INVALID_REQUEST_URL", "HTTP_ACCESS_OR_RATE_BOUNDARY", "HTTP_RESPONSE_BOUNDARY",
    "READ_TIMEOUT", "READ_TRANSPORT_FAILED", "REDIRECT_UNREVIEWED", "WEBSOCKET_UNSUPPORTED",
    "READ_BUDGET_EXHAUSTED",
}
_REVIEW_BOUNDARIES = {"AUTH_BOUNDARY", "CAPTCHA_BOUNDARY", "KNOWN_CHALLENGE_DESTINATION_DENIED"}
_RUNTIME_ERRORS = {"CHROMIUM_NOT_FOUND", "PLAYWRIGHT_UNAVAILABLE", "WEBSOCKET_FENCE_UNAVAILABLE",
                   "BROWSER_LAUNCH_FAILED"}
# An optional external resource denial is diagnostic; every other denial
# prevents positive form evidence. This does not grant any network permission.
_BLOCKING_DENIALS = _DENIALS - {"UNSUPPORTED_EXTERNAL_RESOURCE"}


def _observation_expiry(result: dict) -> datetime:
    stamp = datetime.fromisoformat(result["manifest_inputs"]["observation_valid_until"])
    if stamp.tzinfo is None or stamp.utcoffset() != timedelta(0):
        raise ValueError("batch_observation_time")
    return stamp


def _validate_state(state: dict) -> None:
    if (not isinstance(state, dict) or state.get("schema") != SCHEMA
            or set(state) != set(new_state())
            or any(not isinstance(state[k], dict) for k in ("results", "cooldowns", "access_holds"))):
        raise ValueError("batch_state_schema")
    for entry in state["results"].values():
        if (not isinstance(entry, dict) or type(entry.get("attempt_count")) is not int
                or entry["attempt_count"] < 1 or type(entry.get("retry_eligible")) is not bool
                or not isinstance(entry.get("tenant"), str) or entry.get("outcome") not in {"failure", "form_observed"}):
            raise ValueError("batch_result_state")
        if entry["retry_eligible"]:
            stamp = datetime.fromisoformat(entry["next_eligible_run"])
            if stamp.tzinfo is None or stamp.utcoffset() != timedelta(0):
                raise ValueError("batch_retry_time")
        elif entry.get("next_eligible_run") is not None:
            raise ValueError("batch_retry_state")
        if entry["outcome"] == "form_observed":
            _observation_expiry(entry)
            if not isinstance(entry.get("requisition_id"), str) or not entry["requisition_id"]:
                raise ValueError("batch_requisition_state")
    for key, value in state["cooldowns"].items():
        stamp = datetime.fromisoformat(value)
        if key not in {"detail", "inspect"} or stamp.tzinfo is None or stamp.utcoffset() != timedelta(0):
            raise ValueError("batch_cooldown_state")
    if any(not isinstance(value, str) or not value for value in state["access_holds"].values()):
        raise ValueError("batch_access_hold_state")


def _positive_observation(target: dict, inspection: dict, fence: dict) -> bool:
    denied = set(fence.get("blocked_request_types", ()))
    return (inspection.get("requested_url") == target["url"]
            and inspection.get("http_status") is None
            and inspection.get("error_code") == ""
            and inspection.get("captcha_observed") is False
            and fence.get("challenge_destination_denied") is False
            and denied <= _DENIALS and not denied & _BLOCKING_DENIALS
            and route_live_inspection(inspection)["manifest_review_allowed"] is True
            and any(control.get("visible") is True and control.get("disabled") is not True
                    for control in inspection["form"]["controls"]))


def new_state() -> dict:
    return {"schema": SCHEMA, "results": {}, "cooldowns": {}, "access_holds": {}}


def _retry_after(value: str | None, now: datetime) -> datetime:
    try:
        if value and value.isascii() and value.isdecimal():
            return now + timedelta(seconds=int(value))
        if value:
            parsed = parsedate_to_datetime(value)
            return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)
    except OverflowError:
        return datetime.max.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        pass
    return now


def _pending(until: str | None, now: datetime) -> bool:
    return until is not None and datetime.fromisoformat(until) > now


def _event(candidate: dict, now: datetime, attempt: int, stage: str, code: str, *,
           technical_reason: str | None = None, denial_types=(), challenge_destination_denied=False,
           retry_at: datetime | None = None, outcome="failure") -> dict:
    return {
        "ats": candidate["ats_kind"], "employer": candidate["company"], "tenant": candidate["tenant"],
        "posting_id": candidate["posting_id"], "requisition_id": candidate["requisition_id"],
        "stage": stage, "error_code": code, "technical_reason": technical_reason or code,
        "timestamp": now.isoformat(), "attempt_count": attempt, "outcome": outcome,
        "retry_eligible": retry_at is not None, "next_eligible_run": retry_at.isoformat() if retry_at else None,
        "network_denial_types": sorted(_DENIALS.intersection(denial_types)),
        "known_challenge_destination_denied": challenge_destination_denied,
    }


def _manifest_inputs(candidate: dict, inspection: dict, observed_at: datetime) -> dict:
    fields = []
    upload_controls = []
    for control in inspection["form"]["controls"]:
        if control.get("visible") is not True or control.get("disabled") is True:
            continue
        kind = control.get("type", "")
        observation = {
            "observation_key": control["observation_key"], "label": control.get("label", ""),
            "observed_type": kind, "required": control.get("required") is True,
        }
        if kind == "file":
            upload_controls.append({**observation, "accept": control.get("accept", ""),
                                    "multiple": control.get("multiple") is True})
        else:
            missing = ["key", "canonical_field", "answer.mode", "answer.value", "answer.source_ref"]
            if kind in {"select", "select-one", "select-multiple", "radio"}:
                missing.append("options")
            if not observation["label"]:
                missing.append("label")
            fields.append({**observation, "remaining_inputs": missing,
                           "control_requires_review": kind not in CONTROLS})
    return {
        "target": {"ats_family": candidate["ats_kind"], "company": candidate["tenant"],
                   "posting_id": candidate["posting_id"], "requisition_id": candidate["requisition_id"],
                   "apply_url": candidate["url"]},
        "source_detail_sha256": candidate["detail_sha256"],
        "observation_schema_fingerprint": inspection["form"]["schema_fingerprint"],
        "observed_at": observed_at.isoformat(),
        "observation_valid_until": (observed_at + MAX_OBSERVATION_AGE).isoformat(),
        "observed_fields": fields,
        "observed_upload_controls": upload_controls,
        "required_cv_upload_binding_observed": any(control["required"] for control in upload_controls),
        "additional_steps_possible": inspection.get("step_boundary", {}).get("additional_steps_possible") is True,
        "remaining_inputs": {
            "target": ["opportunity_key"],
            "execution": ["execution_id", "prepared_by_run", "prepared_at", "expires_at",
                          "candidate_profile_version", "submit_key"],
            "policy": {"version": POLICY_VERSION, "remaining": ["rollout_stage", "daily_cap"]},
            "form": ["reviewed fields and step indices", "unique required cv_upload key, label and step",
                     "fingerprint computed from the reviewed inventory"],
            "cv": ["library_name", "filename", "sha256", "size_bytes", "mime_type"],
            "review": ["candidate eligibility", "answer provenance", "canonical duplicate binding",
                       "execution manifest validation and independent review"],
        },
    }


def run_application_batch(discovery: dict, state: dict, *, budget: int, detail, inspect,
                          consumed_requisitions, checkpoint, excluded_postings=(),
                          excluded_requisitions=(), clock=lambda: datetime.now(timezone.utc)) -> dict:
    """Process at most budget eligible vacancies and checkpoint every attempt."""
    if type(budget) is not int or not 1 <= budget <= MAX_DETAIL_REQUESTS:
        raise ValueError("batch_budget")
    _validate_state(state)
    preparation = prepare_application_report(discovery)
    results = state["results"]
    cooldowns = state["cooldowns"]
    holds = state["access_holds"]
    # Track a successful observation only while fresh; a failed observation is
    # not a permanent duplicate reservation. No form observation is submission.
    observed = {}
    for result in results.values():
        if result["outcome"] == "form_observed" and _observation_expiry(result) > clock():
            per_tenant = observed.setdefault(result["tenant"].casefold(), {})
            for digest in requisition_hashes(result["requisition_id"]):
                per_tenant[digest] = max(per_tenant.get(digest, _observation_expiry(result)),
                                         _observation_expiry(result))
    attempted_requisitions = {}

    events = []
    skipped = Counter()
    attempts = 0
    last_runtime_error = None
    for target in preparation["candidates"]:
        tenant = target["tenant"].casefold()
        posting = target["posting_id"]
        key = ":".join((target["ats_kind"], target["region"], tenant, posting or ""))
        if target["reason"] != "detail_not_observed":
            events.append(_event(target, clock(), 0, "discovery", target["reason"].upper()))
            continue
        if (tenant, posting) in excluded_postings:
            skipped["private_exclusion"] += 1
            continue
        if attempts >= budget:
            skipped["batch_budget"] += 1
            continue
        previous = results.get(key)
        now = clock()
        if previous:
            if previous["outcome"] == "form_observed":
                if _observation_expiry(previous) > now:
                    skipped["fresh_form_observation"] += 1
                    continue
            elif not previous["retry_eligible"] or _pending(previous["next_eligible_run"], now):
                skipped["already_processed" if not previous["retry_eligible"] else "retry_backoff"] += 1
                continue
        if tenant in holds or _pending(cooldowns.get("detail"), now):
            skipped["employer_access_hold" if tenant in holds else "detail_rate_limit"] += 1
            continue

        attempt = (previous["attempt_count"] if previous else 0) + 1
        attempts += 1
        # A crash after a read must not cause an immediate repeat on restart.
        results[key] = _event(target, now, attempt, "detail", "ATTEMPT_INTERRUPTED",
                              retry_at=now + timedelta(minutes=15))
        checkpoint(state)

        stage = "detail"
        status = None
        retry_after = None
        fence = {}
        duplicate_until = None
        try:
            observe_posting_detail(target, detail)
        except HTTPError as exc:
            status = exc.code
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            exc.close()
            code = "DETAIL_HTTP_RESPONSE"
        except (URLError, OSError):
            code = "DETAIL_TRANSPORT_FAILED"
        except Exception:
            code = "DETAIL_ADAPTER_FAILED"
        else:
            code = target["reason"].upper()
            if target["readiness"] == "ready_for_form_observation":
                req = requisition_hashes(target["requisition_id"])
                if any((tenant, digest) in excluded_requisitions for digest in req):
                    # Keep owner-supplied exclusions out of persistent result state.
                    if previous is None:
                        del results[key]
                    else:
                        results[key] = previous
                    checkpoint(state)
                    skipped["private_exclusion"] += 1
                    continue
                if req & consumed_requisitions():
                    code = "CANONICAL_REQUISITION_CONSUMED"
                elif any(observed.get(tenant, {}).get(digest, now) > now for digest in req):
                    code = "DUPLICATE_REQUISITION_DEFERRED"
                    duplicate_until = max(observed[tenant][digest] for digest in req if digest in observed[tenant])
                elif req & attempted_requisitions.setdefault(tenant, set()):
                    code = "DUPLICATE_REQUISITION_DEFERRED"
                    duplicate_until = now + timedelta(minutes=15)
                elif _pending(cooldowns.get("inspect"), clock()):
                    code = "INSPECT_RATE_LIMIT_DEFERRED"
                    stage = "inspect"
                elif last_runtime_error:
                    code = last_runtime_error
                    stage = "inspect"
                else:
                    stage = "inspect"
                    attempted_requisitions.setdefault(tenant, set()).update(req)
                    results[key] = _event(target, clock(), attempt, stage, "ATTEMPT_INTERRUPTED",
                                          retry_at=clock() + timedelta(minutes=15))
                    checkpoint(state)
                    result = None
                    try:
                        inspection = inspect(LiveInspectionRequest(target["url"], auto_open_apply=True))
                        status = inspection.get("http_status")
                        if status is not None and (type(status) is not int or not 100 <= status <= 599):
                            status = None
                            raise ValueError("inspection_http_status")
                        retry_after = inspection.get("retry_after")
                        if retry_after is not None and not isinstance(retry_after, str):
                            retry_after = None
                            raise ValueError("inspection_retry_after")
                        candidate_fence = inspection.get("network_fence", {})
                        if (not isinstance(candidate_fence, dict)
                                or not isinstance(candidate_fence.get("blocked_request_types", []), list)
                                or any(not isinstance(item, str) for item in candidate_fence.get("blocked_request_types", []))):
                            raise ValueError("inspection_fence")
                        fence = candidate_fence
                        code = inspection.get("error_code")
                        if _positive_observation(target, inspection, fence):
                            now = clock()
                            result = _event(target, now, attempt, stage, "", outcome="form_observed",
                                            technical_reason="visible_form_structure_observed",
                                            denial_types=fence.get("blocked_request_types", ()))
                            result["manifest_inputs"] = _manifest_inputs(target, inspection, now)
                        elif inspection.get("captcha_observed") is True:
                            code = "CAPTCHA_BOUNDARY"
                        elif fence.get("challenge_destination_denied") is True:
                            code = "KNOWN_CHALLENGE_DESTINATION_DENIED"
                        elif status is not None:
                            code = "INSPECT_HTTP_RESPONSE"
                        elif not code and set(fence.get("blocked_request_types", ())) & _BLOCKING_DENIALS:
                            code = "NETWORK_FENCE_BLOCKED"
                        elif code not in _INSPECTOR_ERRORS:
                            code = "INSPECT_ADAPTER_FAILED"
                    except Exception:
                        result = None
                        code = "INSPECT_ADAPTER_FAILED"
                    if result is not None:
                        for digest in req:
                            observed.setdefault(tenant, {})[digest] = _observation_expiry(result)
                        results[key] = result
                        checkpoint(state)
                        events.append(result)
                        continue
        now = clock()
        denials = fence.get("blocked_request_types", ())
        restricted = status in {401, 403} or code in _REVIEW_BOUNDARIES or fence.get("challenge_destination_denied") is True
        if restricted:
            holds[tenant] = code
        temporary = (not restricted and (status in {408, 425, 429} or
                     type(status) is int and 500 <= status <= 599 or
                     code in {"DETAIL_TRANSPORT_FAILED", "INSPECT_RATE_LIMIT_DEFERRED",
                              "DUPLICATE_REQUISITION_DEFERRED", "FORM_STRUCTURE_NOT_DISCOVERED", *_RUNTIME_ERRORS} or
                     code in {"POSTING_NAVIGATION_FAILED", "ENTRY_NAVIGATION_FAILED", "NETWORK_FENCE_BLOCKED"}
                     and bool({"READ_TIMEOUT", "READ_TRANSPORT_FAILED"}.intersection(denials))
                     and not {"NON_GET_REQUEST", "UNTRUSTED_NAVIGATION", "REDIRECT_UNREVIEWED",
                              "INVALID_REQUEST_URL", "WEBSOCKET_UNSUPPORTED"}.intersection(denials)))
        if code == "FORM_STRUCTURE_NOT_DISCOVERED" and {"NON_GET_REQUEST", "UNTRUSTED_NAVIGATION",
                "REDIRECT_UNREVIEWED", "INVALID_REQUEST_URL", "WEBSOCKET_UNSUPPORTED"}.intersection(denials):
            temporary = False
        retry_at = None
        if temporary:
            retry_at = max(now + timedelta(minutes=min(15 * 2 ** min(attempt - 1, 7), 1440)),
                           _retry_after(retry_after, now))
        if duplicate_until is not None:
            retry_at = max(retry_at, duplicate_until)
        if status == 429 or status == 503 and retry_after:
            # Both original and deferred targets respect the service-wide pause.
            until = max(now + timedelta(minutes=15), _retry_after(retry_after, now))
            cooldowns[stage] = max(cooldowns.get(stage, ""), until.isoformat())
        if code == "INSPECT_RATE_LIMIT_DEFERRED":
            retry_at = max(retry_at, datetime.fromisoformat(cooldowns["inspect"]))
        if code in _RUNTIME_ERRORS:
            last_runtime_error = code

        result = _event(target, now, attempt, stage, code,
                        technical_reason=f"HTTP_{status}" if type(status) is int else None,
                        denial_types=denials, retry_at=retry_at,
                        challenge_destination_denied=fence.get("challenge_destination_denied") is True)
        results[key] = result
        checkpoint(state)
        events.append(result)

    return {"schema": SCHEMA, "inspection_only": True, "submit_authorized": False,
            "attempted_vacancies": attempts, "skipped_counts": dict(skipped), "events": events}
