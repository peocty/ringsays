import { IntentCard, RingSaysProvider } from "@ringsays/react-native-sdk";
import { getRandomBytes } from "expo-crypto";
import { StatusBar } from "expo-status-bar";
import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  AppState,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
  useColorScheme,
} from "react-native";
import { SafeAreaProvider, SafeAreaView } from "react-native-safe-area-context";

import { bank, BankError, type CustomerSummary, type Message } from "./bank";
import { config } from "./config";
import { fill, text, type Lang } from "./i18n";
import { sdkStorage } from "./storage";

const random = (n: number) => getRandomBytes(n);
const POLL_MS = 5000;

const palette = {
  light: { bg: "#f4f6f8", surface: "#ffffff", text: "#14212b", muted: "#5d6b76", border: "#d8dee4", brand: "#1d3f72", onBrand: "#ffffff", danger: "#b42318" },
  dark: { bg: "#0f1419", surface: "#182029", text: "#e6edf3", muted: "#9aa7b2", border: "#2c3742", brand: "#6f9be0", onBrand: "#0f1419", danger: "#ff8a7a" },
};
type Palette = (typeof palette)["light"];

interface Session {
  token: string;
  customer: { id: string; name: { en: string; ar: string }; language: Lang };
}

export default function App() {
  const scheme = useColorScheme() === "dark" ? "dark" : "light";
  const c = palette[scheme];
  const [lang, setLang] = useState<Lang>("ar");
  const [session, setSession] = useState<Session | null>(null);
  const t = text[lang];
  const rtl = lang === "ar";

  return (
    <SafeAreaProvider>
      {/* The SDK: brand colour only; RingSays trust cues (verified badge) stay as RingSays draws them. */}
      <RingSaysProvider
        baseUrl={config.ringsaysApi}
        storage={sdkStorage}
        random={random}
        language={lang}
        theme={scheme === "dark" ? { primary: c.brand, onPrimary: c.onBrand, surface: c.surface, text: c.text, muted: c.muted, border: c.border } : { primary: c.brand, onPrimary: c.onBrand }}
      >
        <SafeAreaView style={{ flex: 1, backgroundColor: c.bg, direction: rtl ? "rtl" : "ltr" }}>
          <StatusBar style="auto" />
          <View style={[styles.header, { backgroundColor: c.surface, borderColor: c.border }]}>
            <View style={styles.brandRow}>
              <View style={[styles.mark, { backgroundColor: c.brand }]}>
                <Text style={{ color: c.onBrand, fontWeight: "800", fontSize: 12 }}>MB</Text>
              </View>
              <View>
                <Text style={[styles.brand, { color: c.text }]}>{t.bank}</Text>
                <Text style={{ color: c.muted, fontSize: 11 }}>{t.demo}</Text>
              </View>
            </View>
            <Pressable accessibilityRole="button" testID="lang-toggle" onPress={() => setLang(rtl ? "en" : "ar")} hitSlop={10}>
              <Text style={{ color: c.brand, fontWeight: "600" }}>{t.switch}</Text>
            </Pressable>
          </View>
          {session ? (
            <Home
              c={c}
              lang={lang}
              session={session}
              onSignOut={() => {
                void bank.logout(session.token).catch(() => undefined);
                setSession(null);
              }}
            />
          ) : (
            <SignIn
              c={c}
              lang={lang}
              onSignedIn={(s) => {
                setLang(s.customer.language);
                setSession(s);
              }}
            />
          )}
        </SafeAreaView>
      </RingSaysProvider>
    </SafeAreaProvider>
  );
}

