# Regional secrets in me-central2, encrypted with our key. Values generated here are kept in the
# OpenTofu state, so the state bucket is itself encrypted, versioned and limited to the platform team
# (see bootstrap/). Rotation: change the random resource (taint), apply, rerun the bootstrap job.

resource "random_password" "db" {
  for_each = toset(["owner", "app", "worker", "backoffice"])
  length   = 40
  special  = false
}

resource "random_password" "jwt" {
  length  = 64
  special = false
}

resource "random_password" "pepper" {
  length  = 48
  special = false
}

resource "random_password" "nats" {
  length  = 40
  special = false
}

# Fernet key: 32 random bytes, URL safe base64.
resource "random_bytes" "webhook_key" {
  length = 32
}

locals {
  sql_ip    = google_sql_database_instance.this.private_ip_address
  pg_tls    = "sslmode=verify-ca&sslrootcert=/etc/ringsays/tls/postgres-ca.pem"
  pg_url    = { for r in ["owner", "app", "worker", "backoffice"] : r => "postgresql+psycopg://ringsays_${r}:${random_password.db[r].result}@${local.sql_ip}:5432/ringsays?${local.pg_tls}" }
  redis_url = "rediss://:${google_redis_instance.this.auth_string}@${google_redis_instance.this.host}:${google_redis_instance.this.port}/0?ssl_cert_reqs=required&ssl_ca_certs=/etc/ringsays/tls/redis-ca.pem"

  secret_values = {
    "ringsays-db-url-owner"           = local.pg_url["owner"]
    "ringsays-db-url-app"             = local.pg_url["app"]
    "ringsays-db-url-worker"          = local.pg_url["worker"]
    "ringsays-db-url-backoffice"      = local.pg_url["backoffice"]
    "ringsays-db-url-admin"           = "postgresql+psycopg://ringsays_admin:${random_password.db_admin.result}@${local.sql_ip}:5432/postgres?${local.pg_tls}"
    "ringsays-db-password-owner"      = random_password.db["owner"].result
    "ringsays-db-password-app"        = random_password.db["app"].result
    "ringsays-db-password-worker"     = random_password.db["worker"].result
    "ringsays-db-password-backoffice" = random_password.db["backoffice"].result
    "ringsays-redis-url"              = local.redis_url
    "ringsays-nats-password"          = random_password.nats.result
    "ringsays-nats-url"               = "nats://ringsays:${random_password.nats.result}@ringsays-nats:4222"
    "ringsays-jwt-secret"             = random_password.jwt.result
    "ringsays-webhook-secret-key"     = replace(replace(random_bytes.webhook_key.base64, "+", "-"), "/", "_")
    "ringsays-phone-pepper"           = random_password.pepper.result
    "ringsays-postgres-server-ca"     = google_sql_database_instance.this.server_ca_cert[0].cert
    "ringsays-redis-server-ca"        = google_redis_instance.this.server_ca_certs[0].cert
  }
}

resource "google_secret_manager_regional_secret" "this" {
  for_each  = nonsensitive(toset(keys(local.secret_values)))
  project   = var.project_id
  location  = var.region
  secret_id = each.value
  labels    = local.labels
  customer_managed_encryption {
    kms_key_name = google_kms_crypto_key.keys["secrets"].id
  }
  depends_on = [google_kms_crypto_key_iam_member.agents]
}

resource "google_secret_manager_regional_secret_version" "this" {
  for_each    = nonsensitive(toset(keys(local.secret_values)))
  secret      = google_secret_manager_regional_secret.this[each.value].id
  secret_data = local.secret_values[each.value]
}

resource "google_secret_manager_regional_secret_iam_member" "external_secrets" {
  for_each  = nonsensitive(toset(keys(local.secret_values)))
  project   = var.project_id
  location  = var.region
  secret_id = google_secret_manager_regional_secret.this[each.value].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = google_service_account.external_secrets.member
}
