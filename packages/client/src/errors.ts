/** RFC 9457 problem returned by the API, or a network failure (status 0). */
export interface Problem {
  type?: string;
  title?: string;
  status: number;
  code?: string;
  detail?: string;
  errors?: { field: string; message: string }[];
}

export class RingSaysError extends Error {
  readonly problem: Problem;
  constructor(problem: Problem) {
    super(problem.detail ?? problem.title ?? `HTTP ${problem.status}`);
    this.name = "RingSaysError";
    this.problem = problem;
  }
  get status(): number {
    return this.problem.status;
  }
  get code(): string | undefined {
    return this.problem.code;
  }
  /** Retry-After seconds from 429 responses, when given. */
  retryAfterS?: number;
}

/** `parsed` is the body when already read (openapi-fetch reads error bodies itself). */
export async function problemFrom(res: Response, parsed?: unknown): Promise<RingSaysError> {
  let body: unknown = parsed ?? null;
  if (body === null && !res.bodyUsed) {
    try {
      body = await res.clone().json();
    } catch {
      /* not JSON */
    }
  }
  const p: Problem =
    body && typeof body === "object" && "status" in body ? (body as Problem) : { status: res.status, title: res.statusText };
  const err = new RingSaysError(p);
  const ra = res.headers.get("Retry-After");
  if (ra && /^\d+$/.test(ra)) err.retryAfterS = Number(ra);
  return err;
}

export function networkError(): RingSaysError {
  return new RingSaysError({ status: 0, code: "network", title: "Network error" });
}