function SignIn({ c, lang, onSignedIn }: { c: Palette; lang: Lang; onSignedIn: (s: Session) => void }) {
  const t = text[lang];
  const [customers, setCustomers] = useState<CustomerSummary[] | null>(null);
  const [picked, setPicked] = useState<string | null>(null);
  const [pin, setPin] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    bank.customers().then(
      (list) => {
        setCustomers(list);
        setPicked((p) => p ?? list[0]?.id ?? null);
      },
      () => setError(t.error),
    );
  }, [t.error]);

  const submit = async () => {
    if (!picked || pin.length !== 4) return;
    setBusy(true);
    setError(null);
    try {
      const r = await bank.login(picked, pin);
      onSignedIn({ token: r.token, customer: r.customer });
    } catch (e) {
      setError(e instanceof BankError && e.status !== 0 ? e.message : t.error);
      setPin("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <ScrollView contentContainerStyle={styles.body} keyboardShouldPersistTaps="handled">
      <View style={[styles.card, { backgroundColor: c.surface, borderColor: c.border }]}>
        <Text accessibilityRole="header" style={[styles.h1, { color: c.text }]}>
          {t.signIn}
        </Text>
        <Text style={[styles.label, { color: c.text }]}>{t.customer}</Text>
        {customers === null && !error ? <ActivityIndicator color={c.brand} /> : null}
        {customers?.map((x) => {
          const on = x.id === picked;
          return (
            <Pressable
              key={x.id}
              testID={`customer-${x.id}`}
              accessibilityRole="radio"
              accessibilityState={{ checked: on }}
              onPress={() => setPicked(x.id)}
              style={[styles.option, { borderColor: on ? c.brand : c.border }]}
            >
              <Text style={{ color: c.text, fontWeight: "600" }}>{x.name[lang]}</Text>
              <Text style={{ color: c.muted, writingDirection: "ltr" }}>{x.phoneHint}</Text>
            </Pressable>
          );
        })}
        <Text style={[styles.label, { color: c.text }]}>{t.pin}</Text>
        <TextInput
          testID="pin-input"
          accessibilityLabel={t.pin}
          value={pin}
          onChangeText={(v) => setPin(v.replace(/[٠-٩]/g, (d) => String(d.charCodeAt(0) - 0x0660)).replace(/\D/g, "").slice(0, 4))}
          keyboardType="number-pad"
          secureTextEntry
          maxLength={4}
          onSubmitEditing={() => void submit()}
          style={[styles.input, { color: c.text, borderColor: c.border, direction: "ltr" }]}
        />
        <Text style={{ color: c.muted, fontSize: 13 }}>{t.pinHint}</Text>
        <Pressable
          testID="sign-in"
          accessibilityRole="button"
          accessibilityState={{ disabled: busy || pin.length !== 4 }}
          disabled={busy || pin.length !== 4}
          onPress={() => void submit()}
          style={[styles.button, { backgroundColor: c.brand, opacity: busy || pin.length !== 4 ? 0.5 : 1 }]}
        >
          {busy ? <ActivityIndicator color={c.onBrand} /> : <Text style={{ color: c.onBrand, fontWeight: "700", fontSize: 16 }}>{t.signIn}</Text>}
        </Pressable>
        {error ? (
          <Text accessibilityRole="alert" testID="sign-in-error" style={{ color: c.danger }}>
            {error}
          </Text>
        ) : null}
      </View>
    </ScrollView>
  );
}

function Home({ c, lang, session, onSignOut }: { c: Palette; lang: Lang; session: Session; onSignOut: () => void }) {
  const t = text[lang];
  const [messages, setMessages] = useState<Message[] | null>(null);
  const [error, setError] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      setMessages(await bank.messages(session.token));
      setError(false);
    } catch (e) {
      if (e instanceof BankError && e.status === 401) return onSignOut();
      setError(true);
    }
  }, [session.token, onSignOut]);

  // The bank's own channel: poll while open (a real app would also use its own push).
  useEffect(() => {
    void load();
    let id = setInterval(() => void load(), POLL_MS);
    const sub = AppState.addEventListener("change", (s) => {
      clearInterval(id);
      if (s === "active") {
        void load();
        id = setInterval(() => void load(), POLL_MS);
      }
    });
    return () => {
      clearInterval(id);
      sub.remove();
    };
  }, [load]);

  const open = messages?.filter((m) => m.contextToken) ?? [];
  const earlier = messages?.filter((m) => !m.contextToken) ?? [];
  const time = (iso: string) =>
    new Intl.DateTimeFormat(lang === "ar" ? "ar-SA-u-ca-gregory-nu-latn" : "en-GB", { weekday: "short", hour: "numeric", minute: "2-digit" }).format(new Date(iso));

  return (
    <ScrollView
      contentContainerStyle={styles.body}
      refreshControl={
        <RefreshControl
          refreshing={refreshing}
          onRefresh={async () => {
            setRefreshing(true);
            await load();
            setRefreshing(false);
          }}
        />
      }
    >
      <View style={styles.rowBetween}>
        <Text style={[styles.h1, { color: c.text }]} testID="hello">
          {fill(t.hello, { name: session.customer.name[lang] })}
        </Text>
        <Pressable accessibilityRole="button" testID="sign-out" onPress={onSignOut} hitSlop={10}>
          <Text style={{ color: c.brand, fontWeight: "600" }}>{t.signOut}</Text>
        </Pressable>
      </View>

      <View style={{ gap: 4 }}>
        <Text accessibilityRole="header" style={[styles.h2, { color: c.text }]}>
          {t.fromBank}
        </Text>
        <Text style={{ color: c.muted }}>{t.fromBankHint}</Text>
      </View>
      {error ? (
        <Text accessibilityRole="alert" style={{ color: c.danger }}>
          {t.error}
        </Text>
      ) : null}
      {messages === null && !error ? <ActivityIndicator color={c.brand} /> : null}
      {messages !== null && open.length === 0 ? (
        <Text style={{ color: c.muted }} testID="nothing">
          {t.nothing}
        </Text>
      ) : null}
      {open.map((m) => (
        <View key={m.intentId} testID={`message-${m.intentId}`}>
          <IntentCard token={m.contextToken!} onAnswered={() => void load()} />
        </View>
      ))}

      {earlier.length ? (
        <View style={[styles.card, { backgroundColor: c.surface, borderColor: c.border }]}>
          <Text accessibilityRole="header" style={[styles.h2, { color: c.text }]}>
            {t.history}
          </Text>
          {earlier.map((m) => (
            <View key={m.intentId} style={[styles.historyRow, { borderColor: c.border }]} testID={`history-${m.intentId}`}>
              <Text style={{ color: c.text, fontWeight: "600" }}>{m.title?.[lang] ?? ""}</Text>
              <Text style={{ color: c.muted }}>
                {t.status[m.status] ?? m.status}
                {m.scheduledSlot && m.status === "SCHEDULED" ? ` · ${fill(t.scheduledFor, { time: time(m.scheduledSlot.start) })}` : ""}
              </Text>
            </View>
          ))}
        </View>
      ) : null}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  header: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: 16, paddingVertical: 12, borderBottomWidth: 1 },
  brandRow: { flexDirection: "row", alignItems: "center", gap: 10 },
  mark: { width: 32, height: 32, borderRadius: 8, alignItems: "center", justifyContent: "center" },
  brand: { fontSize: 18, fontWeight: "800" },
  body: { padding: 16, gap: 16 },
  card: { borderWidth: 1, borderRadius: 16, padding: 16, gap: 10 },
  h1: { fontSize: 22, fontWeight: "700", flexShrink: 1 },
  h2: { fontSize: 17, fontWeight: "700" },
  label: { fontWeight: "600", marginTop: 4 },
  option: { borderWidth: 2, borderRadius: 12, padding: 12, flexDirection: "row", justifyContent: "space-between" },
  input: { borderWidth: 1, borderRadius: 12, padding: 12, fontSize: 22, letterSpacing: 8, textAlign: "center" },
  button: { minHeight: 48, borderRadius: 12, alignItems: "center", justifyContent: "center", marginTop: 4 },
  rowBetween: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 12 },
  historyRow: { borderTopWidth: 1, paddingTop: 10, gap: 2 },
});
