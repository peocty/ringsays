# GKE Autopilot, regional (three zones): Google runs the nodes, hardened defaults, Dataplane V2 enforces
# network policies, workload identity on. Nodes have no public IP. The control plane is reached only
# from the operations networks or through the IAM checked DNS endpoint (CI, operators).
# Nodes run as a dedicated account with only logging, monitoring and image pull rights (never the
# default compute account, which is often an Editor).
resource "google_service_account" "nodes" {
  project      = var.project_id
  account_id   = "ringsays-nodes"
  display_name = "RingSays GKE nodes (${var.environment})"
}

resource "google_project_iam_member" "nodes" {
  for_each = toset([
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/monitoring.viewer",
    "roles/stackdriver.resourceMetadata.writer",
    "roles/autoscaling.metricsWriter",
  ])
  project = var.project_id
  role    = each.value
  member  = google_service_account.nodes.member
}

resource "google_container_cluster" "this" {
  project             = var.project_id
  name                = local.name
  location            = var.region
  enable_autopilot    = true
  network             = google_compute_network.this.id
  subnetwork          = google_compute_subnetwork.nodes.id
  deletion_protection = var.deletion_protection
  resource_labels     = local.labels

  release_channel {
    channel = "REGULAR"
  }

  cluster_autoscaling {
    auto_provisioning_defaults {
      service_account = google_service_account.nodes.email
      oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
    }
  }

  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }

  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = true
  }

  master_authorized_networks_config {
    dynamic "cidr_blocks" {
      for_each = var.ops_cidrs
      content {
        cidr_block   = cidr_blocks.value
        display_name = "operations"
      }
    }
  }

  control_plane_endpoints_config {
    dns_endpoint_config {
      allow_external_traffic = true
    }
  }

  # Kubernetes secrets encrypted at application layer with our key.
  database_encryption {
    state    = "ENCRYPTED"
    key_name = google_kms_crypto_key.keys["kubernetes"].id
  }

  gateway_api_config {
    channel = "CHANNEL_STANDARD"
  }

  binary_authorization {
    evaluation_mode = "PROJECT_SINGLETON_POLICY_ENFORCE"
  }

  security_posture_config {
    mode               = "BASIC"
    vulnerability_mode = "VULNERABILITY_BASIC"
  }

  # Kingdom weekend: Friday early morning (Riyadh), outside banking hours.
  maintenance_policy {
    recurring_window {
      start_time = "2026-01-02T00:00:00Z"
      end_time   = "2026-01-02T04:00:00Z"
      recurrence = "FREQ=WEEKLY;BYDAY=FR"
    }
  }

  logging_config {
    enable_components = ["SYSTEM_COMPONENTS", "WORKLOADS"]
  }

  depends_on = [google_project_service.apis, google_kms_crypto_key_iam_member.agents]
}

# Only images built by RingSays CI and stored in this project's registry may run.
resource "google_binary_authorization_policy" "this" {
  project = var.project_id
  admission_whitelist_patterns {
    name_pattern = "${var.region}-docker.pkg.dev/${var.project_id}/*"
  }
  default_admission_rule {
    evaluation_mode  = "ALWAYS_DENY"
    enforcement_mode = "ENFORCED_BLOCK_AND_AUDIT_LOG"
  }
  global_policy_evaluation_mode = "ENABLE"
}
