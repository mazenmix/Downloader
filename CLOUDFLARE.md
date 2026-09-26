# Cloudflare Pages setup — MX Downloader

Use these exact settings when creating the Pages project:

- Repository: `mazenmix/Downloader`
- Production branch: `main`
- Framework preset: `None`
- Build command: leave empty
- Build output directory: `static`
- Root directory: `/`

The frontend will deploy correctly to a `*.pages.dev` address.

## Backend connection

Cloudflare Pages hosts only the frontend. The actual downloader backend uses Python + yt-dlp + FFmpeg and must run on a server/container host.

When the backend is live, edit `static/config.js`:

```js
window.MX_CONFIG = {
  API_BASE: "https://YOUR-BACKEND-HOST"
};
```

On the backend host, set `CORS_ORIGINS` to the Pages URL, for example:

```text
CORS_ORIGINS=https://downloader.pages.dev
```

You can include multiple origins separated by commas.

## Local mode

Leave `API_BASE` blank and run FastAPI locally. The frontend then uses the same origin for `/api/*`.
