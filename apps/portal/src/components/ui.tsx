import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type FormEvent,
  type ReactNode,
} from "react";
import { useTranslation } from "react-i18next";

import { ApiError } from "../api/client";

// Buttons

type Variant = "primary" | "secondary" | "danger" | "ghost";

export function Button({
  variant = "secondary",
  busy = false,
  children,
  className,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; busy?: boolean }) {
  return (
    <button
      type="button"
      {...rest}
      className={`btn btn-${variant}${className ? ` ${className}` : ""}`}
      disabled={rest.disabled || busy}
      aria-busy={busy || undefined}
    >
      {busy ? <span className="spinner" aria-hidden /> : null}
      {children}
    </button>
  );
}

// Form fields

export function Field({
  label,
  hint,
  error,
  children,
  optional,
}: {
  label: string;
  hint?: string;
  error?: string;
  optional?: boolean;
  children: (props: { id: string; "aria-describedby"?: string; "aria-invalid"?: boolean }) => ReactNode;
}) {
  const { t } = useTranslation();
  const id = useId();
  const hintId = hint ? `${id}-hint` : undefined;
  const errId = error ? `${id}-err` : undefined;
  const describedBy = [hintId, errId].filter(Boolean).join(" ") || undefined;
  return (
    <div className={`field${error ? " field-error" : ""}`}>
      <label htmlFor={id}>
        {label}
        {optional ? <span className="muted"> ({t("common.optional")})</span> : null}
      </label>
      {children({ id, "aria-describedby": describedBy, "aria-invalid": error ? true : undefined })}
      {hint ? (
        <p id={hintId} className="hint">
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={errId} className="error-text" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

/** Error from a failed request, translated by status, with server detail for support. */
export function ErrorAlert({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const { t } = useTranslation();
  if (!error) return null;
  const status = error instanceof ApiError ? error.status : 0;
  const key = status === 0 ? "errors.network" : `errors.${status}`;
  const message = t(key, { defaultValue: t("errors.title") });
  const detail = error instanceof ApiError ? error.problem.detail : undefined;
  return (
    <div className="alert alert-error" role="alert">
      <div>
        <strong>{message}</strong>
        {detail && detail !== message ? (
          <p className="alert-detail">
            {t("errors.serverSays")}: <span dir="auto">{detail}</span>
          </p>
        ) : null}
      </div>
      {onRetry ? (
        <Button variant="ghost" onClick={onRetry}>
          {t("common.retry")}
        </Button>
      ) : null}
    </div>
  );
}

/** Field level errors from a 400 problem, keyed by field path. */
export function fieldErrors(error: unknown): Record<string, string> {
  if (!(error instanceof ApiError) || !error.problem.errors) return {};
  return Object.fromEntries(error.problem.errors.map((e) => [e.field, e.message]));
}

export function Alert({ tone = "info", children }: { tone?: "info" | "warn" | "success" | "error"; children: ReactNode }) {
  return (
    <div className={`alert alert-${tone}`} role={tone === "error" ? "alert" : "status"}>
      <div>{children}</div>
    </div>
  );
}

// Layout pieces

export function PageHeader({ title, actions, intro }: { title: string; actions?: ReactNode; intro?: string }) {
  return (
    <header className="page-header">
      <div>
        <h1>{title}</h1>
        {intro ? <p className="muted intro">{intro}</p> : null}
      </div>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </header>
  );
}

export function Card({ title, actions, children }: { title?: string; actions?: ReactNode; children: ReactNode }) {
  return (
    <section className="card">
      {title || actions ? (
        <div className="card-head">
          {title ? <h2>{title}</h2> : <span />}
          {actions}
        </div>
      ) : null}
      {children}
    </section>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}

export function Loading() {
  const { t } = useTranslation();
  return (
    <p className="loading" role="status">
      <span className="spinner" aria-hidden /> {t("common.loading")}
    </p>
  );
}

export type Tone = "neutral" | "good" | "warn" | "bad" | "info";

export function Pill({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`pill pill-${tone}`}>{children}</span>;
}

/** Accessible tabs. Render the active panel inside <TabPanel id={tabPanelId(prefix)} />. */
export function tabPanelId(prefix: string): string {
  return `${prefix}-panel`;
}

export function TabPanel({ prefix, value, children }: { prefix: string; value: string; children: ReactNode }) {
  return (
    <div role="tabpanel" id={tabPanelId(prefix)} aria-labelledby={`${prefix}-tab-${value}`} tabIndex={0}>
      {children}
    </div>
  );
}

export function Tabs<T extends string>({
  tabs,
  value,
  onChange,
  label,
  prefix = "tabs",
}: {
  tabs: { id: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
  label: string;
  prefix?: string;
}) {
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});
  const onKey = (e: React.KeyboardEvent, i: number) => {
    const rtl = document.documentElement.dir === "rtl";
    const nextKey = rtl ? "ArrowLeft" : "ArrowRight";
    const prevKey = rtl ? "ArrowRight" : "ArrowLeft";
    let j = i;
    if (e.key === nextKey) j = (i + 1) % tabs.length;
    else if (e.key === prevKey) j = (i - 1 + tabs.length) % tabs.length;
    else if (e.key === "Home") j = 0;
    else if (e.key === "End") j = tabs.length - 1;
    else return;
    e.preventDefault();
    const next = tabs[j];
    if (next) {
      onChange(next.id);
      refs.current[next.id]?.focus();
    }
  };
  return (
    <div className="tabs" role="tablist" aria-label={label}>
      {tabs.map((tab, i) => (
        <button
          key={tab.id}
          ref={(el) => {
            refs.current[tab.id] = el;
          }}
          role="tab"
          type="button"
          id={`${prefix}-tab-${tab.id}`}
          aria-controls={tabPanelId(prefix)}
          aria-selected={tab.id === value}
          tabIndex={tab.id === value ? 0 : -1}
          className="tab"
          onClick={() => onChange(tab.id)}
          onKeyDown={(e) => onKey(e, i)}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}

// Dialogs

export function Dialog({
  open,
  title,
  onClose,
  children,
  footer,
  wide,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
}) {
  const { t } = useTranslation();
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const d = ref.current;
    if (!d || !open) return;
    // Remember what had focus, open, focus the first field (not the close button), and on close or
    // unmount put focus back where it was.
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (!d.open) {
      if (typeof d.showModal === "function") d.showModal();
      else d.setAttribute("open", "");
    }
    const first = d.querySelector<HTMLElement>(
      ".dialog-body input:not([type=hidden]):not([disabled]), .dialog-body select, .dialog-body textarea, .dialog-body button",
    );
    first?.focus();
    return () => {
      if (d.open) {
        if (typeof d.close === "function") d.close();
        else d.removeAttribute("open");
      }
      if (opener && document.contains(opener)) opener.focus();
    };
  }, [open]);
  return (
    <dialog
      ref={ref}
      className={`dialog${wide ? " dialog-wide" : ""}`}
      aria-labelledby={titleId}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
    >
      {open ? (
        <>
          <div className="dialog-head">
            <h2 id={titleId}>{title}</h2>
            <button type="button" className="icon-btn" onClick={onClose} aria-label={t("common.close")}>
              ×
            </button>
          </div>
          <div className="dialog-body">{children}</div>
          {footer ? <div className="dialog-foot">{footer}</div> : null}
        </>
      ) : null}
    </dialog>
  );
}

/** Dialog that asks for a reason (stored in the audit log) before an action. */
export function ReasonDialog({
  open,
  title,
  note,
  confirmLabel,
  danger,
  onClose,
  onConfirm,
  busy,
  error,
  reasonLabel,
  onOpen,
}: {
  open: boolean;
  title: string;
  note?: string;
  confirmLabel: string;
  danger?: boolean;
  onClose: () => void;
  onConfirm: (reason: string) => void;
  busy?: boolean;
  error?: unknown;
  reasonLabel?: string;
  /** Clears the previous attempt's error when the dialog opens for another item. */
  onOpen?: () => void;
}) {
  const { t } = useTranslation();
  const [reason, setReason] = useState("");
  useEffect(() => {
    if (open) {
      setReason("");
      onOpen?.();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only on open
  }, [open]);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (reason.trim().length >= 3) onConfirm(reason.trim());
  };
  return (
    <Dialog open={open} title={title} onClose={onClose}>
      <form onSubmit={submit} className="stack">
        {note ? <p className="muted">{note}</p> : null}
        <Field label={reasonLabel ?? t("common.reason")} hint={t("common.reasonHint")}>
          {(p) => (
            <textarea
              {...p}
              rows={3}
              required
              minLength={3}
              maxLength={500}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          )}
        </Field>
        <ErrorAlert error={error} />
        <div className="row end">
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="submit" variant={danger ? "danger" : "primary"} busy={busy} disabled={reason.trim().length < 3}>
            {confirmLabel}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

export function CopyButton({ value }: { value: string }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  return (
    <Button
      variant="ghost"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
          setCopied(true);
          setTimeout(() => setCopied(false), 2000);
        } catch {
          /* clipboard blocked: user can select the text */
        }
      }}
    >
      {copied ? t("common.copied") : t("common.copy")}
    </Button>
  );
}

/** Secret shown once, with explicit acknowledgement before it disappears. */
export function SecretPanel({
  title,
  body,
  items,
  onDone,
}: {
  title: string;
  body: string;
  items: { label: string; value: string }[];
  onDone: () => void;
}) {
  const { t } = useTranslation();
  return (
    <Dialog open title={title} onClose={onDone}>
      <div className="stack">
        <Alert tone="warn">{body}</Alert>
        {items.map((it) => (
          <div key={it.label} className="secret">
            <span className="secret-label">{it.label}</span>
            <code dir="ltr" className="secret-value" data-testid={`secret-${it.label}`}>
              {it.value}
            </code>
            <CopyButton value={it.value} />
          </div>
        ))}
        <div className="row end">
          <Button variant="primary" onClick={onDone}>
            {t("integration.secretAck")}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

// Toasts (polite live region)

interface ToastApi {
  notify: (message: string) => void;
}
const ToastContext = createContext<ToastApi>({ notify: () => undefined });

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<{ id: number; message: string }[]>([]);
  const notify = useCallback((message: string) => {
    const id = Date.now() + Math.random();
    setItems((xs) => [...xs, { id, message }]);
    setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), 5000);
  }, []);
  return (
    <ToastContext.Provider value={{ notify }}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {items.map((x) => (
          <div key={x.id} className="toast">
            {x.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  return useContext(ToastContext);
}

export function LocalisedInputs({
  value,
  onChange,
  labelEn,
  labelAr,
  hint,
  errors,
  maxLength = 160,
}: {
  value: { en: string; ar: string };
  onChange: (v: { en: string; ar: string }) => void;
  labelEn: string;
  labelAr: string;
  hint?: string;
  errors?: { en?: string; ar?: string };
  maxLength?: number;
}) {
  return (
    <div className="grid-2">
      <Field label={labelEn} hint={hint} error={errors?.en}>
        {(p) => (
          <input
            {...p}
            dir="ltr"
            lang="en"
            required
            maxLength={maxLength}
            value={value.en}
            onChange={(e) => onChange({ ...value, en: e.target.value })}
          />
        )}
      </Field>
      <Field label={labelAr} hint={hint} error={errors?.ar}>
        {(p) => (
          <input
            {...p}
            dir="rtl"
            lang="ar"
            required
            maxLength={maxLength}
            value={value.ar}
            onChange={(e) => onChange({ ...value, ar: e.target.value })}
          />
        )}
      </Field>
    </div>
  );
}
