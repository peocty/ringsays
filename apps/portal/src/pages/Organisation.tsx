import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { api, unwrap } from "../api/client";
import { keys, useCan, useCanChange, useMembership } from "../api/session";
import type { Agent, CallingNumber, Department, Tenant, Verification } from "../api/types";
import {
  Button,
  Card,
  Dialog,
  Empty,
  ErrorAlert,
  Field,
  fieldErrors,
  Loading,
  LocalisedInputs,
  PageHeader,
  Pill,
  ReasonDialog,
  TabPanel,
  Tabs,
  useToast,
  type Tone,
} from "../components/ui";
import { useLang } from "../i18n";
import { formatDateTime, ltr, pick } from "../lib/format";
import { useTenant } from "./Overview";

type Tab = "profile" | "departments" | "agents" | "numbers";

export function Organisation() {
  const { t } = useTranslation();
  const [tab, setTab] = useState<Tab>("profile");
  return (
    <>
      <PageHeader title={t("organisation.title")} />
      <Tabs
        prefix="org"
        label={t("organisation.title")}
        value={tab}
        onChange={setTab}
        tabs={[
          { id: "profile", label: t("organisation.tabProfile") },
          { id: "departments", label: t("organisation.tabDepartments") },
          { id: "agents", label: t("organisation.tabAgents") },
          { id: "numbers", label: t("organisation.tabNumbers") },
        ]}
      />
      <TabPanel prefix="org" value={tab}>
        {tab === "profile" ? <Profile /> : null}
        {tab === "departments" ? <Departments /> : null}
        {tab === "agents" ? <Agents /> : null}
        {tab === "numbers" ? <Numbers /> : null}
      </TabPanel>
    </>
  );
}

function usePath() {
  const m = useMembership();
  return { tid: m.tenant_id, path: { params: { path: { tenant_id: m.tenant_id } } } };
}

export function useDepartments() {
  const { tid, path } = usePath();
  return useQuery({
    queryKey: keys.departments(tid),
    queryFn: async () =>
      (await unwrap<{ items: Department[] }>(api.GET("/tenants/{tenant_id}/departments", path))).items,
  });
}

// Profile

