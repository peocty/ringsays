variable "project_id" {
  description = "Google Cloud project for this environment (one project per environment)."
  type        = string
}

variable "environment" {
  description = "staging or production."
  type        = string
  validation {
    condition     = contains(["staging", "production"], var.environment)
    error_message = "environment must be staging or production."
  }
}

variable "region" {
  description = "Kingdom region. Dammam is the Google Cloud region in Saudi Arabia."
  type        = string
  default     = "me-central2"
  validation {
    condition     = var.region == "me-central2"
    error_message = "RingSays customer data stays in the Kingdom: region must be me-central2."
  }
}

variable "network_prefix" {
  description = "First two octets of the environment's private ranges, for example 10.20 (staging) or 10.30 (production)."
  type        = string
}

variable "ops_cidrs" {
  description = "Operations networks (VPN, Interconnect) allowed to the cluster control plane and the internal load balancer."
  type        = list(string)
}

variable "domain" {
  description = "Public domain, for example ringsays.sa (api. and portal. below it)."
  type        = string
}

variable "github_repository" {
  description = "owner/repo whose GitHub Actions may push images (workload identity federation, no keys)."
  type        = string
}

variable "alert_email" {
  description = "Operations mailbox for alerts."
  type        = string
}

variable "high_availability" {
  description = "Regional (multi zone) database and Redis. On for production."
  type        = bool
  default     = true
}

variable "database_tier" {
  description = "Cloud SQL machine tier."
  type        = string
  default     = "db-custom-2-7680"
}

variable "redis_memory_gb" {
  description = "Memorystore size."
  type        = number
  default     = 2
}

variable "deletion_protection" {
  description = "Protect database, cluster and buckets from deletion. Off only for disposable test projects."
  type        = bool
  default     = true
}

variable "real_providers" {
  description = "Real SMS and push providers (production). Creates the empty provider secrets and the Firebase permission."
  type        = bool
  default     = false
}

variable "firebase_project_id" {
  description = "Firebase project that owns the RingSays app registrations (Android and iOS). Needed when real_providers is true."
  type        = string
  default     = ""
  validation {
    condition     = !var.real_providers || var.firebase_project_id != ""
    error_message = "firebase_project_id is required when real_providers is true."
  }
}
