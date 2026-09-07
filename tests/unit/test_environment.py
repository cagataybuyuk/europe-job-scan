import unittest

from ejs.domain.environment import Environment, RuntimePolicy


class RuntimePolicyTests(unittest.TestCase):
    def test_nonprod_cannot_write_prod(self):
        policy = RuntimePolicy(
            environment=Environment.STAGING_SHADOW,
            production_spreadsheet_id="prod",
            configured_spreadsheet_id="prod",
            allow_business_writes=True,
        )
        with self.assertRaises(PermissionError):
            policy.validate()

    def test_shadow_read_only_is_allowed(self):
        policy = RuntimePolicy(
            environment=Environment.STAGING_SHADOW,
            production_spreadsheet_id="prod",
            configured_spreadsheet_id="prod",
            allow_business_writes=False,
        )
        policy.validate()

    def test_external_form_write_is_blocked(self):
        policy = RuntimePolicy(
            environment=Environment.PRODUCTION,
            production_spreadsheet_id="prod",
            configured_spreadsheet_id="prod",
            allow_external_form_writes=True,
        )
        with self.assertRaises(PermissionError):
            policy.validate()


if __name__ == "__main__":
    unittest.main()
