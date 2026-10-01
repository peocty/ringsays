import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

import { openTenant, seed, signIn } from "./helpers";

for (const lang of ["ar", "en"] as const) {
  test(`no serious accessibility violations (${lang})`, async ({ browser }) => {
    const s = seed();
    const page = await signIn(browser, s.people.TENANT_ADMIN, lang);
    const cspErrors: string[] = [];
    page.on("console", (m) => {
      if (m.type() === "error" && /Content Security Policy/i.test(m.text())) cspErrors.push(m.text());
    });
    await openTenant(page, s.tenant_id);
    for (const path of ["overview", "organisation", "catalogue", "monitor", "audit", "people"]) {
      await page.goto(`/t/${s.tenant_id}/${path}`);
      await expect(page.locator("main h1")).toBeVisible();
      await page.waitForLoadState("networkidle");
      const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
      const serious = result.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
      expect(serious.map((v) => `${path}: ${v.id} ${v.nodes.length}`)).toEqual([]);
    }
    expect(cspErrors).toEqual([]);
    await page.context().close();
  });
}