function Profile() {
  const { t } = useTranslation();
  const { lang } = useLang();
  const { tid, path } = usePath();
  const can = useCan();
  const qc = useQueryClient();
  const toast = useToast();
  const tenant = useTenant(tid);
  const canChange = useCanChange();
  const verification = useQuery({
    queryKey: keys.verification(tid),
    queryFn: () => unwrap<Verification>(api.GET("/tenants/{tenant_id}/verification", path)),
    enabled: can("org.manage"),
  });
  const [form, setForm] = useState({ legal_name: { en: "", ar: "" }, commercial_registration: "", domain: "" });
  useEffect(() => {
    if (tenant.data) {
      setForm({
        legal_name: tenant.data.legal_name,
        commercial_registration: tenant.data.commercial_registration ?? "",
        domain: tenant.data.domain ?? "",
      });
    }
  }, [tenant.data]);
  const save = useMutation({
    mutationFn: () =>
      unwrap<Tenant>(
        api.PATCH("/tenants/{tenant_id}", {
          ...path,
          body: {
            legal_name: form.legal_name,
            ...(form.commercial_registration ? { commercial_registration: form.commercial_registration } : {}),
            ...(form.domain ? { domain: form.domain } : {}),
          },
        }),
      ),
    onSuccess: (data) => {
      qc.setQueryData(keys.tenant(tid), data);
      void qc.invalidateQueries({ queryKey: ["me"] });
      toast.notify(t("organisation.saved"));
    },
  });
  if (tenant.isPending) return <Loading />;
  if (tenant.error) return <ErrorAlert error={tenant.error} />;
  const underReview = verification.data?.latest_request?.status === "SUBMITTED";
  const editable = canChange("org.manage") && tenant.data.verification_status === "PENDING" && !underReview;
  const errs = fieldErrors(save.error);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate();
  };
  return (
    <Card>
      {!editable && can("org.manage") ? <p className="muted">{t("organisation.locked")}</p> : null}
      <form onSubmit={submit} className="stack">
        {editable ? (
          <LocalisedInputs
            labelEn={t("common.nameEn")}
            labelAr={t("common.nameAr")}
            value={form.legal_name}
            onChange={(v) => setForm({ ...form, legal_name: v })}
          />
        ) : (
          <dl className="facts">
            <dt>{t("organisation.legalName")}</dt>
            <dd>{pick(tenant.data.legal_name, lang)}</dd>
          </dl>
        )}
        {editable ? (
          <div className="grid-2">
            <Field label={t("organisation.cr")} hint={t("organisation.crHint")} error={errs.commercial_registration}>
              {(p) => (
                <input
                  {...p}
                  dir="ltr"
                  inputMode="numeric"
                  pattern="[0-9]{10}"
                  maxLength={10}
                  value={form.commercial_registration}
                  onChange={(e) => setForm({ ...form, commercial_registration: e.target.value.trim() })}
                />
              )}
            </Field>
            <Field label={t("organisation.domain")} hint={t("organisation.domainHint")} error={errs.domain}>
              {(p) => (
                <input {...p} dir="ltr" value={form.domain} onChange={(e) => setForm({ ...form, domain: e.target.value.trim() })} />
              )}
            </Field>
          </div>
        ) : null}
        <dl className="facts">
          {!editable ? (
            <>
              <dt>{t("organisation.cr")}</dt>
              <dd dir="ltr">{tenant.data.commercial_registration ?? "—"}</dd>
              <dt>{t("organisation.domain")}</dt>
              <dd dir="ltr">{tenant.data.domain ?? "—"}</dd>
            </>
          ) : null}
          <dt>{t("organisation.sector")}</dt>
          <dd>{t(`sector.${tenant.data.sector}`)}</dd>
          <dt>{t("organisation.region")}</dt>
          <dd>{tenant.data.residency_region}</dd>
          <dt>{t("common.created")}</dt>
          <dd>{formatDateTime(tenant.data.created_at, lang)}</dd>
        </dl>
        <ErrorAlert error={save.error} />
        {editable ? (
          <div className="row end">
            <Button type="submit" variant="primary" busy={save.isPending}>
              {t("common.save")}
            </Button>
          </div>
        ) : null}
      </form>
    </Card>
  );
}

// Departments

