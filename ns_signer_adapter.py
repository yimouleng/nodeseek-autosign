"""Map a verified ns-signer session into this project's login result."""

import json
import os
from pathlib import Path
import signal
import subprocess
import time


def enabled():
    return bool(os.getenv("NS_SIGNER_PYTHON", "").strip())


def _run_worker(python, worker, env, timeout):
    process = subprocess.Popen(
        [python, str(worker)], env=env, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, start_new_session=True,
    )
    try:
        stdout, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()
        raise
    return process.returncode, stdout


def login_detail(user, password):
    from login_core import (
        AUTH_COOKIE_NAMES, cookie_expiry_info, cookies_to_string,
        describe_cookie_life,
    )

    python = os.getenv("NS_SIGNER_PYTHON", "").strip()
    package = os.getenv("NS_SIGNER_PACKAGE", "").strip()
    data_dir = os.getenv("NS_SIGNER_DATA_DIR", "").strip()
    browsers = os.getenv("NS_SIGNER_BROWSERS_PATH", "").strip()
    nodes = os.getenv("NS_SIGNER_NODES_FILE", "").strip()
    secret = os.getenv("NS_SIGNER_CONTROLLER_SECRET_FILE", "").strip()
    proxy = os.getenv("LOGIN_PROXY", "").strip()
    paths = (python, package, data_dir, browsers, nodes, secret)
    if not all(paths) or not proxy:
        return {"error": "NS_SIGNER_CONFIG", "message": "续期浏览器或代理配置不完整"}

    env = os.environ.copy()
    env.update({
        "NODESEEK_USERNAME": user,
        "NODESEEK_PASSWORD": password,
        "EMAIL_ADDRESS": os.getenv("NS_IMAP_USER", ""),
        "IMAP_HOST": os.getenv("NS_IMAP_HOST", ""),
        "IMAP_PORT": os.getenv("NS_IMAP_PORT", "993"),
        "IMAP_USERNAME": os.getenv("NS_IMAP_USER", ""),
        "IMAP_PASSWORD": os.getenv("NS_IMAP_PASS", ""),
        "PROXY": proxy,
        "LOGIN_PROXY": proxy,
        "DATA_DIR": data_dir,
        "HEADLESS": "true",
        "PLAYWRIGHT_BROWSERS_PATH": browsers,
        "NS_SIGNER_NODES_FILE": nodes,
        "NS_SIGNER_CONTROLLER_SECRET_FILE": secret,
        "PYTHONPATH": package + os.pathsep + env.get("PYTHONPATH", ""),
    })
    worker = Path(__file__).with_name("ns_signer_worker.py")
    started = time.time()
    try:
        returncode, stdout = _run_worker(
            python, worker, env, int(os.getenv("NS_SIGNER_TIMEOUT", "900")),
        )
        summary = json.loads(stdout.strip().splitlines()[-1])
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError, json.JSONDecodeError):
        return {"error": "NS_SIGNER_RUNTIME", "message": "外部登录程序未返回可用结果"}
    if returncode != 0 or not summary.get("success"):
        outcome = str(summary.get("outcome", "unknown"))
        return {"error": "NS_SIGNER_LOGIN", "message": outcome}

    try:
        state = json.loads((Path(data_dir) / "session.json").read_text())
        if not isinstance(state.get("saved_at"), (int, float)) or state["saved_at"] < started:
            raise ValueError("stale session")
        cookies = state["cookies"]
        tokens = state["tokens"]
        site_cookies = [
            item for item in cookies
            if item.get("domain", "").lstrip(".") in ("nodeseek.com", "www.nodeseek.com")
        ]
        auth = [item for item in site_cookies
                if item.get("name") in AUTH_COOKIE_NAMES and item.get("value")
                and (item.get("expires", -1) <= 0 or item["expires"] > time.time())]
        if not {item["name"] for item in auth}.intersection({"pjwt", "session"}):
            raise ValueError("missing auth cookie")
        if not tokens.get("security_token"):
            raise ValueError("missing security token")
        cookie = cookies_to_string(site_cookies)
        return {
            "cookie": cookie,
            "expiry": cookie_expiry_info(site_cookies),
            "cookie_life": describe_cookie_life(site_cookies),
            "device_memory_expired": False,
            "needs_email_verify": bool(summary.get("email_used")),
        }
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return {"error": "NS_SIGNER_SESSION", "message": "新会话未通过完整性检查"}
