"""Boru hattı ortak yardımcıları: bağlam, günlük limitler, hata yönetimi, strateji ipuçları, yayın saatleri."""
from __future__ import annotations

import json
import pathlib
import time
import traceback
from dataclasses import dataclass

from .. import config, quality
from ..db import DB

MAX_ATTEMPTS = 3
TR_OFFSET = 3 * 3600  # Türkiye UTC+3


@dataclass
class Ctx:
    channel: str
    cfg: dict
    db: DB
    item_id: int = 0
    workdir: pathlib.Path | None = None


def day_start_ts(ts: int | None = None) -> int:
    t = ts or int(time.time())
    return t - (t % 86400)


def week_start_ts(ts: int | None = None) -> int:
    t = day_start_ts(ts)
    wd = time.gmtime(t).tm_wday  # 0=Pazartesi
    return t - wd * 86400


def make_ctx(channel: str) -> Ctx | None:
    if channel not in config.CHANNELS:
        raise SystemExit(f"Bilinmeyen kanal: {channel}")
    db = DB(channel)
    if not config.channel_enabled(channel):
        db.log("info", "Kanal duraklatılmış/devre dışı; çıkılıyor")
        db.close()
        return None
    return Ctx(channel, config.channel_cfg(channel), db)


def open_workdir(ctx: Ctx) -> pathlib.Path:
    d = config.OUT_DIR / ctx.channel / f"item_{ctx.item_id}"
    d.mkdir(parents=True, exist_ok=True)
    ctx.workdir = d
    return d


def strategy_path(channel: str) -> pathlib.Path:
    return config.STATE_DIR / f"{channel}_strategy.json"


def strategy_hints(channel: str) -> str:
    """strategy.py'nin haftalık ürettiği, GERÇEK performans verisine dayalı öğrenilmiş ipuçları."""
    try:
        s = json.loads(strategy_path(channel).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "Henüz yeterli performans verisi yok; çeşitlilik ve kalite öncelikli."
    parts = []
    if s.get("prefer"):
        parts.append("İyi çalışan: " + "; ".join(s["prefer"][:6]))
    if s.get("avoid"):
        parts.append("Kötü çalışan: " + "; ".join(s["avoid"][:6]))
    if s.get("title_patterns"):
        parts.append("Başlık kalıpları: " + "; ".join(s["title_patterns"][:5]))
    return " | ".join(parts) or "Henüz belirgin bir öğrenme yok."


def previous_texts(db: DB, limit: int = 80, exclude: int = 0) -> list[str]:
    """Özgünlük karşılaştırması için önceki senaryolar (işin kendi taslağı hariç)."""
    out = []
    for it in db.items(limit=limit):
        if it["id"] == exclude:
            continue
        t = it["payload"].get("script_text")
        if t:
            out.append(t)
    return out


def too_similar(topic: str, recent: list[str], thr: float = 0.6) -> bool:
    a = set(topic.lower().split())
    for r in recent:
        b = set(r.lower().split())
        if a and b and len(a & b) / len(a | b) >= thr:
            return True
    return False


def resumable_item(ctx: Ctx, kind: str) -> int | None:
    """Daha önce hata veren (deneme hakkı kalan) iş varsa onu sürdür."""
    for it in ctx.db.items(status="new", kind=kind, limit=5):
        if it["attempts"] < MAX_ATTEMPTS and it["payload"].get("script"):
            return it["id"]
    return None


def fail_item(ctx: Ctx, exc: Exception):
    it = ctx.db.get_item(ctx.item_id)
    attempts = it["attempts"] + 1
    # senaryo kaydedilmediyse devam ettirilecek bir şey yok
    status = "failed" if attempts >= MAX_ATTEMPTS or not it["payload"].get("script") else "new"
    msg = f"{type(exc).__name__}: {exc}"
    ctx.db.update_item(ctx.item_id, attempts=attempts, status=status, error=msg[:1500])
    ctx.db.log("error", f"iş #{ctx.item_id} hata (deneme {attempts}/{MAX_ATTEMPTS}, durum={status}): {msg[:500]}")
    if config.env("FORGE_DEBUG") == "1":
        traceback.print_exc()


def next_publish_slot(db: DB, *, hour_utc: int, weekdays: list[int] | None = None, min_lead: int = 3600) -> int:
    """Bir sonraki boş yayın slotu (zaten planlanmış slotlar atlanır)."""
    taken = {p["scheduled_ts"] for p in db.posts(200) if p["scheduled_ts"]}
    t = day_start_ts() + hour_utc * 3600
    for _ in range(60):
        wd = time.gmtime(t).tm_wday
        if t > time.time() + min_lead and t not in taken and (weekdays is None or wd in weekdays):
            return t
        t += 86400
    raise RuntimeError("uygun yayın slotu bulunamadı")


def qc_gate(ctx: Ctx, report: quality.Report) -> bool:
    ctx.db.update_item(ctx.item_id, quality=report.score, payload_update={"qc": report.as_dict()})
    ok = report.passed(ctx.cfg.get("min_quality", 75))
    ctx.db.log("info" if ok else "warn",
               f"KK iş #{ctx.item_id}: puan {report.score:.0f} (eşik {ctx.cfg.get('min_quality')}) "
               f"{'GEÇTİ' if ok else 'KALDI'}; kritik={report.critical}; sorun={report.issues[:4]}")
    return ok


AI_DISCLOSURE = "Bu içerik yapay zekâ destekli araçlarla hazırlanmıştır; bilgiler kaynaklarla doğrulanarak derlenmiştir."
