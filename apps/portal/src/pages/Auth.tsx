import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";

import { useAuth } from "../auth/AuthProvider";
import { Alert, Button } from "../components/ui";
import { config } from "../config";
import { useLang } from "../i18n";

function Centered({ children }: { children: React.ReactNode }) {
  const { t } = useTranslation();
  const { toggle } = useLang();
  return (
    <div className="centered">
      <div className="centered-card">
        <div className="row between">
          <div className="brand brand-lg">
            <span>{t("common.appName")}</span>
            <span className="muted">{t("common.portal")}</span>
          </div>
          <button type="button" className="btn btn-ghost" onClick={toggle} data-testid="lang-toggle">
            {t("common.switchLanguage")}
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function SignIn() {
  const { t } = useTranslation();
  const { signIn } = useAuth();
  const [busy, setBusy] = useState(false);
  return (
    <Centered>
      <p>{t("auth.welcome")}</p>
      <Button
        variant="primary"
        busy={busy}
        onClick={() => {
          setBusy(true);
          void signIn().catch(() => setBusy(false));
        }}
      >
        {t("auth.signIn")}
      </Button>
      {config.oidcAuthority.includes("/dev/oidc") ? <p className="muted small">{t("auth.mockNotice")}</p> : null}
    </Centered>
  );
}

export function Callback() {
  const { t } = useTranslation();
  const { completeSignIn, signIn } = useAuth();
  const navigate = useNavigate();
  const [failed, setFailed] = useState(false);
  const started = useRef(false);
  useEffect(() => {
    if (started.current) return; // React strict mode runs effects twice; a code is single use
    started.current = true;
    completeSignIn()
      .then((to) => navigate(to, { replace: true }))
      .catch(() => setFailed(true));
  }, [completeSignIn, navigate]);
  return (
    <Centered>
      {failed ? (
        <>
          <Alert tone="error">{t("auth.failed")}</Alert>
          <Button variant="primary" onClick={() => void signIn("/")}>
            {t("auth.signInAgain")}
          </Button>
        </>
      ) : (
        <p role="status">{t("auth.signingIn")}</p>
      )}
    </Centered>
  );
}

export function SignedOut() {
  const { t } = useTranslation();
  const { signIn } = useAuth();
  return (
    <Centered>
      <p>{t("auth.signedOut")}</p>
      <Button variant="primary" onClick={() => void signIn("/")}>
        {t("auth.signInAgain")}
      </Button>
    </Centered>
  );
}

export { Centered };
