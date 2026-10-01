import { bank, BankError } from "../src/bank";

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

afterEach(() => jest.restoreAllMocks());

test("sends the bank session as bearer; maps errors", async () => {
  const f = jest.fn(async (_u: string, init?: RequestInit) => {
    void init;
    return json(200, []);
  });
  globalThis.fetch = f as unknown as typeof fetch;
  await bank.messages("tok");
  expect(f.mock.calls[0]![0]).toBe("http://127.0.0.1:4100/app/messages");
  expect((f.mock.calls[0]![1]!.headers as Record<string, string>).Authorization).toBe("Bearer tok");

  globalThis.fetch = (async () => json(401, { error: "sign in again" })) as unknown as typeof fetch;
  await expect(bank.messages("old")).rejects.toMatchObject({ status: 401, message: "sign in again" });

  globalThis.fetch = (async () => {
    throw new TypeError("offline");
  }) as unknown as typeof fetch;
  await expect(bank.customers()).rejects.toBeInstanceOf(BankError);
});
