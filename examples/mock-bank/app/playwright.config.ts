/// <reference types="node" />
import { existsSync } from "node:fs";
import { defineConfig, devices } from "@playwright/test";

/**
 * Whole Mock Bank flow: agent console (bank server, :4100) and customer app web build (:8082)
 * against a running local RingSays API (:8000). Build first: `pnpm export:web` here and
 * `pnpm --filter @mockbank/server build`.
 */
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_PATH ?? (existsSync("/opt/pw-browsers/chromium") ? "/opt/pw-browsers/chromium" : undefined);

export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  workers: 1,
  reporter: [["list"]],
  use: {
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    launchOptions: executablePath ? { executablePath } : {},
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    { command: "node e2e/start-bank.mjs", url: "http://127.0.0.1:4100/health", reuseExistingServer: false, timeout: 60_000 },
    { command: "node e2e/serve.mjs", url: "http://127.0.0.1:8082", reuseExistingServer: true },
  ],
});
