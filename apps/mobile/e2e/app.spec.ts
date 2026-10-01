import { expect, test } from "@playwright/test";

import { API, deliver, newPhone, sendIntent, signIn } from "./helpers";

test("Arabic: request arrives with verified badge and reason; talk now", async ({ browser }) => {
  const phone = newPhone();
  const page = await signIn(browser, phone, "ar");
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await expect(page.getByText("لا أحد بانتظارك", { exact: false })).toBeVisible();
  const id = await sendIntent(phone);
  deliver();
  await page.reload();
  const card = page.getByTestId(`intent-${id}`);
  await expect(card).toBeVisible();
  await expect(card.getByTestId("badge-verified")).toContainText("منشأة ورقم موثّقان");
  await expect(card.getByTestId("intent-why")).toHaveText("توضيح بخصوص مستندات التمويل العقاري");
  await card.click();
  await expect(page).toHaveURL(new RegExp(`/intent/${id}$`));
  await page.getByTestId("action-TALK_NOW").click();
  await expect(page.getByTestId("answered")).toBeVisible();
  await expect(page.getByText("قبلت").last()).toBeVisible();
  await page.goBack();
  await page.getByRole("tab", { name: "المجدولة" }).click();
  await expect(page.getByTestId(`intent-${id}`)).toBeVisible();
  await page.context().close();
});

test("English: later, suggest times and decline with reason", async ({ browser }) => {
  const phone = newPhone();
  const page = await signIn(browser, phone, "en");
  await expect(page.locator("html")).toHaveAttribute("dir", "ltr");
  const [a, b, c] = [await sendIntent(phone), await sendIntent(phone), await sendIntent(phone, { priority: "URGENT" })];
  deliver();
  await page.reload();

  await page.getByTestId(`intent-${a}`).click();
  await page.getByTestId("action-LATER").click();
  await page.getByTestId("later-30").click();
  await expect(page.getByTestId("answered")).toBeVisible();
  await page.goBack();

  await page.getByTestId(`intent-${b}`).click();
  await page.getByTestId("action-PROPOSE").click();
  await page.getByRole("checkbox").first().click();
  await page.getByRole("checkbox").nth(1).click();
  await page.getByTestId("propose-send").click();
  await expect(page.getByTestId("answered")).toBeVisible();
  await expect(page.getByText("You asked for another time").last()).toBeVisible();
  await expect(page.getByText("Times you proposed")).toBeVisible();
  await page.goBack();

  const urgent = page.getByTestId(`intent-${c}`);
  await expect(urgent.getByText("Urgent")).toBeVisible();
  await urgent.click();
  await page.getByTestId("action-DECLINE").click();
  await page.getByTestId("decline-WRONG_PERSON").click();
  await expect(page.getByText("Declined").last()).toBeVisible();
  await page.goBack();
  await page.getByRole("tab", { name: "History" }).click();
  await expect(page.getByTestId(`intent-${c}`)).toBeVisible();
  await page.context().close();
});

test("wrong code is explained; session survives reload", async ({ browser }) => {
  const phone = newPhone();
  const ctx = await browser.newContext({ locale: "en-GB" });
  const page = await ctx.newPage();
  await page.goto("/");
  await page.getByTestId("phone-input").fill(phone.slice(4));
  await page.getByTestId("send-code").click();
  await page.getByTestId("code-input").fill("000000");
  await page.getByTestId("verify").click();
  await expect(page.getByTestId("sign-in-error")).toHaveText("That code is not right, or it has expired.");
  await expect(page.getByText(/New code in \d+ s/)).toBeVisible();
  const { code } = (await (await fetch(`${API}/dev/sms/last-code?phone=${encodeURIComponent(phone)}`)).json()) as { code: string };
  await page.getByTestId("code-input").fill(code);
  await page.getByTestId("verify").click();
  await expect(page).toHaveURL(/\/inbox$/);
  await page.reload();
  await expect(page).toHaveURL(/\/inbox$/);
  await ctx.close();
});

test("settings: verified only and quiet hours saved; stop an organisation; delete account", async ({ browser }) => {
  const phone = newPhone();
  const page = await signIn(browser, phone, "en");
  const id = await sendIntent(phone);
  deliver();
  await page.getByRole("tab", { name: "Settings" }).click();
  const verifiedOnly = page.getByTestId("verified-only");
  await verifiedOnly.click();
  await expect(page.getByText("Saved")).toBeVisible();
  await page.getByTestId("quiet-hours").click();
  await expect(page.getByTestId("quiet-start")).toHaveText("22:00");
  await page.reload();
  await expect(page.getByRole("switch", { name: "Only verified organisations" })).toBeChecked();
  await expect(page.getByTestId("quiet-end")).toHaveText("07:00");

  await page.getByRole("button", { name: "Stop" }).click();
  await page.getByTestId("withdraw-confirm").click();
  await expect(page.getByText("Stopped")).toBeVisible();
  await page.getByRole("tab", { name: "Inbox" }).click();
  await expect(page.getByTestId(`intent-${id}`)).toHaveCount(0);

  await page.getByRole("tab", { name: "Settings" }).click();
  await page.getByTestId("export").click();
  await expect(page.getByText(/Your data is ready: \d+ communications/)).toBeVisible();
  await page.getByTestId("delete-account").click();
  await page.getByTestId("delete-confirm").click();
  await expect(page.getByTestId("phone-input")).toBeVisible();
  await page.context().close();
});

test("language switch flips layout at once", async ({ browser }) => {
  const page = await signIn(browser, newPhone(), "en");
  await page.getByRole("tab", { name: "Settings" }).click();
  await page.getByTestId("lang-toggle").click();
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await expect(page.getByRole("tab", { name: "الوارد" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("tab", { name: "الإعدادات" })).toBeVisible();
  await page.context().close();
});
