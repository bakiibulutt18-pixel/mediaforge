"""Haftalık strateji güncellemesi: gerçek performans verisinden öğrenir; veri yetersizse karar vermez."""
from __future__ import annotations

import json
import time

from . import config, llm
from .db import DB, now
from .pipelines.common import strategy_path

MIN_POSTS = 8
MIN_AGE_DAYS = 2


def update(channel: str) -> bool:
    db = DB(channel)
    try:
        metrics = db.latest_metrics()
        rows = []
        for p in db.posts(200):
            m = metrics.get(p["id"])
            age = (now() - (p["published_ts"] or now())) / 86400
            if m and age >= MIN_AGE_DAYS:
                hour = time.gmtime(p["published_ts"]).tm_hour
                rows.append({"title": p["title"], "kind": p["kind"], "utc_hour": hour,
                             **{k: v for k, v in m.items() if k != "ts" and v is not None}})
        if len(rows) < MIN_POSTS:
            db.log("info", f"strateji: yeterli veri yok ({len(rows)}/{MIN_POSTS} gönderi); erken karar verilmedi")
            return False
        key = "views" if any("views" in r for r in rows) else "likes"
        rows.sort(key=lambda r: -float(r.get(key, 0) or 0))
        top, bottom = rows[:6], rows[-6:]
        prompt = (
            f"Kanal: {config.CHANNELS[channel]['title']}. Aşağıda GERÇEK performans verisi var ({len(rows)} gönderi, sıralama ölçütü: {key}).\n"
            f"EN İYİLER: {json.dumps(top, ensure_ascii=False)}\nEN KÖTÜLER: {json.dumps(bottom, ensure_ascii=False)}\n"
            "Ortak özellikleri çıkar: konu türü, başlık kalıbı, yayın saati. Örneklem küçükse temkinli ol; "
            "yanıltıcı başlık veya clickbait ÖNERME. Sadece JSON: "
            '{"prefer":["..."],"avoid":["..."],"title_patterns":["..."],"best_utc_hours":[0],"notes":"..."}'
        )
        s = llm.generate_json(prompt, task="strategy", required=("prefer",), temperature=0.3)
        s.update({"generated_at": time.strftime("%Y-%m-%d"), "n_posts": len(rows)})
        strategy_path(channel).write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
        db.log("info", f"strateji güncellendi ({len(rows)} gönderi): {s.get('prefer')}")
        return True
    finally:
        db.close()
