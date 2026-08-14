from __future__ import annotations

import html
import os
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

from config_loader import AppConfig, ConfigError


class NotificationError(RuntimeError):
    """Raised when an email cannot be constructed or delivered."""


class QQMailNotifier:
    SMTP_HOST = "smtp.qq.com"
    SMTP_PORT = 465
    IMAGE_CID = "dividend-yield-report"

    def __init__(self, address: str, auth_code: str) -> None:
        self.address = address.strip()
        self.auth_code = "".join(auth_code.split())

    def send(
        self,
        title: str,
        markdown_path: str | Path,
        image_path: str | Path,
    ) -> None:
        markdown_file = Path(markdown_path)
        image_file = Path(image_path)
        try:
            markdown_text = markdown_file.read_text(encoding="utf-8")
            image_data = image_file.read_bytes()
        except OSError as exc:
            raise NotificationError(f"无法读取邮件附件: {exc}") from exc

        message = self._build_message(
            title=title,
            markdown_text=markdown_text,
            markdown_filename=markdown_file.name,
            image_data=image_data,
            image_filename=image_file.name,
        )
        try:
            with smtplib.SMTP_SSL(
                self.SMTP_HOST,
                self.SMTP_PORT,
                context=ssl.create_default_context(),
                timeout=15,
            ) as smtp:
                smtp.login(self.address, self.auth_code)
                refused = smtp.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            raise NotificationError(f"QQ 邮件发送失败: {exc}") from exc
        if refused:
            raise NotificationError(f"QQ 邮箱拒收收件人: {refused}")

    def _build_message(
        self,
        title: str,
        markdown_text: str,
        markdown_filename: str,
        image_data: bytes,
        image_filename: str,
    ) -> EmailMessage:
        message = EmailMessage()
        message["Subject"] = title
        message["From"] = self.address
        message["To"] = self.address
        message.set_content(markdown_text)

        escaped_title = html.escape(title)
        message.add_alternative(
            "<html><body style=\"font-family: sans-serif;\">"
            f"<h2>{escaped_title}</h2>"
            f'<img src="cid:{self.IMAGE_CID}" alt="{escaped_title}" '
            'style="max-width: 100%; height: auto;">'
            "<p style=\"color:#8a5b14;font-size:12px;\">"
            "本报告中的分档仓位是股息率模型信号，不等于真实持仓，不构成完整投资建议。"
            "</p></body></html>",
            subtype="html",
        )
        html_part = message.get_payload()[-1]
        html_part.add_related(
            image_data,
            maintype="image",
            subtype="png",
            cid=f"<{self.IMAGE_CID}>",
            filename=image_filename,
            disposition="inline",
        )
        message.add_attachment(
            markdown_text,
            subtype="markdown",
            filename=markdown_filename,
        )
        return message


def build_notifier(config: AppConfig) -> QQMailNotifier:
    address = os.environ.get(config.notifier.address_env, "").strip()
    auth_code = os.environ.get(config.notifier.auth_code_env, "")
    if not address or not auth_code.strip():
        raise ConfigError(
            "缺少 QQ 邮箱环境变量 "
            f"{config.notifier.address_env} / {config.notifier.auth_code_env}"
        )
    return QQMailNotifier(address, auth_code)
