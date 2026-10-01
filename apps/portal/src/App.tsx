import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { BrowserRouter, Navigate, Outlet, Route, Routes, useLocation, useParams } from "react-router";

import { ApiError, configureApi } from "./api/client";
import { MembershipProvider, useMe } from "./api/session";
import { AuthProvider, useAuth } from "./auth/AuthProvider";
import { StaffShell, TenantShell } from "./components/Shell";
import { ErrorAlert, Loading, ToastProvider } from "./components/ui";
import { currentLang } from "./i18n";
import { Audit } from "./pages/Audit";
import { Callback, Centered, SignedOut, SignIn } from "./pages/Auth";
import { ReviewQueuePage, TenantsPage, VerificationReviewPage } from "./pages/Backoffice";
import { Catalogue } from "./pages/Catalogue";
import { Home } from "./pages/Home";
import { Integration } from "./pages/Integration";
import { Monitor } from "./pages/Monitor";
import { Organisation } from "./pages/Organisation";
import { Overview } from "./pages/Overview";
import { People } from "./pages/People";
import { Verification } from "./pages/Verification";

export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // Never retry authorisation or validation failures; retry network and server errors twice.
        retry: (count, err) => (err instanceof ApiError && err.status > 0 && err.status < 500 ? false : count < 2),
        refetchOnWindowFocus: false,
      },
    },
  });
}

/** Wires API client to the signed in session. Signed out people see the sign in page. */
function RequireAuth() {
  const auth = useAuth();
  const location = useLocation();
  const [expired, setExpired] = useState(false);
  // Configured during render (idempotent) so it is in place before any child query starts.
  configureApi({ token: auth.accessToken, onUnauthenticated: () => setExpired(true), language: currentLang });
  const { signIn } = auth;
  useEffect(() => {
    if (expired) void signIn(location.pathname);
  }, [expired, signIn, location.pathname]);
  if (!auth.ready) return <Loading />;
  if (!auth.user || expired) return <SignIn />;
  return <Outlet />;
}

function NotFound() {
  const { t } = useTranslation();
  return (
    <Centered>
      <p>{t("errors.notFoundPage")}</p>
    </Centered>
  );
}

function TenantRoute() {
  const { t } = useTranslation();
  const { tenantId } = useParams();
  const me = useMe();
  if (me.isPending) return <Loading />;
  if (me.error) return <Centered><ErrorAlert error={me.error} onRetry={() => void me.refetch()} /></Centered>;
  const membership = me.data.memberships.find((m) => m.tenant_id === tenantId);
  if (!membership) {
    return (
      <Centered>
        <p>{t("errors.noAccess")}</p>
      </Centered>
    );
  }
  return (
    <MembershipProvider value={membership}>
      <TenantShell membership={membership}>
        <Outlet />
      </TenantShell>
    </MembershipProvider>
  );
}

function RequirePermission({ allowed, children }: { allowed: (p: string[]) => boolean; children: ReactNode }) {
  const { tenantId } = useParams();
  const me = useMe();
  const m = me.data?.memberships.find((x) => x.tenant_id === tenantId);
  if (!m || !allowed(m.permissions)) return <Navigate to={`/t/${tenantId}/overview`} replace />;
  return <>{children}</>;
}

function StaffRoute() {
  const { t } = useTranslation();
  const me = useMe();
  if (me.isPending) return <Loading />;
  if (me.error) return <Centered><ErrorAlert error={me.error} /></Centered>;
  if (me.data.staff_roles.length === 0) {
    return (
      <Centered>
        <p>{t("errors.403")}</p>
      </Centered>
    );
  }
  return (
    <StaffShell>
      <Outlet />
    </StaffShell>
  );
}

const has = (p: string) => (ps: string[]) => ps.includes(p);

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/auth/callback" element={<Callback />} />
      <Route path="/signed-out" element={<SignedOut />} />
      <Route element={<RequireAuth />}>
        <Route index element={<Home />} />
        <Route path="t/:tenantId" element={<TenantRoute />}>
          <Route index element={<Navigate to="overview" replace />} />
          <Route path="overview" element={<Overview />} />
          <Route path="organisation" element={<Organisation />} />
          <Route path="verification" element={<RequirePermission allowed={has("org.manage")}><Verification /></RequirePermission>} />
          <Route path="catalogue" element={<Catalogue />} />
          <Route path="integration" element={<RequirePermission allowed={has("integration.read")}><Integration /></RequirePermission>} />
          <Route path="people" element={<RequirePermission allowed={has("users.manage")}><People /></RequirePermission>} />
          <Route
            path="monitor"
            element={<RequirePermission allowed={(ps) => ps.includes("intents.read_all") || ps.includes("intents.read_own")}><Monitor /></RequirePermission>}
          />
          <Route path="audit" element={<RequirePermission allowed={has("audit.read")}><Audit /></RequirePermission>} />
        </Route>
        <Route path="backoffice" element={<StaffRoute />}>
          <Route index element={<Navigate to="queue" replace />} />
          <Route path="queue" element={<ReviewQueuePage />} />
          <Route path="verifications/:requestId" element={<VerificationReviewPage />} />
          <Route path="tenants" element={<TenantsPage />} />
        </Route>
      </Route>
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}

export function App() {
  const [client] = useState(makeQueryClient);
  return (
    <AuthProvider>
      <QueryClientProvider client={client}>
        <ToastProvider>
          <BrowserRouter>
            <AppRoutes />
          </BrowserRouter>
        </ToastProvider>
      </QueryClientProvider>
    </AuthProvider>
  );
}
