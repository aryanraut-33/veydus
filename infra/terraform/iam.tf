# ─────────────────────────────────────────────────────────────────
# VEYDUS — Terraform IAM & Least Privilege Service Accounts [SECURITY-CRITICAL]
# ─────────────────────────────────────────────────────────────────
# What:  Provisions four dedicated, least-privilege Google Cloud service accounts
#        enforcing component isolation per HLD §4.4.
# How:   - veydus-api-sa: Operates the public API; reads runtime secrets and
#          enqueues ingestion tasks into Cloud Tasks.
#        - veydus-worker-sa: Operates the private worker; reads runtime secrets,
#          downloads and deletes temporary staged documents in GCS.
#        - veydus-tasks-sa: Used exclusively by Cloud Tasks to sign OIDC tokens;
#          granted roles/run.invoker on the worker service only.
#        - veydus-jobs-sa: Runs Alembic migrations and administrative scripts.
# Why:   HLD §4.4 mandate: Principle of least privilege prevents lateral movement
#        and ensures compromised microservices cannot access migration credentials.
# Tools: Terraform, Google Cloud IAM, Service Accounts.
# ─────────────────────────────────────────────────────────────────

# ── 1. API Service Account ──────────────────────────────────────────
resource "google_service_account" "api_sa" {
  account_id   = "veydus-api-sa"
  display_name = "VEYDUS Public API Service Account"
}

resource "google_secret_manager_secret_iam_member" "api_app_db_url" {
  secret_id = google_secret_manager_secret.app_db_url.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.api_sa.email}"
}

resource "google_secret_manager_secret_iam_member" "api_jwt_secret" {
  secret_id = google_secret_manager_secret.jwt_secret_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.api_sa.email}"
}

resource "google_secret_manager_secret_iam_member" "api_nvidia_key" {
  secret_id = google_secret_manager_secret.nvidia_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.api_sa.email}"
}

resource "google_project_iam_member" "api_tasks_enqueuer" {
  project = var.project_id
  role    = "roles/cloudtasks.enqueuer"
  member  = "serviceAccount:${google_service_account.api_sa.email}"
}

# ── 2. Ingestion Worker Service Account ──────────────────────────────
resource "google_service_account" "worker_sa" {
  account_id   = "veydus-worker-sa"
  display_name = "VEYDUS Ingestion Worker Service Account"
}

resource "google_secret_manager_secret_iam_member" "worker_app_db_url" {
  secret_id = google_secret_manager_secret.app_db_url.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.worker_sa.email}"
}

resource "google_secret_manager_secret_iam_member" "worker_nvidia_key" {
  secret_id = google_secret_manager_secret.nvidia_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.worker_sa.email}"
}

# Worker reads and deletes objects in the staging bucket
resource "google_storage_bucket_iam_member" "worker_staging_admin" {
  bucket = google_storage_bucket.staging.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.worker_sa.email}"
}

# ── 3. Cloud Tasks Invoker Service Account (OIDC Token Signer) ─────
resource "google_service_account" "tasks_sa" {
  account_id   = "veydus-tasks-sa"
  display_name = "VEYDUS Cloud Tasks OIDC Invoker Service Account"
}

# ── 4. Cloud Run Jobs Service Account (Migrations & Provisioning) ──
resource "google_service_account" "jobs_sa" {
  account_id   = "veydus-jobs-sa"
  display_name = "VEYDUS Migration & Jobs Service Account"
}

resource "google_secret_manager_secret_iam_member" "jobs_migrate_db_url" {
  secret_id = google_secret_manager_secret.migrate_db_url.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.jobs_sa.email}"
}

resource "google_secret_manager_secret_iam_member" "jobs_app_db_url" {
  secret_id = google_secret_manager_secret.app_db_url.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.jobs_sa.email}"
}
