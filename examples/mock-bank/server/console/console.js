// Mock Bank agent console. Plain browser JavaScript, no inline code (strict CSP); all text is set
// with textContent, never HTML.
"use strict";

const $ = (id) => document.getElementById(id);
const STATUS_TONE = {
  ACCEPTED: "good", SCHEDULED: "good", COMPLETED: "good", IN_PROGRESS: "good",
  RESCHEDULED: "warn", FOLLOW_UP_REQUIRED: "warn", REQUESTED: "", DELIVERED: "",
  DECLINED: "bad", EXPIRED: "bad", CANCELLED: "bad",
};
const STATUS_TEXT = {
  REQUESTED: "Waiting to open", DELIVERED: "Seen by customer", ACCEPTED: "Customer ready to talk",
  RESCHEDULED: "Customer proposed times", SCHEDULED: "Scheduled", IN_PROGRESS: "On the call",
  FOLLOW_UP_REQUIRED: "Follow up needed", COMPLETED: "Completed", DECLINED: "Declined",
  EXPIRED: "Expired", CANCELLED: "Cancelled",
};
let boot = null;
let timer = null;
let lastJson = "";

async function api(path, body) {
  const res = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: body === undefined ? {} : { "Content-Type": "application/json", "X-Console": "1" },
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: "same-origin",
  });
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (res.status === 401 && path !== "/console/login") showLogin();
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

function el(tag, props = {}, ...children) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") n.className = v;
    else if (k === "on") for (const [ev, fn] of Object.entries(v)) n.addEventListener(ev, fn);
    else if (k === "dataset") Object.assign(n.dataset, v);
    else n.setAttribute(k, v);
  }
  for (const c of children) if (c !== null && c !== undefined) n.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return n;
}

