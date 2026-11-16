"""Focused checks for the isolated login handoff and node selection."""

import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import ns_signer_adapter
import ns_signer_worker


class LoginHandoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = {
            "NS_SIGNER_PYTHON": "/python",
            "NS_SIGNER_PACKAGE": "/package",
            "NS_SIGNER_DATA_DIR": self.tmp.name,
            "NS_SIGNER_BROWSERS_PATH": "/browsers",
            "NS_SIGNER_NODES_FILE": "/nodes",
            "NS_SIGNER_CONTROLLER_SECRET_FILE": "/secret",
            "LOGIN_PROXY": "http://127.0.0.1:7893",
        }
        self.env_patch = patch.dict(os.environ, env)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        core = SimpleNamespace(
            AUTH_COOKIE_NAMES=("pjwt", "session", "smac"),
            cookie_expiry_info=lambda cookies: {},
            cookies_to_string=lambda cookies: "; ".join(c["name"] + "=" + c["value"] for c in cookies),
            describe_cookie_life=lambda cookies: "valid",
        )
        self.core_patch = patch.dict(sys.modules, {"login_core": core})
        self.core_patch.start()
        self.addCleanup(self.core_patch.stop)

    def login(self, saved_at, cookies):
        Path(self.tmp.name, "session.json").write_text(json.dumps({
            "saved_at": saved_at,
            "cookies": cookies,
            "tokens": {"security_token": "token"},
        }))
        with patch.object(ns_signer_adapter, "_run_worker", return_value=(0, '{"success": true}')):
            return ns_signer_adapter.login_detail("user", "password")

    def test_accepts_new_authenticated_session(self):
        result = self.login(time.time() + 1, [{
            "name": "session", "value": "value", "domain": ".nodeseek.com",
            "expires": time.time() + 86400,
        }])
        self.assertEqual(result.get("cookie"), "session=value")

    def test_rejects_old_or_unusable_session(self):
        valid_cookie = {"name": "session", "value": "value", "domain": ".nodeseek.com"}
        self.assertEqual(self.login(time.time() - 30, [valid_cookie])["error"], "NS_SIGNER_SESSION")
        self.assertEqual(self.login(time.time() + 1, [{**valid_cookie, "expires": time.time() - 1}])["error"], "NS_SIGNER_SESSION")
        self.assertEqual(self.login(time.time() + 1, [{**valid_cookie, "domain": "fake-nodeseek.com"}])["error"], "NS_SIGNER_SESSION")


class NodeSelectionTests(unittest.TestCase):
    def test_prefers_configured_nodes_and_limits_attempts(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory, "nodes.json")
            file.write_text(json.dumps(["Taiwan", "US", "Taiwan", "missing", "HK", "Japan"]))
            with patch.dict(os.environ, {"NS_SIGNER_NODES_FILE": str(file)}), patch.object(
                ns_signer_worker, "_controller_request", return_value={"all": ["Taiwan", "US", "HK", "Japan"], "now": "Japan"}
            ):
                self.assertEqual(ns_signer_worker._candidate_nodes(), ["Taiwan", "US", "HK"])

    def test_switches_only_for_page_or_turnstile_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            email_code = SimpleNamespace(extract_code=lambda text: "", wait_for_code=lambda *a: "")
            login = SimpleNamespace(login_with_outcome=Mock(side_effect=[
                (None, "no-turnstile"), (None, "bad-credentials"),
            ]))
            package = SimpleNamespace(email_code=email_code, login=login)
            controller = Mock()
            with patch.dict(sys.modules, {
                "ns_signer": package,
                "ns_signer.config": SimpleNamespace(get_config=lambda: {}),
            }), patch.dict(os.environ, {
                "DATA_DIR": directory, "LOGIN_PROXY": "http://127.0.0.1:7893",
            }), patch.object(ns_signer_worker, "_candidate_nodes", return_value=["Taiwan", "US", "HK"]), patch.object(
                ns_signer_worker, "_controller_request", controller
            ), patch.object(ns_signer_worker, "_install_mail_compatibility"), patch.object(
                ns_signer_worker.logging, "disable"
            ), contextlib.redirect_stdout(io.StringIO()):
                ns_signer_worker.main()
            self.assertEqual([call.args[1]["name"] for call in controller.call_args_list], ["Taiwan", "US"])
            self.assertEqual(login.login_with_outcome.call_count, 2)


class WorkerTimeoutTests(unittest.TestCase):
    def test_kills_browser_process_group_on_timeout(self):
        process = Mock(pid=1234)
        process.communicate.side_effect = [
            ns_signer_adapter.subprocess.TimeoutExpired("worker", 1),
            ("", ""),
        ]
        with patch.object(ns_signer_adapter.subprocess, "Popen", return_value=process), patch.object(
            ns_signer_adapter.os, "killpg"
        ) as killpg:
            with self.assertRaises(ns_signer_adapter.subprocess.TimeoutExpired):
                ns_signer_adapter._run_worker("python", Path("worker.py"), {}, 1)
        killpg.assert_called_once_with(1234, ns_signer_adapter.signal.SIGKILL)
        self.assertEqual(process.communicate.call_count, 2)


if __name__ == "__main__":
    unittest.main()
