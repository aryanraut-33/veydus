# ─────────────────────────────────────────────────────────────────
# VEYDUS — Terraform Versions & Provider Requirements
# ─────────────────────────────────────────────────────────────────
# What:  Specifies the minimum Terraform version and required cloud providers
#        for deploying VEYDUS infrastructure to Google Cloud Platform.
# How:   Pins terraform >= 1.5.0, google provider >= 5.30.0, and random provider.
# Why:   HLD §16 mandate: Consistent, deterministic infrastructure provisioning
#        preventing drift or version mismatches across deployment environments.
# Tools: Terraform, Google Cloud Provider, Random Provider.
# ─────────────────────────────────────────────────────────────────

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.30.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.6.0"
    }
  }
}