function Departments() {
  const { t } = useTranslation();
  const { lang } = useLang();
  const { tid, path } = usePath();
  const can = useCanChange();
  const qc = useQueryClient();
  const depts = useDepartments();
  const [editing, setEditing] = useState<Department | "new" | null>(null);
  const [name, setName] = useState({ en: "", ar: "" });
  const save = useMutation({
    mutationFn: () =>
      editing === "new"
        ? unwrap(api.POST("/tenants/{tenant_id}/departments", { ...path, body: { name } }))
        : unwrap(
            api.PATCH("/tenants/{tenant_id}/departments/{department_id}", {
              params: { path: { tenant_id: tid, department_id: (editing as Department).department_id } },
              body: { name },
            }),
          ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.departments(tid) });
      setEditing(null);
    },
  });
  const open = (d: Department | "new") => {
    save.reset();
    setName(d === "new" ? { en: "", ar: "" } : d.name);
    setEditing(d);
  };
  return (
    <Card
      actions={
        can("org.manage") ? (
          <Button variant="primary" onClick={() => open("new")}>
            {t("organisation.addDepartment")}
          </Button>
        ) : null
      }
    >
      {depts.isPending ? <Loading /> : null}
      <ErrorAlert error={depts.error} />
      {depts.data && depts.data.length === 0 ? <Empty>{t("organisation.noDepartments")}</Empty> : null}
      {depts.data && depts.data.length > 0 ? (
        <table className="table">
          <thead>
            <tr>
              <th>{t("organisation.departmentName")}</th>
              <th className="actions-col">{t("common.actions")}</th>
            </tr>
          </thead>
          <tbody>
            {depts.data.map((d) => (
              <tr key={d.department_id}>
                <td>{pick(d.name, lang)}</td>
                <td className="actions-col">
                  {can("org.manage") ? (
                    <Button variant="ghost" onClick={() => open(d)}>
                      {t("common.edit")}
                    </Button>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      <Dialog
        open={editing !== null}
        title={editing === "new" ? t("organisation.addDepartment") : t("common.edit")}
        onClose={() => setEditing(null)}
      >
        <form
          className="stack"
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate();
          }}
        >
          <LocalisedInputs labelEn={t("common.nameEn")} labelAr={t("common.nameAr")} value={name} onChange={setName} />
          <ErrorAlert error={save.error} />
          <div className="row end">
            <Button onClick={() => setEditing(null)}>{t("common.cancel")}</Button>
            <Button type="submit" variant="primary" busy={save.isPending}>
              {t("common.save")}
            </Button>
          </div>
        </form>
      </Dialog>
    </Card>
  );
}

// Agents

export function useAllAgents() {
  const { tid, path } = usePath();
  return useQuery({
    queryKey: keys.agents(tid),
    queryFn: async () => {
      const all: Agent[] = [];
      let cursor: string | undefined;
      // Pages of 200 until done; agent lists are small (hundreds).
      for (let i = 0; i < 50; i += 1) {
        const page = await unwrap<{ items: Agent[]; next_cursor: string | null }>(
          api.GET("/tenants/{tenant_id}/agents", {
            params: { path: path.params.path, query: { limit: 200, ...(cursor ? { cursor } : {}) } },
          }),
        );
        all.push(...page.items);
        if (!page.next_cursor) break;
        cursor = page.next_cursor;
      }
      return all;
    },
  });
}

function Agents() {
  const { t } = useTranslation();
  const { lang } = useLang();
  const { tid, path } = usePath();
  const can = useCanChange();
  const qc = useQueryClient();
  const agents = useAllAgents();
  const depts = useDepartments();
  const [adding, setAdding] = useState(false);
  const blank = { agent_id: "", display_name: { en: "", ar: "" }, department_id: "", employee_ref: "" };
  const [form, setForm] = useState(blank);
  const deptName = (id: string) => {
    const d = depts.data?.find((x) => x.department_id === id);
    return d ? pick(d.name, lang) : "—";
  };
  const create = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/tenants/{tenant_id}/agents", {
          ...path,
          body: {
            agent_id: form.agent_id,
            display_name: form.display_name,
            department_id: form.department_id,
            ...(form.employee_ref ? { employee_ref: form.employee_ref } : {}),
          },
        }),
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.agents(tid) });
      setAdding(false);
    },
  });
  const toggle = useMutation({
    mutationFn: (a: Agent) =>
      unwrap(
        api.PATCH("/tenants/{tenant_id}/agents/{agent_id}", {
          params: { path: { tenant_id: tid, agent_id: a.agent_id } },
          body: { active: !a.active },
        }),
      ),
    onSuccess: () => void qc.invalidateQueries({ queryKey: keys.agents(tid) }),
  });
  const errs = fieldErrors(create.error);
  return (
    <Card
      actions={
        can("org.manage") ? (
          <Button
            variant="primary"
            disabled={!depts.data?.length}
            onClick={() => {
              create.reset();
              setForm({ ...blank, department_id: depts.data?.[0]?.department_id ?? "" });
              setAdding(true);
            }}
          >
            {t("organisation.addAgent")}
          </Button>
        ) : null
      }
    >
      {agents.isPending ? <Loading /> : null}
      <ErrorAlert error={agents.error ?? toggle.error} />
      {agents.data?.length === 0 ? <Empty>{t("organisation.noAgents")}</Empty> : null}
      {agents.data && agents.data.length > 0 ? (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>{t("organisation.agentId")}</th>
                <th>{t("organisation.agentName")}</th>
                <th>{t("organisation.department")}</th>
                <th>{t("organisation.employeeRef")}</th>
                <th>{t("common.status")}</th>
                <th className="actions-col">{t("common.actions")}</th>
              </tr>
            </thead>
            <tbody>
              {agents.data.map((a) => (
                <tr key={a.agent_id}>
                  <td>
                    <code dir="ltr">{a.agent_id}</code>
                  </td>
                  <td>{pick(a.display_name, lang)}</td>
                  <td>{deptName(a.department_id)}</td>
                  <td dir="auto">{a.employee_ref ?? "—"}</td>
                  <td>
                    <Pill tone={a.active ? "good" : "neutral"}>{a.active ? t("common.active") : t("common.inactive")}</Pill>
                  </td>
                  <td className="actions-col">
                    {can("org.manage") ? (
                      <Button variant="ghost" busy={toggle.isPending && toggle.variables === a} onClick={() => toggle.mutate(a)}>
                        {a.active ? t("organisation.deactivate") : t("organisation.activate")}
                      </Button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      <Dialog open={adding} title={t("organisation.addAgent")} onClose={() => setAdding(false)}>
        <form
          className="stack"
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
        >
          <Field label={t("organisation.agentId")} hint={t("organisation.agentIdHint")} error={errs.agent_id}>
            {(p) => (
              <input
                {...p}
                dir="ltr"
                required
                pattern="[A-Za-z0-9_.\-]{3,64}"
                value={form.agent_id}
                onChange={(e) => setForm({ ...form, agent_id: e.target.value.trim() })}
              />
            )}
          </Field>
          <LocalisedInputs
            labelEn={`${t("organisation.agentName")} (EN)`}
            labelAr={`${t("organisation.agentName")} (AR)`}
            value={form.display_name}
            onChange={(v) => setForm({ ...form, display_name: v })}
          />
          <Field label={t("organisation.department")}>
            {(p) => (
              <select {...p} required value={form.department_id} onChange={(e) => setForm({ ...form, department_id: e.target.value })}>
                {depts.data?.map((d) => (
                  <option key={d.department_id} value={d.department_id}>
                    {pick(d.name, lang)}
                  </option>
                ))}
              </select>
            )}
          </Field>
          <Field label={t("organisation.employeeRef")} hint={t("organisation.employeeRefHint")} optional>
            {(p) => (
              <input {...p} maxLength={64} value={form.employee_ref} onChange={(e) => setForm({ ...form, employee_ref: e.target.value })} />
            )}
          </Field>
          <ErrorAlert error={create.error} />
          <div className="row end">
            <Button onClick={() => setAdding(false)}>{t("common.cancel")}</Button>
            <Button type="submit" variant="primary" busy={create.isPending}>
              {t("common.create")}
            </Button>
          </div>
        </form>
      </Dialog>
    </Card>
  );
}

// Calling numbers

export const NUMBER_TONE: Record<string, Tone> = { PENDING_VERIFICATION: "warn", VERIFIED: "good", REVOKED: "neutral" };

function Numbers() {
  const { t } = useTranslation();
  const { lang } = useLang();
  const { tid, path } = usePath();
  const can = useCanChange();
  const canContain = useCan(); // stopping a number stays allowed while suspended
  const qc = useQueryClient();
  const depts = useDepartments();
  const numbers = useQuery({
    queryKey: keys.numbers(tid),
    queryFn: async () =>
      (await unwrap<{ items: CallingNumber[] }>(api.GET("/tenants/{tenant_id}/calling-numbers", path))).items,
  });
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState({ phone: "", department_id: "", cst: false });
  const [revoking, setRevoking] = useState<CallingNumber | null>(null);
  const add = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/tenants/{tenant_id}/calling-numbers", {
          ...path,
          body: {
            phone: form.phone,
            cst_entity_name_registered: form.cst,
            ...(form.department_id ? { department_id: form.department_id } : {}),
          },
        }),
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.numbers(tid) });
      setAdding(false);
    },
  });
  const revoke = useMutation({
    mutationFn: (reason: string) =>
      unwrap(
        api.POST("/tenants/{tenant_id}/calling-numbers/{number_id}/revoke", {
          params: { path: { tenant_id: tid, number_id: revoking!.number_id } },
          body: { reason },
        }),
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.numbers(tid) });
      setRevoking(null);
    },
  });
  const deptName = (id: string | null | undefined) =>
    id ? pick(depts.data?.find((d) => d.department_id === id)?.name, lang) : t("organisation.anyDepartment");
  const errs = fieldErrors(add.error);
  return (
    <Card
      actions={
        can("org.manage") ? (
          <Button
            variant="primary"
            onClick={() => {
              add.reset();
              setForm({ phone: "", department_id: "", cst: false });
              setAdding(true);
            }}
          >
            {t("organisation.addNumber")}
          </Button>
        ) : null
      }
    >
      {numbers.isPending ? <Loading /> : null}
      <ErrorAlert error={numbers.error} />
      {numbers.data?.length === 0 ? <Empty>{t("organisation.noNumbers")}</Empty> : null}
      {numbers.data && numbers.data.length > 0 ? (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>{t("organisation.phone")}</th>
                <th>{t("organisation.numberDepartment")}</th>
                <th>{t("organisation.cstShort")}</th>
                <th>{t("common.status")}</th>
                <th className="actions-col">{t("common.actions")}</th>
              </tr>
            </thead>
            <tbody>
              {numbers.data.map((n) => (
                <tr key={n.number_id}>
                  <td>
                    <span dir="ltr" className="mono">
                      {n.phone_masked}
                    </span>
                  </td>
                  <td>{deptName(n.department_id)}</td>
                  <td>{n.cst_entity_name_registered ? t("common.yes") : t("common.no")}</td>
                  <td>
                    <Pill tone={NUMBER_TONE[n.status] ?? "neutral"}>{t(`organisation.numberStatus.${n.status}`)}</Pill>
                    {n.review_reason ? <div className="muted small" dir="auto">{n.review_reason}</div> : null}
                  </td>
                  <td className="actions-col">
                    {canContain("org.manage") && n.status !== "REVOKED" ? (
                      <Button variant="ghost" onClick={() => setRevoking(n)}>
                        {t("organisation.revoke")}
                      </Button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      <Dialog open={adding} title={t("organisation.addNumber")} onClose={() => setAdding(false)}>
        <form
          className="stack"
          onSubmit={(e) => {
            e.preventDefault();
            add.mutate();
          }}
        >
          <Field label={t("organisation.phone")} hint={t("organisation.phoneHint")} error={errs.phone}>
            {(p) => (
              <input
                {...p}
                dir="ltr"
                type="tel"
                required
                pattern="\+[1-9][0-9]{6,14}"
                value={form.phone}
                onChange={(e) => setForm({ ...form, phone: e.target.value.replace(/\s/g, "") })}
              />
            )}
          </Field>
          <Field label={t("organisation.numberDepartment")} optional>
            {(p) => (
              <select {...p} value={form.department_id} onChange={(e) => setForm({ ...form, department_id: e.target.value })}>
                <option value="">{t("organisation.anyDepartment")}</option>
                {depts.data?.map((d) => (
                  <option key={d.department_id} value={d.department_id}>
                    {pick(d.name, lang)}
                  </option>
                ))}
              </select>
            )}
          </Field>
          <label className="check-row">
            <input type="checkbox" checked={form.cst} onChange={(e) => setForm({ ...form, cst: e.target.checked })} />
            {t("organisation.cst")}
          </label>
          <ErrorAlert error={add.error} />
          <div className="row end">
            <Button onClick={() => setAdding(false)}>{t("common.cancel")}</Button>
            <Button type="submit" variant="primary" busy={add.isPending}>
              {t("common.add")}
            </Button>
          </div>
        </form>
      </Dialog>
      <ReasonDialog
        open={revoking !== null}
        title={t("organisation.revokeTitle")}
        note={revoking ? ltr(revoking.phone_masked) : undefined}
        confirmLabel={t("organisation.revoke")}
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
