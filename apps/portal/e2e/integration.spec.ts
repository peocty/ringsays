import { expect, test } from "@playwright/test";

import { enterpriseToken, openTenant, seed, sendIntent, signIn } from "./helpers";

test("credentials are shown once and work; monitor shows masked intents live", async ({ browser }) => {
  const s = seed();
  const integ = await signIn(browser, s.people.INTEGRATION_ADMIN, "en");
  await openTenant(integ, s.tenant_id);
  // Separation of duties: integration admin has no people or audit pages.
  await expect(integ.getByRole("link", { name: "People and roles" })).toHaveCount(0);
  await integ.getByRole("link", { name: "Integration" }).click();
  await integ.getByRole("button", { name: "Create credentials" }).click();
  await integ.getByRole("dialog").getByLabel("Label").fill("Core banking e2e");
  await integ.getByRole("dialog").getByRole("button", { name: "Create" }).click();
  const secretEl = integ.getByTestId("secret-Client secret");
  await expect(secretEl).toBeVisible();
  const clientId = (await integ.getByTestId("secret-Client id").textContent())!.trim();
  const secret = (await secretEl.textContent())!.trim();
  await integ.getByRole("button", { name: "I have stored it" }).click();
  await expect(integ.getByText(secret)).toHaveCount(0);
  await expect(integ.getByRole("cell", { name: clientId })).toBeVisible();

  // Webhook endpoint, secret shown once.
  await integ.getByRole("tab", { name: "Webhooks" }).click();
  await integ.getByRole("button", { name: "Add endpoint" }).click();
  await integ.getByRole("dialog").getByLabel("Endpoint URL").fill("https://hooks.mockbank.example/ringsays");
  await integ.getByRole("dialog").getByRole("button", { name: "Add" }).click();
  await expect(integ.getByTestId("secret-RingSays-Signature")).toContainText("whsec_");
  await integ.getByRole("button", { name: "I have stored it" }).click();
  await integ.context().close();

  // The new credentials send an intent.
  const token = await enterpriseToken(clientId, secret);
  const phone = `+9665${String(Date.now()).slice(-8)}`;
  const intentId = await sendIntent(token, s.agent_id, phone);

  // Supervisor sees it live, with the number masked everywhere.
  const sup = await signIn(browser, s.people.SUPERVISOR, "en");
  await openTenant(sup, s.tenant_id);
  await sup.getByRole("link", { name: "Intent monitor" }).click();
  const masked = `${phone.slice(0, 5)}•••••${phone.slice(-3)}`;
  await expect(sup.getByTestId("intents-table").getByText(masked)).toBeVisible();
  expect(await sup.content()).not.toContain(phone);
  await sup.getByTestId("intents-table").getByText(masked).click();
  await expect(sup.getByRole("dialog").getByText(intentId)).toBeVisible();
  await expect(sup.getByRole("dialog").getByText("Requested").first()).toBeVisible();
  // Private phone search finds it.
  await sup.keyboard.press("Escape");
  await sup.getByLabel("Customer number").fill(phone);
  await sup.getByRole("button", { name: "Apply" }).click();
  await expect(sup.getByTestId("intents-table").locator("tbody tr")).toHaveCount(1);
  await sup.context().close();
});

test("agent sees only own intents and cannot open admin pages", async ({ browser }) => {
  const s = seed();
  const agent = await signIn(browser, s.people.AGENT, "en");
  await openTenant(agent, s.tenant_id);
  await expect(agent.getByRole("link", { name: "Intent monitor" })).toBeVisible();
  for (const name of ["Integration", "People and roles", "Audit log", "Verification"]) {
    await expect(agent.getByRole("link", { name })).toHaveCount(0);
  }
  await agent.goto(`/t/${s.tenant_id}/people`);
  await expect(agent).toHaveURL(new RegExp(`/t/${s.tenant_id}/overview$`));
  await agent.goto(`/t/${s.tenant_id}/monitor`);
  await expect(agent.getByText(`You see only intents sent as agent ${s.agent_id}.`)).toBeVisible();
  await agent.context().close();
});

test("other organisation is not reachable by URL", async ({ browser }) => {
  const s = seed();
  const admin = await signIn(browser, s.people.TENANT_ADMIN, "en");
  await admin.goto(`/t/00000000-0000-4000-8000-000000000000/overview`);
  await expect(admin.getByText("You do not have access to this organisation.")).toBeVisible();
  await admin.context().close();
});
