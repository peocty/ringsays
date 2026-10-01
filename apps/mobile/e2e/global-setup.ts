import { execFileSync } from "node:child_process";
import { writeFileSync } from "node:fs";
import path from "node:path";

const API = process.env.E2E_API ?? "http://127.0.0.1:8000";

export default async function globalSetup(): Promise<void> {
  const ok = await fetch(`${API}/health`).then((r) => r.ok).catch(() => false);
  if (!ok) throw new Error(`API not reachable at ${API}`);
  const backend = path.resolve(import.meta.dirname, "../../../backend");
  const py = process.env.E2E_PYTHON ?? path.join(backend, ".venv/bin/python");
  const tag = `m${Date.now().toString(36)}`;
  const out = execFileSync(py, ["-m", "app.scripts.seed", "--tag", tag, "--json"], { cwd: backend }).toString();
  writeFileSync(path.join(import.meta.dirname, ".seed.json"), JSON.stringify({ ...JSON.parse(out), tag }));
}
