"""Telegram delivery and daily reward notification behavior."""

from datetime import datetime, timedelta
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import multi_site_sign_cookie_only as app
import notify


class RewardTests(unittest.TestCase):
    def test_today_reward_comes_from_beijing_date(self):
        today = datetime.now(ZoneInfo("Asia/Shanghai"))
        yesterday = today - timedelta(days=1)
        records = [
            [7, None, "签到", today.isoformat()],
            [2, None, "签到", yesterday.isoformat()],
        ]
        session = Mock()
        session.get.side_effect = [
            Mock(),
            Mock(json=Mock(return_value={"success": True, "data": records})),
            Mock(json=Mock(return_value={"success": True, "data": []})),
        ]
        with patch.object(app, "create_session", return_value=session), patch.object(app.time, "sleep"):
            stats = app.get_stats("session=test", app.SITES_CONFIG["nodeseek"])
        self.assertEqual(stats["today_amount"], 7)
        self.assertEqual(stats["total"], 9)

    def test_message_reward_fallback(self):
        self.assertEqual(app.reward_from_message("今天的签到收益是5个鸡腿"), 5)
        self.assertEqual(app.reward_from_message("今日签到获得鸡腿11个"), 11)
        self.assertIsNone(app.reward_from_message("今天已完成签到"))

    def test_notification_text_and_result_signature(self):
        success = {"user": "user", "status": "success", "msg": "签到成功", "today_amount": 7}
        already = {**success, "status": "already"}
        failed = {**success, "status": "fail", "today_amount": None}
        self.assertIn("今日鸡腿：7 个", app.notification_line(success))
        self.assertIn("无法确认", app.notification_line(failed))
        self.assertEqual(app.notification_signature([success]), app.notification_signature([already]))
        self.assertNotEqual(app.notification_signature([success]), app.notification_signature([failed]))


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.status_file = str(Path(self.directory.name, "notification_status.json"))
        self.file_patch = patch.object(app, "NOTIFICATION_FILE", self.status_file)
        self.file_patch.start()
        self.addCleanup(self.file_patch.stop)

    def test_failure_then_success_sends_both_and_deduplicates_success(self):
        send = Mock(return_value={"telegram_bot": True})
        with patch.dict(os.environ, {"TG_BOT_TOKEN": "test", "TG_USER_ID": "123"}), \
                patch.object(app, "send", send), patch.object(app, "hadsend", True):
            app.notify_site("nodeseek", "失败", "failed", "fail")
            app.notify_site("nodeseek", "成功", "7 个鸡腿", "ok-7")
            app.notify_site("nodeseek", "成功", "7 个鸡腿", "ok-7")
        self.assertEqual(send.call_count, 2)
        self.assertEqual(app.load_notification_status()["nodeseek"]["last_signature"], "ok-7")

    def test_failed_delivery_is_not_marked_as_sent(self):
        send = Mock(return_value={"telegram_bot": False})
        with patch.dict(os.environ, {"TG_BOT_TOKEN": "test", "TG_USER_ID": "123"}), \
                patch.object(app, "send", send), patch.object(app, "hadsend", True):
            app.notify_site("nodeseek", "失败", "failed", "fail")
            app.notify_site("nodeseek", "失败", "failed", "fail")
        self.assertEqual(send.call_count, 2)
        self.assertEqual(app.load_notification_status(), {})

    def test_telegram_uses_post_body_without_empty_thread_id(self):
        response = Mock()
        response.json.return_value = {"ok": True}
        with patch.dict(notify.push_config, {
            "TG_BOT_TOKEN": "test-token", "TG_USER_ID": "123", "TG_THREAD_ID": "",
        }), patch.object(notify.requests, "post", return_value=response) as post:
            self.assertTrue(notify.telegram_bot("签到成功", "今日鸡腿：7 个"))
        payload = post.call_args.kwargs["data"]
        self.assertNotIn("message_thread_id", payload)
        self.assertIn("今日鸡腿：7 个", payload["text"])


if __name__ == "__main__":
    unittest.main()
