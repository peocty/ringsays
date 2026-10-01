import { INTENT_STATUSES, type IntentStatus } from "@ringsays/domain";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { api, unwrap } from "../api/client";
import { keys, useCan, useMembership } from "../api/session";
import type { MonitorIntent, MonitorIntentDetail } from "../api/types";
import { Alert, Button, Card, Dialog, Empty, ErrorAlert, Field, Loading, PageHeader, Pill, type Tone } from "../components/ui";
import { config } from "../config";
import { useLang } from "../i18n";
import { formatDateTime } from "../lib/format";

const STATUS_TONE: Partial<Record<IntentStatus, Tone>> = {
  REQUESTED: "info",
  DELIVERED: "info",
  ACCEPTED: "good",
  SCHEDULED: "good",
  RESCHEDULED: "warn",
  IN_PROGRESS: "good",
  COMPLETED: "good",
  FOLLOW_UP_REQUIRED: "warn",
  DECLINED: "bad",
  EXPIRED: "neutral",
  CANCELLED: "neutral",
};
const FILTERABLE = INTENT_STATUSES.filter((s) => s !== "DRAFT");

interface Filters {
  status: IntentStatus[];
  purpose_code: string;
  agent_id: string;
  phone: string;
  created_from: string;
  created_to: string;
}
const EMPTY: Filters = { status: [], purpose_code: "", agent_id: "", phone: "", created_from: "", created_to: "" };
type Page = { items: MonitorIntent[]; next_cursor: string | null };

function toQuery(f: Filters, cursor?: string) {
  return {
    limit: 50,
    ...(f.status.length ? { status: f.status } : {}),
    ...(f.purpose_code ? { purpose_code: f.purpose_code } : {}),
    ...(f.agent_id ? { agent_id: f.agent_id } : {}),
    ...(f.phone ? { phone: f.phone } : {}),
    ...(f.created_from ? { created_from: new Date(f.created_from).toISOString() } : {}),
    ...(f.created_to ? { created_to: new Date(f.created_to).toISOString() } : {}),
    ...(cursor ? { cursor } : {}),
  };
}

