"""Profesyonel görünümlü video üretimi: yumuşatılmış kamera hareketleri, sahne geçişleri (xfade), renk derecelendirme,
kelime vurgulu altyazılar (kısa video), bölüm başlık kartları (uzun video), ses katmanı (sahneler arası nefes payı).

Zaman çizelgesi: her sahnenin kendi süresi d = HEAD + konuşma + TAIL. Klip, bir sonrakiyle T kadar çakışır
(çapraz geçiş). Konuşma, klibin HEAD saniyesinde başlar → geçişler sessizlikte olur, kelimeler kesilmez.
Herhangi bir adım hata verirse sade (kanıtlanmış) render yoluna düşülür ve uyarı kaydedilir.
"""
from __future__ import annotations

import array
import pathlib
import random
import wave

from . import config, tts, video
from .video import RenderError, _even, _pil, font, run, wrap

FPS = 30
HEAD, TAIL, T = 0.30, 0.35, 0.40
ACCENT = (255, 214, 10)
MAX_GROUP = 12

SHORT_TRANSITIONS = ["fade", "slideleft", "smoothright", "zoomin", "fade", "smoothleft"]
LONG_TRANSITIONS = ["fade", "dissolve", "fade", "smoothleft", "fade", "smoothright"]
MOTIONS = ["in", "out", "right", "in", "left", "up"]


# ---------------------------------------------------------------------------
# Altyazı ve başlık kartı görselleri
# ---------------------------------------------------------------------------
def caption_words_png(words: list[str], active: int, W: int, H: int, path, y_frac: float = 0.62) -> str:
    """Kelime vurgulu (karaoke) altyazı: aktif kelime sarı, diğerleri beyaz."""
    Image, ImageDraw, *_ = _pil()
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    fnt = font(int(W * 0.085))
    max_w = int(W * 0.88)
    space = d.textlength(" ", font=fnt)
    lines, cur, cur_w = [], [], 0.0
    for idx, w in enumerate(words):
        ww = d.textlength(w, font=fnt)
        if cur and cur_w + space + ww > max_w:
            lines.append(cur)
            cur, cur_w = [], 0.0
        cur.append((idx, w, ww))
        cur_w += ww + (space if len(cur) > 1 else 0)
    if cur:
        lines.append(cur)
    lh = int(fnt.size * 1.22)
    y = int(H * y_frac) - len(lines) * lh // 2
    stroke = max(5, W // 150)
    for line in lines:
        total = sum(w for _, _, w in line) + space * (len(line) - 1)
        x = (W - total) / 2
        for idx, w, ww in line:
            fill = ACCENT if idx == active else "white"
            d.text((x + 3, y + 5), w, font=fnt, fill=(0, 0, 0, 120), stroke_width=stroke, stroke_fill=(0, 0, 0, 120))  # gölge
            d.text((x, y), w, font=fnt, fill=fill, stroke_width=stroke, stroke_fill="black")
            x += ww + space
        y += lh
    img.save(path)
    return str(path)


def title_card_png(text: str, W: int, H: int, path) -> str:
    Image, ImageDraw, *_ = _pil()
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    fnt = font(int(H * 0.058))
    lines = wrap(d, text, fnt, int(W * 0.55))
    lh = int(fnt.size * 1.25)
    box_h = len(lines) * lh + 36
    x0, y0 = int(W * 0.06), int(H * 0.72)
    d.rectangle((x0 - 24, y0 - 8, x0 + int(W * 0.58), y0 + box_h + 8), fill=(0, 0, 0, 150))
    d.rectangle((x0 - 24, y0 - 8, x0 - 14, y0 + box_h + 8), fill=ACCENT + (255,))
    y = y0 + 14
    for ln in lines:
        d.text((x0, y), ln, font=fnt, fill="white")
        y += lh
    img.save(path)
    return str(path)


def scene_captions(sp: tts.Speech, text: str, W: int, H: int, wd, i: int, kind: str) -> list[tuple[str, float, float]]:
    """Konuşma işaretlerinden (yoksa orantısal) altyazı görselleri ve klip-içi zamanları üretir."""
    marks = sp.marks or [(0.0, sp.duration, tts.strip_markup(text))]
    out: list[tuple[str, float, float]] = []
    n = 0
    for (s, e, ph_text) in marks:
        ph_text = tts.strip_markup(ph_text)
        if kind == "short":
            chunks = video.split_chunks(ph_text, 4)
            for c, (cs, ce) in zip(chunks, video.chunk_times(chunks, e - s)):
                words = c.split()
                total = sum(len(w) for w in words) or 1
                t = s + cs
                for wi, w in enumerate(words):
                    dur = (ce - cs) * len(w) / total
                    png = caption_words_png(words, wi, W, H, wd / f"c{i}_{n}.png")
                    out.append((png, HEAD + t, HEAD + t + dur + (0.04 if wi == len(words) - 1 else 0)))
                    t += dur
                    n += 1
        else:
            chunks = video.split_chunks(ph_text, 12)
            for c, (cs, ce) in zip(chunks, video.chunk_times(chunks, e - s)):
                out.append((video.caption_png(c, W, H, wd / f"c{i}_{n}.png", "wide"), HEAD + s + cs, HEAD + s + ce))
                n += 1
    return out


# ---------------------------------------------------------------------------
# Klip render'ı (yalnızca görüntü)
# ---------------------------------------------------------------------------
def motion_exprs(mode: str, frames: int) -> tuple[str, str, str]:
    e = f"(1-cos(PI*on/{max(frames - 1, 1)}))/2"  # yumuşak başla-yumuşak bitir
    cx, cy = "(iw-iw/zoom)/2", "(ih-ih/zoom)/2"
    if mode == "in":
        return f"1+0.14*{e}", cx, cy
    if mode == "out":
        return f"1.14-0.14*{e}", cx, cy
    if mode == "left":
        return "1.12", f"(iw-iw/zoom)*(1-{e})", cy
    if mode == "right":
        return "1.12", f"(iw-iw/zoom)*{e}", cy
    return "1.12", cx, f"(ih-ih/zoom)*(1-{e})"  # up


GRADE = "eq=contrast=1.05:saturation=1.10:gamma=0.98,vignette=angle=PI/5"


def render_clip(visual: dict, caps, title_png: str | None, out: str, W: int, H: int, d: float, idx: int) -> str:
    L = d + T
    frames = int(L * FPS)
    ff = video.ffmpeg_exe()
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error"]
    if visual["type"] == "video":
        cmd += ["-stream_loop", "-1", "-t", f"{L:.3f}", "-i", visual["path"]]
        bg = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},setsar=1,{GRADE}[bg]")
    else:
        cmd += ["-loop", "1", "-framerate", str(FPS), "-t", f"{L:.3f}", "-i", visual["path"]]
        sw, sh = _even(W * 1.6), _even(H * 1.6)
        z, x, y = motion_exprs(MOTIONS[idx % len(MOTIONS)], frames)
        bg = (f"[0:v]scale={sw}:{sh}:force_original_aspect_ratio=increase,crop={sw}:{sh},"
              f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={W}x{H}:fps={FPS},setsar=1,{GRADE}[bg]")
    for png, _s, _e in caps:
        cmd += ["-loop", "1", "-framerate", str(FPS), "-t", f"{L:.3f}", "-i", png]
    n_in = 1 + len(caps)
    if title_png:
        cmd += ["-loop", "1", "-framerate", str(FPS), "-t", f"{L:.3f}", "-i", title_png]

    chain, prev = [bg], "bg"
    for i, (_png, s, e) in enumerate(caps, start=1):
        nxt = f"c{i}"
        chain.append(f"[{prev}][{i}:v]overlay=0:0:format=auto:enable='between(t,{s:.3f},{e:.3f})'[{nxt}]")
        prev = nxt
    if title_png:
        chain.append(f"[{n_in}:v]format=rgba,fade=t=in:st=0.6:d=0.4:alpha=1,fade=t=out:st=4.0:d=0.5:alpha=1[tc]")
        chain.append(f"[{prev}][tc]overlay=0:0:format=auto[tt]")
        prev = "tt"
    chain.append(f"[{prev}]format=yuv420p[v]")
    cmd += ["-filter_complex", ";".join(chain), "-map", "[v]", "-an", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS), "-t", f"{L:.3f}", out]
    run(cmd)
    return out


