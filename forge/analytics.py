"""Yayın sonrası performans toplama. Metrikler db.metrics'e yazılır (post_id=0 → kanal geneli).

Dürüstlük: CTR ve gösterim sayısı YouTube Analytics API'sinde sunulmaz; panelde 'API'de yok' görünür.
Gelir yalnızca kanal YouTube Ortaklık Programı'ndaysa gelir.
"""
from __future__ import annotations

import time

from . import config
from .db import DB, now
from .net import HttpError
from .publish import instagram, x, youtube


def _recent(db: DB, days: int = 30):
    cut = now() - days * 86400
    return [p for p in db.posts(300) if p["external_id"] and (p["published_ts"] or 0) >= cut]


def collect(channel: str) -> None:
    cfg = config.CHANNELS[channel]
    db = DB(channel)
    try:
        if config.mock():
            db.log("info", "mock: analitik atlandı")
            return
        posts = _recent(db)
        plat = cfg["platform"]
        if plat == "youtube":
            cred = cfg["cred"]
            try:
                db.add_metric(0, youtube.channel_stats(cred))
                st = youtube.stats(cred, [p["external_id"] for p in posts])
                today = time.strftime("%Y-%m-%d")
                for p in posts:
                    m = dict(st.get(p["external_id"], {}))
                    age_days = (now() - (p["published_ts"] or now())) / 86400
                    if age_days >= 2 and m:
                        try:
                            a = youtube.analytics(cred, p["external_id"], time.strftime("%Y-%m-%d", time.gmtime(p["published_ts"])), today)
                            m.update({"avg_view_pct": a.get("averageViewPercentage"), "avg_view_sec": a.get("averageViewDuration"),
                                      "minutes_watched": a.get("estimatedMinutesWatched"), "subs_gained": a.get("subscribersGained"),
                                      "revenue_usd": a.get("estimatedRevenue")})
                        except HttpError as e:
                            db.log("warn", f"analytics API (video {p['external_id']}): {str(e)[:160]}")
                    if m:
                        db.add_metric(p["id"], m)
                    if p["status"] == "scheduled" and p["scheduled_ts"] and p["scheduled_ts"] < now():
                        s = youtube.video_status(cred, p["external_id"]).get("status", {})
                        if s.get("privacyStatus") == "public":
                            with db.tx() as c:
                                c.execute("UPDATE posts SET status='published', published_ts=? WHERE id=?", (p["scheduled_ts"], p["id"]))
                        elif s:
                            db.log("warn", f"planlı video yayında değil: {p['external_id']} gizlilik={s.get('privacyStatus')} ret={s.get('rejectionReason')}")
            except youtube.YouTubeAuthError as e:
                db.log("error", f"YouTube yetkilendirme: {e}")
        elif plat == "instagram":
            try:
                db.add_metric(0, instagram.account_stats())
                for p in posts[:25]:
                    m = instagram.insights(p["external_id"])
                    if m:
                        db.add_metric(p["id"], m)
            except instagram.InstagramAuthError as e:
                db.log("error", f"Instagram yetkilendirme: {e}")
            except HttpError as e:
                db.log("error", f"Instagram API: {str(e)[:200]} (token süresi dolmuş olabilir)")
        elif plat == "x":
            try:
                me = x.me()
                db.add_metric(0, {"followers": me.get("public_metrics", {}).get("followers_count"),
                                  "tweets": me.get("public_metrics", {}).get("tweet_count")})
                m = x.tweet_metrics([p["external_id"] for p in posts[:100]])
                for p in posts:
                    if p["external_id"] in m:
                        db.add_metric(p["id"], m[p["external_id"]])
            except x.XAuthError as e:
                db.log("error", f"X yetkilendirme: {e}")
            except HttpError as e:
                db.log("warn", f"X okuma API'si: {str(e)[:200]} (ücretsiz katmanda okuma çok kısıtlıdır)")
    finally:
        db.close()
