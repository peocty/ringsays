import { expect, test } from "@playwright/test";

import { pdf, seed, signIn } from "./helpers";

/**
 * Whole onboarding in Arabic (right to left): RingSays operations onboards a bank, its first administrator
 * signs in, completes the profile, uploads evidence and submits; a reviewer approves; the bank is verified.
 */
test("onboard, verify and approve an organisation (Arabic)", async ({ browser }) => {
  const s = seed();
  const bankAdmin = `founder+${s.tag}@newbank.example`;
  const nameEn = `New Bank ${s.tag}`;

  // 1. Operations onboards the organisation.
  const ops = await signIn(browser, s.people.RS_ADMIN);
  await expect(ops.locator("html")).toHaveAttribute("dir", "rtl");
  await ops.goto("/backoffice/tenants");
  await ops.getByRole("button", { name: "إضافة منشأة" }).click();
  const dialog = ops.getByRole("dialog");
  await dialog.getByLabel("الاسم النظامي (بالإنجليزية)").fill(nameEn);
  await dialog.getByLabel("الاسم النظامي (بالعربية)").fill("بنك جديد");
  await dialog.getByLabel("بريد المسؤول الأول").fill(bankAdmin);
  await dialog.getByRole("button", { name: "إنشاء" }).click();
  await expect(ops.getByRole("cell", { name: "بنك جديد" }).first()).toBeVisible();
  await ops.context().close();

  // 2. First administrator signs in: invitation becomes membership, banner says not verified.
  const admin = await signIn(browser, bankAdmin);
  await expect(admin.getByText("لم يتم التحقق من منشأتك بعد")).toBeVisible();
  await expect(admin.getByText("بانتظار التحقق").first()).toBeVisible();
  const tenantUrl = admin.url().replace(/\/overview$/, "");

  // 3. Profile.
  await admin.getByRole("link", { name: "المنشأة" }).click();
  await admin.getByLabel("السجل التجاري").fill("1010987654");
  await admin.getByLabel("نطاق الموقع الإلكتروني").fill("newbank.example");
  await admin.getByRole("button", { name: "حفظ" }).click();
  await expect(admin.getByText("تم حفظ الملف")).toBeVisible();

  // 4. Evidence and submission.
  await admin.getByRole("link", { name: "التحقق" }).click();
  const submit = admin.getByRole("button", { name: "إرسال طلب التحقق" });
  await expect(submit).toBeDisabled();
  for (const [kind, file] of [
    ["COMMERCIAL_REGISTRATION", "cr.pdf"],
    ["REGULATOR_LICENCE", "sama.pdf"],
    ["AUTHORISATION_LETTER", "letter.pdf"],
  ] as const) {
    await admin.getByLabel("النوع").selectOption(kind);
    await admin.getByLabel("الملف").setInputFiles(pdf(file));
    await admin.getByRole("button", { name: "رفع مستند" }).click();
    await expect(admin.getByRole("cell", { name: file })).toBeVisible();
  }
  await submit.click();
  await expect(admin.getByText("قيد المراجعة").first()).toBeVisible();
  await expect(admin.getByRole("button", { name: "رفع مستند" })).toHaveCount(0); // locked while in review

  // 5. Reviewer approves with a reason.
  const reviewer = await signIn(browser, s.people.RS_REVIEWER, "en");
  await reviewer.goto("/backoffice/queue");
  const row = reviewer.getByRole("row", { name: new RegExp(nameEn) });
  await row.getByRole("link", { name: "Review" }).click();
  await expect(reviewer.getByRole("cell", { name: "sama.pdf" })).toBeVisible();
  await reviewer.getByRole("button", { name: "Approve" }).click();
  await reviewer.getByRole("dialog").getByLabel("Reason shown to the organisation").fill("CR and SAMA licence checked against registry");
  await reviewer.getByRole("dialog").getByRole("button", { name: "Approve" }).click();
  await expect(reviewer).toHaveURL(/\/backoffice\/queue$/);
  await reviewer.context().close();

  // 6. Bank sees verified status and the decision in its own audit log.
  await admin.goto(`${tenantUrl}/overview`);
  await expect(admin.getByText("منشأتك موثّقة، ويرى العملاء علامة التوثيق.")).toBeVisible();
  await admin.goto(`${tenantUrl}/audit`);
  await expect(admin.getByRole("cell", { name: "verification.approved" })).toBeVisible();
  await expect(admin.getByRole("cell", { name: "CR and SAMA licence checked against registry" })).toBeVisible();
  await admin.getByRole("button", { name: "فحص السلسلة" }).click();
  await expect(admin.getByText(/السلسلة سليمة/)).toBeVisible();
  await admin.context().close();
});
