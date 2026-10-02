# Customer managed keys, one per purpose, rotated every 90 days. Keys live in me-central2 with the data.
resource "google_kms_key_ring" "this" {
  project    = var.project_id
  name       = local.name
  location   = var.region
  depends_on = [google_project_service.apis]
}

locals {
  key_purposes = ["database", "redis", "storage", "secrets", "registry", "kubernetes"]
}

resource "google_kms_crypto_key" "keys" {
  for_each        = toset(local.key_purposes)
  name            = each.value
  key_ring        = google_kms_key_ring.this.id
  rotation_period = "7776000s"
  purpose         = "ENCRYPT_DECRYPT"
  labels          = local.labels
  version_template {
    algorithm        = "GOOGLE_SYMMETRIC_ENCRYPTION"
    protection_level = local.prod ? "HSM" : "SOFTWARE"
  }
  lifecycle {
    prevent_destroy = true
  }
}

# Service agents that encrypt on our behalf (created explicitly so key grants never race them).
resource "google_project_service_identity" "agents" {
  provider   = google-beta
  for_each   = toset(["sqladmin.googleapis.com", "secretmanager.googleapis.com", "artifactregistry.googleapis.com", "redis.googleapis.com", "container.googleapis.com"])
  project    = var.project_id
  service    = each.value
  depends_on = [google_project_service.apis]
}

data "google_storage_project_service_account" "gcs" {
  project    = var.project_id
  depends_on = [google_project_service.apis]
}

locals {
  n = data.google_project.this.number
  key_users = {
    database   = "serviceAccount:${google_project_service_identity.agents["sqladmin.googleapis.com"].email}"
    redis      = "serviceAccount:${google_project_service_identity.agents["redis.googleapis.com"].email}"
    storage    = "serviceAccount:${data.google_storage_project_service_account.gcs.email_address}"
    secrets    = "serviceAccount:${google_project_service_identity.agents["secretmanager.googleapis.com"].email}"
    registry   = "serviceAccount:${google_project_service_identity.agents["artifactregistry.googleapis.com"].email}"
    kubernetes = "serviceAccount:service-${local.n}@container-engine-robot.iam.gserviceaccount.com"
  }
}

resource "google_kms_crypto_key_iam_member" "agents" {
  for_each      = local.key_users
  crypto_key_id = google_kms_crypto_key.keys[each.key].id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = each.value
  depends_on    = [google_project_service_identity.agents]
}
