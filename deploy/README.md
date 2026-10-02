# Deploying RingSays in the Kingdom

Primary target: **Google Cloud, Dammam region (me-central2)**, bought through CNTXT (Google's exclusive
partner for KSA billing). Everything holding customer data stays in me-central2 and is encrypted with
keys from the environment's own Cloud KMS. The Kubernetes manifests are cloud neutral; a second KSA
cloud (Oracle Riyadh or Jeddah, Alibaba Cloud with stc, Huawei Cloud Riyadh, AWS Riyadh) needs only the
generic component and its own Terraform.

```text
deploy/
  terraform/bootstrap/            state bucket (CMEK, versioned), once per project
  terraform/modules/platform/     network, GKE, Cloud SQL, Redis, storage, registry, secrets, edge, IAM, policies
  terraform/environments/ksa-*/   staging and production roots
  kubernetes/base/                workloads, network policies, external secrets (cloud neutral)
  kubernetes/components/gcp-ksa/  regional Gateways, Cloud Armor, health checks, Secret Manager, workload identity
  kubernetes/components/generic-ingress/  ingress-nginx and Vault style store for other clouds
  kubernetes/overlays/ksa-*/      environment values (environment.env, config.env from Terraform)
  kubernetes/jobs/                migrate (every release), bootstrap (once, and after password rotation)
  scripts/validate.sh             every check CI runs on deployment code
  scripts/environment-from-terraform.sh   Terraform outputs to overlay values
  scripts/release.sh              prerequisites, migrations, rollout, in that order
```

## Architecture

| Layer | Google Cloud service | Settings that matter |
| --- | --- | --- |
| Edge | Regional external Application Load Balancer (GKE Gateway `gke-l7-regional-external-managed`) | Global load balancing is not allowed under the KSA data boundary; regional Cloud Armor with OWASP rules |
| Back office edge | Regional internal Application Load Balancer (`gke-l7-rilb`) | Reachable from the VPC only (operations VPN or Interconnect) |
| Compute | GKE Autopilot, three zones | Private nodes, private endpoint plus IAM checked DNS endpoint, Dataplane V2 network policies, KMS encrypted Kubernetes secrets, binary authorization (own registry only) |
| Database | Cloud SQL PostgreSQL 16, regional HA in production | Private IP only, TLS (verify CA), CMEK, PITR 7 days, backups 35 days, pgAudit (DDL and roles), no statement logging |
| Cache | Memorystore for Redis 7.2, Standard HA in production | AUTH, TLS, CMEK; holds counters and revocation lists only |
| Events | NATS JetStream in cluster (1 node staging, 3 nodes production) | Password auth, file storage, stream on 3 replicas in production |
| Evidence | Cloud Storage, me-central2 | CMEK, uniform access, public access prevention, no versioning (erasure on request is real), soft delete 7 days, regional endpoint |
| Secrets | Secret Manager regional secrets, me-central2 | CMEK; External Secrets Operator copies them into per workload Kubernetes secrets, mounted as files |
| Images | Artifact Registry, me-central2 | Immutable tags, deploy by digest; mirrors of Docker Hub, ghcr.io and quay.io for NATS and add ons; binary authorization admits this registry only |
| Cloud APIs | Private Google Access plus Private Service Connect regional endpoints | `storage.me-central2.rep.googleapis.com` and `secretmanager.me-central2.rep.googleapis.com` resolve to private addresses (regional endpoints are not served through Private Google Access) |
| Egress | Cloud NAT with two fixed addresses | Organisations can allow list RingSays for webhooks |
| Identity | Workload identity per workload, GitHub federation for CI | No service account keys anywhere (org policy forbids them); CI accepted only from the matching GitHub environment, and inside the cluster only through `kubernetes/platform/ci-rbac.yaml` (no Secret reads, no exec) |

