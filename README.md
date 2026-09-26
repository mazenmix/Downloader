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

## Current production frontend

`https://mxdownloader.pages.dev/`

The frontend is hosted on Cloudflare Pages from the `static` directory.

## Recommended all-Cloudflare deployment

The repository now supports:

`Cloudflare Pages -> Pages Function /api/* -> Service Binding -> Cloudflare Worker -> Cloudflare Container -> FastAPI + yt-dlp + FFmpeg`

### 1. Pages frontend

Existing Pages project settings:

- Repository: `mazenmix/Downloader`
- Production branch: `main`
- Framework preset: `None`
- Build command: `exit 0`
- Build output directory: `static`
- Root directory: repository root

The repository includes `functions/api/[[path]].js`, so `/api/*` can proxy to the backend Worker through a Service Binding.

### 2. Create the backend Worker with Containers

Cloudflare Containers require the Workers Paid plan.

In Cloudflare:

1. Go to **Workers & Pages**.
2. Choose **Create application** -> **Import a repository**.
3. Select `mazenmix/Downloader`.
4. Name the Worker exactly `mxdownloader-api`.
5. Production branch: `main`.
6. Root directory: repository root.
7. Deploy command: `npx wrangler deploy -c wrangler.worker.jsonc`.
8. Save and deploy.

Workers Builds can build the Dockerfile and publish the Container automatically.

The container is configured as `basic`, with one maximum active instance and a 2-minute idle sleep timeout to control cost.

### 3. Bind Pages to the backend Worker

After the Worker exists:

1. Open the existing `mxdownloader` Pages project.
2. Go to **Settings -> Bindings**.
3. Add a **Service binding**.
4. Variable name: `DOWNLOADER_API`.
5. Service: `mxdownloader-api`.
6. Save and redeploy the Pages project.

No public backend URL is required. The frontend continues using same-origin `/api/*` routes.

### 4. Verify

Open:

- `https://mxdownloader.pages.dev/`
- `https://mxdownloader.pages.dev/api/health`

When the binding and Container are working, `/api/health` should return JSON with `"ok": true`, and the site badge changes to `ONLINE`.

## Free fallback

`render.yaml` remains in the repository as an optional free external backend deployment path if Cloudflare Containers are not enabled.

## Local Windows start

1. Install Python 3.11+
2. Install FFmpeg and make sure `ffmpeg` is in PATH
3. Run `start.bat`
4. Open `http://127.0.0.1:8000`

## Docker

```bash
docker build -t mx-downloader .
docker run --rm -p 10000:10000 -e PORT=10000 mx-downloader
```

## Usage

Use only with media you own or are permitted to download. The app does not attempt to bypass DRM, paywalls, private/login-only content, or access controls.
