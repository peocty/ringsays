// Local only: create a fresh MOCK tenant in RingSays with a webhook to this server, and write
// .env.local with its credentials and a random console password. Run again for a clean tenant.
import { execFileSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { writeFileSync } from "node:fs";
import path from "node:path";

const here = import.meta.dirname;
const backend = path.resolve(here, "../../../../backend");
const py = process.env.RINGSAYS_PYTHON ?? path.join(backend, ".venv/bin/python");
const port = process.env.PORT ?? "4100";
const tag = process.env.SEED_TAG ?? `bank${Date.now().toString(36)}`;
const out = execFileSync(py, ["-m", "app.scripts.seed", "--tag", tag, "--json", "--webhook-url", `http://127.0.0.1:${port}/webhooks/ringsays`], {
  cwd: backend,
  env: { ...process.env, RINGSAYS_ENVIRONMENT: "local" },
}).toString();
const s = JSON.parse(out);
const password = process.env.CONSOLE_PASSWORD ?? randomBytes(9).toString("base64url");
const env = [
  `PORT=${port}`,
  `RINGSAYS_API=${process.env.RINGSAYS_API ?? "http://127.0.0.1:8000"}`,
  `RINGSAYS_CLIENT_ID=${s.client_id}`,
  `RINGSAYS_CLIENT_SECRET=${s.client_secret}`,
  `RINGSAYS_AGENT_ID=${s.agent_id}`,
  `RINGSAYS_WEBHOOK_SECRET=${s.webhook_secret}`,
  `CONSOLE_PASSWORD=${password}`,
  `DATA_FILE=data/state-${tag}.json`,
  "",
].join("\n");
writeFileSync(path.join(here, "../.env.local"), env, { mode: 0o600 });
console.log(`Mock Bank tenant ${s.tenant_id} ready; .env.local written.`);
console.log(`Console password: ${password}`);
