import os
import smtplib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config_loader import AppConfig, ConfigError, NotifierConfig
from notifier import NotificationError, QQMailNotifier, build_notifier


class QQMailNotifierTest(unittest.TestCase):
    def _files(self, root):
        markdown = Path(root) / "日报.md"
        image = Path(root) / "日报.png"
        markdown.write_text("# 日报\n\n完整内容", encoding="utf-8")
        image.write_bytes(b"PNG DATA")
        return markdown, image

    def test_message_contains_inline_png_markdown_attachment_and_plain_fallback(self):
        notifier = QQMailNotifier("123456@qq.com", "abcd efgh")
        message = notifier._build_message(
            "A股股息率日报",
            "# 完整日报",
            "日报.md",
            b"PNG DATA",
            "日报.png",
        )

        parts = list(message.walk())
        plain_parts = [part for part in parts if part.get_content_type() == "text/plain"]
        html_parts = [part for part in parts if part.get_content_type() == "text/html"]
        image_parts = [part for part in parts if part.get_content_type() == "image/png"]
        markdown_parts = [part for part in parts if part.get_content_type() == "text/markdown"]
        self.assertIn("# 完整日报", plain_parts[0].get_content())
        self.assertIn("cid:dividend-yield-report", html_parts[0].get_content())
        self.assertEqual(image_parts[0]["Content-ID"], "<dividend-yield-report>")
        self.assertEqual(image_parts[0].get_content_disposition(), "inline")
        self.assertEqual(markdown_parts[0].get_filename(), "日报.md")
        self.assertEqual(markdown_parts[0].get_content_disposition(), "attachment")

    @patch("notifier.smtplib.SMTP_SSL")
    def test_send_uses_qq_ssl_and_strips_auth_code_whitespace(self, smtp_ssl):
        smtp = smtp_ssl.return_value.__enter__.return_value
        smtp.send_message.return_value = {}
        notifier = QQMailNotifier("123456@qq.com", "abcd efgh\nijkl")
        with tempfile.TemporaryDirectory() as root:
            markdown, image = self._files(root)
            notifier.send("日报", markdown, image)

        smtp_ssl.assert_called_once()
        self.assertEqual(smtp_ssl.call_args.args, ("smtp.qq.com", 465))
        smtp.login.assert_called_once_with("123456@qq.com", "abcdefghijkl")

    @patch("notifier.smtplib.SMTP_SSL")
    def test_smtp_failure_is_raised(self, smtp_ssl):
        smtp = smtp_ssl.return_value.__enter__.return_value
        smtp.login.side_effect = smtplib.SMTPAuthenticationError(535, b"bad auth")
        notifier = QQMailNotifier("123456@qq.com", "code")
        with tempfile.TemporaryDirectory() as root:
            markdown, image = self._files(root)
            with self.assertRaises(NotificationError):
                notifier.send("日报", markdown, image)

    def test_missing_environment_is_rejected(self):
        config = AppConfig(
            timezone_name="Asia/Shanghai",
            stocks=(),
            notifier=NotifierConfig("qqmail", "QQ_EMAIL_ADDRESS", "QQ_EMAIL_AUTH_CODE"),
        )
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ConfigError):
                build_notifier(config)


if __name__ == "__main__":
    unittest.main()
