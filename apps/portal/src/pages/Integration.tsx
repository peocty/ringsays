import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { api, unwrap } from "../api/client";
import { keys, useCan, useCanChange, useMembership } from "../api/session";
import type {
  ApiClient,
  Scope,
  WebhookDelivery,
  WebhookDeliveryStatus,
  WebhookEndpoint,
  WebhookEventType,
} from "../api/types";
import {
  Button,
  Card,
  Dialog,
  Empty,
  ErrorAlert,
  Field,
  fieldErrors,
  Loading,
  PageHeader,
  Pill,
  ReasonDialog,
  SecretPanel,
  TabPanel,
  Tabs,
  useToast,
  type Tone,
} from "../components/ui";
import { useLang } from "../i18n";
import { formatDateTime } from "../lib/format";

const SCOPES: Scope[] = ["intents:write", "intents:read", "catalogue:read", "catalogue:write"];
const EVENTS: WebhookEventType[] = [
  "intent.delivered",
  "intent.accepted",
  "intent.rescheduled",
  "intent.scheduled",
  "intent.declined",
  "intent.expired",
  "intent.cancelled",
  "outcome.recorded",
];
const DELIVERY_TONE: Record<WebhookDeliveryStatus, Tone> = { PENDING: "info", SENDING: "info", DELIVERED: "good", DEAD: "bad" };

export function Integration() {
  const { t } = useTranslation();
  const [tab, setTab] = useState<"credentials" | "webhooks">("credentials");
  return (
    <>
      <PageHeader title={t("integration.title")} />
      <Tabs
        prefix="integration"
        label={t("integration.title")}
        value={tab}
        onChange={setTab}
        tabs={[
          { id: "credentials", label: t("integration.tabCredentials") },
          { id: "webhooks", label: t("integration.tabWebhooks") },
        ]}
      />
      <TabPanel prefix="integration" value={tab}>
        {tab === "credentials" ? <Credentials /> : <Webhooks />}
      </TabPanel>
    </>
  );
}

