import {
  headline,
  isUrgent,
  isVerifiedOrganisation,
  minutesLeft,
  type IntentDisplay,
} from "@ringsays/client";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import {
  ActivityIndicator,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
  type PressableProps,
  type StyleProp,
  type ViewStyle,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { useLang } from "../i18n";
import { formatTime, ltr } from "../lib/format";
import { radius, space, useTheme, type Theme } from "../theme";

/** Root of every screen: sets layout direction from the app language and the background. */
export function Screen({ children, scroll = true }: { children: ReactNode; scroll?: boolean }) {
  const { rtl } = useLang();
  const t = useTheme();
  const body = scroll ? (
    <ScrollView contentContainerStyle={styles.screenBody} keyboardShouldPersistTaps="handled">
      {children}
    </ScrollView>
  ) : (
    <View style={[styles.screenBody, { flex: 1 }]}>{children}</View>
  );
  return (
    <SafeAreaView edges={["bottom", "left", "right"]} style={{ flex: 1, backgroundColor: t.bg, direction: rtl ? "rtl" : "ltr" }}>
      {body}
    </SafeAreaView>
  );
}

type Variant = "primary" | "secondary" | "danger" | "ghost";

export function Button({
  title,
  variant = "secondary",
  busy,
  disabled,
  style,
  ...rest
}: PressableProps & { title: string; variant?: Variant; busy?: boolean; style?: StyleProp<ViewStyle> }) {
  const t = useTheme();
  const bg = { primary: t.primary, secondary: t.surface, danger: t.danger, ghost: "transparent" }[variant];
  const fg = { primary: t.onPrimary, secondary: t.text, danger: "#fff", ghost: t.primary }[variant];
  const off = disabled || busy;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ disabled: !!off, busy: !!busy }}
      disabled={off}
      style={({ pressed }) => [
        styles.button,
        { backgroundColor: bg, borderColor: variant === "secondary" ? t.border : bg, opacity: off ? 0.55 : pressed ? 0.85 : 1 },
        style,
      ]}
      {...rest}
    >
      {busy ? <ActivityIndicator color={fg} style={{ marginEnd: space.sm }} /> : null}
      <Text style={[styles.buttonText, { color: fg }]}>{title}</Text>
    </Pressable>
  );
}

export function Card({ children, style }: { children: ReactNode; style?: StyleProp<ViewStyle> }) {
  const t = useTheme();
  return <View style={[styles.card, { backgroundColor: t.surface, borderColor: t.border }, style]}>{children}</View>;
}

export function Title({ children }: { children: ReactNode }) {
  const t = useTheme();
  return (
    <Text accessibilityRole="header" style={[styles.title, { color: t.text }]}>
      {children}
    </Text>
  );
}

export function Body({ children, muted, style }: { children: ReactNode; muted?: boolean; style?: object }) {
  const t = useTheme();
  return <Text style={[styles.body, { color: muted ? t.muted : t.text }, style]}>{children}</Text>;
}

export function Banner({ tone, children, testID }: { tone: "error" | "warn" | "good"; children: ReactNode; testID?: string }) {
  const t = useTheme();
  const c = { error: [t.dangerSoft, t.danger], warn: [t.warnSoft, t.warn], good: [t.goodSoft, t.good] }[tone];
  return (
    <View accessibilityRole="alert" testID={testID} style={[styles.banner, { backgroundColor: c[0] }]}>
      <Text style={{ color: c[1], fontSize: 15 }}>{children}</Text>
    </View>
  );
}

export function Loading() {
  const { t } = useTranslation();
  const th = useTheme();
  return (
    <View style={{ padding: space.xl, alignItems: "center" }} accessibilityLabel={t("app.loading")}>
      <ActivityIndicator color={th.primary} />
    </View>
  );
}

