import { RingSaysError } from "@ringsays/client";
import i18next from "i18next";

import { en } from "../src/i18n/en";
import { errorMessage } from "../src/lib/errors";

beforeAll(async () => {
  await i18next.init({ lng: "en", resources: { en: { translation: en } } });
});

const err = (status: number, extra: Partial<RingSaysError> & { code?: string } = {}) => {
  const e = new RingSaysError({ status, code: extra.code });
  if (extra.retryAfterS) e.retryAfterS = extra.retryAfterS;
  return e;
};

test("messages never expose server detail", () => {
  const t = i18next.t.bind(i18next);
  expect(errorMessage(err(0), t)).toBe(en.errors.network);
  expect(errorMessage(err(429, { retryAfterS: 42 }), t)).toBe("Too many tries. Try again in 42 seconds.");
  expect(errorMessage(err(401), t)).toBe(en.errors.wrongCode);
  expect(errorMessage(err(401, { code: "signed_out" }), t)).toBe(en.errors.signedOut);
  expect(errorMessage(err(410), t)).toBe(en.errors.gone);
  expect(errorMessage(err(412), t)).toBe(en.errors.conflict);
  expect(errorMessage(new Error("Internal stack trace"), t)).toBe(en.errors.generic);
});
