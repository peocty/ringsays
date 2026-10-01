// Static server for the web build with single page fallback (browser tests and local preview only).
import { createReadStream, existsSync, statSync } from "node:fs";
import { createServer } from "node:http";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "../dist");
const port = Number(process.env.PORT ?? 8082);
const types = { ".html": "text/html", ".js": "text/javascript", ".png": "image/png", ".json": "application/json", ".css": "text/css", ".ttf": "font/ttf" };

createServer((req, res) => {
  const url = new URL(req.url ?? "/", "http://x");
  let file = path.join(root, decodeURIComponent(url.pathname));
  if (!file.startsWith(root) || !existsSync(file) || statSync(file).isDirectory()) file = path.join(root, "index.html");
  res.writeHead(200, { "Content-Type": types[path.extname(file)] ?? "application/octet-stream" });
  createReadStream(file).pipe(res);
}).listen(port, "127.0.0.1", () => console.log(`serving ${root} on ${port}`));
