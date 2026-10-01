import { RingSaysError, type Consent, type Preferences } from "@ringsays/client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getCalendars } from "expo-localization";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { StyleSheet, Switch, Text, View } from "react-native";

import { Banner, Body, Button, Card, Loading, Screen, Sheet } from "../../src/components/ui";
import { config } from "../../src/config";
import { useLang } from "../../src/i18n";
import { useAuth } from "../../src/lib/auth";
import { errorMessage } from "../../src/lib/errors";
import { ltr } from "../../src/lib/format";
import { keys } from "../../src/lib/keys";
import { forgetDevice, getSession } from "../../src/lib/session";
import { space, useTheme } from "../../src/theme";

const ALL_DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"] as const;
const DEFAULT_QUIET = { days: [...ALL_DAYS], start: "22:00", end: "07:00" };

function phoneTimeZone(): string {
  try {
    return getCalendars()[0]?.timeZone ?? "Asia/Riyadh";
  } catch {
    return "Asia/Riyadh";
  }
}

function shiftHour(hhmm: string, delta: number): string {
  const h = (Number(hhmm.slice(0, 2)) + delta + 24) % 24;
  return `${String(h).padStart(2, "0")}:00`;
}

export default function Settings() {
  const { t } = useTranslation();
  const { lang, toggle } = useLang();
  const th = useTheme();
  const qc = useQueryClient();
  const auth = useAuth();
  const [withdrawing, setWithdrawing] = useState<Consent | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [exported, setExported] = useState<number | null>(null);
  const [saved, setSaved] = useState(false);

  const prefs = useQuery({ queryKey: keys.preferences, queryFn: async () => (await getSession()).preferences() });
  const consents = useQuery({ queryKey: keys.consents, queryFn: async () => (await getSession()).consents() });

  const save = useMutation({
    mutationFn: async (doc: Preferences) => (await getSession()).savePreferences(doc, prefs.data!.etag),
    onSuccess: (r) => {
      qc.setQueryData(keys.preferences, r);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    },
    onError: (e) => {
      // Saved on another device first: load theirs; the person can change it again.
      if (e instanceof RingSaysError && e.status === 412) void prefs.refetch();
    },
  });
  const withdraw = useMutation({
    mutationFn: async (c: Consent) => (await getSession()).withdrawConsent(c.consent_id),
    onSuccess: () => {
      setWithdrawing(null);
      void qc.invalidateQueries({ queryKey: keys.consents });
      void qc.invalidateQueries({ queryKey: keys.inboxAll });
    },
  });
  const exportData = useMutation({
    mutationFn: async () => (await getSession()).exportData(),
    onSuccess: (d) => {
      const list = (d as { communications_received?: unknown[] }).communications_received;
      setExported(Array.isArray(list) ? list.length : 0);
    },
  });
  const erase = useMutation({
    mutationFn: async () => {
      await (await getSession()).eraseAccount();
      await forgetDevice();
    },
    onSuccess: () => auth.markSignedOut(false),
  });

  const update = (patch: Partial<Preferences>) => {
    if (!prefs.data) return;
    save.mutate({ ...prefs.data.doc, timezone: phoneTimeZone(), ...patch });
  };
  const doc = prefs.data?.doc;
  const quiet = doc?.night_mode ?? null;

  return (
    <Screen>
      <Card>
        <Text style={[styles.h, { color: th.text }]}>{t("settings.language")}</Text>
        <Button title={t("app.switchLanguage")} onPress={toggle} testID="lang-toggle" />
      </Card>

      <Card>
        <Text style={[styles.h, { color: th.text }]}>{t("settings.calls")}</Text>
        {prefs.isPending ? <Loading /> : null}
        {prefs.error ? <Banner tone="error">{errorMessage(prefs.error, t)}</Banner> : null}
        {doc ? (
          <>
            <View style={styles.row}>
              <View style={{ flex: 1 }}>
                <Body>{t("settings.verifiedOnly")}</Body>
                <Body muted>{t("settings.verifiedOnlyHint")}</Body>
              </View>
              <Switch
                testID="verified-only"
                accessibilityLabel={t("settings.verifiedOnly")}
                value={!!doc.verified_businesses_only}
                onValueChange={(v) => update({ verified_businesses_only: v })}
                disabled={save.isPending}
              />
            </View>
            <View style={styles.row}>
              <View style={{ flex: 1 }}>
                <Body>{t("settings.quietHours")}</Body>
                <Body muted>{t("settings.quietHoursHint")}</Body>
              </View>
              <Switch
                testID="quiet-hours"
                accessibilityLabel={t("settings.quietHours")}
                value={quiet !== null}
                onValueChange={(v) => update({ night_mode: v ? DEFAULT_QUIET : null })}
                disabled={save.isPending}
              />
            </View>
            {quiet ? (
              <View style={[styles.row, { justifyContent: "space-around" }]}>
                {(["start", "end"] as const).map((k) => (
                  <View key={k} style={{ alignItems: "center", gap: space.xs }}>
                    <Body muted>{k === "start" ? t("settings.from") : t("settings.to")}</Body>
                    <View style={[styles.row, { direction: "ltr" }]}>
                      <Button title="−" onPress={() => update({ night_mode: { ...quiet, [k]: shiftHour(quiet[k], -1) } })} />
                      <Text style={{ color: th.text, fontSize: 20, minWidth: 64, textAlign: "center" }} testID={`quiet-${k}`}>
                        {quiet[k]}
                      </Text>
                      <Button title="+" onPress={() => update({ night_mode: { ...quiet, [k]: shiftHour(quiet[k], 1) } })} />
                    </View>
                  </View>
                ))}
              </View>
            ) : null}
            {saved ? <Banner tone="good">{t("settings.saved")}</Banner> : null}
            {save.error ? <Banner tone="error">{errorMessage(save.error, t)}</Banner> : null}
          </>
        ) : null}
      </Card>

      <Card>
        <Text style={[styles.h, { color: th.text }]}>{t("settings.organisations")}</Text>
        {consents.isPending ? <Loading /> : null}
        {consents.data?.length === 0 ? <Body muted>{t("settings.noOrganisations")}</Body> : null}
        {consents.data?.map((c) => (
          <View key={c.consent_id} style={styles.row} testID={`consent-${c.consent_id}`}>
            <Body style={{ flex: 1 }}>{c.grantee}</Body>
            {c.withdrawn_at ? (
              <Body muted>{t("settings.withdrawn")}</Body>
            ) : (
              <Button title={t("settings.withdraw")} variant="ghost" onPress={() => setWithdrawing(c)} />
            )}
          </View>
        ))}
      </Card>

      <Card>
        <Text style={[styles.h, { color: th.text }]}>{t("settings.privacy")}</Text>
        <Button title={t("settings.export")} busy={exportData.isPending} onPress={() => exportData.mutate()} testID="export" />
        {exported !== null ? <Banner tone="good">{t("settings.exportDone", { count: exported })}</Banner> : null}
        <Button
          title={t("settings.signOut")}
          onPress={async () => {
            await (await getSession()).signOut();
            auth.markSignedOut(false);
          }}
          testID="sign-out"
        />
        <Button title={t("settings.delete")} variant="danger" onPress={() => setDeleting(true)} testID="delete-account" />
      </Card>
      <Body muted style={{ textAlign: "center" }}>
        {t("settings.version", { version: ltr(config.appVersion) })} · {lang.toUpperCase()}
      </Body>

      <Sheet
        visible={withdrawing !== null}
        title={t("settings.withdrawTitle", { name: withdrawing?.grantee ?? "" })}
        onClose={() => setWithdrawing(null)}
      >
        <Body muted>{t("settings.withdrawNote")}</Body>
        {withdraw.error ? <Banner tone="error">{errorMessage(withdraw.error, t)}</Banner> : null}
        <Button
          title={t("settings.withdraw")}
          variant="danger"
          busy={withdraw.isPending}
          onPress={() => withdrawing && withdraw.mutate(withdrawing)}
          testID="withdraw-confirm"
        />
      </Sheet>

      <Sheet visible={deleting} title={t("settings.deleteTitle")} onClose={() => setDeleting(false)}>
        <Body muted>{t("settings.deleteNote")}</Body>
        {erase.error ? <Banner tone="error">{errorMessage(erase.error, t)}</Banner> : null}
        <Button title={t("settings.deleteConfirm")} variant="danger" busy={erase.isPending} onPress={() => erase.mutate()} testID="delete-confirm" />
      </Sheet>
    </Screen>
  );
}

const styles = StyleSheet.create({
  h: { fontSize: 17, fontWeight: "700" },
  row: { flexDirection: "row", alignItems: "center", gap: space.md },
});
