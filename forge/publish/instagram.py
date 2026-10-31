"""Instagram Reels yayını – "Instagram API with Instagram Login" (graph.instagram.com).

Gerekenler: Business/Creator hesap, Meta geliştirici uygulaması, uzun ömürlü (60 gün) erişim token'ı.
Video, resumable upload ile doğrudan yüklenir (herkese açık URL gerekmez). Günlük yayın limiti: 100 gönderi.
"""
from __future__ import annotations

import pathlib
import time

from .. import config
from ..net import HttpError, request

GRAPH = "https://graph.instagram.com/v21.0"


class InstagramAuthError(Exception):
    pass


def _creds() -> tuple[str, str]:
    token, uid = config.env("IG_ACCESS_TOKEN"), config.env("IG_USER_ID")
    if not (token and uid):
        raise InstagramAuthError("IG_ACCESS_TOKEN / IG_USER_ID eksik")
    return token, uid


def refresh_token() -> dict:
    """Uzun ömürlü token'ı yeniler (en az 24 saat eski olmalı; yeni 60 günlük token döner)."""
    token, _ = _creds()
    return request("GET", "https://graph.instagram.com/refresh_access_token",
                   params={"grant_type": "ig_refresh_token", "access_token": token}).json()


def publish_reel(video_path: str, caption: str, *, share_to_feed: bool = True) -> dict:
    token, uid = _creds()
    size = pathlib.Path(video_path).stat().st_size
    c = request("POST", f"{GRAPH}/{uid}/media", form={
        "media_type": "REELS", "upload_type": "resumable", "caption": caption[:2150],
        "share_to_feed": str(share_to_feed).lower(), "access_token": token}).json()
    container = c["id"]
    uri = c.get("uri") or f"https://rupload.facebook.com/ig-api-upload/v21.0/{container}"
    request("POST", uri, data=pathlib.Path(video_path).read_bytes(), timeout=900, retries=2, headers={
        "Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size)})
    for _ in range(40):  # ~6 dk
        st = request("GET", f"{GRAPH}/{container}", params={"fields": "status_code,status", "access_token": token}).json()
        code = st.get("status_code")
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            raise HttpError(0, f"Instagram işleme hatası: {st}", "instagram")
        time.sleep(9)
    else:
        raise HttpError(0, "Instagram video işleme zaman aşımı", "instagram")
    pub = request("POST", f"{GRAPH}/{uid}/media_publish", form={"creation_id": container, "access_token": token}).json()
    media_id = pub["id"]
    permalink = ""
    try:
        permalink = request("GET", f"{GRAPH}/{media_id}", params={"fields": "permalink", "access_token": token}).json().get("permalink", "")
    except HttpError:
        pass
    return {"id": media_id, "url": permalink}


def insights(media_id: str) -> dict:
    token, _ = _creds()
    out = {}
    for metric in ("reach", "likes", "comments", "shares", "saved", "views"):
        try:
            r = request("GET", f"{GRAPH}/{media_id}/insights", params={"metric": metric, "access_token": token}, retries=0).json()
            out[metric] = r["data"][0]["values"][0]["value"]
        except (HttpError, KeyError, IndexError):
            continue
    return out


def account_stats() -> dict:
    token, uid = _creds()
    r = request("GET", f"{GRAPH}/{uid}", params={"fields": "username,followers_count,media_count", "access_token": token}).json()
    return r
