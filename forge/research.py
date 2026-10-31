"""Araştırma: Vikipedi, Gemini arama-temellendirme (google_search), RSS haberleri, Google Trends.

Dürüstlük notu: kaynak güvenilirliği alan adı sezgisiyle puanlanır; bu bir tahmindir, kesin hüküm değildir.
Bir iddia ancak bağımsız (farklı alan adı) en az 2 kaynakta geçiyorsa 'dogrulandi' sayılır.
"""
from __future__ import annotations

import html
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET

from . import config, llm
from .net import HttpError, request

TRUSTED_SUFFIXES = (".gov", ".gov.tr", ".edu", ".edu.tr", ".int", ".mil", ".ac.uk", ".gov.uk")
TRUSTED_DOMAINS = {
    "wikipedia.org": 0.7, "britannica.com": 0.8, "nature.com": 0.95, "science.org": 0.95, "worldbank.org": 0.95,
    "imf.org": 0.95, "oecd.org": 0.95, "un.org": 0.95, "wto.org": 0.95, "iea.org": 0.95, "nasa.gov": 0.95,
    "reuters.com": 0.85, "apnews.com": 0.85, "bbc.com": 0.8, "bbc.co.uk": 0.8, "ft.com": 0.8, "economist.com": 0.8,
    "bloomberg.com": 0.8, "tuik.gov.tr": 0.95, "tcmb.gov.tr": 0.95, "unctad.org": 0.95, "wikimedia.org": 0.6,
    "arxiv.org": 0.75, "sciencedirect.com": 0.85, "springer.com": 0.85, "ieee.org": 0.9,
}


def domain_of(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def base_domain(host: str) -> str:
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in ("co", "com", "gov", "edu", "ac", "org"):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def reliability(url: str) -> tuple[float, str]:
    host = domain_of(url)
    base = base_domain(host)
    if base in TRUSTED_DOMAINS:
        s = TRUSTED_DOMAINS[base]
        return s, "bilinen güvenilir kaynak"
    if host.endswith(TRUSTED_SUFFIXES):
        return 0.9, "resmî/akademik alan adı"
    return 0.45, "bilinmeyen kaynak (sezgisel puan)"


# -- Vikipedi --------------------------------------------------------------
def wiki_search(query: str, lang: str = "tr", n: int = 3) -> list[str]:
    if config.mock():
        from . import mockdata
        return mockdata.wiki_titles(query)
    r = request("GET", f"https://{lang}.wikipedia.org/w/api.php", params={
        "action": "query", "list": "search", "srsearch": query, "srlimit": n, "format": "json"}).json()
    return [x["title"] for x in r.get("query", {}).get("search", [])]


def wiki_extract(title: str, lang: str = "tr", limit: int = 6000) -> dict | None:
    if config.mock():
        from . import mockdata
        return mockdata.wiki_page(title, lang)
    r = request("GET", f"https://{lang}.wikipedia.org/w/api.php", params={
        "action": "query", "prop": "extracts|info", "explaintext": 1, "titles": title, "redirects": 1,
        "inprop": "url", "format": "json"}).json()
    for page in r.get("query", {}).get("pages", {}).values():
        text = (page.get("extract") or "").strip()
        if text and "missing" not in page:
            return {"title": page.get("title", title), "url": page.get("fullurl"), "text": text[:limit], "lang": lang,
                    "published": (page.get("touched") or "")[:10]}
    return None


# -- Gemini arama temellendirme ----------------------------------------------
def _strip_html(raw: str, limit: int) -> str:
    raw = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer|header)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    raw = html.unescape(re.sub(r"\s+", " ", raw))
    return raw[:limit]


def grounded_sources(query: str, max_sources: int = 8) -> list[dict]:
    """Gemini'nin google_search aracıyla web kaynakları bulur, sayfaları çekip metin çıkarır."""
    if config.mock():
        from . import mockdata
        return mockdata.grounded(query)
    key = config.env("GEMINI_API_KEY")
    if not key:
        return []
    body = {
        "contents": [{"role": "user", "parts": [{"text": f"Şu konuda güvenilir, güncel kaynaklardan sayısal verili özet çıkar: {query}"}]}],
        "tools": [{"google_search": {}}],
    }
    r = None
    for rediscover in (False, True):
        try:
            model = llm.gemini_model(key, rediscover=rediscover)
            r = request("POST", f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                        json_body=body, headers={"x-goog-api-key": key}, timeout=120).json()
            break
        except HttpError as e:
            if e.status not in (400, 404) or config.env("GEMINI_MODEL"):
                return []
    if r is None:
        return []
    chunks = (r.get("candidates") or [{}])[0].get("groundingMetadata", {}).get("groundingChunks", [])
    out, seen = [], set()
    for ch in chunks[: max_sources * 2]:
        uri = ch.get("web", {}).get("uri")
        if not uri:
            continue
        try:
            resp = request("GET", uri, timeout=20, retries=1, max_bytes=400_000)
        except HttpError:
            continue
        final = resp.url
        dom = base_domain(domain_of(final))
        if dom in seen or "google." in dom:
            continue
        seen.add(dom)
        text = _strip_html(resp.text, 6000)
        if len(text) < 400:
            continue
        m = re.search(r"<title[^>]*>(.*?)</title>", resp.text, flags=re.S | re.I)
        out.append({"title": html.unescape(m.group(1).strip())[:160] if m else dom, "url": final, "text": text,
                    "published": "", "lang": "web"})
        if len(out) >= max_sources:
            break
    return out


