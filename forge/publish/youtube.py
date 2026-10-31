"""YouTube Data API v3 – yalnızca standart kütüphane (OAuth refresh-token akışı).

Önemli: Doğrulanmamış (denetimden geçmemiş) API projelerinde yüklenen videolar 'özel' kilitlenir.
Kurulum rehberindeki 'API Hizmetleri Denetimi' formunu doldurmadan videolar herkese açık yayınlanmaz.
Kota: günlük 10.000 birim. videos.insert = 1600, thumbnails.set = 50, captions.insert = 400, playlistItems.insert = 50.
"""
from __future__ import annotations

import json
import mimetypes
import pathlib
import time

from .. import config
from ..db import DB, day_key
from ..net import HttpError, request

COST_UPLOAD, COST_THUMB, COST_CAPTION, COST_PLAYLIST, COST_LIST = 1600, 50, 400, 50, 1
DAILY_QUOTA = 10_000
TOKEN_URL = "https://oauth2.googleapis.com/token"


class YouTubeAuthError(Exception):
    pass


def _prefix(cred: str) -> str:
    return {"youtube_fun": "YT_FUN", "youtube_doc": "YT_DOC"}.get(cred, "YT")


def access_token(cred: str) -> str:
    p = _prefix(cred)
    cid = config.env(f"{p}_CLIENT_ID") or config.env("YT_CLIENT_ID")
    sec = config.env(f"{p}_CLIENT_SECRET") or config.env("YT_CLIENT_SECRET")
    rt = config.env(f"{p}_REFRESH_TOKEN")
    if not (cid and sec and rt):
        raise YouTubeAuthError(f"{p}_CLIENT_ID/_CLIENT_SECRET/_REFRESH_TOKEN eksik")
    try:
        r = request("POST", TOKEN_URL, form={"client_id": cid, "client_secret": sec, "refresh_token": rt,
                                              "grant_type": "refresh_token"}).json()
    except HttpError as e:
        raise YouTubeAuthError(f"Token yenilenemedi (yeniden yetkilendirme gerekebilir): {e}")
    return r["access_token"]


def _quota_check(db: DB, need: int):
    used = db.quota_used("youtube_units", day_key())
    if used + need > DAILY_QUOTA - 300:  # 300 birim güvenlik payı
        raise HttpError(429, f"YouTube günlük kota doluyor: kullanılan {used}, gereken {need}", "quota")


def upload_video(db: DB, cred: str, video_path: str, *, title: str, description: str, tags: list[str],
                 category_id: str, publish_at_ts: int | None, synthetic: bool, language: str = "tr") -> str:
    """Resumable upload. publish_at_ts verilirse video 'private' yüklenir ve o saatte otomatik yayınlanır."""
    _quota_check(db, COST_UPLOAD)
    token = access_token(cred)
    status = {"privacyStatus": "public", "selfDeclaredMadeForKids": False, "containsSyntheticMedia": bool(synthetic)}
    if publish_at_ts and publish_at_ts > time.time() + 300:
        status["privacyStatus"] = "private"
        status["publishAt"] = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(publish_at_ts))
    meta = {
        "snippet": {"title": title[:100], "description": description[:4900], "tags": tags[:25],
                    "categoryId": category_id, "defaultLanguage": language, "defaultAudioLanguage": language},
        "status": status,
    }
    data = pathlib.Path(video_path).read_bytes()
    init = request("POST", "https://www.googleapis.com/upload/youtube/v3/videos",
                   params={"uploadType": "resumable", "part": "snippet,status"},
                   headers={"Authorization": f"Bearer {token}", "X-Upload-Content-Type": "video/mp4",
                            "X-Upload-Content-Length": str(len(data))}, json_body=meta)
    loc = init.headers.get("Location") or init.headers.get("location")
    if not loc:
        raise HttpError(0, "resumable upload adresi alınamadı", "youtube")
    done = request("PUT", loc, data=data, headers={"Content-Type": "video/mp4"}, timeout=1800, retries=2).json()
    db.quota_add("youtube_units", day_key(), COST_UPLOAD)
    return done["id"]


def set_thumbnail(db: DB, cred: str, video_id: str, image_path: str):
    _quota_check(db, COST_THUMB)
    token = access_token(cred)
    ctype = mimetypes.guess_type(image_path)[0] or "image/jpeg"
    request("POST", "https://www.googleapis.com/upload/youtube/v3/thumbnails/set",
            params={"videoId": video_id, "uploadType": "media"},
            headers={"Authorization": f"Bearer {token}", "Content-Type": ctype},
            data=pathlib.Path(image_path).read_bytes())
    db.quota_add("youtube_units", day_key(), COST_THUMB)


