from uiflow.http_client import send_http_request


class _FakeResponse:
    def __init__(self, status_code=200, text="ok", headers=None, json_body=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {"Content-Type": "text/plain"}
        self._json_body = json_body

    def json(self):
        if self._json_body is None:
            raise ValueError("no json")
        return self._json_body


def test_send_http_request_returns_parsed_json_when_available(monkeypatch):
    calls = {}

    def fake_request(**kwargs):
        calls.update(kwargs)
        return _FakeResponse(status_code=201, json_body={"ok": True})

    monkeypatch.setattr("requests.request", fake_request)

    result = send_http_request("post", "https://example.com/api", json_body={"a": 1})

    assert calls["method"] == "POST"
    assert calls["url"] == "https://example.com/api"
    assert result == {
        "status_code": 201,
        "headers": {"Content-Type": "text/plain"},
        "text": "ok",
        "json": {"ok": True},
    }


def test_send_http_request_json_is_none_when_body_is_not_json(monkeypatch):
    monkeypatch.setattr("requests.request", lambda **kwargs: _FakeResponse(text="plain text"))

    result = send_http_request("GET", "https://example.com")

    assert result["json"] is None
    assert result["text"] == "plain text"


def test_send_http_request_defaults_method_to_get(monkeypatch):
    calls = {}
    monkeypatch.setattr("requests.request", lambda **kwargs: calls.update(kwargs) or _FakeResponse())

    send_http_request("", "https://example.com")

    assert calls["method"] == "GET"
