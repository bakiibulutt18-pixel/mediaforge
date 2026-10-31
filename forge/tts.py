"""Seslendirme motoru. Çıktı her zaman 24 kHz mono 16-bit WAV + (varsa) cümle/ifade zaman işaretleri.

Sağlayıcı zinciri (TTS_PROVIDERS, varsayılan "gemini,edge,espeak"):
  gemini  Gemini TTS: yönlendirilebilir (ton, nefes, vurgu), en doğal seçenek; ücretsiz kotası sınırlıdır.
  edge    Edge nöral sesler + PROZODİ MOTORU: metin virgül/nokta/soru/ünlemde ifadelere bölünür; her ifadeye ayrı
          perde, hız, ses seviyesi ve nefes boşluğu verilir; *vurgulu* kelimeler öne çıkarılır.
  espeak  son çare (robotik).
Bir video boyunca tek sağlayıcı kullanılır (Session); ortada düşerse tüm video diğer sağlayıcıyla baştan seslendirilir.
"""
from __future__ import annotations

import array
import asyncio
import base64
import hashlib
import json
import pathlib
import random
import re
import shutil
import subprocess
import time
import wave
from dataclasses import dataclass, field

from . import config, video
from .net import HttpError, request

SR = 24000


class TTSError(Exception):
    pass


class TTSRestart(Exception):
    """Video ortasında sağlayıcı düştü: pipeline tüm sahneleri yeniden seslendirmeli."""


@dataclass
class Speech:
    path: str
    duration: float
    marks: list = field(default_factory=list)  # [(start, end, text)] ses dosyası içindeki zamanlar
    provider: str = ""


# ---------------------------------------------------------------------------
# Metin hazırlama
# ---------------------------------------------------------------------------
_REPL = [
    (re.compile(r"%\s?(\d+(?:[.,]\d+)?)"), r"yüzde \1"),
    (re.compile(r"(\d+(?:[.,]\d+)?)\s?%"), r"yüzde \1"),
    (re.compile(r"(?<=\d)\s?km/s\b"), " kilometre bölü saat"),
    (re.compile(r"(?<=\d)\s?km\b"), " kilometre"),
    (re.compile(r"(?<=\d)\s?kg\b"), " kilogram"),
    (re.compile(r"(?<=\d)\s?m²"), " metrekare"),
    (re.compile(r"(?<=\d)\s?°C"), " derece"),
    (re.compile(r"\$\s?(\d+(?:[.,]\d+)?)"), r"\1 dolar"),
    (re.compile(r"€\s?(\d+(?:[.,]\d+)?)"), r"\1 avro"),
    (re.compile(r"\s&\s"), " ve "),
    (re.compile(r"\bvs\.", re.I), "ve benzeri"),
]


def normalize(text: str) -> str:
    """Konuşma için sembolleri ve kısaltmaları açar; boşlukları düzenler."""
    t = " ".join(text.split())
    for rx, rep in _REPL:
        t = rx.sub(rep, t)
    return t


def strip_markup(text: str) -> str:
    """Altyazı için vurgu işaretlerini (*kelime*) kaldırır."""
    return text.replace("*", "")


def _parse_rate(r: str) -> int:
    try:
        return int(r.replace("%", "").replace("+", ""))
    except ValueError:
        return 0


def split_phrases(text: str) -> list[dict]:
    """Metni konuşma ifadelerine böler ve her birine prozodi parametreleri verir."""
    text = normalize(text)
    sentences = [s for s in re.split(r"(?<=[.!?…])\s+", text) if s.strip()]
    raw: list[dict] = []
    for si, sent in enumerate(sentences):
        parts = [p for p in re.split(r"(?<=[,;:])\s+|\s+[—–]\s+", sent) if p.strip()]
        for pi, p in enumerate(parts):
            raw.append({"text": p.strip(), "first_in_sentence": pi == 0, "last_in_sentence": pi == len(parts) - 1})
    # çok kısa ifadeleri öncekiyle birleştir (kesik kesik konuşmayı önler)
    merged: list[dict] = []
    for ph in raw:
        words = len(strip_markup(ph["text"]).split())
        if merged and words < 3 and "*" not in ph["text"] and not merged[-1]["last_in_sentence"]:
            merged[-1]["text"] += " " + ph["text"]
            merged[-1]["last_in_sentence"] = ph["last_in_sentence"]
        else:
            merged.append(ph)
    out: list[dict] = []
    for ph in merged:
        # *vurgu* parçalarını ayrı ifade yap
        pieces = re.split(r"(\*[^*]+\*)", ph["text"])
        pieces = [p for p in pieces if p.strip()]
        for k, piece in enumerate(pieces):
            emph = piece.startswith("*") and piece.endswith("*")
            seg = piece.strip("* ").strip()
            if not seg:
                continue
            last_piece = k == len(pieces) - 1
            out.append({"text": seg, "emph": emph, "end": seg[-1] if last_piece else "",
                        "first": ph["first_in_sentence"] and k == 0, "sentence_end": ph["last_in_sentence"] and last_piece})
    return out


