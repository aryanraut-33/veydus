# ==============================================================================
# FILE: infra/terraform/scheduler.tf
# WHAT: Terraform configuration for Google Cloud Scheduler warm ping job.
# HOW: Defines google_cloud_scheduler_job executing GET /health every 10 minutes.
# WHY: Prevents Cloud Run cold starts on free-tier serverless containers (min_instances=0).
# TOOLS/LIBRARIES: Terraform Google Provider (google_cloud_scheduler_job).
# ==============================================================================

resource "google_cloud_scheduler_job" "api_warm_ping" {
  name             = "veydus-api-warm-ping"
  description      = "Pings Veydus API /health every 10 minutes to mitigate cold starts"
  schedule         = "*/10 * * * *"
  time_zone        = "UTC"
  attempt_deadline = "30s"
  region           = var.region
  project          = var.project_id

  http_target {
    http_method = "GET"
    uri         = "${google_cloud_run_v2_service.api.uri}/health"

    headers = {
      "User-Agent" = "Veydus-CloudScheduler/1.0"
    }
  }

  retry_config {
    retry_count = 1
  }

  depends_on = [
    google_project_service.enabled_services,
    google_cloud_run_v2_service.api
  ]
}
