resource "google_monitoring_notification_channel" "ops" {
  project      = var.project_id
  display_name = "RingSays operations (${var.environment})"
  type         = "email"
  labels       = { email_address = var.alert_email }
}

# /health carries no data; checkers run from Google's probe locations.
resource "google_monitoring_uptime_check_config" "api" {
  project      = var.project_id
  display_name = "RingSays API ${var.environment}"
  timeout      = "10s"
  period       = "60s"
  http_check {
    path         = "/health"
    port         = 443
    use_ssl      = true
    validate_ssl = true
  }
  monitored_resource {
    type   = "uptime_url"
    labels = { project_id = var.project_id, host = "api.${var.domain}" }
  }
}

resource "google_monitoring_alert_policy" "api_down" {
  project      = var.project_id
  display_name = "RingSays API unreachable (${var.environment})"
  combiner     = "OR"
  conditions {
    display_name = "Uptime check failing"
    condition_threshold {
      filter          = "metric.type=\"monitoring.googleapis.com/uptime_check/check_passed\" AND resource.type=\"uptime_url\" AND metric.label.check_id=\"${google_monitoring_uptime_check_config.api.uptime_check_id}\""
      comparison      = "COMPARISON_GT"
      threshold_value = 1
      duration        = "120s"
      aggregations {
        alignment_period     = "60s"
        per_series_aligner   = "ALIGN_NEXT_OLDER"
        cross_series_reducer = "REDUCE_COUNT_FALSE"
        group_by_fields      = ["resource.label.host"]
      }
    }
  }
  notification_channels = [google_monitoring_notification_channel.ops.id]
}

resource "google_monitoring_alert_policy" "database_cpu" {
  project      = var.project_id
  display_name = "RingSays database CPU high (${var.environment})"
  combiner     = "OR"
  conditions {
    display_name = "Cloud SQL CPU above 80% for 10 minutes"
    condition_threshold {
      filter          = "metric.type=\"cloudsql.googleapis.com/database/cpu/utilization\" AND resource.type=\"cloudsql_database\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0.8
      duration        = "600s"
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_MEAN"
      }
    }
  }
  notification_channels = [google_monitoring_notification_channel.ops.id]
}
