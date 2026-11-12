"""Bounded local TEST handoff controller; never an OAuth/network bridge.

The trusted bootstrap fixes state_root and the canonical ledger. The private
command protocol accepts no paths, selectors, browser commands or credentials.
It retains existing objects and reloads independently pinned fixed input files.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
from time import monotonic

from ejs.services.single_application_inputs import load_reviewed_application_inputs
from ejs.services.single_application_runtime import require


_SCOPE = ('target', 'execution_id', 'opportunity_key', 'candidate_profile_version', 'cv', 'read_urls')
_HEX64 = re.compile(r'[a-f0-9]{64}')
_OBSERVATION = re.compile(r'[a-f0-9]{48}')


class _HandoffDeadlineExceeded(Exception):
    pass


def _review_scope(manifest):
    scope = {key: manifest[key] for key in _SCOPE}
    # Missing policy and explicit None preserve the previous entry behavior;
    # any actual strategy/reviewer-policy change requires a new owned flow.
    # Canonical JSON also keeps True distinct from 1 in nested policy values.
    scope['entry_policy'] = json.dumps(manifest.get('entry_policy'), sort_keys=True,
                                       separators=(',', ':'), allow_nan=False)
    return scope


def _pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, 'APPLICATION_HANDOFF_COMMAND_REJECTED')
        result[key] = value
    return result


class RetainedEntryController:
    def __init__(self, state_root: Path, source_repo: Path, source_sha: str, *, timeout_seconds: int = 300):
        require(type(timeout_seconds) is int and 1 <= timeout_seconds <= 600,
                'APPLICATION_HANDOFF_TIMEOUT_INVALID')
        require(type(source_sha) is str and re.fullmatch(r'[a-f0-9]{40}', source_sha),
                'APPLICATION_SOURCE_REVIEW_REQUIRED')
        self._directory = state_root / 'entry-return-controller'
        self._state_root, self._repo, self._source_sha = state_root, source_repo, source_sha
        self._timeout = timeout_seconds
        self._session_id = secrets.token_hex(24)
        self._created = {}
        self._directory_fd = None
        self._sequence = 1
        self._used = self._observed = self._resumed = False

    @staticmethod
    def _remaining(deadline):
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise _HandoffDeadlineExceeded()
        return remaining

    def _source_clean(self, deadline):
        actual = subprocess.run(['git', '-C', str(self._repo), 'rev-parse', 'HEAD'], check=True,
            capture_output=True, text=True, timeout=min(10, self._remaining(deadline))).stdout.strip()
        self._remaining(deadline)
        dirty = subprocess.run(['git', '-C', str(self._repo), 'status', '--porcelain', '--untracked-files=all'],
            check=True, capture_output=True, text=True, timeout=min(10, self._remaining(deadline))).stdout
        self._remaining(deadline)
        require(actual == self._source_sha and not dirty, 'APPLICATION_SOURCE_REVIEW_REQUIRED')

    @staticmethod
    def _owned_page(driver):
        page = getattr(driver, 'page', None)
        context = getattr(driver, 'context', None)
        require(page is not None and page is getattr(driver, '_owned_page', None)
                and context is not None and context is getattr(driver, '_owned_context', None)
                and page.context is context and not page.is_closed() and page in context.pages,
                'APPLICATION_HANDOFF_OWNED_PAGE_REQUIRED')
        return page

    def _pump_idle(self, driver, deadline):
        page = self._owned_page(driver)
        remaining = self._remaining(deadline)
        # This pumps Playwright's queued route/page events without clicking,
        # navigating, observing field values or creating another browser.
        page.wait_for_timeout(min(100, max(1, int(remaining * 1000))))
        self._remaining(deadline)
        self._owned_page(driver)

    @staticmethod
    def _validate_observation(observation):
        require(type(observation) is dict and set(observation) == {
            'observation_id', 'surface_fingerprint', 'observed_at'}, 'APPLICATION_HANDOFF_OBSERVATION_REJECTED')
        require(type(observation['observation_id']) is str and _OBSERVATION.fullmatch(observation['observation_id'])
                and type(observation['surface_fingerprint']) is str and _HEX64.fullmatch(observation['surface_fingerprint'])
                and type(observation['observed_at']) is str and len(observation['observed_at']) <= 64,
                'APPLICATION_HANDOFF_OBSERVATION_REJECTED')
        stamp = datetime.fromisoformat(observation['observed_at'].replace('Z', '+00:00'))
        require(stamp.tzinfo is not None and stamp.utcoffset().total_seconds() == 0
                and 0 <= (datetime.now(timezone.utc) - stamp).total_seconds() <= 120,
                'APPLICATION_HANDOFF_OBSERVATION_REJECTED')

    def _private_directory(self):
        for component in (self._state_root, *self._state_root.parents):
            require(not component.is_symlink(), 'APPLICATION_HANDOFF_PRIVATE_STATE_REQUIRED')
        root = self._state_root.stat()
        require(stat.S_ISDIR(root.st_mode) and root.st_uid == os.getuid()
                and stat.S_IMODE(root.st_mode) == 0o700, 'APPLICATION_HANDOFF_PRIVATE_STATE_REQUIRED')
        self._directory.mkdir(mode=0o700, exist_ok=True)
        require(not self._directory.is_symlink(), 'APPLICATION_HANDOFF_PRIVATE_STATE_REQUIRED')
        info = self._directory.stat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
                and stat.S_IMODE(info.st_mode) == 0o700, 'APPLICATION_HANDOFF_PRIVATE_STATE_REQUIRED')
        self._directory_fd = os.open(self._directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        opened = os.fstat(self._directory_fd)
        require((opened.st_dev, opened.st_ino) == (info.st_dev, info.st_ino),
                'APPLICATION_HANDOFF_PRIVATE_STATE_REQUIRED')

    def _write_once(self, name, value):
        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600,
                             dir_fd=self._directory_fd)
        with os.fdopen(descriptor, 'w') as stream:
            info = os.fstat(stream.fileno())
            self._created[name] = (info.st_dev, info.st_ino)
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())

    def _command(self):
        try:
            descriptor = os.open('request.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 dir_fd=self._directory_fd)
        except FileNotFoundError:
            return None
        with os.fdopen(descriptor, 'rb') as stream:
            before = os.fstat(stream.fileno())
            require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid() and before.st_nlink == 1
                    and stat.S_IMODE(before.st_mode) == 0o600 and 0 < before.st_size <= 16_384,
                    'APPLICATION_HANDOFF_COMMAND_REJECTED')
            raw = stream.read(16_385)
            after = os.fstat(stream.fileno())
        current = os.stat('request.json', dir_fd=self._directory_fd, follow_symlinks=False)
        require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                and (current.st_dev, current.st_ino) == (before.st_dev, before.st_ino)
                and len(raw) <= 16_384, 'APPLICATION_HANDOFF_COMMAND_REJECTED')
        value = json.loads(raw, object_pairs_hook=_pairs)
        require(type(value) is dict and value.get('session_id') == self._session_id
                and type(value.get('sequence')) is int and value['sequence'] == self._sequence,
                'APPLICATION_HANDOFF_COMMAND_REJECTED')
        action = value.get('action')
        expected = {'session_id', 'sequence', 'action'}
        if action == 'resume_pinned_review':
            expected |= {'manifest_sha256', 'facts_sha256'}
        require(action in {'observe_return', 'resume_pinned_review', 'stop'} and set(value) == expected,
                'APPLICATION_HANDOFF_COMMAND_REJECTED')
        os.unlink('request.json', dir_fd=self._directory_fd)
        self._sequence += 1
        return value

    def _cleanup(self):
        complete = True
        if self._directory_fd is None:
            return complete
        try:
            for name, identity in reversed(list(self._created.items())):
                try:
                    current = os.stat(name, dir_fd=self._directory_fd, follow_symlinks=False)
                    if not stat.S_ISREG(current.st_mode) or (current.st_dev, current.st_ino) != identity:
                        complete = False
                        continue
                    os.unlink(name, dir_fd=self._directory_fd)
                except FileNotFoundError:
                    pass
                except OSError:
                    complete = False
        finally:
            try:
                os.close(self._directory_fd)
            except OSError:
                complete = False
            self._directory_fd = None
        return complete

    @staticmethod
    def _occupied(ledger):
        return {'status': 'DUPLICATE_OR_UNCERTAIN', 'applied': False,
                'submit_click_attempts': None}

    def wait_for_review(self, runtime, driver, ledger, manifest, facts, target, cv_path, *, authority):
        result = None
        deadline = monotonic() + self._timeout
        try:
            require(not self._used, 'APPLICATION_HANDOFF_REPLAY_REJECTED')
            self._used = True
            if ledger.has_intent():
                result = self._occupied(ledger)
                return result
            self._source_clean(deadline)
            self._private_directory()
            try:
                os.stat('request.json', dir_fd=self._directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                require(False, 'APPLICATION_HANDOFF_STALE_COMMAND')
            self._write_once('active.lock', {'session_id': self._session_id})
            self._write_once('session.json', {'session_id': self._session_id, 'next_sequence': 1,
                'status': 'HUMAN_HANDOFF_REQUIRED', 'timeout_seconds': self._timeout,
                'oauth_bridge_implemented': False, 'native_fallback_allowed': False})
            scope = deepcopy(_review_scope(manifest))
            self._remaining(deadline)
            observation = None
            while monotonic() < deadline:
                command = self._command()
                if command is None:
                    self._pump_idle(driver, deadline)
                    continue
                if ledger.has_intent():
                    result = self._occupied(ledger)
                    return result
                self._source_clean(deadline)
                if command['action'] == 'stop':
                    result = {'status': 'HANDOFF_STOPPED', 'applied': False, 'submit_click_attempts': 0}
                    return result
                if command['action'] == 'observe_return':
                    require(not self._observed and not self._resumed, 'APPLICATION_HANDOFF_REPLAY_REJECTED')
                    self._observed = True
                    if ledger.has_intent():
                        result = self._occupied(ledger)
                        return result
                    self._owned_page(driver)
                    self._remaining(deadline)
                    observation = driver.observe_entry_return()
                    self._remaining(deadline)
                    self._validate_observation(observation)
                    self._write_once('observation.json', {**observation, 'session_id': self._session_id})
                    continue
                require(self._observed and not self._resumed, 'APPLICATION_HANDOFF_REPLAY_REJECTED')
                self._resumed = True
                require(all(type(command[key]) is str and _HEX64.fullmatch(command[key])
                            for key in ('manifest_sha256', 'facts_sha256')), 'APPLICATION_HANDOFF_PINS_REQUIRED')
                reviewed, reviewed_facts = load_reviewed_application_inputs(
                    self._directory / 'reviewed-manifest.json', self._directory / 'reviewed-facts.json',
                    manifest_sha256=command['manifest_sha256'], facts_sha256=command['facts_sha256'],
                    source_sha=self._source_sha, now=datetime.now(timezone.utc))
                require(_review_scope(reviewed) == scope, 'APPLICATION_HANDOFF_SCOPE_CHANGED')
                require(reviewed.get('entry_return_review', {}).get('observation_id') == observation['observation_id'],
                        'APPLICATION_HANDOFF_OBSERVATION_REJECTED')
                self._source_clean(deadline)
                if ledger.has_intent():
                    result = self._occupied(ledger)
                    return result
                self._owned_page(driver)
                self._remaining(deadline)
                result = runtime.resume_reviewed_return(reviewed, reviewed_facts, target, cv_path,
                    observation_id=observation['observation_id'], authority=authority)
                return result
            result = {'status': 'HANDOFF_TIMED_OUT', 'applied': False, 'submit_click_attempts': 0}
            return result
        except _HandoffDeadlineExceeded:
            result = {'status': 'HANDOFF_TIMED_OUT', 'applied': False, 'submit_click_attempts': 0}
            return result
        except KeyboardInterrupt:
            occupied = True
            try:
                occupied = ledger.has_intent()
            except Exception:
                pass
            result = (self._occupied(ledger) if occupied else
                      {'status': 'HANDOFF_CANCELLED', 'applied': False, 'submit_click_attempts': 0})
            return result
        except Exception:
            occupied = self._resumed
            if self._resumed:
                try:
                    occupied = ledger.has_intent()
                except Exception:
                    pass
            result = (self._occupied(ledger) if occupied else
                      {'status': 'BLOCKED', 'applied': False, 'submit_click_attempts': 0,
                       'error_code': 'APPLICATION_HANDOFF_REVIEW_REQUIRED'})
            return result
        finally:
            # A local cleanup failure cannot replace an authenticated employer
            # result, obscure an uncertain dispatch, or reopen its ledger slot.
            try:
                cleaned = self._cleanup()
            except (Exception, KeyboardInterrupt):
                cleaned = False
            if not cleaned and result is not None:
                result['handoff_cleanup_incomplete'] = True
