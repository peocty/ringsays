import { sign, verifySignature, TOLERANCE_S } from "../src/webhook.js";

const secret = "whsec_test";
const body = Buffer.from('{"event_id":"e1","intent_id":"i1","status":"ACCEPTED"}');

test("valid signature accepted; any tampering refused", () => {
  const t = 1_790_000_000;
  const h = sign(body, secret, t);
  expect(verifySignature(body, h, secret, t)).toBe(true);
  expect(verifySignature(Buffer.from(body.toString().replace("ACCEPTED", "DECLINED")), h, secret, t)).toBe(false);
  expect(verifySignature(body, h, "whsec_other", t)).toBe(false);
  expect(verifySignature(body, undefined, secret, t)).toBe(false);
  expect(verifySignature(body, "v1=abc", secret, t)).toBe(false);
});

test("old or future timestamps refused (replay)", () => {
  const t = 1_790_000_000;
  const h = sign(body, secret, t);
  expect(verifySignature(body, h, secret, t + TOLERANCE_S)).toBe(true);
  expect(verifySignature(body, h, secret, t + TOLERANCE_S + 1)).toBe(false);
  expect(verifySignature(body, h, secret, t - TOLERANCE_S - 1)).toBe(false);
});

test("several v1 values: one valid is enough (secret rotation)", () => {
  const t = 1_790_000_000;
  const good = sign(body, secret, t).split("v1=")[1];
  expect(verifySignature(body, `t=${t},v1=${"0".repeat(64)},v1=${good}`, secret, t)).toBe(true);
});

test("matches RingSays signing: HMAC SHA-256 over t.body", async () => {
  const { createHmac } = await import("node:crypto");
  const t = 1_790_000_123;
  const expected = createHmac("sha256", secret).update(`${t}.${body.toString()}`).digest("hex");
  expect(sign(body, secret, t)).toBe(`t=${t},v1=${expected}`);
});
