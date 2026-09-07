from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import socket
import unittest

from ejs.contracts.google_native import (
    ExecutionAuthority,
    ExecutionEnvironment,
    ExecutionOperation,
    GoogleNativeExecutionRequest,
)
from ejs.services.google_native_executor import GoogleNativeExecutor
from ejs.services.url_policy import PublicHttpsUrlPolicy, sanitize_external_url


class GoogleNativeContractTests(unittest.TestCase):
    def base(self, **kwargs):
        req = GoogleNativeExecutionRequest(
            request_id="req:test:001",
            execution_id="exec:test:001",
            operation=ExecutionOperation.ECHO,
            environment=ExecutionEnvironment.TEST,
            trace={"test": "gd002"},
        )
        return replace(req, **kwargs)

    def test_test_environment_only(self):
        req = self.base()
        object.__setattr__(req, "environment", "PROD")
        with self.assertRaises(PermissionError):
            req.validate()

    def test_write_authorities_forbidden(self):
        with self.assertRaises(PermissionError):
            self.base(authority=ExecutionAuthority(form_write=True)).validate()
        with self.assertRaises(PermissionError):
            self.base(authority=ExecutionAuthority(file_upload=True)).validate()
        with self.assertRaises(PermissionError):
            self.base(authority=ExecutionAuthority(final_submit=True)).validate()

    def test_inspect_requires_target(self):
        with self.assertRaises(ValueError):
            self.base(operation=ExecutionOperation.INSPECT_READ_ONLY).validate()

    def test_fixture_rejects_caller_target(self):
        with self.assertRaises(ValueError):
            self.base(operation=ExecutionOperation.INSPECT_FIXTURE, target_url="https://example.com").validate()

    def test_sensitive_trace_keys_rejected(self):
        with self.assertRaises(ValueError):
            self.base(trace={"token": "x"}).validate()


class UrlPolicyTests(unittest.TestCase):
    def test_https_only(self):
        self.assertFalse(PublicHttpsUrlPolicy.static_validate("http://example.com").allowed)
        self.assertTrue(PublicHttpsUrlPolicy.static_validate("https://example.com").allowed)

    def test_private_ip_literals_rejected(self):
        for url in [
            "https://127.0.0.1/", "https://10.0.0.1/", "https://169.254.169.254/", "https://[::1]/"
        ]:
            self.assertFalse(PublicHttpsUrlPolicy.static_validate(url).allowed, url)

    def test_metadata_and_internal_hosts_rejected(self):
        for url in [
            "https://metadata.google.internal/", "https://localhost/", "https://foo.local/", "https://svc.internal/"
        ]:
            self.assertFalse(PublicHttpsUrlPolicy.static_validate(url).allowed, url)

    def test_nonstandard_port_rejected(self):
        self.assertFalse(PublicHttpsUrlPolicy.static_validate("https://example.com:8443/").allowed)

    def test_dns_private_resolution_rejected(self):
        def resolver(*args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", 443))]
        decision = PublicHttpsUrlPolicy.validate_resolved("https://example.com", resolver=resolver)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "DNS_NON_PUBLIC_IP")

    def test_dns_public_resolution_allowed(self):
        def resolver(*args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        self.assertTrue(PublicHttpsUrlPolicy.validate_resolved("https://example.com", resolver=resolver).allowed)

    def test_url_sanitizer_removes_query_fragment_userinfo(self):
        self.assertEqual(sanitize_external_url("https://user:pw@example.com/a?token=x#z"), "https://example.com/a")


class GoogleNativeExecutorTests(unittest.TestCase):
    def test_echo_has_zero_mutations(self):
        result = GoogleNativeExecutor().execute(GoogleNativeExecutionRequest(
            request_id="req:echo", execution_id="exec:echo", operation=ExecutionOperation.ECHO
        ))
        self.assertEqual(result.runtime_state, "echo")
        self.assertEqual(result.mutation_attempts, 0)
        self.assertEqual(result.file_upload_attempts, 0)
        self.assertEqual(result.submit_attempts, 0)
        self.assertTrue(result.read_only_invariant_ok)

    def test_health_declares_write_boundaries_off(self):
        h = GoogleNativeExecutor().health()
        self.assertTrue(h["browserRead"])
        self.assertFalse(h["formWrite"])
        self.assertFalse(h["fileUpload"])
        self.assertFalse(h["finalSubmit"])


class AppsScriptSourceContractTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[2] / "google_native" / "apps_script"

    def test_manifest_scopes_include_cloud_platform_without_key_material(self):
        text = (self.ROOT / "appsscript.json").read_text()
        self.assertIn("cloud-platform", text)
        combined = "\n".join(p.read_text() for p in self.ROOT.glob("*.gs"))
        self.assertNotIn("BEGIN PRIVATE KEY", combined)

    def test_test_id_and_prod_deny_id_present(self):
        text = (self.ROOT / "EjsConfigV1.gs").read_text()
        self.assertIn("17dBVTbUQrjpWN5eyQkIfvwyzmMDFyYhOm1Ivxcge8nM", text)
        self.assertIn("1_HuYScTMsVmr29SBeaMRbZqOFiZ3gZLUcFm05xnaYIg", text)

    def test_id_token_impersonation_uses_iamcredentials_without_key(self):
        text = (self.ROOT / "EjsCloudRunClientV1.gs").read_text()
        self.assertIn("iamcredentials.googleapis.com", text)
        self.assertIn("generateIdToken", text)
        self.assertIn("ScriptApp.getOAuthToken()", text)
        self.assertNotIn("private_key", text.lower())

    def test_idempotency_uses_script_lock_and_collision_guard(self):
        text = (self.ROOT / "EjsIdempotencyV1.gs").read_text()
        self.assertIn("LockService.getScriptLock()", text)
        self.assertIn("EJS_REQUEST_ID_PAYLOAD_COLLISION", text)
        self.assertIn("exact_retry", text)


if __name__ == "__main__":
    unittest.main()
