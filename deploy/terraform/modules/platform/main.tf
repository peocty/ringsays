# RingSays platform in one Google Cloud project, region me-central2 (Dammam).
# Every service holding customer data is regional in the Kingdom and encrypted with keys from this
# project's Cloud KMS. Nothing has a public IP except the regional load balancer address.

locals {
  name   = "ringsays-${var.environment}"
  labels = { app = "ringsays", environment = var.environment, residency = "ksa" }
  prod   = var.environment == "production"
  # Private ranges (network policies and Kubernetes overlays read them from outputs).
  ranges = {
    nodes    = "${var.network_prefix}.0.0/22"
    proxy    = "${var.network_prefix}.8.0/23" # load balancer proxies (regional external and internal)
    services = "${var.network_prefix}.32.0/20"
    sql      = "${var.network_prefix}.64.0/20" # private services access: Cloud SQL
    redis    = "${var.network_prefix}.80.0/24" # private services access: Memorystore
    pods     = "${var.network_prefix}.128.0/17"
  }
  k8s_namespace = "ringsays"
}

data "google_project" "this" {
  project_id = var.project_id
}

resource "google_project_service" "apis" {
  for_each = toset([
    "artifactregistry.googleapis.com",
    "certificatemanager.googleapis.com",
    "cloudkms.googleapis.com",
    "compute.googleapis.com",
    "container.googleapis.com",
    "containeranalysis.googleapis.com",
    "dns.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "redis.googleapis.com",
    "secretmanager.googleapis.com",
    "servicenetworking.googleapis.com",
    "sqladmin.googleapis.com",
    "storage.googleapis.com",
    "sts.googleapis.com",
    "binaryauthorization.googleapis.com",
  ])
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}
