# ==============================================================================
# FILE: infra/terraform/outputs.tf
# WHAT: Terraform output variables exposing critical deployment endpoints and resource IDs.
# HOW: Exposes Cloud Run URLs, VPC private VM internal IP, GCS bucket names, and IAP SSH command.
# WHY: Allows operators/CI to capture connection endpoints and verify deployment status easily.
# TOOLS/LIBRARIES: Terraform HCL.
# ==============================================================================

output "api_service_url" {
  description = "Public URL of the Veydus API Cloud Run service"
  value       = google_cloud_run_v2_service.api.uri
}

output "worker_service_uri" {
  description = "Internal URI of the Veydus Ingestion Worker Cloud Run service"
  value       = google_cloud_run_v2_service.worker.uri
}

output "postgres_vm_internal_ip" {
  description = "Internal VPC IP of the PostgreSQL 16 pgvector VM (Zero public IP)"
  value       = google_compute_instance.postgres_vm.network_interface[0].network_ip
}

output "staging_bucket_name" {
  description = "GCS Staging Bucket name for encrypted presigned document uploads"
  value       = google_storage_bucket.staging.name
}

output "backup_bucket_name" {
  description = "GCS Backup Bucket name for automated PostgreSQL pg_dump backups"
  value       = google_storage_bucket.backups.name
}

output "tasks_queue_id" {
  description = "Cloud Tasks queue ID for async document ingestion dispatch"
  value       = google_cloud_tasks_queue.ingest_tasks.id
}

output "iap_ssh_command" {
  description = "GCloud command to securely SSH into the private PostgreSQL VM via IAP tunnel"
  value       = "gcloud compute ssh ${google_compute_instance.postgres_vm.name} --zone=${var.zone} --project=${var.project_id} --tunnel-through-iap"
}
