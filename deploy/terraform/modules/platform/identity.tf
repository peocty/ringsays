# One Google service account per workload, used through workload identity (no keys anywhere).
locals {
  workloads = {
    api        = "Tenant facing API"
    worker     = "Background jobs"
    backoffice = "Back office API"
    jobs       = "Migrations and database bootstrap"
  }
  wi_pool = "${var.project_id}.svc.id.goog"
}

resource "google_service_account" "workload" {
  for_each     = local.workloads
  project      = var.project_id
  account_id   = "ringsays-${each.key}"
  display_name = "RingSays ${each.value} (${var.environment})"
}

resource "google_service_account_iam_member" "workload_identity" {
  for_each           = local.workloads
  service_account_id = google_service_account.workload[each.key].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${local.wi_pool}[${local.k8s_namespace}/ringsays-${each.key}]"
  depends_on         = [google_container_cluster.this]
}

# Evidence: the API stores and removes it, the back office reads it, nobody else touches it.
resource "google_storage_bucket_iam_member" "api_evidence" {
  bucket = google_storage_bucket.evidence.name
  role   = "roles/storage.objectUser"
  member = google_service_account.workload["api"].member
}

resource "google_storage_bucket_iam_member" "backoffice_evidence" {
  bucket = google_storage_bucket.evidence.name
  role   = "roles/storage.objectViewer"
  member = google_service_account.workload["backoffice"].member
}

# External Secrets Operator reads RingSays secrets (and only those) from Secret Manager.
resource "google_service_account" "external_secrets" {
  project      = var.project_id
  account_id   = "ringsays-external-secrets"
  display_name = "External Secrets Operator (${var.environment})"
}

resource "google_service_account_iam_member" "external_secrets_wi" {
  service_account_id = google_service_account.external_secrets.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${local.wi_pool}[external-secrets/external-secrets]"
  depends_on         = [google_container_cluster.this]
}

# GitHub Actions: build and push images, and deploy, through workload identity federation. Only the
# named repository, only its main branch and release tags.
resource "google_iam_workload_identity_pool" "github" {
  project                   = var.project_id
  workload_identity_pool_id = "github"
  display_name              = "GitHub Actions"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  display_name                       = "GitHub"
  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }
  attribute_condition = "assertion.repository == '${var.github_repository}' && (assertion.ref == 'refs/heads/main' || assertion.ref.startsWith('refs/tags/v'))"
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account" "ci" {
  project      = var.project_id
  account_id   = "ringsays-ci"
  display_name = "RingSays CI (${var.environment})"
}

resource "google_service_account_iam_member" "ci_federation" {
  service_account_id = google_service_account.ci.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repository}"
}

resource "google_artifact_registry_repository_iam_member" "ci_push" {
  project    = var.project_id
  location   = var.region
  repository = google_artifact_registry_repository.images.repository_id
  role       = "roles/artifactregistry.writer"
  member     = google_service_account.ci.member
}

resource "google_project_iam_member" "ci_deploy" {
  project = var.project_id
  role    = "roles/container.developer"
  member  = google_service_account.ci.member
}
