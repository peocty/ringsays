// Handover screenshots (MOCK data). Runs only with SCREENSHOTS=<folder>.
import { expect, test } from "@playwright/test";

import { openApp, openConsole, row, sendRequest, tick } from "./helpers";

const out = process.env.SCREENSHOTS;
test.skip(!out, "set SCREENSHOTS=<folder> to capture");

test("screenshots", async ({ browser }) => {
  const agent = await openConsole(browser);
  await agent.setViewportSize({ width: 1360, height: 820 });
  const a = await sendRequest(agent, { customer: "cus_noura", reference: "8291", offerSlots: 2 });
  const b = await sendRequest(agent, { customer: "cus_priya", purpose: "CARD.TRANSACTION.VERIFY" });
  const c = await sendRequest(agent, { customer: "cus_faisal", purpose: "LOAN.APPLICATION.UPDATE" });

  const noura = await openApp(browser, "cus_noura");
  await expect(noura.getByTestId(`message-${a}`).getByTestId("ringsays-verified")).toBeVisible();
  await noura.screenshot({ path: `${out}/b-app-request-ar.png`, fullPage: true });
  await noura.getByTestId(`message-${a}`).getByTestId("ringsays-action-SCHEDULE").click();
  await noura.screenshot({ path: `${out}/b-app-offered-times-ar.png`, fullPage: true });
  await noura.getByTestId(`message-${a}`).getByRole("button").first().click();

  const priya = await openApp(browser, "cus_priya");
  await expect(priya.getByTestId(`message-${b}`).getByTestId("ringsays-verified")).toBeVisible();
  await priya.screenshot({ path: `${out}/b-app-urgent-en.png`, fullPage: true });
  await priya.getByTestId(`message-${b}`).getByTestId("ringsays-action-TALK_NOW").click();

  const faisal = await openApp(browser, "cus_faisal");
  const card = faisal.getByTestId(`message-${c}`);
  await card.getByTestId("ringsays-action-PROPOSE").click();
  await card.getByRole("button").first().click();
  await card.getByRole("button", { name: "إرسال" }).click();
  await expect(card.getByTestId("ringsays-outcome")).toBeVisible();

  tick();
  await expect(row(agent, c).getByTestId("status")).toHaveText("Customer proposed times");
  await expect(row(agent, b).getByTestId("status")).toHaveText("Customer ready to talk");
  await agent.screenshot({ path: `${out}/b-console.png`, fullPage: true });
});