def upload_captions(db: DB, cred: str, video_id: str, srt_path: str, language: str = "tr"):
    _quota_check(db, COST_CAPTION)
    token = access_token(cred)
    boundary = "forgeboundary7MA4YWxk"
    meta = json.dumps({"snippet": {"videoId": video_id, "language": language, "name": "Türkçe", "isDraft": False}})
    body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{meta}\r\n"
            f"--{boundary}\r\nContent-Type: application/octet-stream\r\n\r\n").encode("utf-8")
    body += pathlib.Path(srt_path).read_bytes() + f"\r\n--{boundary}--".encode()
    request("POST", "https://www.googleapis.com/upload/youtube/v3/captions",
            params={"uploadType": "multipart", "part": "snippet"},
            headers={"Authorization": f"Bearer {token}", "Content-Type": f"multipart/related; boundary={boundary}"},
            data=body)
    db.quota_add("youtube_units", day_key(), COST_CAPTION)


def ensure_playlist(db: DB, cred: str, name: str) -> str:
    token = access_token(cred)
    h = {"Authorization": f"Bearer {token}"}
    r = request("GET", "https://www.googleapis.com/youtube/v3/playlists",
                params={"part": "snippet", "mine": "true", "maxResults": 50}, headers=h).json()
    db.quota_add("youtube_units", day_key(), COST_LIST)
    for p in r.get("items", []):
        if p["snippet"]["title"] == name:
            return p["id"]
    _quota_check(db, 50)
    made = request("POST", "https://www.googleapis.com/youtube/v3/playlists", params={"part": "snippet,status"},
                   headers=h, json_body={"snippet": {"title": name}, "status": {"privacyStatus": "public"}}).json()
    db.quota_add("youtube_units", day_key(), 50)
    return made["id"]


def add_to_playlist(db: DB, cred: str, playlist_id: str, video_id: str):
    _quota_check(db, COST_PLAYLIST)
    token = access_token(cred)
    request("POST", "https://www.googleapis.com/youtube/v3/playlistItems", params={"part": "snippet"},
            headers={"Authorization": f"Bearer {token}"},
            json_body={"snippet": {"playlistId": playlist_id, "resourceId": {"kind": "youtube#video", "videoId": video_id}}})
    db.quota_add("youtube_units", day_key(), COST_PLAYLIST)


def video_status(cred: str, video_id: str) -> dict:
    """Gerçek yayın durumunu YouTube'dan okur (uploadStatus, privacyStatus, publishAt, rejectionReason)."""
    token = access_token(cred)
    r = request("GET", "https://www.googleapis.com/youtube/v3/videos",
                params={"part": "status,statistics", "id": video_id}, headers={"Authorization": f"Bearer {token}"}).json()
    items = r.get("items", [])
    return items[0] if items else {}


def stats(cred: str, video_ids: list[str]) -> dict[str, dict]:
    token = access_token(cred)
    out = {}
    for i in range(0, len(video_ids), 50):
        r = request("GET", "https://www.googleapis.com/youtube/v3/videos",
                    params={"part": "statistics", "id": ",".join(video_ids[i:i + 50])},
                    headers={"Authorization": f"Bearer {token}"}).json()
        for it in r.get("items", []):
            s = it.get("statistics", {})
            out[it["id"]] = {"views": int(s.get("viewCount", 0)), "likes": int(s.get("likeCount", 0)),
                             "comments": int(s.get("commentCount", 0))}
    return out


def analytics(cred: str, video_id: str, start: str, end: str) -> dict:
    """YouTube Analytics API. CTR/gösterim API'de sunulmadığı için burada yoktur."""
    token = access_token(cred)
    metrics = "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,subscribersGained"
    try:
        r = request("GET", "https://youtubeanalytics.googleapis.com/v2/reports", params={
            "ids": "channel==MINE", "startDate": start, "endDate": end, "metrics": metrics + ",estimatedRevenue",
            "filters": f"video=={video_id}"}, headers={"Authorization": f"Bearer {token}"}).json()
        cols = [c["name"] for c in r["columnHeaders"]]
    except HttpError:  # gelir metriği kanal monetize değilse reddedilir
        r = request("GET", "https://youtubeanalytics.googleapis.com/v2/reports", params={
            "ids": "channel==MINE", "startDate": start, "endDate": end, "metrics": metrics,
            "filters": f"video=={video_id}"}, headers={"Authorization": f"Bearer {token}"}).json()
        cols = [c["name"] for c in r["columnHeaders"]]
    rows = r.get("rows") or [[0] * len(cols)]
    return dict(zip(cols, rows[0]))


def channel_stats(cred: str) -> dict:
    token = access_token(cred)
    r = request("GET", "https://www.googleapis.com/youtube/v3/channels", params={"part": "statistics,snippet", "mine": "true"},
                headers={"Authorization": f"Bearer {token}"}).json()
    it = (r.get("items") or [{}])[0]
    s = it.get("statistics", {})
    return {"title": it.get("snippet", {}).get("title"), "subscribers": int(s.get("subscriberCount", 0)),
            "views": int(s.get("viewCount", 0)), "videos": int(s.get("videoCount", 0))}
