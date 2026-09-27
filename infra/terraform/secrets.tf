# ─────────────────────────────────────────────────────────────────
# VEYDUS — Terraform Secret Manager: Database URLs & Keys [SECURITY-CRITICAL]
# ─────────────────────────────────────────────────────────────────
# What:  Provisions Google Secret Manager secrets and active versions for
#        database connection strings, fallback JWT secrets, and AI API keys.
# How:   - Dynamically constructs the async and sync database connection URLs
#          pointing to the private IP of the database VM.
#        - Automatically rotates versions upon Terraform apply.
#        - Restricts read access via least-privilege IAM bindings in iam.tf.
# Why:   HLD §16 & Sprint A5 Success Check 7: Zero database passwords or API keys
#        are committed to source control or exposed in environment manifests.
# Tools: Terraform, Google Secret Manager.
# ─────────────────────────────────────────────────────────────────

# ── Runtime Database Connection String (veydus_app role, RLS) ───────
resource "google_secret_manager_secret" "app_db_url" {
  secret_id = "veydus-app-db-url"

  replication {
    auto {}
  }
  depends_on = [google_project_service.enabled_services]
}

resource "google_secret_manager_secret_version" "app_db_url_version" {
  secret      = google_secret_manager_secret.app_db_url.id
  secret_data = "postgresql+asyncpg://veydus_app:${var.db_password_app}@${google_compute_instance.postgres_vm.network_interface[0].network_ip}:5432/veydus"
}

# ── Migration Database Connection String (veydus_migrate role, BYPASSRLS) ──
resource "google_secret_manager_secret" "migrate_db_url" {
  secret_id = "veydus-migrate-db-url"

  replication {
    auto {}
  }
  depends_on = [google_project_service.enabled_services]
}

resource "google_secret_manager_secret_version" "migrate_db_url_version" {
  secret      = google_secret_manager_secret.migrate_db_url.id
  secret_data = "postgresql+psycopg2://veydus_migrate:${var.db_password_migrate}@${google_compute_instance.postgres_vm.network_interface[0].network_ip}:5432/veydus"
}

# ── JWT Fallback Secret Key ─────────────────────────────────────────
resource "google_secret_manager_secret" "jwt_secret_key" {
  secret_id = "veydus-jwt-secret-key"

  replication {
    auto {}
  }
  depends_on = [google_project_service.enabled_services]
}

resource "google_secret_manager_secret_version" "jwt_secret_key_version" {
  secret      = google_secret_manager_secret.jwt_secret_key.id
  secret_data = var.jwt_secret_key
}

# ── NVIDIA NIM API Key ──────────────────────────────────────────────
resource "google_secret_manager_secret" "nvidia_api_key" {
  secret_id = "veydus-nvidia-api-key"

  replication {
    auto {}
  }
  depends_on = [google_project_service.enabled_services]
}

resource "google_secret_manager_secret_version" "nvidia_api_key_version" {
  secret      = google_secret_manager_secret.nvidia_api_key.id
  secret_data = var.nvidia_api_key
}
