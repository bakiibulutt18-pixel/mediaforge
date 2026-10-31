"""X (Twitter) API v2 – OAuth 1.0a kullanıcı bağlamı, yalnızca standart kütüphane.

Ücretsiz katmanda aylık yazma limiti vardır (varsayılan 500, kanal ayarından değişir). Sayaç db.quota'da tutulur.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import urllib.parse

from .. import config
from ..db import DB, month_key
from ..net import HttpError, request

API = "https://api.x.com/2"


class XAuthError(Exception):
    pass


def _keys() -> dict:
    k = {n: config.env(n) for n in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")}
    missing = [n for n, v in k.items() if not v]
    if missing:
        raise XAuthError("Eksik: " + ", ".join(missing))
    return k


def _pct(s: str) -> str:
    return urllib.parse.quote(str(s), safe="~")


def oauth1_header(method: str, url: str, keys: dict, params: dict | None = None) -> str:
    """JSON gövdeli istekler için gövde imzaya girmez; sorgu parametreleri girer."""
    oauth = {
        "oauth_consumer_key": keys["X_API_KEY"], "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1", "oauth_timestamp": str(int(time.time())),
        "oauth_token": keys["X_ACCESS_TOKEN"], "oauth_version": "1.0",
    }
    all_params = {**(params or {}), **oauth}
    pstr = "&".join(f"{_pct(k)}={_pct(v)}" for k, v in sorted(all_params.items()))
    base = "&".join([method.upper(), _pct(url), _pct(pstr)])
    key = f"{_pct(keys['X_API_SECRET'])}&{_pct(keys['X_ACCESS_SECRET'])}"
    sig = base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()).decode()
    oauth["oauth_signature"] = sig
    return "OAuth " + ", ".join(f'{_pct(k)}="{_pct(v)}"' for k, v in sorted(oauth.items()))


def _call(method: str, path: str, body=None, params=None) -> dict:
    keys = _keys()
    url = API + path
    hdr = oauth1_header(method, url, keys, params)
    return request(method, url, params=params, json_body=body, headers={"Authorization": hdr}, retries=1).json()


def weighted_len(text: str) -> int:
    """X karakter sayımı: URL'ler 23, emoji ve CJK 2, diğerleri 1."""
    import re
    n = 0
    for tok in re.split(r"(https?://\S+)", text):
        if tok.startswith("http"):
            n += 23
        else:
            for ch in tok:
                n += 2 if ord(ch) > 0x2E7F and not (0x2000 <= ord(ch) <= 0x206F) else 1
    return n


def check_limit(db: DB, monthly_limit: int):
    used = db.quota_used("x_posts", month_key())
    if used >= monthly_limit:
        raise HttpError(429, f"X aylık gönderi limiti doldu: {used}/{monthly_limit}", "x")
    if used >= 0.8 * monthly_limit:
        db.log("warn", f"X aylık limitin %80'i aşıldı: {used}/{monthly_limit}")


def post(db: DB, text: str, *, reply_to: str | None = None, poll: dict | None = None, monthly_limit: int = 500) -> str:
    check_limit(db, monthly_limit)
    if weighted_len(text) > 280:
        raise ValueError(f"Tweet 280 karakteri aşıyor ({weighted_len(text)})")
    body: dict = {"text": text}
    if reply_to:
        body["reply"] = {"in_reply_to_tweet_id": reply_to}
    if poll:
        body["poll"] = {"options": poll["options"][:4], "duration_minutes": int(poll.get("minutes", 1440))}
    r = _call("POST", "/tweets", body=body)
    db.quota_add("x_posts", month_key(), 1)
    return r["data"]["id"]


def tweet_metrics(ids: list[str]) -> dict[str, dict]:
    """Tek istekte 100 tweet'e kadar public_metrics (ücretsiz katmanda okuma kotası çok düşüktür)."""
    if not ids:
        return {}
    r = _call("GET", "/tweets", params={"ids": ",".join(ids[:100]), "tweet.fields": "public_metrics"})
    out = {}
    for t in r.get("data", []):
        m = t.get("public_metrics", {})
        out[t["id"]] = {"views": m.get("impression_count", 0), "likes": m.get("like_count", 0),
                        "replies": m.get("reply_count", 0), "reposts": m.get("retweet_count", 0),
                        "quotes": m.get("quote_count", 0)}
    return out


def me() -> dict:
    return _call("GET", "/users/me", params={"user.fields": "public_metrics"}).get("data", {})
