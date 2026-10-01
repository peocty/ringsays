import {
  ACTION_FOR,
  ContextTokenClient,
  headline,
  isVerifiedOrganisation,
  minutesLeft,
  openSlots,
  RingSaysError,
  suggestedSlots,
  type DisplayAction,
  type IntentDisplay,
  type IntentResponse,
  type Lang,
  type Slot,
} from "@ringsays/client";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, View, type StyleProp, type ViewStyle } from "react-native";

import { loadInstallId, type KeyValueStore } from "./installId";
import { format, strings, type StringKey, type Strings } from "./strings";

export interface RingSaysTheme {
  primary: string;
  onPrimary: string;
  surface: string;
  text: string;
  muted: string;
  border: string;
  good: string;
  goodSoft: string;
  warn: string;
  warnSoft: string;
  danger: string;
  dangerSoft: string;
}

export const defaultTheme: RingSaysTheme = {
  primary: "#0f5c4d",
  onPrimary: "#ffffff",
  surface: "#ffffff",
  text: "#17211e",
  muted: "#5b6a65",
  border: "#d9e0dd",
  good: "#146c43",
  goodSoft: "#e3f4ea",
  warn: "#8a5a00",
  warnSoft: "#fff4d6",
  danger: "#b42318",
  dangerSoft: "#fdecea",
};

interface Ctx {
  client: ContextTokenClient | null;
  /** The install id could not be read or saved; nothing can be shown. */
  storageFailed: boolean;
  lang: Lang;
  t: (k: StringKey, vars?: Record<string, string | number>) => string;
  theme: RingSaysTheme;
}

const RingSaysContext = createContext<Ctx | null>(null);

export interface RingSaysProviderProps {
  /** RingSays API origin given to the organisation at onboarding. */
  baseUrl: string;
  /** Where the install id lives (for example AsyncStorage or expo-secure-store). */
  storage: KeyValueStore;
  language: Lang;
  /** Brand colours; the trust badge colours should stay recognisable. */
  theme?: Partial<RingSaysTheme>;
  /** Override any built in text, per language. */
  strings?: Partial<Record<Lang, Partial<Strings>>>;
  /** Cryptographic random bytes, if global crypto.getRandomValues is not available. */
  random?: (n: number) => Uint8Array;
  fetch?: typeof fetch;
  children: ReactNode;
}

export function RingSaysProvider(props: RingSaysProviderProps) {
  const [installId, setInstallId] = useState<string | null>(null);
  const [storageFailed, setStorageFailed] = useState(false);
  const { storage, random } = props;
  useEffect(() => {
    let live = true;
    setStorageFailed(false);
    loadInstallId(storage, random).then(
      (id) => live && setInstallId(id),
      () => live && setStorageFailed(true),
    );
    return () => {
      live = false;
    };
  }, [storage, random]);
  const langRef = useRef(props.language);
  langRef.current = props.language;
  const client = useMemo(
    () =>
      installId
        ? new ContextTokenClient({ baseUrl: props.baseUrl, installId, language: () => langRef.current, fetch: props.fetch })
        : null,
    [installId, props.baseUrl, props.fetch],
  );
  const value = useMemo<Ctx>(() => {
    const dict = { ...strings[props.language], ...props.strings?.[props.language] } as Strings;
    return {
      client,
      storageFailed,
      lang: props.language,
      t: (k, vars) => format(dict[k], vars),
      theme: { ...defaultTheme, ...props.theme },
    };
  }, [client, storageFailed, props.language, props.strings, props.theme]);
  return <RingSaysContext.Provider value={value}>{props.children}</RingSaysContext.Provider>;
}

function useRingSays(): Ctx {
  const c = useContext(RingSaysContext);
  if (!c) throw new Error("RingSays components must be inside <RingSaysProvider>");
  return c;
}

export type IntentState =
  | { status: "loading" }
  | { status: "ready"; intent: IntentDisplay }
  | { status: "error"; error: RingSaysError };

/** Resolve a Context Token into a displayable intent, and answer it. */
export function useIntent(token: string | null | undefined) {
  const { client, lang, storageFailed } = useRingSays();
  const [state, setState] = useState<IntentState>({ status: "loading" });
  useEffect(() => {
    if (storageFailed) setState({ status: "error", error: new RingSaysError({ status: 0, code: "storage" }) });
  }, [storageFailed]);
  const [sending, setSending] = useState(false);
  /** Failure of the last answer; the intent stays on screen so the customer can try again. */
  const [respondError, setRespondError] = useState<RingSaysError | null>(null);
  // Only the latest request may update the card (the token can change while one is in flight).
  const seq = useRef(0);
  const load = useCallback(async () => {
    if (!client || !token) return;
    const mine = ++seq.current;
    setRespondError(null);
    setState({ status: "loading" });
    try {
      const intent = await client.resolve(token);
      if (mine === seq.current) setState({ status: "ready", intent });
    } catch (e) {
      if (mine === seq.current) setState({ status: "error", error: e instanceof RingSaysError ? e : new RingSaysError({ status: 0 }) });
    }
  }, [client, token]);
  useEffect(() => {
    void load();
  }, [load, lang]);
  const respond = useCallback(
    async (r: IntentResponse): Promise<IntentDisplay | null> => {
      if (!client || !token) return null;
      setSending(true);
      setRespondError(null);
      const mine = ++seq.current;
      try {
        const updated = await client.respond(token, r);
        if (mine === seq.current) setState({ status: "ready", intent: updated });
        return updated;
      } catch (e) {
        if (mine !== seq.current) return null;
        const err = e instanceof RingSaysError ? e : new RingSaysError({ status: 0 });
        // An ended or foreign token cannot be answered at all; anything else can be retried.
        if (err.status === 410 || err.status === 403) setState({ status: "error", error: err });
        else setRespondError(err);
        return null;
      } finally {
        setSending(false);
      }
    },
    [client, token],
  );
  return { state, respond, sending, respondError, reload: load };
}

