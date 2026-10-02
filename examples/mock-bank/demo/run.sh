#!/usr/bin/env bash
# One command walkthrough recording for bank pitches (MOCK data, local machine).
#   examples/mock-bank/demo/run.sh [output folder]      (default: examples/mock-bank/demo/out)
# Needs: `make setup` done, PostgreSQL and Redis running (`make up` starts them in Docker, or local
# services), migrations applied (`make migrate`), Chromium for Playwright, ffmpeg.
# Starts the RingSays API if nothing answers on :8000 and stops it again afterwards.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../../.." && pwd)"
out="$(mkdir -p "${1:-$here/out}" && cd "${1:-$here/out}" && pwd)"
command -v ffmpeg >/dev/null || { echo "ffmpeg is required" >&2; exit 2; }

started=""
if ! curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
  echo "starting the RingSays API (log: $out/api.log)"
  (cd "$root/backend" && RINGSAYS_ENVIRONMENT=local RINGSAYS_OTP_PER_IP_PER_HOUR=1000 \
    exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000) > "$out/api.log" 2>&1 &
  started=$!
  trap '[ -n "$started" ] && kill "$started" 2>/dev/null || true' EXIT
  for _ in $(seq 60); do curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1 && break; sleep 1; done
  curl -fsS http://127.0.0.1:8000/health >/dev/null || { echo "API did not start; see $out/api.log" >&2; exit 1; }
fi

cd "$root"
pnpm --filter @ringsays/react-native-sdk build >/dev/null
pnpm --filter @mockbank/server build >/dev/null
pnpm --filter @mockbank/app export:web >/dev/null
rm -rf "$out/raw" "$out/captions"
DEMO="$out" pnpm --filter @mockbank/app exec playwright test e2e/demo.spec.ts
python3 "$here/compose.py" "$out"
