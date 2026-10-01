import { act, fireEvent, render, screen, waitFor } from "@testing-library/react-native";

import { IntentCard, loadInstallId, randomUuid, RingSaysProvider, type IntentDisplay, type KeyValueStore, type Lang } from "../src";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

const base: IntentDisplay = {
  intent_id: "0190a000-0000-7000-8000-000000000001",
  status: "DELIVERED",
  verification_level: "ORG_AGENT_NUMBER",
  intent_source: "DECLARED",
  priority: "IMPORTANT",
  expected_duration_min: 5,
  valid_until: new Date(Date.now() + 20 * 60_000 + 30_000).toISOString(),
  actions: ["TALK_NOW", "LATER", "DECLINE"],
  organisation_name: "Mock Bank",
  agent_display_name: "Sara",
  why: "Mortgage document clarification",
  masked_reference: "8291",
};

function memStore(): KeyValueStore & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return { data, getItem: async (k) => data.get(k) ?? null, setItem: async (k, v) => void data.set(k, v) };
}

type Call = { method: string; url: string; headers: Headers; body: unknown };

function fakeApi(handler: (c: Call) => { status: number; body: unknown }) {
  const calls: Call[] = [];
  const f = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const req = input instanceof Request ? input : new Request(String(input), init);
    const text = req.method === "POST" ? await req.text() : "";
    const c: Call = { method: req.method, url: req.url, headers: req.headers, body: text ? JSON.parse(text) : null };
    calls.push(c);
    const r = handler(c);
    return new Response(JSON.stringify(r.body), {
      status: r.status,
      headers: { "Content-Type": r.status >= 400 ? "application/problem+json" : "application/json" },
    });
  }) as typeof fetch;
  return { fetch: f, calls };
}

const rng = (n: number) => Uint8Array.from({ length: n }, (_, i) => (i * 37 + 11) & 0xff);

function mount(api: ReturnType<typeof fakeApi>, opts: { lang?: Lang; store?: KeyValueStore; onAnswered?: (i: IntentDisplay) => void } = {}) {
  return render(
    <RingSaysProvider baseUrl="https://api.example" storage={opts.store ?? memStore()} language={opts.lang ?? "en"} fetch={api.fetch} random={rng}>
      <IntentCard token="ctx_abc" onAnswered={opts.onAnswered} />
    </RingSaysProvider>,
  );
}

test("install id: UUID v4, created once and kept", async () => {
  expect(randomUuid(rng)).toMatch(UUID);
  const s = memStore();
  const a = await loadInstallId(s, rng);
  const b = await loadInstallId(s, () => new Uint8Array(16).fill(0xee));
  expect(a).toMatch(UUID);
  expect(b).toBe(a);
});

test("verified organisation: badge, reason, reference, sends install id and language", async () => {
  const api = fakeApi(() => ({ status: 200, body: base }));
  mount(api);
  expect(await screen.findByTestId("ringsays-verified")).toHaveTextContent(/Organisation and number verified/);
  expect(screen.getByTestId("ringsays-why")).toHaveTextContent("Mortgage document clarification");
  expect(screen.getByText("Important")).toBeTruthy();
  expect(screen.getByText(/8291/)).toBeTruthy();
  const c = api.calls[0]!;
  expect(c.url).toBe("https://api.example/v1/tokens/ctx_abc");
  expect(c.headers.get("RingSays-Device-Id")).toMatch(UUID);
  expect(c.headers.get("Accept-Language")).toBe("en");
});

test("unverified sender never gets the verified badge", async () => {
  const api = fakeApi(() => ({
    status: 200,
    body: { ...base, verification_level: "PHONE", why: null, organisation_name: null, unverified_subject: "Account blocked, call now" },
  }));
  mount(api);
  expect(await screen.findByTestId("ringsays-unverified")).toBeTruthy();
  expect(screen.queryByTestId("ringsays-verified")).toBeNull();
  expect(screen.getByTestId("ringsays-why")).toHaveStyle({ fontStyle: "italic" });
});

test("Arabic: right to left and Arabic labels", async () => {
  const api = fakeApi(() => ({ status: 200, body: base }));
  mount(api, { lang: "ar" });
  const card = await screen.findByTestId("ringsays-intent");
  expect(card).toHaveStyle({ direction: "rtl" });
  expect(api.calls[0]!.headers.get("Accept-Language")).toBe("ar");
  expect(screen.getByTestId("ringsays-action-TALK_NOW")).toHaveTextContent("تحدّث الآن");
});

