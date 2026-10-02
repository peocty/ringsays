# Values for the Kubernetes overlay (deploy/scripts/environment-from-terraform.sh writes environment.env).
output "kubernetes_environment" {
  description = "Keys and values for deploy/kubernetes/overlays/<env>/environment.env."
  value = {
    PROJECT_ID            = var.project_id
    GSA_DOMAIN            = "${var.project_id}.iam.gserviceaccount.com"
    DOMAIN                = var.domain
    INTERNAL_DOMAIN       = local.internal_domain
    PUBLIC_ADDRESS_NAME   = google_compute_address.public.name
    SECURITY_POLICY       = google_compute_region_security_policy.edge.name
    LB_PROXY_CIDR         = local.ranges.proxy
    HEALTH_CHECK_CIDR     = "35.191.0.0/16"
    HEALTH_CHECK_CIDR_2   = "130.211.0.0/22"
    DATABASE_CIDR         = local.ranges.sql
    REDIS_CIDR            = local.ranges.redis
    GOOGLE_APIS_CIDR      = "199.36.153.8/30"
    REGIONAL_APIS_CIDR    = local.ranges.psc
    INTERNAL_ADDRESS_NAME = google_compute_address.internal.name
  }
}

# Settings for the RingSays containers that come from infrastructure (overlay config.env).
output "ringsays_config" {
  value = {
    RINGSAYS_JWT_ISSUER           = "https://api.${var.domain}"
    RINGSAYS_PORTAL_ORIGINS       = jsonencode(["https://portal.${var.domain}"])
    RINGSAYS_PORTAL_REDIRECT_URIS = jsonencode(["https://portal.${var.domain}/auth/callback"])
    RINGSAYS_BLOB_BUCKET          = google_storage_bucket.evidence.name
    RINGSAYS_BLOB_ENDPOINT        = "https://storage.${var.region}.rep.googleapis.com"
    # Google appends "<client>, <load balancer address>": proxies subnet and both gateway addresses.
    RINGSAYS_TRUSTED_PROXIES = jsonencode([local.ranges.proxy, "${google_compute_address.public.address}/32", "${google_compute_address.internal.address}/32"])
  }
}

output "portal_config" {
  value = { API_BASE = "https://api.${var.domain}" }
}

output "backoffice_portal_config" {
  value = {
    API_BASE = "https://backoffice-api.${local.internal_domain}"
    ORIGINS  = jsonencode(["https://backoffice.${local.internal_domain}"])
  }
}

output "evidence_bucket" {
  value = google_storage_bucket.evidence.name
}

output "image_repository" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

output "cluster" {
  value = { name = google_container_cluster.this.name, location = google_container_cluster.this.location }
}

output "egress_addresses" {
  description = "Fixed outbound addresses; organisations may allow list them for webhooks."
  value       = google_compute_address.nat[*].address
}

output "ci" {
  description = "For the GitHub Actions google-github-actions/auth step."
  value = {
    workload_identity_provider = google_iam_workload_identity_pool_provider.github.name
    service_account            = google_service_account.ci.email
  }
}

output "external_secrets_service_account" {
  description = "Annotate the external-secrets Kubernetes service account with this (iam.gke.io/gcp-service-account)."
  value       = google_service_account.external_secrets.email
}
