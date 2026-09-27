# ─────────────────────────────────────────────────────────────────
# VEYDUS — Terraform Compute Engine VM: PostgreSQL 16 + pgvector
# ─────────────────────────────────────────────────────────────────
# What:  Provisions an e2-micro Compute Engine instance running PostgreSQL 16
#        and pgvector, operating within the GCP Always Free Tier.
# How:   - Instance type e2-micro (1 vCPU, 1 GB RAM, Always Free in us-central1).
#        - 30 GB standard persistent disk (pd-standard, Always Free).
#        - Attaches to veydus-private-subnet with ZERO external IP address
#          (access_config block intentionally omitted).
#        - Executes vm-startup.sh on initial boot to install packages, configure
#          roles, and schedule backups.
#        - Dedicated service account with least-privilege access to the backup bucket.
# Why:   HLD §16: Complete PostgreSQL 16 + pgvector vector retrieval capabilities
#        with zero monthly Cloud SQL managed database costs.
# Tools: Terraform, Google Compute Instance, IAM, Cloud Storage.
# ─────────────────────────────────────────────────────────────────

# Dedicated service account for the database VM (limited to backup uploads)
resource "google_service_account" "vm_sa" {
  account_id   = "veydus-vm-sa"
  display_name = "VEYDUS Database VM Service Account"
}

resource "google_storage_bucket_iam_member" "vm_backup_writer" {
  bucket = google_storage_bucket.backups.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.vm_sa.email}"
}

# The Private Database Virtual Machine
resource "google_compute_instance" "postgres_vm" {
  name         = "veydus-postgres-vm"
  machine_type = "e2-micro"
  zone         = var.zone

  tags = ["postgres-vm"]

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = 30
      type  = "pd-standard"
    }
  }

  network_interface {
    subnetwork = google_compute_subnetwork.private_subnet.id
    # [SECURITY-CRITICAL] access_config is intentionally omitted.
    # The VM has NO external IP address and cannot receive traffic from the public internet.
  }

  service_account {
    email  = google_service_account.vm_sa.email
    scopes = ["cloud-platform"]
  }

  metadata_startup_script = templatefile("${path.module}/scripts/vm-startup.sh", {
    DB_PASSWORD_MIGRATE = var.db_password_migrate
    DB_PASSWORD_APP     = var.db_password_app
    BACKUP_BUCKET       = google_storage_bucket.backups.name
  })

  lifecycle {
    ignore_changes = [metadata_startup_script]
  }

  depends_on = [
    google_compute_router_nat.nat,
    google_storage_bucket.backups,
  ]
}
