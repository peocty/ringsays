import type { components } from "./schema";

type S = components["schemas"];
export type IntentDisplay = S["IntentDisplay"];
export type DisplayAction = IntentDisplay["actions"][number];
export type IntentResponse = S["IntentResponse"];
export type ResponseAction = IntentResponse["action"];
export type Preferences = S["Preferences"];
export type PreferenceRule = S["Preferences"]["rules"][number];
export type TimeWindow = S["TimeWindow"];
export type Consent = S["Consent"];
export type TokenPair = S["TokenPair"];
export type PushTokens = S["PushTokens"];
export type Slot = { id?: string; start: string; end: string };
export type Folder = "REQUESTS" | "SCHEDULED" | "HISTORY";
export type Lang = "ar" | "en";

/** Display action (what the person taps) to API response action. */
export const ACTION_FOR: Record<DisplayAction, ResponseAction> = {
  TALK_NOW: "ACCEPT",
  LATER: "LATER",
  PROPOSE: "PROPOSE",
  SCHEDULE: "SCHEDULE",
  MESSAGE: "MESSAGE",
  DECLINE: "DECLINE",
};
