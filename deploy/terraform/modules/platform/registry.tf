# Container images in the Kingdom. Tags are immutable; releases are deployed by digest.
resource "google_artifact_registry_repository" "images" {
  project       = var.project_id
  location      = var.region
  repository_id = "ringsays"
  format        = "DOCKER"
  kms_key_name  = google_kms_crypto_key.keys["registry"].id
  labels        = local.labels
  docker_config {
    immutable_tags = true
  }
  cleanup_policy_dry_run = false
  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 30
    }
  }
  cleanup_policies {
    id     = "drop-old-untagged"
    action = "DELETE"
    condition {
      tag_state  = "UNTAGGED"
      older_than = "2592000s"
    }
  }
  depends_on = [google_kms_crypto_key_iam_member.agents]
}

# Third party images (NATS) through a scanned mirror instead of pulling from the internet at run time.
resource "google_artifact_registry_repository" "mirror" {
  project       = var.project_id
  location      = var.region
  repository_id = "mirror"
  format        = "DOCKER"
  mode          = "REMOTE_REPOSITORY"
  kms_key_name  = google_kms_crypto_key.keys["registry"].id
  labels        = local.labels
  remote_repository_config {
    description = "Docker Hub"
    docker_repository {
      public_repository = "DOCKER_HUB"
    }
  }
  depends_on = [google_kms_crypto_key_iam_member.agents]
}

# Add ons (External Secrets from ghcr.io, cert-manager from quay.io) come through mirrors too, so
# binary authorization can allow this project's registry only.
resource "google_artifact_registry_repository" "addon_mirror" {
  for_each      = { ghcr = "https://ghcr.io", quay = "https://quay.io" }
  project       = var.project_id
  location      = var.region
  repository_id = "mirror-${each.key}"
  format        = "DOCKER"
  mode          = "REMOTE_REPOSITORY"
  kms_key_name  = google_kms_crypto_key.keys["registry"].id
  labels        = local.labels
  remote_repository_config {
    description = each.value
    docker_repository {
      custom_repository {
        uri = each.value
      }
    }
  }
  depends_on = [google_kms_crypto_key_iam_member.agents]
}

resource "google_artifact_registry_repository_iam_member" "nodes_pull" {
  for_each = toset(concat(
    [google_artifact_registry_repository.images.repository_id, google_artifact_registry_repository.mirror.repository_id],
    [for r in google_artifact_registry_repository.addon_mirror : r.repository_id],
  ))
  project    = var.project_id
  location   = var.region
  repository = each.value
  role       = "roles/artifactregistry.reader"
  member     = google_service_account.nodes.member
}