export interface IntentCardProps {
  token: string;
  /** Called after the customer answered, with the updated intent (for the app's own flow). */
  onAnswered?: (intent: IntentDisplay) => void;
  style?: StyleProp<ViewStyle>;
  now?: number;
}

const LATER = [15, 30, 60] as const;

/**
 * The intent as RingSays shows it: verified organisation, reason in the customer's language, time
 * left, and the answers the organisation's purpose code allows. Trust cues are not configurable:
 * the badge appears only for organisations RingSays verified.
 */
export function IntentCard({ token, onAnswered, style, now }: IntentCardProps) {
  const { t, theme: th, lang } = useRingSays();
  const { state, respond, sending, respondError } = useIntent(token);
  const [panel, setPanel] = useState<"later" | "propose" | "schedule" | null>(null);
  const [picked, setPicked] = useState<Slot[]>([]);
  const dir = lang === "ar" ? "rtl" : "ltr";
  const box = [styles.card, { backgroundColor: th.surface, borderColor: th.border, direction: dir } as ViewStyle, style];

  if (state.status === "loading") {
    return (
      <View style={box} accessibilityLabel={t("loading")}>
        <ActivityIndicator color={th.primary} />
      </View>
    );
  }
  const message = (e: RingSaysError) =>
    e.code === "storage" ? t("error") : e.status === 0 ? t("network") : e.status === 410 ? t("ended") : e.status === 403 ? t("notForThisDevice") : t("error");
  if (state.status === "error") {
    const msg = message(state.error);
    return (
      <View style={box} accessibilityRole="alert" testID="ringsays-error">
        <Text style={{ color: th.text }}>{msg}</Text>
      </View>
    );
  }
  const i = state.intent;
  const send = async (r: IntentResponse) => {
    setPanel(null);
    const updated = await respond(r);
    if (updated) onAnswered?.(updated);
  };
  const verified = isVerifiedOrganisation(i);
  const h = headline(i);
  const left = minutesLeft(i, now);
  const outcome: Partial<Record<IntentDisplay["status"], StringKey>> = {
    ACCEPTED: "accepted",
    RESCHEDULED: "rescheduled",
    SCHEDULED: "scheduled",
    DECLINED: "declined",
    EXPIRED: "ended",
    CANCELLED: "ended",
  };
  const label: Record<DisplayAction, string> = {
    TALK_NOW: t("talkNow"),
    LATER: t("later"),
    PROPOSE: t("propose"),
    SCHEDULE: t("schedule"),
    MESSAGE: t("message"),
    DECLINE: t("decline"),
  };
  const onAction = (a: DisplayAction) => {
    if (a === "LATER") setPanel("later");
    else if (a === "PROPOSE") {
      setPicked([]);
      setPanel("propose");
    } else if (a === "SCHEDULE") setPanel("schedule");
    else if (a === "DECLINE") void send({ action: "DECLINE", decline_reason: "NOT_NOW" });
    else void send({ action: ACTION_FOR[a] });
  };
  const time = (iso: string) =>
    new Intl.DateTimeFormat(lang === "ar" ? "ar-SA-u-ca-gregory-nu-latn" : "en-GB", {
      weekday: "short",
      hour: "numeric",
      minute: "2-digit",
    }).format(new Date(iso));

  return (
    <View style={[box, i.priority === "URGENT" && { borderColor: th.danger, borderWidth: 2 }]} testID="ringsays-intent">
      <View style={styles.row}>
        <Text style={[styles.org, { color: th.text }]} numberOfLines={1}>
          {i.organisation_name ?? ""}
        </Text>
        {i.priority === "URGENT" || i.priority === "IMPORTANT" ? (
          <Text style={[styles.pill, { backgroundColor: i.priority === "URGENT" ? th.dangerSoft : th.border, color: i.priority === "URGENT" ? th.danger : th.text }]}>
            {i.priority === "URGENT" ? t("urgent") : t("important")}
          </Text>
        ) : null}
      </View>
      <Text
        testID={verified ? "ringsays-verified" : "ringsays-unverified"}
        style={[styles.pill, { backgroundColor: verified ? th.goodSoft : th.warnSoft, color: verified ? th.good : th.warn }]}
      >
        {verified ? "✓ " : "! "}
        {verified ? (i.verification_level === "ORG_AGENT_NUMBER" ? t("verifiedNumber") : t("verified")) : t("unverified")}
      </Text>
      <Text style={[styles.why, { color: h.verified ? th.text : th.muted, fontStyle: h.verified ? "normal" : "italic" }]} testID="ringsays-why">
        {h.text}
      </Text>
      <View style={[styles.row, { flexWrap: "wrap" }]}>
        {i.agent_display_name ? <Text style={{ color: th.muted }}>{t("from", { name: i.agent_display_name })}</Text> : null}
        <Text style={{ color: th.muted }}>{t("duration", { minutes: i.expected_duration_min })}</Text>
        {i.actions.length > 0 ? <Text style={{ color: th.muted }}>{left > 0 ? t("endsIn", { minutes: left }) : t("ended")}</Text> : null}
      </View>
      {i.masked_reference ? <Text style={{ color: th.muted }}>{t("reference", { ref: `⁦${i.masked_reference}⁩` })}</Text> : null}
      {outcome[i.status] ? (
        <Text style={{ color: th.good, fontWeight: "600" }} testID="ringsays-outcome">
          {t(outcome[i.status]!)}
        </Text>
      ) : null}
      {respondError ? (
        <Text accessibilityRole="alert" style={{ color: th.danger }} testID="ringsays-respond-error">
          {message(respondError)}
        </Text>
      ) : null}

      {panel === "later" ? (
        <View style={styles.actions}>
          {LATER.map((m) => (
            <Btn key={m} title={t("laterIn", { minutes: m })} onPress={() => void send({ action: "LATER", later_minutes: m })} />
          ))}
          <Btn title={t("cancel")} ghost onPress={() => setPanel(null)} />
        </View>
      ) : panel === "propose" ? (
        <View style={styles.actions}>
          {suggestedSlots(i.expected_duration_min, now).map((s) => {
            const on = picked.some((p) => p.start === s.start);
            return (
              <Btn
                key={s.start}
                title={`${on ? "✓ " : ""}${time(s.start)}`}
                selected={on}
                onPress={() => setPicked(on ? picked.filter((p) => p.start !== s.start) : [...picked, s])}
              />
            );
          })}
          <Btn title={t("send")} primary disabled={!picked.length} onPress={() => void send({ action: "PROPOSE", proposed_slots: picked })} />
          <Btn title={t("cancel")} ghost onPress={() => setPanel(null)} />
        </View>
      ) : panel === "schedule" ? (
        <View style={styles.actions}>
          {openSlots(i, now).map((s) => (
            <Btn key={s.start} title={time(s.start)} onPress={() => void send({ action: "SCHEDULE", slot: { start: s.start, end: s.end } })} />
          ))}
          <Btn title={t("cancel")} ghost onPress={() => setPanel(null)} />
        </View>
      ) : (
        <View style={styles.actions}>
          {i.actions.map((a, idx) => (
            <Btn
              key={a}
              testID={`ringsays-action-${a}`}
              title={label[a]}
              primary={idx === 0}
              ghost={a === "DECLINE"}
              disabled={sending || (a === "SCHEDULE" && openSlots(i, now).length === 0)}
              onPress={() => onAction(a)}
            />
          ))}
        </View>
      )}
    </View>
  );

}

