"""Dikey kısa video hattı (Instagram Reels ve YouTube Shorts): konu → Vikipedi tabanlı gerçekler →
senaryo → seslendirme → görsel → 1080x1920 render → kalite kontrol → yayın.
"""
from __future__ import annotations

import pathlib
import time

from .. import config, images, llm, quality, research, tts, video
from ..publish import instagram, youtube
from . import common
from .common import Ctx

W, H = 1080, 1920
WORDS_PER_SEC = 2.7


def choose_topic(ctx: Ctx) -> tuple[str, dict]:
    cfg, db = ctx.cfg, ctx.db
    recent = db.recent_topics(100)
    trends = research.trends_tr(10)
    prompt = (
        f"Kanal: {cfg['title']}. Persona: {cfg['persona']}\n"
        f"Kategoriler: {', '.join(cfg['categories'])}\n"
        f"Son işlenen konular (TEKRAR ETME): {'; '.join(recent[:40]) or 'yok'}\n"
        f"Öğrenilmiş ipuçları: {common.strategy_hints(ctx.channel)}\n"
        f"Bugünün Türkiye arama gündemi (yalnızca ilham, zorunlu değil): {', '.join(trends) or 'erişilemedi'}\n\n"
        "8 farklı kısa video konusu öner. Her konu Vikipedi'de maddesi olan, doğrulanabilir, şaşırtıcı bir olguya dayanmalı. "
        'Sadece JSON: {"topics":[{"topic":"...","category":"...","wiki_title":"Vikipedi madde adı","wiki_lang":"tr|en",'
        '"interest":1-10}]}'
    )
    data = llm.generate_json(prompt, task="short_topics", required=("topics",), temperature=0.9)
    cands = sorted(data["topics"], key=lambda t: -float(t.get("interest", 0)))
    for c in cands:
        topic = (c.get("topic") or "").strip()
        if not topic or common.too_similar(topic, recent):
            continue
        for lang in dict.fromkeys([c.get("wiki_lang", "tr"), "tr", "en"]):
            page = research.wiki_extract(c.get("wiki_title") or topic, lang)
            if page and len(page["text"]) > 600:
                return topic, {"candidate": c, "source": page}
    raise RuntimeError("Doğrulanabilir kaynağı olan yeni konu bulunamadı")


def write_script(ctx: Ctx, topic: str, source: dict, feedback: str = "") -> dict:
    cfg = ctx.cfg
    lo, hi = cfg["duration"]
    words = int(((lo + hi) / 2) * WORDS_PER_SEC)
    prompt = (
        f"Persona: {cfg['persona']}\nDil: Türkçe. Konu: {topic}\n"
        f"Hedef: yaklaşık {words} kelime ({lo}-{hi} sn). 5-7 sahne; her sahne 1-2 kısa cümle.\n"
        "Kurallar: 1) İlk sahne güçlü bir merak kancası olsun ama yanıltıcı olmasın. 2) SADECE aşağıdaki kaynak metindeki "
        "bilgileri kullan; kaynakta olmayan sayı/iddia ekleme. 3) Son sahne akılda kalan bir kapanış/yorum olsun, "
        "'takip et' gibi klişe istek yok. 4) Kalıp cümleleri tekrarlama; özgün yaz. "
        "5b) Sayıları, yılları ve ölçüleri RAKAMLA yaz (ör. 1969, 21,5 saat, 384.000 km); yazıyla yazma.\n"
        "5) visual_query ve visual_prompt İNGİLİZCE olsun (stok video araması / yapay zekâ görsel promptu).\n"
        f"{('Önceki taslağın sorunları, düzelt: ' + feedback) if feedback else ''}\n\n"
        f"{llm.wrap_untrusted(source['title'] + ' – ' + source['url'], source['text'], 5000)}\n\n"
        'Sadece JSON: {"title":"...","scenes":[{"text":"anlatım","visual_query":"...","visual_prompt":"...",'
        '"visual_type":"stock|ai"}],"caption":"gönderi açıklaması (2-3 cümle)","hashtags":["#..."]}'
    )
    s = llm.generate_json(prompt, task="short_script", required=("title", "scenes"), temperature=0.85)
    s["scenes"] = s["scenes"][:8]
    s["script_text"] = " ".join(sc["text"] for sc in s["scenes"])
    return s


def render(ctx: Ctx, script: dict) -> tuple[str, list[dict], float]:
    cfg, wd = ctx.cfg, ctx.workdir
    used: set = set()
    clips, licenses, total = [], [], 0.0
    for i, sc in enumerate(script["scenes"]):
        audio, d = tts.synthesize(sc["text"], wd / f"a{i}", voice=cfg["voice"], rate=cfg["rate"])
        vis = images.get_visual(sc, wd, f"v{i}", vertical=True, seed=ctx.item_id * 31 + i, used=used)
        licenses.append({k: vis.get(k) for k in ("provider", "license", "credit_url", "synthetic", "attribution", "errors")})
        dur = d + 0.2
        chunks = video.split_chunks(sc["text"], 4)
        caps = []
        if not (config.mock() or config.skip_render()):
            for j, (c, (s, e)) in enumerate(zip(chunks, video.chunk_times(chunks, dur))):
                caps.append((video.caption_png(c, W, H, wd / f"c{i}_{j}.png", "short"), s, e))
            clips.append(video.render_scene(vis, audio, caps, str(wd / f"s{i}.mp4"), W, H, dur, i))
        total += dur
    out = str(wd / "final.mp4")
    if config.mock() or config.skip_render():
        pathlib.Path(out).write_bytes(b"MOCKVIDEO")
    else:
        video.concat_finish(clips, out, wd)
    return out, licenses, total


