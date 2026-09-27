# ─────────────────────────────────────────────────────────────────
# VEYDUS — Terraform VPC Network & Security Firewalls [SECURITY-CRITICAL]
# ─────────────────────────────────────────────────────────────────
# What:  Provisions a custom isolated Virtual Private Cloud (VPC), private subnet,
#        Cloud NAT gateway, and restrictive ingress firewall rules.
# How:   - Custom VPC with auto_create_subnetworks = false.
#        - Private subnet (10.0.1.0/24) with private_ip_google_access = true.
#        - Cloud NAT: Enables outbound-only internet for VM packages/updates
#          without assigning any external IP to the database VM.
#        - Firewall Rule (allow-internal-postgres): Restricts port 5432 to internal
#          VPC traffic only (10.0.0.0/16).
#        - Firewall Rule (allow-ssh-iap): Restricts port 22 to Google Identity-Aware
#          Proxy (IAP) range (35.235.240.0/20) for secure bastion-less SSH.
# Why:   HLD §16 & Sprint A5 Success Check 3: Guarantees the database VM has
#        ZERO public IP exposure and cannot be reached from the public internet.
# Tools: Terraform, Google Compute Network, Subnetwork, Router, Router NAT, Firewall.
# ─────────────────────────────────────────────────────────────────

resource "google_compute_network" "vpc" {
  name                    = "veydus-vpc"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.enabled_services]
}

resource "google_compute_subnetwork" "private_subnet" {
  name                     = "veydus-private-subnet"
  ip_cidr_range            = "10.0.1.0/24"
  region                   = var.region
  network                  = google_compute_network.vpc.id
  private_ip_google_access = true
}

# ── Cloud Router & NAT Gateway (Outbound Only for VM) ─────────────

resource "google_compute_router" "router" {
  name    = "veydus-router"
  region  = var.region
  network = google_compute_network.vpc.id
}

resource "google_compute_router_nat" "nat" {
  name                               = "veydus-nat"
  router                             = google_compute_router.router.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"

  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}

# ── Ingress Firewall Rules ────────────────────────────────────────

# Allow internal PostgreSQL traffic from Cloud Run (via Direct VPC Egress)
resource "google_compute_firewall" "allow_internal_postgres" {
  name    = "veydus-allow-internal-postgres"
  network = google_compute_network.vpc.name

  allow {
    protocol = "tcp"
    ports    = ["5432"]
  }

  source_ranges = ["10.0.0.0/16"]
  target_tags   = ["postgres-vm"]
}

# Allow secure administrative SSH exclusively through Google Identity-Aware Proxy (IAP)
resource "google_compute_firewall" "allow_ssh_iap" {
  name    = "veydus-allow-ssh-iap"
  network = google_compute_network.vpc.name

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  source_ranges = ["35.235.240.0/20"]
  target_tags   = ["postgres-vm"]
}