Database roles: none is superuser, none has BYPASSRLS (migration 0006). The worker and back office
cross tenants through an explicit `system_roles` policy; the API role is limited to tenant and own
row policies. CI proves this on every push by bootstrapping as a CREATEROLE admin (`managed-postgres` job).

## Prerequisites (organisation administrator, once)

1. Google Cloud organisation and billing through CNTXT (KSA billing address).
2. Folder under **Assured Workloads, KSA data boundary** (with Access Justifications); create both
   environment projects inside it, for example `ringsays-ksa-staging` and `ringsays-ksa-production`.
3. Organisation log storage location `me-central2`, before the projects exist, so `_Default` and
   `_Required` log buckets are created in the Kingdom:
   `gcloud logging settings update --organization=ORG_ID --storage-location=me-central2`
4. Grant the platform team `roles/orgpolicy.policyAdmin` on the projects (Terraform sets project policies).
5. DNS: public zone for the domain (for example `ringsays.sa`), and certificates (see TLS below).

## First deployment of an environment

```bash
# 1. State bucket (local state, once)
tofu -chdir=deploy/terraform/bootstrap init
tofu -chdir=deploy/terraform/bootstrap apply -var project_id=ringsays-ksa-staging \
  -var 'state_admins=["group:ringsays-platform@peocit.com"]'
# copy the output into environments/ksa-staging/backend.hcl

# 2. Platform
cp deploy/terraform/environments/ksa-staging/terraform.tfvars.example deploy/terraform/environments/ksa-staging/terraform.tfvars
tofu -chdir=deploy/terraform/environments/ksa-staging init -backend-config=backend.hcl
tofu -chdir=deploy/terraform/environments/ksa-staging apply

# 3. Overlay values from outputs, then commit them
deploy/scripts/environment-from-terraform.sh ksa-staging

# 4. Cluster add ons (platform team, cluster admin):
#    - External Secrets Operator in namespace external-secrets, images through the mirror
#      (me-central2-docker.pkg.dev/<project>/mirror-ghcr/external-secrets/external-secrets), its
#      service account annotated with `tofu output external_secrets_service_account`
#    - cert-manager if certificates are issued in cluster (mirror-quay/jetstack/...)
#    - CI permissions: replace CI_SERVICE_ACCOUNT in deploy/kubernetes/platform/ci-rbac.yaml with
#      `tofu output ci` service_account, then kubectl apply -f it
#    - deploy/scripts/release.sh ksa-staging prerequisites   (creates the namespace and secret store)

# 5. Database roles (once; again after a password rotation). Needs one pushed API image (run the
#    release workflow's build once, or build and push by hand), pinned by digest:
(cd deploy/kubernetes/jobs/bootstrap && kustomize edit set image ringsays/api=<repository>/api@sha256:<digest>)
kustomize build deploy/kubernetes/jobs/bootstrap | kubectl apply -f -
kubectl -n ringsays wait --for=condition=complete job/ringsays-db-bootstrap --timeout=300s
kustomize build deploy/kubernetes/jobs/bootstrap | kubectl delete -f -     # admin credential leaves the cluster

# 6. GitHub environment "ksa-staging" (the name must match: federation checks it): variables
#    WIF_PROVIDER, CI_SERVICE_ACCOUNT (tofu output ci), IMAGE_REPOSITORY, CLUSTER_NAME, PROJECT_ID.
#    "ksa-production": same, plus required reviewers and protected v* tags.
#    Edit kubernetes/overlays/<env>/identity.env (identity provider issuers) and commit.

# 7. Release: push a tag v0.x.y (staging), or run the release workflow for production.
```

### TLS

Gateways read `ringsays-public-tls` and `ringsays-internal-tls` Kubernetes secrets. Either cert-manager
issues them (DNS 01 against Cloud DNS), or the bank's required certificate authority issues them and the
platform team stores them in Secret Manager (`ringsays-public-tls` as an ExternalSecret of type
kubernetes.io/tls). Banks often require a specific CA for anything they integrate with; ask early.

