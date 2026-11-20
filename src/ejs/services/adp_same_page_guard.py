"""Revalidate existing same-page operations; grants no additional action authority."""
from __future__ import annotations

from ejs.services.adp_navigation_canary import _snapshot
from ejs.services.adp_same_page_manifest import _target_binding


def require_current_verified_target(page, application_url: str) -> None:
    try:
        if _target_binding(str(page.url), application_url).get('target_bound') is not True:
            raise PermissionError('ADP_SAME_PAGE_OPERATION_TARGET_MISMATCH')
        observed = _snapshot(page, requested_url=application_url, timeout_ms=2000, render_wait_ms=100)
        if (observed.get('runtime_state') != 'inspected'
                or observed.get('auth_observed') is not False
                or observed.get('captcha_observed') is not False):
            raise PermissionError('ADP_SAME_PAGE_OPERATION_BOUNDARY_UNCLEAR')
        if _target_binding(str(page.url), application_url).get('target_bound') is not True:
            raise PermissionError('ADP_SAME_PAGE_OPERATION_TARGET_MISMATCH')
    except PermissionError:
        raise
    except Exception:
        raise PermissionError('ADP_SAME_PAGE_OPERATION_OBSERVATION_FAILED') from None


class _GuardedLocator:
    def __init__(self, locator, page, application_url):
        self._locator, self._page, self._url = locator, page, application_url

    def __getattr__(self, name):
        if name in {'set_input_files', 'dispatch_event', 'drag_to'}:
            raise PermissionError('ADP_SAME_PAGE_OPERATION_UNSUPPORTED')
        member = getattr(self._locator, name)
        if name in {'first', 'last'}:
            return _GuardedLocator(member, self._page, self._url)
        if name in {'locator', 'nth', 'filter'} or name.startswith('get_by_'):
            def query(*args, **kwargs):
                return _GuardedLocator(member(*args, **kwargs), self._page, self._url)
            return query
        if name in {'click', 'fill', 'select_option', 'press', 'type', 'check', 'uncheck', 'set_checked', 'input_value', 'inner_text'}:
            def operation(*args, **kwargs):
                require_current_verified_target(self._page, self._url)
                return member(*args, **kwargs)
            return operation
        return member


class _GuardedPage:
    def __init__(self, page, application_url):
        self._page, self._url = page, application_url

    def __getattr__(self, name):
        if name in {'goto', 'reload', 'go_back', 'go_forward', 'set_content'}:
            raise PermissionError('ADP_SAME_PAGE_OPERATION_NAVIGATION_FORBIDDEN')
        member = getattr(self._page, name)
        if name == 'locator' or name.startswith('get_by_'):
            def query(*args, **kwargs):
                return _GuardedLocator(member(*args, **kwargs), self._page, self._url)
            return query
        return member


def guard_verified_page(page, application_url: str):
    if isinstance(page, _GuardedPage):
        if page._url != application_url:
            raise PermissionError('ADP_SAME_PAGE_OPERATION_TARGET_MISMATCH')
        return page
    return _GuardedPage(page, application_url)
