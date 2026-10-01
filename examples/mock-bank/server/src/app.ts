import { randomBytes, timingSafeEqual } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import Fastify, { type FastifyInstance, type FastifyRequest } from "fastify";

import type { Config } from "./config.js";
import { RingSays, RingSaysApiError, type PurposeCode, type Slot } from "./ringsays.js";
import { pinMatches, Store, type BankIntent } from "./store.js";
import { parseEvent, verifySignature } from "./webhook.js";

const PRIORITIES = ["LOW", "NORMAL", "IMPORTANT", "URGENT"] as const;
const CHANNELS = ["SDK", "PRECALL_PUSH", "PSTN"] as const;
const OUTCOMES = [
  "RESOLVED",
  "FOLLOW_UP_REQUIRED",
  "NO_DECISION",
  "CALL_BACK",
  "DOCUMENT_REQUIRED",
  "TASK_CREATED",
  "MEETING_REQUIRED",
  "ESCALATED",
] as const;
const OPEN = new Set(["REQUESTED", "DELIVERED", "ACCEPTED", "RESCHEDULED", "SCHEDULED", "IN_PROGRESS", "FOLLOW_UP_REQUIRED"]);
/** Statuses the customer can still answer in the bank app (the SDK card). */
const ANSWERABLE = new Set(["REQUESTED", "DELIVERED", "RESCHEDULED", "SCHEDULED"]);

const APP_SESSION_MS = 12 * 3600_000;
const CONSOLE_SESSION_MS = 8 * 3600_000;
const LOGIN_WINDOW_MS = 15 * 60_000;
const LOGIN_MAX = 5;
/** One address may try several customers (a family on one Wi-Fi), but not spray them all. */
const LOGIN_MAX_PER_ADDRESS = 20;

export interface Deps {
  config: Config;
  store?: Store;
  ringsays?: RingSays;
  now?: () => number;
  fetch?: typeof fetch;
}

const consoleDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../console");
const CONSOLE_FILES: Record<string, [string, string]> = {
  "/console": ["index.html", "text/html; charset=utf-8"],
  "/console/console.js": ["console.js", "text/javascript; charset=utf-8"],
  "/console/console.css": ["console.css", "text/css; charset=utf-8"],
};
const SECURITY_HEADERS = {
  "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "no-referrer",
  "Cache-Control": "no-store",
};

class HttpError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

const token = () => randomBytes(32).toString("base64url");

function bodyOf<T extends object>(req: FastifyRequest): Partial<T> {
  const b = req.body;
  return b && typeof b === "object" && !Buffer.isBuffer(b) ? (b as Partial<T>) : {};
}

function sameSecret(a: string, b: string): boolean {
  const x = Buffer.from(a);
  const y = Buffer.from(b);
  return x.length === y.length && timingSafeEqual(x, y);
}

function cookie(req: FastifyRequest, name: string): string | undefined {
  for (const part of (req.headers.cookie ?? "").split(";")) {
    const [k, ...v] = part.trim().split("=");
    if (k === name) return v.join("=");
  }
  return undefined;
}

