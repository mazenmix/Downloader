from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
import imageio_ffmpeg
import yt_dlp
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, HttpUrl
from starlette.background import BackgroundTask

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
POT_PROVIDER_URL = os.getenv("POT_PROVIDER_URL", "").strip().rstrip("/")

DEFAULT_PIPED_INSTANCES = [
    "https://pipedapi.kavin.rocks",
    "https://pipedapi.adminforge.de",
    "https://api.piped.private.coffee",
    "https://api.piped.yt",
    "https://piped-api.privacy.com.de",
]
PIPED_INSTANCES = [
    x.strip().rstrip("/")
    for x in os.getenv("PIPED_INSTANCES", ",".join(DEFAULT_PIPED_INSTANCES)).split(",")
    if x.strip()
]

MAX_FETCH_BYTES = int(os.getenv("MAX_FETCH_BYTES", str(1_500_000_000)))
HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/154 Safari/537.36",
    "Accept": "*/*",
}

try:
    FFMPEG_EXE = shutil.which("ffmpeg") or imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FFMPEG_EXE = shutil.which("ffmpeg")

app = FastAPI(title="MX Downloader", version="1.5.0")

cors_raw = os.getenv("CORS_ORIGINS", "*").strip()
cors_origins = ["*"] if cors_raw == "*" else [x.strip() for x in cors_raw.split(",") if x.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
    expose_headers=["Content-Disposition"],
)

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
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise HTTPException(400, "Private or local network targets are blocked.")
    return url


def _is_youtube_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return (
        host == "youtu.be"
        or host.endswith(".youtu.be")
        or host == "youtube.com"
        or host.endswith(".youtube.com")
        or host == "youtube-nocookie.com"
        or host.endswith(".youtube-nocookie.com")
    )


def _youtube_video_id(url: str) -> str | None:
    p = urlparse(url)
    host = (p.hostname or "").lower()
    path = p.path.strip("/")

    if host == "youtu.be" or host.endswith(".youtu.be"):
        candidate = path.split("/")[0] if path else ""
    elif host == "youtube.com" or host.endswith(".youtube.com") or host.endswith(".youtube-nocookie.com"):
        if path == "watch":
            candidate = (parse_qs(p.query).get("v") or [""])[0]
        elif path.startswith("shorts/") or path.startswith("embed/") or path.startswith("live/"):
            candidate = path.split("/", 1)[1].split("/")[0]
        else:
            candidate = (parse_qs(p.query).get("v") or [""])[0]
    else:
        return None

    return candidate if re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate or "") else None


def _client_candidates(url: str) -> list[str | None]:
    if _is_youtube_url(url):
        if POT_PROVIDER_URL:
            return ["mweb", "android_vr", "web_embedded", None]
        return ["android_vr", "web_embedded", None]
    return [None]


def _base_ydl_opts(youtube_client: str | None = None) -> dict[str, Any]:
    opts: dict[str, Any] = {
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
    if FFMPEG_EXE:
        opts["ffmpeg_location"] = FFMPEG_EXE

    extractor_args: dict[str, dict[str, list[str]]] = {}
    if youtube_client:
        extractor_args["youtube"] = {"player_client": [youtube_client]}
    if POT_PROVIDER_URL and youtube_client == "mweb":
        extractor_args["youtubepot-bgutilhttp"] = {"base_url": [POT_PROVIDER_URL]}
    if extractor_args:
        opts["extractor_args"] = extractor_args
    return opts


def _piped_get(video_id: str) -> dict[str, Any]:
    errors: list[str] = []
    timeout = httpx.Timeout(12.0, connect=6.0)
    for base in PIPED_INSTANCES:
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True, headers=HTTP_HEADERS) as client:
                r = client.get(f"{base}/streams/{video_id}")
                r.raise_for_status()
                data = r.json()
            if isinstance(data, dict) and (data.get("videoStreams") or data.get("audioStreams")):
                data["_mx_piped_instance"] = base
                return data
            errors.append(f"{base}: empty response")
        except Exception as exc:
            errors.append(f"{base}: {type(exc).__name__}")
    raise RuntimeError("Piped fallback failed: " + "; ".join(errors))


def _piped_options(data: dict[str, Any]) -> list[dict[str, Any]]:
    heights = sorted(
        {
            int(s.get("height") or 0)
            for s in (data.get("videoStreams") or [])
            if isinstance(s, dict) and int(s.get("height") or 0) > 0 and s.get("url")
        }
    )
    standard = [360, 480, 720, 1080, 1440, 2160]
    available = [q for q in standard if any(h >= q for h in heights)]
    if heights and not available:
        available = [max(heights)]
    max_h = max(heights) if heights else 0
    available = [q for q in available if q <= max_h] or ([max_h] if max_h else [])
    options = [{"id": str(q), "label": f"{q}p", "kind": "video"} for q in available]
    if data.get("audioStreams"):
        options.append({"id": "audio", "label": "Audio MP3", "kind": "audio"})
    return options


