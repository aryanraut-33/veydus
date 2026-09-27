#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
# VEYDUS — Compute Engine Startup Script: PostgreSQL 16 + pgvector
# ─────────────────────────────────────────────────────────────────
# What:  Automated provisioning script executed by systemd on first boot of
#        the private e2-micro database VM instance.
# How:   - Adds the official PostgreSQL PGDG Debian repository.
#        - Installs PostgreSQL 16 and postgresql-16-pgvector.
#        - Tunes memory limits for 1GB RAM (e2-micro Always Free shape).
#        - Configures pg_hba.conf to accept connections from VPC subnet 10.0.0.0/16.
#        - Creates database "veydus", enables "vector" extension, and provisions
#          roles "veydus_migrate" (BYPASSRLS) and "veydus_app" (NOBYPASSRLS).
#        - Configures daily pg_dump cron job streaming backups to Google Cloud Storage.
# Why:   HLD §5 & §16 mandate: Provides self-hosted PostgreSQL 16 + pgvector
#        operating 100% within the GCP Always Free Tier with zero external IP exposure.
# Tools: Bash, apt, PostgreSQL 16, pgvector, gsutil/gcloud.
# ─────────────────────────────────────────────────────────────────
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

echo "[VEYDUS INIT] Starting PostgreSQL 16 + pgvector provisioning..."

# 1. Update and install repository prerequisites
apt-get update -y
apt-get install -y curl ca-certificates gnupg lsb-release cron

# 2. Add PostgreSQL official PGDG repository
install -d /etc/apt/keyrings
curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor -o /etc/apt/keyrings/postgresql.gpg
echo "deb [signed-by=/etc/apt/keyrings/postgresql.gpg] http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list

apt-get update -y

# 3. Install PostgreSQL 16 and pgvector
apt-get install -y postgresql-16 postgresql-16-pgvector

# 4. Tune PostgreSQL configuration for 1GB RAM (e2-micro)
PG_CONF="/etc/postgresql/16/main/postgresql.conf"
PG_HBA="/etc/postgresql/16/main/pg_hba.conf"

sed -i "s/#listen_addresses = 'localhost'/listen_addresses = '*'/g" "$PG_CONF"
sed -i "s/shared_buffers = 128MB/shared_buffers = 256MB/g" "$PG_CONF"
sed -i "s/#work_mem = 4MB/work_mem = 16MB/g" "$PG_CONF"
sed -i "s/#maintenance_work_mem = 64MB/maintenance_work_mem = 64MB/g" "$PG_CONF"

# 5. Configure pg_hba.conf for VPC subnet access
echo "# VEYDUS VPC internal subnet access" >> "$PG_HBA"
echo "host all all 10.0.0.0/16 md5" >> "$PG_HBA"

systemctl restart postgresql

# 6. Initialize database, vector extension, and security roles
echo "[VEYDUS INIT] Creating database and security roles..."

sudo -u postgres psql <<EOSQL
-- Create database
CREATE DATABASE veydus;
\c veydus

-- Enable pgvector and pgcrypto
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Create table owner role (veydus_migrate) with BYPASSRLS
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'veydus_migrate') THEN
    CREATE ROLE veydus_migrate WITH LOGIN PASSWORD '${DB_PASSWORD_MIGRATE}' BYPASSRLS;
  ELSE
    ALTER ROLE veydus_migrate WITH PASSWORD '${DB_PASSWORD_MIGRATE}';
  END IF;
END
\$\$;

-- Create application runtime role (veydus_app) subject to RLS
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'veydus_app') THEN
    CREATE ROLE veydus_app WITH LOGIN PASSWORD '${DB_PASSWORD_APP}' NOBYPASSRLS;
  ELSE
    ALTER ROLE veydus_app WITH PASSWORD '${DB_PASSWORD_APP}';
  END IF;
END
\$\$;

-- Grant database & schema privileges
GRANT ALL PRIVILEGES ON DATABASE veydus TO veydus_migrate;
GRANT ALL ON SCHEMA public TO veydus_migrate;
GRANT USAGE ON SCHEMA public TO veydus_app;
ALTER DEFAULT PRIVILEGES FOR ROLE veydus_migrate IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO veydus_app;
ALTER DEFAULT PRIVILEGES FOR ROLE veydus_migrate IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO veydus_app;

EOSQL

# 7. Configure daily pg_dump backup to GCS bucket
echo "[VEYDUS INIT] Configuring backup cron job..."

cat << 'EOF' > /usr/local/bin/veydus-backup.sh
#!/usr/bin/env bash
set -eo pipefail
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="/tmp/veydus_backup_$${TIMESTAMP}.sql.gz"

echo "Creating compressed pg_dump..."
PGPASSWORD="${DB_PASSWORD_MIGRATE}" pg_dump -U veydus_migrate -h localhost -d veydus | gzip > "$BACKUP_FILE"

echo "Streaming backup to GCS..."
gcloud storage cp "$BACKUP_FILE" "gs://${BACKUP_BUCKET}/backups/veydus_$${TIMESTAMP}.sql.gz"

rm -f "$BACKUP_FILE"
echo "Backup completed successfully."
EOF

chmod +x /usr/local/bin/veydus-backup.sh

# Run nightly at 02:00 UTC
(crontab -l 2>/dev/null || true; echo "0 2 * * * /usr/local/bin/veydus-backup.sh >> /var/log/veydus-backup.log 2>&1") | crontab -

echo "[VEYDUS INIT] PostgreSQL 16 + pgvector provisioning complete!"
