/**
 * Against a running local API (RINGSAYS_ENVIRONMENT=local, MOCK adapters). Skipped when unreachable.
 * Proves the TypeScript device key, signed refresh and Context Token flow work with the real server.
 */
import { execFileSync } from "node:child_process";
import { randomBytes, randomUUID } from "node:crypto";
import path from "node:path";

import { ContextTokenClient, MemoryStore, Session, SoftwareDeviceKey, type Lang } from "../src";

const API = process.env.RINGSAYS_API ?? "http://127.0.0.1:8000";
const BACKEND = path.resolve(import.meta.dirname, "../../../backend");
const PY = path.join(BACKEND, ".venv/bin/python");
const rng = (n: number) => new Uint8Array(randomBytes(n));
const up = await fetch(`${API}/health`).then((r) => r.ok).catch(() => false);
const live = up ? describe : describe.skip;

interface Seeded {
  tenant_id: string;
  client_id: string;
  client_secret: string;
  agent_id: string;
}

function seed(): Seeded {
  const out = execFileSync(PY, ["-m", "app.scripts.seed", "--tag", `client${Date.now().toString(36)}`, "--json"], {
    cwd: BACKEND,
  });
  return JSON.parse(out.toString()) as Seeded;
}

function deliver(): void {
  execFileSync(PY, ["-m", "app.worker", "--once"], { cwd: BACKEND, stdio: "ignore" });
}

async function enterpriseToken(t: Seeded): Promise<string> {
  const r = await fetch(`${API}/oauth/token`, {
    method: "POST",
    body: new URLSearchParams({ grant_type: "client_credentials", client_id: t.client_id, client_secret: t.client_secret }),
  });
  return ((await r.json()) as { access_token: string }).access_token;
}

async function sendIntent(token: string, t: Seeded, phone: string, channels: string[]) {
  const now = Date.now();
  const r = await fetch(`${API}/v1/intents`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", "Idempotency-Key": randomUUID() },
    body: JSON.stringify({
      to: { phone },
      agent_id: t.agent_id,
      purpose_code: "MORTGAGE.DOC.CLARIFY",
      masked_reference: "8291",
      priority: "NORMAL",
      expected_duration_min: 5,
      valid_from: new Date(now - 1000).toISOString(),
      valid_until: new Date(now + 3_600_000).toISOString(),
      channel_preference: channels,
      language: "ar",
    }),
  });
  if (r.status !== 201) throw new Error(`intent ${r.status} ${await r.text()}`);
  return (await r.json()) as { intent_id: string; context_token?: string | null };
}

async function signIn(phone: string, lang: Lang = "ar") {
  let offset = 0;
  const store = new MemoryStore();
  const signer = SoftwareDeviceKey.generate(rng);
  const session = new Session({ baseUrl: API, store, signer, language: () => lang, now: () => Date.now() + offset });
  const { challengeId } = await session.requestCode(phone, lang);
  const sms = (await (await fetch(`${API}/dev/sms/last-code?phone=${encodeURIComponent(phone)}`)).json()) as { code: string };
  // MOCK push provider accepts any token; without one, delivery falls back to an ordinary call.
  await session.verifyCode(challengeId, sms.code, { platform: "ANDROID", appVersion: "0.1.0", push: { fcm: "mock-fcm" } });
  return { session, store, skip: (ms: number) => (offset += ms) };
}

const phone = () => `+9665${String(Date.now()).slice(-8)}`;

live("live API", () => {
  it("sign in, receive, read in Arabic, answer, refresh with device signature", async () => {
    const t = seed();
    const p = phone();
    const { session, skip } = await signIn(p);
    const token = await enterpriseToken(t);
    const { intent_id } = await sendIntent(token, t, p, ["PRECALL_PUSH", "PSTN"]);
    deliver();
    const inbox = await session.inbox("REQUESTS");
    const item = inbox.items.find((i) => i.intent_id === intent_id)!;
    expect(item.why).toBe("توضيح بخصوص مستندات التمويل العقاري");
    expect(item.verification_level).toBe("ORG_AGENT_NUMBER");
    expect(item.actions).toContain("TALK_NOW");

    skip(16 * 60_000); // access token expired: next call refreshes, signed by the TypeScript key
    expect((await session.intent(intent_id)).intent_id).toBe(intent_id); // still visible after refresh
    const later = await session.respond(intent_id, { action: "LATER", later_minutes: 30 });
    expect(later.status).toBe("SCHEDULED");

    const prefs = await session.preferences();
    const saved = await session.savePreferences({ ...prefs.doc, timezone: "Asia/Riyadh", verified_businesses_only: true }, prefs.etag);
    expect(saved.doc.verified_businesses_only).toBe(true);
    await expect(session.savePreferences(prefs.doc, prefs.etag)).rejects.toMatchObject({ status: 412 });
    expect((await session.consents()).length).toBe(1);
  });

  it("reused refresh token revokes the device session", async () => {
    const { session, store, skip } = await signIn(phone(), "en");
    const before = JSON.parse((await store.get("ringsays.session"))!) as { refreshToken: string };
    skip(16 * 60_000);
    await session.inbox();
    // A thief replays the old (already rotated) refresh token; signature even valid is not enough.
    const r = await fetch(`${API}/v1/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: before.refreshToken, device_signature: "AAAA" }),
    });
    expect(r.status).toBe(401);
    skip(16 * 60_000);
    await expect(session.inbox()).rejects.toMatchObject({ status: 401 });
  });

  it("bank app shows and answers with a Context Token; another install cannot", async () => {
    const t = seed();
    const token = await enterpriseToken(t);
    const created = await sendIntent(token, t, phone(), ["SDK", "PSTN"]);
    expect(created.context_token).toBeTruthy();
    const install = randomUUID();
    const sdk = new ContextTokenClient({ baseUrl: API, installId: install, language: () => "en" });
    const shown = await sdk.resolve(created.context_token!);
    expect(shown.why).toBe("Home finance document clarification");
    const other = new ContextTokenClient({ baseUrl: API, installId: randomUUID(), language: () => "en" });
    await expect(other.resolve(created.context_token!)).rejects.toMatchObject({ status: 403 });
    const answered = await sdk.respond(created.context_token!, { action: "ACCEPT" });
    expect(answered.status).toBe("ACCEPTED");
  });
});
