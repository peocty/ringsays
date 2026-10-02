# PostgreSQL 16, private IP only, TLS required, customer managed key, point in time recovery.
resource "google_sql_database_instance" "this" {
  project             = var.project_id
  name                = local.name
  region              = var.region
  database_version    = "POSTGRES_16"
  encryption_key_name = google_kms_crypto_key.keys["database"].id
  deletion_protection = var.deletion_protection

  settings {
    tier              = var.database_tier
    edition           = "ENTERPRISE"
    availability_type = var.high_availability ? "REGIONAL" : "ZONAL"
    disk_type         = "PD_SSD"
    disk_autoresize   = true
    user_labels       = local.labels

    ip_configuration {
      ipv4_enabled                                  = false
      private_network                               = google_compute_network.this.id
      allocated_ip_range                            = google_compute_global_address.sql.name
      ssl_mode                                      = "ENCRYPTED_ONLY"
      enable_private_path_for_google_cloud_services = false
    }

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      start_time                     = "23:00" # 02:00 Riyadh
      location                       = var.region
      transaction_log_retention_days = 7
      backup_retention_settings {
        retained_backups = local.prod ? 35 : 7
      }
    }

    maintenance_window {
      day          = 5 # Friday
      hour         = 0 # 03:00 Riyadh
      update_track = "stable"
    }

    insights_config {
      query_insights_enabled  = true
      record_application_tags = false
      record_client_address   = false
    }

    # No statement text in logs: with bound parameters it would carry customer values. Query
    # Insights (without client addresses) covers performance.
    database_flags {
      name  = "log_min_duration_statement"
      value = "-1"
    }
    database_flags {
      name  = "log_checkpoints"
      value = "on"
    }
    database_flags {
      name  = "log_min_error_statement"
      value = "error"
    }
    # Audit trail of schema and role changes (who granted what), never of data reads.
    database_flags {
      name  = "cloudsql.enable_pgaudit"
      value = "on"
    }
    database_flags {
      name  = "pgaudit.log"
      value = "ddl,role"
    }
    database_flags {
      name  = "log_connections"
      value = "on"
    }
    database_flags {
      name  = "log_disconnections"
      value = "on"
    }
    database_flags {
      name  = "log_lock_waits"
      value = "on"
    }
    database_flags {
      name  = "password_encryption"
      value = "scram-sha-256"
    }
  }

  depends_on = [google_service_networking_connection.private_services, google_kms_crypto_key_iam_member.agents]
}

# The service's admin (cloudsqlsuperuser: CREATEROLE, CREATEDB, not superuser). Used only by the
# bootstrap job; RingSays roles are created by app.scripts.bootstrap_db, never as built in users
# (built in users would be cloudsqlsuperuser members).
resource "random_password" "db_admin" {
  length  = 40
  special = false
}

resource "google_sql_user" "admin" {
  project  = var.project_id
  instance = google_sql_database_instance.this.name
  name     = "ringsays_admin"
  password = random_password.db_admin.result
}

# Memorystore for Redis: rate limits and revocation lists (no customer data), TLS and AUTH required.
resource "google_redis_instance" "this" {
  project                 = var.project_id
  name                    = local.name
  region                  = var.region
  tier                    = var.high_availability ? "STANDARD_HA" : "BASIC"
  memory_size_gb          = var.redis_memory_gb
  redis_version           = "REDIS_7_2"
  authorized_network      = google_compute_network.this.id
  connect_mode            = "PRIVATE_SERVICE_ACCESS"
  reserved_ip_range       = google_compute_global_address.redis.name
  auth_enabled            = true
  transit_encryption_mode = "SERVER_AUTHENTICATION"
  customer_managed_key    = google_kms_crypto_key.keys["redis"].id
  labels                  = local.labels
  redis_configs = {
    maxmemory-policy = "volatile-ttl"
  }
  maintenance_policy {
    weekly_maintenance_window {
      day = "FRIDAY"
      start_time {
        hours   = 0
        minutes = 0
      }
    }
  }
  depends_on = [google_service_networking_connection.private_services, google_kms_crypto_key_iam_member.agents]
}

# Verification evidence. Private, uniform access, customer managed key, soft delete for recovery.
resource "google_storage_bucket" "evidence" {
  project                     = var.project_id
  name                        = "${var.project_id}-evidence"
  location                    = upper(var.region)
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = !var.deletion_protection
  labels                      = local.labels
  encryption {
    default_kms_key_name = google_kms_crypto_key.keys["storage"].id
  }
  versioning {
    enabled = true
  }
  soft_delete_policy {
    retention_duration_seconds = 30 * 86400
  }
  lifecycle_rule {
    condition {
      days_since_noncurrent_time = 90
      with_state                 = "ARCHIVED"
    }
    action {
      type = "Delete"
    }
  }
  logging {
    log_bucket = google_storage_bucket.access_logs.name
  }
  depends_on = [google_kms_crypto_key_iam_member.agents]
}

resource "google_storage_bucket" "access_logs" {
  project                     = var.project_id
  name                        = "${var.project_id}-access-logs"
  location                    = upper(var.region)
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = !var.deletion_protection
  labels                      = local.labels
  encryption {
    default_kms_key_name = google_kms_crypto_key.keys["storage"].id
  }
  versioning {
    enabled = true
  }
  lifecycle_rule {
    condition {
      age = 400
    }
    action {
      type = "Delete"
    }
  }
  depends_on = [google_kms_crypto_key_iam_member.agents]
}