const fmt = (iso) => new Intl.DateTimeFormat("en-GB", { weekday: "short", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Riyadh" }).format(new Date(iso));

function showLogin() {
  clearInterval(timer);
  $("work").hidden = true;
  $("logout").hidden = true;
  $("login").hidden = false;
  $("password").focus();
}

async function start() {
  try {
    boot = await api("/console/api/bootstrap");
  } catch {
    return showLogin();
  }
  $("login").hidden = true;
  $("work").hidden = false;
  $("logout").hidden = false;
  fillForm();
  await refresh();
  clearInterval(timer);
  timer = setInterval(refresh, 3000);
}

function fillForm() {
  const customer = $("customer");
  customer.replaceChildren(...boot.customers.map((c) => el("option", { value: c.id }, `${c.name} (${c.phoneHint}, ${c.language === "ar" ? "Arabic" : "English"})`)));
  const purpose = $("purpose");
  purpose.replaceChildren(...boot.purposeCodes.map((p) => el("option", { value: p.code }, p.code)));
  purpose.addEventListener("change", onPurpose);
  onPurpose();
  renderSlots();
}

function onPurpose() {
  const p = boot.purposeCodes.find((x) => x.code === $("purpose").value);
  if (!p) return;
  $("purpose-text").textContent = `${p.display_text.en} · ${p.display_text.ar}`;
  const order = ["LOW", "NORMAL", "IMPORTANT", "URGENT"];
  const allowed = order.slice(0, order.indexOf(p.max_priority) + 1);
  $("priority").replaceChildren(...allowed.map((x) => el("option", { value: x }, x)));
  $("priority").value = allowed.includes("NORMAL") ? "NORMAL" : allowed[allowed.length - 1];
  const d = $("duration");
  d.max = String(p.max_duration_min);
  if (Number(d.value) > p.max_duration_min) d.value = String(p.max_duration_min);
}

/** Next working hour slots in Riyadh time, rounded to the half hour. */
function nextSlots() {
  const out = [];
  let t = Math.ceil((Date.now() + 45 * 60_000) / 1_800_000) * 1_800_000;
  while (out.length < 4) {
    const hour = Number(new Intl.DateTimeFormat("en-GB", { hour: "numeric", hourCycle: "h23", timeZone: "Asia/Riyadh" }).format(new Date(t)));
    if (hour >= 9 && hour < 17) out.push(t);
    t += 1_800_000;
  }
  return out;
}

function renderSlots() {
  $("slots").replaceChildren(
    ...nextSlots().map((t, i) =>
      el("label", { class: "chip" }, el("input", { type: "checkbox", value: String(t), id: `slot-${i}` }), fmt(new Date(t).toISOString())),
    ),
  );
}

async function send(e) {
  e.preventDefault();
  $("send-error").textContent = "";
  $("send").disabled = true;
  const minutes = Number($("duration").value);
  const offeredSlots = [...$("slots").querySelectorAll("input:checked")].slice(0, 3).map((i) => {
    const t = Number(i.value);
    return { start: new Date(t).toISOString(), end: new Date(t + minutes * 60_000).toISOString() };
  });
  try {
    await api("/console/api/intents", {
      customerId: $("customer").value,
      purposeCode: $("purpose").value,
      priority: $("priority").value,
      durationMin: minutes,
      maskedReference: $("reference").value || undefined,
      offeredSlots,
    });
    renderSlots();
    await refresh();
  } catch (err) {
    $("send-error").textContent = err.message;
  } finally {
    $("send").disabled = false;
  }
}

async function act(id, action, body) {
  $("action-error").textContent = "";
  try {
    await api(`/console/api/intents/${encodeURIComponent(id)}/${action}`, body ?? {});
    await refresh();
  } catch (err) {
    $("action-error").textContent = err.message;
  }
}

function actionsFor(i) {
  const b = (label, action, body, cls = "small secondary") =>
    el("button", { class: cls, type: "button", dataset: { action }, on: { click: () => act(i.intentId, action, body) } }, label);
  const out = [];
  const canCall = i.status === "ACCEPTED" || i.status === "SCHEDULED" || (i.status === "DELIVERED" && i.channelUsed === "PSTN");
  if (canCall) out.push(b("Call now", "calling", undefined, "small"));
  if (i.status === "RESCHEDULED") {
    for (const s of i.proposedSlots) out.push(b(`Confirm ${fmt(s.start)}`, "schedule", { slot: s }, "small"));
  }
  if (i.status === "IN_PROGRESS" || i.status === "FOLLOW_UP_REQUIRED") {
    const sel = el("select", { "aria-label": "Outcome", class: "outcome" }, ...boot.outcomes.map((o) => el("option", { value: o }, o)));
    out.push(sel, el("button", { class: "small", type: "button", dataset: { action: "outcome" }, on: { click: () => act(i.intentId, "outcome", { code: sel.value }) } }, "Record outcome"));
  }
  if (i.open && i.status !== "IN_PROGRESS" && i.status !== "FOLLOW_UP_REQUIRED") out.push(b("Cancel", "cancel", undefined, "small ghost"));
  out.push(b("Refresh", "refresh", undefined, "small ghost"));
  return out;
}

async function refresh() {
  let list;
  try {
    list = await api("/console/api/intents");
  } catch {
    return;
  }
  $("updated").textContent = `Updated ${new Intl.DateTimeFormat("en-GB", { timeStyle: "medium" }).format(new Date())}`;
  const json = JSON.stringify(list);
  if (json === lastJson) return; // unchanged: keep the table (and any open outcome choice) as it is
  lastJson = json;
  const label = (code) => boot.purposeCodes.find((p) => p.code === code)?.display_text.en ?? code;
  $("rows").replaceChildren(
    ...list.map((i) => {
      const when = i.scheduledSlot ? fmt(i.scheduledSlot.start) : i.status === "RESCHEDULED" ? i.proposedSlots.map((s) => fmt(s.start)).join(", ") : fmt(i.createdAt);
      const extra = [i.declineReason && `Reason: ${i.declineReason}`, i.outcomeCode && `Outcome: ${i.outcomeCode}`, i.channelUsed && `via ${i.channelUsed}`].filter(Boolean).join(" · ");
      return el(
        "tr",
        { dataset: { intent: i.intentId, status: i.status } },
        el("td", {}, i.customer?.name ?? i.customerId),
        el("td", {}, label(i.purposeCode), el("div", { class: "trail" }, `${i.priority} · ${i.durationMin} min`)),
        el("td", {}, el("span", { class: `status ${STATUS_TONE[i.status] ?? ""}`, dataset: { testid: "status" } }, STATUS_TEXT[i.status] ?? i.status), extra ? el("div", { class: "trail" }, extra) : null,
          el("div", { class: "trail" }, i.events.filter((e) => e.source === "webhook").length ? `${i.events.filter((e) => e.source === "webhook").length} webhook events` : "")),
        el("td", {}, when),
        el("td", {}, el("div", { class: "actions" }, ...actionsFor(i))),
      );
    }),
  );
  $("empty").hidden = list.length > 0;
}

document.addEventListener("DOMContentLoaded", () => {
  $("login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    $("login-error").textContent = "";
    try {
      await api("/console/login", { password: $("password").value });
      $("password").value = "";
      await start();
    } catch (err) {
      $("login-error").textContent = err.message;
    }
  });
  $("new-intent").addEventListener("submit", send);
  $("logout").addEventListener("click", async () => {
    await api("/console/logout", {}).catch(() => null);
    showLogin();
  });
  void start();
});