test("talk now: posts ACCEPT, shows outcome, calls onAnswered", async () => {
  const api = fakeApi((c) =>
    c.method === "POST" ? { status: 200, body: { ...base, status: "ACCEPTED", actions: [] } } : { status: 200, body: base },
  );
  const answered = jest.fn();
  mount(api, { onAnswered: answered });
  fireEvent.press(await screen.findByTestId("ringsays-action-TALK_NOW"));
  expect(await screen.findByTestId("ringsays-outcome")).toBeTruthy();
  const post = api.calls.find((c) => c.method === "POST")!;
  expect(post.url).toBe("https://api.example/v1/tokens/ctx_abc/respond");
  expect(post.body).toEqual({ action: "ACCEPT" });
  expect(answered).toHaveBeenCalledWith(expect.objectContaining({ status: "ACCEPTED" }));
  expect(screen.queryByTestId("ringsays-action-TALK_NOW")).toBeNull();
});

test("later: offers minutes and posts the choice", async () => {
  const api = fakeApi((c) =>
    c.method === "POST" ? { status: 200, body: { ...base, status: "SCHEDULED", actions: [] } } : { status: 200, body: base },
  );
  mount(api);
  fireEvent.press(await screen.findByTestId("ringsays-action-LATER"));
  fireEvent.press(screen.getByText("In 30 min"));
  await screen.findByTestId("ringsays-outcome");
  expect(api.calls.find((c) => c.method === "POST")!.body).toEqual({ action: "LATER", later_minutes: 30 });
});

test("token answered on another install: 403 shows not for this device", async () => {
  const api = fakeApi(() => ({ status: 403, body: { type: "about:blank", title: "Forbidden", status: 403 } }));
  mount(api);
  expect(await screen.findByTestId("ringsays-error")).toHaveTextContent(/another device/i);
});

test("ended intent: 410", async () => {
  const api = fakeApi(() => ({ status: 410, body: { type: "about:blank", title: "Gone", status: 410 } }));
  mount(api);
  expect(await screen.findByTestId("ringsays-error")).toHaveTextContent(/ended/i);
});

test("failed answer keeps the intent so the customer can retry", async () => {
  let fail = true;
  const api = fakeApi((c) => {
    if (c.method !== "POST") return { status: 200, body: base };
    if (fail) return { status: 503, body: { type: "about:blank", title: "Unavailable", status: 503 } };
    return { status: 200, body: { ...base, status: "ACCEPTED", actions: [] } };
  });
  mount(api);
  fireEvent.press(await screen.findByTestId("ringsays-action-TALK_NOW"));
  expect(await screen.findByTestId("ringsays-respond-error")).toBeTruthy();
  expect(screen.getByTestId("ringsays-intent")).toBeTruthy();
  fail = false;
  fireEvent.press(screen.getByTestId("ringsays-action-TALK_NOW"));
  await waitFor(() => expect(screen.queryByTestId("ringsays-respond-error")).toBeNull());
  expect(await screen.findByTestId("ringsays-outcome")).toBeTruthy();
});

test("network failure", async () => {
  const f = (async () => {
    throw new TypeError("Network request failed");
  }) as typeof fetch;
  render(
    <RingSaysProvider baseUrl="https://api.example" storage={memStore()} language="en" fetch={f} random={rng}>
      <IntentCard token="ctx_abc" />
    </RingSaysProvider>,
  );
  expect(await screen.findByTestId("ringsays-error")).toHaveTextContent(/connection|network|internet/i);
  await act(async () => {});
});

test("storage that cannot be read shows an error instead of loading forever", async () => {
  const api = fakeApi(() => ({ status: 200, body: base }));
  const broken: KeyValueStore = { getItem: async () => Promise.reject(new Error("locked")), setItem: async () => {} };
  mount(api, { store: broken });
  expect(await screen.findByTestId("ringsays-error")).toHaveTextContent(/went wrong/);
  expect(api.calls).toHaveLength(0);
});

test("token change: a slow answer for the old token never replaces the new intent", async () => {
  let releaseA: () => void = () => {};
  const gateA = new Promise<void>((r) => (releaseA = r));
  const f = (async (input: RequestInfo | URL) => {
    const url = input instanceof Request ? input.url : String(input);
    if (url.endsWith("/tokens/A")) {
      await gateA;
      return new Response(JSON.stringify({ ...base, organisation_name: "Old Bank" }), { status: 200, headers: { "Content-Type": "application/json" } });
    }
    return new Response(JSON.stringify({ ...base, organisation_name: "New Bank" }), { status: 200, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;
  const s = memStore();
  const ui = (token: string) => (
    <RingSaysProvider baseUrl="https://api.example" storage={s} language="en" fetch={f} random={rng}>
      <IntentCard token={token} />
    </RingSaysProvider>
  );
  const r = render(ui("A"));
  await act(async () => {
    await new Promise((x) => setTimeout(x, 20));
  });
  r.rerender(ui("B"));
  expect(await screen.findByText("New Bank")).toBeTruthy();
  await act(async () => {
    releaseA();
    await new Promise((x) => setTimeout(x, 20));
  });
  expect(screen.queryByText("Old Bank")).toBeNull();
  expect(screen.getByText("New Bank")).toBeTruthy();
});
