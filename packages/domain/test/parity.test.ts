import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { parse } from "yaml";
import {
  ACTORS,
  CHANNELS,
  DECLINE_REASONS,
  EXPIRABLE_STATUSES,
  INTENT_SOURCES,
  INTENT_STATUSES,
  OUTCOME_CODES,
  PRIORITIES,
  TERMINAL_STATUSES,
  TRANSITIONS,
  VERIFICATION_LEVELS,
  availableReceiverActions,
  canTransition,
  showsVerifiedBadge,
} from "../src/index.js";

const root = fileURLToPath(new URL("../../../", import.meta.url));
const common = parse(readFileSync(`${root}contracts/openapi/common.yaml`, "utf8")).components.schemas;
const machine = parse(readFileSync(`${root}contracts/state-machines/intent.yaml`, "utf8"));

describe("contract parity", () => {
  it.each([
    ["IntentStatus", INTENT_STATUSES],
    ["Priority", PRIORITIES],
    ["IntentSource", INTENT_SOURCES],
    ["VerificationLevel", VERIFICATION_LEVELS],
    ["Channel", CHANNELS],
    ["OutcomeCode", OUTCOME_CODES],
    ["DeclineReason", DECLINE_REASONS],
  ] as const)("%s matches OpenAPI", (schema, values) => {
    expect([...values]).toEqual(common[schema].enum);
  });

  it("transition table matches canonical file", () => {
    const norm = (list: { from: string; to: string; actors: string[] }[]) =>
      list.map((t) => `${t.from}>${t.to}:${[...t.actors].sort().join(",")}`).sort();
    expect(norm(TRANSITIONS.map((t) => ({ ...t, actors: [...t.actors] })))).toEqual(norm(machine.transitions));
    expect([...TERMINAL_STATUSES].sort()).toEqual([...machine.terminal].sort());
    expect([...EXPIRABLE_STATUSES].sort()).toEqual([...machine.expirable].sort());
  });
});

describe("canTransition", () => {
  it("rejects every pair outside table", () => {
    for (const from of INTENT_STATUSES)
      for (const to of INTENT_STATUSES)
        for (const actor of ACTORS) {
          const listed = TRANSITIONS.some((t) => t.from === from && t.to === to && t.actors.includes(actor));
          expect(canTransition(from, to, actor)).toBe(listed);
        }
  });

  it("terminal statuses have no exits", () => {
    for (const s of TERMINAL_STATUSES) expect(TRANSITIONS.some((t) => t.from === s)).toBe(false);
  });
});

describe("availableReceiverActions", () => {
  it("offers full set on delivered intent with offered slots", () => {
    expect(availableReceiverActions("DELIVERED", true)).toEqual([
      "ACCEPT",
      "LATER",
      "PROPOSE",
      "SCHEDULE",
      "MESSAGE",
      "DECLINE",
    ]);
  });

  it("hides SCHEDULE without offered slots", () => {
    expect(availableReceiverActions("DELIVERED", false)).not.toContain("SCHEDULE");
  });

  it("offers only reschedule or decline once scheduled", () => {
    expect(availableReceiverActions("SCHEDULED", false)).toEqual(["PROPOSE", "DECLINE"]);
  });

  it("offers nothing before delivery or after terminal", () => {
    for (const s of ["REQUESTED", "COMPLETED", "EXPIRED", "IN_PROGRESS"] as const)
      expect(availableReceiverActions(s, true)).toEqual([]);
  });
});

describe("showsVerifiedBadge", () => {
  it("only organisation levels show badge", () => {
    expect(VERIFICATION_LEVELS.filter(showsVerifiedBadge)).toEqual(["ORG", "ORG_AGENT_NUMBER"]);
  });
});
