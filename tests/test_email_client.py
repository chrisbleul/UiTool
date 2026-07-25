import email.utils

from uiflow.email_client import read_emails, send_email


class _FakeSmtp:
    sent = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        self.tls = True

    def login(self, username, password):
        self.login_args = (username, password)

    def send_message(self, message):
        _FakeSmtp.sent.append(message)


def test_send_email_builds_message_and_logs_in(monkeypatch):
    _FakeSmtp.sent.clear()
    monkeypatch.setattr("smtplib.SMTP", _FakeSmtp)

    send_email(
        smtp_host="smtp.example.com",
        username="bot@example.com",
        password="secret",
        to="user@example.com",
        subject="Hi",
        body="Hello there",
    )

    assert len(_FakeSmtp.sent) == 1
    message = _FakeSmtp.sent[0]
    assert message["To"] == "user@example.com"
    assert message["From"] == "bot@example.com"
    assert message["Subject"] == "Hi"
    assert message.get_content().strip() == "Hello there"


def test_send_email_uses_explicit_from_addr(monkeypatch):
    _FakeSmtp.sent.clear()
    monkeypatch.setattr("smtplib.SMTP", _FakeSmtp)

    send_email(
        smtp_host="smtp.example.com",
        username="bot@example.com",
        password="secret",
        to="user@example.com",
        subject="Hi",
        body="Body",
        from_addr="noreply@example.com",
    )

    assert _FakeSmtp.sent[0]["From"] == "noreply@example.com"


class _FakeImap:
    def __init__(self, host):
        self.host = host
        self.fetch_specs = []

    def login(self, username, password):
        pass

    def select(self, folder):
        pass

    def search(self, charset, criterion):
        return "OK", [b"1 2"]

    def fetch(self, msg_id, spec):
        self.fetch_specs.append(spec)
        raw = (
            f"From: sender@example.com\r\n"
            f"Subject: Test {msg_id.decode()}\r\n"
            f"Date: {email.utils.formatdate()}\r\n"
            f"\r\nBody text {msg_id.decode()}"
        ).encode()
        return "OK", [(b"1", raw)]

    def logout(self):
        pass


def test_read_emails_parses_subject_and_body(monkeypatch):
    monkeypatch.setattr("imaplib.IMAP4_SSL", _FakeImap)

    messages = read_emails(
        imap_host="imap.example.com", username="u", password="p", limit=2, unseen_only=False
    )

    assert len(messages) == 2
    assert messages[0]["from"] == "sender@example.com"
    assert "Test" in messages[0]["subject"]
    assert "Body text" in messages[0]["body"]


def test_read_emails_does_not_mark_messages_as_read_by_default(monkeypatch):
    created = []
    monkeypatch.setattr("imaplib.IMAP4_SSL", lambda host: created.append(_FakeImap(host)) or created[-1])

    read_emails(imap_host="imap.example.com", username="u", password="p", limit=2)

    # RFC822 would set \Seen on the server; BODY.PEEK[] is the read-only form.
    assert created[0].fetch_specs == ["(BODY.PEEK[])", "(BODY.PEEK[])"]


def test_read_emails_marks_messages_as_read_when_asked(monkeypatch):
    created = []
    monkeypatch.setattr("imaplib.IMAP4_SSL", lambda host: created.append(_FakeImap(host)) or created[-1])

    read_emails(imap_host="imap.example.com", username="u", password="p", limit=1, mark_as_read=True)

    assert created[0].fetch_specs == ["(RFC822)"]