## Every release

`.github/workflows/release.yml`: build both images (API with the GCP extra), push to the
environment's registry, SBOM and provenance, Trivy scan (fails on fixable high or critical), pin
digests, then `deploy/scripts/release.sh`:

1. prerequisites only (namespace, identities, config, secret store and external secrets, network policies)
2. migration job as `ringsays_owner`, to completion; if it fails nothing else changes
3. everything else, then wait for each rollout; smoke test `/health`

Migrations must stay backward compatible with the running version (expand, then contract in a later release).

## Verification checklist after the first deployment

| Check | How |
| --- | --- |
| Regional storage endpoint works through private access | `kubectl -n ringsays exec deploy/ringsays-api -- python -c "from app.platform import blobs; s=blobs.get_store(); k=s.put(b'%PDF-x'); s.delete(k); print('ok')"` |
| Database TLS and roles | API `/ready` is 200; `psql` as admin: `select rolname, rolsuper, rolbypassrls from pg_roles where rolname like 'ringsays%'` all false |
| Real client address behind the load balancer | Sign in code requests from two phones count separately (per address limit) |
| Network policies | From the portal pod, `curl` to the database address must time out |
| Webhooks egress from fixed addresses | Point a test endpoint at a request bin; source address is one of `tofu output egress_addresses` |
| NATS cluster (production) | `kubectl -n ringsays exec ringsays-nats-0 -- wget -qO- localhost:8222/jsz` shows `meta_cluster` with 3 peers |
| Back office not public | `curl https://backoffice-api.<internal domain>` from the internet fails to resolve or connect |
| Real SMS | Request a code for a staff test number; it arrives from the registered sender name; API log shows `sms sent provider=...` with no number |
| Real push | Send a test intent to a staff phone with the app; notification shows the fixed bilingual text only |
| No local tools at the edge | `curl -I https://api.<domain>/dev/oidc/.well-known/openid-configuration` redirects to the portal |

## Operations

- **Backups and recovery:** Cloud SQL automated backups (35 days production) and point in time
  recovery (7 days of logs). Restore into a new instance, run `bootstrap_db` against it, repoint the
  `ringsays-db-url-*` secrets.
- **Disaster recovery:** Google Cloud has one region in the Kingdom; production spans its three zones.
  A second region inside the Kingdom means a second provider (Oracle Jeddah and Riyadh have two
  regions). Plan: nightly logical export to that provider's object storage, encrypted, kept in the Kingdom.
- **Secret rotation:** taint the `random_password` (or `random_bytes`) resource, apply, rerun the
  bootstrap job for database passwords (it sends SCRAM verifiers, so passwords never appear in the
  pgAudit role log), then `kubectl rollout restart` the workloads. External Secrets refreshes hourly.
- **Scaling:** API autoscales on CPU (3 to 20 in production). Worker replicas are safe to raise
  (leases, SKIP LOCKED). Cloud SQL tier and Redis size are Terraform variables.
- **Maintenance windows (Kingdom weekend):** GKE Friday and Saturday 00:00 to 08:00 Riyadh; Cloud SQL
  and Redis Friday 03:00 Riyadh.
- **Erasure:** evidence deleted through the portal is gone from the bucket at once and from soft delete
  after 7 days; database backups age out after 35 days (production).

## Real providers (production)

Production sends sign in codes by SMS and intent notifications by push; staging keeps MOCK (codes in
the API log). The API refuses to start in production with MOCK, or with real providers half configured.

