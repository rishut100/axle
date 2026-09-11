"""Sync HTTP client with uniform error handling — mirrors Heimdall's ServiceClient pattern,
adapted to `requests` (axle is sync Flask). Transport + HTTP-status errors become a structured
ServiceError; body-level errors (GraphQL `errors`, Slack `ok:false`) stay with the caller.

base_url and headers are lazy (str/callable) so env-driven config is read per-call, not frozen
at import — Drive's base is env-driven, so it passes a callable just like headers_fn.
"""
import logging

import requests

from dtapp.garage.core.errors import ServiceError

logger = logging.getLogger(__name__)
_DETAIL_LIMIT = 300


def _detail(resp: requests.Response) -> str:
    """Short snippet from an error body (JSON error/message field, else raw text), truncated."""
    try:
        body = resp.json()
        text = (body.get("error") or body.get("message") or resp.text) if isinstance(body, dict) else resp.text
    except Exception:
        text = resp.text
    return (text or "").strip()[:_DETAIL_LIMIT]


class ServiceClient:
    def __init__(self, name: str, base_url="", headers_fn=None, timeout: float = 15):
        self._name = name
        self._base_url = base_url  # str or callable → str (lazy, for env-driven bases)
        self._headers_fn = headers_fn or (lambda: {})
        self._timeout = timeout

    def _url(self, path: str) -> str:
        base = self._base_url() if callable(self._base_url) else self._base_url
        return base.rstrip("/") + path

    def post(self, path: str, json=None, headers=None) -> dict:
        return self._send("post", path, headers=headers, json=json)

    def get(self, path: str, params=None, headers=None) -> dict:
        return self._send("get", path, headers=headers, params=params)

    def patch(self, path: str, json=None, headers=None) -> dict:
        return self._send("patch", path, headers=headers, json=json)

    def put(self, path: str, json=None, headers=None) -> dict:
        return self._send("put", path, headers=headers, json=json)

    def delete(self, path: str, headers=None, params=None) -> dict:
        return self._send("delete", path, headers=headers, params=params)

    def _send(self, method: str, path: str, headers=None, **kwargs) -> dict:
        url = self._url(path)
        merged = self._headers_fn()
        if headers:
            # Per-call headers may add new headers or override Host (tenant-vhost routing), but must NOT
            # clobber the base auth/identity headers (apikey, use-internal) — base wins on conflict.
            base_keys = {k.lower() for k in merged}
            merged = {**merged, **{k: v for k, v in headers.items()
                                   if k.lower() == "host" or k.lower() not in base_keys}}
        try:
            r = requests.request(method, url, headers=merged, timeout=self._timeout, **kwargs)
        except requests.RequestException as e:
            raise ServiceError(self._name, f"unreachable: {e}")
        if r.status_code >= 300:
            raise ServiceError(self._name, f"HTTP {r.status_code}: {_detail(r)}")
        return r.json() if r.content else {}