# -- RSS / Trends --------------------------------------------------------------
def fetch_rss(url: str, n: int = 8) -> list[dict]:
    if config.mock():
        from . import mockdata
        return mockdata.rss(url)[:n]
    try:
        raw = request("GET", url, timeout=25, retries=1, max_bytes=800_000).body
        root = ET.fromstring(raw)
    except (HttpError, ET.ParseError):
        return []
    items = []
    for it in list(root.iter("item"))[:n] + list(root.iter("{http://www.w3.org/2005/Atom}entry"))[:n]:
        def g(tag):
            el = it.find(tag)
            if el is None:
                el = it.find("{http://www.w3.org/2005/Atom}" + tag)
            return (el.text or "").strip() if el is not None else ""
        link = g("link")
        if not link:
            le = it.find("{http://www.w3.org/2005/Atom}link")
            link = le.get("href", "") if le is not None else ""
        summ = _strip_html(g("description") or g("summary"), 500)
        items.append({"title": html.unescape(g("title")), "link": link, "summary": summ,
                      "published": g("pubDate") or g("updated")})
    return items[:n]


def trends_tr(n: int = 15) -> list[str]:
    """Google Trends günlük arama RSS'i (Türkiye). Erişilemezse boş döner."""
    if config.mock():
        from . import mockdata
        return mockdata.trends()
    for url in ("https://trends.google.com/trending/rss?geo=TR", "https://trends.google.com/trends/trendingsearches/daily/rss?geo=TR"):
        items = fetch_rss(url, n)
        if items:
            return [i["title"] for i in items if i["title"]]
    return []


# -- Toplu araştırma -------------------------------------------------------------
def gather(topic: str, queries: list[str], *, mode: str, min_sources: int) -> dict:
    """mode: 'light' (Vikipedi tr+en) | 'deep' (Vikipedi + web arama temellendirme).
    Dönen: {sources:[{id,url,title,text,reliability,...}], independent:int, ok:bool, reasons:[...]}
    """
    accessed = time.strftime("%Y-%m-%d")
    raw: list[dict] = []
    for q in queries[:4] or [topic]:
        for lang in ("tr", "en"):
            try:
                for t in wiki_search(q, lang, 2):
                    page = wiki_extract(t, lang)
                    if page:
                        raw.append(page)
            except HttpError:
                continue
    # Vikipedi dilleri birbirinden bağımsız sayılmaz; gerçek çeşitlilik web aramasından gelir.
    for q in (queries[:3] if mode == "deep" else queries[:1]) or [topic]:
        raw.extend(grounded_sources(q))

    sources, seen_urls, domains = [], set(), set()
    for s in raw:
        if not s.get("url") or s["url"] in seen_urls:
            continue
        seen_urls.add(s["url"])
        score, label = reliability(s["url"])
        s.update({"id": len(sources) + 1, "reliability": score, "reliability_label": label, "accessed": accessed})
        domains.add(base_domain(domain_of(s["url"])))
        sources.append(s)

    reasons = []
    independent = len(domains)
    if independent < min_sources:
        reasons.append(f"yetersiz bağımsız kaynak: {independent} < {min_sources}")
    strong = [s for s in sources if s["reliability"] >= 0.7]
    if mode == "deep" and len({base_domain(domain_of(s["url"])) for s in strong}) < 2:
        reasons.append("güçlü (güvenilirlik ≥ 0.7) bağımsız kaynak sayısı 2'den az")
    return {"sources": sources, "independent": independent, "ok": not reasons, "reasons": reasons}


def fact_pack(sources: list[dict], per_source: int = 2500) -> str:
    return "\n\n".join(
        llm.wrap_untrusted(f"S{s['id']} {s['title']} ({s['url']})", s["text"], per_source) for s in sources
    )