| What | Where | Who |
| --- | --- | --- |
| SMS provider and sender name | `kubernetes/overlays/ksa-production/providers.env`: `RINGSAYS_SMS_PROVIDER` (`taqnyat` or `unifonic`), `RINGSAYS_SMS_SENDER_ID` | PEOCIT, after CST registers the sender name |
| SMS credential | Secret Manager `ringsays-sms-api-key`: Taqnyat bearer token, or Unifonic AppSid | Holder of the provider account |
| Firebase project | `firebase_project_id` in `environments/ksa-production/terraform.tfvars`; OpenTofu grants the API and worker identities the messaging role there (no key file) | Platform team |
| Apple key id, team, topic | `providers.env`: `RINGSAYS_APNS_KEY_ID`, `RINGSAYS_APNS_TEAM_ID`, `RINGSAYS_APNS_TOPIC` (the app bundle id) | PEOCIT Apple developer account owner |
| Apple signing key | Secret Manager `ringsays-apns-private-key`: the whole `.p8` file | Same |

OpenTofu (`real_providers = true`) creates both secrets empty and CMEK encrypted, so the values never
pass through state. It also enables the messaging API in the Firebase project and grants the API and
worker identities the messaging role there, so the identity running `tofu apply` needs Service Usage
Admin and Project IAM Admin on the Firebase project too (a separate project, outside the KSA folder).
Add the secret values once, then release:

```sh
gcloud secrets versions add ringsays-sms-api-key --location=me-central2 --data-file=- <<< "$TOKEN"
gcloud secrets versions add ringsays-apns-private-key --location=me-central2 --data-file=AuthKey_XXXX.p8
```

`release.sh` refuses to release while `providers.env` still has `REPLACE_` values. Check push with a
TestFlight or store build (production APNs); a development build has a sandbox token that production
refuses (`BadDeviceToken`). Dead tokens (app removed) are cleared automatically and the provider's
error appears in the intent's delivery trail. A provider outage
shows as `503 sms_unavailable` on code requests (no provider detail reaches the caller) and as failed
push attempts on the intent, after which delivery falls back as the policy allows. Logs carry only the
provider name and message id, never the number or the code.

Data: an SMS carries the code and the bank neutral text; a push carries the intent id and its kind with
a fixed bilingual text, so nothing about the customer or the caller passes through Google or Apple.
Taqnyat and Unifonic are Saudi providers. Changing provider is one setting and one secret.

## Not yet in place

- Provider accounts themselves (CST sender registration, Taqnyat or Unifonic contract, Apple and Firebase
  app registrations): the adapters are built and tested against each provider's documented API, not yet
  against a live account.
- Egress is any public address on 443; narrow to the provider list with an egress proxy or FQDN
  policies once SMS, push and identity providers are fixed.
- Asymmetric token signing with a KMS held key (HS256 shared secret today).
- Google Groups for GKE RBAC (`authenticator_groups_config`) once the platform team's group exists.
- Nothing here has been applied to a real project from this workspace: provider downloads, container
  registries and image builds are blocked here. CI runs `tofu validate`, builds the images and can plan.

## Other KSA clouds

| Need | Google Cloud (here) | Oracle Cloud | Alibaba Cloud (stc) | AWS Riyadh |
| --- | --- | --- | --- | --- |
| Kubernetes | GKE Autopilot | OKE | ACK | EKS |
| PostgreSQL | Cloud SQL | OCI Database with PostgreSQL | ApsaraDB RDS PostgreSQL | RDS PostgreSQL |
| Redis | Memorystore | OCI Cache | Tair or ApsaraDB Redis | ElastiCache |
| Object storage | GCS (`RINGSAYS_BLOB_BACKEND=gcs`) | Object Storage S3 API (`s3`) | OSS S3 API (`s3`) | S3 (`s3`) |
| Secrets | Secret Manager | OCI Vault | KMS Secrets Manager | Secrets Manager |
| Edge | Gateway (regional ALB), Cloud Armor | OCI LB, WAF | ALB, WAF | ALB, AWS WAF |
| Manifests | `components/gcp-ksa` | `components/generic-ingress` | `components/generic-ingress` | `components/generic-ingress` |

The database setup is the same everywhere: `bootstrap_db` as the service's admin user, then migrations.
