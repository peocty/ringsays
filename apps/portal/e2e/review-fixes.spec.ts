import { expect, test } from "@playwright/test";

import { enterpriseToken, openTenant, seed, seedFresh, sendIntent, signIn } from "./helpers";

test("live refresh after Load more never skips rows", async ({ browser }) => {
  const s = seedFresh(`${seed().tag}page`);
  const token = await enterpriseToken(s.client_id, s.client_secret);
  for (let i = 0; i < 55; i += 1) await sendIntent(token, s.agent_id, `+96650${String(1000000 + i)}`);
  const sup = await signIn(browser, s.people.SUPERVISOR, "en");
  await openTenant(sup, s.tenant_id);
  await sup.getByRole("link", { name: "Intent monitor" }).click();
  const rows = sup.getByTestId("intents-table").locator("tbody tr");
  await expect(rows).toHaveCount(50);
  await sup.getByRole("button", { name: "Load more" }).click();
  await expect(rows).toHaveCount(55);
  for (let i = 0; i < 7; i += 1) await sendIntent(token, s.agent_id, `+96651${String(1000000 + i)}`);
  await expect(rows).toHaveCount(62, { timeout: 20_000 });
  await sup.context().close();
});

test("expired session keeps the page and typed input, then offers sign in", async ({ browser }) => {
  const s = seed();
  const admin = await signIn(browser, s.people.TENANT_ADMIN, "en");
  await openTenant(admin, s.tenant_id);
  await admin.getByRole("link", { name: "Organisation" }).click();
  await admin.getByRole("tab", { name: "Departments" }).click();
  await admin.getByRole("button", { name: "Add department" }).click();
  await admin.getByRole("dialog").getByLabel("Name (English)").fill("Treasury");
  await admin.getByRole("dialog").getByLabel("Name (Arabic)").fill("الخزينة");
  // Session rejected by the API (for example revoked at the identity provider).
  await admin.evaluate(() => {
    for (const k of Object.keys(sessionStorage)) {
      if (k.startsWith("oidc.user:")) {
        const u = JSON.parse(sessionStorage.getItem(k)!);
        u.access_token = "invalid";
        delete u.refresh_token;
        sessionStorage.setItem(k, JSON.stringify(u));
      }
    }
  });
  await admin.getByRole("dialog").getByRole("button", { name: "Save" }).click();
  await expect(admin.getByRole("alert").filter({ hasText: "Your sign in has expired" }).first()).toBeVisible();
  expect(admin.url()).toContain(`/t/${s.tenant_id}/organisation`);
  await expect(admin.getByRole("dialog").getByLabel("Name (English)")).toHaveValue("Treasury");
  await admin.context().close();
});

test("suspended organisation: changes hidden, containment still offered", async ({ browser }) => {
  const s = seedFresh(`${seed().tag}susp`);
  const ops = await signIn(browser, s.people.RS_ADMIN, "en");
  await ops.goto("/backoffice/tenants");
  const row = ops.getByRole("row", { name: /Mock Bank/ }).filter({ hasText: "Verified" }).first();
  await expect(row).toBeVisible();
  // Suspend this run's fresh tenant by id through the API with the staff session.
  const staffToken = await ops.evaluate(() => {
    const k = Object.keys(sessionStorage).find((x) => x.startsWith("oidc.user:"))!;
    return JSON.parse(sessionStorage.getItem(k)!).access_token as string;
  });
  const r = await fetch(`http://127.0.0.1:8000/admin/v1/backoffice/tenants/${s.tenant_id}/suspend`, {
    method: "POST",
    headers: { Authorization: `Bearer ${staffToken}`, "Content-Type": "application/json" },
    body: JSON.stringify({ reason: "e2e containment check" }),
  });
  expect(r.status).toBe(200);
  await ops.context().close();

  const integ = await signIn(browser, s.people.INTEGRATION_ADMIN, "en");
  await openTenant(integ, s.tenant_id);
  await expect(integ.getByText(/suspended: e2e containment check/)).toBeVisible();
  await integ.getByRole("link", { name: "Integration" }).click();
  await expect(integ.getByRole("button", { name: "Create credentials" })).toHaveCount(0);
  await expect(integ.getByRole("button", { name: "Revoke" }).first()).toBeVisible();
  await integ.context().close();
});
