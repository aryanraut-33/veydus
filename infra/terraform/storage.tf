# ─────────────────────────────────────────────────────────────────
# VEYDUS — Terraform Cloud Storage: Staging & Backups [SECURITY-CRITICAL]
# ─────────────────────────────────────────────────────────────────
# What:  Provisions Google Cloud Storage buckets for temporary document ingestion
#        staging and database disaster-recovery backups.
# How:   - veydus-staging-${project_id}: Uniform bucket-level access, public access
#          prevention enforced, strict 1-day auto-deletion lifecycle rule.
#        - veydus-backups-${project_id}: Stores encrypted pg_dump archives with
#          a 30-day retention lifecycle rule.
# Why:   HLD §7 & Sprint A5 Success Check 5: Staging files are ephemeral. The 1-day
#        lifecycle delete rule guarantees defense-in-depth against lingering PII
#        or confidential documents in object storage.
# Tools: Terraform, Google Cloud Storage.
# ─────────────────────────────────────────────────────────────────

# Ephemeral Document Staging Bucket
resource "google_storage_bucket" "staging" {
  name                        = "veydus-staging-${var.project_id}"
  location                    = var.region
  force_destroy               = false
  uniform_bucket_level_access = true

  public_access_prevention = "enforced"

  # [SECURITY INVARIANT HLD §7 & §16]: 1-day auto-delete lifecycle backstop
  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      age = 1 # Automatically deleted after 1 day
    }
  }

  cors {
    origin          = ["*"]
    method          = ["GET", "PUT", "POST"]
    response_header = ["*"]
    max_age_seconds = 3600
  }
}

# Nightly Database Backup Bucket
resource "google_storage_bucket" "backups" {
  name                        = "veydus-backups-${var.project_id}"
  location                    = var.region
  force_destroy               = false
  uniform_bucket_level_access = true

  public_access_prevention = "enforced"

  # Retain daily backups for 30 days
  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      age = 30
    }
  }
}