export function Monitor() {
  const { t } = useTranslation();
  const { lang } = useLang();
  const m = useMembership();
  const can = useCan();
  const tid = m.tenant_id;
  const ownOnly = !can("intents.read_all");
  const [draft, setDraft] = useState<Filters>(EMPTY);
  const [filters, setFilters] = useState<Filters>(EMPTY);
  const [live, setLive] = useState(true);
  const [older, setOlder] = useState<Page[]>([]);
  const [selected, setSelected] = useState<string | null>(null);

  const fetchPage = (cursor?: string) =>
    unwrap<Page>(
      api.GET("/tenants/{tenant_id}/intents", {
        params: { path: { tenant_id: tid }, query: toQuery(filters, cursor) },
        querySerializer: { array: { style: "form", explode: false } },
      }),
    );
  const first = useQuery({
    queryKey: keys.intents(tid, filters),
    queryFn: () => fetchPage(),
    refetchInterval: live ? config.monitorRefreshMs : false,
  });
  const more = useMutation({
    mutationFn: (cursor: string) => fetchPage(cursor),
    onSuccess: (p) => setOlder((xs) => [...xs, p]),
  });
  const apply = (e: FormEvent) => {
    e.preventDefault();
    setOlder([]);
    setFilters(draft);
  };
  // Live refresh replaces the newest page; older pages stay until filters change.
  const seen = new Set<string>();
  const rows = [first.data?.items ?? [], ...older.map((p) => p.items)].flat().filter((i) => {
    if (seen.has(i.intent_id)) return false;
    seen.add(i.intent_id);
    return true;
  });
  const lastCursor = older.length ? older[older.length - 1]!.next_cursor : (first.data?.next_cursor ?? null);

  return (
    <>
      <PageHeader
        title={t("monitor.title")}
        actions={
          <div className="row gap">
            <Pill tone={live ? "good" : "neutral"}>
              <span className={live ? "live-dot" : ""} aria-hidden /> {live ? t("monitor.live") : t("monitor.paused")}
            </Pill>
            <Button variant="ghost" onClick={() => setLive((v) => !v)}>
              {live ? t("monitor.pause") : t("monitor.resume")}
            </Button>
          </div>
        }
      />
      {ownOnly ? <Alert tone="info">{t("monitor.ownOnly", { agent: m.agent_id ?? "—" })}</Alert> : null}
      <Card>
        <form className="filters" onSubmit={apply} aria-label={t("common.apply")}>
          <Field label={t("monitor.status")}>
            {(p) => (
              <select
                {...p}
                value={draft.status[0] ?? ""}
                onChange={(e) => setDraft({ ...draft, status: e.target.value ? [e.target.value as IntentStatus] : [] })}
              >
                <option value="">{t("common.all")}</option>
                {FILTERABLE.map((s) => (
                  <option key={s} value={s}>
                    {t(`monitor.intentStatus.${s}`)}
                  </option>
                ))}
              </select>
            )}
          </Field>
          <Field label={t("monitor.purpose")}>
            {(p) => <input {...p} dir="ltr" value={draft.purpose_code} onChange={(e) => setDraft({ ...draft, purpose_code: e.target.value.trim().toUpperCase() })} />}
          </Field>
          {!ownOnly ? (
            <Field label={t("monitor.agent")}>
              {(p) => <input {...p} dir="ltr" value={draft.agent_id} onChange={(e) => setDraft({ ...draft, agent_id: e.target.value.trim() })} />}
            </Field>
          ) : null}
          <Field label={t("monitor.phone")} hint={t("monitor.phoneHint")}>
            {(p) => (
              <input
                {...p}
                dir="ltr"
                type="tel"
                pattern="\+[1-9][0-9]{6,14}"
                autoComplete="off"
                value={draft.phone}
                onChange={(e) => setDraft({ ...draft, phone: e.target.value.replace(/\s/g, "") })}
              />
            )}
          </Field>
          <Field label={t("monitor.from")}>
            {(p) => <input {...p} type="datetime-local" value={draft.created_from} onChange={(e) => setDraft({ ...draft, created_from: e.target.value })} />}
          </Field>
          <Field label={t("monitor.to")}>
            {(p) => <input {...p} type="datetime-local" value={draft.created_to} onChange={(e) => setDraft({ ...draft, created_to: e.target.value })} />}
          </Field>
          <div className="filters-actions">
            <Button type="submit" variant="primary">
              {t("common.apply")}
            </Button>
            <Button
              onClick={() => {
                setDraft(EMPTY);
                setOlder([]);
                setFilters(EMPTY);
              }}
            >
              {t("common.reset")}
            </Button>
          </div>
        </form>
      </Card>
      <Card>
        {first.isPending ? <Loading /> : null}
        <ErrorAlert error={first.error ?? more.error} onRetry={() => void first.refetch()} />
        {first.data && rows.length === 0 ? <Empty>{t("monitor.noIntents")}</Empty> : null}
        {rows.length > 0 ? (
          <div className="table-wrap">
            <table className="table" data-testid="intents-table">
              <thead>
                <tr>
                  <th>{t("monitor.created")}</th>
                  <th>{t("monitor.recipient")}</th>
                  <th>{t("monitor.purpose")}</th>
                  <th>{t("monitor.agent")}</th>
                  <th>{t("monitor.priority")}</th>
                  <th>{t("monitor.status")}</th>
                  <th>{t("monitor.channel")}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((i) => (
                  <tr key={i.intent_id} className="clickable" onClick={() => setSelected(i.intent_id)}>
                    <td>
                      <button type="button" className="link" onClick={() => setSelected(i.intent_id)}>
                        {formatDateTime(i.created_at, lang)}
                      </button>
                    </td>
                    <td>
                      <span dir="ltr" className="mono">
                        {i.to_masked}
                      </span>
                    </td>
                    <td>
                      <code dir="ltr">{i.purpose_code ?? "—"}</code>
                    </td>
                    <td>
                      <code dir="ltr">{i.agent_id ?? "—"}</code>
                    </td>
                    <td>{t(`priority.${i.priority}`)}</td>
                    <td>
                      <Pill tone={STATUS_TONE[i.status] ?? "neutral"}>{t(`monitor.intentStatus.${i.status}`)}</Pill>
                    </td>
                    <td>{t(`channel.${i.channel_used ?? "NONE"}`)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {lastCursor ? (
          <div className="row center">
            <Button busy={more.isPending} onClick={() => more.mutate(lastCursor)}>
              {t("common.loadMore")}
            </Button>
          </div>
        ) : null}
      </Card>
      {selected ? <IntentDetail intentId={selected} onClose={() => setSelected(null)} live={live} /> : null}
    </>
  );
}

function IntentDetail({ intentId, onClose, live }: { intentId: string; onClose: () => void; live: boolean }) {
  const { t } = useTranslation();
  const { lang } = useLang();
  const m = useMembership();
  const q = useQuery({
    queryKey: keys.intent(m.tenant_id, intentId),
    queryFn: () =>
      unwrap<MonitorIntentDetail>(
        api.GET("/tenants/{tenant_id}/intents/{intent_id}", {
          params: { path: { tenant_id: m.tenant_id, intent_id: intentId } },
        }),
      ),
    refetchInterval: live ? config.monitorRefreshMs : false,
  });
  const d = q.data;
  return (
    <Dialog open title={t("monitor.detailTitle")} onClose={onClose} wide>
      {q.isPending ? <Loading /> : null}
      <ErrorAlert error={q.error} />
      {d ? (
        <div className="stack">
          <dl className="facts">
            <dt>{t("monitor.status")}</dt>
            <dd>
              <Pill tone={STATUS_TONE[d.status] ?? "neutral"}>{t(`monitor.intentStatus.${d.status}`)}</Pill>
            </dd>
            <dt>{t("monitor.recipient")}</dt>
            <dd dir="ltr" className="mono">
              {d.to_masked}
            </dd>
            <dt>{t("monitor.purpose")}</dt>
            <dd>
              <code dir="ltr">{d.purpose_code}</code>
            </dd>
            <dt>{t("monitor.reference")}</dt>
            <dd dir="ltr">{d.masked_reference ? `••${d.masked_reference}` : "—"}</dd>
            <dt>{t("monitor.verification")}</dt>
            <dd>{t(`monitor.level.${d.verification_level}`)}</dd>
            <dt>{t("monitor.validity")}</dt>
            <dd>
              {formatDateTime(d.valid_from, lang)} → {formatDateTime(d.valid_until, lang)}
            </dd>
            {d.scheduled_slot ? (
              <>
                <dt>{t("monitor.scheduled")}</dt>
                <dd>{formatDateTime(d.scheduled_slot.start, lang)}</dd>
              </>
            ) : null}
            {d.proposed_slots && d.proposed_slots.length > 0 ? (
              <>
                <dt>{t("monitor.proposed")}</dt>
                <dd>{d.proposed_slots.map((s) => formatDateTime(s.start, lang)).join(" · ")}</dd>
              </>
            ) : null}
            {d.decline_reason ? (
              <>
                <dt>{t("monitor.declineReason")}</dt>
                <dd>
                  <code dir="ltr">{d.decline_reason}</code>
                </dd>
              </>
            ) : null}
            {d.outcome_code ? (
              <>
                <dt>{t("monitor.outcome")}</dt>
                <dd>
                  <code dir="ltr">{d.outcome_code}</code>
                </dd>
              </>
            ) : null}
          </dl>
          <h3>{t("monitor.timeline")}</h3>
          <ol className="timeline">
            {d.events.map((e, idx) => (
              <li key={idx}>
                <span className="muted">{formatDateTime(e.at, lang)}</span>{" "}
                <strong>{t(`monitor.intentStatus.${e.to_status}`)}</strong>{" "}
                <span className="muted">· {t(`monitor.actor.${e.actor}`)}</span>
              </li>
            ))}
          </ol>
          {d.delivery_attempts.length > 0 ? (
            <>
              <h3>{t("monitor.attempts")}</h3>
              <ul className="plain">
                {d.delivery_attempts.map((a, idx) => (
                  <li key={idx}>
                    <span className="muted">{formatDateTime(a.at, lang)}</span> {t(`channel.${a.channel}`, { defaultValue: a.channel })}{" "}
                    <code dir="ltr">{a.outcome}</code> {a.reason ? <span className="muted small" dir="ltr">{a.reason}</span> : null}
                  </li>
                ))}
              </ul>
            </>
          ) : null}
          {d.webhooks.length > 0 ? (
            <>
              <h3>{t("monitor.webhooks")}</h3>
              <ul className="plain">
                {d.webhooks.map((w) => (
                  <li key={w.delivery_id}>
                    {t(`integration.event.${w.event_type}`)} · {t(`integration.deliveryStatus.${w.status}`)} · {w.attempts}
                  </li>
                ))}
              </ul>
            </>
          ) : null}
          <p className="muted small" dir="ltr">
            {d.intent_id}
          </p>
        </div>
      ) : null}
    </Dialog>
  );
}
