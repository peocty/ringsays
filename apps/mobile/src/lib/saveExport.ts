import { File, Paths } from "expo-file-system";
import * as Sharing from "expo-sharing";

/**
 * Personal data export as a JSON file the person keeps (PDPL right of access): written to the app
 * cache, then the system share sheet (Files, mail, AirDrop). The cache copy is removed afterwards.
 */
export async function saveExport(doc: unknown, name: string): Promise<void> {
  const file = new File(Paths.cache, name);
  if (file.exists) file.delete();
  file.create();
  file.write(JSON.stringify(doc, null, 2));
  try {
    if (await Sharing.isAvailableAsync()) {
      await Sharing.shareAsync(file.uri, { mimeType: "application/json", UTI: "public.json", dialogTitle: name });
    }
  } finally {
    if (file.exists) file.delete();
  }
}
