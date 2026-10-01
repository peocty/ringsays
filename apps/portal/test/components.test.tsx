import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { vi } from "vitest";

import { ApiError } from "../src/api/client";
import type { Me, Membership } from "../src/api/types";
import { TenantShell } from "../src/components/Shell";
import { ErrorAlert, ReasonDialog, SecretPanel } from "../src/components/ui";
import { initI18n, setLang } from "../src/i18n";

vi.mock("../src/auth/AuthProvider", () => ({
  useAuth: () => ({ signOut: vi.fn(), signIn: vi.fn(), user: {}, ready: true }),
}));

beforeAll(() => {
  initI18n("en");
  // jsdom has no <dialog> methods
  HTMLDialogElement.prototype.showModal = function showModal(this: HTMLDialogElement) {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function close(this: HTMLDialogElement) {
    this.removeAttribute("open");
  };
});
afterEach(() => setLang("en"));

function membership(roles: Membership["roles"], permissions: Membership["permissions"]): Membership {
  return {
    tenant_id: "11111111-1111-4111-8111-111111111111",
    legal_name: { en: "Mock Bank", ar: "بنك تجريبي" },
    verification_status: "VERIFIED",
    roles,
    permissions,
    agent_id: null,
  };
}

function renderShell(m: Membership) {
  const qc = new QueryClient();
  const me: Me = { email: "a@b.example", display_name: null, memberships: [m], staff_roles: [] };
  qc.setQueryData(["me"], me);
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <TenantShell membership={m}>
          <p>content</p>
        </TenantShell>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("navigation follows permissions", () => {
  it("agent sees monitor but no admin pages", () => {
    renderShell(membership(["AGENT"], ["org.read", "intents.read_own"]));
    expect(screen.getByRole("link", { name: "Intent monitor" })).toBeInTheDocument();
    for (const name of ["Integration", "People and roles", "Audit log", "Verification"]) {
      expect(screen.queryByRole("link", { name })).toBeNull();
    }
  });

  it("integration admin sees integration only", () => {
    renderShell(membership(["INTEGRATION_ADMIN"], ["org.read", "integration.read", "integration.manage"]));
    expect(screen.getByRole("link", { name: "Integration" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Intent monitor" })).toBeNull();
  });

  it("switches to Arabic and right to left", async () => {
    renderShell(membership(["TENANT_ADMIN"], ["org.read", "org.manage"]));
    await userEvent.click(screen.getByTestId("lang-toggle"));
    expect(document.documentElement.dir).toBe("rtl");
    expect(await screen.findByRole("link", { name: "نظرة عامة" })).toBeInTheDocument();
  });
});

describe("errors", () => {
  it("translates by status and shows server detail", () => {
    render(<ErrorAlert error={new ApiError({ status: 403, detail: "Your role does not allow this" })} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Your role does not allow this.");
  });

  it("network failure has its own message", () => {
    render(<ErrorAlert error={new ApiError({ status: 0 })} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Cannot reach RingSays");
  });
});

describe("dialogs", () => {
  it("reason dialog needs at least 3 characters", async () => {
    const onConfirm = vi.fn();
    render(<ReasonDialog open title="Revoke" confirmLabel="Revoke" onClose={() => undefined} onConfirm={onConfirm} />);
    const button = screen.getByRole("button", { name: "Revoke" });
    expect(button).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Reason"), "rotated");
    await userEvent.click(button);
    expect(onConfirm).toHaveBeenCalledWith("rotated");
  });

  it("secret panel shows value until acknowledged", async () => {
    const onDone = vi.fn();
    render(<SecretPanel title="Copy now" body="Shown once" items={[{ label: "Secret", value: "s3cr3t" }]} onDone={onDone} />);
    expect(screen.getByTestId("secret-Secret")).toHaveTextContent("s3cr3t");
    await userEvent.click(screen.getByRole("button", { name: "I have stored it" }));
    expect(onDone).toHaveBeenCalled();
  });
});
