import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams } from "react-router";

import { api, unwrap } from "../api/client";
import { keys, useMe } from "../api/session";
import type { ReviewQueue, Sector, Tenant, VerificationReview } from "../api/types";
import { STATUS_TONE } from "../components/Shell";
import {
  Alert,
  Button,
  Card,
  Dialog,
  Empty,
  ErrorAlert,
  Field,
  Loading,
  LocalisedInputs,
  PageHeader,
  Pill,
  ReasonDialog,
  Tabs,
  useToast,
} from "../components/ui";
import { useLang } from "../i18n";
import { downloadAuthenticated } from "../lib/download";
import { formatBytes, formatDateTime, pick } from "../lib/format";

type Decision = "APPROVE" | "REJECT";

function useStaffRoles() {
  const me = useMe();
  const roles = me.data?.staff_roles ?? [];
  return { reviewer: roles.includes("RS_REVIEWER"), operator: roles.includes("RS_ADMIN") };
}

function DecisionButtons({ onDecide, disabled }: { onDecide: (d: Decision) => void; disabled?: boolean }) {
  const { t } = useTranslation();
  return (
    <>
      <Button variant="primary" disabled={disabled} onClick={() => onDecide("APPROVE")}>
        {t("backoffice.approve")}
      </Button>
      <Button variant="danger" disabled={disabled} onClick={() => onDecide("REJECT")}>
        {t("backoffice.reject")}
      </Button>
    </>
  );
}

