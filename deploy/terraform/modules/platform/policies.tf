# Project guard rails. The KSA data boundary itself (Assured Workloads folder through CNTXT) and the
# organisation's log storage location (me-central2) are set at organisation level before this runs.
locals {
  boolean_policies = {
    "iam.disableServiceAccountKeyCreation"            = true
    "iam.automaticIamGrantsForDefaultServiceAccounts" = true
    "sql.restrictPublicIp"                            = true
    "storage.uniformBucketLevelAccess"                = true
    "compute.requireOsLogin"                          = true
    "compute.skipDefaultNetworkCreation"              = true
    "compute.disableSerialPortAccess"                 = true
  }
}

resource "google_org_policy_policy" "boolean" {
  for_each = local.boolean_policies
  name     = "projects/${var.project_id}/policies/${each.key}"
  parent   = "projects/${var.project_id}"
  spec {
    rules {
      enforce = each.value ? "TRUE" : "FALSE"
    }
  }
}

# Resources may be created in the Kingdom only.
resource "google_org_policy_policy" "locations" {
  name   = "projects/${var.project_id}/policies/gcp.resourceLocations"
  parent = "projects/${var.project_id}"
  spec {
    rules {
      values {
        allowed_values = ["in:me-central2-locations"]
      }
    }
  }
}

resource "google_org_policy_policy" "public_access_prevention" {
  name   = "projects/${var.project_id}/policies/storage.publicAccessPrevention"
  parent = "projects/${var.project_id}"
  spec {
    rules {
      enforce = "TRUE"
    }
  }
}

resource "google_org_policy_policy" "no_vm_external_ip" {
  name   = "projects/${var.project_id}/policies/compute.vmExternalIpAccess"
  parent = "projects/${var.project_id}"
  spec {
    rules {
      deny_all = "TRUE"
    }
  }
}

# Data access audit logs for the services that hold customer data or keys.
resource "google_project_iam_audit_config" "data_access" {
  for_each = toset(["cloudkms.googleapis.com", "secretmanager.googleapis.com", "storage.googleapis.com", "cloudsql.googleapis.com"])
  project  = var.project_id
  service  = each.value
  audit_log_config { log_type = "ADMIN_READ" }
  audit_log_config { log_type = "DATA_READ" }
  audit_log_config { log_type = "DATA_WRITE" }
}
