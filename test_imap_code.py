"""IMAP polling must leave unrelated unread messages untouched."""

from email.message import EmailMessage
from email.utils import format_datetime
from datetime import datetime, timedelta, timezone
import os
import unittest
from unittest.mock import patch

from agent import login_core


def make_message(sender, code, age=timedelta()):
    message = EmailMessage()
    message["From"] = sender
    message["Date"] = format_datetime(datetime.now(timezone.utc) - age)
    message["Subject"] = "Verification"
    message.set_content("Your verification code: " + code)
    return message.as_bytes()


class ImapCodeTests(unittest.TestCase):
    def test_only_matching_message_is_marked_read(self):
        code = "a1b2c3d4e5f60718293a4b5c"

        class FakeImap:
            def __init__(self):
                self.fetches = []
                self.stores = []

            def login(self, user, password):
                return "OK", []

            def select(self, folder):
                return "OK", []

            def search(self, *args):
                return "OK", [b"1 2 3"]

            def fetch(self, message_id, part):
                self.fetches.append((message_id, part))
                sender = "other@example.com" if message_id == b"2" else "notice@nodeseek.com"
                age = timedelta(days=1) if message_id == b"3" else timedelta()
                return "OK", [(b"", make_message(sender, code, age))]

            def store(self, *args):
                self.stores.append(args)

            def logout(self):
                return "BYE", []

        mailbox = FakeImap()
        settings = {
            "NS_IMAP_HOST": "imap.example.invalid",
            "NS_IMAP_PORT": "993",
            "NS_IMAP_USER": "user@example.invalid",
            "NS_IMAP_PASS": "app-password",
            "NS_IMAP_SENDER": "nodeseek",
        }
        with patch.dict(os.environ, settings), \
                patch("imaplib.IMAP4_SSL", return_value=mailbox):
            self.assertEqual(login_core._poll_code_imap(5), code)

        self.assertEqual(mailbox.fetches, [
            (b"3", "(BODY.PEEK[])"), (b"2", "(BODY.PEEK[])"),
            (b"1", "(BODY.PEEK[])"),
        ])
        self.assertEqual(mailbox.stores, [(b"1", "+FLAGS", "\\Seen")])


if __name__ == "__main__":
    unittest.main()