function Credentials() {
  const { t } = useTranslation();
  const { lang } = useLang();
  const m = useMembership();
  const can = useCan();
  const tid = m.tenant_id;
  const path = { params: { path: { tenant_id: tid } } };
  const qc = useQueryClient();
  const clients = useQuery({
    queryKey: keys.clients(tid),
    queryFn: async () => (await unwrap<{ items: ApiClient[] }>(api.GET("/tenants/{tenant_id}/api-clients", path))).items,
  });
  const [creating, setCreating] = useState(false);
  const [label, setLabel] = useState("");
  const [scopes, setScopes] = useState<Scope[]>(["intents:write", "intents:read", "catalogue:read"]);
  const [secret, setSecret] = useState<{ id: string; secret: string } | null>(null);
  const [revoking, setRevoking] = useState<ApiClient | null>(null);
  const create = useMutation({
    mutationFn: () =>
      unwrap<ApiClient & { client_secret: string }>(api.POST("/tenants/{tenant_id}/api-clients", { ...path, body: { label, scopes } })),
    onSuccess: (c) => {
      setCreating(false);
      setSecret({ id: c.client_id, secret: c.client_secret });
      void qc.invalidateQueries({ queryKey: keys.clients(tid) });
    },
  });
  const revoke = useMutation({
    mutationFn: (reason: string) =>
      unwrap(
        api.POST("/tenants/{tenant_id}/api-clients/{client_id}/revoke", {
          params: { path: { tenant_id: tid, client_id: revoking!.client_id } },
          body: { reason },
        }),
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.clients(tid) });
      setRevoking(null);
    },
  });
  const canChange = useCanChange();
  const manage = can("integration.manage"); // revoke stays allowed while suspended
  const canCreate = canChange("integration.manage");
  // catalogue:write needs a role that may propose purpose codes (the API enforces the same rule).
  const scopeChoices = SCOPES.filter((sc) => sc !== "catalogue:write" || can("catalogue.propose"));
  return (
    <Card
      actions={
        canCreate ? (
          <Button
            variant="primary"
            onClick={() => {
              create.reset();
              setLabel("");
              setCreating(true);
            }}
          >
            {t("integration.createCredential")}
          </Button>
        ) : null
      }
    >
      {clients.isPending ? <Loading /> : null}
      <ErrorAlert error={clients.error} />
      {clients.data?.length === 0 ? <Empty>{t("integration.noCredentials")}</Empty> : null}
      {clients.data && clients.data.length > 0 ? (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>{t("integration.label")}</th>
                <th>{t("integration.clientId")}</th>
                <th>{t("integration.scopes")}</th>
                <th>{t("common.created")}</th>
                <th>{t("common.status")}</th>
                <th className="actions-col">{t("common.actions")}</th>
              </tr>
            </thead>
            <tbody>
              {clients.data.map((c) => (
                <tr key={c.client_id}>
                  <td dir="auto">{c.label}</td>
                  <td>
                    <code dir="ltr">{c.client_id}</code>
                  </td>
                  <td>
                    {c.scopes.map((s) => (
                      <span key={s} className="tag" title={t(`integration.scope.${s}`)}>
                        <code dir="ltr">{s}</code>
                      </span>
                    ))}
                  </td>
                  <td>{formatDateTime(c.created_at, lang)}</td>
                  <td>
                    {c.active ? (
                      <Pill tone="good">{t("common.active")}</Pill>
                    ) : (
                      <Pill tone="neutral">{t("integration.revokedOn", { date: formatDateTime(c.revoked_at, lang) })}</Pill>
                    )}
                  </td>
                  <td className="actions-col">
                    {manage && c.active ? (
                      <Button variant="ghost" onClick={() => setRevoking(c)}>
                        {t("integration.revoke")}
                      </Button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      <Dialog open={creating} title={t("integration.createCredential")} onClose={() => setCreating(false)}>
        <form
          className="stack"
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
        >
          <Field label={t("integration.label")} hint={t("integration.labelHint")}>
            {(p) => <input {...p} required maxLength={80} value={label} onChange={(e) => setLabel(e.target.value)} />}
          </Field>
          <fieldset className="fieldset">
            <legend>{t("integration.scopes")}</legend>
            {scopeChoices.map((s) => (
              <label key={s} className="check-row">
                <input
                  type="checkbox"
                  checked={scopes.includes(s)}
                  onChange={(e) => setScopes(e.target.checked ? [...scopes, s] : scopes.filter((x) => x !== s))}
                />
                <span>
                  {t(`integration.scope.${s}`)} <code dir="ltr" className="muted small">{s}</code>
                </span>
              </label>
            ))}
          </fieldset>
          <ErrorAlert error={create.error} />
          <div className="row end">
            <Button onClick={() => setCreating(false)}>{t("common.cancel")}</Button>
            <Button type="submit" variant="primary" busy={create.isPending} disabled={scopes.length === 0 || !label.trim()}>
              {t("common.create")}
            </Button>
          </div>
        </form>
      </Dialog>
      {secret ? (
        <SecretPanel
          title={t("integration.secretTitle")}
          body={t("integration.secretBody")}
          items={[
            { label: t("integration.clientId"), value: secret.id },
            { label: t("integration.clientSecret"), value: secret.secret },
          ]}
          onDone={() => {
            setSecret(null);
            create.reset(); // drop the secret from the mutation cache too
          }}
        />
      ) : null}
      <ReasonDialog
        open={revoking !== null}
        title={t("integration.revokeTitle")}
        note={t("integration.revokeNote")}
        confirmLabel={t("integration.revoke")}
        danger
        onClose={() => setRevoking(null)}
        onConfirm={(r) => revoke.mutate(r)}
        onOpen={revoke.reset}
        busy={revoke.isPending}
        error={revoke.error}
      />
    </Card>
  );
}

function Webhooks() {
  const { t } = useTranslation();
  const { lang } = useLang();
  const m = useMembership();
  const can = useCan();
  const tid = m.tenant_id;
  const path = { params: { path: { tenant_id: tid } } };
  const qc = useQueryClient();
  const endpoints = useQuery({
    queryKey: keys.endpoints(tid),
    queryFn: async () =>
      (await unwrap<{ items: WebhookEndpoint[] }>(api.GET("/tenants/{tenant_id}/webhook-endpoints", path))).items,
  });
  const [adding, setAdding] = useState(false);
  const [url, setUrl] = useState("https://");
  const [events, setEvents] = useState<WebhookEventType[]>([...EVENTS]);
  const [secret, setSecret] = useState<string | null>(null);
  const [disabling, setDisabling] = useState<WebhookEndpoint | null>(null);
  const [viewing, setViewing] = useState<WebhookEndpoint | null>(null);
  const create = useMutation({
    mutationFn: () =>
      unwrap<WebhookEndpoint & { secret: string }>(
        api.POST("/tenants/{tenant_id}/webhook-endpoints", { ...path, body: { url, events } }),
      ),
    onSuccess: (e) => {
      setAdding(false);
      setSecret(e.secret);
      void qc.invalidateQueries({ queryKey: keys.endpoints(tid) });
    },
  });
  const disable = useMutation({
    mutationFn: (reason: string) =>
      unwrap(
        api.POST("/tenants/{tenant_id}/webhook-endpoints/{endpoint_id}/disable", {
          params: { path: { tenant_id: tid, endpoint_id: disabling!.endpoint_id } },
          body: { reason },
        }),
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.endpoints(tid) });
      setDisabling(null);
    },
  });
  const canChange = useCanChange();
  const manage = can("integration.manage"); // disable stays allowed while suspended
  const errs = fieldErrors(create.error);
  return (
    <Card
      actions={
        canChange("integration.manage") ? (
          <Button
            variant="primary"
            onClick={() => {
              create.reset();
              setUrl("https://");
              setEvents([...EVENTS]);
              setAdding(true);
            }}
          >
            {t("integration.addEndpoint")}
          </Button>
        ) : null
      }
    >
      {endpoints.isPending ? <Loading /> : null}
      <ErrorAlert error={endpoints.error} />
      {endpoints.data?.length === 0 ? <Empty>{t("integration.noEndpoints")}</Empty> : null}
      {endpoints.data && endpoints.data.length > 0 ? (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>{t("integration.url")}</th>
                <th>{t("integration.events")}</th>
                <th>{t("common.created")}</th>
                <th>{t("common.status")}</th>
                <th className="actions-col">{t("common.actions")}</th>
              </tr>
            </thead>
            <tbody>
              {endpoints.data.map((e) => (
                <tr key={e.endpoint_id}>
                  <td>
                    <code dir="ltr" className="break">
                      {e.url}
                    </code>
                  </td>
                  <td>{e.events.length === EVENTS.length ? t("common.all") : e.events.map((x) => t(`integration.event.${x}`)).join(" · ")}</td>
                  <td>{formatDateTime(e.created_at, lang)}</td>
                  <td>
                    <Pill tone={e.active ? "good" : "neutral"}>{e.active ? t("common.active") : t("common.inactive")}</Pill>
                  </td>
                  <td className="actions-col">
                    <Button variant="ghost" onClick={() => setViewing(e)}>
                      {t("integration.deliveries")}
                    </Button>
                    {manage && e.active ? (
                      <Button variant="ghost" onClick={() => setDisabling(e)}>
                        {t("integration.disable")}
                      </Button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      <Dialog open={adding} title={t("integration.addEndpoint")} onClose={() => setAdding(false)}>
        <form
          className="stack"
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
        >
          <Field label={t("integration.url")} hint={t("integration.urlHint")} error={errs.url}>
            {(p) => (
              <input {...p} type="url" dir="ltr" required pattern="https://.+" maxLength={2048} value={url} onChange={(e) => setUrl(e.target.value.trim())} />
            )}
          </Field>
          <fieldset className="fieldset">
            <legend>{t("integration.events")}</legend>
            {EVENTS.map((ev) => (
              <label key={ev} className="check-row">
                <input
                  type="checkbox"
                  checked={events.includes(ev)}
                  onChange={(e) => setEvents(e.target.checked ? [...events, ev] : events.filter((x) => x !== ev))}
                />
                <span>
                  {t(`integration.event.${ev}`)} <code dir="ltr" className="muted small">{ev}</code>
                </span>
              </label>
            ))}
          </fieldset>
          <ErrorAlert error={create.error} />
          <div className="row end">
            <Button onClick={() => setAdding(false)}>{t("common.cancel")}</Button>
            <Button type="submit" variant="primary" busy={create.isPending} disabled={events.length === 0}>
              {t("common.add")}
            </Button>
          </div>
        </form>
      </Dialog>
      {secret ? (
        <SecretPanel
          title={t("integration.secretTitle")}
          body={t("integration.signingSecretBody")}
          items={[{ label: "RingSays-Signature", value: secret }]}
          onDone={() => {
            setSecret(null);
            create.reset();
          }}
        />
      ) : null}
      <ReasonDialog
        open={disabling !== null}
        title={t("integration.disableTitle")}
        note={disabling?.url}
        confirmLabel={t("integration.disable")}
        danger
        onClose={() => setDisabling(null)}
        onConfirm={(r) => disable.mutate(r)}
        onOpen={disable.reset}
        busy={disable.isPending}
        error={disable.error}
      />
      {viewing ? (
        <DeliveryLog endpoint={viewing} onClose={() => setViewing(null)} canReplay={canChange("integration.manage")} />
      ) : null}
    </Card>
  );
}

function DeliveryLog({ endpoint, onClose, canReplay }: { endpoint: WebhookEndpoint; onClose: () => void; canReplay: boolean }) {
  const { t } = useTranslation();
  const { lang } = useLang();
  const m = useMembership();
  const tid = m.tenant_id;
  const qc = useQueryClient();
  const toast = useToast();
  const [status, setStatus] = useState<WebhookDeliveryStatus | "">("");
  type Page = { items: WebhookDelivery[]; next_cursor: string | null };
  // Keyed by filter, so a page fetched for an old filter can never show under a new one.
  const log = useInfiniteQuery({
    queryKey: keys.deliveries(tid, endpoint.endpoint_id, status),
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      unwrap<Page>(
        api.GET("/tenants/{tenant_id}/webhook-endpoints/{endpoint_id}/deliveries", {
          params: {
            path: { tenant_id: tid, endpoint_id: endpoint.endpoint_id },
            query: { limit: 50, ...(status ? { status } : {}), ...(pageParam ? { cursor: pageParam } : {}) },
          },
        }),
      ),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
  const replay = useMutation({
    mutationFn: (id: number) =>
      unwrap(
        api.POST("/tenants/{tenant_id}/webhook-deliveries/{delivery_id}/replay", {
          params: { path: { tenant_id: tid, delivery_id: id } },
        }),
      ),
    onSuccess: () => {
      toast.notify(t("integration.replayed"));
      void qc.invalidateQueries({ queryKey: ["tenant", tid, "deliveries", endpoint.endpoint_id] });
    },
  });
  const rows = log.data?.pages.flatMap((p) => p.items) ?? [];
  return (
    <Dialog open title={`${t("integration.deliveries")}`} onClose={onClose} wide>
      <p>
        <code dir="ltr" className="break">
          {endpoint.url}
        </code>
      </p>
      <div className="row gap">
        <Field label={t("common.status")}>
          {(p) => (
            <select
              {...p}
              value={status}
              onChange={(e) => setStatus(e.target.value as WebhookDeliveryStatus | "")}
            >
              <option value="">{t("common.all")}</option>
              {(["PENDING", "SENDING", "DELIVERED", "DEAD"] as const).map((s) => (
                <option key={s} value={s}>
                  {t(`integration.deliveryStatus.${s}`)}
                </option>
              ))}
            </select>
          )}
        </Field>
        <Button variant="ghost" onClick={() => void log.refetch()}>
          {t("common.refresh")}
        </Button>
      </div>
      {log.isPending ? <Loading /> : null}
      <ErrorAlert error={log.error ?? replay.error} />
      {log.data && rows.length === 0 ? <Empty>{t("integration.noDeliveries")}</Empty> : null}
      {rows.length > 0 ? (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>{t("integration.eventType")}</th>
                <th>{t("integration.intent")}</th>
                <th>{t("common.status")}</th>
                <th>{t("integration.attempts")}</th>
                <th>{t("integration.lastResult")}</th>
                <th>{t("common.created")}</th>
                <th className="actions-col">{t("common.actions")}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((d) => (
                <tr key={d.delivery_id}>
                  <td>{t(`integration.event.${d.event_type}`)}</td>
                  <td>
                    <code dir="ltr" className="small">
                      {d.intent_id.slice(0, 8)}
                    </code>
                  </td>
                  <td>
                    <Pill tone={DELIVERY_TONE[d.status]}>{t(`integration.deliveryStatus.${d.status}`)}</Pill>
                    {d.next_attempt_at && d.status === "PENDING" ? (
                      <div className="muted small">
                        {t("integration.nextAttempt")}: {formatDateTime(d.next_attempt_at, lang)}
                      </div>
                    ) : null}
                  </td>
                  <td>{d.attempts}</td>
                  <td dir="ltr" className="small">
                    {d.last_status_code ?? ""} {d.last_error ?? ""}
                  </td>
                  <td>{formatDateTime(d.created_at, lang)}</td>
                  <td className="actions-col">
                    {canReplay && (d.status === "DELIVERED" || d.status === "DEAD") && endpoint.active ? (
                      <Button variant="ghost" busy={replay.isPending && replay.variables === d.delivery_id} onClick={() => replay.mutate(d.delivery_id)}>
                        {t("integration.replay")}
                      </Button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {log.hasNextPage ? (
        <div className="row center">
          <Button busy={log.isFetchingNextPage} onClick={() => void log.fetchNextPage()}>
            {t("common.loadMore")}
          </Button>
        </div>
      ) : null}
    </Dialog>
  );
}
