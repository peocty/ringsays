import { formatBytes, formatDateTime, ltr, pick } from "../src/lib/format";

describe("formatting", () => {
  it("dates use Gregorian calendar and Latin digits in Arabic", () => {
    const s = formatDateTime("2026-10-04T07:30:00Z", "ar");
    expect(s).toMatch(/2026/);
    expect(s).toContain("10:30");
    expect(s).not.toMatch(/[٠-٩]/);
  });

  it("dates show Riyadh time", () => {
    expect(formatDateTime("2026-10-04T07:30:00Z", "en")).toContain("10:30");
  });

  it("missing or bad dates show a dash", () => {
    expect(formatDateTime(null, "en")).toBe("—");
    expect(formatDateTime("nope", "en")).toBe("—");
  });

  it("picks the language and falls back", () => {
    expect(pick({ en: "Bank", ar: "بنك" }, "ar")).toBe("بنك");
    expect(pick({ en: "Bank", ar: "" }, "ar")).toBe("Bank");
    expect(pick(null, "en")).toBe("—");
  });

  it("formats sizes", () => {
    expect(formatBytes(2048, "en")).toBe("2 KB");
    expect(formatBytes(5 * 1024 * 1024, "en")).toBe("5 MB");
  });

  it("isolates left to right fragments", () => {
    expect(ltr("+966")).toBe("⁦+966⁩");
  });
});
