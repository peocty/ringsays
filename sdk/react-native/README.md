# @ringsays/react-native-sdk

Show and answer a RingSays intent inside an organisation's own React Native app. The customer sees
the same trust cues as in the RingSays app (verified badge, reason, time left, allowed answers)
without a RingSays account.

## Flow

1. Organisation backend creates an intent with `channel_preference` including `APP_SDK`; the
   response carries `context_token`.
2. Backend delivers token to its own app (its push, in app inbox).
3. App renders `<IntentCard token=… />`. First install that opens a token owns it; any other
   install gets 403, an ended intent 410.

Token never leaves the organisation's channel, never goes into logs or analytics.

## Use

```tsx
import AsyncStorage from "@react-native-async-storage/async-storage";
import "react-native-get-random-values"; // or pass `random`
import { IntentCard, RingSaysProvider } from "@ringsays/react-native-sdk";

<RingSaysProvider baseUrl="https://api.ringsays.example" storage={AsyncStorage} language={lang}>
  <IntentCard token={token} onAnswered={(i) => track(i.status)} />
</RingSaysProvider>;
```

| Prop | Purpose |
| --- | --- |
| `storage` | `getItem`/`setItem` store for install id (AsyncStorage, expo-secure-store wrapper) |
| `language` | `en` or `ar`; Arabic lays out right to left |
| `theme` | brand colours; badge semantics stay fixed: verified badge only for RingSays verified organisations |
| `strings` | per language text overrides |
| `random` | random bytes when `crypto.getRandomValues` missing |
| `fetch` | custom fetch (certificate pinning, tests) |

`useIntent(token)` gives `{ state, respond, sending, respondError, reload }` for a fully custom UI.
Custom UIs must keep RingSays trust rules: badge only when `isVerifiedOrganisation`, unverified
text shown as unverified, answers limited to `intent.actions`.

## Errors

| Case | Card shows |
| --- | --- |
| network down | `network`, retry by remount or `reload` |
| 403 | `notForThisDevice` |
| 410 | `ended` |
| answer failed (5xx, 409, 429) | intent stays, inline error, customer can try again |
| storage unreadable | `error` |

## Limits

* Install id is an app storage UUID, not a hardware bound key; it binds a token to one install only.
* DECLINE from card sends reason `NOT_NOW`; richer reasons through `useIntent`.
* SCHEDULE needs organisation offered slots; enterprise API cannot offer slots yet.

## Develop

`pnpm --filter @ringsays/react-native-sdk test` (Jest, fake API) · `build` · `typecheck`.
