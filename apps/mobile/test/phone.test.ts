import { config } from "../src/config";
import { toE164 } from "../src/lib/phone";

const [SA, IN, AE] = config.countries;

test("Saudi numbers in the forms people type", () => {
  for (const typed of ["512345678", "0512345678", "+966 51 234 5678", "966512345678", "٠٥١٢٣٤٥٦٧٨"]) {
    expect(toE164(SA, typed)).toBe("+966512345678");
  }
  expect(toE164(SA, "412345678")).toBeNull(); // not a mobile number
  expect(toE164(SA, "51234")).toBeNull();
});

test("India and UAE", () => {
  expect(toE164(IN, "98765 43210")).toBe("+919876543210");
  expect(toE164(IN, "58765 43210")).toBeNull();
  expect(toE164(AE, "0501234567")).toBe("+971501234567");
});
