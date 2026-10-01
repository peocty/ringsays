import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { api, unwrap } from "../api/client";
import { keys, useCan, useCanChange, useMe, useMembership } from "../api/session";
import type { PortalUser, TenantRole } from "../api/types";
import { Button, Card, Dialog, Empty, ErrorAlert, Field, fieldErrors, Loading, PageHeader, Pill, useToast } from "../components/ui";
import { useLang } from "../i18n";
import { formatDateTime, pick } from "../lib/format";
import { useAllAgents } from "./Organisation";

export const TENANT_ROLES: TenantRole[] = ["TENANT_ADMIN", "INTEGRATION_ADMIN", "SUPERVISOR", "AGENT", "COMPLIANCE"];

function RolePicker({ value, onChange }: { value: TenantRole[]; onChange: (v: TenantRole[]) => void }) {
  const { t } = useTranslation();
  return (
    <fieldset className="fieldset">
      <legend>{t("people.roles")}</legend>
      {TENANT_ROLES.map((r) => (
        <label key={r} className="check-row">
          <input
            type="checkbox"
            checked={value.includes(r)}
            onChange={(e) => onChange(e.target.checked ? [...value, r] : value.filter((x) => x !== r))}
          />
          <span>
            <strong>{t(`roles.${r}`)}</strong>
            <span className="muted small block">{t(`roleHelp.${r}`)}</span>
          </span>
        </label>
      ))}
    </fieldset>
  );
}

