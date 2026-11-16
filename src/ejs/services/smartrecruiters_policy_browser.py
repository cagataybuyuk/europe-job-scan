"""GD-006 A1 step 4b-2: SmartRecruiters Playwright binding of ``PolicyFormBrowser``.

The browser owns a fresh context (no user cookies). Every request passes the
``PolicyNetworkBoundary``: reads only on reviewed SmartRecruiters hosts, one CV
upload POST to the exact SmartRecruiters attachment path with the approved file
name, and one application POST that is armed only after the runtime committed
``mark_started``. Values, request bodies and CV bytes are never logged.

Control keys are derived from the visible label with ``control_key``. Claude's
morning inspection uses the same rule, so the manifest and the live page agree
without employer-supplied selectors. Duplicate keys on one page fail closed.
DOM discovery reuses the reviewed helpers of ``oneclick_browser``.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
from threading import Lock
from time import monotonic, sleep
import unicodedata
from urllib.parse import parse_qsl, urlsplit

from ejs.services.oneclick_browser import _VALUE, OneClickBrowser
from ejs.services.policy_application_runtime import SubmitObservation, UploadResult

_ORIGIN_HOST = "jobs.smartrecruiters.com"
DEFAULT_READ_HOSTS = frozenset({_ORIGIN_HOST})
_COMPANY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}\Z")
_POSTING = re.compile(r"[0-9]{6,20}\Z")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
_COMPANY_ID = re.compile(r"[a-f0-9]{24}\Z")
_PDF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,150}\.pdf\Z")
_NEXT_LABELS = ("Next", "Continue")
_SUBMIT_LABELS = ("Submit", "Submit application", "Send application")
_CHALLENGE_TEXT = ("verify you are human", "complete the captcha", "verification code", "access denied",
                   "are you a robot")
_KIND_TO_CONTROL = {"text": "text", "email": "email", "tel": "tel", "number": "text", "search": "text",
                    "url": "text", "select": "select", "radio": "radio", "checkbox": "checkbox"}


class BrowserStop(PermissionError):
    """Fixed-code refusal; never carries page or candidate values."""


def _require(condition: object, code: str = "POLICY_APP_SR_SURFACE") -> None:
    if not condition:
        raise BrowserStop(code)


def control_key(label: str) -> str:
    """Deterministic key from a visible label (shared with Claude's inspection).

    NFKC, casefold, every run of non-alphanumerics becomes '-', trimmed, at most
    100 characters. ``"Expected salary (EUR)"`` -> ``"expected-salary-eur"``.
    """
    _require(type(label) is str and label.strip(), "POLICY_APP_SR_LABEL")
    text = unicodedata.normalize("NFKC", label).casefold()
    key = re.sub(r"[^0-9a-z]+", "-", "".join(ch if ch.isascii() else "-" for ch in text)).strip("-")[:100].strip("-")
    _require(key, "POLICY_APP_SR_LABEL")
    return key


class PolicyNetworkBoundary:
    """Reads on reviewed hosts; exactly one CV upload and one application POST."""

    def __init__(self, company: str, posting_id: str, filename: str, *,
                 read_hosts: frozenset[str] = DEFAULT_READ_HOSTS):
        _require(type(company) is str and _COMPANY.fullmatch(company)
                 and type(posting_id) is str and _POSTING.fullmatch(posting_id)
                 and type(filename) is str and _PDF.fullmatch(filename)
                 and isinstance(read_hosts, frozenset) and _ORIGIN_HOST in read_hosts
                 and all(type(h) is str and h.endswith(".smartrecruiters.com") or h == _ORIGIN_HOST
                         for h in read_hosts), "POLICY_APP_SR_BOUNDARY")
        self.company, self.posting_id, self.filename = company, posting_id, filename
        self.read_hosts = read_hosts
        self.publication_id: str | None = None
        self._lock = Lock()
        self._phase = "read"
        self.blocked_writes = 0
        self.uploads = 0
        self.application_attempts = 0
        self.applications_allowed = 0

    # ---- exact URLs ---------------------------------------------------------------------

    def _publication_prefix(self) -> str:
        return f"/oneclick-ui/company/{self.company}/publication/{self.publication_id}"

    def application_url(self) -> str:
        _require(self.publication_id is not None, "POLICY_APP_SR_BOUNDARY")
        return (f"https://{_ORIGIN_HOST}/oneclick-ui/api/company/{self.company}/publication/"
                f"{self.publication_id}/application")

    def entry_url(self) -> str:
        _require(self.publication_id is not None, "POLICY_APP_SR_BOUNDARY")
        return f"https://{_ORIGIN_HOST}{self._publication_prefix()}?dcr_ci={self.company}"

    def set_publication(self, publication_id: str) -> None:
        with self._lock:
            _require(self.publication_id is None and type(publication_id) is str
                     and _UUID.fullmatch(publication_id), "POLICY_APP_SR_BOUNDARY")
            self.publication_id = publication_id

    def is_upload(self, url: str) -> bool:
        parts = urlsplit(url)
        match = re.fullmatch(r"/oneclick-ui/api/company/([a-f0-9]{24})/attachment/resume", parts.path)
        return (parts.scheme == "https" and parts.netloc == _ORIGIN_HOST and match is not None
                and dict(parse_qsl(parts.query, keep_blank_values=True)) == {"filename": self.filename}
                and parts.query == "filename=" + self.filename and not parts.fragment)

    def _navigation_ok(self, url: str) -> bool:
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.netloc != _ORIGIN_HOST or parts.fragment:
            return False
        posting = re.fullmatch(rf"/{re.escape(self.company)}/{self.posting_id}(?:-[A-Za-z0-9_-]{{1,200}})?",
                               parts.path)
        if posting and not parts.query:
            return True
        if self.publication_id is None:
            return False
        prefix = self._publication_prefix()
        return (parts.path in (prefix, prefix + "/screening", prefix + "/success")
                and parts.query == f"dcr_ci={self.company}")

    def note_blocked(self) -> None:
        with self._lock:
            self.blocked_writes += 1

    # ---- phases ---------------------------------------------------------------------------

    def begin_upload(self) -> None:
        with self._lock:
            _require(self._phase == "read" and self.uploads == 0, "POLICY_APP_SR_BOUNDARY")
            self._phase = "upload"

    def end_upload(self) -> None:
        with self._lock:
            if self._phase == "upload":
                self._phase = "read"

    def begin_submit(self) -> None:
        """Arm once, AFTER the runtime committed mark_started."""
        with self._lock:
            _require(self._phase == "read" and self.application_attempts == 0
                     and self.applications_allowed == 0 and self.publication_id is not None,
                     "POLICY_APP_SR_BOUNDARY")
            self._phase = "submit"

    def allows(self, method: str, url: str, resource_type: str, is_navigation: bool) -> bool:
        with self._lock:
            if type(method) is not str or type(url) is not str or type(is_navigation) is not bool:
                self.blocked_writes += 1
                return False
            try:
                parts = urlsplit(url)
            except ValueError:
                self.blocked_writes += 1
                return False
            if method in ("GET", "HEAD"):
                if is_navigation:
                    return resource_type == "document" and self._navigation_ok(url)
                return parts.scheme == "https" and parts.netloc in self.read_hosts and not parts.username
            if method == "POST" and self.publication_id is not None and url == self.application_url():
                self.application_attempts += 1
                if (self._phase == "submit" and self.application_attempts == 1
                        and self.applications_allowed == 0 and not is_navigation):
                    self.applications_allowed += 1
                    self._phase = "closed"  # consumed before the request continues
                    return True
                self._phase = "closed"
                self.blocked_writes += 1
                return False
            if method == "POST" and self._phase == "upload" and self.uploads == 0 and self.is_upload(url) \
                    and not is_navigation:
                self.uploads += 1
                return True
            self.blocked_writes += 1
            return False


class SmartRecruitersPolicyBrowser:
    """One owned page implementing ``PolicyFormBrowser`` for SmartRecruiters one-click."""

    def __init__(self, context, boundary: PolicyNetworkBoundary, *, poll_seconds: float = 2.0,
                 clock=lambda: datetime.now(timezone.utc), transport: str = "fetch",
                 offline_test_transport: bool = False):
        """``transport="fetch"`` (production) performs every allowed request itself with
        redirects disabled, so no redirect hop can bypass the boundary. ``"fallback"``
        is only for offline tests whose own route handler fulfils the request."""
        _require(transport == "fetch" or (transport == "fallback" and offline_test_transport is True),
                 "POLICY_APP_SR_BOUNDARY")
        self._transport = transport
        self._fatal = False
        _require(isinstance(boundary, PolicyNetworkBoundary), "POLICY_APP_SR_BOUNDARY")
        self.context, self.boundary = context, boundary
        self.page = context.new_page()
        self.page.set_default_timeout(10_000)
        self._step = 0
        self._popup = False
        self._socket = False
        self._attachment: dict | None = None
        self._submitted = False
        self._poll = poll_seconds
        self._clock = clock
        context.route("**/*", self._route)
        _require(callable(getattr(context, "route_web_socket", None)), "POLICY_APP_SR_BOUNDARY")
        context.route_web_socket("**/*", self._deny_socket)
        context.on("page", self._new_page)
        # Belt and braces: any redirect hop or service worker is fatal for this session.
        context.on("request", self._observe_request)
        context.on("serviceworker", self._service_worker)

    @classmethod
    def launch(cls, chromium_browser, boundary: PolicyNetworkBoundary, **options) -> "SmartRecruitersPolicyBrowser":
        """Production entry point: a fresh context with service workers blocked."""
        _require("transport" not in options and "offline_test_transport" not in options,
                 "POLICY_APP_SR_BOUNDARY")
        context = chromium_browser.new_context(service_workers="block", accept_downloads=False)
        return cls(context, boundary, **options)

    # ---- fences ------------------------------------------------------------------------------

    def _route(self, route):
        request = route.request
        if not self.boundary.allows(request.method, request.url, request.resource_type,
                                    request.is_navigation_request()):
            route.abort("blockedbyclient")
            return
        if self._transport == "fallback":
            route.fallback()
            return
        try:
            response = route.fetch(max_redirects=0, timeout=30_000)
        except Exception:
            route.abort("failed")
            return
        if 300 <= response.status < 400:
            self._fatal = True
            self.boundary.note_blocked()
            route.abort("blockedbyclient")
            return
        route.fulfill(response=response)

    def _observe_request(self, request):
        if request.redirected_from is not None:
            self._fatal = True
            self.boundary.note_blocked()

    def _service_worker(self, _worker):
        self._fatal = True
        self.boundary.note_blocked()

    def _deny_socket(self, _socket):
        self._socket = True

    def _new_page(self, page):
        if page != self.page:
            self._popup = True  # OAuth/CAPTCHA windows are a human handoff, never driven

    # ---- discovery ---------------------------------------------------------------------------

    def _controls(self):
        """(key, locator, row, kind) for every logical control on the current page."""
        found, keys = [], set()
        for locator, info in OneClickBrowser._controls(self):  # reviewed SPL/shadow discovery
            kind = info["kind"]
            if kind == "file":
                control = "file"
            elif info["tag"] == "textarea":
                control = "textarea"
            else:
                control = _KIND_TO_CONTROL.get(kind)
            _require(control is not None and not info["disabled"], "POLICY_APP_SR_CONTROL_UNSUPPORTED")
            key = control_key(info["label"])
            _require(key not in keys, "POLICY_APP_SR_DUPLICATE_KEY")
            keys.add(key)
            options = None
            if control in ("select", "radio"):
                options = [o.strip() for o in info["options"] if o and o.strip()]
                if control == "select" and info["tag"] != "select":
                    options = self._combobox_options(locator)
                _require(options and len(set(options)) == len(options), "POLICY_APP_SR_CONTROL_UNSUPPORTED")
            found.append((key, locator, dict(key=key, label=info["label"], control=control,
                                             required=bool(info["required"]), options=options), kind))
        return found

    def _combobox_options(self, locator) -> list[str]:
        """Options of a combobox whose aria-controls names a listbox (read without opening)."""
        menu_id = locator.get_attribute("aria-controls")
        _require(bool(menu_id) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", menu_id or ""),
                 "POLICY_APP_SR_CONTROL_UNSUPPORTED")
        menu = self.page.locator(f'[id="{menu_id}"]')
        _require(menu.count() == 1 and menu.get_attribute("role") in ("listbox", "menu"),
                 "POLICY_APP_SR_CONTROL_UNSUPPORTED")
        return [text.strip() for text in menu.locator('[role="option"]').all_text_contents() if text.strip()]

    def _find(self, key: str):
        matches = [(locator, row, kind) for k, locator, row, kind in self._controls() if k == key]
        _require(len(matches) == 1, "POLICY_APP_SR_CONTROL_CHANGED")
        return matches[0]

    def _button(self, labels) -> object | None:
        found = []
        for label in labels:
            for button in self.page.get_by_role("button", name=label, exact=True).all():
                if button.is_visible() and button.is_enabled():
                    found.append(button)
        _require(len(found) <= 1, "POLICY_APP_SR_AMBIGUOUS_ACTION")
        return found[0] if found else None

    # ---- PolicyFormBrowser -------------------------------------------------------------------

    def open(self, url: str) -> None:
        parts = urlsplit(url)
        _require(parts.netloc == _ORIGIN_HOST and self.boundary._navigation_ok(url)
                 and self.boundary.publication_id is None, "POLICY_APP_SR_ENTRY")
        self.page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        prefix = f"https://{_ORIGIN_HOST}/oneclick-ui/company/{self.boundary.company}/publication/"
        hrefs = {href for href in self.page.locator("a[href]").evaluate_all("els => els.map(e => e.href)")
                 if href.startswith(prefix)}
        _require(len(hrefs) == 1, "POLICY_APP_SR_ENTRY")
        entry = urlsplit(hrefs.pop())
        publication = entry.path[len(urlsplit(prefix).path):]
        _require(entry.query == f"dcr_ci={self.boundary.company}" and not entry.fragment
                 and _UUID.fullmatch(publication), "POLICY_APP_SR_ENTRY")
        self.boundary.set_publication(publication)
        self.page.goto(self.boundary.entry_url(), wait_until="domcontentloaded", timeout=30_000)
        self.page.locator("input, select, textarea").first.wait_for(state="attached", timeout=30_000)

    def inspect(self) -> dict:
        controls = self._controls()
        fields = [row for _, _, row, _ in controls if row["control"] != "file"]
        files = [row for _, _, row, _ in controls if row["control"] == "file"]
        _require(len(files) <= 1, "POLICY_APP_SR_CONTROL_UNSUPPORTED")
        body = self.page.locator("body").inner_text().casefold()
        _require(not self._fatal, "POLICY_APP_SR_BOUNDARY")
        challenge = (self._popup or self._socket or self.page.locator("iframe").count() > 0
                     or any(text in body for text in _CHALLENGE_TEXT))
        form_valid = self.page.locator("input,select,textarea").evaluate_all(
            "els => els.filter(x => x.type !== 'hidden' && !x.disabled).every(x => x.checkValidity())")
        attachment = None
        if self._attachment is not None and files:
            name = self._attachment["filename"]
            visible = self.page.get_by_text(name, exact=True)
            attachment = dict(self._attachment, visible=visible.count() == 1 and visible.is_visible())
        return dict(url=self.page.url.split("#")[0], step=self._step, fields=fields,
                    cv_upload=({k: files[0][k] for k in ("key", "label", "required")} if files else None),
                    challenge=bool(challenge), form_valid=bool(form_valid) and self.boundary.blocked_writes == 0,
                    submit_visible=self._button(_SUBMIT_LABELS) is not None,
                    next_visible=self._button(_NEXT_LABELS) is not None,
                    cv_attachment=attachment)

    def write(self, key: str, control: str, value: object) -> None:
        locator, row, _ = self._find(key)
        _require(row["control"] == control, "POLICY_APP_SR_CONTROL_CHANGED")
        if self.readback(key, control) == value:
            return
        if control == "checkbox":
            _require(type(value) is bool, "POLICY_APP_SR_VALUE")
            locator.set_checked(value)
        elif control == "radio":
            _require(type(value) is str and value in (row["options"] or ()), "POLICY_APP_SR_VALUE")
            radios = [r for r in locator.get_by_role("radio", name=value, exact=True).all() if r.is_visible()]
            _require(len(radios) == 1, "POLICY_APP_SR_OPTION")
            radios[0].check()
        elif control == "select":
            _require(type(value) is str and value in (row["options"] or ()), "POLICY_APP_SR_VALUE")
            if locator.evaluate("el => el.tagName === 'SELECT'"):
                locator.select_option(label=value)
            else:
                locator.click()
                menu = self.page.locator(f'[id="{locator.get_attribute("aria-controls")}"]')
                options = [o for o in menu.get_by_role("option", name=value, exact=True).all() if o.is_visible()]
                _require(len(options) == 1, "POLICY_APP_SR_OPTION")
                options[0].click()
        else:
            _require(type(value) is str, "POLICY_APP_SR_VALUE")
            locator.fill(value)
            locator.blur()

    def readback(self, key: str, control: str) -> object:
        locator, row, _ = self._find(key)
        _require(row["control"] == control, "POLICY_APP_SR_CONTROL_CHANGED")
        value = locator.evaluate(_VALUE)
        if control == "radio":
            _require(value != "AMBIGUOUS_RADIO_SELECTION", "POLICY_APP_SR_OPTION")
        return value

    def upload(self, key: str, payload: bytes, filename: str) -> UploadResult:
        _require(self._attachment is None and filename == self.boundary.filename
                 and self.boundary.blocked_writes == 0, "POLICY_APP_SR_UPLOAD")
        locator, row, _ = self._find(key)
        _require(row["control"] == "file", "POLICY_APP_SR_UPLOAD")
        self.boundary.begin_upload()
        try:
            with self.page.expect_response(lambda r: r.request.method == "POST" and self.boundary.is_upload(r.url),
                                           timeout=30_000) as pending:
                locator.set_input_files({"name": filename, "mimeType": "application/pdf", "buffer": payload})
            response = pending.value
            _require(response.status in (200, 201) and response.request.redirected_from is None,
                     "POLICY_APP_SR_UPLOAD")
            data = response.json()
            _require(isinstance(data, dict) and isinstance(data.get("fileId"), str) and data["fileId"]
                     and data.get("fileName") == filename, "POLICY_APP_SR_UPLOAD")
            shown = self.page.get_by_text(filename, exact=True)
            shown.wait_for(state="visible", timeout=15_000)
            visible = shown.count() == 1 and shown.is_visible()
        finally:
            self.boundary.end_upload()
        self._attachment = dict(sha256=hashlib.sha256(payload).hexdigest(), size=len(payload), filename=filename)
        return UploadResult(self._attachment["sha256"], len(payload), filename, visible)

    def advance(self) -> None:
        button = self._button(_NEXT_LABELS)
        _require(button is not None and self.boundary.blocked_writes == 0, "POLICY_APP_SR_ADVANCE")
        before = self.page.url
        button.click()
        self.page.wait_for_function("u => location.href !== u", arg=before, timeout=15_000)
        self.page.locator("input, select, textarea").first.wait_for(state="attached", timeout=15_000)
        self._step += 1

    def wait_for_owner(self, timeout_seconds: float) -> bool:
        """The owner solves the challenge in this visible window; we only observe."""
        deadline = monotonic() + max(0.0, float(timeout_seconds))
        while monotonic() < deadline:
            if not self.inspect()["challenge"]:
                return True
            sleep(self._poll)
        return False

    def submit_once(self) -> SubmitObservation:
        _require(not self._submitted and not self._fatal and self.boundary.blocked_writes == 0,
                 "POLICY_APP_SR_SUBMIT")
        button = self._button(_SUBMIT_LABELS)
        _require(button is not None, "POLICY_APP_SR_SUBMIT")
        self._submitted = True
        self.boundary.begin_submit()
        url = self.boundary.application_url()
        with self.page.expect_response(lambda r: r.url == url and r.request.method == "POST",
                                       timeout=30_000) as pending:
            button.click()
        response = pending.value
        payload = response.body()
        _require(len(payload) <= 1_000_000 and response.request.redirected_from is None, "POLICY_APP_SR_SUBMIT")
        try:
            data = json.loads(payload)
        except ValueError:
            data = None
        success = response.status in (200, 201) and isinstance(data, dict) and data.get("success") is True
        success_path = f"/oneclick-ui/company/{self.boundary.company}/publication/{self.boundary.publication_id}/success"
        try:
            self.page.wait_for_url(lambda u: urlsplit(u).path == success_path, timeout=15_000)
            visible = True
        except Exception:
            visible = False
        challenge = self._popup or self.boundary.application_attempts != 1 or self.boundary.blocked_writes != 0
        return SubmitObservation(self._clock(), success, visible,
                                 hashlib.sha256(payload).hexdigest(), challenge)
