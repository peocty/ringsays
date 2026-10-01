import { useTranslation } from "react-i18next";
import { Link, Navigate } from "react-router";

import { useMe } from "../api/session";
import { STATUS_TONE } from "../components/Shell";
import { ErrorAlert, Loading, Pill } from "../components/ui";
import { useLang } from "../i18n";
import { pick } from "../lib/format";
import { Centered } from "./Auth";

/** After sign in: go straight to the only organisation, or let the person choose. */
export function Home() {
  const { t } = useTranslation();
  const { lang } = useLang();
  const me = useMe();
  if (me.isPending) return <Loading />;
  if (me.error) return <Centered><ErrorAlert error={me.error} onRetry={() => void me.refetch()} /></Centered>;
  const { memberships, staff_roles } = me.data;
  if (memberships.length === 1 && staff_roles.length === 0) {
    return <Navigate to={`/t/${memberships[0]!.tenant_id}/overview`} replace />;
  }
  if (memberships.length === 0 && staff_roles.length > 0) return <Navigate to="/backoffice/queue" replace />;
  return (
    <Centered>
      {memberships.length === 0 ? (
        <p>{t("home.noMemberships", { email: me.data.email })}</p>
      ) : (
        <>
          <h1 className="h2">{t("home.chooseOrg")}</h1>
          <ul className="choice-list">
            {memberships.map((m) => (
              <li key={m.tenant_id}>
                <Link to={`/t/${m.tenant_id}/overview`} className="choice">
                  <span>{pick(m.legal_name, lang)}</span>
                  <Pill tone={STATUS_TONE[m.verification_status] ?? "neutral"}>
                    {t(`tenantStatus.${m.verification_status}`)}
                  </Pill>
                </Link>
              </li>
            ))}
          </ul>
        </>
      )}
      {staff_roles.length > 0 ? (
        <Link to="/backoffice/queue" className="choice">
          {t("home.backoffice")}
        </Link>
      ) : null}
    </Centered>
  );
}
