import { expect, test } from "@playwright/test";

import { openApp, openConsole, row, sendRequest, tick } from "./helpers";

test("Arabic customer picks one of the bank's offered times; agent sees it by webhook", async ({ browser }) => {
  const agent = await openConsole(browser);
  const id = await sendRequest(agent, { customer: "cus_noura", reference: "8291", offerSlots: 2 });
  await expect(row(agent, id).getByTestId("status")).toHaveText("Waiting to open");

  const app = await openApp(browser, "cus_noura");
  const card = app.getByTestId(`message-${id}`);
  await expect(card.getByTestId("ringsays-verified")).toContainText("المنشأة والرقم موثّقان");
  await expect(card.getByTestId("ringsays-why")).toHaveText("توضيح بخصوص مستندات التمويل العقاري");
  await expect(card.getByText(/8291/)).toBeVisible();
  await card.getByTestId("ringsays-action-SCHEDULE").click();
  await card.getByRole("button").first().click(); // earliest offered time
  await expect(card.getByTestId("ringsays-outcome")).toBeVisible();

  tick();
  await expect(row(agent, id).getByTestId("status")).toHaveText("Scheduled");
  await expect(row(agent, id)).toContainText("webhook events");
  await agent.context().close();
  await app.context().close();
});

test("English customer talks now; agent calls and records the outcome; app history updates", async ({ browser }) => {
  const agent = await openConsole(browser);
  const id = await sendRequest(agent, { customer: "cus_priya", purpose: "ACCOUNT.SERVICE.FOLLOWUP" });
  const app = await openApp(browser, "cus_priya");
  const card = app.getByTestId(`message-${id}`);
  await expect(card.getByTestId("ringsays-verified")).toContainText("Organisation and number verified");
  await card.getByTestId("ringsays-action-TALK_NOW").click();
  await expect(card.getByTestId("ringsays-outcome")).toBeVisible();

  tick();
  const r = row(agent, id);
  await expect(r.getByTestId("status")).toHaveText("Customer ready to talk");
  await r.getByRole("button", { name: "Call now" }).click();
  await expect(r.getByTestId("status")).toHaveText("On the call");
  await r.getByLabel("Outcome").selectOption("RESOLVED");
  await r.getByRole("button", { name: "Record outcome" }).click();
  await expect(r.getByTestId("status")).toHaveText("Completed");
  await expect(r).toContainText("Outcome: RESOLVED");

  await expect(app.getByTestId(`history-${id}`)).toContainText("Completed", { timeout: 20_000 });
  await agent.context().close();
  await app.context().close();
});

test("customer suggests a time; agent confirms it; the request is bound to the first device", async ({ browser }) => {
  const agent = await openConsole(browser);
  const id = await sendRequest(agent, { customer: "cus_faisal", purpose: "LOAN.APPLICATION.UPDATE" });
  const app = await openApp(browser, "cus_faisal");
  await app.getByTestId("lang-toggle").click(); // to English
  const card = app.getByTestId(`message-${id}`);
  await card.getByTestId("ringsays-action-PROPOSE").click();
  await card.getByRole("button").first().click(); // first suggested time
  await card.getByRole("button", { name: "Send" }).click();
  await expect(card.getByTestId("ringsays-outcome")).toBeVisible();

  tick();
  const r = row(agent, id);
  await expect(r.getByTestId("status")).toHaveText("Customer proposed times");
  await r.getByRole("button", { name: /^Confirm / }).first().click();
  await expect(r.getByTestId("status")).toHaveText("Scheduled");

  // Same customer on a second phone: the Context Token belongs to the first install.
  const other = await openApp(browser, "cus_faisal");
  await expect(other.getByTestId(`message-${id}`).getByTestId("ringsays-error")).toContainText("جهاز آخر");
  await agent.context().close();
  await app.context().close();
  await other.context().close();
});

test("agent withdraws a request; it leaves the customer's open requests", async ({ browser }) => {
  const agent = await openConsole(browser);
  const id = await sendRequest(agent, { customer: "cus_noura", purpose: "KYC.DOC.EXPIRED" });
  const app = await openApp(browser, "cus_noura");
  await expect(app.getByTestId(`message-${id}`)).toBeVisible();
  await row(agent, id).getByRole("button", { name: "Cancel" }).click();
  await expect(row(agent, id).getByTestId("status")).toHaveText("Cancelled");
  await expect(app.getByTestId(`message-${id}`)).toHaveCount(0, { timeout: 20_000 });
  await expect(app.getByTestId(`history-${id}`)).toContainText("سحبه البنك");
  await agent.context().close();
  await app.context().close();
});