_PAUSE = {",": 0.20, ";": 0.30, ":": 0.30, "—": 0.30, "–": 0.30, ".": 0.50, "?": 0.55, "!": 0.45, "…": 0.70}


def prosody(ph: dict, base_rate: int, seed: int) -> dict:
    """Bir ifade için perde/hız/ses seviyesi ve sonrasındaki nefes boşluğu."""
    rnd = random.Random(seed)
    pitch, rate, vol = 0, base_rate, 0
    end = ph["end"]
    if end == "?":
        pitch, rate = pitch + 6, rate - 2
    elif end == "!":
        pitch, rate, vol = pitch + 5, rate + 4, vol + 6
    elif end in ".…":
        pitch, rate = pitch - 3, rate - 3
    elif end == ",":
        pitch += 2
    if ph["first"]:
        rate -= 2
    if ph["emph"]:
        pitch, rate, vol = pitch + 5, rate - 7, vol + 8
    pitch += rnd.choice([-1, 0, 1])
    rate += rnd.choice([-2, -1, 0, 1, 2])
    pause = _PAUSE.get(end, 0.0)
    if ph["emph"]:
        pause = max(pause, 0.12)
    elif not end:
        pause = 0.0
    pause *= 0.9 + rnd.random() * 0.25
    return {"pitch": pitch, "rate": rate, "vol": vol, "pause": pause}


# ---------------------------------------------------------------------------
# WAV yardımcıları
# ---------------------------------------------------------------------------
def _read_wav(path) -> array.array:
    with wave.open(str(path)) as w:
        if w.getframerate() != SR or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise TTSError(f"beklenmeyen WAV biçimi: {path}")
        a = array.array("h")
        a.frombytes(w.readframes(w.getnframes()))
        return a


def _write_wav(path, samples: array.array) -> None:
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(samples.tobytes())


def _fade(a: array.array, n: int = 160) -> array.array:
    """Birleşim noktalarındaki 'tık' seslerini önlemek için ~7 ms yumuşatma."""
    n = min(n, len(a) // 2)
    for i in range(n):
        g = i / n
        a[i] = int(a[i] * g)
        a[len(a) - 1 - i] = int(a[len(a) - 1 - i] * g)
    return a


def _silence(sec: float) -> array.array:
    return array.array("h", [0]) * int(SR * sec)


def _to_trimmed_wav(src, dst) -> None:
    """Her ses biçimini 24 kHz mono WAV'a çevirir ve baştaki/sondaki sessizliği kırpar."""
    ff = video.ffmpeg_exe()
    trim = "silenceremove=start_periods=1:start_threshold=-50dB:detection=peak"
    af = f"aresample={SR},{trim},areverse,{trim},areverse"
    video.run([ff, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-af", af, "-ac", "1", "-ar", str(SR),
               "-sample_fmt", "s16", str(dst)])


# ---------------------------------------------------------------------------
# Edge (prozodi motoruyla)
# ---------------------------------------------------------------------------
async def _edge_save(text, voice, rate, pitch, volume, path):
    import edge_tts
    await edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, volume=volume).save(str(path))


async def _edge_many(jobs):
    sem = asyncio.Semaphore(3)

    async def one(job):
        text, voice, rate, pitch, volume, path = job
        last = None
        for attempt in range(3):
            async with sem:
                try:
                    await _edge_save(text, voice, rate, pitch, volume, path)
                    return
                except Exception as e:  # ağ/servis hataları
                    last = e
            await asyncio.sleep(1.5 * (attempt + 1))
        raise TTSError(f"edge-tts başarısız: {last}")

    await asyncio.gather(*(one(j) for j in jobs))


