"""SMTP send / IMAP read helpers for the engine's `send_email` and
`read_emails` actions. Kept separate from engine.py so they're independently
unit-testable (mocking smtplib/imaplib) without going through the engine."""

from __future__ import annotations

from email.header import decode_header
from email.message import EmailMessage
from typing import Any


def send_email(
    smtp_host: str,
    username: str,
    password: str,
    to: str,
    subject: str,
    body: str,
    smtp_port: int = 587,
    use_tls: bool = True,
    from_addr: str | None = None,
) -> None:
    import smtplib

    message = EmailMessage()
    message["From"] = from_addr or username
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    with smtplib.SMTP(smtp_host, int(smtp_port), timeout=30) as server:
        if use_tls:
            server.starttls()
        server.login(username, password)
        server.send_message(message)


def _decode(value: str | None) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    return "".join(
        part.decode(encoding or "utf-8", errors="replace") if isinstance(part, bytes) else part
        for part, encoding in parts
    )


def _extract_body(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get("Content-Disposition"):
                payload = part.get_payload(decode=True) or b""
                return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        return ""
    payload = msg.get_payload(decode=True) or b""
    return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")


def read_emails(
    imap_host: str,
    username: str,
    password: str,
    folder: str = "INBOX",
    limit: int = 10,
    unseen_only: bool = True,
    use_ssl: bool = True,
) -> list[dict[str, Any]]:
    import email as email_lib
    import imaplib

    client_cls = imaplib.IMAP4_SSL if use_ssl else imaplib.IMAP4
    client = client_cls(imap_host)
    try:
        client.login(username, password)
        client.select(folder)
        criterion = "UNSEEN" if unseen_only else "ALL"
        status, data = client.search(None, criterion)
        if status != "OK":
            return []
        ids = data[0].split()
        ids = ids[-int(limit) :] if limit else ids

        messages = []
        for msg_id in reversed(ids):
            status, msg_data = client.fetch(msg_id, "(RFC822)")
            if status != "OK" or not msg_data or msg_data[0] is None:
                continue
            raw = msg_data[0][1]
            msg = email_lib.message_from_bytes(raw)
            messages.append(
                {
                    "subject": _decode(msg.get("Subject")),
                    "from": _decode(msg.get("From")),
                    "date": msg.get("Date", ""),
                    "body": _extract_body(msg),
                }
            )
        return messages
    finally:
        try:
            client.logout()
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass
