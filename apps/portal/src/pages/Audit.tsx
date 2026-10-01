import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { api, unwrap } from "../api/client";
import { keys, useMembership } from "../api/session";
import type { AuditEvent } from "../api/types";
import { Alert, Button, Card, Empty, ErrorAlert, Field, Loading, PageHeader } from "../components/ui";
import { useLang } from "../i18n";
import { downloadAuthenticated } from "../lib/download";
import { formatDateTime } from "../lib/format";

type Page = { items: AuditEvent[]; next_cursor: string | null };

/** Actor ids are opaque references; show their kind in words. */
function actorLabel(actor: string, t: (k: string) => string): string {
  if (actor.startsWith("staff:")) return `RingSays · ${actor.slice(6, 14)}`;
  if (actor.startsWith("portal:")) return `${t("nav.people")} · ${actor.slice(7, 15)}`;
  if (actor.startsWith("client:")) return `API · ${actor.slice(7)}`;
  return actor;
}

export function Audit() {
  const { t } = useTranslation();
  const { lang } = useLang();
  const m = useMembership();
  const tid = m.tenant_id;
  const [action, setAction] = useState("");
  const [applied, setApplied] = useState("");
  const [older, setOlder] = useState<Page[]>([]);
  const fetchPage = (cursor?: string) =>
    unwrap<Page>(
      api.GET("/tenants/{tenant_id}/audit-events", {
        params: {
          path: { tenant_id: tid },
          query: { limit: 50, ...(applied ? { action: applied } : {}), ...(cursor ? { cursor } : {}) },
        },
      }),
    );
  const first = useQuery({ queryKey: keys.audit(tid, applied), queryFn: () => fetchPage() });
  const more = useMutation({ mutationFn: (c: string) => fetchPage(c), onSuccess: (p) => setOlder((x) => [...x, p]) });
  const verify = useMutation({
    mutationFn: () =>
      unwrap<{ intact: boolean; events_checked: number; head: string | null }>(
        api.GET("/tenants/{tenant_id}/audit-events/verify", { params: { path: { tenant_id: tid } } }),
      ),
  });
  const exporting = useMutation({
    mutationFn: () => downloadAuthenticated(`/tenants/${tid}/audit-events/export`, `ringsays-audit-${tid}.csv`),
  });
  const rows = [...(first.data?.items ?? []), ...older.flatMap((p) => p.items)];
  const cursor = older.length ? older[older.length - 1]!.next_cursor : (first.data?.next_cursor ?? null);
  return (
    <>
      <PageHeader
        title={t("audit.title")}
        intro={t("audit.intro")}
        actions={
          <div className="row gap">
            <Button busy={verify.isPending} onClick={() => verify.mutate()}>
              {t("audit.verify")}
            </Button>
            <Button busy={exporting.isPending} onClick={() => exporting.mutate()}>
              {t("audit.export")}
            </Button>
          </div>
        }
      />
      {verify.data ? (
        verify.data.intact ? (
          <Alert tone="success">{t("audit.intact", { count: verify.data.events_checked })}</Alert>
        ) : (
          <Alert tone="error">{t("audit.broken")}</Alert>
        )
      ) : null}
      <ErrorAlert error={verify.error ?? exporting.error} />
      <Card>
        <form
          className="row gap end-align"
          onSubmit={(e) => {
            e.preventDefault();
            setOlder([]);
            setApplied(action.trim());
          }}
        >
          <Field label={t("audit.filterAction")}>
            {(p) => <input {...p} dir="ltr" placeholder="verification.approved" value={action} onChange={(e) => setAction(e.target.value)} />}
          </Field>
          <Button type="submit">{t("common.apply")}</Button>
        </form>
        {first.isPending ? <Loading /> : null}
        <ErrorAlert error={first.error ?? more.error} />
        {first.data && rows.length === 0 ? <Empty>{t("audit.noEvents")}</Empty> : null}
        {rows.length > 0 ? (
          <div className="table-wrap">
            <table className="table" data-testid="audit-table">
              <thead>
                <tr>
                  <th>{t("audit.at")}</th>
                  <th>{t("audit.actor")}</th>
                  <th>{t("audit.action")}</th>
                  <th>{t("audit.object")}</th>
                  <th>{t("common.reason")}</th>
                  <th>{t("audit.hash")}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((e) => (
                  <tr key={e.event_id}>
                    <td>{formatDateTime(e.at, lang)}</td>
                    <td dir="ltr" className="small">
                      {actorLabel(e.actor, t)}
                    </td>
                    <td>
                      <code dir="ltr">{e.action}</code>
                    </td>
                    <td dir="ltr" className="small">
                      {e.object_type} {e.object_id.slice(0, 24)}
                    </td>
                    <td dir="auto">{e.reason ?? ""}</td>
                    <td>
                      <code dir="ltr" className="small" title={e.hash}>
                        {e.hash.slice(0, 12)}…
                      </code>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {cursor ? (
          <div className="row center">
            <Button busy={more.isPending} onClick={() => more.mutate(cursor)}>
              {t("common.loadMore")}
            </Button>
          </div>
        ) : null}
      </Card>
    </>
  );
}