export function buildApp(deps: Deps): FastifyInstance {
  const { config } = deps;
  const now = deps.now ?? Date.now;
  const store = deps.store ?? new Store(config.dataFile);
  const rs =
    deps.ringsays ??
    new RingSays({ baseUrl: config.ringsaysApi, clientId: config.clientId, clientSecret: config.clientSecret, fetch: deps.fetch, now });

  // Logs never carry request bodies, headers or tokens.
  const app = Fastify({ logger: { level: process.env.LOG_LEVEL ?? "info", redact: ["req.headers.authorization", "req.headers.cookie"] }, bodyLimit: 64 * 1024 });

  const appSessions = new Map<string, { customerId: string; exp: number }>();
  const consoleSessions = new Map<string, { exp: number }>();
  const failures = new Map<string, number[]>();
  let catalogue: { at: number; codes: PurposeCode[] } | null = null;

  const codes = async (): Promise<PurposeCode[]> => {
    if (!catalogue || now() - catalogue.at > 10 * 60_000) catalogue = { at: now(), codes: await rs.purposeCodes() };
    return catalogue.codes;
  };

  // Housekeeping: expired sessions and old failure counters go away.
  const sweep = setInterval(() => {
    const t = now();
    for (const [k, v] of appSessions) if (v.exp < t) appSessions.delete(k);
    for (const [k, v] of consoleSessions) if (v.exp < t) consoleSessions.delete(k);
    for (const [k, v] of failures) if (!v.some((x) => t - x < LOGIN_WINDOW_MS)) failures.delete(k);
  }, 60_000);
  sweep.unref();
  app.addHook("onClose", async () => clearInterval(sweep));

  const throttled = (key: string): boolean => {
    const recent = (failures.get(key) ?? []).filter((t) => now() - t < LOGIN_WINDOW_MS);
    failures.set(key, recent);
    return recent.length >= (key.startsWith("ip:") ? LOGIN_MAX_PER_ADDRESS : LOGIN_MAX);
  };
  const failed = (key: string) => failures.set(key, [...(failures.get(key) ?? []), now()]);

  // Raw body for the webhook route only: the signature covers the exact bytes.
  app.addContentTypeParser("application/json", { parseAs: "buffer" }, (req, body, done) => {
    if (req.url === "/webhooks/ringsays") return done(null, body);
    try {
      done(null, (body as Buffer).length ? JSON.parse((body as Buffer).toString("utf8")) : {});
    } catch {
      done(new HttpError(400, "invalid JSON"), undefined);
    }
  });

  app.setErrorHandler((err, _req, reply) => {
    if (err instanceof HttpError) return reply.code(err.status).send({ error: err.message });
    if (err instanceof RingSaysApiError) {
      // Pass RingSays' explanation through to the agent (rule violations, wrong state).
      const status = err.status >= 500 ? 502 : err.status === 401 || err.status === 403 ? 502 : err.status;
      return reply.code(status).send({ error: err.message, code: err.problem?.code ?? null, ringsays_status: err.status });
    }
    const status = (err as { statusCode?: number }).statusCode ?? 500;
    if (status >= 500) app.log.error({ err: { name: (err as Error).name, message: (err as Error).message } }, "request failed");
    return reply.code(status).send({ error: status >= 500 ? "internal error" : (err as Error).message });
  });

  // CORS for the bank app web preview (native apps do not need it). Bearer tokens, no cookies.
  app.addHook("onRequest", async (req, reply) => {
    const origin = req.headers.origin;
    if (req.url.startsWith("/app/") && origin && config.appOrigins.includes(origin)) {
      reply.header("Access-Control-Allow-Origin", origin);
      reply.header("Vary", "Origin");
      reply.header("Access-Control-Allow-Headers", "Authorization, Content-Type");
      reply.header("Access-Control-Allow-Methods", "GET, POST");
      reply.header("Access-Control-Max-Age", "600");
    }
  });
  app.options("/app/*", async (_req, reply) => reply.code(204).send());

  app.get("/health", async () => ({ status: "ok", service: "mock-bank" }));

  // Webhooks from RingSays

  app.post("/webhooks/ringsays", async (req, reply) => {
    const raw = req.body as Buffer;
    const ok = Buffer.isBuffer(raw) && verifySignature(raw, req.headers["ringsays-signature"] as string | undefined, config.webhookSecret, Math.floor(now() / 1000));
    if (!ok) return reply.code(401).send({ error: "bad signature" });
    const e = parseEvent(raw);
    if (!e) return reply.code(400).send({ error: "bad payload" });
    const result = store.applyEvent(e);
    app.log.info({ event: e.type, result }, "webhook");
    // Not ours yet (our create call has not finished storing it): ask RingSays to retry later.
    if (result === "unknown_intent") return reply.code(503).send({ error: "unknown intent, retry" });
    // Webhook is the signal, the API is the truth: times and validity change on these events.
    if (result === "applied" && (e.type === "intent.scheduled" || e.type === "intent.rescheduled")) {
      void rs.getIntent(e.intent_id).then(
        (r) => store.applyIntent(e.intent_id, r),
        () => undefined,
      );
    }
    return reply.code(204).send();
  });

  // Bank app API (the bank's own customer sign in; RingSays is not involved here)

  const customerOf = (req: FastifyRequest): string => {
    const h = req.headers.authorization ?? "";
    const s = h.startsWith("Bearer ") ? appSessions.get(h.slice(7)) : undefined;
    if (!s || s.exp < now()) throw new HttpError(401, "sign in again");
    return s.customerId;
  };

  // DEMO ONLY: lets the sample app offer a customer picker. A bank never lists its customers.
  app.get("/app/customers", async () =>
    store.customers.map((c) => ({ id: c.id, name: c.name, phoneHint: `••• ${c.phone.slice(-4)}` })),
  );

  app.post("/app/login", async (req) => {
    const b = bodyOf<{ customerId?: unknown; pin?: unknown }>(req);
    const id = typeof b.customerId === "string" ? b.customerId : "";
    const pin = typeof b.pin === "string" ? b.pin : "";
    const c = store.customer(id);
    // Per customer (PIN guessing) and per address (spraying many customers); unknown ids share one key.
    const keys = [`app:${c ? c.id : "unknown"}`, `ip:${req.ip}`];
    if (keys.some(throttled)) throw new HttpError(429, "too many attempts, try later");
    if (!c || !/^\d{4}$/.test(pin) || !pinMatches(pin, c.pinHash)) {
      keys.forEach(failed);
      throw new HttpError(401, "customer or PIN not recognised");
    }
    const t = token();
    appSessions.set(t, { customerId: c.id, exp: now() + APP_SESSION_MS });
    return { token: t, customer: { id: c.id, name: c.name, language: c.language } };
  });

  app.post("/app/logout", async (req, reply) => {
    const h = req.headers.authorization ?? "";
    if (h.startsWith("Bearer ")) appSessions.delete(h.slice(7));
    return reply.code(204).send();
  });

  app.get("/app/messages", async (req) => {
    const customerId = customerOf(req);
    const list = await codes().catch(() => [] as PurposeCode[]);
    return store.forCustomer(customerId).map((i) => {
      const answerable = ANSWERABLE.has(i.status) && Date.parse(i.validUntil) > now() && i.contextToken !== null;
      return {
        intentId: i.intentId,
        createdAt: i.createdAt,
        status: i.status,
        title: list.find((c) => c.code === i.purposeCode)?.display_text ?? null,
        scheduledSlot: i.scheduledSlot,
        // The Context Token goes only to this customer's signed in app, only while it can be used.
        contextToken: answerable ? i.contextToken : null,
      };
    });
  });

  // Agent console

  const requireConsole = (req: FastifyRequest): void => {
    const s = consoleSessions.get(cookie(req, "mb_console") ?? "");
    if (!s || s.exp < now()) throw new HttpError(401, "sign in to the console");
    // CSRF: SameSite=Strict cookie plus a header a cross site form cannot send.
    if (req.method !== "GET" && req.headers["x-console"] !== "1") throw new HttpError(403, "missing console header");
  };

  for (const [route, [file, type]] of Object.entries(CONSOLE_FILES)) {
    app.get(route, async (_req, reply) => reply.headers({ ...SECURITY_HEADERS, "Content-Type": type }).send(readFileSync(path.join(consoleDir, file))));
  }

  app.post("/console/login", async (req, reply) => {
    const b = bodyOf<{ password?: unknown }>(req);
    const key = `console:${req.ip}`;
    if (throttled(key)) throw new HttpError(429, "too many attempts, try later");
    if (typeof b.password !== "string" || !sameSecret(b.password, config.consolePassword)) {
      failed(key);
      throw new HttpError(401, "wrong password");
    }
    const t = token();
    consoleSessions.set(t, { exp: now() + CONSOLE_SESSION_MS });
    // Secure whenever served over https (always, outside a developer's machine).
    const secure = req.protocol === "https" ? "; Secure" : "";
    reply.header("Set-Cookie", `mb_console=${t}; HttpOnly; SameSite=Strict; Path=/console; Max-Age=${CONSOLE_SESSION_MS / 1000}${secure}`);
    return { ok: true };
  });

  app.post("/console/logout", async (req, reply) => {
    consoleSessions.delete(cookie(req, "mb_console") ?? "");
    reply.header("Set-Cookie", "mb_console=; HttpOnly; SameSite=Strict; Path=/console; Max-Age=0");
    return reply.code(204).send();
  });

  const consoleView = (i: BankIntent) => {
    const c = store.customer(i.customerId);
    // Everything except the Context Token.
    const { contextToken: _hidden, ...rest } = i;
    return { ...rest, customer: c ? { id: c.id, name: c.name.en } : null, open: OPEN.has(i.status) };
  };

  app.get("/console/api/bootstrap", async (req) => {
    requireConsole(req);
    return {
      agentId: config.agentId,
      customers: store.customers.map((c) => ({ id: c.id, name: c.name.en, phoneHint: `••• ${c.phone.slice(-4)}`, language: c.language })),
      purposeCodes: await codes(),
      outcomes: OUTCOMES,
    };
  });

  app.get("/console/api/intents", async (req) => {
    requireConsole(req);
    return store.intents().map(consoleView);
  });

  // One RingSays call per request id at a time (double click, retry while the first is running).
  const inFlight = new Map<string, Promise<ReturnType<typeof consoleView>>>();

  app.post("/console/api/intents", async (req) => {
    requireConsole(req);
    const id = String(bodyOf<{ requestId: unknown }>(req).requestId ?? "");
    const running = inFlight.get(id);
    if (running) return running;
    const work = createFromConsole(req).finally(() => inFlight.delete(id));
    inFlight.set(id, work);
    return work;
  });

  async function createFromConsole(req: FastifyRequest) {
    const b = bodyOf<Record<string, unknown>>(req);
    // The console sends one request id per form; a repeated click or retry reuses it.
    const requestId = String(b.requestId ?? "");
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(requestId)) throw new HttpError(400, "requestId must be a UUID");
    const existing = store.byRequest(requestId);
    if (existing) return consoleView(existing);
    const c = store.customer(String(b.customerId ?? ""));
    if (!c) throw new HttpError(400, "unknown customer");
    const code = (await codes()).find((x) => x.code === b.purposeCode);
    if (!code) throw new HttpError(400, "purpose code not in the approved catalogue");
    const priority = String(b.priority ?? "NORMAL");
    if (!(PRIORITIES as readonly string[]).includes(priority)) throw new HttpError(400, "bad priority");
    const duration = Number(b.durationMin ?? 5);
    if (!Number.isInteger(duration) || duration < 1 || duration > code.max_duration_min) {
      throw new HttpError(400, `duration must be 1 to ${code.max_duration_min} minutes for this purpose`);
    }
    const ref = b.maskedReference ? String(b.maskedReference) : undefined;
    if (ref !== undefined && !/^[A-Za-z0-9]{3,4}$/.test(ref)) throw new HttpError(400, "reference: last 3 or 4 characters only");
    const channels = Array.isArray(b.channels) && b.channels.length ? b.channels.map(String) : ["SDK", "PRECALL_PUSH", "PSTN"];
    if (!channels.every((x) => (CHANNELS as readonly string[]).includes(x)) || channels[0] !== "SDK") {
      throw new HttpError(400, "channels: SDK first, then PRECALL_PUSH or PSTN");
    }
    const offered = Array.isArray(b.offeredSlots) ? (b.offeredSlots as Slot[]).slice(0, 3) : [];
    for (const s of offered) {
      if (!s || Number.isNaN(Date.parse(s.start)) || Number.isNaN(Date.parse(s.end))) throw new HttpError(400, "bad offered time");
    }
    const t0 = now();
    const created = await rs.createIntent({
      to: { phone: c.phone },
      agent_id: config.agentId,
      purpose_code: code.code,
      priority,
      expected_duration_min: duration,
      valid_from: new Date(t0 - 1000).toISOString(),
      valid_until: new Date(t0 + 2 * 3600_000).toISOString(),
      channel_preference: channels,
      language: c.language,
      ...(ref ? { masked_reference: ref } : {}),
      ...(offered.length ? { offered_slots: offered } : {}),
    }, requestId);
    const known = store.intent(created.intent_id);
    if (known) return consoleView(known); // replay of an earlier send that we already stored
    const record: BankIntent = {
      requestId,
      intentId: created.intent_id,
      customerId: c.id,
      purposeCode: code.code,
      priority,
      durationMin: duration,
      createdAt: new Date(t0).toISOString(),
      createdBy: config.agentId,
      status: created.status,
      channelUsed: null,
      scheduledSlot: null,
      proposedSlots: offered,
      declineReason: null,
      outcomeCode: null,
      validUntil: new Date(t0 + 2 * 3600_000).toISOString(),
      contextToken: created.context_token ?? null,
      events: [{ at: new Date(t0).toISOString(), type: "created", status: created.status, source: "api" }],
      lastEventAt: null,
    };
    store.add(record);
    return consoleView(record);
  }

  const withIntent = (req: FastifyRequest): BankIntent => {
    requireConsole(req);
    const i = store.intent((req.params as { id: string }).id);
    if (!i) throw new HttpError(404, "no such intent");
    return i;
  };

  app.post("/console/api/intents/:id/refresh", async (req) => {
    const i = withIntent(req);
    return consoleView(store.applyIntent(i.intentId, await rs.getIntent(i.intentId))!);
  });

  app.post("/console/api/intents/:id/cancel", async (req) => {
    const i = withIntent(req);
    return consoleView(store.applyIntent(i.intentId, await rs.cancel(i.intentId))!);
  });

  app.post("/console/api/intents/:id/calling", async (req) => {
    const i = withIntent(req);
    await rs.calling(i.intentId);
    return consoleView(store.applyIntent(i.intentId, await rs.getIntent(i.intentId))!);
  });

  app.post("/console/api/intents/:id/outcome", async (req) => {
    const i = withIntent(req);
    const code = String(bodyOf<{ code: unknown }>(req).code ?? "");
    if (!(OUTCOMES as readonly string[]).includes(code)) throw new HttpError(400, "bad outcome");
    const r = await rs.outcome(i.intentId, code);
    i.outcomeCode = code;
    return consoleView(store.applyIntent(i.intentId, r)!);
  });

  app.post("/console/api/intents/:id/schedule", async (req) => {
    const i = withIntent(req);
    const slot = bodyOf<{ slot: Slot }>(req).slot;
    if (!slot || !i.proposedSlots.some((s) => Date.parse(s.start) === Date.parse(slot.start))) {
      throw new HttpError(400, "pick one of the customer's proposed times");
    }
    const match = i.proposedSlots.find((s) => Date.parse(s.start) === Date.parse(slot.start))!;
    return consoleView(store.applyIntent(i.intentId, await rs.schedule(i.intentId, match))!);
  });

  app.addHook("onSend", async (req, reply, payload) => {
    if (req.url.startsWith("/console") || req.url.startsWith("/app/")) reply.header("Cache-Control", "no-store");
    return payload;
  });

  return app;
}
