# ─────────────────────────────────────────────────────────────────
# VEYDUS — Terraform Main Provider & API Enablement
# ─────────────────────────────────────────────────────────────────
# What:  Initializes the Google Cloud Provider and enables all required
#        GCP service APIs for serverless RAG operations.
# How:   - Configures provider "google" with project and region bindings.
#        - Enables compute, Cloud Run, Artifact Registry, Cloud Tasks,
#          Cloud Scheduler, Secret Manager, Identity Platform, and IAP APIs.
#        - Uses disable_on_destroy = false to prevent unintended service disruption.
# Why:   HLD §16: Centralizes API enablement to ensure required cloud services
#        are active prior to resource creation.
# Tools: Terraform, Google Cloud Platform APIs.
# ─────────────────────────────────────────────────────────────────

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  services = [
    "compute.googleapis.com",              # Compute Engine for private database VM
    "run.googleapis.com",                  # Cloud Run serverless container runtime
    "artifactregistry.googleapis.com",     # Container image registry
    "cloudtasks.googleapis.com",           # Asynchronous job queue for document ingestion
    "cloudscheduler.googleapis.com",       # Cron scheduler for warm pings
    "secretmanager.googleapis.com",        # Database and application secret storage
    "identitytoolkit.googleapis.com",      # GCP Identity Platform (Firebase Auth)
    "vpcaccess.googleapis.com",            # Serverless VPC networking
    "iap.googleapis.com",                  # Identity-Aware Proxy for secure VM access
  ]
}

resource "google_project_service" "enabled_services" {
  for_each           = toset(local.services)
  project            = var.project_id
  service            = each.key
  disable_on_destroy = false
}
