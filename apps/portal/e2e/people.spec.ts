import { expect, test } from "@playwright/test";

import { openTenant, seed, signIn } from "./helpers";

test("administrator invites a person, who signs in with the right role; language persists", async ({ browser }) => {
  const s = seed();
  const email = `analyst+${s.tag}@mockbank.example`;
  const admin = await signIn(browser, s.people.TENANT_ADMIN, "en");
  await openTenant(admin, s.tenant_id);
  await admin.getByRole("link", { name: "People and roles" }).click();
  await admin.getByRole("button", { name: "Invite person" }).click();
  const d = admin.getByRole("dialog");
  await d.getByLabel("Work email").fill(email);
  await d.getByRole("checkbox", { name: /Compliance/ }).check();
  await d.getByRole("button", { name: "Invite person" }).click();
  await expect(admin.getByRole("cell", { name: new RegExp(email.replace("+", "\\+")) })).toBeVisible();
  await expect(admin.getByText("Invited").first()).toBeVisible();

  // Switch to Arabic; it survives reload.
  await admin.getByTestId("lang-toggle").click();
  await expect(admin.locator("html")).toHaveAttribute("dir", "rtl");
  await admin.reload();
  await expect(admin.locator("html")).toHaveAttribute("lang", "ar");
  await expect(admin.getByRole("heading", { name: "المستخدمون والأدوار" })).toBeVisible();
  await admin.context().close();

  const analyst = await signIn(browser, email, "en");
  await openTenant(analyst, s.tenant_id);
  await expect(analyst.getByRole("link", { name: "Audit log" })).toBeVisible();
  await expect(analyst.getByRole("link", { name: "Integration" })).toHaveCount(0);
  await analyst.context().close();
});
