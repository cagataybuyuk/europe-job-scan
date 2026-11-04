"""Conservative network boundary for one prepare-only browser session.

This is not a live upload endpoint allowlist. Before preparation, only the exact
entry document and same-origin static resources without queries can load. Once
personal data may be present, every network request is denied. Real ATS upload
or autosave needs a separately reviewed narrow policy; this class grants none.
"""
from __future__ import annotations

from urllib.parse import urlsplit


class PrepareBoundaryError(PermissionError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class PrepareNetworkFence:
    _STATIC_TYPES = frozenset({"script", "stylesheet", "image", "font", "media"})

    def __init__(self, target_url: str):
        self.target_url = target_url
        target = urlsplit(target_url)
        if target.username or target.password or target.fragment:
            raise PrepareBoundaryError("PREPARE_TARGET_INVALID")
        if target.scheme == "file":
            if target.netloc not in {"", "localhost"} or target.query:
                raise PrepareBoundaryError("PREPARE_TARGET_INVALID")
            self.fixture = True
        else:
            self.fixture = False
            loopback_fixture = target.scheme == "http" and target.hostname in {"127.0.0.1", "localhost"}
            if not loopback_fixture and (target.scheme != "https" or not target.hostname or target.port not in {None, 443}):
                raise PrepareBoundaryError("PREPARE_TARGET_INVALID")
        self.origin = (target.scheme, target.hostname, target.port or (443 if target.scheme == "https" else 80))
        self.preparing = False
        self.blocked = False
        self.block_reasons: set[str] = set()

    def allowed(self, *, url: str, method: str, resource_type: str, navigation: bool) -> bool:
        try:
            parsed = urlsplit(url)
            if self.fixture or self.preparing or method not in {"GET", "HEAD"}:
                return False
            if parsed.username or parsed.password or parsed.fragment:
                return False
            origin = (parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
            if origin != self.origin:
                return False
            if navigation:
                return url == self.target_url and resource_type == "document"
            # No XHR/fetch/beacon and no GET query channel, even on the target origin.
            return not parsed.query and resource_type in self._STATIC_TYPES
        except (TypeError, ValueError):
            return False

    def deny(self, reason: str) -> None:
        self.blocked = True
        self.block_reasons.add(reason)

    def install(self, context) -> None:
        if not callable(getattr(context, "route_web_socket", None)):
            raise PrepareBoundaryError("PREPARE_WEBSOCKET_FENCE_UNAVAILABLE")

        def route_request(route):
            request = route.request
            try:
                permitted = self.allowed(url=request.url, method=request.method,
                    resource_type=request.resource_type, navigation=request.is_navigation_request())
                if permitted:
                    route.continue_()
                else:
                    self.deny("REQUEST_DENIED")
                    route.abort("blockedbyclient")
            except Exception:
                self.deny("REQUEST_FENCE_ERROR")
                route.abort("blockedbyclient")

        def close_websocket(ws):
            self.deny("WEBSOCKET_DENIED")
            # Routed sockets do not connect unless connect_to_server() is called.
            # Leave it disconnected; executor closes the entire context at once.
            # Calling sync close() inside the route callback can deadlock Chromium.

        context.route("**/*", route_request)
        context.route_web_socket("**", close_websocket)

    def begin_preparation(self) -> None:
        self.require_clear()
        self.preparing = True

    def require_clear(self) -> None:
        if self.blocked:
            raise PrepareBoundaryError("PREPARE_NETWORK_BLOCKED")
