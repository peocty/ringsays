import createClient, { type Client, type Middleware } from "openapi-fetch";

import type { DeviceSigner } from "./deviceKey";
import { networkError, problemFrom, RingSaysError } from "./errors";
import type { paths } from "./schema";
import type { SecretStore } from "./storage";
import type {
  Consent,
  Folder,
  IntentDisplay,
  IntentResponse,
  Lang,
  Preferences,
  PushTokens,
  TokenPair,
} from "./types";

const KEY = "ringsays.session";
/** Refresh this long before the access token expires. */
const REFRESH_MARGIN_MS = 60_000;

interface Stored {
  accessToken: string;
  refreshToken: string;
  expiresAt: number;
  userId: string;
  deviceId: string;
}

export interface SessionOptions {
  /** API origin, for example https://api.ringsays.sa */
  baseUrl: string;
  store: SecretStore;
  signer: DeviceSigner;
  language: () => Lang;
  /** Injected for tests; defaults to global fetch. */
  fetch?: typeof fetch;
  now?: () => number;
  /** Called when the session ends without the person asking (refresh refused, account erased). */
  onSignedOut?: () => void;
}

export interface DeviceInfo {
  platform: "IOS" | "ANDROID";
  appVersion: string;
  push?: PushTokens;
}

/**
 * A signed in RingSays user on this device.
 *
 * - Access tokens are short; they are refreshed before expiry, and once more after a 401.
 * - Refresh is single flight: concurrent requests wait for one refresh, because a refresh token works
 *   once and reusing it makes the server revoke every session of this device family.
 * - The refresh token is signed by the device key on every use.
 */
export class Session {
  readonly api: Client<paths>;
  private state: Stored | null = null;
  private loaded = false;
  private refreshing: Promise<Stored | null> | null = null;
  /** Bumped on every sign out; a refresh that started before it must not bring the session back. */
  private epoch = 0;
  private readonly fetchImpl: typeof fetch;
  private readonly now: () => number;

  constructor(private readonly opts: SessionOptions) {
    this.fetchImpl = opts.fetch ?? ((...a) => fetch(...a));
    this.now = opts.now ?? Date.now;
    this.api = createClient<paths>({ baseUrl: `${opts.baseUrl}/v1`, fetch: this.fetchImpl });
    this.api.use(this.middleware());
  }

  // Sign in

  async requestCode(phone: string, locale: Lang): Promise<{ challengeId: string; expiresIn: number }> {
    const r = await this.call(() => this.raw.POST("/auth/otp", { body: { phone, locale } }));
    return { challengeId: r.challenge_id, expiresIn: r.expires_in };
  }

  async verifyCode(challengeId: string, code: string, device: DeviceInfo): Promise<TokenPair> {
    const tokens = await this.call(() =>
      this.raw.POST("/auth/verify", {
        body: {
          challenge_id: challengeId,
          code,
          device: {
            platform: device.platform,
            public_key: this.opts.signer.publicKeySpki(),
            app_version: device.appVersion,
            ...(device.push ? { push: device.push } : {}),
          },
        },
      }),
    );
    await this.save(tokens);
    return tokens;
  }

  async isSignedIn(): Promise<boolean> {
    return (await this.load()) !== null;
  }

  async userId(): Promise<string | null> {
    return (await this.load())?.userId ?? null;
  }

  async deviceId(): Promise<string | null> {
    return (await this.load())?.deviceId ?? null;
  }

