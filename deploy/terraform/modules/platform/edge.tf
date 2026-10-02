# Internal load balancer address for the back office Gateway (fixed, so it can be trusted as a proxy).
resource "google_compute_address" "internal" {
  project      = var.project_id
  name         = "${local.name}-internal"
  region       = var.region
  subnetwork   = google_compute_subnetwork.nodes.id
  address_type = "INTERNAL"
  purpose      = "SHARED_LOADBALANCER_VIP"
}

# Public edge: one regional address for the regional external Application Load Balancer (Gateway),
# and a regional Cloud Armor policy (global load balancing is not available under the KSA data boundary).
resource "google_compute_address" "public" {
  project      = var.project_id
  name         = "${local.name}-public"
  region       = var.region
  address_type = "EXTERNAL"
  network_tier = "PREMIUM"
}

resource "google_compute_region_security_policy" "edge" {
  project     = var.project_id
  name        = "${local.name}-edge"
  region      = var.region
  type        = "CLOUD_ARMOR"
  description = "RingSays public edge: OWASP rules, sign in code throttling"
}

resource "google_compute_region_security_policy_rule" "waf" {
  for_each = {
    1000 = "evaluatePreconfiguredWaf('sqli-v33-stable', {'sensitivity': 1})"
    1001 = "evaluatePreconfiguredWaf('xss-v33-stable', {'sensitivity': 1})"
    1002 = "evaluatePreconfiguredWaf('lfi-v33-stable', {'sensitivity': 1})"
    1003 = "evaluatePreconfiguredWaf('rce-v33-stable', {'sensitivity': 1})"
    1004 = "evaluatePreconfiguredWaf('protocolattack-v33-stable', {'sensitivity': 1})"
  }
  project         = var.project_id
  region          = var.region
  security_policy = google_compute_region_security_policy.edge.name
  priority        = tonumber(each.key)
  action          = "deny(403)"
  match {
    expr {
      expression = each.value
    }
  }
}

# Sign in codes: coarse per address brake at the edge. Mobile carriers in the Kingdom put many
# subscribers behind one address (carrier grade NAT), so this stays high; the application's per phone
# limits do the real work. Tune with production traffic.
resource "google_compute_region_security_policy_rule" "otp_throttle" {
  project         = var.project_id
  region          = var.region
  security_policy = google_compute_region_security_policy.edge.name
  priority        = 1100 # after the OWASP rules: an allowed request must still be inspected first
  action          = "throttle"
  match {
    expr {
      expression = "request.path.startsWith('/v1/auth/otp')"
    }
  }
  rate_limit_options {
    conform_action = "allow"
    exceed_action  = "deny(429)"
    enforce_on_key = "IP"
    rate_limit_threshold {
      count        = 300
      interval_sec = 600
    }
  }
}

resource "google_compute_region_security_policy_rule" "default" {
  project         = var.project_id
  region          = var.region
  security_policy = google_compute_region_security_policy.edge.name
  priority        = 2147483647
  action          = "allow"
  match {
    versioned_expr = "SRC_IPS_V1"
    config {
      src_ip_ranges = ["*"]
    }
  }
}
