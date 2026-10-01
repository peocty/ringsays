import { expect, test } from "@playwright/test";

import { openTenant, seed, signIn } from "./helpers";

test("phone layout: menu opens, no horizontal page scroll", async ({ browser }) => {
  const s = seed();
  const page = await signIn(browser, s.people.TENANT_ADMIN, "ar");
  await page.setViewportSize({ width: 390, height: 844 });
  await openTenant(page, s.tenant_id);
  const menu = page.getByRole("button", { name: "القائمة" });
  await expect(menu).toBeVisible();
  // Closed menu is out of the tab order and hidden from assistive technology.
  await expect(page.getByRole("link", { name: "رموز الغرض" })).toBeHidden();
  await menu.click();
  await page.getByRole("link", { name: "رموز الغرض" }).click();
  await expect(page.locator(".backdrop")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "رموز الغرض" })).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  await page.context().close();
});
