import createClient, { type Middleware } from "openapi-fetch";

import { config } from "../config";
import type { paths } from "./schema";
import type { Problem } from "./types";

/** Error carrying an RFC 9457 problem from the API. */
export class ApiError extends Error {
  readonly problem: Problem;
  constructor(problem: Problem) {
    super(problem.detail ?? problem.title ?? `HTTP ${problem.status}`);
    this.problem = problem;
  }
  get status(): number {
    return this.problem.status;
  }
}

type TokenSource = () => Promise<string | null>;
let tokenSource: TokenSource = async () => null;
let onUnauthenticated: () => void = () => undefined;
let language: () => string = () => "ar";

export function configureApi(opts: { token: TokenSource; onUnauthenticated: () => void; language: () => string }) {
  tokenSource = opts.token;
  onUnauthenticated = opts.onUnauthenticated;
  language = opts.language;
}

export async function authHeaders(): Promise<Record<string, string>> {
  const token = await tokenSource();
  const h: Record<string, string> = { "Accept-Language": language() };
  if (token) h.Authorization = `Bearer ${token}`;
  return h;
}

const auth: Middleware = {
  async onRequest({ request }) {
    for (const [k, v] of Object.entries(await authHeaders())) request.headers.set(k, v);
    return request;
  },
  async onResponse({ response }) {
    if (response.status === 401) onUnauthenticated();
    return response;
  },
};

export const api = createClient<paths>({ baseUrl: `${config.apiBase}/admin/v1` });
api.use(auth);

export async function toProblem(response: Response, body: unknown): Promise<Problem> {
  if (body && typeof body === "object" && "status" in body) return body as Problem;
  return { status: response.status, title: response.statusText };
}

/**
 * Unwrap an openapi-fetch result: return data or throw ApiError.
 * Every call site goes through here, so error handling is uniform.
 */
export async function unwrap<T>(
  p: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  let res: { data?: T; error?: unknown; response: Response };
  try {
    res = await p;
  } catch {
    throw new ApiError({ status: 0, code: "network", title: "Network error" });
  }
  if (res.error !== undefined || !res.response.ok) {
    throw new ApiError(await toProblem(res.response, res.error));
  }
  return res.data as T;
}
