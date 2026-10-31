"""Salt-okunur izleme paneli: tüm kanal veritabanlarından statik docs/index.html (GitHub Pages) üretir.
Manuel video üretme/yayın düğmesi YOK. Durdurma/yeniden başlatma state/control.json dosyasından yapılır.
"""
from __future__ import annotations

import html
import json
import time

from . import config
from .db import DB, day_key, month_key, now
from .publish import youtube

CSS = """
:root{--bg:#0b0f17;--card:#131a26;--line:#223049;--tx:#e6edf7;--mut:#8a9bb5;--ok:#34d399;--warn:#fbbf24;--bad:#f87171;--acc:#38bdf8}
@media (prefers-color-scheme: light){:root{--bg:#f4f6fa;--card:#fff;--line:#d9e0ec;--tx:#101828;--mut:#5b6b85}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:15px/1.5 system-ui,Segoe UI,Roboto,sans-serif}
main{max-width:1180px;margin:0 auto;padding:20px 16px 60px}h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:0 0 10px}
.sub{color:var(--mut);font-size:13px}.grid{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));margin-top:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px;margin:8px 0 12px}
.kpi{background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:8px 10px}.kpi b{display:block;font-size:20px}.kpi span{color:var(--mut);font-size:12px}
table{width:100%;border-collapse:collapse;font-size:13px}td,th{text-align:left;padding:5px 6px;border-top:1px solid var(--line);vertical-align:top}th{color:var(--mut);font-weight:500}
.pill{display:inline-block;padding:1px 8px;border-radius:99px;font-size:12px;border:1px solid var(--line)}
.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}.mut{color:var(--mut)}a{color:var(--acc)}
.bar{height:6px;background:var(--line);border-radius:4px;overflow:hidden}.bar i{display:block;height:100%;background:var(--acc)}
"""


def _e(x) -> str:
    return html.escape(str(x if x is not None else ""))


def _ts(ts) -> str:
    return time.strftime("%d.%m %H:%M", time.gmtime(ts + 3 * 3600)) + " TR" if ts else "-"


def _num(n) -> str:
    return "-" if n in (None, "") else f"{int(n):,}".replace(",", ".") if isinstance(n, (int, float)) else _e(n)


def collect() -> dict:
    out = {"generated": now(), "paused": config.control()["paused"], "channels": {}}
    for ch, cfg in config.CHANNELS.items():
        path = config.STATE_DIR / f"{ch}.db"
        d = {"cfg": {k: cfg[k] for k in ("platform", "kind", "title")}, "exists": path.exists(),
             "enabled": config.channel_enabled(ch), "budget": config.budget_usd(ch)}
        if path.exists():
            db = DB(ch)
            try:
                items = db.items(limit=400)
                posts = db.posts(60)
                mets = db.latest_metrics()
                d["counts"] = {}
                for it in items:
                    d["counts"][it["status"]] = d["counts"].get(it["status"], 0) + 1
                d["running"] = [{"id": i["id"], "topic": i["topic"], "kind": i["kind"], "attempts": i["attempts"]} for i in items if i["status"] == "running"]
                d["queued"] = sorted([{"topic": i["topic"], "due": i["due_ts"]} for i in items if i["status"] == "queued"], key=lambda r: r["due"] or 0)[:8]
                d["failed"] = [{"id": i["id"], "topic": i["topic"], "error": i["error"]} for i in items if i["status"] == "failed"][:5]
                d["rejected"] = sum(1 for i in items if i["status"] == "rejected")
                d["scheduled"] = [{"title": p["title"], "ts": p["scheduled_ts"], "url": p["url"]} for p in posts if p["status"] == "scheduled" and (p["scheduled_ts"] or 0) > now()]
                d["posts"] = [{"title": p["title"], "url": p["url"], "ts": p["published_ts"], "kind": p["kind"],
                               "m": mets.get(p["id"], {})} for p in posts[:12]]
                d["channel_stats"] = mets.get(0, {})
                d["totals"] = {k: sum((mets.get(p["id"], {}).get(k) or 0) for p in posts) for k in ("views", "likes", "comments")}
                d["cost_month"] = db.month_cost()
                d["events"] = db.events(12)
                d["errors"] = db.events(5, "error")
                d["quota"] = {"youtube_units_today": db.quota_used("youtube_units", day_key()), "x_posts_month": db.quota_used("x_posts", month_key())}
                today = now() - now() % 86400
                d["today_done"] = sum(1 for i in items if i["status"] == "published" and i["updated_ts"] >= today)
                d["last_item_ts"] = items[0]["created_ts"] if items else None
                if items:
                    qs = [i["quality"] for i in items if i["quality"]]
                    d["avg_quality"] = round(sum(qs) / len(qs), 1) if qs else None
                src = db.conn.execute("SELECT MAX(accessed) a FROM sources").fetchone()
                d["last_research"] = src["a"] if src else None
            finally:
                db.close()
        out["channels"][ch] = d
    return out


