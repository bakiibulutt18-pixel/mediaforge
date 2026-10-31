"""X gönderi hattı. İki aşama:
  plan:  RSS haberlerinden günlük gönderi paketini üretir, kalite kontrolden geçirir, saatlere dağıtıp kuyruğa koyar.
  post:  zamanı gelen gönderileri yayınlar (yalnızca stdlib; hafif ve sık çalışır).
Otomatik beğeni/takip/yanıt/mention YOK: platform kurallarına aykırı ve hesabı askıya aldırır.
"""
from __future__ import annotations

import calendar
import random
import re
import time

from .. import config, llm, quality, research
from ..db import month_key
from ..net import HttpError
from ..publish import x
from . import common

WINDOW_START_H, WINDOW_END_H = 4, 20  # UTC (TR 07:00 - 23:00)
TYPES = {"take", "tip", "question", "poll", "thread"}


def _calls(t: dict) -> int:
    return 1 + len(t.get("thread") or [])


def _validate(t: dict, recent_texts: list[str], allowed_links: set[str]) -> str | None:
    if t.get("type") not in TYPES:
        return "bilinmeyen tür"
    text = (t.get("text") or "").strip()
    if not text:
        return "boş metin"
    if "@" in text:
        return "@ etiketi yasak"
    for link in re.findall(r"https?://\S+", text):
        if link not in allowed_links:
            return "izin verilmeyen bağlantı"
    if len(re.findall(r"#\w+", text)) > 2:
        return "2'den fazla hashtag"
    for part in [text] + list(t.get("thread") or []):
        if x.weighted_len(part) > 280:
            return f"280 karakteri aşıyor ({x.weighted_len(part)})"
    if t["type"] == "poll":
        opts = (t.get("poll") or {}).get("options") or []
        if not (2 <= len(opts) <= 4) or any(len(o) > 25 for o in opts):
            return "anket seçenekleri geçersiz"
    if t["type"] == "thread" and not (2 <= len(t.get("thread") or []) <= 6):
        return "thread uzunluğu geçersiz"
    if any(quality.overlap(text, r) > 0.4 for r in recent_texts):
        return "önceki gönderiyle benzer"
    return None


