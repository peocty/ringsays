import { fill, text } from "../src/i18n";

test("Arabic and English have the same texts and statuses", () => {
  expect(Object.keys(text.ar).sort()).toEqual(Object.keys(text.en).sort());
  expect(Object.keys(text.ar.status).sort()).toEqual(Object.keys(text.en.status).sort());
  for (const v of Object.values(text.ar)) if (typeof v === "string") expect(v.length).toBeGreaterThan(0);
});

test("fill", () => {
  expect(fill(text.en.hello, { name: "Noura" })).toBe("Hello, Noura");
});
