#!/usr/bin/env bash
# Roll a release out to one environment: prerequisites first (namespace, identities, secrets), then
# migrations to completion, then workloads, then wait for every rollout. Images must already be pinned
# by digest in the overlay and in jobs/migrate (kustomize edit set image ...@sha256:...).
# Usage: release.sh ksa-staging                  (kubectl context must point at the environment's cluster)
#        release.sh ksa-staging prerequisites    (first install: namespace, identities, secrets only)
set -euo pipefail
env="${1:?environment, for example ksa-staging}"
mode="${2:-release}"
here="$(cd "$(dirname "$0")/.." && pwd)"
overlay="$here/kubernetes/overlays/$env"
rendered="$(mktemp)"
trap 'rm -f "$rendered" "$rendered.pre"' EXIT

if [ "$mode" = release ] && grep -q REPLACED_BY_PIPELINE "$overlay/kustomization.yaml" "$here/kubernetes/jobs/migrate/kustomization.yaml"; then
  echo "images are not pinned (REPLACED_BY_PIPELINE still present)" >&2
  exit 2
fi

kustomize build "$overlay" > "$rendered"

# 1. Prerequisites only: what the migration job needs to start.
python3 - "$rendered" > "$rendered.pre" <<'PY'
import sys, yaml
keep = {"Namespace", "ServiceAccount", "ConfigMap", "ClusterSecretStore", "ExternalSecret", "NetworkPolicy"}
docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d and d.get("kind") in keep]
print(yaml.safe_dump_all(docs, sort_keys=False))
PY
kubectl apply --server-side -f "$rendered.pre"
kubectl -n ringsays wait --for=condition=Ready externalsecret --all --timeout=180s
if [ "$mode" = prerequisites ]; then
  echo "prerequisites for $env applied"
  exit 0
fi

# 2. Migrations (as ringsays_owner) to completion before any new code runs.
kubectl -n ringsays delete job ringsays-migrate --ignore-not-found --wait=true
kustomize build "$here/kubernetes/jobs/migrate" | kubectl apply -f -
kubectl -n ringsays wait --for=condition=Ready externalsecret/ringsays-migrate-secrets --timeout=180s
if ! kubectl -n ringsays wait --for=condition=complete job/ringsays-migrate --timeout=900s; then
  kubectl -n ringsays logs job/ringsays-migrate --tail=100 || true
  echo "migration failed; nothing else was changed" >&2
  exit 1
fi

# 3. Everything, then wait.
kubectl apply --server-side -f "$rendered"
for d in ringsays-api ringsays-worker ringsays-backoffice ringsays-portal ringsays-backoffice-portal; do
  kubectl -n ringsays rollout status "deployment/$d" --timeout=600s
done
kubectl -n ringsays rollout status statefulset/ringsays-nats --timeout=600s
echo "release to $env complete"
