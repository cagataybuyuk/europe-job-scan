"""Exact OneClick routes and one-use network phases for a trusted live driver.

This module does not establish vacancy suitability, authenticate a reviewer, open
a browser, or grant submission authority. The trusted caller must check those
gates and commit the canonical durable intent BEFORE ``begin_submit``. Context
creation must disable service workers and separately deny WebSockets/popups.

No URL normalization broadens a reviewed allowlist: query changes, encoded path
aliases, redirects and a second vendor-generated POST are all separate requests.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from threading import Lock
from urllib.parse import urlsplit
from uuid import UUID


_ORIGIN = "https://jobs.smartrecruiters.com"
_COMPANY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}\Z")
_DATABASE_ID = re.compile(r"[a-f0-9]{24}\Z")
_POSTING_ID = re.compile(r"[0-9]{8,20}\Z")
_REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}\Z")
_PDF_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.pdf\Z", re.IGNORECASE)


def _valid(pattern: re.Pattern, value: object) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _https_url(value: object) -> bool:
    """Accept a bounded exact HTTPS URL, never a wildcard or origin rule."""
    if not isinstance(value, str) or not 1 <= len(value) <= 4096:
        return False
    if not value.isascii() or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value):
        return False
    if "\\" in value or "*" in value or "#" in value:
        return False
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme == "https"
            and bool(parsed.hostname)
            and parsed.port in (None, 443)
            and not parsed.username and not parsed.password and not parsed.fragment
            and parsed.netloc == parsed.hostname  # Canonical host, no explicit port.
            and parsed.path.startswith("/")
            and not parsed.path.endswith("/")
        )
    except (TypeError, ValueError):
        return False


@dataclass(frozen=True)
class OneClickTarget:
    company: str
    company_id: str
    posting_id: str
    publication_id: str
    requisition_id: str
    posting_url: str

    def __post_init__(self) -> None:
        valid = (
            _valid(_COMPANY, self.company)
            and _valid(_DATABASE_ID, self.company_id)
            and _valid(_POSTING_ID, self.posting_id)
            and _valid(_REFERENCE, self.requisition_id)
            and isinstance(self.publication_id, str)
        )
        if valid:
            try:
                valid = str(UUID(self.publication_id)) == self.publication_id
            except (TypeError, ValueError, AttributeError):
                valid = False
        if valid:
            valid = (
                _https_url(self.posting_url)
                and re.fullmatch(
                    re.escape(f"{_ORIGIN}/{self.company}/{self.posting_id}")
                    + r"(?:-[A-Za-z0-9_-]{1,200})?", self.posting_url
                ) is not None
            )
        if not valid:
            raise ValueError("ONECLICK_TARGET_INVALID")

    @property
    def entry_url(self) -> str:
        return (
            f"{_ORIGIN}/oneclick-ui/company/{self.company}/publication/"
            f"{self.publication_id}?dcr_ci={self.company}"
        )

    @property
    def screening_url(self) -> str:
        return (
            f"{_ORIGIN}/oneclick-ui/company/{self.company}/publication/"
            f"{self.publication_id}/screening?dcr_ci={self.company}"
        )

    def resume_upload_url(self, filename: str) -> str:
        if not _valid(_PDF_NAME, filename):
            raise ValueError("ONECLICK_RESUME_FILENAME_INVALID")
        return (
            f"{_ORIGIN}/oneclick-ui/api/company/{self.company_id}"
            f"/attachment/resume?filename={filename}"
        )

    @property
    def application_url(self) -> str:
        return (
            f"{_ORIGIN}/oneclick-ui/api/company/{self.company}/publication/"
            f"{self.publication_id}/application"
        )


class OneClickNetworkBoundary:
    """Deny every unreviewed read and every write except one armed exact POST.

    ``application_attempts`` counts ALL observed exact application POSTs, even
    denied retries. ``application_requests_allowed`` counts the at-most-one
    dispatched request. A consumed submit phase cannot be reopened. The class is
    thread-safe but its state is process-local; durable duplicate prevention is
    deliberately the separately required canonical ledger.
    """

    def __init__(self, target: OneClickTarget, *, read_urls: tuple[str, ...],
                 resume_filename: str):
        if not isinstance(target, OneClickTarget):
            raise ValueError("ONECLICK_TARGET_INVALID")
        if (not isinstance(read_urls, tuple) or len(read_urls) > 1000
                or not all(_https_url(x) for x in read_urls)
                or len(set(read_urls)) != len(read_urls)):
            raise ValueError("ONECLICK_READ_ALLOWLIST_INVALID")
        self._target = target
        self._upload_url = target.resume_upload_url(resume_filename)
        self._navigation_urls = frozenset((target.posting_url, target.entry_url,
                                           target.screening_url))
        self._read_urls = frozenset(read_urls) | self._navigation_urls
        # A reviewed read list must not silently turn a known mutation endpoint
        # into a GET channel. Neither the upload parser nor the application API
        # is a public metadata read.
        if any(url == target.application_url or "/attachment/" in urlsplit(url).path
               or urlsplit(url).path.endswith("/application") for url in read_urls):
            raise ValueError("ONECLICK_READ_ALLOWLIST_INVALID")
        self._lock = Lock()
        self._phase = "read"
        self._upload_begun = False
        self._submit_begun = False
        self._blocked_write_count = 0
        self._application_attempts = 0
        self._application_requests_allowed = 0
        self._upload_requests_allowed = 0

    @property
    def target(self) -> OneClickTarget:
        return self._target

    @property
    def phase(self) -> str:
        with self._lock:
            return self._phase

    @property
    def blocked_write_count(self) -> int:
        with self._lock:
            return self._blocked_write_count

    @property
    def application_attempts(self) -> int:
        with self._lock:
            return self._application_attempts

    @property
    def application_requests_allowed(self) -> int:
        with self._lock:
            return self._application_requests_allowed

    @property
    def upload_requests_allowed(self) -> int:
        with self._lock:
            return self._upload_requests_allowed

    def begin_upload(self) -> None:
        with self._lock:
            if self._phase != "read" or self._upload_begun or self._submit_begun:
                raise PermissionError("ONECLICK_UPLOAD_PHASE_INVALID")
            self._upload_begun = True
            self._phase = "upload"

    def end_upload(self) -> None:
        with self._lock:
            if self._phase != "upload":
                raise PermissionError("ONECLICK_UPLOAD_PHASE_INVALID")
            self._phase = "read"

    def begin_submit(self) -> None:
        """Arm once; caller must have already committed the durable intent."""
        with self._lock:
            if self._phase != "read" or self._submit_begun:
                raise PermissionError("ONECLICK_SUBMIT_PHASE_INVALID")
            self._submit_begun = True
            self._phase = "submit"

    def allows(self, method: str, url: str, resource_type: str = "",
               is_navigation: bool = False) -> bool:
        # Checking membership against the already validated exact string sets
        # avoids URL parser normalization or query wildcard surprises.
        with self._lock:
            if not isinstance(method, str) or not isinstance(url, str):
                self._blocked_write_count += 1
                return False
            if method in ("GET", "HEAD"):
                if type(is_navigation) is not bool:
                    return False
                if is_navigation:
                    return resource_type == "document" and url in self._navigation_urls
                return url in self._read_urls

            if method == "POST" and url == self.target.application_url:
                self._application_attempts += 1
            if type(is_navigation) is bool and not is_navigation and method == "POST":
                if self._phase == "upload" and url == self._upload_url and not self._upload_requests_allowed:
                    self._upload_requests_allowed += 1
                    return True
                if (self._phase == "submit" and url == self.target.application_url
                        and self._application_attempts == 1 and not self._application_requests_allowed):
                    self._application_requests_allowed += 1
                    self._phase = "closed"  # Consume BEFORE continuing the request.
                    return True
            if method == "POST" and url == self.target.application_url:
                # A premature exact-target attempt or a retry cannot later be
                # rearmed in this session, even if the trusted caller errs.
                self._phase = "closed"
            self._blocked_write_count += 1
            return False