export function ReviewQueuePage() {
  const { t } = useTranslation();
  const { lang } = useLang();
  const qc = useQueryClient();
  const { reviewer } = useStaffRoles();
  const [tab, setTab] = useState<"verifications" | "codes" | "numbers">("verifications");
  const queue = useQuery({ queryKey: keys.queue, queryFn: () => unwrap<ReviewQueue>(api.GET("/review/queue")) });
  const [pending, setPending] = useState<{ kind: "code" | "number"; decision: Decision; tenantId?: string; id: string; label: string } | null>(
    null,
  );
  const decide = useMutation({
    mutationFn: (reason: string): Promise<unknown> => {
      const p = pending!;
      return p.kind === "code"
        ? unwrap(
            api.POST("/review/purpose-codes/{tenant_id}/{code}", {
              params: { path: { tenant_id: p.tenantId!, code: p.id } },
              body: { decision: p.decision, reason },
            }),
          )
        : unwrap(
            api.POST("/review/calling-numbers/{number_id}", {
              params: { path: { number_id: p.id } },
              body: { decision: p.decision, reason },
            }),
          );
    },
    onSuccess: () => {
      setPending(null);
      void qc.invalidateQueries({ queryKey: keys.queue });
    },
  });
  const q = queue.data;
  return (
    <>
      <PageHeader
        title={t("backoffice.queueTitle")}
        actions={
          <Button variant="ghost" onClick={() => void queue.refetch()}>
            {t("common.refresh")}
          </Button>
        }
      />
      <Tabs
        prefix="queue"
        label={t("backoffice.queueTitle")}
        value={tab}
        onChange={setTab}
        tabs={[
          { id: "verifications", label: `${t("backoffice.tabVerifications")} (${q?.verifications.length ?? 0})` },
          { id: "codes", label: `${t("backoffice.tabCodes")} (${q?.purpose_codes.length ?? 0})` },
          { id: "numbers", label: `${t("backoffice.tabNumbers")} (${q?.calling_numbers.length ?? 0})` },
        ]}
      />
      <Card>
        <div role="tabpanel" id="queue-panel" aria-labelledby={`queue-tab-${tab}`}>
        {queue.isPending ? <Loading /> : null}
        <ErrorAlert error={queue.error} onRetry={() => void queue.refetch()} />
        {q && tab === "verifications" ? (
          q.verifications.length === 0 ? (
            <Empty>{t("backoffice.nothingWaiting")}</Empty>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>{t("backoffice.tenant")}</th>
                  <th>{t("organisation.sector")}</th>
                  <th>{t("backoffice.submitted")}</th>
                  <th className="actions-col">{t("common.actions")}</th>
                </tr>
              </thead>
              <tbody>
                {q.verifications.map((v) => (
                  <tr key={v.request_id}>
                    <td>{pick(v.legal_name, lang)}</td>
                    <td>{t(`sector.${v.sector}`)}</td>
                    <td>{formatDateTime(v.submitted_at, lang)}</td>
                    <td className="actions-col">
                      <Link className="btn btn-secondary" to={`/backoffice/verifications/${v.request_id}`}>
                        {t("backoffice.open")}
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
        ) : null}
        {q && tab === "codes" ? (
          q.purpose_codes.length === 0 ? (
            <Empty>{t("backoffice.nothingWaiting")}</Empty>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>{t("backoffice.tenant")}</th>
                    <th>{t("catalogue.code")}</th>
                    <th>{t("catalogue.displayEn")}</th>
                    <th>{t("catalogue.displayAr")}</th>
                    <th>{t("catalogue.maxPriority")}</th>
                    <th>{t("catalogue.maxDuration")}</th>
                    <th>{t("catalogue.channels")}</th>
                    <th className="actions-col">{t("common.actions")}</th>
                  </tr>
                </thead>
                <tbody>
                  {q.purpose_codes.map((c) => (
                    <tr key={`${c.tenant_id}-${c.code}`}>
                      <td>{pick(c.tenant_name, lang)}</td>
                      <td>
                        <code dir="ltr">{c.code}</code>
                      </td>
                      <td dir="ltr" lang="en">
                        {c.display_text.en}
                      </td>
                      <td dir="rtl" lang="ar">
                        {c.display_text.ar}
                      </td>
                      <td>{t(`priority.${c.max_priority}`)}</td>
                      <td>{c.max_duration_min}</td>
                      <td>{c.allowed_channels.map((ch) => t(`channel.${ch}`)).join(" · ")}</td>
                      <td className="actions-col">
                        <DecisionButtons
                          disabled={!reviewer}
                          onDecide={(d) => {
                            decide.reset();
                            setPending({ kind: "code", decision: d, tenantId: c.tenant_id, id: c.code, label: c.code });
                          }}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        ) : null}
        {q && tab === "numbers" ? (
          q.calling_numbers.length === 0 ? (
            <Empty>{t("backoffice.nothingWaiting")}</Empty>
          ) : (
            <>
              <Alert tone="info">{t("backoffice.checkCst")}</Alert>
              <table className="table">
                <thead>
                  <tr>
                    <th>{t("backoffice.tenant")}</th>
                    <th>{t("backoffice.fullNumber")}</th>
                    <th>{t("organisation.cstShort")}</th>
                    <th>{t("backoffice.submitted")}</th>
                    <th className="actions-col">{t("common.actions")}</th>
                  </tr>
                </thead>
                <tbody>
                  {q.calling_numbers.map((n) => (
                    <tr key={n.number_id}>
                      <td>{pick(n.tenant_name, lang)}</td>
                      <td dir="ltr" className="mono">
                        {n.phone}
                      </td>
                      <td>{n.cst_entity_name_registered ? t("common.yes") : t("common.no")}</td>
                      <td>{formatDateTime(n.created_at, lang)}</td>
                      <td className="actions-col">
                        <DecisionButtons
                          disabled={!reviewer}
                          onDecide={(d) => {
                            decide.reset();
                            setPending({ kind: "number", decision: d, id: n.number_id, label: n.phone });
                          }}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )
        ) : null}
        </div>
      </Card>
      <ReasonDialog
        open={pending !== null}
        title={pending?.decision === "APPROVE" ? t("backoffice.decisionTitleApprove") : t("backoffice.decisionTitleReject")}
        note={pending?.label}
        reasonLabel={t("backoffice.decisionReason")}
        confirmLabel={pending?.decision === "APPROVE" ? t("backoffice.approve") : t("backoffice.reject")}
        danger={pending?.decision === "REJECT"}
        onClose={() => setPending(null)}
        onConfirm={(r) => decide.mutate(r)}
        onOpen={decide.reset}
        busy={decide.isPending}
        error={decide.error}
      />
    </>
  );
}

export function VerificationReviewPage() {
  const { t } = useTranslation();
  const { lang } = useLang();
  const { requestId = "" } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const { reviewer } = useStaffRoles();
  const [decision, setDecision] = useState<Decision | null>(null);
  const review = useQuery({
    queryKey: keys.review(requestId),
    queryFn: () =>
      unwrap<VerificationReview>(api.GET("/review/verifications/{request_id}", { params: { path: { request_id: requestId } } })),
  });
  const decide = useMutation({
    mutationFn: (reason: string) =>
      unwrap(
        api.POST("/review/verifications/{request_id}", {
          params: { path: { request_id: requestId } },
          body: { decision: decision!, reason },
        }),
      ),
    onSuccess: () => {
      setDecision(null);
      void qc.invalidateQueries({ queryKey: keys.queue });
      void qc.invalidateQueries({ queryKey: keys.review(requestId) });
      toast.notify(t("backoffice.decisionRecorded"));
      navigate("/backoffice/queue");
    },
  });
  const download = useMutation({
    mutationFn: (doc: { document_id: string; file_name: string }) =>
      downloadAuthenticated(`/review/documents/${doc.document_id}/file`, doc.file_name),
  });
  if (review.isPending) return <Loading />;
  if (review.error) return <ErrorAlert error={review.error} />;
  const { request, tenant, documents } = review.data;
  const open = request.status === "SUBMITTED";
  return (
    <>
      <PageHeader
        title={t("backoffice.verificationTitle")}
        actions={
          <Link to="/backoffice/queue" className="btn btn-ghost">
            {t("common.back")}
          </Link>
        }
      />
      <Card title={t("backoffice.profile")}>
        <dl className="facts">
          <dt>{t("backoffice.legalNameEn")}</dt>
          <dd dir="ltr">{tenant.legal_name.en}</dd>
          <dt>{t("backoffice.legalNameAr")}</dt>
          <dd dir="rtl">{tenant.legal_name.ar}</dd>
          <dt>{t("organisation.cr")}</dt>
          <dd dir="ltr">{tenant.commercial_registration ?? "—"}</dd>
          <dt>{t("organisation.domain")}</dt>
          <dd dir="ltr">{tenant.domain ?? "—"}</dd>
          <dt>{t("organisation.sector")}</dt>
          <dd>{t(`sector.${tenant.sector}`)}</dd>
          <dt>{t("common.status")}</dt>
          <dd>
            <Pill tone={STATUS_TONE[tenant.verification_status] ?? "neutral"}>{t(`tenantStatus.${tenant.verification_status}`)}</Pill>
          </dd>
          <dt>{t("backoffice.submitted")}</dt>
          <dd>{formatDateTime(request.submitted_at, lang)}</dd>
        </dl>
      </Card>
      <Card title={t("backoffice.documents")}>
        <ErrorAlert error={download.error} />
        <table className="table">
          <thead>
            <tr>
              <th>{t("verification.kind")}</th>
              <th>{t("verification.reference")}</th>
              <th>{t("verification.file")}</th>
              <th>{t("verification.size")}</th>
              <th>SHA-256</th>
              <th className="actions-col">{t("common.actions")}</th>
            </tr>
          </thead>
          <tbody>
            {documents.map((d) => (
              <tr key={d.document_id}>
                <td>{t(`verification.kinds.${d.kind}`)}</td>
                <td dir="ltr">{d.reference ?? "—"}</td>
                <td dir="ltr">{d.file_name}</td>
                <td>{formatBytes(d.size_bytes, lang)}</td>
                <td>
                  <code dir="ltr" className="small" title={d.sha256}>
                    {d.sha256.slice(0, 12)}…
                  </code>
                </td>
                <td className="actions-col">
                  <Button variant="ghost" busy={download.isPending && download.variables?.document_id === d.document_id} onClick={() => download.mutate(d)}>
                    {t("common.download")}
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      <Card>
        {open ? (
          <div className="row end gap">
            <DecisionButtons disabled={!reviewer} onDecide={(d) => { decide.reset(); setDecision(d); }} />
          </div>
        ) : (
          <Alert tone="info">
            {t("backoffice.alreadyDecided")}: {t(`verification.requestStatus.${request.status}`)} · <span dir="auto">{request.decision_reason}</span>
          </Alert>
        )}
      </Card>
      <ReasonDialog
        open={decision !== null}
        title={decision === "APPROVE" ? t("backoffice.decisionTitleApprove") : t("backoffice.decisionTitleReject")}
        note={pick(tenant.legal_name, lang)}
        reasonLabel={t("backoffice.decisionReason")}
        confirmLabel={decision === "APPROVE" ? t("backoffice.approve") : t("backoffice.reject")}
        danger={decision === "REJECT"}
        onClose={() => setDecision(null)}
        onConfirm={(r) => decide.mutate(r)}
        busy={decide.isPending}
        error={decide.error}
      />
    </>
  );
}

const SECTORS: Sector[] = ["BANK", "INSURANCE", "FINANCE", "GOVERNMENT"];

export function TenantsPage() {
  const { t } = useTranslation();
  const { lang } = useLang();
  const qc = useQueryClient();
  const toast = useToast();
  const { operator } = useStaffRoles();
  const [status, setStatus] = useState<"" | "PENDING" | "VERIFIED" | "SUSPENDED">("");
  const tenants = useQuery({
    queryKey: keys.tenants(status),
    queryFn: async () =>
      (
        await unwrap<{ items: Tenant[] }>(
          api.GET("/backoffice/tenants", { params: { query: status ? { status } : {} } }),
        )
      ).items,
  });
  const blank = { legal_name: { en: "", ar: "" }, sector: "BANK" as Sector, domain: "", admin_email: "", admin_name: "" };
  const [form, setForm] = useState(blank);
  const [creating, setCreating] = useState(false);
  const [action, setAction] = useState<{ tenant: Tenant; kind: "suspend" | "reinstate" } | null>(null);
  const create = useMutation({
    mutationFn: () =>
      unwrap<Tenant>(
        api.POST("/backoffice/tenants", {
          body: {
            legal_name: form.legal_name,
            sector: form.sector,
            admin_email: form.admin_email,
            ...(form.domain ? { domain: form.domain } : {}),
            ...(form.admin_name ? { admin_name: form.admin_name } : {}),
          },
        }),
      ),
    onSuccess: () => {
      toast.notify(t("backoffice.createdNote", { email: form.admin_email }));
      setCreating(false);
      void qc.invalidateQueries({ queryKey: ["backoffice", "tenants"] });
    },
  });
  const act = useMutation({
    mutationFn: (reason: string) =>
      action!.kind === "suspend"
        ? unwrap(api.POST("/backoffice/tenants/{tenant_id}/suspend", { params: { path: { tenant_id: action!.tenant.tenant_id } }, body: { reason } }))
        : unwrap(api.POST("/backoffice/tenants/{tenant_id}/reinstate", { params: { path: { tenant_id: action!.tenant.tenant_id } }, body: { reason } })),
    onSuccess: () => {
      setAction(null);
      void qc.invalidateQueries({ queryKey: ["backoffice", "tenants"] });
    },
  });
  return (
    <>
      <PageHeader
        title={t("backoffice.tenantsTitle")}
        actions={
          operator ? (
            <Button
              variant="primary"
              onClick={() => {
                create.reset();
                setForm(blank);
                setCreating(true);
              }}
            >
              {t("backoffice.createTenant")}
            </Button>
          ) : null
        }
      />
      <Card>
        <div className="row gap">
          <Field label={t("common.status")}>
            {(p) => (
              <select {...p} value={status} onChange={(e) => setStatus(e.target.value as typeof status)}>
                <option value="">{t("common.all")}</option>
                {(["PENDING", "VERIFIED", "SUSPENDED"] as const).map((s) => (
                  <option key={s} value={s}>
                    {t(`tenantStatus.${s}`)}
                  </option>
                ))}
              </select>
            )}
          </Field>
        </div>
        {tenants.isPending ? <Loading /> : null}
        <ErrorAlert error={tenants.error} />
        {tenants.data?.length === 0 ? <Empty>{t("backoffice.nothingWaiting")}</Empty> : null}
        {tenants.data && tenants.data.length > 0 ? (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>{t("organisation.legalName")}</th>
                  <th>{t("organisation.sector")}</th>
                  <th>{t("organisation.domain")}</th>
                  <th>{t("backoffice.region")}</th>
                  <th>{t("common.status")}</th>
                  <th>{t("common.created")}</th>
                  <th className="actions-col">{t("common.actions")}</th>
                </tr>
              </thead>
              <tbody>
                {tenants.data.map((tn) => (
                  <tr key={tn.tenant_id}>
                    <td>{pick(tn.legal_name, lang)}</td>
                    <td>{t(`sector.${tn.sector}`)}</td>
                    <td dir="ltr">{tn.domain ?? "—"}</td>
                    <td>{tn.residency_region}</td>
                    <td>
                      <Pill tone={STATUS_TONE[tn.verification_status] ?? "neutral"}>{t(`tenantStatus.${tn.verification_status}`)}</Pill>
                      {tn.suspended_reason ? <div className="muted small" dir="auto">{tn.suspended_reason}</div> : null}
                    </td>
                    <td>{formatDateTime(tn.created_at, lang)}</td>
                    <td className="actions-col">
                      {operator ? (
                        tn.verification_status === "SUSPENDED" ? (
                          <Button variant="ghost" onClick={() => { act.reset(); setAction({ tenant: tn, kind: "reinstate" }); }}>
                            {t("backoffice.reinstate")}
                          </Button>
                        ) : (
                          <Button variant="ghost" onClick={() => { act.reset(); setAction({ tenant: tn, kind: "suspend" }); }}>
                            {t("backoffice.suspend")}
                          </Button>
                        )
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </Card>
      <Dialog open={creating} title={t("backoffice.createTenant")} onClose={() => setCreating(false)} wide>
        <form
          className="stack"
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
        >
          <LocalisedInputs
            labelEn={t("backoffice.legalNameEn")}
            labelAr={t("backoffice.legalNameAr")}
            value={form.legal_name}
            onChange={(v) => setForm({ ...form, legal_name: v })}
          />
          <div className="grid-2">
            <Field label={t("organisation.sector")}>
              {(p) => (
                <select {...p} value={form.sector} onChange={(e) => setForm({ ...form, sector: e.target.value as Sector })}>
                  {SECTORS.map((s) => (
                    <option key={s} value={s}>
                      {t(`sector.${s}`)}
                    </option>
                  ))}
                </select>
              )}
            </Field>
            <Field label={t("organisation.domain")} optional>
              {(p) => <input {...p} dir="ltr" value={form.domain} onChange={(e) => setForm({ ...form, domain: e.target.value.trim() })} />}
            </Field>
            <Field label={t("backoffice.adminEmail")}>
              {(p) => (
                <input {...p} type="email" dir="ltr" required value={form.admin_email} onChange={(e) => setForm({ ...form, admin_email: e.target.value.trim() })} />
              )}
            </Field>
            <Field label={t("backoffice.adminName")} optional>
              {(p) => <input {...p} maxLength={120} value={form.admin_name} onChange={(e) => setForm({ ...form, admin_name: e.target.value })} />}
            </Field>
          </div>
          <ErrorAlert error={create.error} />
          <div className="row end">
            <Button onClick={() => setCreating(false)}>{t("common.cancel")}</Button>
            <Button type="submit" variant="primary" busy={create.isPending}>
              {t("common.create")}
            </Button>
          </div>
        </form>
      </Dialog>
      <ReasonDialog
        open={action !== null}
        title={action?.kind === "suspend" ? t("backoffice.suspendTitle") : t("backoffice.reinstateTitle")}
        note={action?.kind === "suspend" ? t("backoffice.suspendNote") : action ? pick(action.tenant.legal_name, lang) : undefined}
        confirmLabel={action?.kind === "suspend" ? t("backoffice.suspend") : t("backoffice.reinstate")}
        danger={action?.kind === "suspend"}
        onClose={() => setAction(null)}
        onConfirm={(r) => act.mutate(r)}
        busy={act.isPending}
        error={act.error}
      />
    </>
  );
}

