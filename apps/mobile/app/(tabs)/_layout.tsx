import { Tabs } from "expo-router";
import { useTranslation } from "react-i18next";
import { Text, type ColorValue } from "react-native";

import { useLang } from "../../src/i18n";
import { useTheme } from "../../src/theme";

export default function TabsLayout() {
  const { t } = useTranslation();
  const { rtl } = useLang();
  const th = useTheme();
  const icon = (glyph: string) => ({ color }: { color: ColorValue }) => <Text style={{ color, fontSize: 18 }}>{glyph}</Text>;
  return (
    <Tabs
      screenOptions={{
        headerStyle: { backgroundColor: th.surface },
        headerTintColor: th.text,
        tabBarActiveTintColor: th.primary,
        tabBarStyle: { backgroundColor: th.surface, direction: rtl ? "rtl" : "ltr" },
        sceneStyle: { backgroundColor: th.bg },
      }}
    >
      <Tabs.Screen name="inbox" options={{ title: t("tabs.inbox"), tabBarIcon: icon("✉") }} />
      <Tabs.Screen name="settings" options={{ title: t("tabs.settings"), tabBarIcon: icon("⚙") }} />
    </Tabs>
  );
}
