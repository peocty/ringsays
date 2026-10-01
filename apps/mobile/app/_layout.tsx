import { RingSaysError } from "@ringsays/client";
import { focusManager, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import * as Notifications from "expo-notifications";
import { Stack, router } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { AppState, Platform } from "react-native";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { Loading } from "../src/components/ui";
import { initI18n, useLang } from "../src/i18n";
import { AuthProvider, useAuth } from "../src/lib/auth";
import { configureForeground, intentIdFrom, keepPushTokensCurrent } from "../src/lib/push";
import { getSession } from "../src/lib/session";
import { useTheme } from "../src/theme";

const client = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (n, err) => (err instanceof RingSaysError && err.status > 0 && err.status < 500 ? false : n < 2),
      staleTime: 10_000,
    },
  },
});

configureForeground();

// Phones: queries refetch when the app comes back to the foreground, and polling pauses in background.
if (Platform.OS !== "web") {
  focusManager.setEventListener((setFocused) => {
    const sub = AppState.addEventListener("change", (s) => setFocused(s === "active"));
    return () => sub.remove();
  });
}

function Routes() {
  const { t } = useTranslation();
  const { lang, rtl } = useLang();
  const th = useTheme();
  const { status } = useAuth();

  // Organisation reasons and names come from the server in the chosen language: reload on switch.
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    void client.invalidateQueries();
  }, [lang]);

  // A tapped "organisation is calling" notification opens that intent.
  useEffect(() => {
    if (Platform.OS === "web" || status !== "signedIn") return;
    const open = (r: Notifications.NotificationResponse) => {
      const id = intentIdFrom(r);
      if (id) router.push(`/intent/${id}`);
    };
    // Opened from a notification while signed out: handle it once, then clear it so a later sign in
    // (maybe by someone else) does not reopen it.
    void Notifications.getLastNotificationResponseAsync().then((r) => {
      if (!r) return;
      Notifications.clearLastNotificationResponseAsync().catch(() => undefined);
      open(r);
    });
    const sub = Notifications.addNotificationResponseReceivedListener(open);
    const stopTokens = keepPushTokensCurrent(async (t) => (await getSession()).updatePushTokens(t));
    return () => {
      sub.remove();
      stopTokens();
    };
  }, [status]);

  if (status === "loading") return <Loading />;
  return (
    <Stack
      screenOptions={{
        headerStyle: { backgroundColor: th.surface },
        headerTintColor: th.text,
        contentStyle: { backgroundColor: th.bg, direction: rtl ? "rtl" : "ltr" },
        headerBackTitle: t("app.back"),
      }}
    >
      <Stack.Protected guard={status === "signedIn"}>
        <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
        <Stack.Screen name="intent/[id]" options={{ title: t("intent.title") }} />
      </Stack.Protected>
      <Stack.Protected guard={status !== "signedIn"}>
        <Stack.Screen name="sign-in" options={{ headerShown: false }} />
      </Stack.Protected>
    </Stack>
  );
}

export default function RootLayout() {
  const [ready, setReady] = useState(false);
  useEffect(() => {
    void initI18n().finally(() => setReady(true));
  }, []);
  if (!ready) return null;
  return (
    <SafeAreaProvider>
      <QueryClientProvider client={client}>
        <AuthProvider>
          <StatusBar style="auto" />
          <Routes />
        </AuthProvider>
      </QueryClientProvider>
    </SafeAreaProvider>
  );
}
