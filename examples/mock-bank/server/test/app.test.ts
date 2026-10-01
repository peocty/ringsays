import type { FastifyInstance } from "fastify";

import { buildApp } from "../src/app.js";
import type { Config } from "../src/config.js";
import { RingSays } from "../src/ringsays.js";
import { Store } from "../src/store.js";
import { sign } from "../src/webhook.js";

const config: Config = {
  port: 0,
  host: "127.0.0.1",
  ringsaysApi: "https://rs.test",
  clientId: "cli_x",
  clientSecret: "secret_x",
  agentId: "agt_demo_01",
  webhookSecret: "whsec_t",
  consolePassword: "console-pw",
  appOrigins: ["http://127.0.0.1:8082"],
  dataFile: null,
};

const CODES = [
  { code: "MORTGAGE.DOC.CLARIFY", display_text: { en: "Mortgage", ar: "تمويل" }, max_priority: "IMPORTANT", max_duration_min: 10, allowed_channels: ["SDK", "PRECALL_PUSH", "PSTN"], status: "APPROVED" },
];

function fakeRingSays() {
  const calls: { method: string; url: string; body: unknown; headers: Headers }[] = [];
  let n = 0;
  const f = (async (url: string | URL, init?: RequestInit) => {
    const u = String(url);
    const headers = new Headers(init?.headers);
    calls.push({ method: init?.method ?? "GET", url: u, body: init?.body ? (typeof init.body === "string" ? JSON.parse(init.body) : String(init.body)) : null, headers });
    const json = (status: number, b: unknown) => new Response(JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } });
    if (u.endsWith("/oauth/token")) return json(200, { access_token: "at", expires_in: 900 });
    if (u.endsWith("/v1/purpose-codes")) return json(200, { items: CODES });
    if (u.endsWith("/v1/intents") && init?.method === "POST") {
      n += 1;
      return json(201, { intent_id: `int-${n}`, status: "REQUESTED", verification_level: "ORG_AGENT_NUMBER", context_token: `ctx-secret-${n}` });
    }
    if (/\/v1\/intents\/[^/]+\/schedule$/.test(u)) {
      const b = JSON.parse(String(init?.body));
      return json(200, { status: "SCHEDULED", channel_used: "SDK", scheduled_slot: b.slot, proposed_slots: [], valid_until: "2030-01-01T00:00:00Z", updated_at: new Date().toISOString() });
    }
    return json(404, { title: "not found" });
  }) as typeof fetch;
  return { f, calls };
}

let app: FastifyInstance;
let rs: ReturnType<typeof fakeRingSays>;
let store: Store;

beforeEach(async () => {
  rs = fakeRingSays();
  store = new Store(null);
  app = buildApp({ config, store, ringsays: new RingSays({ baseUrl: config.ringsaysApi, clientId: "cli_x", clientSecret: "secret_x", fetch: rs.f }) });
  await app.ready();
});
afterEach(() => app.close());

async function consoleCookie(): Promise<string> {
  const r = await app.inject({ method: "POST", url: "/console/login", payload: { password: "console-pw" } });
  expect(r.statusCode).toBe(200);
  return String(r.headers["set-cookie"]).split(";")[0]!;
}

async function createIntent(cookie: string, extra: Record<string, unknown> = {}) {
  const r = await app.inject({
    method: "POST",
    url: "/console/api/intents",
    headers: { cookie, "x-console": "1" },
    payload: { customerId: "cus_noura", purposeCode: "MORTGAGE.DOC.CLARIFY", priority: "NORMAL", durationMin: 5, maskedReference: "8291", ...extra },
  });
  return r;
}

async function appToken(customerId = "cus_noura", pin = "2468") {
  const r = await app.inject({ method: "POST", url: "/app/login", payload: { customerId, pin } });
  return r;
}

test("console needs password, cookie and the console header", async () => {
  expect((await app.inject({ method: "GET", url: "/console/api/intents" })).statusCode).toBe(401);
  expect((await app.inject({ method: "POST", url: "/console/login", payload: { password: "nope" } })).statusCode).toBe(401);
  const cookie = await consoleCookie();
  expect(String((await app.inject({ method: "POST", url: "/console/login", payload: { password: "console-pw" } })).headers["set-cookie"])).toMatch(/HttpOnly; SameSite=Strict/);
  const noHeader = await app.inject({ method: "POST", url: "/console/api/intents", headers: { cookie }, payload: {} });
  expect(noHeader.statusCode).toBe(403);
});

test("create intent: sends the bank's request to RingSays; console never sees the Context Token", async () => {
  const cookie = await consoleCookie();
  const r = await createIntent(cookie, { offeredSlots: [{ start: "2030-01-01T09:00:00Z", end: "2030-01-01T09:05:00Z" }] });
  expect(r.statusCode).toBe(200);
  expect(r.body).not.toContain("ctx-secret");
  const post = rs.calls.find((c) => c.url.endsWith("/v1/intents"))!;
  expect(post.headers.get("Idempotency-Key")).toMatch(/^[0-9a-f-]{36}$/);
  expect(post.headers.get("Authorization")).toBe("Bearer at");
  expect(post.body).toMatchObject({
    to: { phone: "+966500001001" },
    agent_id: "agt_demo_01",
    purpose_code: "MORTGAGE.DOC.CLARIFY",
    channel_preference: ["SDK", "PRECALL_PUSH", "PSTN"],
    language: "ar",
    masked_reference: "8291",
    offered_slots: [{ start: "2030-01-01T09:00:00Z", end: "2030-01-01T09:05:00Z" }],
  });
  const list = await app.inject({ method: "GET", url: "/console/api/intents", headers: { cookie } });
  expect(list.body).not.toContain("ctx-secret");
});