def _edge(text, out_base, cfg, seed) -> Speech:
    try:
        import edge_tts  # noqa: F401
    except ImportError:
        raise TTSError("edge-tts yüklü değil")
    voice = cfg.get("voice", "tr-TR-AhmetNeural")
    base_rate = _parse_rate(cfg.get("rate", "+0%"))
    phrases = split_phrases(text)
    if not phrases:
        raise TTSError("boş metin")
    cache = config.OUT_DIR / "cache"
    cache.mkdir(parents=True, exist_ok=True)

    plan, jobs = [], []
    for i, ph in enumerate(phrases):
        pr = prosody(ph, base_rate, seed * 1000 + i + hash_int(ph["text"]))
        key = hashlib.sha256(f"{voice}|{pr['rate']}|{pr['pitch']}|{pr['vol']}|{ph['text']}".encode()).hexdigest()[:20]
        wav = cache / f"ph_{key}.wav"
        mp3 = cache / f"ph_{key}.mp3"
        if not wav.exists():
            jobs.append((ph["text"], voice, f"{pr['rate']:+d}%", f"{pr['pitch']:+d}Hz", f"{pr['vol']:+d}%", mp3))
        plan.append((ph, pr, wav, mp3))
    if jobs:
        asyncio.run(_edge_many(jobs))
        for ph, pr, wav, mp3 in plan:
            if not wav.exists():
                _to_trimmed_wav(mp3, wav)

    out = array.array("h")
    marks = []
    for k, (ph, pr, wav, _mp3) in enumerate(plan):
        a = _fade(_read_wav(wav))
        start = len(out) / SR
        out.extend(a)
        marks.append((start, len(out) / SR, ph["text"]))
        if k < len(plan) - 1:
            out.extend(_silence(pr["pause"]))
    path = pathlib.Path(out_base).with_suffix(".wav")
    _write_wav(path, out)
    return Speech(str(path), len(out) / SR, marks, "edge")


def hash_int(s: str) -> int:
    return int(hashlib.md5(s.encode()).hexdigest()[:6], 16)


# ---------------------------------------------------------------------------
# Gemini TTS
# ---------------------------------------------------------------------------
_tts_model_cache: str | None = None
_last_gemini_call = 0.0


def gemini_tts_model(key: str) -> str:
    global _tts_model_cache
    explicit = config.env("GEMINI_TTS_MODEL")
    if explicit:
        return explicit
    if _tts_model_cache:
        return _tts_model_cache
    try:
        r = request("GET", "https://generativelanguage.googleapis.com/v1beta/models", params={"pageSize": 200},
                    headers={"x-goog-api-key": key}, retries=1).json()
    except HttpError as e:
        raise TTSError(f"Gemini model listesi alınamadı: {e}")
    best, best_v = None, -1.0
    for m in r.get("models", []):
        name = m.get("name", "").removeprefix("models/")
        if "tts" not in name or "generateContent" not in m.get("supportedGenerationMethods", []):
            continue
        mv = re.match(r"gemini-(\d+(?:\.\d+)?)", name)
        v = float(mv.group(1)) if mv else 0.0
        v += 0.05 if "flash" in name else 0.0  # kota için flash'ı tercih et
        if v > best_v:
            best, best_v = name, v
    if not best:
        raise TTSError("Gemini TTS modeli bulunamadı")
    _tts_model_cache = best
    return best


