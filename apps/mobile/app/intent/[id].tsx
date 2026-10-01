import {
  ACTION_FOR,
  RingSaysError,
  openSlots,
  suggestedSlots,
  type DisplayAction,
  type IntentDisplay,
  type IntentResponse,
  type Slot,
} from "@ringsays/client";
import { DECLINE_REASONS, type DeclineReason } from "@ringsays/domain";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Stack, useLocalSearchParams } from "expo-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Pressable, Text, View } from "react-native";

import { Banner, Body, Button, Card, IntentSummary, Loading, Screen, Sheet, urgentStyle } from "../../src/components/ui";
import { useLang } from "../../src/i18n";
import { errorMessage } from "../../src/lib/errors";
import { formatTime } from "../../src/lib/format";
import { keys } from "../../src/lib/keys";
import { getSession } from "../../src/lib/session";
import { radius, space, useTheme } from "../../src/theme";

type SheetKind = "later" | "propose" | "schedule" | "decline" | "message" | null;
const LATER_MINUTES = [15, 30, 60, 120] as const;
/** Reasons offered to the person (MESSAGE_INSTEAD has its own button). */
const REASONS = DECLINE_REASONS.filter((r) => r !== "MESSAGE_INSTEAD");

export default function IntentScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { t } = useTranslation();
  const { lang } = useLang();
  const th = useTheme();
  const qc = useQueryClient();
  const [sheet, setSheet] = useState<SheetKind>(null);
  const [picked, setPicked] = useState<Slot[]>([]);
  const [sent, setSent] = useState(false);

  const q = useQuery({
    queryKey: keys.intent(id),
    queryFn: async () => (await getSession()).intent(id),
    enabled: !!id,
  });
  const respond = useMutation({
    mutationFn: async (r: IntentResponse) => (await getSession()).respond(id, r),
    onSuccess: (updated: IntentDisplay) => {
      qc.setQueryData(keys.intent(id), updated);
      void qc.invalidateQueries({ queryKey: keys.inboxAll });
      setSheet(null);
      setSent(true);
    },
    onError: (e) => {
      // Ended or taken elsewhere: show its current state, actions go away.
      if (e instanceof RingSaysError && (e.status === 410 || e.status === 409)) {
        setSheet(null);
        void qc.invalidateQueries({ queryKey: keys.intent(id) });
        void qc.invalidateQueries({ queryKey: keys.inboxAll });
      }
    },
  });

  if (q.isPending) return <Loading />;
  if (q.error || !q.data) {
    return (
      <Screen>
        <Banner tone="error">{errorMessage(q.error, t)}</Banner>
        <Button title={t("app.retry")} onPress={() => void q.refetch()} />
      </Screen>
    );
  }
  const intent = q.data;
  const actions = intent.actions;
  const send = (r: IntentResponse) => respond.mutate(r);
  const onAction = (a: DisplayAction) => {
    respond.reset();
    if (a === "TALK_NOW") send({ action: ACTION_FOR.TALK_NOW });
    else if (a === "LATER") setSheet("later");
    else if (a === "PROPOSE") {
      setPicked([]);
      setSheet("propose");
    } else if (a === "SCHEDULE") setSheet("schedule");
    else if (a === "MESSAGE") setSheet("message");
    else setSheet("decline");
  };
  const label: Record<DisplayAction, string> = {
    TALK_NOW: t("intent.talkNow"),
    LATER: t("intent.later"),
    PROPOSE: t("intent.propose"),
    SCHEDULE: t("intent.schedule"),
    MESSAGE: t("intent.message"),
    DECLINE: t("intent.decline"),
  };
  const offered = openSlots(intent);
  const suggestions = suggestedSlots(intent.expected_duration_min, Date.now(), 3, intent.deadline);

  return (
    <Screen>
      <Stack.Screen options={{ title: intent.organisation_name ?? t("intent.title") }} />
      <Card style={urgentStyle(intent, th)}>
        <IntentSummary intent={intent} />
        {intent.status === "RESCHEDULED" && intent.proposed_slots?.length ? (
          <View style={{ gap: space.xs }}>
            <Body muted>{t("intent.proposedTimes")}</Body>
            {intent.proposed_slots.map((s) => (
              <Body key={s.start}>{formatTime(s.start, lang)}</Body>
            ))}
          </View>
        ) : null}
      </Card>
      {sent ? (
        <Banner tone="good" testID="answered">
          {t("intent.answered")}
        </Banner>
      ) : null}
      {respond.error ? <Banner tone="error">{errorMessage(respond.error, t)}</Banner> : null}
      <View style={{ gap: space.sm }}>
        {actions.length === 0 ? <Body muted>{t("intent.noActions")}</Body> : null}
        {actions.map((a, i) => (
          <Button
            key={a}
            testID={`action-${a}`}
            title={label[a]}
            variant={a === "DECLINE" ? "ghost" : i === 0 ? "primary" : "secondary"}
            busy={respond.isPending && a === "TALK_NOW"}
            disabled={
              respond.isPending || (a === "SCHEDULE" && offered.length === 0) || (a === "PROPOSE" && suggestions.length === 0)
            }
            onPress={() => onAction(a)}
          />
        ))}
      </View>

      <Sheet visible={sheet === "later"} title={t("intent.later")} onClose={() => setSheet(null)}>
        {respond.error ? <Banner tone="error">{errorMessage(respond.error, t)}</Banner> : null}
        {LATER_MINUTES.map((m) => (
          <Button key={m} title={t("intent.laterIn", { minutes: m })} onPress={() => send({ action: "LATER", later_minutes: m })} testID={`later-${m}`} />
        ))}
      </Sheet>

      <Sheet visible={sheet === "propose"} title={t("intent.proposeTitle")} onClose={() => setSheet(null)}>
        {respond.error ? <Banner tone="error">{errorMessage(respond.error, t)}</Banner> : null}
        {suggestions.map((s) => {
          const on = picked.some((p) => p.start === s.start);
          return (
            <Pressable
              key={s.start}
              accessibilityRole="checkbox"
              accessibilityState={{ checked: on }}
              onPress={() => setPicked(on ? picked.filter((p) => p.start !== s.start) : [...picked, s])}
              style={{ borderWidth: 2, borderColor: on ? th.primary : th.border, borderRadius: radius.md, padding: space.md }}
            >
              <Text style={{ color: th.text, fontSize: 16 }}>
                {on ? "✓ " : ""}
                {formatTime(s.start, lang)}
              </Text>
            </Pressable>
          );
        })}
        <Button
          title={t("intent.proposeSend")}
          variant="primary"
          disabled={picked.length === 0}
          busy={respond.isPending}
          onPress={() => send({ action: "PROPOSE", proposed_slots: picked })}
          testID="propose-send"
        />
      </Sheet>

      <Sheet visible={sheet === "schedule"} title={t("intent.scheduleTitle")} onClose={() => setSheet(null)}>
        {respond.error ? <Banner tone="error">{errorMessage(respond.error, t)}</Banner> : null}
        {offered.map((s) => (
          <Button key={s.start} title={formatTime(s.start, lang)} onPress={() => send({ action: "SCHEDULE", slot: { start: s.start, end: s.end } })} />
        ))}
      </Sheet>

      <Sheet visible={sheet === "message"} title={t("intent.message")} onClose={() => setSheet(null)}>
        {respond.error ? <Banner tone="error">{errorMessage(respond.error, t)}</Banner> : null}
        <Body muted>{t("intent.messageNote")}</Body>
        <Button title={t("app.confirm")} variant="primary" busy={respond.isPending} onPress={() => send({ action: "MESSAGE" })} testID="message-confirm" />
      </Sheet>

      <Sheet visible={sheet === "decline"} title={t("intent.declineTitle")} onClose={() => setSheet(null)}>
        {respond.error ? <Banner tone="error">{errorMessage(respond.error, t)}</Banner> : null}
        {REASONS.map((r: DeclineReason) => (
          <Button key={r} title={t(`intent.reasons.${r}`)} onPress={() => send({ action: "DECLINE", decline_reason: r })} testID={`decline-${r}`} />
        ))}
      </Sheet>
    </Screen>
  );
}
