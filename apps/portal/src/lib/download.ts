import { authHeaders } from "../api/client";
import { config } from "../config";

/** Download a file from an authenticated admin endpoint (bearer header, so a plain link cannot be used). */
export async function downloadAuthenticated(path: string, fallbackName: string): Promise<void> {
  const res = await fetch(`${config.apiBase}/admin/v1${path}`, { headers: await authHeaders() });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const blob = await res.blob();
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const match = /filename="([^"]+)"/.exec(disposition);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = match?.[1] ?? fallbackName;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}
