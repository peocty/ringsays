import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { getSession, onSignedOut } from "./session";

type Status = "loading" | "signedOut" | "signedIn";

interface AuthState {
  status: Status;
  /** Set when the session ended without the person asking (shown once on the sign in screen). */
  endedUnexpectedly: boolean;
  markSignedIn: () => void;
  markSignedOut: (unexpected?: boolean) => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [status, setStatus] = useState<Status>("loading");
  const [endedUnexpectedly, setEnded] = useState(false);
  const markSignedOut = useCallback(
    (unexpected = false) => {
      qc.clear(); // nothing from the previous person stays in memory
      setEnded(unexpected);
      setStatus("signedOut");
    },
    [qc],
  );
  useEffect(() => {
    let live = true;
    void getSession()
      .then((s) => s.isSignedIn())
      .then((yes) => live && setStatus(yes ? "signedIn" : "signedOut"))
      .catch(() => live && setStatus("signedOut"));
    const off = onSignedOut(() => markSignedOut(true));
    return () => {
      live = false;
      off();
    };
  }, [markSignedOut]);
  const value = useMemo(
    () => ({
      status,
      endedUnexpectedly,
      markSignedIn: () => {
        setEnded(false);
        setStatus("signedIn");
      },
      markSignedOut,
    }),
    [status, endedUnexpectedly, markSignedOut],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const v = useContext(AuthContext);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}
