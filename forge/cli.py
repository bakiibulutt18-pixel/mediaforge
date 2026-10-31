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
  demo-voice           ses örnekleri (Gemini ve Edge) → out/demo
  demo-video           örnek dikey + yatay video → out/demo
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


SAMPLE_TEXTS = [
    "1969 yılında, Ay'a ayak basan ilk insanlar *sadece* iki kişiydi. Peki ya sonrasında ne oldu? Bunu çok az kişi bilir.",
    "Üstelik, o günün bilgisayarı bugünkü bir hesap makinesinden bile *daha zayıftı*! İnanması zor, değil mi?",
    "Ve işte asıl şaşırtıcı kısım: Her şey, sadece birkaç satırlık bir kodla başladı.",
]


def demo_voice() -> int:
    """Aynı örnek metni her sağlayıcıyla seslendirir → out/demo/ses_<sağlayıcı>.mp3"""
    import os
    import pathlib

    from . import tts, video
    out = config.OUT_DIR / "demo"
    out.mkdir(parents=True, exist_ok=True)
    cfg = dict(config.CHANNELS["yt_fun_shorts"])
    text = " ".join(SAMPLE_TEXTS)
    for prov in ("gemini", "edge"):
        os.environ["TTS_PROVIDERS"] = prov
        try:
            sp = tts.speak_all(None, [text], out, cfg, seed=7)[0]
            dst = out / f"ses_{prov}.mp3"
            try:
                video.run([video.ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", "-i", sp.path, "-b:a", "128k", str(dst)])
            except Exception:
                dst = pathlib.Path(sp.path)
            print(f"OK     {prov}: {dst.name} ({sp.duration:.1f} sn)")
        except Exception as e:
            print(f"HATA   {prov}: {str(e)[:300]}")
    return 0


def demo_video() -> int:
    """Gerçek görsellerle kısa dikey + yatay örnek video → out/demo/ornek_dikey.mp4, ornek_yatay.mp4"""
    from . import images, render, tts
    out = config.OUT_DIR / "demo"
    out.mkdir(parents=True, exist_ok=True)
    queries = [("moon landing astronaut", "Astronaut on the moon, cinematic"), ("old computer circuit board", "Vintage computer, cinematic"),
               ("code on screen", "Programmer typing code, cinematic")]
    cfg = dict(config.CHANNELS["yt_fun_shorts"])
    speeches = tts.speak_all(None, SAMPLE_TEXTS, out, cfg, seed=3)
    for kind, (W, H), name in (("short", (1080, 1920), "ornek_dikey.mp4"), ("long", (1920, 1080), "ornek_yatay.mp4")):
        used: set = set()
        scenes = []
        for i, (q, p) in enumerate(queries):
            vis = images.get_visual({"visual_type": "stock", "visual_query": q, "visual_prompt": p}, out, f"dv_{kind}{i}",
                                    vertical=(kind == "short"), seed=i, used=used)
            scenes.append({"speech": speeches[i], "visual": vis, "text": SAMPLE_TEXTS[i], "title": "Ay'a yolculuk" if kind == "long" and i == 0 else None})
        r = render.render_video(scenes, str(out / name), out, W=W, H=H, kind=kind, seed=5, log=lambda l, m: print(l, m))
        print(f"OK     {name}: {r['duration']:.1f} sn, mod={r['mode']}, ses={speeches[0].provider}")
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
    elif cmd == "demo-voice":
        return demo_voice()
    elif cmd == "demo-video":
        return demo_video()
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
