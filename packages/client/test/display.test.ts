// Times in these tests are read on a UTC clock unless a test sets its own zone.
process.env.TZ = "UTC";

import { headline, isVerifiedOrganisation, minutesLeft, openSlots, suggestedSlots, type IntentDisplay } from "../src";

const base: IntentDisplay = {
  intent_id: "0190a000-0000-7000-8000-000000000001",
  status: "DELIVERED",
  verification_level: "ORG",
  intent_source: "DECLARED",
  priority: "NORMAL",
  expected_duration_min: 5,
  valid_until: "2026-10-04T08:00:00Z",
  actions: ["TALK_NOW", "LATER", "DECLINE"],
};

describe("display rules", () => {
  it("verified badge only for organisations", () => {
    expect(isVerifiedOrganisation(base)).toBe(true);
    expect(isVerifiedOrganisation({ verification_level: "ORG_AGENT_NUMBER" })).toBe(true);
    expect(isVerifiedOrganisation({ verification_level: "PHONE" })).toBe(false);
    expect(isVerifiedOrganisation({ verification_level: "NONE" })).toBe(false);
  });

  it("consumer text is never a verified headline", () => {
    expect(headline({ ...base, why: "Loan document" })).toEqual({ text: "Loan document", verified: true });
    expect(headline({ ...base, why: null, unverified_subject: "Your account is blocked" })).toEqual({
      text: "Your account is blocked",
      verified: false,
    });
  });

  it("time left and open slots", () => {
    const now = Date.parse("2026-10-04T07:30:00Z");
    expect(minutesLeft(base, now)).toBe(30);
    expect(minutesLeft(base, Date.parse("2026-10-05T00:00:00Z"))).toBe(0);
    const slots = openSlots(
      {
        proposed_slots: [
          { start: "2026-10-04T09:00:00Z", end: "2026-10-04T09:10:00Z" },
          { start: "2026-10-04T07:00:00Z", end: "2026-10-04T07:10:00Z" },
          { start: "2026-10-04T08:00:00Z", end: "2026-10-04T08:10:00Z" },
        ],
      },
      now,
    );
    expect(slots.map((x) => x.start)).toEqual(["2026-10-04T08:00:00Z", "2026-10-04T09:00:00Z"]);
  });

  it("suggested slots start at least an hour ahead on the quarter hour", () => {
    const now = Date.parse("2026-10-04T10:07:00Z");
    const s = suggestedSlots(10, now);
    expect(s[0]!.start).toBe("2026-10-04T11:15:00.000Z");
    expect(s[0]!.end).toBe("2026-10-04T11:25:00.000Z");
    expect(s).toHaveLength(3);
  });
});

describe("suggested times stay in working hours (phone's time zone)", () => {
  const tz = process.env.TZ;
  beforeAll(() => {
    process.env.TZ = "Asia/Riyadh";
  });
  afterAll(() => {
    process.env.TZ = tz;
  });
  const hour = (iso: string) => new Date(iso).getHours();

  it("late evening: next morning from 09:00", () => {
    const s = suggestedSlots(5, Date.parse("2026-10-01T23:49:00+03:00"));
    expect(s.map((x) => hour(x.start))).toEqual([9, 11, 13]);
    expect(new Date(s[0]!.start).getDate()).toBe(2);
  });

  it("afternoon: rolls past 20:00 to the next morning", () => {
    const s = suggestedSlots(5, Date.parse("2026-10-01T16:10:00+03:00"));
    expect(s.map((x) => hour(x.start))).toEqual([17, 19, 9]);
  });

  it("early morning: starts at 09:00", () => {
    expect(hour(suggestedSlots(5, Date.parse("2026-10-01T05:00:00+03:00"))[0]!.start)).toBe(9);
  });
});

it("suggestions respect the intent deadline", () => {
  const now = Date.parse("2026-10-04T10:07:00Z");
  expect(suggestedSlots(10, now, 3, "2026-10-04T13:30:00Z").map((x) => x.start)).toEqual([
    "2026-10-04T11:15:00.000Z",
    "2026-10-04T13:15:00.000Z",
  ]);
  expect(suggestedSlots(10, now, 3, "2026-10-04T10:30:00Z")).toEqual([]);
});
