# 0014 Hosting in the Kingdom: Google Cloud Dammam first, cloud neutral manifests

Status: Accepted, 2026-10-02

## Context
SAMA's cloud rules and the PDPL keep customer data of Saudi banks in the Kingdom. In October 2026 the
live hyperscaler regions in the Kingdom are Google Cloud Dammam (me-central2, through CNTXT), Oracle
Jeddah and Riyadh, Alibaba Cloud with stc (Riyadh) and Huawei Cloud Riyadh; AWS and Azure regions are
announced or new. Banks may also insist on a provider they already use.

## Decision
- **Primary target: Google Cloud me-central2** inside an Assured Workloads folder with the KSA data
  boundary: managed Kubernetes (GKE Autopilot), PostgreSQL (Cloud SQL), Redis (Memorystore), KMS,
  Secret Manager and regional load balancing all exist there. Regional services only; global load
  balancing is not used (not allowed under the data boundary).
- **Kustomize, not Helm.** Plain YAML a bank's security team can read line by line, no template
  logic, applied natively by kubectl, Argo CD and Flux. Environment values come from Terraform outputs
  (`environment-from-terraform.sh`), never typed twice. (Helm could not be installed in the build
  workspace either, so every rendered manifest is validated here only because it is plain YAML.)
- **OpenTofu or Terraform** for infrastructure, one project per environment, state in an encrypted
  bucket in me-central2.
- **Portability by design:** base manifests and the application know nothing about Google; the
  database needs no superuser and no BYPASSRLS; object storage speaks GCS or S3; the generic component
  covers ingress-nginx and other secret stores.
- **Secrets** live only in the cloud secret manager; External Secrets Operator gives each workload just
  its keys, mounted as files (`RINGSAYS_SETTINGS_DIR`).
- **Releases** deploy by digest; migrations run to completion before new code; production needs approval.

## Consequences
A second provider in the Kingdom is the disaster recovery path (Google Cloud has one KSA region). Real
SMS and push adapters are required before production can sign anyone in. Nothing was applied to a real
project from the build workspace; CI validates providers and builds images.
