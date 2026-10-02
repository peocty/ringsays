# Run once per environment project, with local state, by a platform administrator:
#   tofu -chdir=deploy/terraform/bootstrap init
#   tofu -chdir=deploy/terraform/bootstrap apply -var project_id=<project>
# It creates the encrypted, versioned state bucket in me-central2 that the environment uses
# (environments/*/backend.hcl). The state holds generated secrets: only the platform team may read it.
terraform {
  required_version = ">= 1.9" # variable validation may reference other variables
  required_providers {
    google = { source = "hashicorp/google", version = ">= 6.50, < 8.0" }
  }
}

variable "project_id" {
  type = string
}

variable "state_admins" {
  description = "Groups or users allowed to read and write state, for example group:ringsays-platform@peocit.com"
  type        = list(string)
}

provider "google" {
  project = var.project_id
  region  = "me-central2"
}


resource "google_project_service" "apis" {
  for_each           = toset(["cloudkms.googleapis.com", "storage.googleapis.com"])
  service            = each.value
  disable_on_destroy = false
}

resource "google_kms_key_ring" "state" {
  name       = "tofu-state"
  location   = "me-central2"
  depends_on = [google_project_service.apis]
}

resource "google_kms_crypto_key" "state" {
  name            = "state"
  key_ring        = google_kms_key_ring.state.id
  rotation_period = "7776000s"
  lifecycle {
    prevent_destroy = true
  }
}

data "google_storage_project_service_account" "gcs" {}

resource "google_kms_crypto_key_iam_member" "gcs" {
  crypto_key_id = google_kms_crypto_key.state.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${data.google_storage_project_service_account.gcs.email_address}"
}

resource "google_storage_bucket" "state" {
  name                        = "${var.project_id}-tofu-state"
  location                    = "ME-CENTRAL2"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  versioning {
    enabled = true
  }
  encryption {
    default_kms_key_name = google_kms_crypto_key.state.id
  }
  lifecycle_rule {
    condition {
      num_newer_versions = 50
    }
    action {
      type = "Delete"
    }
  }
  depends_on = [google_kms_crypto_key_iam_member.gcs]
}

resource "google_storage_bucket_iam_member" "admins" {
  for_each = toset(var.state_admins)
  bucket   = google_storage_bucket.state.name
  role     = "roles/storage.objectAdmin"
  member   = each.value
}

output "backend" {
  value = "bucket = \"${google_storage_bucket.state.name}\"\nprefix = \"ringsays\""
}
