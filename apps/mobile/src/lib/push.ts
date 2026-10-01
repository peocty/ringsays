import type { PushTokens } from "@ringsays/client";
import * as Device from "expo-device";
import * as Notifications from "expo-notifications";
import { AppState, Platform } from "react-native";

import { config } from "../config";

/**
 * Native push token for RingSays' own push service (APNs on iOS, FCM on Android). RingSays sends the
 * intent id and kind only; the app fetches the details over the authenticated API, so nothing about
 * the customer or the call is in the push itself.
 */
/** `ask`: show the permission prompt if it may still be shown (sign in); false for silent checks. */
export async function pushTokens(ask = true): Promise<PushTokens | undefined> {
  if (Platform.OS === "web") return config.previewPushToken ? { fcm: config.previewPushToken } : undefined;
  if (!Device.isDevice) return undefined;
  // Android 13+: the permission prompt appears only once a notification channel exists.
  if (Platform.OS === "android") {
    await Notifications.setNotificationChannelAsync("intents", {
      name: "Incoming calls and requests",
      importance: Notifications.AndroidImportance.HIGH,
    });
  }
  const settings = await Notifications.getPermissionsAsync();
  let granted = settings.granted;
  if (!granted && settings.canAskAgain && ask) granted = (await Notifications.requestPermissionsAsync()).granted;
  if (!granted) return undefined;
  return asTokens(await Notifications.getDevicePushTokenAsync());
}

function asTokens(token: Notifications.DevicePushToken): PushTokens {
  return Platform.OS === "ios" ? { apns: String(token.data) } : { fcm: String(token.data) };
}

/**
 * While signed in: send the current push token on start and whenever it changes (APNs/FCM rotate
 * tokens, permission granted later in system settings). Returns an unsubscribe function.
 */
export function keepPushTokensCurrent(update: (t: PushTokens) => Promise<void>): () => void {
  if (Platform.OS === "web" || !Device.isDevice) return () => undefined;
  let last = "";
  const send = (t: PushTokens | undefined) => {
    const key = JSON.stringify(t ?? null);
    if (!t || key === last) return;
    last = key;
    update(t).catch(() => {
      last = ""; // retry on next foreground
    });
  };
  const check = () => void pushTokens(false).then(send, () => undefined);
  check();
  const sub = Notifications.addPushTokenListener((t) => send(asTokens(t)));
  const app = AppState.addEventListener("change", (s) => s === "active" && check());
  return () => {
    sub.remove();
    app.remove();
  };
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
