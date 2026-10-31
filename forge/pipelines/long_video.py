"""Uzun video hattı (YouTube eğlence + belgesel): fırsat puanlı konu seçimi → çok kaynaklı araştırma →
doğrulanmış iddialar → anahtar-bölüm senaryosu → seslendirme/görsel/render → thumbnail/metadata → kalite → zamanlı yayın.
"""
from __future__ import annotations

import math
import pathlib
import random
import statistics
import time

from .. import config, images, llm, quality, research, tts, video
from .. import render as renderer
from ..net import HttpError, request
from ..publish import youtube
from . import common
from .common import Ctx

W, H = 1920, 1080
WORDS_PER_SEC = 2.45
WEIGHTS = {"demand": 0.18, "search": 0.10, "competition": 0.14, "longevity": 0.14, "story": 0.16,
           "visual": 0.10, "advertiser": 0.12, "fit": 0.06}
MEASURED = {"demand", "search", "competition"}  # gerçek veriyle ölçülebilenler; diğerleri model tahmini


def competitor_stats(query: str) -> dict | None:
    """YouTube Data API (YOUTUBE_API_KEY) ile konudaki rakip videoların gerçek izlenme verisi. Anahtar yoksa None."""
    key = config.env("YOUTUBE_API_KEY")
    if not key or config.mock():
        return None
    try:
        s = request("GET", "https://www.googleapis.com/youtube/v3/search", params={
            "part": "snippet", "q": query, "type": "video", "order": "viewCount", "maxResults": 12,
            "relevanceLanguage": "tr", "regionCode": "TR", "key": key}).json()
        ids = ",".join(i["id"]["videoId"] for i in s.get("items", []))
        if not ids:
            return None
        v = request("GET", "https://www.googleapis.com/youtube/v3/videos", params={"part": "statistics", "id": ids, "key": key}).json()
        views = [int(i["statistics"].get("viewCount", 0)) for i in v.get("items", [])]
        return {"median_views": statistics.median(views), "n": len(views), "big": sum(1 for x in views if x > 1_000_000)}
    except (HttpError, KeyError):
        return None


def score_topic(c: dict, trending: list[str], comp: dict | None) -> dict:
    parts: dict[str, float | None] = {k: None for k in WEIGHTS}
    for k in ("longevity", "story", "visual", "advertiser", "fit"):
        parts[k] = float(c.get(k, 0))
    t = c["topic"].lower()
    if trending:
        parts["search"] = 100.0 if any(w.lower() in t or t in w.lower() for w in trending) else None
    if comp:
        parts["demand"] = max(0.0, min(100.0, (math.log10(max(comp["median_views"], 1)) - 3) * 25))
        parts["competition"] = max(0.0, 100.0 - comp["big"] * 12)
    avail = {k: v for k, v in parts.items() if v is not None}
    total_w = sum(WEIGHTS[k] for k in avail)
    score = sum(WEIGHTS[k] * v for k, v in avail.items()) / total_w if total_w else 0.0
    return {"score": round(score, 1), "coverage": round(total_w, 2), "parts": parts,
            "measured": sorted(k for k in avail if k in MEASURED),
            "note": "ölçülen: " + (", ".join(k for k in avail if k in MEASURED) or "yok") + "; kalan kriterler model tahmini"}


