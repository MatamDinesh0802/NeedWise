"""Outgoing email (SMTP). In development, Mailpit catches everything (http://localhost:8025);
nothing leaves the machine unless SMTP_HOST points at a real server."""

import asyncio
import smtplib
from collections.abc import Sequence
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import make_msgid


class EmailError(Exception):
    pass


@dataclass(frozen=True)
class SentEmail:
    message_id: str
    recipients: list[str]


class SmtpMailer:
    def __init__(
        self,
        host: str,
        port: int,
        *,
        sender: str,
        username: str | None = None,
        password: str | None = None,
        starttls: bool = False,
    ) -> None:
        self._host, self._port = host, port
        self._sender = sender
        self._login = (username, password) if username and password else None
        self._starttls = starttls

    async def send(
        self, to: Sequence[str], subject: str, body: str, *, cc: Sequence[str] = ()
    ) -> SentEmail:
        message = EmailMessage()
        message["From"] = self._sender
        message["To"] = ", ".join(to)
        if cc:
            message["Cc"] = ", ".join(cc)
        message["Subject"] = subject
        message["Message-ID"] = make_msgid(domain=self._sender.split("@")[-1])
        message.set_content(body)
        await asyncio.to_thread(self._deliver, message)
        return SentEmail(str(message["Message-ID"]), [*to, *cc])

    def _deliver(self, message: EmailMessage) -> None:
        try:
            with smtplib.SMTP(self._host, self._port, timeout=30) as smtp:
                if self._starttls:
                    smtp.starttls()
                if self._login:
                    smtp.login(*self._login)
                smtp.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            raise EmailError(f"the mail server refused the message: {exc}") from exc
