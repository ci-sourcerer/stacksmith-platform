import { createServer as createHttpServer } from "node:http";
import { once } from "node:events";
import { createServer as createViteServer } from "vite";
import { expect, it } from "vitest";
import config from "./vite.config";

it.each(["http", "https"])(
  "preserves the browser host and %s scheme through the API proxy",
  async (scheme) => {
    let publicOrigin;
    const backend = createHttpServer((request, response) => {
      response.writeHead(307, {
        Location:
          `${request.headers["x-forwarded-proto"] || "http"}://${request.headers.host}` ===
          publicOrigin
            ? "https://github.com/login/oauth/authorize"
            : "http://127.0.0.1:5173/api/auth/github",
      });
      response.end();
    });
    backend.listen(0, "127.0.0.1");
    await once(backend, "listening");
    const route = config.server.proxy["/api"];
    const target = `http://127.0.0.1:${backend.address().port}`;
    const proxy = await createViteServer({
      configFile: false,
      logLevel: "silent",
      optimizeDeps: { noDiscovery: true, entries: [] },
      server: {
        host: "127.0.0.1",
        port: 0,
        proxy: {
          "/api": typeof route === "string" ? target : { ...route, target },
        },
      },
    });
    try {
      await proxy.listen();
      publicOrigin = `${scheme}://127.0.0.1:${proxy.httpServer.address().port}`;
      const response = await fetch(
        `http://127.0.0.1:${proxy.httpServer.address().port}/api/auth/github`,
        {
          redirect: "manual",
          headers: { "X-Forwarded-Proto": scheme },
        },
      );
      expect(response.status).toBe(307);
      expect(response.headers.get("location")).toBe(
        "https://github.com/login/oauth/authorize",
      );
    } finally {
      await proxy.close();
      backend.close();
      await once(backend, "close");
    }
  },
);
