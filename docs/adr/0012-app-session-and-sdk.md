# 0012 App session, device key, delivery to the app and enterprise SDK

Status: Accepted, 2026-10-01

## Decision
- **One typed client** (`packages/client`) for the RingSays app and the enterprise SDK, generated from
  `contracts/openapi/client.yaml`; CI fails when generated types drift.
- **Device key.** Each sign in makes a P-256 key on the phone (`@noble/curves`, random bytes from the
  platform). The public key (SPKI, base64) is registered at sign in; every refresh is signed with the
  private key (ECDSA SHA-256, DER), so a copied refresh token alone is useless. Key and session live in
  iOS Keychain / Android Keystore through expo-secure-store, readable only while unlocked, never backed
  up. A new key after every sign out, so two accounts on one phone are not linkable. Software key now;
  Secure Enclave / StrongBox key (non exportable) is the planned hardening.
- **Session.** Access token refreshed 60 s before expiry, single flight (concurrent calls share one
  refresh; a second use of a rotated token revokes the family server side). One retry after 401. A
  refused refresh ends the session and tells the app. A sign out increments a session epoch, so a
  refresh already in flight cannot write the session back.
- **Sign out** calls `POST /auth/logout`: device revoked, its refresh tokens revoked, push tokens
  cleared, revocation published to every API process. The phone side always completes, even offline.
- **Delivery to the app.** Push carries the intent id and kind only; the app fetches details over the
  authenticated API in the chosen language. Without a push token the intent falls back to PSTN. The app
  keeps its push token current (start, foreground, token rotation).
- **Layout direction** comes from the app language (root Yoga `direction`), not the phone; native RTL is
  off so Arabic phones with the app in English stay consistent.
- **Enterprise SDK** (`@ringsays/react-native-sdk`) works without a RingSays account: the organisation's
  backend receives a Context Token for `SDK` delivery and passes it to its own app. Install id (UUID in
  app storage) binds the token to the first install that opens it (others get 403). Trust cues are
  fixed: the verified badge appears only for organisations RingSays verified; brand colours only.

## Consequences
Release builds refuse a non https API base. Android push needs the Firebase project file
(`GOOGLE_SERVICES_JSON` at build). Native device testing and real APNs/FCM credentials are required
before launch; browser tests run the web build against MOCK push.