def plan(channel: str) -> int:
    ctx = common.make_ctx(channel)
    if not ctx:
        return 0
    db, cfg = ctx.db, ctx.cfg
    try:
        day0 = common.day_start_ts()
        existing = [i for i in db.items(kind="tweet", limit=300) if i["created_ts"] >= day0 and i["status"] in ("queued", "published", "ready")]
        need = cfg["daily"] - len(existing)
        if need <= 0:
            db.log("info", "bugünün gönderi planı zaten tam")
            return 0

        # aylık yazma limitine göre günlük üst sınır (thread/anket çağrıları da sayılır)
        limit = cfg.get("monthly_post_limit", 500)
        used = db.quota_used("x_posts", month_key())
        queued_calls = sum(_calls(i["payload"]) for i in db.items(status="queued", kind="tweet", limit=300))
        now = time.gmtime()
        days_left = calendar.monthrange(now.tm_year, now.tm_mon)[1] - now.tm_mday + 1
        call_budget = max(int((limit - used - queued_calls) / days_left), 0)
        db.log("info", f"X çağrı bütçesi bugün: {call_budget} (kullanılan {used}/{limit}, kuyrukta {queued_calls})")
        if call_budget < 1:
            return 0

        recent_texts = [i["payload"].get("text", "") for i in db.items(kind="tweet", limit=60)]
        seen_links = {i["payload"].get("link") for i in db.items(kind="tweet", limit=200)}
        news = []
        for f in cfg["feeds"]:
            for it in research.fetch_rss(f, 6):
                if it["link"] and it["link"] not in seen_links and it["title"]:
                    news.append(it)
        random.shuffle(news)
        news = news[:24]
        news_block = "\n".join(f"- [{n['link']}] {n['title']}: {n['summary'][:200]}" for n in news) or "haber yok"
        allowed = {n["link"] for n in news}

        n_ask = min(need + 3, 18)
        prompt = (
            f"Hesap: {cfg['title']} (Türkçe). Persona: {cfg['persona']}\n"
            f"{n_ask} gönderi üret. Karışım: ~%45 'take' (haber üzerine KENDİ yorumun; haberin metnini kopyalama, "
            "bağlantıyı metnin sonuna ekleyebilirsin), ~%25 'tip' (uygulanabilir yapay zekâ/üretkenlik ipucu), "
            "~%15 'question' (tartışma sorusu), 1 adet 'poll' (2-4 seçenek, her biri ≤25 karakter), 1 adet 'thread' "
            "(text=giriş tweet'i, thread=3-5 devam tweet'i).\n"
            "Kurallar: her gönderi ≤280 karakter; Türkçe; @etiket YOK; en fazla 2 hashtag; abartılı/yanıltıcı iddia YOK; "
            "haberde olmayan sayı uydurma; her gönderi farklı bir açı ve kalıp kullansın.\n"
            f"Öğrenilmiş ipuçları: {common.strategy_hints(ctx.channel)}\n"
            f"Son gönderilerin (tekrar etme): {' || '.join(t[:80] for t in recent_texts[:15])}\n\n"
            f"{llm.wrap_untrusted('haber başlıkları', news_block, 6000)}\n\n"
            'Sadece JSON: {"tweets":[{"type":"take|tip|question|poll|thread","text":"...","thread":["..."],'
            '"poll":{"options":["..",".."]},"link":"kullandıysan haber URL\'si"}]}'
        )
        data = llm.generate_json(prompt, task="tweet_batch", required=("tweets",), temperature=0.9)

        good, spent = [], 0
        for t in data["tweets"]:
            err = _validate(t, recent_texts + [g["text"] for g in good], allowed)
            if err:
                db.log("info", f"gönderi elendi ({err}): {(t.get('text') or '')[:60]}")
                continue
            if spent + _calls(t) > call_budget or len(good) >= need:
                continue
            good.append(t)
            spent += _calls(t)
        if not good:
            raise RuntimeError("geçerli gönderi üretilemedi")

        joined = "\n---\n".join(g["text"] for g in good)
        rep = quality.check_script(joined, [{"text": g["text"], "visual_query": "-"} for g in good], [])
        quality.apply_review(rep, quality.llm_review(cfg["title"], joined, "Bunlar X gönderileri."))
        if not rep.passed(cfg["min_quality"]):
            raise RuntimeError(f"gönderi paketi kalite kontrolünden geçemedi: {rep.critical + rep.issues}")

        # zamanlama: kalan pencereye eşit dağıt + doğal gecikme
        t_now = int(time.time())
        start = max(t_now + 600, day0 + WINDOW_START_H * 3600)
        end = day0 + WINDOW_END_H * 3600 + 1800
        if start >= end - 600 * len(good):  # bugünkü pencere bitti → yarına planla
            start = day0 + 86400 + WINDOW_START_H * 3600
            end = day0 + 86400 + WINDOW_END_H * 3600 + 1800
        step = (end - start) / max(len(good), 1)
        for i, g in enumerate(good):
            due = int(start + step * i + random.randint(-int(step * 0.25), int(step * 0.25)))
            db.new_item("tweet", g["text"][:60], {"type": g["type"], "text": g["text"], "thread": g.get("thread") or [],
                                                  "poll": g.get("poll"), "link": g.get("link")},
                        status="queued", due_ts=max(due, t_now + 300))
        db.log("info", f"{len(good)} gönderi kuyruğa alındı (KK puanı {rep.score:.0f})")
        return len(good)
    except Exception as e:
        db.log("error", f"gönderi planı başarısız: {type(e).__name__}: {e}")
        return 0
    finally:
        db.close()


def post_due(channel: str) -> int:
    ctx = common.make_ctx(channel)
    if not ctx:
        return 0
    db, cfg = ctx.db, ctx.cfg
    sent = 0
    try:
        for it in db.due_items("tweet")[:3]:
            p = it["payload"]
            if time.time() - it["due_ts"] > 8 * 3600:
                db.update_item(it["id"], status="rejected", error="zamanı geçti")
                continue
            if config.dry_run():
                db.update_item(it["id"], status="ready")
                db.log("info", f"[kuru çalıştırma] gönderilecekti: {p['text'][:80]}")
                continue
            try:
                first = x.post(db, p["text"], poll=p.get("poll") if p["type"] == "poll" else None,
                               monthly_limit=cfg.get("monthly_post_limit", 500))
                prev = first
                for part in p.get("thread") or []:
                    time.sleep(2)
                    prev = x.post(db, part, reply_to=prev, monthly_limit=cfg.get("monthly_post_limit", 500))
                db.add_post(item_id=it["id"], platform="x", external_id=first, url=f"https://x.com/i/status/{first}",
                            title=p["text"][:80], kind=p["type"])
                db.update_item(it["id"], status="published", payload_update={"tweet_id": first})
                sent += 1
                db.log("info", f"gönderildi: {p['text'][:70]}")
            except x.XAuthError as e:
                db.log("error", f"X kimlik bilgisi sorunu: {e}")
                break
            except HttpError as e:
                att = it["attempts"] + 1
                if e.status == 429 and "limit" in e.body.lower():
                    db.log("error", f"X limiti: {e}")
                    break
                db.update_item(it["id"], attempts=att, status="failed" if att >= common.MAX_ATTEMPTS else "queued",
                               error=str(e)[:500])
                db.log("error", f"gönderi hatası (deneme {att}): {e}")
            time.sleep(random.uniform(1, 4))
        return sent
    finally:
        db.close()
