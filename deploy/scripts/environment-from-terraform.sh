#!/usr/bin/env bash
# Write the Kubernetes overlay's value files (environment.env, config.env, portal.env,
# backoffice-portal.env) from OpenTofu outputs, so domains, addresses, names and ranges are never typed
# by hand. identity.env (identity provider issuers) is the one file people edit. Usage: environment-from-terraform.sh ksa-staging
set -euo pipefail
env="${1:?environment, for example ksa-staging}"
here="$(cd "$(dirname "$0")/.." && pwd)"
tf="$here/terraform/environments/$env"
overlay="$here/kubernetes/overlays/$env"
[ -d "$tf" ] && [ -d "$overlay" ] || { echo "unknown environment $env" >&2; exit 2; }
write() {
  local output="$1" file="$2"
  {
    echo "# Written by deploy/scripts/environment-from-terraform.sh from \`tofu output\` ($env). Do not edit."
    tofu -chdir="$tf" output -json "$output" | python3 -c 'import json,sys; [print(f"{k}={v}") for k, v in sorted(json.load(sys.stdin).items())]'
  } > "$file.tmp"
  mv "$file.tmp" "$file"
  echo "wrote $file"
}
write kubernetes_environment "$overlay/environment.env"
write ringsays_config "$overlay/config.env"
write portal_config "$overlay/portal.env"
write backoffice_portal_config "$overlay/backoffice-portal.env"
