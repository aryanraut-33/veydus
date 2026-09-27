# ==============================================================================
# FILE: infra/terraform/tasks.tf
# WHAT: Terraform configuration for Google Cloud Tasks asynchronous ingestion queue.
# HOW: Declares google_cloud_tasks_queue with rate limiting and exponential backoff retry.
# WHY: Decouples ingestion document parsing/chunking from API requests; prevents worker OOM.
# TOOLS/LIBRARIES: Terraform Google Provider (google_cloud_tasks_queue).
# ==============================================================================

resource "google_cloud_tasks_queue" "ingest_tasks" {
  name     = "veydus-ingest-tasks"
  location = var.region
  project  = var.project_id

  rate_limits {
    max_concurrent_dispatches = 5
    max_dispatches_per_second = 2.0
  }

  retry_config {
    max_attempts       = 5
    min_backoff        = "10s"
    max_backoff        = "300s"
    max_doublings      = 4
    max_retry_duration = "1800s" # 30 minutes max retry window
  }

  depends_on = [google_project_service.enabled_services]
}
