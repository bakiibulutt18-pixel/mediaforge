"""FFmpeg + Pillow ile sahne render'ı, birleştirme, altyazı PNG'leri, grafik ve thumbnail üretimi."""
from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess

from . import config

FPS = 30
FONT_CANDIDATES = [
    config.env("FORGE_FONT"),
    "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]


class RenderError(Exception):
    pass


def ffmpeg_exe() -> str:
    exe = config.env("FFMPEG") or shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        raise RenderError("FFmpeg bulunamadı (GitHub Actions'ta hazır gelir)")


def run(cmd: list[str]) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RenderError(f"ffmpeg hata ({p.returncode}): {p.stderr[-900:]}")
    return p.stderr


def probe(path) -> dict:
    """ffmpeg -i çıktısından süre, çözünürlük, ses/video bilgisi."""
    p = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    err = p.stderr
    info = {"duration": 0.0, "width": 0, "height": 0, "has_audio": "Audio:" in err, "has_video": "Video:" in err,
            "vcodec": "", "acodec": ""}
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", err)
    if m:
        info["duration"] = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
    m = re.search(r"Video: (\w+).*?, (\d{2,5})x(\d{2,5})", err)
    if m:
        info["vcodec"], info["width"], info["height"] = m[1], int(m[2]), int(m[3])
    m = re.search(r"Audio: (\w+)", err)
    if m:
        info["acodec"] = m[1]
    return info


def duration(path) -> float:
    if str(path).endswith(".wav"):
        import wave
        with wave.open(str(path)) as w:
            return w.getnframes() / w.getframerate()
    return probe(path)["duration"]


# ---------------------------------------------------------------------------
# Pillow yardımcıları
# ---------------------------------------------------------------------------
def _pil():
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageStat
    except ImportError:
        raise RenderError("Pillow yüklü değil (GitHub Actions'ta kurulur)")
    return Image, ImageDraw, ImageFont, ImageFilter, ImageStat


def font(size: int):
    _, _, ImageFont, _, _ = _pil()
    for f in FONT_CANDIDATES:
        if f and os.path.exists(f):
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def wrap(draw, text: str, fnt, max_w: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=fnt) <= max_w or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def caption_png(text: str, W: int, H: int, path, mode: str = "short") -> str:
    Image, ImageDraw, *_ = _pil()
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if mode == "short":
        fnt = font(int(W * 0.075))
        lines = wrap(d, text, fnt, int(W * 0.86))
        lh = int(fnt.size * 1.25)
        y = int(H * 0.58) - len(lines) * lh // 2
        for ln in lines:
            w = d.textlength(ln, font=fnt)
            d.text(((W - w) / 2, y), ln, font=fnt, fill="white", stroke_width=max(4, W // 180), stroke_fill="black")
            y += lh
    else:
        fnt = font(int(H * 0.045))
        lines = wrap(d, text, fnt, int(W * 0.82))
        lh = int(fnt.size * 1.3)
        box_h = len(lines) * lh + 30
        y0 = H - box_h - int(H * 0.06)
        d.rounded_rectangle((int(W * 0.07), y0, int(W * 0.93), y0 + box_h), radius=14, fill=(0, 0, 0, 150))
        y = y0 + 15
        for ln in lines:
            w = d.textlength(ln, font=fnt)
            d.text(((W - w) / 2, y), ln, font=fnt, fill="white")
            y += lh
    img.save(path)
    return str(path)


def gradient_png(path, W: int, H: int, seed: int = 0, label: str = "") -> str:
    Image, ImageDraw, *_ = _pil()
    palettes = [((15, 32, 67), (88, 28, 135)), ((7, 59, 76), (6, 214, 160)), ((43, 18, 76), (255, 94, 98)),
                ((17, 24, 39), (59, 130, 246)), ((30, 41, 59), (234, 179, 8))]
    a, b = palettes[seed % len(palettes)]
    img = Image.new("RGB", (W, H))
    px = img.load()
    for y in range(H):
        t = y / max(H - 1, 1)
        row = tuple(int(a[i] * (1 - t) + b[i] * t) for i in range(3))
        for x in range(W):
            px[x, y] = row
    img.save(path)
    return str(path)


def chart_png(spec: dict, W: int, H: int, path) -> str:
    """Basit yatay çubuk grafik. spec: {title, unit, bars:[{label,value}]} – değerler doğrulanmış olmalı."""
    Image, ImageDraw, *_ = _pil()
    img = Image.new("RGB", (W, H), (12, 17, 28))
    d = ImageDraw.Draw(img)
    bars = [b for b in spec.get("bars", []) if isinstance(b.get("value"), (int, float))][:8]
    d.text((W * 0.06, H * 0.07), spec.get("title", ""), font=font(int(H * 0.06)), fill="white")
    if not bars:
        img.save(path)
        return str(path)
    vmax = max(b["value"] for b in bars) or 1
    top, avail = H * 0.22, H * 0.68
    row_h = avail / len(bars)
    lab_f, val_f = font(int(row_h * 0.38)), font(int(row_h * 0.38))
    left, right = W * 0.26, W * 0.9
    for i, b in enumerate(bars):
        y = top + i * row_h
        d.text((W * 0.04, y + row_h * 0.2), str(b["label"])[:22], font=lab_f, fill=(200, 210, 230))
        bw = (right - left) * (b["value"] / vmax)
        d.rounded_rectangle((left, y + row_h * 0.15, left + bw, y + row_h * 0.75), radius=8, fill=(56, 189, 248))
        d.text((left + bw + 12, y + row_h * 0.2), f"{b['value']:,}".replace(",", ".") + " " + spec.get("unit", ""),
               font=val_f, fill="white")
    img.save(path)
    return str(path)


# ---------------------------------------------------------------------------
# Sahne render'ı
# ---------------------------------------------------------------------------
def _even(n: float) -> int:
    n = int(n)
    return n - (n % 2)


def render_scene(visual: dict, audio: str, captions: list[tuple[str, float, float]], out: str,
                 W: int, H: int, dur: float, index: int = 0) -> str:
    """visual: {path, type: image|video}. captions: [(png, start, end)]."""
    ff = ffmpeg_exe()
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error"]
    if visual["type"] == "video":
        cmd += ["-stream_loop", "-1", "-t", f"{dur:.3f}", "-i", visual["path"]]
        bg = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
              f"fps={FPS},setsar=1,eq=brightness=-0.04[bg]")
    else:
        cmd += ["-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.3f}", "-i", visual["path"]]
        bw, bh = _even(W * 1.25), _even(H * 1.25)
        frames = max(int(dur * FPS), 1)
        step = 0.16 / frames
        if index % 2 == 0:
            z = f"min(1+{step:.6f}*on,1.16)"
        else:
            z = f"max(1.16-{step:.6f}*on,1)"
        bg = (f"[0:v]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},"
              f"zoompan=z='{z}':x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2':d=1:s={W}x{H}:fps={FPS},setsar=1[bg]")
    for png, _s, _e in captions:
        cmd += ["-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.3f}", "-i", png]
    cmd += ["-i", audio]
    audio_idx = 1 + len(captions)

    chain, prev = [bg], "bg"
    for i, (_png, s, e) in enumerate(captions, start=1):
        nxt = f"v{i}"
        chain.append(f"[{prev}][{i}:v]overlay=0:0:format=auto:enable='between(t,{s:.3f},{e:.3f})'[{nxt}]")
        prev = nxt
    chain.append(f"[{audio_idx}:a]apad,atrim=0:{dur:.3f},aresample=48000,aformat=channel_layouts=stereo[a]")
    cmd += ["-filter_complex", ";".join(chain), "-map", f"[{prev}]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", "-t", f"{dur:.3f}", out]
    run(cmd)
    return out


def concat_finish(clips: list[str], out: str, workdir) -> str:
    workdir = pathlib.Path(workdir)
    lst = workdir / "concat.txt"
    lst.write_text("".join(f"file '{pathlib.Path(c).resolve().as_posix()}'\n" for c in clips), encoding="utf-8")
    raw = workdir / "concat_raw.mp4"
    ff = ffmpeg_exe()
    run([ff, "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(raw)])
    run([ff, "-y", "-hide_banner", "-loglevel", "error", "-i", str(raw), "-c:v", "copy",
         "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
         "-movflags", "+faststart", out])
    return out


def split_chunks(text: str, max_words: int) -> list[str]:
    words = text.split()
    return [" ".join(words[i:i + max_words]) for i in range(0, len(words), max_words)] or [""]


def chunk_times(chunks: list[str], dur: float) -> list[tuple[float, float]]:
    total = sum(max(len(c), 1) for c in chunks)
    t, out = 0.0, []
    for c in chunks:
        d = dur * max(len(c), 1) / total
        out.append((t, t + d))
        t += d
    return out


def fmt_srt(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def write_srt(entries: list[tuple[float, float, str]], path) -> str:
    lines = []
    for i, (s, e, text) in enumerate(entries, 1):
        lines += [str(i), f"{fmt_srt(s)} --> {fmt_srt(e)}", text, ""]
    pathlib.Path(path).write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def verify_video(path, *, W: int, H: int, min_s: float, max_s: float) -> list[str]:
    """Medya bütünlük kontrolü. Boş liste = sorun yok."""
    issues = []
    p = pathlib.Path(path)
    if not p.exists() or p.stat().st_size < 10_000:
        return ["video dosyası yok ya da çok küçük"]
    info = probe(p)
    if not info["has_video"]:
        issues.append("video akışı yok")
    if not info["has_audio"]:
        issues.append("ses akışı yok")
    if (info["width"], info["height"]) != (W, H):
        issues.append(f"çözünürlük {info['width']}x{info['height']} (beklenen {W}x{H})")
    if not (min_s <= info["duration"] <= max_s):
        issues.append(f"süre {info['duration']:.1f}s, beklenen {min_s:.0f}-{max_s:.0f}s")
    if info["vcodec"] and info["vcodec"] != "h264":
        issues.append(f"video codec {info['vcodec']} (beklenen h264)")
    return issues


# ---------------------------------------------------------------------------
# Thumbnail
# ---------------------------------------------------------------------------
def make_thumbnails(base_image: str, texts: list[str], out_dir, size=(1280, 720)) -> list[dict]:
    """3 farklı taslak üretir ve sezgisel puanla sıralar (okunabilirlik, kısa metin, kontrast)."""
    Image, ImageDraw, ImageFont, ImageFilter, ImageStat = _pil()
    out_dir = pathlib.Path(out_dir)
    W, H = size
    base = Image.open(base_image).convert("RGB")
    scale = max(W / base.width, H / base.height)
    base = base.resize((int(base.width * scale) + 1, int(base.height * scale) + 1))
    left, top = (base.width - W) // 2, (base.height - H) // 2
    base = base.crop((left, top, left + W, top + H))
    results = []
    layouts = ["left_dark", "center_box", "bottom_band"]
    for i, layout in enumerate(layouts):
        text = (texts[i % len(texts)] if texts else "").upper()
        img = base.copy()
        ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(ov)
        fnt = font(int(H * (0.17 if len(text) < 18 else 0.13)))
        if layout == "left_dark":
            for x in range(int(W * 0.7)):
                a = int(210 * (1 - x / (W * 0.7)))
                d.line([(x, 0), (x, H)], fill=(0, 0, 0, a))
            lines = wrap(d, text, fnt, int(W * 0.58))
            y = H // 2 - len(lines) * int(fnt.size * 1.1) // 2
            for ln in lines:
                d.text((int(W * 0.05), y), ln, font=fnt, fill=(255, 221, 0), stroke_width=6, stroke_fill="black")
                y += int(fnt.size * 1.1)
            region = (0, 0, int(W * 0.6), H)
        elif layout == "center_box":
            lines = wrap(d, text, fnt, int(W * 0.8))
            lh = int(fnt.size * 1.15)
            bh = len(lines) * lh + 40
            y0 = (H - bh) // 2
            d.rounded_rectangle((int(W * 0.06), y0, int(W * 0.94), y0 + bh), radius=24, fill=(220, 20, 60, 225))
            y = y0 + 20
            for ln in lines:
                w = d.textlength(ln, font=fnt)
                d.text(((W - w) / 2, y), ln, font=fnt, fill="white")
                y += lh
            region = (int(W * 0.06), y0, int(W * 0.94), y0 + bh)
        else:
            lines = wrap(d, text, fnt, int(W * 0.9))
            lh = int(fnt.size * 1.1)
            bh = len(lines) * lh + 30
            d.rectangle((0, H - bh, W, H), fill=(0, 0, 0, 200))
            y = H - bh + 15
            for ln in lines:
                w = d.textlength(ln, font=fnt)
                d.text(((W - w) / 2, y), ln, font=fnt, fill=(255, 255, 255), stroke_width=3, stroke_fill="black")
                y += lh
            region = (0, H - bh, W, H)
        img = Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")
        path = out_dir / f"thumb_{i + 1}.jpg"
        q = 90
        img.save(path, quality=q)
        while path.stat().st_size > 1_900_000 and q > 50:
            q -= 10
            img.save(path, quality=q)
        # sezgisel puan: metin bölgesi kontrastı + kısa metin + mobilde okunabilir font
        bg_lum = ImageStat.Stat(base.crop(region).convert("L")).mean[0]
        contrast = abs(bg_lum - 255) if layout != "center_box" else 100
        words = len(text.split())
        score = min(contrast / 2.55, 60) + (30 if words <= 4 else 15 if words <= 6 else 0) + (10 if fnt.size >= H * 0.13 else 0)
        results.append({"path": str(path), "layout": layout, "text": text, "score": round(score, 1)})
    results.sort(key=lambda r: -r["score"])
    return results
