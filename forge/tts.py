"""TTS adaptörleri: edge-tts (ücretsiz Türkçe nöral ses) → espeak-ng (yedek) ; mock: sessizlik.

Sağlayıcı sırası: TTS_PROVIDERS="edge,espeak". Biri çalışmazsa sıradakine geçilir.
"""
from __future__ import annotations

import hashlib
import pathlib
import shutil
import subprocess
import sys
import wave

from . import config, video


class TTSError(Exception):
    pass


def _edge(text, out, voice, rate):
    out = pathlib.Path(out).with_suffix(".mp3")
    txt = out.with_suffix(".txt")
    txt.write_text(text, encoding="utf-8")
    cmd = [sys.executable, "-m", "edge_tts", "--voice", voice, f"--rate={rate}", "--file", str(txt),
           "--write-media", str(out)]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=240)
    if p.returncode != 0 or not out.exists() or out.stat().st_size < 1000:
        raise TTSError(f"edge-tts başarısız: {p.stderr[-300:]}")
    return str(out)


def _espeak(text, out, voice, rate):
    exe = shutil.which("espeak-ng") or shutil.which("espeak")
    if not exe:
        raise TTSError("espeak-ng yok")
    wav = pathlib.Path(out).with_suffix(".wav")
    p = subprocess.run([exe, "-v", "tr", "-s", "150", "-w", str(wav), text], capture_output=True, text=True)
    if p.returncode != 0 or not wav.exists():
        raise TTSError("espeak-ng başarısız")
    return str(wav)


def _mock(text, out, voice, rate):
    wav = pathlib.Path(out).with_suffix(".wav")
    secs = max(len(text.split()) / 2.6, 0.8)
    with wave.open(str(wav), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * int(16000 * secs))
    return str(wav)


PROVIDERS = {"edge": _edge, "espeak": _espeak}


def synthesize(text: str, out_base, *, voice: str, rate: str = "+0%") -> tuple[str, float]:
    """Metni sese çevirir. Dönen: (dosya_yolu, süre_sn). Aynı metin önbellekten gelir."""
    text = " ".join(text.split())
    if not text:
        raise TTSError("boş metin")
    if config.mock():
        path = _mock(text, out_base, voice, rate)
        return path, video.duration(path)

    h = hashlib.sha256(f"{voice}|{rate}|{text}".encode()).hexdigest()[:24]
    cache_dir = config.OUT_DIR / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    for ext in (".mp3", ".wav"):
        c = cache_dir / f"tts_{h}{ext}"
        if c.exists():
            return str(c), video.duration(c)

    errors = []
    for name in (config.env("TTS_PROVIDERS", "edge,espeak") or "").split(","):
        fn = PROVIDERS.get(name.strip())
        if not fn:
            continue
        try:
            produced = pathlib.Path(fn(text, cache_dir / f"tts_{h}", voice, rate))
            return str(produced), video.duration(produced)
        except (TTSError, subprocess.TimeoutExpired, video.RenderError) as e:
            errors.append(f"{name}: {e}")
    raise TTSError("Tüm TTS sağlayıcıları başarısız: " + " | ".join(errors))