export function Segmented<T extends string>({
  options,
  value,
  onChange,
}: {
  options: { id: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
}) {
  const t = useTheme();
  return (
    <View accessibilityRole="tablist" style={[styles.segmented, { backgroundColor: t.surface2 }]}>
      {options.map((o) => {
        const on = o.id === value;
        return (
          <Pressable
            key={o.id}
            accessibilityRole="tab"
            accessibilityState={{ selected: on }}
            onPress={() => onChange(o.id)}
            style={[styles.segment, on && { backgroundColor: t.surface }]}
          >
            <Text style={{ color: on ? t.primary : t.muted, fontWeight: "600" }}>{o.label}</Text>
          </Pressable>
        );
      })}
    </View>
  );
}

/** Bottom sheet style choice list (plain RN Modal, works on iOS, Android and web). */
export function Sheet({
  visible,
  title,
  onClose,
  children,
}: {
  visible: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const t = useTheme();
  const { t: tr } = useTranslation();
  const { rtl } = useLang();
  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <Pressable style={styles.sheetBackdrop} onPress={onClose} accessibilityLabel={tr("app.cancel")} />
      <View style={[styles.sheet, { backgroundColor: t.surface, direction: rtl ? "rtl" : "ltr" }]} accessibilityViewIsModal>
        <Text accessibilityRole="header" style={[styles.sheetTitle, { color: t.text }]}>
          {title}
        </Text>
        {children}
        <Button title={tr("app.cancel")} variant="ghost" onPress={onClose} />
      </View>
    </Modal>
  );
}

// Intent presentation

export function TrustBadge({ intent }: { intent: Pick<IntentDisplay, "verification_level"> }) {
  const { t } = useTranslation();
  const th = useTheme();
  const verified = isVerifiedOrganisation(intent);
  const label = !verified
    ? t("intent.unverified")
    : intent.verification_level === "ORG_AGENT_NUMBER"
      ? t("intent.verifiedNumber")
      : t("intent.verified");
  return (
    <View
      testID={verified ? "badge-verified" : "badge-unverified"}
      style={[styles.badge, { backgroundColor: verified ? th.goodSoft : th.warnSoft }]}
    >
      <Text style={{ color: verified ? th.good : th.warn, fontWeight: "700", fontSize: 13 }}>
        {verified ? "✓ " : "! "}
        {label}
      </Text>
    </View>
  );
}

export function PriorityTag({ priority }: { priority: IntentDisplay["priority"] }) {
  const { t } = useTranslation();
  const th = useTheme();
  if (priority !== "URGENT" && priority !== "IMPORTANT") return null;
  const urgent = priority === "URGENT";
  return (
    <View style={[styles.badge, { backgroundColor: urgent ? th.dangerSoft : th.surface2 }]}>
      <Text style={{ color: urgent ? th.danger : th.text, fontWeight: "700", fontSize: 13 }}>
        {urgent ? t("intent.urgent") : t("intent.important")}
      </Text>
    </View>
  );
}

export function IntentSummary({ intent, now = Date.now() }: { intent: IntentDisplay; now?: number }) {
  const { t } = useTranslation();
  const { lang } = useLang();
  const th = useTheme();
  const h = headline(intent);
  const left = minutesLeft(intent, now);
  const open = ["REQUESTED", "DELIVERED", "RESCHEDULED"].includes(intent.status);
  return (
    <View style={{ gap: space.sm }}>
      <View style={styles.row}>
        <Text style={[styles.org, { color: th.text }]} numberOfLines={1}>
          {intent.organisation_name ?? t("intent.unverified")}
        </Text>
        <PriorityTag priority={intent.priority} />
      </View>
      <TrustBadge intent={intent} />
      <Text
        style={[styles.why, { color: h.verified ? th.text : th.muted, fontStyle: h.verified ? "normal" : "italic" }]}
        testID="intent-why"
      >
        {h.text}
      </Text>
      <View style={[styles.row, { flexWrap: "wrap", gap: space.md }]}>
        {intent.agent_display_name ? <Body muted>{t("intent.agent", { name: intent.agent_display_name })}</Body> : null}
        <Body muted>{t("intent.duration", { minutes: intent.expected_duration_min })}</Body>
        {intent.scheduled_slot ? (
          <Body muted>{t("intent.scheduledFor", { time: formatTime(intent.scheduled_slot.start, lang) })}</Body>
        ) : open ? (
          <Body muted>{left > 0 ? t("intent.expiresIn", { minutes: left }) : t("intent.expired")}</Body>
        ) : null}
      </View>
      <Body muted>{t(`intent.status.${intent.status}`)}</Body>
      {!isVerifiedOrganisation(intent) ? <Banner tone="warn">{t("intent.unverifiedWarning")}</Banner> : null}
      {intent.masked_reference ? <Body muted>{t("intent.reference", { ref: ltr(intent.masked_reference) })}</Body> : null}
    </View>
  );
}

export function urgentStyle(intent: IntentDisplay, th: Theme): StyleProp<ViewStyle> {
  return isUrgent(intent) ? { borderColor: th.danger, borderWidth: 2 } : undefined;
}

const styles = StyleSheet.create({
  screenBody: { padding: space.lg, gap: space.lg },
  button: {
    minHeight: 48,
    borderRadius: radius.md,
    borderWidth: 1,
    paddingHorizontal: space.lg,
    alignItems: "center",
    justifyContent: "center",
    flexDirection: "row",
  },
  buttonText: { fontSize: 16, fontWeight: "600" },
  card: { borderRadius: radius.lg, borderWidth: 1, padding: space.lg, gap: space.md },
  title: { fontSize: 24, fontWeight: "700", textAlign: "auto" },
  body: { fontSize: 15, lineHeight: 22, textAlign: "auto" },
  banner: { borderRadius: radius.md, padding: space.md },
  segmented: { flexDirection: "row", borderRadius: radius.md, padding: 4 },
  segment: { flex: 1, alignItems: "center", paddingVertical: 10, borderRadius: radius.sm },
  sheetBackdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.35)" },
  sheet: { padding: space.lg, gap: space.sm, borderTopLeftRadius: radius.lg, borderTopRightRadius: radius.lg },
  sheetTitle: { fontSize: 18, fontWeight: "700", marginBottom: space.sm },
  badge: { alignSelf: "flex-start", borderRadius: 999, paddingHorizontal: 10, paddingVertical: 3 },
  row: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: space.sm },
  org: { fontSize: 18, fontWeight: "700", flexShrink: 1 },
  why: { fontSize: 17, lineHeight: 25 },
});
