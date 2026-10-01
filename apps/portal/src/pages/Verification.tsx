import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError, api, authHeaders, toProblem, unwrap } from "../api/client";
import { keys, useMembership } from "../api/session";
import type { DocumentKind, Verification as VerificationT, VerificationDocument } from "../api/types";
import { Alert, Button, Card, Empty, ErrorAlert, Field, Loading, PageHeader, Pill, useToast } from "../components/ui";
import { config } from "../config";
import { useLang } from "../i18n";
import { formatBytes, formatDateTime } from "../lib/format";
import { useTenant } from "./Overview";

const KINDS: DocumentKind[] = ["COMMERCIAL_REGISTRATION", "REGULATOR_LICENCE", "AUTHORISATION_LETTER", "DOMAIN_PROOF", "OTHER"];
const MAX_BYTES = 5 * 1024 * 1024;

async function uploadDocument(tenantId: string, kind: DocumentKind, reference: string, file: File): Promise<VerificationDocument> {
  const form = new FormData();
  form.set("kind", kind);
  if (reference) form.set("reference", reference);
  form.set("file", file, file.name);
  let res: Response;
  try {
    res = await fetch(`${config.apiBase}/admin/v1/tenants/${tenantId}/verification/documents`, {
      method: "POST",
      headers: await authHeaders(),
      body: form,
    });
  } catch {
    throw new ApiError({ status: 0, code: "network" });
  }
  const body: unknown = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(await toProblem(res, body));
  return body as VerificationDocument;
}

