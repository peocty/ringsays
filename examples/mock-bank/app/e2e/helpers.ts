import { execFileSync } from "node:child_process";
import path from "node:path";

import { devices, expect, type Browser, type Page } from "@playwright/test";

const backend = path.resolve(import.meta.dirname, "../../../../backend");
const py = process.env.E2E_PYTHON ?? path.join(backend, ".venv/bin/python");
export const CONSOLE = "http://127.0.0.1:4100/console";
export const APP = "http://127.0.0.1:8082";

/** One worker pass: webhook deliveries to the bank (and SDK fallbacks). */
export function tick(): void {
  execFileSync(py, ["-m", "app.worker", "--once"], { cwd: backend, stdio: "ignore", env: { ...process.env, RINGSAYS_ENVIRONMENT: "local" } });
}

export async function openConsole(browser: Browser): Promise<Page> {
  const page = await (await browser.newContext({ timezoneId: "Asia/Riyadh" })).newPage();
  await page.goto(CONSOLE);
  await page.getByLabel("Console password").fill("e2e-console");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Ask a customer for a call" })).toBeVisible();
  return page;
}

export async function sendRequest(
  page: Page,
  opts: { customer: string; purpose?: string; reference?: string; offerSlots?: number },
): Promise<string> {
  await page.getByLabel("Customer").selectOption(opts.customer);
  await page.getByLabel("Reason (approved purpose)").selectOption(opts.purpose ?? "MORTGAGE.DOC.CLARIFY");
  if (opts.reference) await page.getByLabel("Reference (last 4)").fill(opts.reference);
  for (let i = 0; i < (opts.offerSlots ?? 0); i++) await page.locator(`#slot-${i}`).check();
  const created = page.waitForResponse((r) => r.url().endsWith("/console/api/intents") && r.request().method() === "POST");
  await page.getByRole("button", { name: "Send request" }).click();
  const res = await created;
  expect(res.status()).toBe(200);
  const body = (await res.json()) as { intentId: string };
  expect(JSON.stringify(body)).not.toMatch(/contextToken/);
  return body.intentId;
}

export function row(page: Page, intentId: string) {
  return page.locator(`tr[data-intent="${intentId}"]`);
}

export async function openApp(browser: Browser, customerId: string): Promise<Page> {
  const page = await (await browser.newContext({ ...devices["Pixel 7"], timezoneId: "Asia/Riyadh" })).newPage();
  await page.goto(APP);
  await page.getByTestId(`customer-${customerId}`).click();
  await page.getByTestId("pin-input").fill("2468");
  await page.getByTestId("sign-in").click();
  await expect(page.getByTestId("hello")).toBeVisible();
  return page;
}
