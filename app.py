from __future__ import annotations

import asyncio
import ipaddress
import shutil
import socket
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, HttpUrl
from starlette.background import BackgroundTask
import yt_dlp

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"

app = FastAPI(title="MX Downloader", version="1.0.0")

download_semaphore = asyncio.Semaphore(2)

class AnalyzeRequest(BaseModel):
    url: HttpUrl

class DownloadRequest(BaseModel):
    url: HttpUrl
    quality: str = "720"

def _validate_public_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(400, "Only http/https URLs are allowed.")
    if not parsed.hostname:
        raise HTTPException(400, "Invalid URL.")
    hostname = parsed.hostname.strip().lower()
    if hostname in {"localhost", "localhost.localdomain"}:
        raise HTTPException(400, "Local addresses are not allowed.")
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        raise HTTPException(400, "Hostname could not be resolved.")
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            raise HTTPException(400, "Private or local network targets are blocked.")
    return url

def _base_ydl_opts() -> dict[str, Any]:
    return {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "ignoreerrors": False,
        "extract_flat": False,
        "cachedir": False,
        "socket_timeout": 20,
        "retries": 2,
        "fragment_retries": 2,
        "restrictfilenames": True,
        "windowsfilenames": True,
    }

def _extract_info(url: str) -> dict[str, Any]:
    with yt_dlp.YoutubeDL(_base_ydl_opts()) as ydl:
        return ydl.extract_info(url, download=False)

def _format_options(info: dict[str, Any]) -> list[dict[str, Any]]:
    formats = info.get("formats") or []
    heights = sorted({int(f["height"]) for f in formats if isinstance(f, dict) and f.get("height") and f.get("vcodec") not in {None, "none"}})
    standard = [360, 480, 720, 1080, 1440, 2160]
    available = [q for q in standard if any(h >= q for h in heights)]
    if heights and not available:
        available = [max(heights)]
    max_h = max(heights) if heights else 0
    available = [q for q in available if q <= max_h] or ([max_h] if max_h else [])
    options = [{"id": str(q), "label": f"{q}p", "kind": "video"} for q in available]
    has_audio = any(isinstance(f, dict) and f.get("acodec") not in {None, "none"} for f in formats)
    if has_audio:
        options.append({"id": "audio", "label": "Audio MP3", "kind": "audio"})
    return options

def _download_to_temp(url: str, quality: str) -> tuple[Path, Path]:
    temp_dir = Path(tempfile.mkdtemp(prefix="mxdl_"))
    if quality == "audio":
        opts = {
            **_base_ydl_opts(),
            "format": "bestaudio/best",
            "outtmpl": str(temp_dir / "%(title).120B-%(id)s.%(ext)s"),
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}],
        }
    else:
        try:
            q = int(quality)
        except ValueError:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise HTTPException(400, "Invalid quality.")
        if q < 144 or q > 4320:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise HTTPException(400, "Invalid quality.")
        opts = {
            **_base_ydl_opts(),
            "format": f"bestvideo[height<={q}]+bestaudio/best[height<={q}]/best",
            "merge_output_format": "mp4",
            "outtmpl": str(temp_dir / "%(title).120B-%(id)s.%(ext)s"),
        }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
        candidates = [p for p in temp_dir.iterdir() if p.is_file()]
        if not candidates:
            raise RuntimeError("No output file created.")
        if quality == "audio":
            mp3s = [p for p in candidates if p.suffix.lower() == ".mp3"]
            output = max(mp3s or candidates, key=lambda p: p.stat().st_mtime)
        else:
            mp4s = [p for p in candidates if p.suffix.lower() == ".mp4"]
            output = max(mp4s or candidates, key=lambda p: p.stat().st_mtime)
        return temp_dir, output
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise

@app.get("/api/health")
def health():
    return {"ok": True, "service": "MX Downloader", "ffmpeg": shutil.which("ffmpeg") is not None}

@app.post("/api/analyze")
async def analyze(payload: AnalyzeRequest):
    url = _validate_public_url(str(payload.url))
    try:
        info = await asyncio.to_thread(_extract_info, url)
    except yt_dlp.utils.DownloadError as exc:
        raise HTTPException(422, "This link could not be analyzed. It may be private, DRM-protected, login-only, unsupported, or temporarily blocked.") from exc
    except Exception as exc:
        raise HTTPException(500, "Could not analyze this link.") from exc
    if not info:
        raise HTTPException(422, "No downloadable media was found.")
    return {
        "title": info.get("title") or "Untitled media",
        "uploader": info.get("uploader") or info.get("channel"),
        "thumbnail": info.get("thumbnail"),
        "duration": info.get("duration"),
        "extractor": info.get("extractor_key") or info.get("extractor"),
        "url": info.get("webpage_url") or url,
        "formats": _format_options(info),
    }

@app.post("/api/download")
async def download(payload: DownloadRequest):
    url = _validate_public_url(str(payload.url))
    quality = payload.quality.strip().lower()
    async with download_semaphore:
        try:
            temp_dir, output = await asyncio.to_thread(_download_to_temp, url, quality)
        except yt_dlp.utils.DownloadError as exc:
            raise HTTPException(422, "Download failed. The source may require login, use DRM, block this server, or no longer expose that format.") from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(500, "Download failed.") from exc
    media_type = "audio/mpeg" if output.suffix.lower() == ".mp3" else "video/mp4"
    return FileResponse(path=output, filename=output.name, media_type=media_type, background=BackgroundTask(shutil.rmtree, temp_dir, True))

app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
