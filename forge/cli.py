"""Komut satırı: python -m forge <komut> [kanal]"""
from __future__ import annotations

import sys

from . import analytics, config, dashboard, strategy
from .db import DB
from .pipelines import long_video, short_video, tweets

USAGE = """Kullanım: python -m forge <komut> [kanal]
  short <kanal>        kısa video üret ve yayınla (ig_facts, yt_fun_shorts)
  long <kanal>         uzun video üret ve zamanlı yayınla (yt_fun, yt_doc)
  tweets-plan <kanal>  günlük gönderi paketini üret ve kuyruğa al (x_tech)
  tweets-post <kanal>  zamanı gelen gönderileri yayınla
  analytics [kanal]    performans metriklerini topla
  strategy [kanal]     haftalık strateji güncellemesi
  dashboard            docs/index.html panelini üret
  health               gizli anahtarların ve bağlantıların durumunu kontrol et
  ig-refresh           Instagram token'ını yenile (yeni token'ı GitHub Secret'a elle yaz)
"""


def _channels(arg: str | None) -> list[str]:
    return [arg] if arg and arg != "all" else list(config.CHANNELS)


def health() -> int:
    from .publish import instagram, x, youtube
    checks = {
        "GEMINI_API_KEY (LLM)": bool(config.env("GEMINI_API_KEY")),
        "GROQ_API_KEY (yedek LLM, isteğe bağlı)": bool(config.env("GROQ_API_KEY")),
        "PEXELS_API_KEY (stok görsel, isteğe bağlı)": bool(config.env("PEXELS_API_KEY")),
        "YOUTUBE_API_KEY (rakip analizi, isteğe bağlı)": bool(config.env("YOUTUBE_API_KEY")),
    }
    for k, v in checks.items():
        print(("OK     " if v else "EKSİK  ") + k)
    for name, fn in (("YouTube eğlence", lambda: youtube.access_token("youtube_fun")),
                     ("YouTube belgesel", lambda: youtube.access_token("youtube_doc")),
                     ("Instagram", lambda: instagram.account_stats()), ("X", lambda: x.me())):
        try:
            r = fn()
            print(f"OK     {name} bağlantısı" + (f": {r}" if isinstance(r, dict) else ""))
        except Exception as e:
            print(f"HATA   {name}: {str(e)[:160]}")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(USAGE)
        return 0
    cmd, arg = argv[1], (argv[2] if len(argv) > 2 else None)
    if cmd == "short" and arg:
        short_video.run(arg)
    elif cmd == "long" and arg:
        long_video.run(arg)
    elif cmd == "tweets-plan" and arg:
        tweets.plan(arg)
    elif cmd == "tweets-post" and arg:
        tweets.post_due(arg)
    elif cmd == "analytics":
        for ch in _channels(arg):
            if (config.STATE_DIR / f"{ch}.db").exists():
                try:
                    analytics.collect(ch)
                except Exception as e:
                    print(f"[{ch}] analitik hatası: {e}")
    elif cmd == "strategy":
        for ch in _channels(arg):
            if (config.STATE_DIR / f"{ch}.db").exists():
                try:
                    strategy.update(ch)
                except Exception as e:
                    print(f"[{ch}] strateji hatası: {e}")
    elif cmd == "dashboard":
        print(dashboard.build())
    elif cmd == "health":
        return health()
    elif cmd == "ig-refresh":
        from .publish import instagram
        r = instagram.refresh_token()
        print("Yeni token alındı. GitHub → Settings → Secrets → IG_ACCESS_TOKEN değerini bununla güncelle:")
        print(r.get("access_token"))
    else:
        print(USAGE)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
