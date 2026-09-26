import { Container, getContainer } from "@cloudflare/containers";

export class MxDownloaderContainer extends Container {
  defaultPort = 10000;
  sleepAfter = "2m";
  enableInternet = true;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/worker-health") {
      return Response.json({ ok: true, service: "mxdownloader-cloudflare-worker" });
    }

    // One shared backend instance is enough for the initial deployment.
    // The FastAPI app already limits simultaneous download jobs.
    const backend = getContainer(env.MX_BACKEND, "primary");
    return backend.fetch(request);
  },
};
