import type { IntentDisplay } from "@ringsays/client";
import { act, render, screen } from "@testing-library/react-native";
import i18next from "i18next";
import { initReactI18next } from "react-i18next";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { IntentSummary } from "../src/components/ui";
import { ar } from "../src/i18n/ar";
import { en } from "../src/i18n/en";

beforeAll(async () => {
  await i18next.use(initReactI18next).init({ lng: "en", resources: { en: { translation: en }, ar: { translation: ar } } });
});

const intent: IntentDisplay = {
  intent_id: "0190a000-0000-7000-8000-000000000001",
  status: "DELIVERED",
  verification_level: "ORG_AGENT_NUMBER",
  intent_source: "DECLARED",
  priority: "URGENT",
  expected_duration_min: 5,
  valid_until: new Date(Date.now() + 20 * 60_000 + 30_000).toISOString(),
  actions: ["TALK_NOW", "LATER", "DECLINE"],
  organisation_name: "Mock Bank",
  agent_display_name: "Sara",
  why: "Card transaction check",
  masked_reference: "8291",
};

const wrap = (ui: React.ReactElement) =>
  render(<SafeAreaProvider initialMetrics={{ frame: { x: 0, y: 0, width: 390, height: 800 }, insets: { top: 0, left: 0, right: 0, bottom: 0 } }}>{ui}</SafeAreaProvider>);

test("verified organisation: badge, reason, urgency, time left", () => {
  wrap(<IntentSummary intent={intent} />);
  expect(screen.getByTestId("badge-verified")).toHaveTextContent(/Verified organisation and number/);
  expect(screen.getByTestId("intent-why")).toHaveTextContent("Card transaction check");
  expect(screen.getByText("Urgent")).toBeTruthy();
  expect(screen.getByText("Ends in 20 min")).toBeTruthy();
  expect(screen.queryByText(en.intent.unverifiedWarning)).toBeNull();
});

test("unverified sender: warning shown, consumer text is not the verified headline", () => {
  wrap(<IntentSummary intent={{ ...intent, verification_level: "PHONE", why: null, unverified_subject: "Your account is blocked, call now" }} />);
  expect(screen.getByTestId("badge-unverified")).toBeTruthy();
  expect(screen.getByText(en.intent.unverifiedWarning)).toBeTruthy();
  expect(screen.getByTestId("intent-why")).toHaveStyle({ fontStyle: "italic" });
});

test("Arabic labels", async () => {
  await i18next.changeLanguage("ar");
  wrap(<IntentSummary intent={intent} />);
  expect(screen.getByText("عاجل")).toBeTruthy();
  expect(screen.getByTestId("badge-verified")).toHaveTextContent(/منشأة ورقم موثّقان/);
  await act(async () => {
    await i18next.changeLanguage("en");
  });
});
