/** Mock Bank server settings, from the environment (`pnpm setup` writes .env.local for local runs). */
export interface Config {
  port: number;
  host: string;
  ringsaysApi: string;
  clientId: string;
  clientSecret: string;
  agentId: string;
  webhookSecret: string;
  consolePassword: string;
  appOrigins: string[];
  dataFile: string | null;
}

function need(name: string): string {
  const v = process.env[name];
  if (!v) throw new Error(`${name} is not set (run: pnpm --filter @mockbank/server run setup)`);
  return v;
}

export function loadConfig(): Config {
  return {
    port: Number(process.env.PORT ?? 4100),
    host: process.env.HOST ?? "127.0.0.1",
    ringsaysApi: process.env.RINGSAYS_API ?? "http://127.0.0.1:8000",
    clientId: need("RINGSAYS_CLIENT_ID"),
    clientSecret: need("RINGSAYS_CLIENT_SECRET"),
    agentId: process.env.RINGSAYS_AGENT_ID ?? "agt_demo_01",
    webhookSecret: need("RINGSAYS_WEBHOOK_SECRET"),
    consolePassword: need("CONSOLE_PASSWORD"),
    appOrigins: (process.env.APP_ORIGINS ?? "http://127.0.0.1:8082,http://localhost:8082").split(",").map((s) => s.trim()),
    dataFile: process.env.DATA_FILE === "" ? null : (process.env.DATA_FILE ?? "data/state.json"),
  };
}
