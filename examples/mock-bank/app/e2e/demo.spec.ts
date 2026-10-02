// Recorded walkthrough for bank pitches (MOCK data). Runs only with DEMO=<folder>; examples/mock-bank/demo/run.sh
// then composes the two recordings side by side with captions (demo/compose.py).
import { mkdirSync, renameSync, writeFileSync } from "node:fs";
import path from "node:path";

import { devices, expect, test, type Browser, type Page } from "@playwright/test";

import { APP, CONSOLE, row, sendRequest, tick } from "./helpers";

const out = process.env.DEMO;
test.skip(!out, "set DEMO=<folder> to record");
test.setTimeout(300_000);

/** Click ring so viewers see where the agent and the customer tap (video has no pointer). */
const POINTER = `
addEventListener("pointerdown", (e) => {
  const d = document.createElement("div");
  Object.assign(d.style, { position: "fixed", left: e.clientX - 22 + "px", top: e.clientY - 22 + "px", width: "44px",
    height: "44px", borderRadius: "50%", border: "4px solid #f5a623", background: "rgba(245,166,35,.25)",
    pointerEvents: "none", zIndex: 2147483647, transition: "opacity .8s, transform .8s" });
  document.documentElement.appendChild(d);
  requestAnimationFrame(() => { d.style.opacity = "0"; d.style.transform = "scale(1.6)"; });
  setTimeout(() => d.remove(), 900);
}, true);`;

type Caption = { at: number; text: string };