test("console input checked before calling RingSays", async () => {
  const cookie = await consoleCookie();
  expect((await createIntent(cookie, { durationMin: 60 })).statusCode).toBe(400);
  expect((await createIntent(cookie, { maskedReference: "1234567890" })).statusCode).toBe(400);
  expect((await createIntent(cookie, { purposeCode: "NOT.APPROVED" })).statusCode).toBe(400);
  expect((await createIntent(cookie, { channels: ["PSTN"] })).statusCode).toBe(400);
  expect(rs.calls.filter((c) => c.url.endsWith("/v1/intents"))).toHaveLength(0);
});

test("bank app: own sign in; Context Token only to that customer, only while answerable", async () => {
  const cookie = await consoleCookie();
  await createIntent(cookie);
  expect((await appToken("cus_noura", "0000")).statusCode).toBe(401);
  const noura = (await appToken()).json().token as string;
  const faisal = (await appToken("cus_faisal")).json().token as string;
  const mine = (await app.inject({ method: "GET", url: "/app/messages", headers: { authorization: `Bearer ${noura}` } })).json();
  expect(mine).toHaveLength(1);
  expect(mine[0].contextToken).toBe("ctx-secret-1");
  expect(mine[0].title.ar).toBe("تمويل");
  const theirs = (await app.inject({ method: "GET", url: "/app/messages", headers: { authorization: `Bearer ${faisal}` } })).json();
  expect(theirs).toHaveLength(0);
  expect((await app.inject({ method: "GET", url: "/app/messages" })).statusCode).toBe(401);

  // Declined (webhook): no token any more.
  const id = mine[0].intentId as string;
  const body = JSON.stringify({ event_id: "e-1", type: "intent.declined", occurred_at: new Date().toISOString(), intent_id: id, status: "DECLINED", channel_used: "SDK", decline_reason: "NOT_NOW" });
  const hook = await app.inject({ method: "POST", url: "/webhooks/ringsays", headers: { "content-type": "application/json", "ringsays-signature": sign(body, "whsec_t", Math.floor(Date.now() / 1000)) }, payload: body });
  expect(hook.statusCode).toBe(204);
  const after = (await app.inject({ method: "GET", url: "/app/messages", headers: { authorization: `Bearer ${noura}` } })).json();
  expect(after[0].status).toBe("DECLINED");
  expect(after[0].contextToken).toBeNull();
});

test("PIN guessing is throttled per customer", async () => {
  for (let i = 0; i < 5; i++) expect((await appToken("cus_priya", "1111")).statusCode).toBe(401);
  expect((await appToken("cus_priya", "2468")).statusCode).toBe(429);
  expect((await appToken("cus_noura")).statusCode).toBe(200);
});

test("webhooks: bad signature 401; duplicates and older events ignored", async () => {
  const cookie = await consoleCookie();
  const id = (await createIntent(cookie)).json().intentId as string;
  const now = Date.now();
  const ev = (eventId: string, type: string, status: string, at: number, extra: Record<string, unknown> = {}) =>
    JSON.stringify({ event_id: eventId, type, occurred_at: new Date(at).toISOString(), intent_id: id, status, channel_used: "SDK", ...extra });
  const send = (body: string, sig = sign(body, "whsec_t", Math.floor(now / 1000))) =>
    app.inject({ method: "POST", url: "/webhooks/ringsays", headers: { "content-type": "application/json", "ringsays-signature": sig }, payload: body });

  expect((await send(ev("a", "intent.accepted", "ACCEPTED", now), "t=1,v1=" + "0".repeat(64))).statusCode).toBe(401);
  const proposed = [{ start: "2030-01-01T09:00:00Z", end: "2030-01-01T09:05:00Z" }];
  expect((await send(ev("b", "intent.rescheduled", "RESCHEDULED", now, { proposed_slots: proposed }))).statusCode).toBe(204);
  expect((await send(ev("b", "intent.rescheduled", "RESCHEDULED", now, { proposed_slots: proposed }))).statusCode).toBe(204);
  expect((await send(ev("c", "intent.delivered", "DELIVERED", now - 60_000))).statusCode).toBe(204);
  const i = store.intent(id)!;
  expect(i.status).toBe("RESCHEDULED");
  expect(i.events.filter((e) => e.source === "webhook")).toHaveLength(2);

  // Agent confirms one of the customer's times.
  const r = await app.inject({ method: "POST", url: `/console/api/intents/${id}/schedule`, headers: { cookie, "x-console": "1" }, payload: { slot: proposed[0] } });
  expect(r.statusCode).toBe(200);
  expect(r.json().status).toBe("SCHEDULED");
  const bad = await app.inject({ method: "POST", url: `/console/api/intents/${id}/schedule`, headers: { cookie, "x-console": "1" }, payload: { slot: { start: "2031-01-01T00:00:00Z", end: "2031-01-01T00:05:00Z" } } });
  expect(bad.statusCode).toBe(400);
});

test("CORS only for the bank app origin; console has strict CSP", async () => {
  const pre = await app.inject({ method: "OPTIONS", url: "/app/messages", headers: { origin: "http://127.0.0.1:8082", "access-control-request-method": "GET" } });
  expect(pre.statusCode).toBe(204);
  expect(pre.headers["access-control-allow-origin"]).toBe("http://127.0.0.1:8082");
  const evil = await app.inject({ method: "GET", url: "/app/customers", headers: { origin: "https://evil.example" } });
  expect(evil.headers["access-control-allow-origin"]).toBeUndefined();
  const page = await app.inject({ method: "GET", url: "/console" });
  expect(page.headers["content-security-policy"]).toContain("default-src 'self'");
  expect(page.body).not.toMatch(/<script>(?!<)/);
});