interface BtnProps {
  title: string;
  onPress: () => void;
  primary?: boolean;
  ghost?: boolean;
  selected?: boolean;
  disabled?: boolean;
  testID?: string;
}

function Btn(p: BtnProps) {
  const th = useRingSays().theme;
  const bg = p.primary ? th.primary : p.ghost ? "transparent" : th.surface;
  const fg = p.primary ? th.onPrimary : p.ghost ? th.primary : th.text;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ disabled: !!p.disabled, selected: !!p.selected }}
      testID={p.testID}
      disabled={p.disabled}
      onPress={p.onPress}
      style={[styles.btn, { backgroundColor: bg, borderColor: p.selected ? th.primary : p.ghost ? "transparent" : th.border, opacity: p.disabled ? 0.5 : 1 }]}
    >
      <Text style={{ color: fg, fontWeight: "600", fontSize: 16 }}>{p.title}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: { borderWidth: 1, borderRadius: 16, padding: 16, gap: 10 },
  row: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8 },
  org: { fontSize: 18, fontWeight: "700", flexShrink: 1 },
  pill: { alignSelf: "flex-start", borderRadius: 999, paddingHorizontal: 10, paddingVertical: 3, fontWeight: "700", fontSize: 13, overflow: "hidden" },
  why: { fontSize: 17, lineHeight: 25 },
  actions: { gap: 8 },
  btn: { minHeight: 48, borderRadius: 12, borderWidth: 1, alignItems: "center", justifyContent: "center", paddingHorizontal: 16 },
});
