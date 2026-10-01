import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";

import { expect, type Browser, type Page } from "@playwright/test";

export const API = process.env.E2E_API ?? "http://127.0.0.1:8000";

export interface Seed {
  tenant_id: string;
  client_id: string;
  client_secret: string;
  agent_id: string;
  tag: string;
  people: Record<"TENANT_ADMIN" | "INTEGRATION_ADMIN" | "SUPERVISOR" | "AGENT" | "COMPLIANCE" | "RS_REVIEWER" | "RS_ADMIN", string>;
}

export function seed(): Seed {
  return JSON.parse(readFileSync(path.join(import.meta.dirname, ".seed.json"), "utf8")) as Seed;
}

/** Sign in through the MOCK identity provider by typing the email, in a fresh browser context. */
export async function signIn(browser: Browser, email: string, lang: "ar" | "en" = "ar"): Promise<Page> {
  const context = await browser.newContext();
  // Starting language only; a choice made in the page later is kept across reloads.
  await context.addInitScript((l) => {
    if (!window.localStorage.getItem("ringsays.portal.lang")) window.localStorage.setItem("ringsays.portal.lang", l);
  }, lang);
  const page = await context.newPage();
  await page.goto("/");
  await page.getByRole("button", { name: lang === "ar" ? "تسجيل الدخول" : "Sign in", exact: true }).click();
  await page.waitForURL(/\/dev\/oidc\/authorize/);
  await page.locator('input[name="other_email"]').fill(email);
  await page.getByRole("button", { name: "Sign in with this email" }).click();
  await page.waitForURL((u) => !u.pathname.startsWith("/auth/"));
  return page;
}

export async function openTenant(page: Page, tenantId: string): Promise<void> {
  await page.goto(`/t/${tenantId}/overview`);
  await expect(page.locator("main h1")).toBeVisible();
}

/** Smallest valid PDF the API accepts (content is sniffed, not the extension). */
export function pdf(name: string): { name: string; mimeType: string; buffer: Buffer } {
  return { name, mimeType: "application/pdf", buffer: Buffer.from(`%PDF-1.4\n% ${name}\n%%EOF\n`) };
}

export async function enterpriseToken(clientId: string, clientSecret: string): Promise<string> {
  const r = await fetch(`${API}/oauth/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ grant_type: "client_credentials", client_id: clientId, client_secret: clientSecret }),
  });
  if (!r.ok) throw new Error(`token ${r.status}`);
  return ((await r.json()) as { access_token: string }).access_token;
}

export async function sendIntent(token: string, agentId: string, phone: string): Promise<string> {
  const now = Date.now();
  const r = await fetch(`${API}/v1/intents`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID() },
    body: JSON.stringify({
      to: { phone },
      agent_id: agentId,
      purpose_code: "MORTGAGE.DOC.CLARIFY",
      masked_reference: "8291",
      priority: "NORMAL",
      expected_duration_min: 5,
      valid_from: new Date(now + 60_000).toISOString(),
      valid_until: new Date(now + 3_600_000).toISOString(),
      channel_preference: ["PRECALL_PUSH", "PSTN"],
      language: "ar",
    }),
  });
  if (r.status !== 201) throw new Error(`intent ${r.status} ${await r.text()}`);
  return ((await r.json()) as { intent_id: string }).intent_id;
}

/** A second, separate MOCK tenant for tests that change organisation wide state (suspension). */
export function seedFresh(tag: string): Seed {
  const backend = path.resolve(import.meta.dirname, "../../../backend");
  const python = process.env.E2E_PYTHON ?? path.join(backend, ".venv/bin/python");
  const out = execFileSync(python, ["-m", "app.scripts.seed", "--tag", tag, "--json"], { cwd: backend }).toString();
  return { ...(JSON.parse(out) as Omit<Seed, "tag">), tag };
}
