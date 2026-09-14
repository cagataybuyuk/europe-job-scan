"""Offline-only select and wizard-step adapter prototype.

It deliberately accepts a Playwright page only after the caller has loaded the
bundled synthetic fixture. There is no live URL, upload, submit, or final-step
authority in this module.
"""
from __future__ import annotations

from dataclasses import dataclass

from ejs.services.smartrecruiters_shadow import inspect_shadow_form


@dataclass(frozen=True)
class FixtureSelectPlan:
    host_ids: tuple[str, ...]
    select_id: str
    expected_label: str
    expected_option: str


@dataclass(frozen=True)
class FixtureNextPlan:
    host_ids: tuple[str, ...]
    button_id: str
    expected_label: str = "Next"
    expected_next_schema: str = ""


def _scope(page, host_ids):
    scope = page
    for host_id in host_ids:
        scope = scope.locator(f'[id="{host_id}"]')
        if scope.count() != 1 or not scope.evaluate('(el) => !!el.shadowRoot'):
            raise ValueError("HOST_NOT_UNIQUE_OR_OPEN")
    return scope


def select_fixture_option(page, plan: FixtureSelectPlan, expected_schema: str) -> dict:
    """Select one exact native option, then read it back and recheck schema."""
    result = {"runtime_state": "blocked", "error_code": "", "select_attempts": 0,
              "verified_selections": 0, "next_attempts": 0, "submit_attempts": 0,
              "file_upload_attempts": 0, "live_execution_ready": False}
    try:
        if page.url != "about:blank":
            raise ValueError("FIXTURE_ONLY")
        if inspect_shadow_form(page)["schema_fingerprint"] != expected_schema:
            raise ValueError("SCHEMA_DRIFT")
        loc = _scope(page, plan.host_ids).locator(f'[id="{plan.select_id}"]')
        if loc.count() != 1:
            raise ValueError("CONTROL_NOT_UNIQUE")
        meta = loc.evaluate('''el => ({tag:el.tagName.toLowerCase(), label:el.getAttribute('aria-label') || Array.from(el.labels || []).map(n=>n.textContent).join(' '), options:Array.from(el.options).map(o=>o.textContent.trim()), disabled:el.disabled})''')
        if meta["tag"] != "select":
            raise ValueError("CONTROL_TYPE_DRIFT")
        if " ".join(meta["label"].split()) != plan.expected_label:
            raise ValueError("CONTROL_LABEL_DRIFT")
        if meta["disabled"]:
            raise ValueError("CONTROL_UNAVAILABLE")
        if plan.expected_option not in meta["options"]:
            raise ValueError("OPTION_NOT_OBSERVED")
        if loc.input_value() != plan.expected_option:
            result["select_attempts"] = 1
            loc.select_option(label=plan.expected_option)
        if loc.input_value() != plan.expected_option:
            raise ValueError("READBACK_MISMATCH")
        if inspect_shadow_form(page)["schema_fingerprint"] != expected_schema:
            raise ValueError("POST_SELECT_SCHEMA_DRIFT")
        result.update(runtime_state="fixture_select_verified", verified_selections=1)
    except ValueError as exc:
        result["error_code"] = str(exc)
    except Exception:
        result["error_code"] = "FIXTURE_SELECT_ERROR"
    return result


def advance_fixture_next(page, plan: FixtureNextPlan, expected_schema: str, next_schema: str) -> dict:
    """Advance once only when a unique exact Next button is observed.

    This helper is intentionally not called by the live executor. It verifies
    navigation, the new schema, and that no submit/upload occurred.
    """
    result = {"runtime_state": "blocked", "error_code": "", "next_attempts": 0,
              "verified_next": False, "submit_attempts": 0, "file_upload_attempts": 0,
              "live_execution_ready": False}
    try:
        if page.url != "about:blank":
            raise ValueError("FIXTURE_ONLY")
        if inspect_shadow_form(page)["schema_fingerprint"] != expected_schema:
            raise ValueError("SCHEMA_DRIFT")
        button = _scope(page, plan.host_ids).locator(f'[id="{plan.button_id}"]')
        if button.count() != 1:
            raise ValueError("NEXT_NOT_UNIQUE")
        meta = button.evaluate('''el => ({tag:el.tagName.toLowerCase(), label:(el.innerText || el.textContent || '').trim(), disabled:el.disabled})''')
        if meta["tag"] != "button" or meta["label"] != plan.expected_label:
            raise ValueError("NEXT_LABEL_DRIFT")
        if meta["disabled"]:
            raise ValueError("NEXT_UNAVAILABLE")
        result["next_attempts"] = 1
        button.click()
        if inspect_shadow_form(page)["schema_fingerprint"] != next_schema:
            raise ValueError("NEXT_SCHEMA_DRIFT")
        result.update(runtime_state="fixture_next_verified", verified_next=True)
    except ValueError as exc:
        result["error_code"] = str(exc)
    except Exception:
        result["error_code"] = "FIXTURE_NEXT_ERROR"
    return result