def publish(ctx: Ctx, script: dict, path: str, licenses: list[dict]) -> dict:
    cfg = ctx.cfg
    tags = " ".join((script.get("hashtags") or [])[:5] + cfg.get("hashtags", [])[:3])
    synthetic = any(l.get("synthetic") for l in licenses)
    credits = images.attributions(licenses)
    if cfg["platform"] == "instagram":
        caption = f"{script['title']}\n\n{script.get('caption', '')}\n\n{tags}\n\n🤖 Yapay zekâ destekli içerik."
        if credits:
            caption += f"\n\n{credits}"
        return instagram.publish_reel(path, caption[:2150])
    desc = f"{script.get('caption', '')}\n\n{tags}\n\n{common.AI_DISCLOSURE}" + (f"\n\n{credits}" if credits else "")
    vid = youtube.upload_video(ctx.db, cfg["cred"], path, title=f"{script['title']} #shorts"[:100], description=desc,
                               tags=["shorts"] + [h.strip("#") for h in script.get("hashtags", [])][:8],
                               category_id=cfg.get("category_id", "24"), publish_at_ts=None, synthetic=synthetic)
    return {"id": vid, "url": f"https://youtube.com/shorts/{vid}"}


def run(channel: str) -> bool:
    ctx = common.make_ctx(channel)
    if not ctx:
        return False
    db, cfg = ctx.db, ctx.cfg
    try:
        made_today = db.count_since("short", common.day_start_ts())
        if made_today >= cfg["daily"] and not common.resumable_item(ctx, "short"):
            db.log("info", f"Günlük hedef tamam ({made_today}/{cfg['daily']})")
            return False

        item_id = common.resumable_item(ctx, "short")
        if item_id:
            ctx.item_id = item_id
            payload = db.get_item(item_id)["payload"]
            topic, source, script = db.get_item(item_id)["topic"], payload["source"], payload["script"]
        else:
            topic, ex = choose_topic(ctx)
            source, script = ex["source"], None
            ctx.item_id = db.new_item("short", topic, {"source": {k: source[k] for k in ("title", "url", "text", "lang")}})
        db.update_item(ctx.item_id, status="running")
        common.open_workdir(ctx)
        db.add_sources(ctx.item_id, [{"url": source["url"], "title": source["title"], "published": source.get("published", ""),
                                      "accessed": time.strftime("%Y-%m-%d"), "reliability": research.reliability(source["url"])[0],
                                      "reliability_label": research.reliability(source["url"])[1], "note": "Vikipedi"}])

        report = None
        feedback = ""
        for attempt in range(2):
            if script is None:
                script = write_script(ctx, topic, source, feedback)
            report = quality.check_script(script["script_text"], script["scenes"], common.previous_texts(db, exclude=ctx.item_id),
                                          source_text=source["text"])
            lo, hi = cfg["duration"]
            n_words = len(script["script_text"].split())
            est_secs = n_words / WORDS_PER_SEC
            if not (lo * 0.7 <= est_secs <= hi * 1.3):
                report.penalize(10, f"tahmini süre {est_secs:.0f}s hedef {lo}-{hi}s dışında")
            quality.apply_review(report, quality.llm_review(script["title"], script["script_text"]))
            db.update_item(ctx.item_id, payload_update={"script": script, "script_text": script["script_text"]})
            if common.qc_gate(ctx, report):
                break
            feedback = "; ".join(report.critical + report.issues)[:600]
            script = None
        else:
            raise RuntimeError(f"Senaryo kalite eşiğini geçemedi: {report.critical + report.issues}")

        path, licenses, total = render(ctx, script)
        fb = images.fallback_report(licenses)
        if fb:
            db.log("warn", fb)
        if not (config.mock() or config.skip_render()):
            lo, hi = cfg["duration"]
            mi = video.verify_video(path, W=W, H=H, min_s=lo * 0.6, max_s=min(hi * 1.5, 59 if cfg["platform"] == "youtube" else 90))
            quality.check_media(report, mi)
            if not common.qc_gate(ctx, report):
                raise RuntimeError(f"Medya kontrolü başarısız: {mi}")
        db.update_item(ctx.item_id, payload_update={"licenses": licenses, "video_path": path, "duration": total})

        if config.dry_run():
            db.update_item(ctx.item_id, status="ready")
            db.log("info", f"[kuru çalıştırma] iş #{ctx.item_id} hazır, yayınlanmadı: {script['title']}")
            return True
        res = publish(ctx, script, path, licenses)
        db.add_post(item_id=ctx.item_id, platform=cfg["platform"], external_id=res["id"], url=res.get("url", ""),
                    title=script["title"], kind="short")
        db.update_item(ctx.item_id, status="published", payload_update={"post": res})
        db.log("info", f"yayınlandı: {script['title']} → {res.get('url') or res['id']}")
        return True
    except Exception as e:  # tek iş hatası sistemi durdurmaz
        if ctx.item_id:
            common.fail_item(ctx, e)
        else:
            db.log("error", f"konu seçilemedi: {e}")
        return False
    finally:
        db.close()
