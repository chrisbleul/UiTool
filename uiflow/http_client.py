"""Thin wrapper around `requests` for the engine's `http_request` action. A
separate module (rather than inline in engine.py) so it can be unit-tested
without going through the engine, matching excel.py's precedent."""

from __future__ import annotations

from typing import Any


def send_http_request(
    method: str,
    url: str,
    headers: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    data: str | None = None,
    timeout: float = 30,
) -> dict[str, Any]:
    import requests

    response = requests.request(
        method=(method or "GET").upper(),
        url=url,
        headers=headers or None,
        params=params or None,
        json=json_body or None,
        data=data or None,
        timeout=timeout,
    )
    try:
        parsed_json = response.json()
    except ValueError:
        parsed_json = None
    return {
        "status_code": response.status_code,
        "headers": dict(response.headers),
        "text": response.text,
        "json": parsed_json,
    }
