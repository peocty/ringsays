import type { Folder } from "@ringsays/client";

export const keys = {
  inbox: (folder: Folder) => ["inbox", folder] as const,
  inboxAll: ["inbox"] as const,
  intent: (id: string) => ["intent", id] as const,
  preferences: ["preferences"] as const,
  consents: ["consents"] as const,
};