def xfade_group(clips: list[str], ds: list[float], transitions: list[str], out: str, *, fade_in: bool, fade_out: bool) -> str:
    ff = video.ffmpeg_exe()
    total = sum(ds)
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error"]
    for c in clips:
        cmd += ["-i", c]
    chain, prev, offset = [], "0:v", 0.0
    for k in range(1, len(clips)):
        offset += ds[k - 1]
        nxt = f"x{k}"
        chain.append(f"[{prev}][{k}:v]xfade=transition={transitions[k - 1]}:duration={T}:offset={offset:.3f}[{nxt}]")
        prev = nxt
    tail = []
    if fade_in:
        tail.append("fade=t=in:st=0:d=0.25")
    if fade_out:
        tail.append(f"fade=t=out:st={max(total - 0.35, 0):.3f}:d=0.35")
    tail.append("format=yuv420p")
    chain.append(f"[{prev}]{','.join(tail)}[v]")
    cmd += ["-filter_complex", ";".join(chain), "-map", "[v]", "-an", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS), "-t", f"{total:.3f}", out]
    run(cmd)
    return out


def build_audio(speeches: list[tts.Speech], starts: list[float], total: float, path) -> str:
    """Sahne seslerini zaman çizelgesindeki yerlerine yerleştirir (24 kHz mono WAV)."""
    n = int(total * tts.SR) + tts.SR
    buf = array.array("h", [0]) * n
    for sp, st in zip(speeches, starts):
        with wave.open(sp.path) as w:
            a = array.array("h")
            a.frombytes(w.readframes(w.getnframes()))
        off = int(st * tts.SR)
        end = min(off + len(a), n)
        buf[off:end] = a[: end - off]  # eşit uzunlukta dilim ataması
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(tts.SR)
        w.writeframes(buf[: int(total * tts.SR)].tobytes())
    return str(path)