def render(data: dict) -> str:
    repo = config.env("GITHUB_REPOSITORY", "")
    ctl = f"https://github.com/{repo}/edit/main/state/control.json" if repo else "state/control.json"
    cards = []
    for ch, d in data["channels"].items():
        title = f"{_e(d['cfg']['title'])} <span class='mut'>· {_e(d['cfg']['platform'])}/{_e(d['cfg']['kind'])}</span>"
        if not d["exists"]:
            cards.append(f"<div class='card'><h2>{title}</h2><p class='mut'>Henüz çalışmadı.</p></div>")
            continue
        st = "<span class='pill warn'>duraklatıldı</span>" if not d["enabled"] else "<span class='pill ok'>aktif</span>"
        cs, tot = d.get("channel_stats", {}), d.get("totals", {})
        followers = cs.get("subscribers") or cs.get("followers") or cs.get("followers_count")
        kp = [("Takipçi/abone", _num(followers)), ("Görüntülenme", _num(tot.get("views"))), ("Beğeni", _num(tot.get("likes"))),
              ("Bugün yayın", _num(d.get("today_done"))), ("KK ort.", _e(d.get("avg_quality") or "-")),
              ("Maliyet (ay)", f"${d.get('cost_month', 0):.2f}")]
        kp_html = "".join(f"<div class='kpi'><b>{v}</b><span>{k}</span></div>" for k, v in kp)
        q = d.get("quota", {})
        quota = ""
        if d["cfg"]["platform"] == "youtube":
            quota = f"<div class='sub'>YouTube kota (bugün): {q.get('youtube_units_today', 0)}/10000</div><div class='bar'><i style='width:{min(q.get('youtube_units_today', 0) / 100, 100)}%'></i></div>"
        if d["cfg"]["platform"] == "x":
            lim = config.CHANNELS[ch].get("monthly_post_limit", 500)
            quota = f"<div class='sub'>X gönderi (ay): {q.get('x_posts_month', 0)}/{lim}</div><div class='bar'><i style='width:{min(q.get('x_posts_month', 0) / lim * 100, 100)}%'></i></div>"
        run = "".join(f"<li>#{r['id']} {_e(r['topic'])} (deneme {r['attempts']})</li>" for r in d.get("running", [])) or "<li class='mut'>yok</li>"
        nxt = "".join(f"<tr><td>{_ts(r['ts'] if 'ts' in r else r.get('due'))}</td><td>{_e((r.get('title') or r.get('topic'))[:60])}</td></tr>"
                      for r in (d.get("scheduled") or d.get("queued") or [])[:6]) or "<tr><td class='mut'>planlı yayın yok</td></tr>"
        posts = "".join(
            f"<tr><td>{_ts(p['ts'])}</td><td><a href='{_e(p['url'])}'>{_e((p['title'] or '')[:46])}</a></td>"
            f"<td>{_num(p['m'].get('views'))}</td><td>{_num(p['m'].get('likes'))}</td></tr>" for p in d.get("posts", [])[:8]) or "<tr><td class='mut'>henüz yayın yok</td></tr>"
        errs = "".join(f"<li class='bad'>{_ts(e['ts'])} {_e(e['msg'][:140])}</li>" for e in d.get("errors", [])) or "<li class='ok'>son hata yok</li>"
        cnt = " · ".join(f"{_e(k)}: {v}" for k, v in sorted(d.get("counts", {}).items()))
        extra = ""
        if d["cfg"]["platform"] == "youtube":
            extra = "<div class='sub'>CTR/gösterim: API'de sunulmuyor (YouTube Studio'da görülür)</div>"
        cards.append(
            f"<div class='card'><h2>{title} {st}</h2><div class='kpis'>{kp_html}</div>{quota}{extra}"
            f"<p class='sub'>Son araştırma: {_e(d.get('last_research') or '-')} · Son üretim: {_ts(d.get('last_item_ts'))} · Reddedilen konu: {d.get('rejected', 0)}<br>{cnt}</p>"
            f"<h2>Üretimde</h2><ul>{run}</ul><h2>Sıradaki yayınlar</h2><table>{nxt}</table>"
            f"<h2>Son yayınlar</h2><table><tr><th>Zaman</th><th>Başlık</th><th>İzlenme</th><th>Beğeni</th></tr>{posts}</table>"
            f"<h2>Hatalar</h2><ul>{errs}</ul></div>")
    banner = "<span class='pill bad'>TÜM OTOMASYON DURAKLATILDI</span>" if data["paused"] else "<span class='pill ok'>otomasyon çalışıyor</span>"
    return (f"<!doctype html><html lang='tr'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>Media Engine Paneli</title><style>{CSS}</style></head><body><main>"
            f"<h1>Autonomous Media Engine {banner}</h1>"
            f"<div class='sub'>Güncellendi: {_ts(data['generated'])} · Salt okunur panel. Durdurmak/bütçe/sıklık için <a href='{_e(ctl)}'>state/control.json</a> dosyasını düzenle.</div>"
            f"<div class='grid'>{''.join(cards)}</div></main></body></html>")


def build() -> str:
    data = collect()
    config.DOCS_DIR.mkdir(parents=True, exist_ok=True)
    (config.DOCS_DIR / "data.json").write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
    (config.DOCS_DIR / ".nojekyll").write_text("", encoding="utf-8")
    path = config.DOCS_DIR / "index.html"
    path.write_text(render(data), encoding="utf-8")
    return str(path)
