import { ApiError, authHeaders, notifyUnauthenticated } from "../api/client";
import { config } from "../config";

/** Download a file from an authenticated admin endpoint (bearer header, so a plain link cannot be used). */
export async function downloadAuthenticated(path: string, fallbackName: string): Promise<void> {
  const res = await fetch(`${config.apiBase}/admin/v1${path}`, { headers: await authHeaders() });
  if (res.status === 401) notifyUnauthenticated();
  if (!res.ok) throw new ApiError({ status: res.status });
  const blob = await res.blob();
  const name = fileNameFrom(res.headers.get("Content-Disposition") ?? "");
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name ?? fallbackName;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

/** File name from Content-Disposition, preferring RFC 5987 filename* (needed for Arabic names). */
export function fileNameFrom(disposition: string): string | null {
  const star = /filename\*\s*=\s*UTF-8''([^;]+)/i.exec(disposition);
  if (star?.[1]) {
    try {
      return decodeURIComponent(star[1].trim());
    } catch {
      /* fall through */
    }
  }
  return /filename\s*=\s*"([^"]+)"/i.exec(disposition)?.[1] ?? null;
}