def _piped_to_info(data: dict[str, Any], original_url: str, video_id: str) -> dict[str, Any]:
    return {
        "title": data.get("title") or "Untitled media",
        "uploader": data.get("uploader"),
        "thumbnail": data.get("thumbnailUrl"),
        "duration": data.get("duration"),
        "extractor": "YouTube • Piped fallback",
        "webpage_url": original_url,
        "formats": [],
        "_mx_provider": "piped",
        "_mx_video_id": video_id,
        "_mx_piped": data,
    }


def _extract_info(url: str) -> dict[str, Any]:
    last_error: Exception | None = None
    for client in _client_candidates(url):
        try:
            with yt_dlp.YoutubeDL(_base_ydl_opts(client)) as ydl:
                return ydl.extract_info(url, download=False)
        except Exception as exc:
            last_error = exc
            print(f"yt-dlp analyze failed client={client or 'default'}: {exc}", flush=True)

    if _is_youtube_url(url):
        video_id = _youtube_video_id(url)
        if video_id:
            try:
                data = _piped_get(video_id)
                print(f"Piped analyze fallback succeeded via {data.get('_mx_piped_instance')}", flush=True)
                return _piped_to_info(data, url, video_id)
            except Exception as exc:
                print(f"Piped analyze fallback failed: {exc}", flush=True)
                last_error = exc

    if last_error:
        raise last_error
    raise RuntimeError("No extractor succeeded.")


def _format_options(info: dict[str, Any]) -> list[dict[str, Any]]:
    if info.get("_mx_provider") == "piped":
        return _piped_options(info.get("_mx_piped") or {})

    formats = info.get("formats") or []
    heights = sorted(
        {
            int(f["height"])
            for f in formats
            if isinstance(f, dict) and f.get("height") and f.get("vcodec") not in {None, "none"}
        }
    )
    standard = [360, 480, 720, 1080, 1440, 2160]
    available = [q for q in standard if any(h >= q for h in heights)]
    if heights and not available:
        available = [max(heights)]
    max_h = max(heights) if heights else 0
    available = [q for q in available if q <= max_h] or ([max_h] if max_h else [])
    options = [{"id": str(q), "label": f"{q}p", "kind": "video"} for q in available]
    if any(isinstance(f, dict) and f.get("acodec") not in {None, "none"} for f in formats):
        options.append({"id": "audio", "label": "Audio MP3", "kind": "audio"})
    return options


def _download_once(url: str, quality: str, youtube_client: str | None) -> tuple[Path, Path]:
    temp_dir = Path(tempfile.mkdtemp(prefix="mxdl_"))
    if quality == "audio":
        opts = {
            **_base_ydl_opts(youtube_client),
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
            **_base_ydl_opts(youtube_client),
            "format": f"bestvideo[height<={q}]+bestaudio/best[height<={q}]/best",
            "merge_output_format": "mp4",
            "outtmpl": str(temp_dir / "%(title).120B-%(id)s.%(ext)s"),
        }

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(url, download=True)
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


def _safe_name(name: str) -> str:
    name = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", name).strip().strip(".")
    return (name[:120] or "media")


def _download_http_file(url: str, destination: Path) -> None:
    current = url
    total = 0
    timeout = httpx.Timeout(30.0, connect=10.0, read=120.0)
    with httpx.Client(timeout=timeout, follow_redirects=False, headers=HTTP_HEADERS) as client:
        for _ in range(6):
            _validate_public_url(current)
            with client.stream("GET", current) as r:
                if 300 <= r.status_code < 400 and r.headers.get("location"):
                    current = urljoin(current, r.headers["location"])
                    continue
                r.raise_for_status()
                with destination.open("wb") as f:
                    for chunk in r.iter_bytes(1024 * 512):
                        total += len(chunk)
                        if total > MAX_FETCH_BYTES:
                            raise RuntimeError("Media exceeds server download limit.")
                        f.write(chunk)
                return
        raise RuntimeError("Too many redirects while fetching media.")


def _select_piped_video(data: dict[str, Any], q: int) -> dict[str, Any]:
    streams = [s for s in (data.get("videoStreams") or []) if isinstance(s, dict) and s.get("url")]
    if not streams:
        raise RuntimeError("No Piped video stream available.")

    def height(s: dict[str, Any]) -> int:
        try:
            return int(s.get("height") or 0)
        except Exception:
            m = re.search(r"(\d{3,4})", str(s.get("quality") or ""))
            return int(m.group(1)) if m else 0

    eligible = [s for s in streams if 0 < height(s) <= q] or streams
    eligible.sort(
        key=lambda s: (
            height(s),
            1 if "mp4" in str(s.get("mimeType") or "").lower() else 0,
            1 if not s.get("videoOnly") else 0,
            int(s.get("fps") or 0),
            int(s.get("bitrate") or 0),
        ),
        reverse=True,
    )
    return eligible[0]


def _select_piped_audio(data: dict[str, Any]) -> dict[str, Any]:
    streams = [s for s in (data.get("audioStreams") or []) if isinstance(s, dict) and s.get("url")]
    if not streams:
        raise RuntimeError("No Piped audio stream available.")
    streams.sort(
        key=lambda s: (
            1 if "mp4" in str(s.get("mimeType") or "").lower() or str(s.get("format") or "").upper() == "M4A" else 0,
            int(s.get("bitrate") or 0),
        ),
        reverse=True,
    )
    return streams[0]


