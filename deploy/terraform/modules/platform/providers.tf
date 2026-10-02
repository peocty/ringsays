# Real SMS (Taqnyat or Unifonic) and push (Firebase Cloud Messaging, Apple Push Notification service).
#
# Secrets: created here empty and CMEK encrypted; the values are added by hand, once, by the person
# holding the provider accounts (deploy/README.md, "Real providers"), so they never pass through
# OpenTofu state. Only External Secrets reads them, and only the API and the worker receive them.
#
# Firebase: the API and the worker send with their own workload identity (no key file), so they need
# the messaging role in the Firebase project. A push carries only the intent id and its kind, with a
# fixed bilingual text; nothing about the customer, the caller or the purpose leaves the KSA region.
locals {
  provider_secrets = var.real_providers ? toset(["ringsays-sms-api-key", "ringsays-apns-private-key"]) : toset([])
  push_senders     = var.real_providers ? toset(["api", "worker"]) : toset([])
}

resource "google_secret_manager_regional_secret" "provider" {
  for_each  = local.provider_secrets
  project   = var.project_id
  location  = var.region
  secret_id = each.value
  labels    = local.labels
  customer_managed_encryption {
    kms_key_name = google_kms_crypto_key.keys["secrets"].id
  }
  depends_on = [google_kms_crypto_key_iam_member.agents]
}

resource "google_secret_manager_regional_secret_iam_member" "provider_external_secrets" {
  for_each  = local.provider_secrets
  project   = var.project_id
  location  = var.region
  secret_id = google_secret_manager_regional_secret.provider[each.value].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = google_service_account.external_secrets.member
}

resource "google_project_service" "fcm" {
  count              = var.real_providers ? 1 : 0
  project            = var.firebase_project_id
  service            = "fcm.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_iam_member" "fcm_sender" {
  for_each = local.push_senders
  project  = var.firebase_project_id
  role     = "roles/firebasecloudmessaging.admin"
  member   = google_service_account.workload[each.value].member
}