def mux(video_path: str, audio_path: str, out: str, total: float) -> str:
    """Ses işleme (alçak kesim, sıkıştırma, ses seviyesi) + isteğe bağlı fon müziği (assets/music, otomatik kısılır)."""
    ff = video.ffmpeg_exe()
    music = sorted((config.ROOT / "assets" / "music").glob("*.mp3")) if (config.ROOT / "assets" / "music").exists() else []
    voice = "highpass=f=70,acompressor=threshold=0.125:ratio=3:attack=8:release=120"
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", video_path, "-i", audio_path]
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music[0])]
        fc = (f"[1:a]{voice},asplit=2[vo][sc];[2:a]volume=0.14,atrim=0:{total:.2f}[m];"
              f"[m][sc]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=500[duck];"
              f"[vo][duck]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=11[a]")
    else:
        fc = f"[1:a]{voice},loudnorm=I=-16:TP=-1.5:LRA=11[a]"
    cmd += ["-filter_complex", fc, "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-ar", "48000", "-ac", "2", "-t", f"{total:.3f}", "-movflags", "+faststart", out]
    run(cmd)
    return out


# ---------------------------------------------------------------------------
# Orkestra
# ---------------------------------------------------------------------------
def render_video(scenes: list[dict], out: str, workdir, *, W: int, H: int, kind: str, seed: int, log=print) -> dict:
    """scenes: [{speech: Speech, visual: dict, text: str, title: str|None}] → {path, duration, speech_starts}"""
    wd = pathlib.Path(workdir)
    try:
        return _render_fancy(scenes, out, wd, W=W, H=H, kind=kind, seed=seed)
    except Exception as e:  # gelişmiş yol ne sebeple düşerse düşsün sade yol dener
        log("warn", f"gelişmiş render başarısız, sade render'a düşüldü: {str(e)[:500]}")
        return _render_simple(scenes, out, wd, W=W, H=H, kind=kind)


def _render_fancy(scenes, out, wd, *, W, H, kind, seed) -> dict:
    ds = [HEAD + s["speech"].duration + TAIL for s in scenes]
    starts = []
    t = 0.0
    for d in ds:
        starts.append(t)
        t += d
    total = t

    clips = []
    for i, s in enumerate(scenes):
        caps = scene_captions(s["speech"], s["text"], W, H, wd, i, kind)
        title = title_card_png(s["title"], W, H, wd / f"t{i}.png") if s.get("title") else None
        clips.append(render_clip(s["visual"], caps, title, str(wd / f"clip{i}.mp4"), W, H, ds[i], i))

    # gruplar: başlık (bölüm) başlangıçlarında ve MAX_GROUP'ta bölünür
    groups: list[list[int]] = []
    for i, s in enumerate(scenes):
        if not groups or (s.get("title") and kind == "long") or len(groups[-1]) >= MAX_GROUP:
            groups.append([])
        groups[-1].append(i)
    pool = SHORT_TRANSITIONS if kind == "short" else LONG_TRANSITIONS
    rnd = random.Random(seed)
    gfiles = []
    for gi, idxs in enumerate(groups):
        trans = [pool[rnd.randrange(len(pool))] for _ in range(len(idxs) - 1)]
        gout = str(wd / f"group{gi}.mp4")
        xfade_group([clips[i] for i in idxs], [ds[i] for i in idxs], trans, gout,
                    fade_in=(kind == "long" and gi > 0), fade_out=(kind == "long"))
        gfiles.append(gout)

    lst = wd / "groups.txt"
    lst.write_text("".join(f"file '{pathlib.Path(g).resolve().as_posix()}'\n" for g in gfiles), encoding="utf-8")
    vid = str(wd / "video_only.mp4")
    run([video.ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", vid])

    speech_starts = [st + HEAD for st in starts]
    audio = build_audio([s["speech"] for s in scenes], speech_starts, total, wd / "mix.wav")
    mux(vid, audio, out, total)
    return {"path": out, "duration": total, "speech_starts": speech_starts, "mode": "fancy"}


def _render_simple(scenes, out, wd, *, W, H, kind) -> dict:
    """Kanıtlanmış sade yol: sabit zoom + sert kesme."""
    clips, starts, t = [], [], 0.0
    for i, s in enumerate(scenes):
        sp = s["speech"]
        d = sp.duration + 0.2
        starts.append(t)
        marks = sp.marks or [(0.0, sp.duration, tts.strip_markup(s["text"]))]
        caps = []
        for j, (ms, me, txt) in enumerate(marks):
            chunks = video.split_chunks(tts.strip_markup(txt), 4 if kind == "short" else 12)
            for c, (cs, ce) in zip(chunks, video.chunk_times(chunks, me - ms)):
                caps.append((video.caption_png(c, W, H, wd / f"sc{i}_{j}_{len(caps)}.png", "short" if kind == "short" else "wide"),
                             ms + cs, ms + ce))
        clips.append(video.render_scene(s["visual"], sp.path, caps, str(wd / f"s{i}.mp4"), W, H, d, i))
        t += d
    video.concat_finish(clips, out, wd)
    return {"path": out, "duration": t, "speech_starts": starts, "mode": "simple"}
