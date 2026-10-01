import createClient, { type Client } from "openapi-fetch";

import { networkError, problemFrom, RingSaysError } from "./errors";
import type { paths } from "./schema";
import type { IntentDisplay, IntentResponse, Lang } from "./types";

export interface ContextTokenClientOptions {
  baseUrl: string;
  /** UUID generated once per app install and kept in app storage. Binds a token to this install. */
  installId: string;
  language: () => Lang;
  fetch?: typeof fetch;
}

/**
 * For an organisation's own app (enterprise SDK): show and answer an intent with the Context Token
 * the organisation's backend received from RingSays. No RingSays account is needed. The first install
 * that resolves a token owns it; any other install gets 403, and an ended intent 410.
 */
export class ContextTokenClient {
  private readonly api: Client<paths>;

  constructor(private readonly opts: ContextTokenClientOptions) {
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(opts.installId)) {
      throw new Error("installId must be a UUID");
    }
    this.api = createClient<paths>({ baseUrl: `${opts.baseUrl}/v1`, fetch: opts.fetch ?? ((...a) => fetch(...a)) });
  }

  resolve(token: string): Promise<IntentDisplay> {
    return this.run(() =>
      this.api.GET("/tokens/{token}", {
        params: { path: { token }, header: this.headers() },
      }),
    );
  }

  respond(token: string, response: IntentResponse): Promise<IntentDisplay> {
    return this.run(() =>
      this.api.POST("/tokens/{token}/respond", {
        params: { path: { token }, header: this.headers() },
        body: response,
      }),
    );
  }

  private headers(): { "RingSays-Device-Id": string; "Accept-Language": string } {
    return { "RingSays-Device-Id": this.opts.installId, "Accept-Language": this.opts.language() };
  }

  private async run<T>(fn: () => Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
    let res: { data?: T; error?: unknown; response: Response };
    try {
      res = await fn();
    } catch (e) {
      if (e instanceof RingSaysError) throw e;
      throw networkError();
    }
    if (!res.response.ok) throw await problemFrom(res.response, res.error);
    return res.data as T;
  }
}
