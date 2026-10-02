#!/usr/bin/env bash
# Render every overlay and job, then check: Kubernetes and CRD schemas (kubeconform, strict),
# kube-linter, Checkov (accepted findings in deploy/.checkov.yaml), and OpenTofu formatting.
# Needs: kustomize, kubeconform, kube-linter, checkov, tofu (CI installs them).
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
out="$(mktemp -d)"
trap 'rm -rf "$out"' EXIT
crds='https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'
k8s_version="${K8S_VERSION:-1.33.0}"

for o in "$here"/kubernetes/overlays/*/; do
  name="$(basename "$o")"
  kustomize build "$o" > "$out/$name.yaml"
done
for j in "$here"/kubernetes/jobs/*/; do
  name="job-$(basename "$j")"
  kustomize build "$j" > "$out/$name.yaml"
done

# Placeholders must never reach a rendered GCP overlay.
if grep -nE '192\.0\.2\.|[^_]PROJECT_ID|GSA_DOMAIN|PUBLIC_ADDRESS_NAME|SECURITY_POLICY|\*\.DOMAIN' "$out"/ksa-*.yaml; then
  echo "unreplaced placeholder in a KSA overlay" >&2
  exit 1
fi

kubeconform -strict -summary -kubernetes-version "$k8s_version" \
  -schema-location default -schema-location "$crds" "$out"/*.yaml
# One environment at a time: objects share names across overlays.
# Jobs are applied into an environment that already has their service account (base).
for f in "$out"/*.yaml; do
  case "$(basename "$f")" in
    job-*) kube-linter lint --exclude non-existent-service-account "$f" ;;
    *) kube-linter lint "$f" ;;
  esac
done
checkov --skip-download --config-file "$here/.checkov.yaml" -d "$out" --framework kubernetes --compact --quiet
checkov --skip-download --config-file "$here/.checkov.yaml" -d "$here/terraform" --framework terraform --compact --quiet
tofu fmt -check -recursive "$here/terraform"
echo "deploy: all checks passed"
