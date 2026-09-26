# MX Downloader

MX Downloader is a clean web app for analyzing and downloading public media links that you are permitted to save.

## Features
- Paste a public media URL
- Analyze title, thumbnail, duration and source
- Offer common MP4 qualities
- Offer MP3 when an audio stream is available
- Uses `yt-dlp` + FFmpeg on the backend
- No personal link history by default
- Rejects localhost/private-network targets to reduce SSRF risk

## Local Windows start
1. Install Python 3.11+
2. Install FFmpeg and make sure `ffmpeg` is in PATH
3. Run `start.bat`
4. Open `http://127.0.0.1:8000`

## Docker
```bash
docker build -t mx-downloader .
docker run --rm -p 8000:8000 mx-downloader
```

## Deployment note
GitHub Pages or Cloudflare Pages alone cannot run the Python + yt-dlp + FFmpeg backend. For a public deployment, run the app on a server/container host and optionally put Cloudflare in front of it.

## Usage
Use only with media you own or are permitted to download. The app does not attempt to bypass DRM, paywalls, private/login-only content, or access controls.
