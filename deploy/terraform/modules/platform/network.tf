resource "google_compute_network" "this" {
  project                 = var.project_id
  name                    = local.name
  auto_create_subnetworks = false
  routing_mode            = "REGIONAL"
  depends_on              = [google_project_service.apis]
}

resource "google_compute_subnetwork" "nodes" {
  project                  = var.project_id
  name                     = "${local.name}-nodes"
  region                   = var.region
  network                  = google_compute_network.this.id
  ip_cidr_range            = local.ranges.nodes
  private_ip_google_access = true
  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = local.ranges.pods
  }
  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = local.ranges.services
  }
  log_config {
    aggregation_interval = "INTERVAL_5_SEC"
    flow_sampling        = 0.5
    metadata             = "INCLUDE_ALL_METADATA"
  }
}

# Proxy only subnet used by the regional external and internal Application Load Balancers.
resource "google_compute_subnetwork" "proxy" {
  project       = var.project_id
  name          = "${local.name}-lb-proxy"
  region        = var.region
  network       = google_compute_network.this.id
  ip_cidr_range = local.ranges.proxy
  purpose       = "REGIONAL_MANAGED_PROXY"
  role          = "ACTIVE"
}

# Private services access: Cloud SQL and Memorystore get addresses inside these ranges, never public.
resource "google_compute_global_address" "sql" {
  project       = var.project_id
  name          = "${local.name}-sql"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  address       = split("/", local.ranges.sql)[0]
  prefix_length = tonumber(split("/", local.ranges.sql)[1])
  network       = google_compute_network.this.id
}

resource "google_compute_global_address" "redis" {
  project       = var.project_id
  name          = "${local.name}-redis"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  address       = split("/", local.ranges.redis)[0]
  prefix_length = tonumber(split("/", local.ranges.redis)[1])
  network       = google_compute_network.this.id
}

resource "google_service_networking_connection" "private_services" {
  network                 = google_compute_network.this.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.sql.name, google_compute_global_address.redis.name]
}

# Outbound internet (organisations' webhooks, identity provider keys, SMS and push providers) leaves
# through fixed addresses, so organisations can allow list RingSays.
resource "google_compute_address" "nat" {
  count        = 2
  project      = var.project_id
  name         = "${local.name}-nat-${count.index}"
  region       = var.region
  address_type = "EXTERNAL"
  network_tier = "PREMIUM"
}

resource "google_compute_router" "this" {
  project = var.project_id
  name    = local.name
  region  = var.region
  network = google_compute_network.this.id
}

resource "google_compute_router_nat" "this" {
  project                            = var.project_id
  name                               = local.name
  router                             = google_compute_router.this.name
  region                             = var.region
  nat_ip_allocate_option             = "MANUAL_ONLY"
  nat_ips                            = google_compute_address.nat[*].self_link
  source_subnetwork_ip_ranges_to_nat = "LIST_OF_SUBNETWORKS"
  subnetwork {
    name                    = google_compute_subnetwork.nodes.id
    source_ip_ranges_to_nat = ["ALL_IP_RANGES"]
  }
  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}

# Google APIs over the private access addresses (199.36.153.8/30), so network policies can name them.
resource "google_dns_managed_zone" "googleapis" {
  project    = var.project_id
  name       = "${local.name}-googleapis"
  dns_name   = "googleapis.com."
  visibility = "private"
  private_visibility_config {
    networks { network_url = google_compute_network.this.id }
  }
}

resource "google_dns_record_set" "private_googleapis" {
  project      = var.project_id
  managed_zone = google_dns_managed_zone.googleapis.name
  name         = "private.googleapis.com."
  type         = "A"
  ttl          = 300
  rrdatas      = ["199.36.153.8", "199.36.153.9", "199.36.153.10", "199.36.153.11"]
}

resource "google_dns_record_set" "googleapis_wildcard" {
  project      = var.project_id
  managed_zone = google_dns_managed_zone.googleapis.name
  name         = "*.googleapis.com."
  type         = "CNAME"
  ttl          = 300
  rrdatas      = ["private.googleapis.com."]
}

resource "google_compute_route" "private_googleapis" {
  project          = var.project_id
  name             = "${local.name}-private-googleapis"
  network          = google_compute_network.this.id
  dest_range       = "199.36.153.8/30"
  next_hop_gateway = "default-internet-gateway"
  priority         = 900
}

# Firewall: nothing in from the internet. Load balancer proxies and Google health checkers may reach
# the nodes; everything else is denied by the implied rule.
resource "google_compute_firewall" "lb_to_nodes" {
  project       = var.project_id
  name          = "${local.name}-lb-to-nodes"
  network       = google_compute_network.this.id
  direction     = "INGRESS"
  priority      = 1000
  source_ranges = [local.ranges.proxy, "35.191.0.0/16", "130.211.0.0/22"]
  allow {
    protocol = "tcp"
    ports    = ["8000", "8080"]
  }
  log_config { metadata = "INCLUDE_ALL_METADATA" }
}
