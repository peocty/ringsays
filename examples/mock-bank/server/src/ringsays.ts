import { randomUUID } from "node:crypto";

/**
 * RingSays enterprise API client, as a bank backend would write it: OAuth client credentials with
 * a cached token, an Idempotency-Key on every write (retries are safe), problem details on errors.
 * The client secret and context tokens never leave this server and are never logged.
 */
export interface Slot {
  start: string;
  end: string;
}

export interface Intent {
  intent_id: string;
  status: string;
  purpose_code: string | null;
  priority: string;
  expected_duration_min: number;
  channel_used: string | null;
  scheduled_slot: Slot | null;
  proposed_slots?: Slot[];
  valid_until: string;
  updated_at: string;
}

export interface CreateIntent {
  to: { phone: string };
  agent_id: string;
  purpose_code: string;
  priority: string;
  expected_duration_min: number;
  valid_from: string;
  valid_until: string;
  masked_reference?: string;
  channel_preference?: string[];
  language?: "en" | "ar";
  offered_slots?: Slot[];
}

export interface PurposeCode {
  code: string;
  display_text: { en: string; ar: string };
  max_priority: string;
  max_duration_min: number;
  allowed_channels: string[];
  status: string;
}

export class RingSaysApiError extends Error {
  constructor(
    readonly status: number,
    readonly problem: { title?: string; detail?: string; code?: string } | null,
  ) {
    super(problem?.detail ?? problem?.title ?? `RingSays HTTP ${status}`);
  }
}

export interface RingSaysOptions {
  baseUrl: string;
  clientId: string;
  clientSecret: string;
  fetch?: typeof fetch;
  now?: () => number;
}

export class RingSays {
  private token: { value: string; expiresAt: number } | null = null;
  private pending: Promise<string> | null = null;
  private readonly f: typeof fetch;
  private readonly now: () => number;

  constructor(private readonly o: RingSaysOptions) {
    this.f = o.fetch ?? fetch;
    this.now = o.now ?? Date.now;
  }

  createIntent(body: CreateIntent): Promise<Intent & { context_token?: string | null }> {
    return this.call("POST", "/v1/intents", body, randomUUID());
  }
  getIntent(id: string): Promise<Intent> {
    return this.call("GET", `/v1/intents/${encodeURIComponent(id)}`);
  }
  cancel(id: string): Promise<Intent> {
    return this.call("POST", `/v1/intents/${encodeURIComponent(id)}/cancel`, undefined, randomUUID());
  }
  calling(id: string): Promise<unknown> {
    return this.call("POST", `/v1/intents/${encodeURIComponent(id)}/calling`, undefined, randomUUID());
  }
  outcome(id: string, code: string): Promise<Intent> {
    return this.call("POST", `/v1/intents/${encodeURIComponent(id)}/outcome`, { code }, randomUUID());
  }
  schedule(id: string, slot: Slot): Promise<Intent> {
    return this.call("POST", `/v1/intents/${encodeURIComponent(id)}/schedule`, { slot }, randomUUID());
  }
  async purposeCodes(): Promise<PurposeCode[]> {
    const r = await this.call<{ items: PurposeCode[] }>("GET", "/v1/purpose-codes");
    return r.items.filter((c) => c.status === "APPROVED");
  }

  private async accessToken(): Promise<string> {
    if (this.token && this.token.expiresAt - 60_000 > this.now()) return this.token.value;
    this.pending ??= (async () => {
      try {
        const res = await this.f(`${this.o.baseUrl}/oauth/token`, {
          method: "POST",
          headers: { "Content-Type": "application/x-www-form-urlencoded" },
          body: new URLSearchParams({
            grant_type: "client_credentials",
            client_id: this.o.clientId,
            client_secret: this.o.clientSecret,
          }),
        });
        if (!res.ok) throw new RingSaysApiError(res.status, { title: "token request refused" });
        const t = (await res.json()) as { access_token: string; expires_in: number };
        this.token = { value: t.access_token, expiresAt: this.now() + t.expires_in * 1000 };
        return t.access_token;
      } finally {
        this.pending = null;
      }
    })();
    return this.pending;
  }

  private async call<T>(method: string, path: string, body?: unknown, idempotencyKey?: string): Promise<T> {
    for (let attempt = 0; ; attempt++) {
      const token = await this.accessToken();
      const res = await this.f(`${this.o.baseUrl}${path}`, {
        method,
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: "application/json",
          ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
          ...(idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {}),
        },
        ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
      });
      if (res.status === 401 && attempt === 0) {
        this.token = null; // revoked or rotated key: one fresh token, same idempotency key
        continue;
      }
      const text = await res.text();
      let parsed: unknown = null;
      try {
        parsed = text ? JSON.parse(text) : null;
      } catch {
        parsed = null;
      }
      if (!res.ok) throw new RingSaysApiError(res.status, parsed as RingSaysApiError["problem"]);
      return parsed as T;
    }
  }
}