def pick_topics(ctx: Ctx) -> list[dict]:
    cfg, db = ctx.cfg, ctx.db
    recent = db.recent_topics(120)
    trending = research.trends_tr(20)
    prompt = (
        f"Kanal: {cfg['title']}. Persona: {cfg['persona']}\nKategoriler: {', '.join(cfg['categories'])}\n"
        f"İşlenmiş konular (ÇAKIŞMA YOK): {'; '.join(recent[:60]) or 'yok'}\n"
        f"Öğrenilmiş ipuçları: {common.strategy_hints(ctx.channel)}\n"
        f"Türkiye arama gündemi (ilham): {', '.join(trending[:15]) or 'erişilemedi'}\n\n"
        "8 farklı uzun video fikri üret. Her biri için 0-100 arası KENDİ tahminlerini ver: longevity (uzun vadeli "
        "izlenebilirlik), story (hikâye potansiyeli), visual (görselleştirilebilirlik), advertiser (reklamveren "
        "uygunluğu), fit (kanal uyumu). search_queries: kaynak araştırması için 3 sorgu (en az 1'i İngilizce).\n"
        'Sadece JSON: {"topics":[{"topic":"...","category":"...","angle":"özgün bakış açısı","search_queries":["..."],'
        '"longevity":0,"story":0,"visual":0,"advertiser":0,"fit":0}]}'
    )
    data = llm.generate_json(prompt, task="long_topics", required=("topics",), temperature=0.9)
    cands = [c for c in data["topics"] if c.get("topic") and not common.too_similar(c["topic"], recent)]
    pre = sorted(cands, key=lambda c: -sum(float(c.get(k, 0)) for k in ("longevity", "story", "visual", "advertiser")))[:5]
    for c in pre:
        comp = competitor_stats((c.get("search_queries") or [c["topic"]])[0])
        c["opportunity"] = score_topic(c, trending, comp)
        c["competitor"] = comp
    return sorted(pre, key=lambda c: -c["opportunity"]["score"])


def extract_claims(ctx: Ctx, research_res: dict, topic: str) -> list[dict]:
    pack = research.fact_pack(research_res["sources"], 2200)
    prompt = (
        f"Konu: {topic}\nAşağıdaki kaynak metinlerden videoda kullanılabilecek 15-30 somut iddia/veri çıkar. "
        "Her iddia için hangi kaynak numaralarında (S1, S2...) geçtiğini yaz. Kaynaklar birbiriyle çelişen sayılar "
        "içeriyorsa 'conflict': true işaretle ve iki değeri de açıkla. Kaynakta olmayan bilgi EKLEME.\n\n"
        f"{pack}\n\nSadece JSON: {{\"claims\":[{{\"claim\":\"...\",\"source_ids\":[1,2],\"conflict\":false}}]}}"
    )
    data = llm.generate_json(prompt, task="long_facts", required=("claims",), temperature=0.2)
    by_id = {s["id"]: s for s in research_res["sources"]}
    out = []
    for cl in data["claims"]:
        ids = [i for i in cl.get("source_ids", []) if i in by_id]
        doms = {research.base_domain(research.domain_of(by_id[i]["url"])) + (by_id[i].get("lang", "") if "wikipedia" in by_id[i]["url"] else "")
                for i in ids}
        status = "celiski" if cl.get("conflict") else "dogrulandi" if len(doms) >= 2 else "tek_kaynak" if ids else "dogrulanamadi"
        out.append({"claim": cl["claim"], "source_ids": ids, "status": status})
    return out


