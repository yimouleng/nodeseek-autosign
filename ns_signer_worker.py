"""Run the optional ns-signer login in its isolated Python environment."""

import contextlib
import fcntl
import imaplib
import io
import json
import logging
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request


RETRYABLE_OUTCOMES = {"no-page", "no-turnstile", "blocked", "unverified", "error"}


def _controller_request(method, payload=None):
    token = Path(os.environ["NS_SIGNER_CONTROLLER_SECRET_FILE"]).read_text().strip()
    request = urllib.request.Request(
        "http://127.0.0.1:9090/proxies/NODESEEK",
        data=json.dumps(payload).encode() if payload else None,
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        },
        method=method,
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=8) as response:
        return json.load(response) if method == "GET" else None


def _candidate_nodes():
    configured = json.loads(Path(os.environ["NS_SIGNER_NODES_FILE"]).read_text())
    if not isinstance(configured, list):
        raise ValueError("node list must be an array")
    group = _controller_request("GET")
    available = set(group.get("all", []))
    nodes = [node for node in dict.fromkeys(configured) if node in available]
    current = group.get("now")
    if current in nodes:
        index = nodes.index(current)
        nodes = nodes[index:] + nodes[:index]
    return nodes


def _node_reachable(node):
    token = Path(os.environ["NS_SIGNER_CONTROLLER_SECRET_FILE"]).read_text().strip()
    query = urllib.parse.urlencode({"timeout": 5000, "url": "https://www.nodeseek.com/"})
    url = "http://127.0.0.1:9090/proxies/" + urllib.parse.quote(node, safe="") + "/delay?" + query
    request = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=7) as response:
            return bool(json.load(response).get("delay"))
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise
        return False
    except urllib.error.URLError:
        return False


def _install_mail_compatibility(email_code):
    original_extract = email_code.extract_code

    def extract_code(text):
        match = re.search(r"\b[a-f0-9]{24}\b", text, re.IGNORECASE)
        return match.group(0) if match else original_extract(text)

    email_code.extract_code = extract_code

    original_fetch = imaplib.IMAP4_SSL.fetch

    def peek_fetch(self, message_id, query):
        part = "(BODY.PEEK[])" if query == "(RFC822)" else query
        return original_fetch(self, message_id, part)

    imaplib.IMAP4_SSL.fetch = peek_fetch


def main():
    os.umask(0o077)
    data_dir = Path(os.environ["DATA_DIR"])
    data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    data_dir.chmod(0o700)

    from ns_signer import email_code, login
    from ns_signer.config import get_config

    _install_mail_compatibility(email_code)
    config = get_config()
    config["login_retries"] = int(os.getenv("NS_SIGNER_RETRIES_PER_NODE", "2"))
    config["login_attempt_gap"] = 20
    config["proxy"] = os.environ["LOGIN_PROXY"]

    email_used = False
    original_wait = email_code.wait_for_code

    def wait_for_code(*args, **kwargs):
        nonlocal email_used
        code = original_wait(*args, **kwargs)
        email_used = email_used or bool(code)
        return code

    email_code.wait_for_code = wait_for_code
    logging.disable(logging.CRITICAL)

    with (data_dir / "renew.lock").open("w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"success": False, "outcome": "busy"}))
            return

        outcome = "no-reachable-node"
        for node in _candidate_nodes():
            _controller_request("PUT", {"name": node})
            if not _node_reachable(node):
                continue
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                cookies, outcome = login.login_with_outcome(config)
            if cookies:
                print(json.dumps({
                    "success": True,
                    "outcome": "success",
                    "email_used": email_used,
                    "node": node,
                }, ensure_ascii=False))
                return
            if outcome not in RETRYABLE_OUTCOMES:
                break
        print(json.dumps({"success": False, "outcome": outcome}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"success": False, "outcome": type(exc).__name__}))
        sys.exit(1)