def _run_ffmpeg(args: list[str]) -> None:
    if not FFMPEG_EXE:
        raise RuntimeError("FFmpeg is unavailable.")
    proc = subprocess.run([FFMPEG_EXE, "-y", *args], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or "FFmpeg failed")[-1200:])


def _piped_download(url: str, quality: str) -> tuple[Path, Path]:
    video_id = _youtube_video_id(url)
    if not video_id:
        raise RuntimeError("Could not determine YouTube video ID.")
    data = _piped_get(video_id)
    if data.get("livestream"):
        raise RuntimeError("Livestream downloads are not supported by the fallback yet.")

    temp_dir = Path(tempfile.mkdtemp(prefix="mxpiped_"))
    title = _safe_name(str(data.get("title") or video_id))
    try:
        if quality == "audio":
            audio = _select_piped_audio(data)
            mime = str(audio.get("mimeType") or "").lower()
            src = temp_dir / ("audio.m4a" if "mp4" in mime else "audio.webm")
            out = temp_dir / f"{title}.mp3"
            _download_http_file(str(audio["url"]), src)
            _run_ffmpeg(["-i", str(src), "-vn", "-codec:a", "libmp3lame", "-b:a", "192k", str(out)])
            return temp_dir, out

        try:
            q = int(quality)
        except ValueError:
            raise HTTPException(400, "Invalid quality.")
        if q < 144 or q > 4320:
            raise HTTPException(400, "Invalid quality.")

        video = _select_piped_video(data, q)
        v_mime = str(video.get("mimeType") or "").lower()
        v_src = temp_dir / ("video.mp4" if "mp4" in v_mime else "video.webm")
        _download_http_file(str(video["url"]), v_src)
        out = temp_dir / f"{title}.mp4"

        if not video.get("videoOnly") and "mp4" in v_mime:
            shutil.move(str(v_src), str(out))
            return temp_dir, out

        audio = _select_piped_audio(data)
        a_mime = str(audio.get("mimeType") or "").lower()
        a_src = temp_dir / ("audio.m4a" if "mp4" in a_mime else "audio.webm")
        _download_http_file(str(audio["url"]), a_src)

        try:
            _run_ffmpeg(["-i", str(v_src), "-i", str(a_src), "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", str(out)])
        except Exception:
            _run_ffmpeg(["-i", str(v_src), "-i", str(a_src), "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-b:a", "192k", "-shortest", str(out)])
        return temp_dir, out
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def _download_to_temp(url: str, quality: str) -> tuple[Path, Path]:
    last_error: Exception | None = None
    for client in _client_candidates(url):
        try:
            return _download_once(url, quality, client)
        except HTTPException:
            raise
        except Exception as exc:
            last_error = exc
            print(f"yt-dlp download failed client={client or 'default'}: {exc}", flush=True)

    if _is_youtube_url(url):
        try:
            result = _piped_download(url, quality)
            print("Piped download fallback succeeded", flush=True)
            return result
        except HTTPException:
            raise
        except Exception as exc:
            print(f"Piped download fallback failed: {exc}", flush=True)
            last_error = exc

    if last_error:
        raise last_error
    raise RuntimeError("No download method succeeded.")


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "service": "MX Downloader",
        "ffmpeg": bool(FFMPEG_EXE),
        "version": "1.5.0",
        "youtubeFallback": "piped",
    }


@app.get("/api/piped-test/{video_id}")
def piped_test(video_id: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise HTTPException(400, "Invalid video ID")
    try:
        data = _piped_get(video_id)
        return {
            "ok": True,
            "title": data.get("title"),
            "duration": data.get("duration"),
            "instance": data.get("_mx_piped_instance"),
            "videoStreams": len(data.get("videoStreams") or []),
            "audioStreams": len(data.get("audioStreams") or []),
        }
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc


@app.post("/api/analyze")
async def analyze(payload: AnalyzeRequest):
    url = _validate_public_url(str(payload.url))
    try:
        info = await asyncio.to_thread(_extract_info, url)
    except Exception as exc:
        raise HTTPException(
            422,
            "This link could not be analyzed. The source may be private, protected, unsupported, or temporarily blocking all available public extraction routes.",
        ) from exc

    if not info:
        raise HTTPException(422, "No downloadable media was found.")

    return {
        "title": info.get("title") or "Untitled media",
        "uploader": info.get("uploader") or info.get("channel"),
        "thumbnail": info.get("thumbnail") or info.get("thumbnailUrl"),
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
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                422,
                "Download failed. The source may be private/protected, or all public download routes may be temporarily unavailable.",
            ) from exc

    media_type = "audio/mpeg" if output.suffix.lower() == ".mp3" else "video/mp4"
    return FileResponse(
        path=output,
        filename=output.name,
        media_type=media_type,
        background=BackgroundTask(shutil.rmtree, temp_dir, True),
    )


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
