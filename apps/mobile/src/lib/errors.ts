import { RingSaysError } from "@ringsays/client";
import type { TFunction } from "i18next";

/** One sentence the person can act on. Server detail (English) is never shown in the app. */
export function errorMessage(err: unknown, t: TFunction): string {
  if (!(err instanceof RingSaysError)) return t("errors.generic");
  if (err.status === 0) return t("errors.network");
  if (err.status === 429) {
    return err.retryAfterS ? t("errors.tooMany", { seconds: err.retryAfterS }) : t("errors.tooManyLater");
  }
  if (err.status === 401) return err.code === "signed_out" ? t("errors.signedOut") : t("errors.wrongCode");
  if (err.status === 410) return t("errors.gone");
  if (err.status === 409 || err.status === 412) return t("errors.conflict");
  return t("errors.generic");
}
