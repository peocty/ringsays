import { ApiError, unwrap } from "../src/api/client";

function res(status: number): Response {
  return new Response(null, { status });
}

describe("unwrap", () => {
  it("returns data on success", async () => {
    await expect(unwrap(Promise.resolve({ data: { ok: 1 }, response: res(200) }))).resolves.toEqual({ ok: 1 });
  });

  it("throws ApiError with the problem body", async () => {
    const err = await unwrap(
      Promise.resolve({ error: { status: 409, code: "conflict", detail: "locked" }, response: res(409) }),
    ).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(409);
    expect((err as ApiError).message).toBe("locked");
  });

  it("maps network failure to status 0", async () => {
    const err = await unwrap(Promise.reject(new TypeError("fetch failed"))).catch((e: unknown) => e);
    expect((err as ApiError).status).toBe(0);
  });
});
