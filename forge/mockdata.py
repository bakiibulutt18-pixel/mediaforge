"""FORGE_MOCK=1 için sahte LLM/araştırma yanıtları. YALNIZCA mantık testleri içindir; asla yayın yapılmaz."""
from __future__ import annotations

import json
import random
import re

_rng = random.Random()
_SYL = ["ba", "ke", "lo", "mu", "ri", "sa", "ten", "vo", "zi", "dar", "fen", "gul", "hat", "kim", "nor", "pel", "sun", "yar"]


def word() -> str:
    return "".join(_rng.choice(_SYL) for _ in range(_rng.randint(2, 4)))


def sentence(n_words: int = 12) -> str:
    return " ".join(word() for _ in range(n_words)).capitalize() + "."


def _int(pattern: str, text: str, default: int) -> int:
    m = re.search(pattern, text)
    return int(m.group(1)) if m else default


def respond(task: str, prompt: str) -> str:
    return json.dumps(_build(task, prompt), ensure_ascii=False)


def _build(task: str, prompt: str):
    if task == "short_topics":
        return {"topics": [{"topic": f"{word()} {word()} olgusu", "category": "uzay", "wiki_title": word().capitalize(),
                            "wiki_lang": "tr", "interest": _rng.randint(5, 10)} for _ in range(8)]}
    if task == "short_script":
        words = _int(r"yaklaşık (\d+) kelime", prompt, 80)
        n = 6
        return {"title": f"{word().capitalize()} {word()} sırrı",
                "scenes": [{"text": sentence(max(words // n, 5)), "visual_query": "space nebula", "visual_prompt": "nebula",
                            "visual_type": "stock"} for _ in range(n)],
                "caption": sentence(10), "hashtags": ["#bilim", "#uzay"]}
    if task == "qc_review":
        return {"misleading": 1, "policy_risk": 0, "title_matches_content": True, "factual_red_flags": [], "notes": "mock"}
    if task == "tweet_batch":
        n = _int(r"(\d+) gönderi üret", prompt, 10)
        out = []
        for i in range(n):
            t = {"type": ["take", "tip", "question"][i % 3], "text": sentence(_rng.randint(12, 25))}
            if i == 1:
                t = {"type": "poll", "text": sentence(8), "poll": {"options": [word(), word(), word()]}}
            if i == 2:
                t = {"type": "thread", "text": sentence(10), "thread": [sentence(15), sentence(15), sentence(15)]}
            out.append(t)
        return {"tweets": out}
    if task == "long_topics":
        return {"topics": [{"topic": f"{word()} {word()} sistemi nasıl çalışır", "category": "lojistik", "angle": sentence(8),
                            "search_queries": [sentence(4), "global " + word()], "longevity": _rng.randint(60, 95),
                            "story": _rng.randint(60, 95), "visual": _rng.randint(60, 95),
                            "advertiser": _rng.randint(70, 95), "fit": _rng.randint(60, 95)} for _ in range(8)]}
    if task == "long_facts":
        ids = [int(x) for x in re.findall(r"S(\d+) ", prompt)]
        ids = sorted(set(ids)) or [1]
        return {"claims": [{"claim": sentence(14), "source_ids": ids[i % len(ids):i % len(ids) + 2] or ids[:1],
                            "conflict": False} for i in range(20)]}
    if task == "long_outline":
        n = _int(r"(\d+) bölümlük", prompt, 6)
        return {"sections": [{"heading": f"{word().capitalize()} {word()}", "goal": sentence(6),
                              "claim_ids": [i, i + 1]} for i in range(n)]}
    if task == "long_section":
        words = _int(r"~(\d+) kelime", prompt, 200)
        n = max(words // 16, 3)
        scenes = [{"text": sentence(16), "visual_type": "stock", "visual_query": "container ship port",
                   "visual_prompt": "port", "source_ids": [1, 2]} for _ in range(n)]
        scenes[-1].update({"visual_type": "chart", "chart": {"title": "Örnek", "unit": "", "bars": [{"label": "A", "value": 3}]}})
        return {"scenes": scenes}
    if task == "long_meta":
        return {"title": f"{word().capitalize()} {word()} Nasıl Çalışır?", "description": sentence(30) + "\n\n" + sentence(25),
                "tags": [word() for _ in range(12)], "thumbnail_texts": [f"{word()} {word()}".upper() for _ in range(3)],
                "thumbnail_prompt": "cinematic port at dusk"}
    if task == "strategy":
        return {"prefer": ["kısa başlıklar"], "avoid": ["çok genel konular"], "title_patterns": ["Nasıl çalışır?"], "notes": "mock"}
    return {}


# -- araştırma sahte verileri --------------------------------------------------
def wiki_titles(query: str) -> list[str]:
    return [word().capitalize(), word().capitalize()]


def wiki_page(title: str, lang: str):
    text = " ".join(sentence(14) for _ in range(60))
    return {"title": title, "url": f"https://{lang}.wikipedia.org/wiki/{title}", "text": text, "lang": lang, "published": "2026-01-01"}


def grounded(query: str) -> list[dict]:
    doms = ["nasa.gov", "worldbank.org", "reuters.com", "iea.org", "nature.com", "tuik.gov.tr"]
    return [{"title": f"{d} raporu", "url": f"https://www.{d}/{word()}", "text": " ".join(sentence(14) for _ in range(40)),
             "published": "2026-02-01", "lang": "web"} for d in doms]


def rss(url: str) -> list[dict]:
    return [{"title": sentence(7), "link": f"https://example.org/{word()}{_rng.randint(1, 10**6)}", "summary": sentence(20),
             "published": ""} for _ in range(6)]


def trends() -> list[str]:
    return [word() for _ in range(8)]