def build_script(ctx: Ctx, topic: str, cand: dict, claims: list[dict], feedback: str = "") -> dict:
    cfg = ctx.cfg
    lo, hi = cfg["duration_min"]
    minutes = random.uniform(lo + 0.5, hi - 0.5)
    total_words = int(minutes * 60 * WORDS_PER_SEC)
    usable = [c for c in claims if c["status"] in ("dogrulandi", "tek_kaynak", "celiski")]
    claim_txt = "\n".join(f"[{i}] ({c['status']}; kaynaklar {c['source_ids']}) {c['claim']}" for i, c in enumerate(usable))
    n_sections = max(4, round(minutes / 1.7))

    outline = llm.generate_json(
        f"Persona: {cfg['persona']}\nKonu: {topic}\nAçı: {cand.get('angle', '')}\n"
        f"{n_sections} bölümlük bir video planı yap (ilk bölüm = ilk 20 saniyede güçlü merak kancası + konunun önemi; "
        "son bölüm = tatmin edici sonuç; uygunsa Türkiye bağlantısı). Her bölümde kullanılacak iddia numaralarını ver. "
        "'celiski' durumlu iddiaları yalnızca iki değeri de belirterek, 'tek_kaynak' olanları '...e göre' diyerek kullan.\n"
        f"İDDİALER:\n{claim_txt}\n{('Önceki denemenin sorunları: ' + feedback) if feedback else ''}\n"
        'Sadece JSON: {"sections":[{"heading":"...","goal":"...","claim_ids":[0,1]}]}',
        task="long_outline", required=("sections",), temperature=0.7)

    scenes, sections_meta, prev = [], [], ""
    per_section = total_words // max(len(outline["sections"]), 1)
    for si, sec in enumerate(outline["sections"]):
        ids = [i for i in sec.get("claim_ids", []) if isinstance(i, int) and 0 <= i < len(usable)]
        sec_claims = "\n".join(f"[{i}] ({usable[i]['status']}; kaynaklar {usable[i]['source_ids']}) {usable[i]['claim']}" for i in ids)
        data = llm.generate_json(
            f"Persona: {cfg['persona']}\nVideo konusu: {topic}\nBölüm {si + 1}/{len(outline['sections'])}: {sec['heading']} — {sec['goal']}\n"
            f"Hedef: ~{per_section} kelime Türkçe anlatım; her sahne 1-2 cümle (8-25 kelime). Önceki bölümün son cümlesi: {prev or '—'}\n"
            "Yalnızca aşağıdaki iddialardaki bilgi ve sayıları kullan; sayı uydurma. Sayıları, yılları ve ölçüleri RAKAMLA yaz "
            "(ör. 1969, 24.000 konteyner); yazıyla yazma. Konuşma dili gibi yaz, virgülleri doğru kullan; sahne başına en fazla 1-2 "
            "kelimeyi *yıldız* içine alarak vurgula. Doğal, özgün, kalıp cümlesiz yaz; "
            f"{'bu bir belgesel: ciddi ve akıcı ton.' if cfg.get('research') == 'deep' else 'esprili ve enerjik ton, ama bilgi doğru kalsın.'}\n"
            "visual_type: stock (stok video/foto), ai (yapay zekâ illüstrasyon) veya chart (YALNIZCA iddialardaki sayılarla "
            "karşılaştırma grafiği; chart alanını doldur). visual_query/visual_prompt İNGİLİZCE.\n"
            f"İDDİALAR:\n{sec_claims or 'yok'}\n"
            'Sadece JSON: {"scenes":[{"text":"...","visual_type":"stock|ai|chart","visual_query":"...","visual_prompt":"...",'
            '"source_ids":[1],"chart":{"title":"","unit":"","bars":[{"label":"","value":0}]}}]}',
            task="long_section", required=("scenes",), temperature=0.8)
        start_idx = len(scenes)
        for sc in data["scenes"]:
            sc["section"] = si
            scenes.append(sc)
        sections_meta.append({"heading": sec["heading"], "first_scene": start_idx})
        prev = data["scenes"][-1]["text"] if data["scenes"] else prev
    text = tts.strip_markup(" ".join(s["text"] for s in scenes))
    return {"scenes": scenes, "sections": sections_meta, "script_text": text, "target_minutes": minutes}


def make_metadata(ctx: Ctx, topic: str, script: dict, sources: list[dict]) -> dict:
    cfg = ctx.cfg
    outline = "\n".join(f"- {s['heading']}" for s in script["sections"])
    meta = llm.generate_json(
        f"Kanal: {cfg['title']}. Konu: {topic}\nBölümler:\n{outline}\nSenaryo özeti için ilk sahneler: "
        f"{' '.join(s['text'] for s in script['scenes'][:3])}\n"
        "YouTube için: dürüst, merak uyandıran ama yanıltıcı olmayan Türkçe başlık (≤70 karakter), 2-3 paragraflık açıklama "
        "(bölüm listesi ve kaynaklar ayrıca eklenecek, yazma), 12 etiket, 3 farklı thumbnail yazısı (her biri ≤4 kelime, "
        "videonun gerçek içeriğini yansıtmalı), thumbnail için İngilizce görsel promptu (metinsiz, sinematik).\n"
        'Sadece JSON: {"title":"...","description":"...","tags":["..."],"thumbnail_texts":["..","..",".."],"thumbnail_prompt":"..."}',
        task="long_meta", required=("title", "description", "thumbnail_texts"), temperature=0.7)
    return meta


