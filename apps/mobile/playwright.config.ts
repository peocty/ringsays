/// <reference types="node" />
import { existsSync } from "node:fs";
import { defineConfig, devices } from "@playwright/test";

/**
 * The RingSays app's web build, in a phone sized browser, against a running local API.
 * Build first with a MOCK push token so the MOCK push provider delivers to it:
 *   EXPO_PUBLIC_RINGSAYS_PREVIEW_PUSH_TOKEN=mock-web pnpm export:web
 */
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_PATH ?? (existsSync("/opt/pw-browsers/chromium") ? "/opt/pw-browsers/chromium" : undefined);

export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  workers: 1,
  // On GitHub, failures also become annotations (readable without downloading logs).
  reporter: process.env.CI ? [["github"], ["list"]] : [["list"]],
  globalSetup: "./e2e/global-setup.ts",
  use: {
    baseURL: "http://127.0.0.1:8081",
    ...devices["Pixel 7"],
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    launchOptions: executablePath ? { executablePath } : {},
  },
  webServer: { command: "node e2e/serve.mjs", url: "http://127.0.0.1:8081", reuseExistingServer: true },
});
