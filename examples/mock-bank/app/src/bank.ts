import { config } from "./config";

/** The bank's own API (its sign in, its messages). RingSays is not involved in these calls. */
export interface CustomerSummary {
  id: string;
  name: { en: string; ar: string };
  phoneHint: string;
}

export interface Message {
  intentId: string;
  createdAt: string;
  status: string;
  title: { en: string; ar: string } | null;
  scheduledSlot: { start: string; end: string } | null;
  /** Present only while the customer can answer; passed straight to the RingSays SDK. */
  contextToken: string | null;
}

export class BankError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

async function call<T>(path: string, init: RequestInit = {}, token?: string): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${config.bankApi}${path}`, {
      ...init,
      headers: {
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
    });
  } catch {
    throw new BankError(0, "network");
  }
  if (res.status === 204) return undefined as T;
  const data = (await res.json().catch(() => ({}))) as { error?: string };
  if (!res.ok) throw new BankError(res.status, data.error ?? `HTTP ${res.status}`);
  return data as T;
}

export const bank = {
  customers: () => call<CustomerSummary[]>("/app/customers"),
  login: (customerId: string, pin: string) =>
    call<{ token: string; customer: { id: string; name: { en: string; ar: string }; language: "en" | "ar" } }>("/app/login", {
      method: "POST",
      body: JSON.stringify({ customerId, pin }),
    }),
  logout: (token: string) => call<void>("/app/logout", { method: "POST" }, token),
  messages: (token: string) => call<Message[]>("/app/messages", {}, token),
};