def render(ctx: Ctx, script: dict) -> dict:
    cfg, wd = ctx.cfg, ctx.workdir
    used: set = set()
    fake = config.mock() or config.skip_render()
    speeches = tts.speak_all(ctx.db, [sc["text"] for sc in script["scenes"]], wd, cfg, seed=ctx.item_id)
    first_of_section = {s["first_scene"]: s["heading"] for s in script["sections"]}
    scenes, licenses = [], []
    for i, sc in enumerate(script["scenes"]):
        if sc.get("visual_type") == "chart" and sc.get("chart", {}).get("bars") and not fake:
            vis = {"path": video.chart_png(sc["chart"], W, H, wd / f"v{i}.png"), "type": "image", "provider": "chart",
                   "license": "özgün grafik", "credit_url": "", "synthetic": False}
        else:
            vis = images.get_visual(sc, wd, f"v{i}", vertical=False, seed=ctx.item_id * 17 + i, used=used)
        licenses.append({k: vis.get(k) for k in ("provider", "license", "credit_url", "synthetic", "attribution", "errors")})
        scenes.append({"speech": speeches[i], "visual": vis, "text": sc["text"], "title": first_of_section.get(i)})
    out = str(wd / "final.mp4")
    if fake:
        pathlib.Path(out).write_bytes(b"MOCKVIDEO")
        starts, t = [], 0.0
        for s in speeches:
            starts.append(t)
            t += s.duration + 0.65
    else:
        r = renderer.render_video(scenes, out, wd, W=W, H=H, kind="long", seed=ctx.item_id,
                                log=lambda lvl, msg: ctx.db.log(lvl, msg))
        starts, t = r["speech_starts"], r["duration"]
        ctx.db.log("info", f"render modu: {r['mode']}, ses: {speeches[0].provider}")
    srt = []
    for i, sp in enumerate(speeches):
        marks = sp.marks or [(0.0, sp.duration, tts.strip_markup(script["scenes"][i]["text"]))]
        for ms, me, txt in marks:
            chunks = video.split_chunks(tts.strip_markup(txt), 14)
            for c, (s, e) in zip(chunks, video.chunk_times(chunks, me - ms)):
                srt.append((starts[i] + ms + s, starts[i] + ms + e, c))
    srt_path = video.write_srt(srt, wd / "captions.srt")
    chap = []
    for sec in script["sections"]:
        chap.append((max(starts[sec["first_scene"]] - renderer.HEAD, 0) if sec["first_scene"] < len(starts) else 0, sec["heading"]))
    return {"path": out, "licenses": licenses, "duration": t, "srt": srt_path, "chapters": chap}


