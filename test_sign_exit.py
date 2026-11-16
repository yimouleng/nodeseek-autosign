"""The cron wrapper must see a failure when signing did not succeed."""

import contextlib
import io
import unittest
from unittest.mock import patch

import multi_site_sign_cookie_only as sign_module


class SignExitTests(unittest.TestCase):
    def run_main(self, results=None, error=None):
        with patch.object(sign_module, "SITES_CONFIG", {"nodeseek": {"name": "NodeSeek"}}):
            with patch.object(sign_module, "process_site", side_effect=error) if error else \
                    patch.object(sign_module, "process_site", return_value=results):
                with contextlib.redirect_stdout(io.StringIO()):
                    return sign_module.main()

    def test_success_and_already_signed_are_ok(self):
        for status in ("success", "already"):
            with self.subTest(status=status):
                self.assertEqual(self.run_main([{"status": status}]), 0)

    def test_account_failure_is_reported(self):
        self.assertEqual(self.run_main([{"status": "fail"}]), 1)

    def test_site_exception_is_reported(self):
        self.assertEqual(self.run_main(error=RuntimeError("login failed")), 1)

    def test_no_configured_accounts_is_reported(self):
        self.assertEqual(self.run_main([]), 1)

    def test_signing_precedes_failed_renewal(self):
        calls = []

        def sign(*args):
            calls.append("sign")
            return "success", "signed"

        def recover(*args):
            calls.append("renew")
            return None, {}

        replacements = {
            "get_accounts": lambda config: [{"user": "user", "pwd": "password"}],
            "load_account_cookie": lambda *args: "session=existing",
            "load_cookie_meta": lambda *args: {"expiry": {"session": {"days_left": 4}}},
            "check_cookie_validity": lambda *args: True,
            "sign": sign,
            "get_stats": lambda *args: None,
            "should_renew_cookie": lambda *args: True,
            "recover_cookie": recover,
            "should_send_notification": lambda *args: False,
        }
        with contextlib.ExitStack() as stack:
            for name, replacement in replacements.items():
                stack.enter_context(patch.object(sign_module, name, replacement))
            with contextlib.redirect_stdout(io.StringIO()):
                results = sign_module.process_site(
                    "nodeseek", sign_module.SITES_CONFIG["nodeseek"], "true"
                )
        self.assertEqual(calls, ["sign", "renew"])
        self.assertEqual(results[0]["status"], "success")

    def test_stats_server_errors_do_not_invalidate_cookie(self):
        class Response:
            def __init__(self, status_code):
                self.status_code = status_code

        class Session:
            def __init__(self, status_code):
                self.status_code = status_code

            def get(self, *args, **kwargs):
                return Response(self.status_code)

        for status_code in (404, 500):
            with self.subTest(status_code=status_code):
                with patch.object(sign_module, "create_session", return_value=Session(status_code)), \
                        patch.object(sign_module, "response_looks_like_waf", return_value=False), \
                        patch.object(sign_module.time, "sleep"), \
                        contextlib.redirect_stdout(io.StringIO()):
                    result = sign_module.check_cookie_validity(
                        sign_module.SITES_CONFIG["nodeseek"], "session=existing", retries=2
                    )
                self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
