import type * as Notifications from "expo-notifications";

import { intentIdFrom } from "../src/lib/push";

const ID = "0b9f0f7e-5a2c-4c1e-9d55-3b8c2f4a1e10";
const note = (data: unknown, payload?: unknown) =>
  ({ request: { content: { data }, trigger: payload === undefined ? null : { type: "push", payload } } }) as unknown as Notifications.Notification;

describe("intentIdFrom", () => {
  it("reads content.data (Android, and iOS through the body key)", () => {
    expect(intentIdFrom(note({ intent_id: ID, kind: "INTENT" }))).toBe(ID);
  });
  it("falls back to the raw APNs payload", () => {
    expect(intentIdFrom(note(null, { aps: {}, intent_id: ID }))).toBe(ID);
    expect(intentIdFrom(note({}, { aps: {}, body: { intent_id: ID } }))).toBe(ID);
  });
  it("ignores anything that is not an intent id", () => {
    expect(intentIdFrom(note({ intent_id: "../x" }))).toBeNull();
    expect(intentIdFrom({ notification: note(null) } as unknown as Notifications.NotificationResponse)).toBeNull();
  });
});
