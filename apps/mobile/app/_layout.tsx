import { RingSaysError } from "@ringsays/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import * as Notifications from "expo-notifications";
import { Stack, router } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Platform } from "react-native";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { Loading } from "../src/components/ui";
import { initI18n, useLang } from "../src/i18n";
import { AuthProvider, useAuth } from "../src/lib/auth";
import { configureForeground, intentIdFrom } from "../src/lib/push";
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

function Routes() {
  const { t } = useTranslation();
  const { rtl } = useLang();
  const th = useTheme();
  const { status } = useAuth();

  // A tapped "organisation is calling" notification opens that intent.
  useEffect(() => {
    if (Platform.OS === "web" || status !== "signedIn") return;
    const open = (r: Notifications.NotificationResponse) => {
      const id = intentIdFrom(r);
      if (id) router.push(`/intent/${id}`);
    };
    void Notifications.getLastNotificationResponseAsync().then((r) => r && open(r));
    const sub = Notifications.addNotificationResponseReceivedListener(open);
    return () => sub.remove();
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
