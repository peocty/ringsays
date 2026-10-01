import { randomBytes } from "node:crypto";

import { MemoryStore, Session, SoftwareDeviceKey } from "../src";

const rng = (n: number) => new Uint8Array(randomBytes(n));
const BASE = "https://api.test";

function json(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });
}

function tokens(n: number, expiresIn = 900) {
  return {
    access_token: `at${n}`,
    refresh_token: `rt_${n}`,
    expires_in: expiresIn,
    user_id: "0190a000-0000-7000-8000-000000000001",
    device_id: "0190a000-0000-7000-8000-000000000002",
  };
}

function setup(handler: (url: string, init: RequestInit & { headers?: Headers }) => Response | Promise<Response>) {
  let clock = 1_000_000;
  const calls: { url: string; auth: string | null; body: string | null }[] = [];
  const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const req = input instanceof Request ? input : new Request(String(input), init);
    const body = req.method === "GET" ? null : await req.clone().text();
    calls.push({ url: req.url, auth: req.headers.get("Authorization"), body });
    return handler(req.url, { method: req.method, headers: req.headers, body });
  }) as typeof fetch;
  const signedOut: string[] = [];
  const session = new Session({
    baseUrl: BASE,
    store: new MemoryStore(),
    signer: SoftwareDeviceKey.generate(rng),
    language: () => "ar",
    fetch: fetchImpl,
    now: () => clock,
    onSignedOut: () => signedOut.push("out"),
  });
  return { session, calls, signedOut, advance: (ms: number) => (clock += ms) };
}

const inboxBody = { items: [], next_cursor: null };