def fmt_ts(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 60}:{sec % 60:02d}" if sec < 3600 else f"{sec // 3600}:{sec // 60 % 60:02d}:{sec % 60:02d}"


def build_description(meta: dict, chapters, sources: list[dict], deep: bool, credits: str = "") -> str:
    parts = [meta["description"].replace("<", "").replace(">", "").strip()]
    if len(chapters) >= 3:
        ch = [(0.0, chapters[0][1])] + [(s, h) for s, h in chapters[1:]]
        parts.append("Bölümler:\n" + "\n".join(f"{fmt_ts(s)} {h}" for s, h in ch))
    if deep and sources:
        parts.append("Kaynaklar:\n" + "\n".join(f"- {s['title'][:80]} {s['url']}" for s in sources[:10]))
    if credits:
        parts.append(credits)
    parts.append(common.AI_DISCLOSURE)
    tags =" ".join("#" + t.replace(" ", "") for t in meta.get("tags", [])[:3])
    if tags:
        parts.append(tags)
    return "\n\n".join(parts)[:4900]


def thumbnails(ctx: Ctx, meta: dict) -> str | None:
    if config.mock() or config.skip_render():
        return None
    wd = ctx.workdir
    base = images.get_visual({"visual_type": "ai", "visual_prompt": meta.get("thumbnail_prompt") or meta["title"]},
                             wd, "thumb_base", vertical=False, seed=ctx.item_id, used=set())
    results = video.make_thumbnails(base["path"], meta["thumbnail_texts"], wd)
    ctx.db.update_item(ctx.item_id, payload_update={"thumbnails": results})
    return results[0]["path"]


def run(channel: str) -> bool:
    ctx = common.make_ctx(channel)
    if not ctx:
        return False
    db, cfg = ctx.db, ctx.cfg
    try:
        if "weekly" in cfg:
            made, target = db.count_since("long", common.week_start_ts()), cfg["weekly"]
        else:
            made, target = db.count_since("long", common.day_start_ts()), cfg["daily"]
        if made >= target and not common.resumable_item(ctx, "long"):
            db.log("info", f"hedef tamam ({made}/{target})")
            return False

        item_id = common.resumable_item(ctx, "long")
        script = research_res = claims = None
        if item_id:
            ctx.item_id = item_id
            it = db.get_item(item_id)
            topic, cand = it["topic"], it["payload"]["candidate"]
            script, claims = it["payload"].get("script"), it["payload"]["claims"]
            research_res = it["payload"]["research"]
        else:
            # --- konu seçimi + araştırma (yetersiz kaynakta otomatik reddet) ---
            chosen = None
            for cand in pick_topics(ctx):
                res = research.gather(cand["topic"], cand.get("search_queries") or [cand["topic"]],
                                      mode=cfg["research"], min_sources=cfg["min_sources"])
                if res["ok"]:
                    chosen = (cand, res)
                    break
                rid = db.new_item("long", cand["topic"], {"candidate": cand, "reject": res["reasons"]}, status="rejected")
                db.log("warn", f"konu reddedildi ({cand['topic']}): {res['reasons']}")
            if not chosen:
                raise RuntimeError("yeterli kaynağı olan konu bulunamadı")
            cand, research_res = chosen
            topic = cand["topic"]
            claims = extract_claims(ctx, research_res, topic)
            slim = [{k: s[k] for k in ("id", "url", "title", "text", "lang", "reliability", "reliability_label", "accessed", "published") if k in s}
                    for s in research_res["sources"]]
            research_res = {**research_res, "sources": slim}
            ctx.item_id = db.new_item("long", topic, {"candidate": cand, "research": research_res, "claims": claims})
            db.log("info", f"seçilen konu: {topic} (fırsat puanı {cand['opportunity']['score']}, {cand['opportunity']['note']})")
        db.update_item(ctx.item_id, status="running")
        common.open_workdir(ctx)
        sources = research_res["sources"]
        db.add_sources(ctx.item_id, [{"url": s["url"], "title": s["title"], "published": s.get("published", ""),
                                      "accessed": s.get("accessed"), "reliability": s.get("reliability"),
                                      "reliability_label": s.get("reliability_label"), "note": ""} for s in sources])
        db.add_claims(ctx.item_id, claims)

        # --- senaryo + kalite kapısı ---
        source_text = " ".join(s["text"] for s in sources)
        report, feedback = None, ""
        for attempt in range(2):
            if script is None:
                script = build_script(ctx, topic, cand, claims, feedback)
            deep = cfg.get("research") == "deep"
            report = quality.check_script(script["script_text"], script["scenes"], common.previous_texts(db, exclude=ctx.item_id),
                                          source_text=source_text, require_sources=deep,
                                          valid_source_ids={s["id"] for s in sources})
            est_min = len(script["script_text"].split()) / WORDS_PER_SEC / 60
            lo, hi = cfg["duration_min"]
            if not (lo * 0.8 <= est_min <= hi * 1.2):
                report.penalize(10, f"tahmini süre {est_min:.1f} dk hedef {lo}-{hi} dk dışında")
            quality.apply_review(report, quality.llm_review(topic, script["script_text"],
                                                            "Doğrulanmış iddialar: " + "; ".join(c["claim"] for c in claims[:25])))
            db.update_item(ctx.item_id, payload_update={"script": script, "script_text": script["script_text"]})
            if common.qc_gate(ctx, report):
                break
            feedback = "; ".join(report.critical + report.issues)[:700]
            script = None
        else:
            raise RuntimeError(f"Senaryo eşiği geçemedi: {report.critical + report.issues}")

        meta = make_metadata(ctx, topic, script, sources)
        r = render(ctx, script)
        if not (config.mock() or config.skip_render()):
            lo, hi = cfg["duration_min"]
            mi = video.verify_video(r["path"], W=W, H=H, min_s=lo * 60 * 0.7, max_s=hi * 60 * 1.4)
            quality.check_media(report, mi)
            if not common.qc_gate(ctx, report):
                raise RuntimeError(f"Medya kontrolü başarısız: {mi}")
        thumb = thumbnails(ctx, meta)
        fb = images.fallback_report(r["licenses"])
        if fb:
            db.log("warn", fb)
        desc = build_description(meta, r["chapters"], sources, cfg.get("research") == "deep",
                                 images.attributions(r["licenses"], 10))
        synthetic = any(l.get("synthetic") for l in r["licenses"])
        db.update_item(ctx.item_id, payload_update={"meta": meta, "licenses": r["licenses"], "video_path": r["path"],
                                                    "duration": r["duration"], "description": desc})

        if config.dry_run():
            db.update_item(ctx.item_id, status="ready")
            db.log("info", f"[kuru çalıştırma] iş #{ctx.item_id} hazır: {meta['title']} ({r['duration'] / 60:.1f} dk)")
            return True

        slot = common.next_publish_slot(db, hour_utc=cfg["publish_hour_utc"], weekdays=cfg.get("publish_weekdays"))
        vid = youtube.upload_video(db, cfg["cred"], r["path"], title=meta["title"], description=desc,
                                   tags=meta.get("tags", []), category_id=cfg.get("category_id", "24"),
                                   publish_at_ts=slot, synthetic=synthetic)
        for what, fn in (("thumbnail", lambda: thumb and youtube.set_thumbnail(db, cfg["cred"], vid, thumb)),
                         ("altyazı", lambda: youtube.upload_captions(db, cfg["cred"], vid, r["srt"])),
                         ("oynatma listesi", lambda: youtube.add_to_playlist(
                             db, cfg["cred"], youtube.ensure_playlist(db, cfg["cred"], cfg["playlist"]), vid))):
            try:
                fn()
            except (HttpError, youtube.YouTubeAuthError) as e:
                db.log("warn", f"{what} eklenemedi (video yüklendi): {e}")
        st = youtube.video_status(cfg["cred"], vid).get("status", {})
        db.log("info", f"YouTube durumu: upload={st.get('uploadStatus')} gizlilik={st.get('privacyStatus')} publishAt={st.get('publishAt')}"
                       f" ret={st.get('rejectionReason', '-')}")
        if st.get("privacyStatus") == "private" and not st.get("publishAt"):
            db.log("warn", "Video 'özel' kaldı: API projesi henüz denetimden geçmemiş olabilir (SETUP.md → YouTube denetimi)")
        db.add_post(item_id=ctx.item_id, platform="youtube", external_id=vid, url=f"https://youtu.be/{vid}",
                    title=meta["title"], kind="long", scheduled_ts=slot, status="scheduled")
        db.update_item(ctx.item_id, status="published", payload_update={"post": {"id": vid, "publish_at": slot}})
        return True
    except Exception as e:
        if ctx.item_id:
            common.fail_item(ctx, e)
        else:
            db.log("error", f"uzun video başlatılamadı: {type(e).__name__}: {e}")
        return False
    finally:
        db.close()
