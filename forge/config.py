"""Merkezi ayarlar: yollar, ortam değişkenleri, kanal tanımları, kontrol dosyası."""
from __future__ import annotations

import json
import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "state"
OUT_DIR = ROOT / "out"
DOCS_DIR = ROOT / "docs"
CONTROL_FILE = STATE_DIR / "control.json"


def env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def mock() -> bool:
    """Test modu: gerçek API'ye gitmez, yayın yapmaz."""
    return env("FORGE_MOCK") == "1"


def dry_run() -> bool:
    """Üretir ama yayınlamaz. Güvenli varsayılan: FORGE_LIVE=1 açıkça verilmedikçe yayın yapılmaz."""
    return mock() or env("FORGE_DRY_RUN") == "1" or env("FORGE_LIVE") != "1"


def skip_render() -> bool:
    """Medya render'ını atlar (FFmpeg/Pillow olmayan ortamda mantık testi için)."""
    return env("FORGE_SKIP_RENDER") == "1"


# ---------------------------------------------------------------------------
# Kanal tanımları. Her kanalın kendi kalıcı durumu: state/<kanal>.db
# ---------------------------------------------------------------------------
CHANNELS: dict[str, dict] = {
    # 1) Instagram: günde 5 Reels
    "ig_facts": {
        "platform": "instagram",
        "kind": "short",
        "lang": "tr",
        "title": "Bunu Biliyor muydun?",
        "persona": (
            "Enerjik, meraklı ve net konuşan bir Türkçe bilim-doğa-uzay anlatıcısı. "
            "Kısa cümleler kurar, ilk cümlede şaşırtıcı bir iddiayla izleyiciyi yakalar."
        ),
        "categories": ["uzay", "insan vücudu", "hayvanlar", "okyanus", "fizik", "tarih", "teknoloji", "doğa olayları"],
        "voice": "tr-TR-AhmetNeural",
        "rate": "+3%",
        "gemini_voice": "Puck",
        "tts_style": "Enerjik, meraklı ve samimi bir hikâye anlatıcısı gibi, Türkçe aksanıyla kusursuz oku.",
        "duration": [24, 40],
        "daily": 5,
        "hashtags": ["#bunubiliyormuydun", "#bilim", "#ilginçbilgiler", "#keşfet", "#reels"],
        "min_quality": 75,
        "cred": "instagram",
    },
    # 2) X: günde 10-15 gönderi
    "x_tech": {
        "platform": "x",
        "kind": "tweets",
        "lang": "tr",
        "title": "Yapay Zeka & Teknoloji",
        "persona": (
            "Türkçe konuşan, pratik, hafif esprili bir yapay zekâ ve teknoloji yorumcusu. "
            "Haber aktarmaz; kendi yorumunu ve işe yarar çıkarımını yazar."
        ),
        "daily": 12,
        "monthly_post_limit": 500,
        "feeds": [
            "https://webrazzi.com/feed/",
            "https://techcrunch.com/category/artificial-intelligence/feed/",
            "https://www.theverge.com/rss/index.xml",
            "https://feeds.arstechnica.com/arstechnica/technology-lab",
        ],
        "min_quality": 70,
        "cred": "x",
    },
    # 3a) YouTube eğlence kanalı: günlük uzun video
    "yt_fun": {
        "platform": "youtube",
        "kind": "long",
        "lang": "tr",
        "title": "Ya Böyle Olsaydı?",
        "persona": (
            "Esprili, hızlı ve samimi bir Türkçe anlatıcı. 'Ya şöyle olsaydı?' senaryolarını ve "
            "tuhaf ama gerçek hikâyeleri bilimsel temelle, eğlenceli bir dille anlatır."
        ),
        "categories": ["ya şöyle olsaydı senaryoları", "tuhaf ama gerçek tarih", "dünyanın garip yerleri", "bilim kurgu gibi gerçekler", "ilginç rekorlar"],
        "voice": "tr-TR-AhmetNeural",
        "rate": "+4%",
        "gemini_voice": "Puck",
        "tts_style": "Esprili, enerjik ve sıcak bir sunucu gibi, Türkçe aksanıyla kusursuz oku.",
        "duration_min": [7, 10],
        "min_sources": 3,
        "daily": 1,
        "playlist": "Ya Böyle Olsaydı?",
        "publish_hour_utc": 15,
        "min_quality": 78,
        "cred": "youtube_fun",
        "research": "light",
        "category_id": "24",  # Entertainment
    },
    # 3b) Aynı kanalın Shorts hattı: günde 4
    "yt_fun_shorts": {
        "platform": "youtube",
        "kind": "short",
        "lang": "tr",
        "title": "Ya Böyle Olsaydı? Shorts",
        "persona": (
            "Esprili, hızlı, samimi Türkçe anlatıcı. 30 saniyede tek bir 'ya şöyle olsaydı?' "
            "veya tuhaf-ama-gerçek hikâyeyi punchline ile bitirir."
        ),
        "categories": ["ya şöyle olsaydı", "tuhaf rekorlar", "garip yasalar", "komik tarih anıları", "hayvanlar ve şaşırtıcı gerçekler"],
        "voice": "tr-TR-AhmetNeural",
        "rate": "+5%",
        "gemini_voice": "Puck",
        "tts_style": "Esprili, enerjik ve sıcak bir sunucu gibi, Türkçe aksanıyla kusursuz oku.",
        "duration": [22, 45],
        "daily": 4,
        "publish_hours_utc": [6, 9, 13, 17],
        "hashtags": ["#shorts", "#yaböyleolsaydı", "#ilginç"],
        "min_quality": 75,
        "cred": "youtube_fun",
        "category_id": "24",
    },
    # 4) YouTube belgesel: haftada 2 uzun video (kullanıcının verdiği spesifikasyon)
    "yt_doc": {
        "platform": "youtube",
        "kind": "long",
        "lang": "tr",
        "title": "Dünya Nasıl Çalışıyor?",
        "persona": (
            "Güven veren, tok sesli, profesyonel bir Türkçe belgesel anlatıcısı. Teknik ve ekonomik konuları "
            "net örneklerle, doğrulanmış verilerle ve dramatik ritimle anlatır."
        ),
        "categories": [
            "uluslararası lojistik ve küresel ticaret", "büyük limanlar ve nakliye", "ekonomi ve finansal sistemler",
            "mühendislik ve altyapı projeleri", "enerji sistemleri", "havacılık ve ulaşım",
            "teknoloji ve yapay zekâ", "ülkelerin endüstriyel sistemleri", "küresel tedarik zincirleri",
        ],
        "voice": "tr-TR-AhmetNeural",
        "rate": "-4%",
        "gemini_voice": "Charon",
        "tts_style": "Ağırbaşlı, güven veren ve akıcı bir belgesel anlatıcısı gibi, düşünceli duraklamalarla ve Türkçe aksanıyla kusursuz oku.",
        "duration_min": [12, 18],
        "min_sources": 5,
        "weekly": 2,
        "publish_weekdays": [1, 4],  # Salı, Cuma (0=Pazartesi)
        "publish_hour_utc": 16,
        "playlist": "Dünya Nasıl Çalışıyor?",
        "min_quality": 82,
        "cred": "youtube_doc",
        "research": "deep",
        "category_id": "27",  # Education
    },
}


# ---------------------------------------------------------------------------
# Kontrol dosyası (state/control.json) – panelden değil, GitHub'da düzenlenir
# ---------------------------------------------------------------------------
DEFAULT_CONTROL = {
    "paused": False,
    "channels": {},  # {"yt_doc": {"enabled": true, "budget_usd": 0, "daily": 1}}
}


def control() -> dict:
    try:
        data = json.loads(CONTROL_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return json.loads(json.dumps(DEFAULT_CONTROL))
    data.setdefault("paused", False)
    data.setdefault("channels", {})
    return data


def channel_enabled(channel: str) -> bool:
    c = control()
    if c["paused"]:
        return False
    return bool(c["channels"].get(channel, {}).get("enabled", True))


def budget_usd(channel: str) -> float:
    return float(control()["channels"].get(channel, {}).get("budget_usd", 0.0))


def channel_cfg(channel: str) -> dict:
    cfg = dict(CHANNELS[channel])
    override = control()["channels"].get(channel, {})
    for k in ("daily", "weekly"):
        if k in override:
            cfg[k] = override[k]
    return cfg
