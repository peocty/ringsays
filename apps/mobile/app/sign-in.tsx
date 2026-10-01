import { RingSaysError } from "@ringsays/client";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Platform, Pressable, StyleSheet, Text, TextInput, View } from "react-native";

import { Banner, Body, Button, Card, Screen, Title } from "../src/components/ui";
import { config, type Country } from "../src/config";
import { useLang } from "../src/i18n";
import { useAuth } from "../src/lib/auth";
import { errorMessage } from "../src/lib/errors";
import { ltr } from "../src/lib/format";
import { toE164 } from "../src/lib/phone";
import { pushTokens } from "../src/lib/push";
import { getSession } from "../src/lib/session";
import { radius, space, useTheme } from "../src/theme";

const RESEND_S = 60;

export default function SignIn() {
  const { t } = useTranslation();
  const { lang, toggle } = useLang();
  const th = useTheme();
  const auth = useAuth();
  const [country, setCountry] = useState<Country>(config.countries[0]);
  const [typed, setTyped] = useState("");
  const [phone, setPhone] = useState<string | null>(null);
  const [challenge, setChallenge] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [wait, setWait] = useState(0);
  const codeRef = useRef<TextInput>(null);

  useEffect(() => {
    if (wait <= 0) return;
    const id = setTimeout(() => setWait((w) => w - 1), 1000);
    return () => clearTimeout(id);
  }, [wait]);

  const send = async (target: string) => {
    setBusy(true);
    setError(null);
    try {
      const s = await getSession();
      const r = await s.requestCode(target, lang);
      setPhone(target);
      setChallenge(r.challengeId);
      setCode("");
      setWait(RESEND_S);
      setTimeout(() => codeRef.current?.focus(), 50);
    } catch (e) {
      setError(errorMessage(e, t));
      if (e instanceof RingSaysError && e.retryAfterS) setWait(e.retryAfterS);
    } finally {
      setBusy(false);
    }
  };

  const verify = async () => {
    if (!challenge || code.length !== 6) return;
    setBusy(true);
    setError(null);
    try {
      const s = await getSession();
      const push = await pushTokens().catch(() => undefined);
      await s.verifyCode(challenge, code, {
        platform: Platform.OS === "ios" ? "IOS" : "ANDROID",
        appVersion: config.appVersion,
        ...(push ? { push } : {}),
      });
      auth.markSignedIn();
    } catch (e) {
      setError(errorMessage(e, t));
      setCode("");
    } finally {
      setBusy(false);
    }
  };

  const e164 = toE164(country, typed);
  return (
    <Screen>
      <View style={[styles.top]}>
        <Text style={[styles.brand, { color: th.primary }]}>{t("app.name")}</Text>
        <Pressable accessibilityRole="button" onPress={toggle} testID="lang-toggle" hitSlop={12}>
          <Text style={{ color: th.primary, fontWeight: "600", fontSize: 16 }}>{t("app.switchLanguage")}</Text>
        </Pressable>
      </View>
      <Body muted>{t("app.tagline")}</Body>
      {auth.endedUnexpectedly ? <Banner tone="warn">{t("errors.signedOut")}</Banner> : null}
      <Card>
        {!challenge ? (
          <>
            <Title>{t("signIn.title")}</Title>
            <Text style={[styles.label, { color: th.text }]}>{t("signIn.country")}</Text>
            <View style={styles.countries}>
              {config.countries.map((c) => (
                <Pressable
                  key={c.iso}
                  accessibilityRole="radio"
                  accessibilityState={{ checked: c.iso === country.iso }}
                  onPress={() => setCountry(c)}
                  style={[
                    styles.country,
                    { borderColor: c.iso === country.iso ? th.primary : th.border, backgroundColor: th.surface },
                  ]}
                >
                  <Text style={{ color: th.text, fontWeight: "600" }}>
                    {c.iso} {ltr(c.dial)}
                  </Text>
                </Pressable>
              ))}
            </View>
            <Text style={[styles.label, { color: th.text }]} nativeID="phone-label">
              {t("signIn.phone")}
            </Text>
            <View style={[styles.phoneRow, { borderColor: th.border, direction: "ltr" }]}>
              <Text style={{ color: th.muted, fontSize: 18 }}>{country.dial}</Text>
              <TextInput
                accessibilityLabel={t("signIn.phone")}
                accessibilityLabelledBy="phone-label"
                testID="phone-input"
                value={typed}
                onChangeText={setTyped}
                keyboardType="phone-pad"
                textContentType="telephoneNumber"
                autoComplete="tel"
                placeholder={country.example}
                placeholderTextColor={th.muted}
                style={[styles.input, { color: th.text }]}
                onSubmitEditing={() => e164 && void send(e164)}
              />
            </View>
            <Body muted>{t("signIn.phoneHint", { example: ltr(`${country.dial} ${country.example}`) })}</Body>
            {typed.length > 3 && !e164 ? <Body style={{ color: th.danger }}>{t("signIn.invalidPhone")}</Body> : null}
            <Button
              title={t("signIn.sendCode")}
              variant="primary"
              busy={busy}
              disabled={!e164 || wait > 0}
              onPress={() => e164 && void send(e164)}
              testID="send-code"
            />
          </>
        ) : (
          <>
            <Title>{t("signIn.codeTitle")}</Title>
            <Body muted>{t("signIn.codeSent", { phone: ltr(phone ?? "") })}</Body>
            <TextInput
              ref={codeRef}
              accessibilityLabel={t("signIn.code")}
              testID="code-input"
              value={code}
              onChangeText={(v) => setCode(v.replace(/[٠-٩]/g, (d) => String(d.charCodeAt(0) - 0x0660)).replace(/\D/g, "").slice(0, 6))}
              keyboardType="number-pad"
              textContentType="oneTimeCode"
              autoComplete="sms-otp"
              maxLength={6}
              style={[styles.code, { color: th.text, borderColor: th.border, direction: "ltr" }]}
              onSubmitEditing={() => void verify()}
            />
            <Button title={t("signIn.verify")} variant="primary" busy={busy} disabled={code.length !== 6} onPress={() => void verify()} testID="verify" />
            <View style={styles.links}>
              <Button
                title={wait > 0 ? t("signIn.resendIn", { seconds: wait }) : t("signIn.resend")}
                variant="ghost"
                disabled={wait > 0 || busy}
                onPress={() => phone && void send(phone)}
              />
              <Button
                title={t("signIn.changeNumber")}
                variant="ghost"
                onPress={() => {
                  setChallenge(null);
                  setError(null);
                }}
              />
            </View>
          </>
        )}
        {error ? (
          <Banner tone="error" testID="sign-in-error">
            {error}
          </Banner>
        ) : null}
      </Card>
      <Body muted>{t("signIn.privacy")}</Body>
    </Screen>
  );
}

const styles = StyleSheet.create({
  top: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginTop: space.xl },
  brand: { fontSize: 28, fontWeight: "800" },
  label: { fontWeight: "600", fontSize: 15 },
  countries: { flexDirection: "row", gap: space.sm, flexWrap: "wrap" },
  country: { borderWidth: 2, borderRadius: radius.md, paddingHorizontal: space.md, paddingVertical: space.sm },
  phoneRow: { flexDirection: "row", alignItems: "center", gap: space.sm, borderWidth: 1, borderRadius: radius.md, paddingHorizontal: space.md },
  input: { flex: 1, fontSize: 18, paddingVertical: 12, textAlign: "left" },
  code: { fontSize: 28, letterSpacing: 10, borderWidth: 1, borderRadius: radius.md, padding: space.md, textAlign: "center" },
  links: { flexDirection: "row", justifyContent: "space-between", flexWrap: "wrap" },
});
