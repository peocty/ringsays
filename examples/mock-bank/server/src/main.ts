import { buildApp } from "./app.js";
import { loadConfig } from "./config.js";

const config = loadConfig();
const app = buildApp({ config });
app.listen({ port: config.port, host: config.host }).then(
  () => app.log.info(`Mock Bank on http://${config.host}:${config.port} (console: /console)`),
  (err: Error) => {
    app.log.error(err.message);
    process.exit(1);
  },
);
