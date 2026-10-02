# RingSays production in Google Cloud Dammam (me-central2).
#   tofu -chdir=deploy/terraform/environments/ksa-production init -backend-config=backend.hcl
#   tofu -chdir=deploy/terraform/environments/ksa-production plan
terraform {
  required_version = ">= 1.6"
  backend "gcs" {}
  required_providers {
    google      = { source = "hashicorp/google", version = ">= 6.50, < 8.0" }
    google-beta = { source = "hashicorp/google-beta", version = ">= 6.50, < 8.0" }
    random      = { source = "hashicorp/random", version = ">= 3.6, < 4.0" }
  }
}

provider "google" {
  project = var.project_id
  region  = "me-central2"
}

provider "google-beta" {
  project = var.project_id
  region  = "me-central2"
}

variable "project_id" {
  type = string
}

variable "domain" {
  type = string
}

variable "ops_cidrs" {
  type = list(string)
}

variable "github_repository" {
  type = string
}

variable "alert_email" {
  type = string
}

variable "firebase_project_id" {
  type = string
}

module "platform" {
  source              = "../../modules/platform"
  project_id          = var.project_id
  environment         = "production"
  network_prefix      = "10.30"
  domain              = var.domain
  ops_cidrs           = var.ops_cidrs
  github_repository   = var.github_repository
  alert_email         = var.alert_email
  high_availability   = true
  database_tier       = "db-custom-4-15360"
  redis_memory_gb     = 5
  deletion_protection = true
  real_providers      = true
  firebase_project_id = var.firebase_project_id
}

output "kubernetes_environment" {
  value = module.platform.kubernetes_environment
}

output "ringsays_config" {
  value = module.platform.ringsays_config
}

output "portal_config" {
  value = module.platform.portal_config
}

output "backoffice_portal_config" {
  value = module.platform.backoffice_portal_config
}

output "image_repository" {
  value = module.platform.image_repository
}

output "evidence_bucket" {
  value = module.platform.evidence_bucket
}

output "cluster" {
  value = module.platform.cluster
}

output "egress_addresses" {
  value = module.platform.egress_addresses
}

output "ci" {
  value = module.platform.ci
}

output "external_secrets_service_account" {
  value = module.platform.external_secrets_service_account
}
