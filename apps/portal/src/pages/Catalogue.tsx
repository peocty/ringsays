import { CHANNELS, PRIORITIES, type Channel, type Priority } from "@ringsays/domain";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { api, unwrap } from "../api/client";
import { keys, useCan, useMembership } from "../api/session";
import type { AdminPurposeCode } from "../api/types";
import {
  Alert,
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
  useToast,
  type Tone,
} from "../components/ui";
import { useLang } from "../i18n";
import { pick } from "../lib/format";

export const CODE_TONE: Record<string, Tone> = { PENDING_REVIEW: "info", APPROVED: "good", REJECTED: "bad", RETIRED: "neutral" };
const CODE_PATTERN = "[A-Z][A-Z0-9]*(\\.[A-Z0-9]+){1,4}";
const URGENT_PREFIXES = ["CARD.", "FRAUD.", "SECURITY."];
/** Channels an enterprise can choose for a purpose code (VOIP and caller id are RingSays internal). */
const CODE_CHANNELS = CHANNELS.filter((c) => c === "SDK" || c === "PRECALL_PUSH" || c === "PSTN");

export function Catalogue() {
  const { t } = useTranslation();
  const { lang } = useLang();
  const m = useMembership();
  const can = useCan();
  const tid = m.tenant_id;
  const path = { params: { path: { tenant_id: tid } } };
  const qc = useQueryClient();
  const toast = useToast();
  const codes = useQuery({
    queryKey: keys.codes(tid),
    queryFn: async () =>
      (await unwrap<{ items: AdminPurposeCode[] }>(api.GET("/tenants/{tenant_id}/purpose-codes", path))).items,
  });
  const blank = {
    code: "",
    display_text: { en: "", ar: "" },
    max_priority: "NORMAL" as Priority,
    max_duration_min: 10,
    allowed_channels: ["PRECALL_PUSH", "PSTN"] as Channel[],
  };
  const [form, setForm] = useState(blank);
  const [proposing, setProposing] = useState(false);
  const [retiring, setRetiring] = useState<AdminPurposeCode | null>(null);
  const propose = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/tenants/{tenant_id}/purpose-codes", {
          ...path,
          body: { ...form, allowed_channels: form.allowed_channels as ("SDK" | "PRECALL_PUSH" | "PSTN")[] },
        }),
      ),
    onSuccess: () => {
      toast.notify(t("catalogue.proposedNote"));
      void qc.invalidateQueries({ queryKey: keys.codes(tid) });
      setProposing(false);
    },
  });
  const retire = useMutation({
    mutationFn: (reason: string) =>
      unwrap(
        api.POST("/tenants/{tenant_id}/purpose-codes/{code}/retire", {
          params: { path: { tenant_id: tid, code: retiring!.code } },
          body: { reason },
        }),
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.codes(tid) });
      setRetiring(null);
    },
  });
  const urgentBlocked = form.max_priority === "URGENT" && !URGENT_PREFIXES.some((p) => form.code.startsWith(p));
  const errs = fieldErrors(propose.error);
  return (
    <>
      <PageHeader
        title={t("catalogue.title")}
        intro={t("catalogue.intro")}
        actions={
          can("catalogue.propose") ? (
            <Button
              variant="primary"
              onClick={() => {
                propose.reset();
                setForm(blank);
                setProposing(true);
              }}
            >
              {t("catalogue.propose")}
            </Button>
          ) : null
        }
      />
      <Card>
        {codes.isPending ? <Loading /> : null}
        <ErrorAlert error={codes.error} />
        {codes.data?.length === 0 ? <Empty>{t("catalogue.noCodes")}</Empty> : null}
        {codes.data && codes.data.length > 0 ? (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>{t("catalogue.code")}</th>
                  <th>{t("catalogue.displayText")}</th>
                  <th>{t("catalogue.maxPriority")}</th>
                  <th>{t("catalogue.maxDuration")}</th>
                  <th>{t("catalogue.channels")}</th>
                  <th>{t("common.status")}</th>
                  <th className="actions-col">{t("common.actions")}</th>
                </tr>
              </thead>
              <tbody>
                {codes.data.map((c) => (
                  <tr key={c.code}>
                    <td>
                      <code dir="ltr">{c.code}</code>
                    </td>
                    <td>{pick(c.display_text, lang)}</td>
                    <td>{t(`priority.${c.max_priority}`)}</td>
                    <td>{c.max_duration_min}</td>
                    <td>{c.allowed_channels.map((ch) => t(`channel.${ch}`)).join(lang === "ar" ? "، " : ", ")}</td>
                    <td>
                      <Pill tone={CODE_TONE[c.status] ?? "neutral"}>{t(`catalogue.codeStatus.${c.status}`)}</Pill>
                      {c.review_reason ? <div className="muted small" dir="auto">{c.review_reason}</div> : null}
                    </td>
                    <td className="actions-col">
                      {can("catalogue.propose") && c.status !== "RETIRED" && c.status !== "REJECTED" ? (
                        <Button variant="ghost" onClick={() => setRetiring(c)}>
                          {t("catalogue.retire")}
                        </Button>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </Card>
      <Dialog open={proposing} title={t("catalogue.propose")} onClose={() => setProposing(false)} wide>
        <form
          className="stack"
          onSubmit={(e) => {
            e.preventDefault();
            propose.mutate();
          }}
        >
          <Field label={t("catalogue.code")} hint={t("catalogue.codeHint")} error={errs.code}>
            {(p) => (
              <input
                {...p}
                dir="ltr"
                required
                pattern={CODE_PATTERN}
                maxLength={64}
                value={form.code}
                onChange={(e) => setForm({ ...form, code: e.target.value.toUpperCase().replace(/\s/g, "") })}
              />
            )}
          </Field>
          <LocalisedInputs
            labelEn={t("catalogue.displayEn")}
            labelAr={t("catalogue.displayAr")}
            hint={t("catalogue.displayHint")}
            value={form.display_text}
            onChange={(v) => setForm({ ...form, display_text: v })}
          />
          <div className="grid-2">
            <Field label={t("catalogue.maxPriority")}>
              {(p) => (
                <select {...p} value={form.max_priority} onChange={(e) => setForm({ ...form, max_priority: e.target.value as Priority })}>
                  {PRIORITIES.map((pr) => (
                    <option key={pr} value={pr}>
                      {t(`priority.${pr}`)}
                    </option>
                  ))}
                </select>
              )}
            </Field>
            <Field label={t("catalogue.maxDuration")}>
              {(p) => (
                <input
                  {...p}
                  type="number"
                  min={1}
                  max={120}
                  required
                  value={form.max_duration_min}
                  onChange={(e) => setForm({ ...form, max_duration_min: Number(e.target.value) })}
                />
              )}
            </Field>
          </div>
          {urgentBlocked ? <Alert tone="warn">{t("catalogue.urgentHint")}</Alert> : null}
          <fieldset className="fieldset">
            <legend>{t("catalogue.channels")}</legend>
            {CODE_CHANNELS.map((ch) => (
              <label key={ch} className="check-row">
                <input
                  type="checkbox"
                  checked={form.allowed_channels.includes(ch)}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      allowed_channels: e.target.checked
                        ? [...form.allowed_channels, ch]
                        : form.allowed_channels.filter((x) => x !== ch),
                    })
                  }
                />
                {t(`channel.${ch}`)}
              </label>
            ))}
          </fieldset>
          <ErrorAlert error={propose.error} />
          <div className="row end">
            <Button onClick={() => setProposing(false)}>{t("common.cancel")}</Button>
            <Button
              type="submit"
              variant="primary"
              busy={propose.isPending}
              disabled={urgentBlocked || form.allowed_channels.length === 0}
            >
              {t("catalogue.propose")}
            </Button>
          </div>
        </form>
      </Dialog>
      <ReasonDialog
        open={retiring !== null}
        title={t("catalogue.retireTitle")}
        note={retiring?.code}
        confirmLabel={t("catalogue.retire")}
        danger
        onClose={() => setRetiring(null)}
        onConfirm={(r) => retire.mutate(r)}
        busy={retire.isPending}
        error={retire.error}
      />
    </>
  );
}
