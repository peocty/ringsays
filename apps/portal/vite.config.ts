/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Origins only (scheme, host, port), exactly as nginx/15-ringsays.envsh derives them: a CSP source
// with a path matches that one URL only, which would block OIDC discovery and token calls.
const api = new URL(process.env.VITE_API_BASE ?? "http://127.0.0.1:8000").origin;
const oidc = new URL(process.env.VITE_OIDC_AUTHORITY ?? "http://127.0.0.1:8000/dev/oidc").origin;
/** Same policy as nginx.conf.template, so browser tests run under the production CSP. */
const csp = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self'",
  "img-src 'self'",
  "font-src 'self'",
  `connect-src 'self' ${api} ${oidc}`,
  `form-action 'self' ${oidc}`,
  "frame-ancestors 'none'",
  "base-uri 'none'",
  "object-src 'none'",
].join("; ");

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true },
  preview: {
    port: 4173,
    strictPort: true,
    headers: { "Content-Security-Policy": csp, "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer" },
  },
  // No inlined data: assets, so the strict font-src and img-src policy holds.
  build: { sourcemap: "hidden", target: "es2022", assetsInlineLimit: 0 },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}", "test/**/*.test.{ts,tsx}"],
  },
});
