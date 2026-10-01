/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const api = process.env.VITE_API_BASE ?? "http://127.0.0.1:8000";
const oidc = process.env.VITE_OIDC_AUTHORITY ?? "http://127.0.0.1:8000/dev/oidc";
/** Same policy as nginx.conf.template, so browser tests run under the production CSP. */
const csp = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self'",
  "img-src 'self' data:",
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
  build: { sourcemap: true, target: "es2022" },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}", "test/**/*.test.{ts,tsx}"],
  },
});
