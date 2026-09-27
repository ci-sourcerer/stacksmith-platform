import { defineConfig, type ProxyOptions } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  cacheDir: process.env.VITE_CACHE_DIR || "node_modules/.vite",
  server: {
    port: 5173,
    strictPort: true,
    allowedHosts: [
      new URL(
        process.env.STACKSMITH_PLATFORM_PUBLIC_URL || "http://localhost:8000",
      ).hostname,
    ],
    proxy: Object.fromEntries(
      ["/api", "/docs", "/redoc", "/openapi.json"].map((path) => [
        path,
        {
          target: process.env.API_UPSTREAM || "http://127.0.0.1:8000",
          changeOrigin: false,
          xfwd: true,
          configure(proxy) {
            proxy.on("proxyReq", (proxyRequest, request) => {
              proxyRequest.setHeader(
                "X-Forwarded-Proto",
                String(request.headers["x-forwarded-proto"] || "http")
                  .split(",")[0]
                  .trim() === "https"
                  ? "https"
                  : "http",
              );
            });
          },
        } satisfies ProxyOptions,
      ]),
    ),
  },
});
