import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { NavLink, useNavigate } from "react-router";

import { useMe } from "../api/session";
import type { Membership, Permission } from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { useLang } from "../i18n";
import { pick } from "../lib/format";
import { Pill, type Tone } from "./ui";

interface NavItem {
  to: string;
  label: string;
  show: boolean;
}

export const STATUS_TONE: Record<string, Tone> = { PENDING: "warn", VERIFIED: "good", SUSPENDED: "bad" };

function Brand() {
  const { t } = useTranslation();
  return (
    <div className="brand">
      <svg viewBox="0 0 32 32" width="28" height="28" aria-hidden>
        <rect width="32" height="32" rx="8" fill="currentColor" />
        <path d="M10 9h7.5a5 5 0 0 1 1.2 9.85L23 23h-3.6l-3.9-4H13v4h-3zm3 3v4h4.4a2 2 0 0 0 0-4z" fill="#fff" />
      </svg>
      <span>{t("common.appName")}</span>
    </div>
  );
}

function TopBar({ children }: { children?: ReactNode }) {
  const { t } = useTranslation();
  const { toggle } = useLang();
  const { signOut } = useAuth();
  const me = useMe();
  return (
    <div className="topbar-actions">
      {children}
      <button type="button" className="btn btn-ghost" onClick={toggle} data-testid="lang-toggle">
        {t("common.switchLanguage")}
      </button>
      <span className="muted user-email" dir="ltr">
        {me.data?.email}
      </span>
      <button type="button" className="btn btn-ghost" onClick={() => void signOut()}>
        {t("common.signOut")}
      </button>
    </div>
  );
}

function Frame({ nav, header, children }: { nav: NavItem[]; header: ReactNode; children: ReactNode }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <div className="shell">
      <a className="skip" href="#main">
        {t("common.skipToContent")}
      </a>
      <aside className={`sidebar${open ? " open" : ""}`}>
        <Brand />
        <nav aria-label={t("common.menu")}>
          <ul>
            {nav
              .filter((n) => n.show)
              .map((n) => (
                <li key={n.to}>
                  <NavLink to={n.to} onClick={() => setOpen(false)}>
                    {n.label}
                  </NavLink>
                </li>
              ))}
          </ul>
        </nav>
      </aside>
      <div className="main-col">
        <header className="topbar">
          <button
            type="button"
            className="btn btn-ghost menu-btn"
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
          >
            {t("common.menu")}
          </button>
          {header}
        </header>
        <main id="main" tabIndex={-1}>
          {children}
        </main>
      </div>
    </div>
  );
}

export function TenantShell({ membership, children }: { membership: Membership; children: ReactNode }) {
  const { t } = useTranslation();
  const { lang } = useLang();
  const me = useMe();
  const navigate = useNavigate();
  const base = `/t/${membership.tenant_id}`;
  const can = (p: Permission) => membership.permissions.includes(p);
  const nav: NavItem[] = [
    { to: `${base}/overview`, label: t("nav.overview"), show: true },
    { to: `${base}/monitor`, label: t("nav.monitor"), show: can("intents.read_all") || can("intents.read_own") },
    { to: `${base}/organisation`, label: t("nav.organisation"), show: can("org.read") },
    { to: `${base}/verification`, label: t("nav.verification"), show: can("org.manage") },
    { to: `${base}/catalogue`, label: t("nav.catalogue"), show: can("org.read") },
    { to: `${base}/integration`, label: t("nav.integration"), show: can("integration.read") },
    { to: `${base}/people`, label: t("nav.people"), show: can("users.manage") },
    { to: `${base}/audit`, label: t("nav.audit"), show: can("audit.read") },
  ];
  const others = (me.data?.memberships.length ?? 0) > 1 || (me.data?.staff_roles.length ?? 0) > 0;
  return (
    <Frame
      nav={nav}
      header={
        <>
          <div className="org">
            <strong>{pick(membership.legal_name, lang)}</strong>
            <Pill tone={STATUS_TONE[membership.verification_status] ?? "neutral"}>
              {t(`tenantStatus.${membership.verification_status}`)}
            </Pill>
            <span className="muted roles">{membership.roles.map((r) => t(`roles.${r}`)).join(" · ")}</span>
          </div>
          <TopBar>
            {others ? (
              <button type="button" className="btn btn-ghost" onClick={() => navigate("/")}>
                {t("common.switchOrg")}
              </button>
            ) : null}
          </TopBar>
        </>
      }
    >
      {children}
    </Frame>
  );
}

export function StaffShell({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const me = useMe();
  const navigate = useNavigate();
  const roles = me.data?.staff_roles ?? [];
  const nav: NavItem[] = [
    { to: "/backoffice/queue", label: t("nav.reviewQueue"), show: true },
    { to: "/backoffice/tenants", label: t("nav.tenants"), show: true },
  ];
  return (
    <Frame
      nav={nav}
      header={
        <>
          <div className="org">
            <strong>{t("home.backoffice")}</strong>
            <span className="muted roles">{roles.map((r) => t(`roles.${r}`)).join(" · ")}</span>
          </div>
          <TopBar>
            {(me.data?.memberships.length ?? 0) > 0 ? (
              <button type="button" className="btn btn-ghost" onClick={() => navigate("/")}>
                {t("common.switchOrg")}
              </button>
            ) : null}
          </TopBar>
        </>
      }
    >
      {children}
    </Frame>
  );
}