  /**
   * Sign this device out: the server revokes the device, its refresh tokens and push tokens (so no
   * more pushes for this account reach the phone), then the session is removed from the phone. The
   * phone side always happens, even offline; the server side is best effort.
   */
  async signOut(): Promise<void> {
    let token: string | null = null;
    try {
      token = await this.accessToken();
    } catch {
      /* offline or refresh failed: sign out locally */
    }
    await this.endLocal();
    if (!token) return;
    const abort = typeof AbortController === "function" ? new AbortController() : null;
    const timer = abort ? setTimeout(() => abort.abort(), 5000) : null;
    try {
      await this.fetchImpl(`${this.opts.baseUrl}/v1/auth/logout`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        ...(abort ? { signal: abort.signal } : {}),
      });
    } catch {
      /* best effort; the refresh token is gone from the phone either way */
    } finally {
      if (timer) clearTimeout(timer);
    }
  }

  /** Remove the session from this phone only. */
  private async endLocal(): Promise<void> {
    this.epoch += 1;
    this.state = null;
    this.loaded = true;
    await this.opts.store.delete(KEY);
  }

  /** Valid access token, refreshing if needed; null when signed out. */
  async accessToken(): Promise<string | null> {
    const s = await this.load();
    if (!s) return null;
    if (s.expiresAt - REFRESH_MARGIN_MS > this.now()) return s.accessToken;
    return (await this.refresh())?.accessToken ?? null;
  }

  // Inbox and responses

  async inbox(folder?: Folder, cursor?: string, limit = 50): Promise<{ items: IntentDisplay[]; next_cursor: string | null }> {
    const r = await this.call(() =>
      this.api.GET("/inbox", {
        params: { query: { ...(folder ? { folder } : {}), ...(cursor ? { cursor } : {}), limit } },
      }),
    );
    return { items: r.items, next_cursor: r.next_cursor ?? null };
  }

  intent(intentId: string): Promise<IntentDisplay> {
    return this.call(() => this.api.GET("/me/intents/{intent_id}", { params: { path: { intent_id: intentId } } }));
  }

  respond(intentId: string, response: IntentResponse): Promise<IntentDisplay> {
    return this.call(() =>
      this.api.POST("/intents/{intent_id}/respond", { params: { path: { intent_id: intentId } }, body: response }),
    );
  }

  // Preferences and privacy

  async preferences(): Promise<{ doc: Preferences; etag: string }> {
    const res = await this.callWithResponse(() => this.api.GET("/me/preferences"));
    return { doc: res.data, etag: res.response.headers.get("ETag") ?? 'W/"v0"' };
  }

  /** Save with optimistic concurrency: 412 means another device saved first; reload and retry. */
  async savePreferences(doc: Preferences, etag: string): Promise<{ doc: Preferences; etag: string }> {
    const res = await this.callWithResponse(() =>
      this.api.PUT("/me/preferences", { body: doc, params: { header: { "If-Match": etag } } }),
    );
    return { doc: res.data, etag: res.response.headers.get("ETag") ?? etag };
  }

  async consents(): Promise<Consent[]> {
    return (await this.call(() => this.api.GET("/me/consents"))).items;
  }

  async withdrawConsent(consentId: string): Promise<void> {
    await this.callEmpty(() => this.api.DELETE("/me/consents/{consent_id}", { params: { path: { consent_id: consentId } } }));
  }

  exportData(): Promise<unknown> {
    return this.call(() => this.api.POST("/me/export"));
  }

  /** Erase the account. The session ends on every device. */
  async eraseAccount(): Promise<void> {
    await this.callEmpty(() => this.api.DELETE("/me"));
    await this.endLocal();
  }

  async updatePushTokens(push: PushTokens): Promise<void> {
    const deviceId = await this.deviceId();
    if (!deviceId) return;
    await this.callEmpty(() =>
      this.api.PUT("/devices/{device_id}/push-tokens", { params: { path: { device_id: deviceId } }, body: push }),
    );
  }

  // Internals

  /** Unauthenticated client for sign in calls (no bearer, no refresh). */
  private get raw(): Client<paths> {
    const c = createClient<paths>({ baseUrl: `${this.opts.baseUrl}/v1`, fetch: this.fetchImpl });
    c.use({
      onRequest: ({ request }) => {
        request.headers.set("Accept-Language", this.opts.language());
        return request;
      },
    });
    return c;
  }

  private middleware(): Middleware {
    return {
      onRequest: async ({ request }) => {
        request.headers.set("Accept-Language", this.opts.language());
        const hadSession = (await this.load()) !== null;
        const token = await this.accessToken();
        // Session ended while refreshing: fail here instead of sending the request without a token.
        if (hadSession && !token) throw new RingSaysError({ status: 401, code: "signed_out", title: "Signed out" });
        if (token) request.headers.set("Authorization", `Bearer ${token}`);
        return request;
      },
    };
  }

  private async load(): Promise<Stored | null> {
    if (!this.loaded) {
      const raw = await this.opts.store.get(KEY);
      try {
        this.state = raw ? (JSON.parse(raw) as Stored) : null;
      } catch {
        this.state = null;
      }
      this.loaded = true;
    }
    return this.state;
  }

  private async save(t: TokenPair): Promise<Stored> {
    const s: Stored = {
      accessToken: t.access_token,
      refreshToken: t.refresh_token,
      expiresAt: this.now() + t.expires_in * 1000,
      userId: t.user_id,
      deviceId: t.device_id,
    };
    this.state = s;
    this.loaded = true;
    await this.opts.store.set(KEY, JSON.stringify(s));
    return s;
  }

  private refresh(): Promise<Stored | null> {
    if (!this.refreshing) {
      this.refreshing = this.doRefresh().finally(() => {
        this.refreshing = null;
      });
    }
    return this.refreshing;
  }

  private async doRefresh(): Promise<Stored | null> {
    const s = await this.load();
    if (!s) return null;
    const epoch = this.epoch;
    const signature = await this.opts.signer.sign(s.refreshToken);
    let res: Response;
    try {
      res = await this.fetchImpl(`${this.opts.baseUrl}/v1/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: s.refreshToken, device_signature: signature }),
      });
    } catch {
      throw networkError(); // offline: keep the session, try again later
    }
    if (epoch !== this.epoch) return null; // signed out meanwhile: drop the result
    if (res.status === 401 || res.status === 403) {
      await this.endLocal();
      this.opts.onSignedOut?.();
      return null;
    }
    if (!res.ok) throw await problemFrom(res);
    const pair = (await res.json()) as TokenPair;
    if (epoch !== this.epoch) return null;
    return this.save(pair);
  }

  private async callWithResponse<T>(
    fn: () => Promise<{ data?: T; error?: unknown; response: Response }>,
  ): Promise<{ data: T; response: Response }> {
    let res: { data?: T; error?: unknown; response: Response };
    const hadSession = (await this.load()) !== null;
    try {
      res = await fn();
      if (res.response.status === 401 && hadSession) {
        // Access token rejected before its time (for example revoked): refresh once and retry.
        const fresh = await this.refresh();
        if (fresh) res = await fn();
      }
    } catch (e) {
      if (e instanceof RingSaysError) throw e;
      throw networkError();
    }
    if (!res.response.ok) {
      if (res.response.status === 401 && hadSession) {
        await this.signOut();
        this.opts.onSignedOut?.();
      }
      throw await problemFrom(res.response, res.error);
    }
    return { data: res.data as T, response: res.response };
  }

  private async call<T>(fn: () => Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
    return (await this.callWithResponse(fn)).data;
  }

  private async callEmpty(fn: () => Promise<{ error?: unknown; response: Response }>): Promise<void> {
    await this.callWithResponse(fn as () => Promise<{ data?: unknown; error?: unknown; response: Response }>);
  }
}