def _gemini(text, out_base, cfg, seed) -> Speech:
    global _last_gemini_call
    key = config.env("GEMINI_API_KEY")
    if not key:
        raise TTSError("GEMINI_API_KEY yok")
    model = gemini_tts_model(key)
    voice = cfg.get("gemini_voice", "Charon")
    style = cfg.get("tts_style") or "Doğal, akıcı ve insani bir tonda oku."
    prompt = (f"{style}\nNefes al: virgülde kısa, noktada tam bir duraklama yap. Vurgulanması gereken yerde sesi yükselt, "
              f"cümle sonlarında doğal şekilde alçal. Robotik gibi değil, gerçek bir insan gibi konuş.\n\n"
              f"### TRANSCRIPT\n{strip_markup(normalize(text))}")
    gap = float(config.env("GEMINI_TTS_INTERVAL", "7"))
    wait = _last_gemini_call + gap - time.time()
    if wait > 0:
        time.sleep(wait)
    _last_gemini_call = time.time()
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"responseModalities": ["AUDIO"],
                                 "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}}}
    r = None
    for attempt in range(2):
        try:
            r = request("POST", f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                        json_body=body, headers={"x-goog-api-key": key}, timeout=180).json()
        except HttpError as e:
            raise TTSError(f"Gemini TTS: {str(e)[:200]}")
        parts = (r.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
        inline = next((p["inlineData"] for p in parts if "inlineData" in p), None)
        if inline:
            break
        time.sleep(2)
    else:
        raise TTSError(f"Gemini TTS ses döndürmedi: {str(r)[:200]}")
    pcm = base64.b64decode(inline["data"])
    rate_m = re.search(r"rate=(\d+)", inline.get("mimeType", ""))
    src_rate = int(rate_m.group(1)) if rate_m else 24000
    raw = pathlib.Path(out_base).with_suffix(".raw.wav")
    with wave.open(str(raw), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(src_rate)
        w.writeframes(pcm)
    path = pathlib.Path(out_base).with_suffix(".wav")
    _to_trimmed_wav(raw, path)
    dur = len(_read_wav(path)) / SR
    return Speech(str(path), dur, [], "gemini")


# ---------------------------------------------------------------------------
# espeak (son çare) ve mock
# ---------------------------------------------------------------------------
def _espeak(text, out_base, cfg, seed) -> Speech:
    exe = shutil.which("espeak-ng") or shutil.which("espeak")
    if not exe:
        raise TTSError("espeak-ng yok")
    raw = pathlib.Path(out_base).with_suffix(".raw.wav")
    p = subprocess.run([exe, "-v", "tr", "-s", "150", "-w", str(raw), strip_markup(normalize(text))], capture_output=True, text=True)
    if p.returncode != 0 or not raw.exists():
        raise TTSError("espeak-ng başarısız")
    path = pathlib.Path(out_base).with_suffix(".wav")
    _to_trimmed_wav(raw, path)
    return Speech(str(path), len(_read_wav(path)) / SR, [], "espeak")


def _mock(text, out_base, cfg, seed) -> Speech:
    phrases = split_phrases(text) or [{"text": text, "emph": False, "end": ".", "first": True}]
    out, marks = array.array("h"), []
    for k, ph in enumerate(phrases):
        pr = prosody(ph, 0, seed + k)
        secs = max(len(ph["text"].split()) / 2.6, 0.4)
        s = len(out) / SR
        out.extend(array.array("h", [0]) * int(SR * secs))
        marks.append((s, len(out) / SR, ph["text"]))
        if k < len(phrases) - 1:
            out.extend(_silence(pr["pause"]))
    path = pathlib.Path(out_base).with_suffix(".wav")
    _write_wav(path, out)
    return Speech(str(path), len(out) / SR, marks, "mock")


PROVIDERS = {"gemini": _gemini, "edge": _edge, "espeak": _espeak}


class Session:
    """Bir video için tek sağlayıcı: ilk başarılı sağlayıcıya sabitlenir; düşerse TTSRestart ile baştan başlatılır."""

    def __init__(self, db=None, n_calls: int = 0):
        chain = (config.env("TTS_PROVIDERS", "gemini,edge,espeak") or "").split(",")
        self.chain = [c.strip() for c in chain if c.strip() in PROVIDERS]
        self.disabled: set[str] = set()
        self.pinned: str | None = None
        self.db = db
        if config.mock():
            self.chain = ["mock"]
            return
        if "gemini" in self.chain:
            if not config.env("GEMINI_API_KEY"):
                self.disabled.add("gemini")
            elif db is not None:
                from .db import day_key
                limit = int(config.env("GEMINI_TTS_DAILY", "80"))
                if db.quota_used("gemini_tts", day_key()) + n_calls > limit:
                    db.log("info", f"Gemini TTS günlük kota payı yetmiyor ({n_calls} çağrı gerekli, limit {limit}); Edge'e geçiliyor")
                    self.disabled.add("gemini")

    def synthesize(self, text: str, out_base, cfg: dict, seed: int = 0) -> Speech:
        if config.mock():
            return _mock(text, out_base, cfg, seed)
        candidates = [self.pinned] if self.pinned else [p for p in self.chain if p not in self.disabled]
        errors = []
        for name in candidates:
            try:
                sp = PROVIDERS[name](text, out_base, cfg, seed)
                self.pinned = name
                if name == "gemini" and self.db is not None:
                    from .db import day_key
                    self.db.quota_add("gemini_tts", day_key(), 1)
                return sp
            except (TTSError, video.RenderError, subprocess.TimeoutExpired) as e:
                errors.append(f"{name}: {e}")
                if self.db is not None:
                    self.db.log("warn", f"TTS sağlayıcısı başarısız ({name}): {str(e)[:200]}")
                self.disabled.add(name)
                if self.pinned == name:
                    self.pinned = None
                    raise TTSRestart(f"{name} video ortasında düştü")
        raise TTSError("Tüm TTS sağlayıcıları başarısız: " + " | ".join(errors))


def speak_all(db, texts: list[str], out_dir, cfg: dict, seed: int = 0) -> list[Speech]:
    """Bir videonun tüm sahnelerini tek sağlayıcıyla seslendirir; sağlayıcı ortada düşerse baştan başlar."""
    out_dir = pathlib.Path(out_dir)
    for _ in range(4):
        sess = Session(db, len(texts))
        try:
            return [sess.synthesize(t, out_dir / f"a{i}", cfg, seed * 100 + i) for i, t in enumerate(texts)]
        except TTSRestart:
            continue
    raise TTSError("Seslendirme tamamlanamadı (sağlayıcılar art arda düştü)")
