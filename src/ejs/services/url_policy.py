from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import socket
from typing import Callable, Iterable
from urllib.parse import urlsplit, urlunsplit


@dataclass(frozen=True)
class UrlPolicyDecision:
    allowed: bool
    code: str = ""
    message: str = ""


class PublicHttpsUrlPolicy:
    """Fail-closed initial URL policy for the Cloud Run browser worker.

    GD-002 accepts only public HTTPS destinations. Caller-controlled local/file/browser
    schemes and Google metadata/link-local/private destinations are rejected before launch.
    Runtime DNS resolution can additionally prove that the requested hostname resolves only
    to globally routable addresses.
    """

    _blocked_hostnames = {
        "localhost",
        "metadata.google.internal",
        "metadata",
    }
    _blocked_suffixes = (
        ".localhost",
        ".local",
        ".internal",
        ".home.arpa",
    )

    @classmethod
    def static_validate(cls, url: str) -> UrlPolicyDecision:
        try:
            parsed = urlsplit(url)
        except Exception:
            return UrlPolicyDecision(False, "INVALID_URL", "URL could not be parsed")
        if parsed.scheme.lower() != "https":
            return UrlPolicyDecision(False, "HTTPS_REQUIRED", "only HTTPS external targets are allowed")
        if parsed.username or parsed.password:
            return UrlPolicyDecision(False, "USERINFO_FORBIDDEN", "URL userinfo is forbidden")
        host = (parsed.hostname or "").rstrip(".").lower()
        if not host:
            return UrlPolicyDecision(False, "HOST_REQUIRED", "target hostname is required")
        if host in cls._blocked_hostnames or host.endswith(cls._blocked_suffixes):
            return UrlPolicyDecision(False, "BLOCKED_HOST", "local/internal/metadata host is forbidden")
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            ip = None
        if ip is not None and not ip.is_global:
            return UrlPolicyDecision(False, "NON_PUBLIC_IP", "non-public IP literal is forbidden")
        if parsed.port not in (None, 443):
            return UrlPolicyDecision(False, "PORT_FORBIDDEN", "GD-002 allows only default HTTPS port 443")
        return UrlPolicyDecision(True)

    @classmethod
    def validate_resolved(
        cls,
        url: str,
        *,
        resolver: Callable[..., Iterable[tuple]] = socket.getaddrinfo,
    ) -> UrlPolicyDecision:
        static = cls.static_validate(url)
        if not static.allowed:
            return static
        host = (urlsplit(url).hostname or "").rstrip(".")
        try:
            answers = resolver(host, 443, type=socket.SOCK_STREAM)
        except Exception as exc:
            return UrlPolicyDecision(False, "DNS_UNAVAILABLE", f"DNS resolution failed: {type(exc).__name__}")
        ips: set[str] = set()
        for item in answers:
            try:
                sockaddr = item[4]
                ips.add(str(sockaddr[0]))
            except Exception:
                continue
        if not ips:
            return UrlPolicyDecision(False, "DNS_EMPTY", "DNS returned no usable address")
        for raw in sorted(ips):
            try:
                ip = ipaddress.ip_address(raw)
            except ValueError:
                return UrlPolicyDecision(False, "DNS_INVALID_IP", "DNS returned an invalid address")
            if not ip.is_global:
                return UrlPolicyDecision(False, "DNS_NON_PUBLIC_IP", "hostname resolves to non-public address")
        return UrlPolicyDecision(True)


def sanitize_external_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port and parsed.port != 443 else ""
    return urlunsplit((parsed.scheme, f"{host}{port}", parsed.path, "", ""))
