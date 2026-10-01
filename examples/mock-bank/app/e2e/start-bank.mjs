// Browser tests: fresh MOCK tenant with a webhook to this server, then start the Mock Bank server.
import { execFileSync, spawn } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";

const server = path.resolve(import.meta.dirname, "../../server");
const env = { ...process.env, PORT: "4100", CONSOLE_PASSWORD: "e2e-console", SEED_TAG: `e2e${Date.now().toString(36)}` };
execFileSync("node", ["scripts/setup.mjs"], { cwd: server, env, stdio: "inherit" });
const vars = Object.fromEntries(
  readFileSync(path.join(server, ".env.local"), "utf8")
    .split("\n")
    .filter(Boolean)
    .map((l) => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]),
);
const child = spawn("node", ["dist/main.js"], { cwd: server, env: { ...env, ...vars, DATA_FILE: "", LOG_LEVEL: "warn" }, stdio: "inherit" });
for (const s of ["SIGINT", "SIGTERM"]) process.on(s, () => child.kill(s));
child.on("exit", (code) => process.exit(code ?? 0));
