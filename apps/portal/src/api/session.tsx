import { useQuery } from "@tanstack/react-query";
import { createContext, useContext, type ReactNode } from "react";

import { api, unwrap } from "./client";
import type { Me, Membership, Permission } from "./types";

export function useMe() {
  return useQuery({
    queryKey: ["me"],
    queryFn: () => unwrap<Me>(api.GET("/me")),
    staleTime: 60_000,
  });
}

/** Current tenant membership, available to every page under /t/:tenantId. */
const MembershipContext = createContext<Membership | null>(null);

export function MembershipProvider({ value, children }: { value: Membership; children: ReactNode }) {
  return <MembershipContext.Provider value={value}>{children}</MembershipContext.Provider>;
}

export function useMembership(): Membership {
  const m = useContext(MembershipContext);
  if (!m) throw new Error("useMembership outside tenant route");
  return m;
}

export function useCan(): (p: Permission) => boolean {
  const m = useMembership();
  return (p) => m.permissions.includes(p);
}

/**
 * For changes: same as useCan, but false while the organisation is suspended (the API refuses them).
 * Containment actions (revoke, disable, stop using) use useCan, because they stay allowed.
 */
export function useCanChange(): (p: Permission) => boolean {
  const m = useMembership();
  return (p) => m.permissions.includes(p) && m.verification_status !== "SUSPENDED";
}

/** Query keys per tenant, so switching organisation never shows another tenant's cached data. */
export const keys = {
  tenant: (t: string) => ["tenant", t] as const,
  departments: (t: string) => ["tenant", t, "departments"] as const,
  agents: (t: string) => ["tenant", t, "agents"] as const,
  numbers: (t: string) => ["tenant", t, "numbers"] as const,
  verification: (t: string) => ["tenant", t, "verification"] as const,
  users: (t: string) => ["tenant", t, "users"] as const,
  codes: (t: string) => ["tenant", t, "codes"] as const,
  clients: (t: string) => ["tenant", t, "clients"] as const,
  endpoints: (t: string) => ["tenant", t, "endpoints"] as const,
  deliveries: (t: string, e: string, s: string) => ["tenant", t, "deliveries", e, s] as const,
  intents: (t: string, f: unknown) => ["tenant", t, "intents", f] as const,
  intent: (t: string, i: string) => ["tenant", t, "intent", i] as const,
  summary: (t: string) => ["tenant", t, "summary"] as const,
  audit: (t: string, f: unknown) => ["tenant", t, "audit", f] as const,
  queue: ["backoffice", "queue"] as const,
  review: (id: string) => ["backoffice", "review", id] as const,
  tenants: (s: string) => ["backoffice", "tenants", s] as const,
};
