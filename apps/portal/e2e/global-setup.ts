import { execFileSync } from "node:child_process";
import { writeFileSync } from "node:fs";
import path from "node:path";

const API = process.env.E2E_API ?? "http://127.0.0.1:8000";
export const SEED_FILE = path.join(import.meta.dirname, ".seed.json");

/** Seeds a fresh MOCK tenant with tagged people, so every run is isolated from earlier data. */
export default async function globalSetup(): Promise<void> {
  const health = await fetch(`${API}/health`).catch(() => null);
  if (!health?.ok) throw new Error(`API not reachable at ${API}; start it before running browser tests`);
  const backend = path.resolve(import.meta.dirname, "../../../backend");
  const python = process.env.E2E_PYTHON ?? path.join(backend, ".venv/bin/python");
  const tag = `e2e${Date.now().toString(36)}`;
  const out = execFileSync(python, ["-m", "app.scripts.seed", "--tag", tag, "--json"], { cwd: backend }).toString();
  writeFileSync(SEED_FILE, JSON.stringify({ ...JSON.parse(out), tag }));
}
