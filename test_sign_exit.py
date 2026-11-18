"""The cron wrapper must see a failure when signing did not succeed."""

import contextlib
import io
import os
import unittest
from unittest.mock import patch

import multi_site_sign_cookie_only as sign_module


class SignExitTests(unittest.TestCase):
    def test_nodeseek_uses_browser_proxy_for_http_requests(self):
        proxy = "http://user:password@example.invalid:44445"
        with patch.dict(os.environ, {"AGENT_PROXY": proxy}):
            ns = sign_module.create_session(
                "session=existing", sign_module.SITES_CONFIG["nodeseek"]
            )
            df = sign_module.create_session(
                "session=existing", sign_module.SITES_CONFIG["deepflood"]
            )
        self.assertEqual(ns.proxies, {"http": proxy, "https": proxy})
        self.assertEqual(df.proxies, {})
        self.assertEqual(ns.cookies.get("session"), "existing")

    def test_proxy_credentials_are_removed_from_network_errors(self):
        proxy = "http://user:password@example.invalid:44445"
        with patch.dict(os.environ, {"AGENT_PROXY": proxy}):
            message = sign_module.safe_network_error(
                RuntimeError("request via " + proxy + " failed for user:password")
            )
        self.assertNotIn("password", message)
        self.assertNotIn("user", message)

    def test_proxy_network_error_does_not_trigger_login(self):
        self.assertFalse(sign_module.sign_failure_should_recover(
            "error", "Proxy CONNECT failed"
        ))
        self.assertTrue(sign_module.sign_failure_should_recover(
            "invalid", "Cookie expired"
        ))

    def test_nodeseek_uses_fallback_only_for_network_or_waf_failure(self):
        site = sign_module.SITES_CONFIG["nodeseek"]
        cases = [
            (("error", "ConnectionError"), 2),
            (("fail", "被WAF拦截"), 2),
            (("fail", "未知响应 (Code 403)"), 2),
            (("already", "今天已完成签到"), 1),
            (("invalid", "Cookie expired"), 1),
        ]
        for first, calls in cases:
            with self.subTest(first=first), patch.dict(os.environ, {"NS_HTTP_FALLBACK_PROXY": "http://127.0.0.1:7893"}), \
                    patch.object(sign_module, "_sign_once", side_effect=[first, ("already", "已签到")]) as once:
                sign_module.sign("session=existing", site, "true")
                self.assertEqual(once.call_count, calls)
                if calls == 2:
                    self.assertEqual(once.call_args.kwargs["proxy_override"], "http://127.0.0.1:7893")

    def test_deepflood_does_not_use_nodeseek_fallback(self):
        with patch.dict(os.environ, {"NS_HTTP_FALLBACK_PROXY": "http://127.0.0.1:7893"}), \
                patch.object(sign_module, "_sign_once", return_value=("error", "network")) as once:
            sign_module.sign("session=existing", sign_module.SITES_CONFIG["deepflood"], "true")
        once.assert_called_once()

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

    def test_telegram_delivery_failure_is_reported(self):
        with patch.dict(os.environ, {"TG_BOT_TOKEN": "test", "TG_USER_ID": "123"}), \
                patch.object(sign_module, "should_send_notification", return_value=True):
            self.assertEqual(self.run_main([{"status": "success"}]), 1)

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