export function Verification() {
  const { t } = useTranslation();
  const { lang } = useLang();
  const m = useMembership();
  const tid = m.tenant_id;
  const path = { params: { path: { tenant_id: tid } } };
  const qc = useQueryClient();
  const toast = useToast();
  const tenant = useTenant(tid);
  const v = useQuery({
    queryKey: keys.verification(tid),
    queryFn: () => unwrap<VerificationT>(api.GET("/tenants/{tenant_id}/verification", path)),
  });
  const [kind, setKind] = useState<DocumentKind>("COMMERCIAL_REGISTRATION");
  const [reference, setReference] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: keys.verification(tid) });
    void qc.invalidateQueries({ queryKey: keys.tenant(tid) });
  };
  const upload = useMutation({
    mutationFn: () => uploadDocument(tid, kind, reference, file!),
    onSuccess: () => {
      setFile(null);
      setReference("");
      if (fileRef.current) fileRef.current.value = "";
      refresh();
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) =>
      unwrap(
        api.DELETE("/tenants/{tenant_id}/verification/documents/{document_id}", {
          params: { path: { tenant_id: tid, document_id: id } },
        }),
      ),
    onSuccess: refresh,
  });
  const submit = useMutation({
    mutationFn: () => unwrap(api.POST("/tenants/{tenant_id}/verification/submit", path)),
    onSuccess: () => {
      toast.notify(t("verification.submitted"));
      refresh();
      void qc.invalidateQueries({ queryKey: ["me"] });
    },
  });

  if (v.isPending || tenant.isPending) return <Loading />;
  if (v.error) return <ErrorAlert error={v.error} onRetry={() => void v.refetch()} />;
  const data = v.data;
  const latest = data.latest_request;
  const underReview = latest?.status === "SUBMITTED";
  const pending = data.tenant_status === "PENDING";
  const editable = pending && !underReview;
  const have = new Set(data.documents.map((d) => d.kind));
  const missing = data.required_kinds.filter((k) => !have.has(k));
  const profileMissing = !tenant.data?.commercial_registration || !tenant.data?.domain;

  return (
    <>
      <PageHeader title={t("verification.title")} intro={t("verification.intro")} />
      {data.tenant_status === "VERIFIED" ? <Alert tone="success">{t("verification.verifiedNote")}</Alert> : null}
      {latest ? (
        <Card title={t("verification.latest")}>
          <div className="row wrap gap">
            <Pill tone={latest.status === "APPROVED" ? "good" : latest.status === "REJECTED" ? "bad" : "info"}>
              {t(`verification.requestStatus.${latest.status}`)}
            </Pill>
            <span className="muted">{t("verification.submittedOn", { date: formatDateTime(latest.submitted_at, lang) })}</span>
            {latest.decided_at ? (
              <span className="muted">{t("verification.decidedOn", { date: formatDateTime(latest.decided_at, lang) })}</span>
            ) : null}
          </div>
          {latest.decision_reason ? (
            <p dir="auto" className="quote">
              {latest.decision_reason}
            </p>
          ) : null}
        </Card>
      ) : null}
      <Card title={t("verification.required")}>
        <ul className="checklist">
          {data.required_kinds.map((k) => (
            <li key={k} className={have.has(k) ? "done" : ""}>
              <span className="check" aria-hidden>
                {have.has(k) ? "✓" : ""}
              </span>
              <span className="grow">{t(`verification.kinds.${k}`)}</span>
              <Pill tone={have.has(k) ? "good" : "warn"}>{have.has(k) ? t("verification.provided") : t("verification.missing")}</Pill>
            </li>
          ))}
        </ul>
      </Card>
      {editable ? (
        <Card title={t("verification.upload")}>
          <form
            className="stack"
            onSubmit={(e) => {
              e.preventDefault();
              setLocalError(null);
              if (!file) return;
              if (file.size > MAX_BYTES) {
                setLocalError(t("verification.fileHint"));
                return;
              }
              upload.mutate();
            }}
          >
            <div className="grid-3">
              <Field label={t("verification.kind")}>
                {(p) => (
                  <select {...p} value={kind} onChange={(e) => setKind(e.target.value as DocumentKind)}>
                    {KINDS.map((k) => (
                      <option key={k} value={k}>
                        {t(`verification.kinds.${k}`)}
                      </option>
                    ))}
                  </select>
                )}
              </Field>
              <Field label={t("verification.reference")} optional>
                {(p) => <input {...p} dir="ltr" maxLength={80} value={reference} onChange={(e) => setReference(e.target.value)} />}
              </Field>
              <Field label={t("verification.file")} hint={t("verification.fileHint")} error={localError ?? undefined}>
                {(p) => (
                  <input
                    {...p}
                    ref={fileRef}
                    type="file"
                    required
                    accept="application/pdf,image/png,image/jpeg"
                    onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                  />
                )}
              </Field>
            </div>
            <ErrorAlert error={upload.error} />
            <div className="row end">
              <Button type="submit" variant="primary" busy={upload.isPending} disabled={!file}>
                {t("verification.upload")}
              </Button>
            </div>
          </form>
        </Card>
      ) : null}
      <Card title={t("verification.documents")}>
        {data.documents.length === 0 ? <Empty>{t("verification.noDocuments")}</Empty> : null}
        {data.documents.length > 0 ? (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>{t("verification.kind")}</th>
                  <th>{t("verification.file")}</th>
                  <th>{t("verification.reference")}</th>
                  <th>{t("verification.size")}</th>
                  <th>{t("common.created")}</th>
                  <th className="actions-col">{t("common.actions")}</th>
                </tr>
              </thead>
              <tbody>
                {data.documents.map((d) => (
                  <tr key={d.document_id}>
                    <td>{t(`verification.kinds.${d.kind}`)}</td>
                    <td dir="ltr">{d.file_name}</td>
                    <td dir="ltr">{d.reference ?? "—"}</td>
                    <td>{formatBytes(d.size_bytes, lang)}</td>
                    <td>{formatDateTime(d.uploaded_at, lang)}</td>
                    <td className="actions-col">
                      {editable ? (
                        <Button variant="ghost" busy={remove.isPending && remove.variables === d.document_id} onClick={() => remove.mutate(d.document_id)}>
                          {t("common.remove")}
                        </Button>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        <ErrorAlert error={remove.error} />
      </Card>
      {editable ? (
        <Card>
          <p className="muted">{t("verification.submitHint")}</p>
          {profileMissing ? <Alert tone="warn">{t("verification.profileMissing")}</Alert> : null}
          <ErrorAlert error={submit.error} />
          <div className="row end">
            <Button
              variant="primary"
              busy={submit.isPending}
              disabled={missing.length > 0 || profileMissing}
              onClick={() => submit.mutate()}
            >
              {t("verification.submit")}
            </Button>
          </div>
        </Card>
      ) : null}
    </>
  );
}
