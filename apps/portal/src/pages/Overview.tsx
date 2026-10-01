import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { api, unwrap } from "../api/client";
import { keys, useCan, useMembership } from "../api/session";
import type {
  AdminPurposeCode,
  ApiClient,
  CallingNumber,
  MonitorSummary,
  Tenant,
  Verification,
  WebhookEndpoint,
} from "../api/types";
import { Alert, Card, ErrorAlert, Loading, PageHeader, Pill } from "../components/ui";
import { useLang } from "../i18n";
import { formatNumber } from "../lib/format";

export function useTenant(tenantId: string) {
  return useQuery({
    queryKey: keys.tenant(tenantId),
    queryFn: () => unwrap<Tenant>(api.GET("/tenants/{tenant_id}", { params: { path: { tenant_id: tenantId } } })),
  });
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="stat">
      <span className="stat-value">{value}</span>
      <span className="stat-label">{label}</span>
    </div>
  );
}

export function Overview() {
  const { t } = useTranslation();
  const { lang } = useLang();
  const m = useMembership();
  const can = useCan();
  const tid = m.tenant_id;
  const path = { params: { path: { tenant_id: tid } } };
  const tenant = useTenant(tid);
  const seesIntents = can("intents.read_all") || can("intents.read_own");
  const summary = useQuery({
    queryKey: keys.summary(tid),
    queryFn: () => unwrap<MonitorSummary>(api.GET("/tenants/{tenant_id}/intents/summary", path)),
    enabled: seesIntents,
    refetchInterval: 30_000,
  });
  const verification = useQuery({
    queryKey: keys.verification(tid),
    queryFn: () => unwrap<Verification>(api.GET("/tenants/{tenant_id}/verification", path)),
    enabled: can("org.manage"),
  });
  const numbers = useQuery({
    queryKey: keys.numbers(tid),
    queryFn: async () =>
      (await unwrap<{ items: CallingNumber[] }>(api.GET("/tenants/{tenant_id}/calling-numbers", path))).items,
  });
  const codes = useQuery({
    queryKey: keys.codes(tid),
    queryFn: async () =>
      (await unwrap<{ items: AdminPurposeCode[] }>(api.GET("/tenants/{tenant_id}/purpose-codes", path))).items,
  });
  const clients = useQuery({
    queryKey: keys.clients(tid),
    queryFn: async () => (await unwrap<{ items: ApiClient[] }>(api.GET("/tenants/{tenant_id}/api-clients", path))).items,
    enabled: can("integration.read"),
  });
  const endpoints = useQuery({
    queryKey: keys.endpoints(tid),
    queryFn: async () =>
      (await unwrap<{ items: WebhookEndpoint[] }>(api.GET("/tenants/{tenant_id}/webhook-endpoints", path))).items,
    enabled: can("integration.read"),
  });

  if (tenant.isPending) return <Loading />;
  if (tenant.error) return <ErrorAlert error={tenant.error} onRetry={() => void tenant.refetch()} />;
  const status = tenant.data.verification_status;
  const underReview = verification.data?.latest_request?.status === "SUBMITTED";
  const s = summary.data;
  const count = (...ks: string[]) => ks.reduce((n, k) => n + (s?.by_status[k] ?? 0), 0);

  const base = `/t/${tid}`;
  const checklist = [
    {
      label: t("overview.checkProfile"),
      done: status === "VERIFIED" || Boolean(tenant.data.commercial_registration && tenant.data.domain),
      to: `${base}/organisation`,
      show: can("org.manage"),
    },
    {
      label: t("overview.checkEvidence"),
      done: (verification.data?.documents.length ?? 0) > 0 || status === "VERIFIED",
      to: `${base}/verification`,
      show: can("org.manage"),
    },
    { label: t("overview.checkVerified"), done: status === "VERIFIED", to: `${base}/verification`, show: can("org.manage") },
    {
      label: t("overview.checkNumbers"),
      done: (numbers.data ?? []).some((n) => n.status === "VERIFIED"),
      to: `${base}/organisation`,
      show: can("org.manage"),
    },
    {
      label: t("overview.checkCodes"),
      done: (codes.data ?? []).some((c) => c.status === "APPROVED"),
      to: `${base}/catalogue`,
      show: true,
    },
    {
      label: t("overview.checkCredentials"),
      done: (clients.data ?? []).some((c) => c.active),
      to: `${base}/integration`,
      show: can("integration.read"),
    },
    {
      label: t("overview.checkWebhook"),
      done: (endpoints.data ?? []).some((e) => e.active),
      to: `${base}/integration`,
      show: can("integration.read"),
    },
  ].filter((c) => c.show);

  return (
    <>
      <PageHeader title={t("overview.title")} />
      {status === "SUSPENDED" ? (
        <Alert tone="error">{t("overview.bannerSuspended", { reason: tenant.data.suspended_reason ?? "" })}</Alert>
      ) : status === "PENDING" ? (
        <Alert tone="warn">{underReview ? t("overview.bannerSubmitted") : t("overview.bannerPending")}</Alert>
      ) : (
        <Alert tone="success">{t("overview.bannerVerified")}</Alert>
      )}
      {seesIntents ? (
        <Card title={t("overview.statsTitle")}>
          {summary.error ? <ErrorAlert error={summary.error} /> : null}
          <div className="stats" data-testid="stats">
            <Stat label={t("overview.total")} value={formatNumber(s?.total ?? 0, lang)} />
            <Stat
              label={t("overview.answered")}
              value={formatNumber(count("ACCEPTED", "SCHEDULED", "RESCHEDULED", "IN_PROGRESS", "COMPLETED", "FOLLOW_UP_REQUIRED"), lang)}
            />
            <Stat label={t("overview.declined")} value={formatNumber(count("DECLINED"), lang)} />
            <Stat label={t("overview.expired")} value={formatNumber(count("EXPIRED"), lang)} />
          </div>
        </Card>
      ) : null}
      <Card title={t("overview.checklistTitle")}>
        <ul className="checklist">
          {checklist.map((c) => (
            <li key={c.label} className={c.done ? "done" : ""}>
              <span className="check" aria-hidden>
                {c.done ? "✓" : ""}
              </span>
              <span className="grow">{c.label}</span>
              <Pill tone={c.done ? "good" : "warn"}>{c.done ? t("overview.done") : t("overview.todo")}</Pill>
              <Link to={c.to}>{t("overview.open")}</Link>
            </li>
          ))}
        </ul>
      </Card>
    </>
  );
}
