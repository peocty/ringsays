import { ar } from "../src/i18n/ar";
import { en } from "../src/i18n/en";

type Tree = { [k: string]: string | Tree };

function flatten(t: Tree, prefix = ""): Record<string, string> {
  return Object.entries(t).reduce<Record<string, string>>((acc, [k, v]) => {
    const key = prefix ? `${prefix}.${k}` : k;
    if (typeof v === "string") acc[key] = v;
    else Object.assign(acc, flatten(v, key));
    return acc;
  }, {});
}

const E = flatten(en as unknown as Tree);
const A = flatten(ar as unknown as Tree);

describe("translations", () => {
  it("have the same keys in English and Arabic", () => {
    expect(Object.keys(A).sort()).toEqual(Object.keys(E).sort());
  });

  it("have no empty strings", () => {
    expect(Object.entries({ ...E, ...A }).filter(([, v]) => !v.trim())).toEqual([]);
  });

  it("use the same interpolation placeholders", () => {
    const vars = (s: string) => (s.match(/{{\s*\w+\s*}}/g) ?? []).sort();
    const mismatched = Object.keys(E).filter((k) => JSON.stringify(vars(E[k]!)) !== JSON.stringify(vars(A[k]!)));
    expect(mismatched).toEqual([]);
  });

  it("Arabic strings are actually Arabic (except technical keys)", () => {
    const technical = new Set(["common.switchLanguage", "common.none", "integration.event", "verification.fileHint"]);
    const notArabic = Object.entries(A).filter(
      ([k, v]) => ![...technical].some((t) => k.startsWith(t)) && !/[؀-ۿ]/.test(v),
    );
    expect(notArabic).toEqual([]);
  });

  it("cover every role, status and channel the API returns", () => {
    for (const r of ["TENANT_ADMIN", "INTEGRATION_ADMIN", "SUPERVISOR", "AGENT", "COMPLIANCE", "RS_REVIEWER", "RS_ADMIN"]) {
      expect(E[`roles.${r}`]).toBeTruthy();
    }
    for (const s of ["PENDING_REVIEW", "APPROVED", "REJECTED", "RETIRED"]) expect(E[`catalogue.codeStatus.${s}`]).toBeTruthy();
    for (const s of ["PENDING", "SENDING", "DELIVERED", "DEAD"]) expect(E[`integration.deliveryStatus.${s}`]).toBeTruthy();
  });
});