test("demo", async ({ browser }) => {
  const dir = path.resolve(out!);
  const raw = path.join(dir, "raw");
  mkdirSync(raw, { recursive: true });

  const open = async (b: Browser, opts: Parameters<Browser["newContext"]>[0]) => {
    const ctx = await b.newContext({ ...opts, timezoneId: "Asia/Riyadh", bypassCSP: true });
    await ctx.addInitScript(POINTER);
    const page = await ctx.newPage();
    return { page, started: Date.now() };
  };
  const agentSize = { width: 1280, height: 800 };
  // Narrower than a Pixel 7 so the app's text stays readable once scaled into the frame.
  const phoneDevice = { ...devices["Pixel 7"], viewport: { width: 360, height: 780 }, deviceScaleFactor: 2 };
  const agent = await open(browser, { viewport: agentSize, recordVideo: { dir: raw, size: agentSize } });
  const phone = await open(browser, { ...phoneDevice, recordVideo: { dir: raw, size: phoneDevice.viewport } });
  const t0 = Date.now();
  const captions: Caption[] = [];
  const say = async (text: string, hold = 2500) => {
    captions.push({ at: (Date.now() - t0) / 1000, text });
    await agent.page.waitForTimeout(hold);
  };
  const a = agent.page;
  const p = phone.page;

  // Scene 0: both sides signed in.
  await a.goto(CONSOLE);
  await p.goto(APP);
  await say("Mock Bank, a fictional bank, integrated with RingSays: agent console on the left, the customer's own bank app on the right", 4500);
  await a.getByLabel("Console password").fill("e2e-console");
  await a.getByRole("button", { name: "Sign in" }).click();
  await expect(a.getByRole("heading", { name: "Ask a customer for a call" })).toBeVisible();
  await p.getByTestId("customer-cus_noura").click();
  await p.getByTestId("pin-input").pressSequentially("2468", { delay: 150 });
  await p.getByTestId("sign-in").click();
  await expect(p.getByTestId("hello")).toBeVisible();
  await say("Noura signs in to her bank app as she always does (Arabic). She has no calls waiting");

  // Scene 1: the agent asks for a call instead of cold calling.
  await say("Instead of cold calling, the agent asks for a call: an approved reason, a reference, two offered times", 1500);
  await a.getByLabel("Customer").selectOption("cus_noura");
  await a.waitForTimeout(700);
  await a.getByLabel("Reason (approved purpose)").selectOption("MORTGAGE.DOC.CLARIFY");
  await a.waitForTimeout(900);
  await a.getByLabel("Reference (last 4)").pressSequentially("8291", { delay: 180 });
  await a.locator("#slot-0").check();
  await a.waitForTimeout(400);
  await a.locator("#slot-1").check();
  await a.waitForTimeout(800);
  const first = await sendRequest(a, { customer: "cus_noura", purpose: "MORTGAGE.DOC.CLARIFY" });
  await expect(row(a, first).getByTestId("status")).toHaveText("Waiting to open");
  await say("RingSays checks the bank, the purpose and the limits, then gives the request to Noura's app only");

  // Scene 2: verified request on the phone.
  const card = p.getByTestId(`message-${first}`);
  await expect(card.getByTestId("ringsays-verified")).toBeVisible({ timeout: 20_000 });
  await say("Noura sees who is asking, why, for how long and the reference, verified by RingSays. Nothing to guess, nothing to fear", 6000);
  await say("She chooses one of the times the bank offered", 600);
  await card.getByTestId("ringsays-action-SCHEDULE").click();
  await p.waitForTimeout(3000);
  await card.getByRole("button").first().click();
  await expect(card.getByTestId("ringsays-outcome")).toBeVisible();
  await p.waitForTimeout(2500);

  // Scene 3: signed webhook back to the bank.
  tick();
  await expect(row(a, first).getByTestId("status")).toHaveText("Scheduled");
  await say("A signed webhook tells the bank at once: the call is booked for the time Noura chose", 4000);

  // Scene 4: second customer, English, talks now; the agent calls only after a yes.
  await p.getByTestId("sign-out").click();
  await p.getByTestId("customer-cus_priya").click();
  await p.getByTestId("pin-input").pressSequentially("2468", { delay: 120 });
  await p.getByTestId("sign-in").click();
  await expect(p.getByTestId("hello")).toBeVisible();
  await say("Next customer: Priya, in English", 1500);
  await a.getByLabel("Customer").selectOption("cus_priya");
  await a.waitForTimeout(600);
  await a.getByLabel("Reason (approved purpose)").selectOption("ACCOUNT.SERVICE.FOLLOWUP");
  await a.getByLabel("Reference (last 4)").fill("");
  await a.waitForTimeout(1200);
  const second = await sendRequest(a, { customer: "cus_priya", purpose: "ACCOUNT.SERVICE.FOLLOWUP" });
  const card2 = p.getByTestId(`message-${second}`);
  await expect(card2.getByTestId("ringsays-verified")).toBeVisible({ timeout: 20_000 });
  await say("A new request reaches Priya's app, in English this time", 3500);
  await say("Priya is free now, so she taps Talk now", 2000);
  await card2.getByTestId("ringsays-action-TALK_NOW").click();
  await expect(card2.getByTestId("ringsays-outcome")).toBeVisible();
  tick();
  const r = row(a, second);
  await expect(r.getByTestId("status")).toHaveText("Customer ready to talk");
  await say("The agent sees that Priya is ready and calls. A call that was expected, and answered", 3000);
  await r.getByRole("button", { name: "Call now" }).click();
  await expect(r.getByTestId("status")).toHaveText("On the call");
  await a.waitForTimeout(2000);
  await r.getByLabel("Outcome").selectOption("RESOLVED");
  await a.waitForTimeout(800);
  await r.getByRole("button", { name: "Record outcome" }).click();
  await expect(r.getByTestId("status")).toHaveText("Completed");
  await say("The outcome is recorded once, for the bank's records and RingSays audit evidence", 2000);
  await expect(p.getByTestId(`history-${second}`)).toContainText("Completed", { timeout: 20_000 });
  await p.getByTestId(`history-${second}`).scrollIntoViewIfNeeded();
  await say("Priya's app shows the request as completed. Every step was her choice", 4000);
  const end = (Date.now() - t0) / 1000;
  captions.push({ at: end, text: "" });

  const aVideo = a.video();
  const pVideo = p.video();
  await a.context().close();
  await p.context().close();
  renameSync(await aVideo!.path(), path.join(raw, "agent.webm"));
  renameSync(await pVideo!.path(), path.join(raw, "phone.webm"));
  writeFileSync(
    path.join(dir, "timeline.json"),
    JSON.stringify(
      {
        agentOffset: (t0 - agent.started) / 1000,
        phoneOffset: (t0 - phone.started) / 1000,
        duration: end,
        captions,
      },
      null,
      2,
    ),
  );
});
