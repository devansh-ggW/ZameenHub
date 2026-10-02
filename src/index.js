export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/api/health") {
      return Response.json({
        ok: true,
        service: "cinderclip",
        version: "0.1.0",
        runtime: "cloudflare-worker",
        processing: false,
        storage: false
      });
    }

    if (url.pathname === "/api/upload" || url.pathname === "/api/generate") {
      return Response.json(
        {
          ok: false,
          error: "Cloud video processing is not connected yet. This deployment currently uses the local video engine bridge."
        },
        { status: 501 }
      );
    }

    if (url.pathname.startsWith("/api/")) {
      return Response.json(
        { ok: false, error: "API endpoint not found." },
        { status: 404 }
      );
    }

    const assets = await env.ASSETS.fetch(request);
    const response = new Response(assets.body, assets);
    response.headers.set("Permissions-Policy", "loopback-network=(self)");
    return response;
  }
};
