"""Görsel kaynakları: Pexels (ücretsiz stok foto/video) → Pollinations (ücretsiz AI görsel) → gradyan kart.

Her görsel için lisans/kaynak bilgisi döndürülür; pipeline bunu kayıt altına alır.
"""
from __future__ import annotations

import pathlib
import urllib.parse

from . import config, video
from .net import HttpError, request

PEXELS_LICENSE = "Pexels Lisansı (ücretsiz, ticari kullanım serbest)"


def _download(url: str, dest: pathlib.Path, min_bytes=8_000) -> pathlib.Path:
    r = request("GET", url, timeout=120, retries=2)
    if len(r.body) < min_bytes:
        raise HttpError(0, "indirilen dosya çok küçük", url)
    dest.write_bytes(r.body)
    return dest


def _pexels_video(query, dest_base, vertical, used):
    key = config.env("PEXELS_API_KEY")
    if not key:
        return None
    r = request("GET", "https://api.pexels.com/videos/search", headers={"Authorization": key}, params={
        "query": query, "per_page": 12, "orientation": "portrait" if vertical else "landscape", "size": "medium"}).json()
    for v in r.get("videos", []):
        if v["url"] in used or v.get("duration", 0) < 4:
            continue
        files = [f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4" and 700 <= (f.get("width") or 0) <= 2000]
        if not files:
            continue
        f = min(files, key=lambda x: abs((x["width"] if not vertical else x["height"]) - 1080))
        path = _download(f["link"], dest_base.with_suffix(".mp4"), 50_000)
        used.add(v["url"])
        return {"path": str(path), "type": "video", "provider": "pexels-video", "license": PEXELS_LICENSE,
                "credit_url": v["url"], "synthetic": False}
    return None


def _pexels_photo(query, dest_base, vertical, used):
    key = config.env("PEXELS_API_KEY")
    if not key:
        return None
    r = request("GET", "https://api.pexels.com/v1/search", headers={"Authorization": key}, params={
        "query": query, "per_page": 12, "orientation": "portrait" if vertical else "landscape"}).json()
    for p in r.get("photos", []):
        if p["url"] in used:
            continue
        src = p["src"].get("large2x") or p["src"]["original"]
        path = _download(src, dest_base.with_suffix(".jpg"))
        used.add(p["url"])
        return {"path": str(path), "type": "image", "provider": "pexels", "license": PEXELS_LICENSE,
                "credit_url": p["url"], "synthetic": False}
    return None


def _pollinations(prompt, dest_base, vertical, seed):
    W, H = (1080, 1920) if vertical else (1920, 1080)
    url = ("https://image.pollinations.ai/prompt/" + urllib.parse.quote(prompt[:600]) +
           f"?width={W}&height={H}&seed={seed}&nologo=true&model=flux")
    path = _download(url, dest_base.with_suffix(".jpg"), 15_000)
    return {"path": str(path), "type": "image", "provider": "pollinations", "license": "Yapay zekâ üretimi (Pollinations)",
            "credit_url": "https://pollinations.ai", "synthetic": True}


def get_visual(scene: dict, dest_dir, name: str, *, vertical: bool, seed: int, used: set) -> dict:
    """scene: {visual_type: stock|ai, visual_query (EN), visual_prompt (EN)}"""
    dest_dir = pathlib.Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    base = dest_dir / name
    W, H = (1080, 1920) if vertical else (1920, 1080)

    if config.mock() or config.skip_render():
        p = base.with_suffix(".jpg")
        p.write_bytes(b"MOCKIMG")
        return {"path": str(p), "type": "image", "provider": "mock", "license": "mock", "credit_url": "", "synthetic": False}

    query = scene.get("visual_query") or scene.get("visual_prompt") or "abstract background"
    prompt = scene.get("visual_prompt") or query
    order = ["pexels_video", "pexels_photo", "ai"] if scene.get("visual_type", "stock") == "stock" else ["ai", "pexels_photo"]
    for step in order:
        try:
            res = None
            if step == "pexels_video":
                res = _pexels_video(query, base, vertical, used)
            elif step == "pexels_photo":
                res = _pexels_photo(query, base, vertical, used)
            elif step == "ai":
                res = _pollinations(prompt + ", cinematic, high detail, no text, no watermark", base, vertical, seed)
            if res:
                return res
        except HttpError:
            continue
    path = video.gradient_png(base.with_suffix(".png"), W, H, seed)
    return {"path": path, "type": "image", "provider": "gradient", "license": "özgün (otomatik üretim)",
            "credit_url": "", "synthetic": False}
