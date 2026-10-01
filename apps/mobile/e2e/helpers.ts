import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";

import { expect, type Browser, type Page } from "@playwright/test";

export const API = process.env.E2E_API ?? "http://127.0.0.1:8000";
const backend = path.resolve(import.meta.dirname, "../../../backend");
const py = process.env.E2E_PYTHON ?? path.join(backend, ".venv/bin/python");

export interface Seed {
  tenant_id: string;
  client_id: string;
  client_secret: string;
  agent_id: string;
}

export const seed = (): Seed => JSON.parse(readFileSync(path.join(import.meta.dirname, ".seed.json"), "utf8")) as Seed;

let n = 0;
export function newPhone(): string {
  n += 1;
  return `+9665${String(Date.now() + n).slice(-8)}`;
}

/** Run the worker once: delivery ladder sends the intent to the app (MOCK push). */
export function deliver(): void {
  execFileSync(py, ["-m", "app.worker", "--once"], { cwd: backend, stdio: "ignore" });
}

export async function sendIntent(phone: string, opts: { priority?: string } = {}) {
  const s = seed();
  const tok = await fetch(`${API}/oauth/token`, {
    method: "POST",
    body: new URLSearchParams({ grant_type: "client_credentials", client_id: s.client_id, client_secret: s.client_secret }),
  }).then((r) => r.json() as Promise<{ access_token: string }>);
  const now = Date.now();
  const r = await fetch(`${API}/v1/intents`, {
    method: "POST",
    headers: { Authorization: `Bearer ${tok.access_token}`, "Content-Type": "application/json", "Idempotency-Key": randomUUID() },
    body: JSON.stringify({
      to: { phone },
      agent_id: s.agent_id,
      purpose_code: opts.priority === "URGENT" ? "CARD.TRANSACTION.VERIFY" : "MORTGAGE.DOC.CLARIFY",
      masked_reference: "8291",
      priority: opts.priority ?? "NORMAL",
      expected_duration_min: opts.priority === "URGENT" ? 3 : 5,
      valid_from: new Date(now - 1000).toISOString(),
      valid_until: new Date(now + 4 * 3_600_000).toISOString(),
      channel_preference: ["PRECALL_PUSH", "PSTN"],
      language: "ar",
    }),
  });
  if (r.status !== 201) throw new Error(`intent ${r.status} ${await r.text()}`);
  return ((await r.json()) as { intent_id: string }).intent_id;
}

/** Sign in through the app's own screens with the MOCK SMS code. */
export async function signIn(browser: Browser, phone: string, lang: "ar" | "en" = "ar"): Promise<Page> {
  const ctx = await browser.newContext({ locale: lang === "ar" ? "ar-SA" : "en-GB" });
  const page = await ctx.newPage();
  await page.goto("/");
  await page.getByTestId("phone-input").fill(phone.slice(4));
  await page.getByTestId("send-code").click();
  await expect(page.getByTestId("code-input")).toBeVisible();
  const { code } = (await (await fetch(`${API}/dev/sms/last-code?phone=${encodeURIComponent(phone)}`)).json()) as { code: string };
  await page.getByTestId("code-input").fill(code);
  await page.getByTestId("verify").click();
  await expect(page).toHaveURL(/\/inbox$/);
  return page;
}
