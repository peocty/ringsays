import { User, UserManager, WebStorageStateStore } from "oidc-client-ts";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { config } from "../config";

/**
 * OpenID Connect sign in with authorization code and PKCE. Tokens are kept in sessionStorage
 * (cleared when the tab closes) and sent only as a bearer header, never as a cookie.
 * Access tokens are short lived; they are renewed in the background with the refresh token
 * (offline_access), and on demand just before a request if the background renewal missed.
 */
export function createUserManager(): UserManager {
  return new UserManager({
    authority: config.oidcAuthority,
    client_id: config.oidcClientId,
    redirect_uri: `${window.location.origin}/auth/callback`,
    post_logout_redirect_uri: `${window.location.origin}/signed-out`,
    response_type: "code",
    scope: config.oidcScope,
    userStore: new WebStorageStateStore({ store: window.sessionStorage }),
    automaticSilentRenew: true,
    accessTokenExpiringNotificationTimeInSeconds: 60,
    loadUserInfo: false,
  });
}

interface AuthState {
  user: User | null;
  ready: boolean;
  signIn: (returnTo?: string) => Promise<void>;
  signOut: () => Promise<void>;
  completeSignIn: () => Promise<string>;
  accessToken: () => Promise<string | null>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children, manager }: { children: ReactNode; manager?: UserManager }) {
  const um = useMemo(() => manager ?? createUserManager(), [manager]);
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let live = true;
    um.getUser()
      .then((u) => {
        if (live) setUser(u && !u.expired ? u : null);
      })
      .catch(() => undefined)
      .finally(() => live && setReady(true));
    const onLoaded = (u: User) => setUser(u);
    const onUnloaded = () => setUser(null);
    um.events.addUserLoaded(onLoaded);
    um.events.addUserUnloaded(onUnloaded);
    return () => {
      live = false;
      um.events.removeUserLoaded(onLoaded);
      um.events.removeUserUnloaded(onUnloaded);
    };
  }, [um]);

  const signIn = useCallback(
    (returnTo?: string) =>
      um.signinRedirect({ state: { returnTo: returnTo ?? window.location.pathname + window.location.search } }),
    [um],
  );
  const signOut = useCallback(async () => {
    // signoutRedirect sends id_token_hint from the stored user, then removes it.
    await um.signoutRedirect().catch(async () => {
      await um.removeUser();
      window.location.assign("/signed-out");
    });
  }, [um]);
  const completeSignIn = useCallback(async () => {
    const u = await um.signinRedirectCallback();
    setUser(u);
    const state = u.state as { returnTo?: string } | undefined;
    const target = state?.returnTo ?? "/";
    // Only same origin paths, never a full URL from state.
    return target.startsWith("/") && !target.startsWith("//") ? target : "/";
  }, [um]);
  const accessToken = useCallback(async () => {
    let u = await um.getUser();
    if (u && (u.expires_in ?? 0) < 30 && u.refresh_token) {
      u = await um.signinSilent().catch(() => u);
    }
    return u && !u.expired ? u.access_token : null;
  }, [um]);

  const value = useMemo(
    () => ({ user, ready, signIn, signOut, completeSignIn, accessToken }),
    [user, ready, signIn, signOut, completeSignIn, accessToken],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}
