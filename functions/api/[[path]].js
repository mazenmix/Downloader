export async function onRequest(context) {
  const service = context.env.DOWNLOADER_API;

  if (!service || typeof service.fetch !== "function") {
    return Response.json(
      {
        detail: "MX Downloader backend is not connected yet. Add the Cloudflare Service Binding DOWNLOADER_API to the mxdownloader-api Worker."
      },
      { status: 503 }
    );
  }

  return service.fetch(context.request);
}