export function People() {
  const { t } = useTranslation();
  const { lang } = useLang();
  const m = useMembership();
  const me = useMe();
  const tid = m.tenant_id;
  const path = { params: { path: { tenant_id: tid } } };
  const qc = useQueryClient();
  const toast = useToast();
  const agents = useAllAgents();
  const users = useQuery({
    queryKey: keys.users(tid),
    queryFn: async () => (await unwrap<{ items: PortalUser[] }>(api.GET("/tenants/{tenant_id}/users", path))).items,
  });
  const [inviting, setInviting] = useState(false);
  const [editing, setEditing] = useState<PortalUser | null>(null);
  const [form, setForm] = useState<{ email: string; display_name: string; roles: TenantRole[]; agent_id: string }>({
    email: "",
    display_name: "",
    roles: [],
    agent_id: "",
  });
  const invite = useMutation({
    mutationFn: () =>
      unwrap<PortalUser>(
        api.POST("/tenants/{tenant_id}/users", {
          ...path,
          body: {
            email: form.email,
            roles: form.roles,
            ...(form.display_name ? { display_name: form.display_name } : {}),
            ...(form.agent_id ? { agent_id: form.agent_id } : {}),
          },
        }),
      ),
    onSuccess: (u) => {
      toast.notify(t("people.invitedNote", { email: u.email }));
      void qc.invalidateQueries({ queryKey: keys.users(tid) });
      setInviting(false);
    },
  });
  const update = useMutation({
    mutationFn: (args: { user: PortalUser; body: { roles?: TenantRole[]; status?: "ACTIVE" | "DISABLED"; agent_id?: string | null } }) =>
      unwrap<PortalUser>(
        api.PATCH("/tenants/{tenant_id}/users/{user_id}", {
          params: { path: { tenant_id: tid, user_id: args.user.user_id } },
          body: args.body,
        }),
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.users(tid) });
      void qc.invalidateQueries({ queryKey: ["me"] }); // menus follow role changes at once
      setEditing(null);
    },
  });
  const needsAgent = form.roles.includes("AGENT");
  const canChange = useCanChange()("users.manage");
  const canDisable = useCan()("users.manage"); // disabling people stays allowed while suspended
  const agentName = (id: string | null | undefined) => {
    if (!id) return "—";
    const a = agents.data?.find((x) => x.agent_id === id);
    return a ? `${pick(a.display_name, lang)} (${id})` : id;
  };
  const AgentSelect = (
    <Field label={t("people.agent")} hint={t("people.agentHint")} optional={!needsAgent}>
      {(p) => (
        <select {...p} required={needsAgent} value={form.agent_id} onChange={(e) => setForm({ ...form, agent_id: e.target.value })}>
          <option value="">—</option>
          {agents.data?.filter((a) => a.active).map((a) => (
            <option key={a.agent_id} value={a.agent_id}>
              {pick(a.display_name, lang)} ({a.agent_id})
            </option>
          ))}
        </select>
      )}
    </Field>
  );
  const errs = fieldErrors(invite.error);
  return (
    <>
      <PageHeader
        title={t("people.title")}
        actions={
          <Button
            variant="primary"
            disabled={!canChange}
            onClick={() => {
              invite.reset();
              setForm({ email: "", display_name: "", roles: [], agent_id: "" });
              setInviting(true);
            }}
          >
            {t("people.invite")}
          </Button>
        }
      />
      <Card>
        {users.isPending ? <Loading /> : null}
        <ErrorAlert error={users.error ?? update.error} />
        {users.data?.length === 0 ? <Empty>{t("people.noPeople")}</Empty> : null}
        {users.data && users.data.length > 0 ? (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>{t("people.email")}</th>
                  <th>{t("people.roles")}</th>
                  <th>{t("people.agent")}</th>
                  <th>{t("common.status")}</th>
                  <th>{t("people.lastSignIn")}</th>
                  <th className="actions-col">{t("common.actions")}</th>
                </tr>
              </thead>
              <tbody>
                {users.data.map((u) => {
                  const isMe = u.email === me.data?.email;
                  return (
                    <tr key={u.user_id}>
                      <td>
                        <span dir="ltr">{u.email}</span>
                        {u.display_name ? <div className="muted small" dir="auto">{u.display_name}</div> : null}
                        {isMe ? <Pill tone="info">{t("people.you")}</Pill> : null}
                      </td>
                      <td>{u.roles.map((r) => t(`roles.${r}`)).join(" · ")}</td>
                      <td>{agentName(u.agent_id)}</td>
                      <td>
                        <Pill tone={u.status === "ACTIVE" ? "good" : u.status === "INVITED" ? "info" : "neutral"}>
                          {t(`people.userStatus.${u.status}`)}
                        </Pill>
                      </td>
                      <td>{u.last_sign_in_at ? formatDateTime(u.last_sign_in_at, lang) : t("people.never")}</td>
                      <td className="actions-col">
                        <Button
                          variant="ghost"
                          disabled={!canChange || isMe}
                          title={isMe ? t("people.selfNote") : undefined}
                          onClick={() => {
                            update.reset();
                            setForm({ email: u.email, display_name: "", roles: [...u.roles], agent_id: u.agent_id ?? "" });
                            setEditing(u);
                          }}
                        >
                          {t("people.editRoles")}
                        </Button>
                        {!isMe ? (
                          <Button
                            variant="ghost"
                            onClick={() => update.mutate({ user: u, body: { status: u.status === "DISABLED" ? "ACTIVE" : "DISABLED" } })}
                            disabled={
                              u.status === "DISABLED"
                                ? !canChange || u.last_sign_in_at === null
                                : !canDisable
                            }
                          >
                            {u.status === "DISABLED" ? t("people.enable") : t("people.disable")}
                          </Button>
                        ) : null}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : null}
      </Card>
      <Dialog open={inviting} title={t("people.invite")} onClose={() => setInviting(false)}>
        <form
          className="stack"
          onSubmit={(e) => {
            e.preventDefault();
            invite.mutate();
          }}
        >
          <Field label={t("people.email")} error={errs.email}>
            {(p) => <input {...p} type="email" dir="ltr" required maxLength={254} value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />}
          </Field>
          <Field label={t("people.displayName")} optional>
            {(p) => <input {...p} maxLength={120} value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} />}
          </Field>
          <RolePicker value={form.roles} onChange={(roles) => setForm({ ...form, roles })} />
          {AgentSelect}
          <ErrorAlert error={invite.error} />
          <div className="row end">
            <Button onClick={() => setInviting(false)}>{t("common.cancel")}</Button>
            <Button type="submit" variant="primary" busy={invite.isPending} disabled={form.roles.length === 0}>
              {t("people.invite")}
            </Button>
          </div>
        </form>
      </Dialog>
      <Dialog open={editing !== null} title={t("people.editRoles")} onClose={() => setEditing(null)}>
        <form
          className="stack"
          onSubmit={(e) => {
            e.preventDefault();
            if (editing) update.mutate({ user: editing, body: { roles: form.roles, agent_id: form.agent_id || null } });
          }}
        >
          <p dir="ltr">{editing?.email}</p>
          <RolePicker value={form.roles} onChange={(roles) => setForm({ ...form, roles })} />
          {AgentSelect}
          <ErrorAlert error={update.error} />
          <div className="row end">
            <Button onClick={() => setEditing(null)}>{t("common.cancel")}</Button>
            <Button type="submit" variant="primary" busy={update.isPending} disabled={form.roles.length === 0}>
              {t("common.save")}
            </Button>
          </div>
        </form>
      </Dialog>
    </>
  );
}
