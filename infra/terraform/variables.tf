# ─────────────────────────────────────────────────────────────────
# VEYDUS — Terraform Input Variables
# ─────────────────────────────────────────────────────────────────
# What:  Declarative input variables defining GCP deployment parameters,
#        regions, container images, and database credentials.
# How:   Specifies project_id, region (default us-central1 for Always Free tier),
#        zone, container image URIs, and sensitive database passwords.
# Why:   HLD §16: Enables parameterization across staging and production environments
#        while securing sensitive passwords via Terraform variable handling.
# Tools: Terraform.
# ─────────────────────────────────────────────────────────────────

variable "project_id" {
  type        = string
  description = "The Google Cloud Platform project ID."
}

variable "region" {
  type        = string
  description = "GCP Region for serverless runtimes and compute (Mumbai, India: asia-south1)."
  default     = "asia-south1"
}

variable "zone" {
  type        = string
  description = "GCP Zone for the database Compute Engine instance (Mumbai, India: asia-south1-a)."
  default     = "asia-south1-a"
}

variable "environment" {
  type        = string
  description = "Deployment environment name (e.g. prod, staging)."
  default     = "prod"
}

# ── Database Passwords (Sensitive) ─────────────────────────────────

variable "db_password_migrate" {
  type        = string
  description = "Password for the veydus_migrate table owner role (BYPASSRLS)."
  sensitive   = true
}

variable "db_password_app" {
  type        = string
  description = "Password for the veydus_app runtime application role (subject to RLS)."
  sensitive   = true
}

# ── Application Secrets (Sensitive) ────────────────────────────────

variable "jwt_secret_key" {
  type        = string
  description = "HMAC secret key used for fallback/development token validation."
  sensitive   = true
  default     = "change_me_in_production_jwt_secret_key_32_bytes_long"
}

variable "nvidia_api_key" {
  type        = string
  description = "NVIDIA NIM API key for remote hosted embedding and generation models."
  sensitive   = true
  default     = ""
}

# ── Container Image URIs ──────────────────────────────────────────

variable "api_image" {
  type        = string
  description = "Container image URI for the veydus-api Cloud Run service."
  default     = "asia-south1-docker.pkg.dev/placeholder/veydus/api:latest"
}

variable "worker_image" {
  type        = string
  description = "Container image URI for the veydus-worker Cloud Run service."
  default     = "asia-south1-docker.pkg.dev/placeholder/veydus/worker:latest"
}

variable "jobs_image" {
  type        = string
  description = "Container image URI for Cloud Run Jobs (Alembic and provisioning)."
  default     = "asia-south1-docker.pkg.dev/placeholder/veydus/jobs:latest"
}