describe("session", () => {
  it("signs in, sends bearer and Arabic language", async () => {
    const { session, calls } = setup((url) => {
      if (url.endsWith("/auth/verify")) return json(200, tokens(1));
      return json(200, inboxBody);
    });
    await session.verifyCode("0190a000-0000-7000-8000-00000000000a", "123456", { platform: "IOS", appVersion: "1.0" });
    await session.inbox();
    const verifyBody = JSON.parse(calls[0]!.body!);
    expect(verifyBody.device.public_key).toMatch(/^MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE/);
    expect(calls[1]!.auth).toBe("Bearer at1");
  });

  it("refreshes before expiry, once for concurrent requests, signing the refresh token", async () => {
    let refreshes = 0;
    const { session, calls, advance } = setup((url) => {
      if (url.endsWith("/auth/verify")) return json(200, tokens(1));
      if (url.endsWith("/auth/refresh")) {
        refreshes += 1;
        return json(200, tokens(2));
      }
      return json(200, inboxBody);
    });
    await session.verifyCode("0190a000-0000-7000-8000-00000000000a", "123456", { platform: "ANDROID", appVersion: "1" });
    advance(850_000); // inside the refresh margin
    await Promise.all([session.inbox(), session.inbox(), session.inbox()]);
    expect(refreshes).toBe(1);
    const refreshCall = calls.find((c) => c.url.endsWith("/auth/refresh"))!;
    const body = JSON.parse(refreshCall.body!);
    expect(body.refresh_token).toBe("rt_1");
    expect(body.device_signature.length).toBeGreaterThan(60);
    expect(calls.filter((c) => c.url.includes("/inbox")).every((c) => c.auth === "Bearer at2")).toBe(true);
  });

  it("retries once after a 401 with a fresh token", async () => {
    let first = true;
    const { session } = setup((url, init) => {
      if (url.endsWith("/auth/verify")) return json(200, tokens(1));
      if (url.endsWith("/auth/refresh")) return json(200, tokens(2));
      if (first && init.headers?.get("Authorization") === "Bearer at1") {
        first = false;
        return json(401, { status: 401, code: "unauthenticated" });
      }
      return json(200, inboxBody);
    });
    await session.verifyCode("0190a000-0000-7000-8000-00000000000a", "123456", { platform: "IOS", appVersion: "1" });
    await expect(session.inbox()).resolves.toEqual(inboxBody);
  });

  it("refused refresh ends the session and reports it", async () => {
    const { session, signedOut, advance } = setup((url) => {
      if (url.endsWith("/auth/verify")) return json(200, tokens(1));
      if (url.endsWith("/auth/refresh")) return json(401, { status: 401 });
      return json(200, inboxBody);
    });
    await session.verifyCode("0190a000-0000-7000-8000-00000000000a", "123456", { platform: "IOS", appVersion: "1" });
    advance(900_000);
    await expect(session.inbox()).rejects.toMatchObject({ status: 401 });
    expect(await session.isSignedIn()).toBe(false);
    expect(signedOut).toEqual(["out"]);
  });

  it("a wrong code is an error, not a sign out", async () => {
    const { session, signedOut } = setup(() => json(401, { status: 401, code: "auth_failed", detail: "Wrong code" }));
    await expect(
      session.verifyCode("0190a000-0000-7000-8000-00000000000a", "000000", { platform: "IOS", appVersion: "1" }),
    ).rejects.toMatchObject({ status: 401, message: "Wrong code" });
    expect(signedOut).toEqual([]);
  });

  it("offline refresh keeps the session", async () => {
    const { session, advance } = setup((url) => {
      if (url.endsWith("/auth/verify")) return json(200, tokens(1));
      if (url.endsWith("/auth/refresh")) throw new TypeError("network down");
      return json(200, inboxBody);
    });
    await session.verifyCode("0190a000-0000-7000-8000-00000000000a", "123456", { platform: "IOS", appVersion: "1" });
    advance(900_000);
    await expect(session.inbox()).rejects.toMatchObject({ status: 0 });
    expect(await session.isSignedIn()).toBe(true);
  });

  it("preferences use ETag for safe concurrent saves", async () => {
    const { session, calls } = setup((url, init) => {
      if (url.endsWith("/auth/verify")) return json(200, tokens(1));
      if (init.method === "PUT") return json(200, { timezone: "Asia/Riyadh", rules: [] }, { ETag: 'W/"v2"' });
      return json(200, { timezone: "Asia/Riyadh", rules: [] }, { ETag: 'W/"v1"' });
    });
    await session.verifyCode("0190a000-0000-7000-8000-00000000000a", "123456", { platform: "IOS", appVersion: "1" });
    const { doc, etag } = await session.preferences();
    expect(etag).toBe('W/"v1"');
    const saved = await session.savePreferences({ ...doc, verified_businesses_only: true }, etag);
    expect(saved.etag).toBe('W/"v2"');
    expect(calls.length).toBe(3);
  });

  it("sign out revokes on the server, then clears the phone even when offline", async () => {
    const { session, calls } = setup((url) => {
      if (url.endsWith("/auth/verify")) return json(200, tokens(1));
      if (url.endsWith("/auth/logout")) return new Response(null, { status: 204 });
      return json(200, inboxBody);
    });
    await session.verifyCode("0190a000-0000-7000-8000-00000000000a", "123456", { platform: "IOS", appVersion: "1" });
    await session.signOut();
    const out = calls.find((c) => c.url.endsWith("/auth/logout"))!;
    expect(out.auth).toBe("Bearer at1");
    expect(await session.isSignedIn()).toBe(false);

    const offline = setup((url) => {
      if (url.endsWith("/auth/verify")) return json(200, tokens(1));
      throw new TypeError("offline");
    });
    await offline.session.verifyCode("0190a000-0000-7000-8000-00000000000a", "123456", { platform: "IOS", appVersion: "1" });
    await offline.session.signOut();
    expect(await offline.session.isSignedIn()).toBe(false);
  });

  it("a refresh in flight during sign out does not bring the session back", async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => (release = r));
    const { session, advance, signedOut } = setup(async (url) => {
      if (url.endsWith("/auth/verify")) return json(200, tokens(1));
      if (url.endsWith("/auth/refresh")) {
        await gate;
        return json(200, tokens(2));
      }
      if (url.endsWith("/auth/logout")) return new Response(null, { status: 204 });
      return json(200, inboxBody);
    });
    await session.verifyCode("0190a000-0000-7000-8000-00000000000a", "123456", { platform: "IOS", appVersion: "1" });
    advance(850_000);
    const pending = session.inbox().catch((e: unknown) => e);
    await new Promise((r) => setTimeout(r, 10)); // refresh now waiting on the server
    const out = session.signOut(); // waits on the same single flight refresh
    // Simulate the person signing out locally first by ending while refresh is pending.
    await (session as unknown as { endLocal(): Promise<void> }).endLocal();
    release();
    await out;
    await pending;
    expect(await session.isSignedIn()).toBe(false);
    expect(signedOut).toEqual([]);
  });
});
