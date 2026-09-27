# ─────────────────────────────────────────────────────────────────
# VEYDUS — Terraform Cloud Run Services & Jobs [SECURITY-CRITICAL]
# ─────────────────────────────────────────────────────────────────
# What:  Provisions serverless Cloud Run v2 services (veydus-api, veydus-worker)
#        and Cloud Run Jobs (database migrations, organization provisioning).
# How:   - veydus-api: Public ingress, auto-scaling 0..5, Direct VPC egress
#          to reach the private database VM without public exposure.
#        - veydus-worker: INTERNAL_ONLY ingress. Invocation is strictly restricted
#          to the veydus-tasks-sa service account via Google OIDC tokens (HLD §4.4).
#        - Direct VPC Egress: Routes container egress directly through the VPC
#          subnet, eliminating legacy Serverless VPC Access connector fees.
#        - Cloud Run Jobs: Executes Alembic schema upgrades and organization
#          provisioning directly inside the private database subnet.
# Why:   HLD §16 & Sprint A5 Success Checks 1, 3, 4: Complete containerized RAG
#        pipeline running on GCP serverless with zero monthly idle costs.
# Tools: Terraform, Google Cloud Run v2 (Service, Job, IAM).
# ─────────────────────────────────────────────────────────────────

# ── 1. Public API Service ───────────────────────────────────────────
resource "google_cloud_run_v2_service" "api" {
  name                = "veydus-api"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL" # Public HTTPS ingress
  deletion_protection = false

  template {
    service_account = google_service_account.api_sa.email

    scaling {
      min_instance_count = 0 # Scale to zero when idle
      max_instance_count = 5
    }

    # Direct VPC Egress into the private database subnet
    vpc_access {
      network_interfaces {
        network    = google_compute_network.vpc.id
        subnetwork = google_compute_subnetwork.private_subnet.id
      }
      egress = "ALL_TRAFFIC"
    }

    containers {
      image = var.api_image

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }

      ports {
        container_port = 8080
      }

      env {
        name  = "AUTH_MODE"
        value = "idp"
      }

      env {
        name  = "GCP_PROJECT_ID"
        value = var.project_id
      }

      env {
        name  = "STAGING_BUCKET_NAME"
        value = google_storage_bucket.staging.name
      }

      env {
        name  = "CLOUD_TASKS_QUEUE_NAME"
        value = google_cloud_tasks_queue.ingest_tasks.name
      }

      env {
        name  = "CLOUD_TASKS_SERVICE_ACCOUNT"
        value = google_service_account.tasks_sa.email
      }

      env {
        name  = "WORKER_SERVICE_URL"
        value = google_cloud_run_v2_service.worker.uri
      }

      env {
        name = "VEYDUS_APP_DB_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.app_db_url.secret_id
            version = "latest"
          }
        }
      }

      env {
        name = "JWT_SECRET_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.jwt_secret_key.secret_id
            version = "latest"
          }
        }
      }

      env {
        name = "NVIDIA_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.nvidia_api_key.secret_id
            version = "latest"
          }
        }
      }
    }
  }

  depends_on = [
    google_compute_instance.postgres_vm,
    google_secret_manager_secret_version.app_db_url_version,
  ]
}

# Allow public invocations for the API service
resource "google_cloud_run_v2_service_iam_member" "api_public" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.api.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# ── 2. Ingestion Worker Service (Internal Only) ──────────────────────
resource "google_cloud_run_v2_service" "worker" {
  name                = "veydus-worker"
  location            = var.region
  deletion_protection = false

  # [SECURITY-CRITICAL HLD §4.4]: Block public internet ingress
  ingress = "INGRESS_TRAFFIC_INTERNAL_ONLY"

  template {
    service_account = google_service_account.worker_sa.email

    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }

    vpc_access {
      network_interfaces {
        network    = google_compute_network.vpc.id
        subnetwork = google_compute_subnetwork.private_subnet.id
      }
      egress = "ALL_TRAFFIC"
    }

    containers {
      image = var.worker_image

      resources {
        limits = {
          cpu    = "2"
          memory = "2Gi"
        }
      }

      ports {
        container_port = 8080
      }

      env {
        name  = "STAGING_BUCKET_NAME"
        value = google_storage_bucket.staging.name
      }

      env {
        name  = "CLOUD_TASKS_SERVICE_ACCOUNT"
        value = google_service_account.tasks_sa.email
      }

      env {
        name = "VEYDUS_APP_DB_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.app_db_url.secret_id
            version = "latest"
          }
        }
      }

      env {
        name = "NVIDIA_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.nvidia_api_key.secret_id
            version = "latest"
          }
        }
      }
    }
  }

  depends_on = [
    google_compute_instance.postgres_vm,
    google_secret_manager_secret_version.app_db_url_version,
  ]
}

# [SECURITY-CRITICAL HLD §4.4 & Success Check 4]:
# Only the Cloud Tasks service account may invoke the worker service
resource "google_cloud_run_v2_service_iam_member" "tasks_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.worker.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.tasks_sa.email}"
}

# ── 3. Database Migration Job (Alembic) ─────────────────────────────
resource "google_cloud_run_v2_job" "migration_job" {
  name                = "veydus-migration-job"
  location            = var.region
  deletion_protection = false

  template {
    template {
      service_account = google_service_account.jobs_sa.email

      vpc_access {
        network_interfaces {
          network    = google_compute_network.vpc.id
          subnetwork = google_compute_subnetwork.private_subnet.id
        }
        egress = "ALL_TRAFFIC"
      }

      containers {
        image   = var.jobs_image
        command = ["alembic"]
        args    = ["upgrade", "head"]

        resources {
          limits = {
            cpu    = "1"
            memory = "512Mi"
          }
        }

        env {
          name = "VEYDUS_MIGRATE_DB_URL"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.migrate_db_url.secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }

  depends_on = [
    google_compute_instance.postgres_vm,
    google_secret_manager_secret_version.migrate_db_url_version,
  ]
}

# ── 4. Organization Provisioning Job ────────────────────────────────
resource "google_cloud_run_v2_job" "provision_org_job" {
  name                = "veydus-provision-org-job"
  location            = var.region
  deletion_protection = false

  template {
    template {
      service_account = google_service_account.jobs_sa.email

      vpc_access {
        network_interfaces {
          network    = google_compute_network.vpc.id
          subnetwork = google_compute_subnetwork.private_subnet.id
        }
        egress = "ALL_TRAFFIC"
      }

      containers {
        image   = var.jobs_image
        command = ["python3"]
        args    = ["scripts/provision_org.py", "--help"]

        resources {
          limits = {
            cpu    = "1"
            memory = "512Mi"
          }
        }

        env {
          name = "VEYDUS_MIGRATE_DB_URL"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.migrate_db_url.secret_id
              version = "latest"
            }
          }
        }

        env {
          name = "VEYDUS_APP_DB_URL"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.app_db_url.secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }

  depends_on = [
    google_compute_instance.postgres_vm,
    google_secret_manager_secret_version.migrate_db_url_version,
  ]
}
