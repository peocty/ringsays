import type { PushTokens } from "@ringsays/client";
import * as Device from "expo-device";
import * as Notifications from "expo-notifications";
import { Platform } from "react-native";

import { config } from "../config";

/**
 * Native push token for RingSays' own push service (APNs on iOS, FCM on Android). RingSays sends the
 * intent id and kind only; the app fetches the details over the authenticated API, so nothing about
 * the customer or the call is in the push itself.
 */
export async function pushTokens(): Promise<PushTokens | undefined> {
  if (Platform.OS === "web") return config.previewPushToken ? { fcm: config.previewPushToken } : undefined;
  if (!Device.isDevice) return undefined;
  const settings = await Notifications.getPermissionsAsync();
  let granted = settings.granted;
  if (!granted && settings.canAskAgain) granted = (await Notifications.requestPermissionsAsync()).granted;
  if (!granted) return undefined;
  if (Platform.OS === "android") {
    await Notifications.setNotificationChannelAsync("intents", {
      name: "Incoming calls and requests",
      importance: Notifications.AndroidImportance.HIGH,
    });
  }
  const token = await Notifications.getDevicePushTokenAsync();
  return Platform.OS === "ios" ? { apns: String(token.data) } : { fcm: String(token.data) };
}

/** Intent id from a notification's data, if it is a RingSays intent notification. */
export function intentIdFrom(n: Notifications.Notification | Notifications.NotificationResponse): string | null {
  const content = "notification" in n ? n.notification.request.content : n.request.content;
  const id = (content.data as Record<string, unknown> | undefined)?.intent_id;
  return typeof id === "string" && /^[0-9a-f-]{36}$/i.test(id) ? id : null;
}

export function configureForeground(): void {
  if (Platform.OS === "web") return;
  Notifications.setNotificationHandler({
    handleNotification: async () => ({
      shouldShowBanner: true,
      shouldShowList: true,
      shouldPlaySound: true,
      shouldSetBadge: false,
    }),
  });
}
