import { ar } from "../src/i18n/ar";
import { en } from "../src/i18n/en";

type Tree = { [k: string]: string | Tree };
const flat = (t: Tree, p = ""): Record<string, string> =>
  Object.entries(t).reduce<Record<string, string>>((a, [k, v]) => {
    const key = p ? `${p}.${k}` : k;
    if (typeof v === "string") a[key] = v;
    else Object.assign(a, flat(v, key));
    return a;
  }, {});

const E = flat(en as unknown as Tree);
const A = flat(ar as unknown as Tree);

test("same keys in Arabic and English", () => {
  expect(Object.keys(A).sort()).toEqual(Object.keys(E).sort());
});

test("same placeholders", () => {
  const vars = (s: string) => (s.match(/{{\w+}}/g) ?? []).sort().join();
  expect(Object.keys(E).filter((k) => vars(E[k]!) !== vars(A[k]!))).toEqual([]);
});

test("Arabic is Arabic", () => {
  const exempt = new Set(["app.switchLanguage"]);
  expect(Object.entries(A).filter(([k, v]) => !exempt.has(k) && !/[؀-ۿ]/.test(v))).toEqual([]);
});

test("every status and decline reason has a label", () => {
  const { INTENT_STATUSES, DECLINE_REASONS } = jest.requireActual("@ringsays/domain") as typeof import("@ringsays/domain");
  for (const s of INTENT_STATUSES) expect(E[`intent.status.${s}`]).toBeTruthy();
  for (const r of DECLINE_REASONS) expect(E[`intent.reasons.${r}`]).toBeTruthy();
});
