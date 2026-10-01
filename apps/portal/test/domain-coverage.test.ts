import { CHANNELS, INTENT_STATUSES, PRIORITIES, VERIFICATION_LEVELS } from "@ringsays/domain";
import { readFileSync } from "node:fs";
import path from "node:path";
import { parse } from "yaml";

import { en } from "../src/i18n/en";

const admin = parse(readFileSync(path.resolve(__dirname, "../../../contracts/openapi/admin.yaml"), "utf8"));
const schemas = admin.components.schemas;

describe("portal labels match shared domain and admin contract", () => {
  it("every intent status, priority, channel and verification level has a label", () => {
    for (const s of INTENT_STATUSES) expect(en.monitor.intentStatus[s]).toBeTruthy();
    for (const p of PRIORITIES) expect(en.priority[p]).toBeTruthy();
    for (const c of CHANNELS) expect(en.channel[c]).toBeTruthy();
    for (const v of VERIFICATION_LEVELS) expect(en.monitor.level[v]).toBeTruthy();
  });

  it("every contract enum shown in the portal has a label", () => {
    const enumOf = (name: string): string[] => schemas[name].enum;
    for (const r of enumOf("TenantRole")) expect((en.roles as Record<string, string>)[r]).toBeTruthy();
    for (const r of enumOf("StaffRole")) expect((en.roles as Record<string, string>)[r]).toBeTruthy();
    for (const s of enumOf("Sector")) expect((en.sector as Record<string, string>)[s]).toBeTruthy();
    for (const k of enumOf("DocumentKind")) expect((en.verification.kinds as Record<string, string>)[k]).toBeTruthy();
    for (const e of enumOf("WebhookEventType")) expect((en.integration.event as Record<string, string>)[e]).toBeTruthy();
    for (const s of enumOf("Scope")) expect((en.integration.scope as Record<string, string>)[s]).toBeTruthy();
    for (const s of enumOf("TenantStatus")) expect((en.tenantStatus as Record<string, string>)[s]).toBeTruthy();
  });
});
