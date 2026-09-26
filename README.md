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

## Recommended deployment

### 1. Frontend — Cloudflare Pages
Connect this repository to Cloudflare Pages with:

- Repository: `mazenmix/Downloader`
- Production branch: `main`
- Framework preset: `None`
- Build command: `exit 0`
- Build output directory: `static`
- Root directory: repository root

Cloudflare will publish the frontend on a `*.pages.dev` address and redeploy after Git pushes.

### 2. Backend — Render Free Web Service
This repository includes `render.yaml` and a Dockerfile that install FFmpeg and run FastAPI/yt-dlp.

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/mazenmix/Downloader)

After Render creates the service, copy its public `https://...onrender.com` URL and put it in `static/config.js`:

```js
window.MX_CONFIG = {
  API_BASE: "https://YOUR-SERVICE.onrender.com"
};
```

Then Cloudflare Pages will automatically redeploy from the GitHub update.

> Render Free services can spin down after inactivity, so the first request after an idle period may take longer.

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

## Usage
Use only with media you own or are permitted to download. The app does not attempt to bypass DRM, paywalls, private/login-only content, or access controls.
