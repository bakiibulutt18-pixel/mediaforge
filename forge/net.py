"""Standart kütüphane tabanlı HTTP yardımcısı (yeniden deneme + geri çekilme)."""
from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request

RETRY_CODES = (429, 500, 502, 503, 504)


class HttpError(Exception):
    def __init__(self, status: int, body: str, url: str):
        super().__init__(f"HTTP {status} {url}: {body[:300]}")
        self.status = status
        self.body = body
        self.url = url


class Response:
    def __init__(self, status: int, body: bytes, headers: dict, url: str):
        self.status = status
        self.body = body
        self.headers = headers
        self.url = url

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.body.decode("utf-8"))


def request(
    method: str,
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    json_body=None,
    form: dict | None = None,
    data: bytes | None = None,
    timeout: float = 60,
    retries: int = 3,
    backoff: float = 2.0,
    max_bytes: int | None = None,
) -> Response:
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    hdrs = {"User-Agent": "forge/1.0 (+media-engine)"}
    hdrs.update(headers or {})
    body = None
    if json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    elif form is not None:
        body = urllib.parse.urlencode(form).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
    elif data is not None:
        body = data

    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read(max_bytes) if max_bytes else r.read()
                return Response(r.status, raw, dict(r.headers), r.geturl())
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")
            last = HttpError(e.code, text, url)
            if e.code in RETRY_CODES and attempt < retries:
                wait = float(e.headers.get("Retry-After", 0) or 0) or backoff ** attempt
                time.sleep(min(wait, 60) + random.random())
                continue
            raise last
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = HttpError(0, str(e), url)
            if attempt < retries:
                time.sleep(backoff ** attempt + random.random())
                continue
            raise last
    raise last  # pragma: no cover


def get_json(url: str, **kw):
    return request("GET", url, **kw).json()


def post_json(url: str, body, **kw):
    return request("POST", url, json_body=body, **kw).json()
