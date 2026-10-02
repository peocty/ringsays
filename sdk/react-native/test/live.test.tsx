/// <reference types="node" />
/**
 * Against a running local API (MOCK adapters). Skipped when unreachable. Real Context Token from the
 * enterprise API, rendered and answered through IntentCard, then refused on a second install.
 */
import { execFileSync } from "node:child_process";
import http from "node:http";
import { randomBytes, randomUUID } from "node:crypto";
import path from "node:path";

import { fireEvent, render, screen } from "@testing-library/react-native";

import { IntentCard, RingSaysProvider, type KeyValueStore } from "../src";

const API = process.env.RINGSAYS_API ?? "http://127.0.0.1:8000";
const BACKEND = path.resolve(__dirname, "../../../backend");
const rng = (n: number) => new Uint8Array(randomBytes(n));
/**
 * React Native's test setup replaces fetch and the web stream globals, and undici's streamed bodies
 * then sometimes never finish there. This client buffers the whole response with node:http and returns
 * an ordinary Response, which is all the SDK needs (local API only).
 */
const nodeFetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  const req = typeof input === "object" && "url" in input ? (input as Request) : null;
  const url = new URL(req ? req.url : String(input));
  const method = req?.method ?? init?.method ?? "GET";
  const headers: Record<string, string> = {};
  new Headers(req ? req.headers : (init?.headers as HeadersInit | undefined)).forEach((v, k) => (headers[k] = v));
  const raw = req ? (method === "GET" || method === "HEAD" ? undefined : await req.text()) : init?.body;
  if (raw instanceof URLSearchParams && !headers["content-type"]) headers["content-type"] = "application/x-www-form-urlencoded";
  const body = raw === undefined || raw === null ? undefined : String(raw);
  return new Promise<Response>((resolve, reject) => {
    const r = http.request(
      { host: url.hostname, port: url.port, path: url.pathname + url.search, method, headers, timeout: 15000 },
      (res) => {
        const chunks: Buffer[] = [];
        res.on("data", (c: Buffer) => chunks.push(c));
        res.on("end", () => {
          const h = new Headers();
          for (const [k, v] of Object.entries(res.headers)) if (typeof v === "string") h.set(k, v);
          const status = res.statusCode ?? 0;
          resolve(new Response(status === 204 ? null : Buffer.concat(chunks).toString("utf8"), { status, headers: h }));
        });
        res.on("error", reject);
      },
    );
    r.on("timeout", () => r.destroy(new Error(`timeout ${method} ${url.pathname}`)));
    r.on("error", reject);
    if (body) r.write(body);
    r.end();
  });
}) as unknown as typeof fetch;

function store(): KeyValueStore {
  const m = new Map<string, string>();
  return { getItem: async (k) => m.get(k) ?? null, setItem: async (k, v) => void m.set(k, v) };
}

let up = false;
let token = "";
// Seeding starts Python and writes a tenant: slow when the whole workspace tests in parallel, so it
// has its own time budget and the test's budget covers only the SDK behaviour.
beforeAll(async () => {
  up = await nodeFetch(`${API}/health`).then((r) => r.ok).catch(() => false);
  if (up) token = await contextToken();
}, 90_000);

async function contextToken(): Promise<string> {
  const t = JSON.parse(
    execFileSync(path.join(BACKEND, ".venv/bin/python"), ["-m", "app.scripts.seed", "--tag", `sdk${Date.now().toString(36)}`, "--json"], {
      cwd: BACKEND,
    }).toString(),
  ) as { client_id: string; client_secret: string; agent_id: string };
  const tok = (await (
    await nodeFetch(`${API}/oauth/token`, {
      method: "POST",
      body: new URLSearchParams({ grant_type: "client_credentials", client_id: t.client_id, client_secret: t.client_secret }),
    })
  ).json()) as { access_token: string };
  const now = Date.now();
  const r = await nodeFetch(`${API}/v1/intents`, {
    method: "POST",
    headers: { Authorization: `Bearer ${tok.access_token}`, "Content-Type": "application/json", "Idempotency-Key": randomUUID() },
    body: JSON.stringify({
      to: { phone: `+9665${String(now).slice(-8)}` },
      agent_id: t.agent_id,
      purpose_code: "MORTGAGE.DOC.CLARIFY",
      masked_reference: "8291",
      priority: "NORMAL",
      expected_duration_min: 5,
      valid_from: new Date(now - 1000).toISOString(),
      valid_until: new Date(now + 3_600_000).toISOString(),
      channel_preference: ["SDK"],
      language: "ar",
    }),
  });
  if (r.status !== 201) throw new Error(`intent ${r.status} ${await r.text()}`);
  const body = (await r.json()) as { context_token?: string | null };
  if (!body.context_token) throw new Error("no context_token");
  return body.context_token;
}

const card = (token: string, s: KeyValueStore) => (
  <RingSaysProvider baseUrl={API} storage={s} language="ar" fetch={nodeFetch} random={rng}>
    <IntentCard token={token} />
  </RingSaysProvider>
);

test("real Context Token: verified card in Arabic, answer, second install refused", async () => {
  if (!up) return console.warn("live SDK test skipped: API not reachable");
  const first = render(card(token, store()));
  expect(await screen.findByTestId("ringsays-verified", {}, { timeout: 10_000 })).toBeTruthy();
  expect(screen.getByText(/8291/)).toBeTruthy();
  fireEvent.press(screen.getByTestId("ringsays-action-TALK_NOW"));
  expect(await screen.findByTestId("ringsays-outcome", {}, { timeout: 10_000 })).toBeTruthy();
  first.unmount();

  render(card(token, store()));
  expect(await screen.findByTestId("ringsays-error", {}, { timeout: 10_000 })).toHaveTextContent("هذا الطلب معروض على جهاز آخر.");
}, 30_000);
