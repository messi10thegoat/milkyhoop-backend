#!/bin/bash
# ==================================================
# MilkyHoop Secrets Loader
# Decrypt and export secrets from SOPS-encrypted file
# ISO 27001:2022 Compliant (A.8.24 Cryptography)
# ==================================================
#
# Usage:
#   source secrets/load-secrets.sh
#   # Now secrets are available as environment variables
#
# ==================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SECRETS_FILE="$SCRIPT_DIR/production.yaml"
SOPS_AGE_KEY_FILE="${SOPS_AGE_KEY_FILE:-/root/.config/sops/age/keys.txt}"

if [ ! -f "$SECRETS_FILE" ]; then
    echo "ERROR: Secrets file not found: $SECRETS_FILE"
    return 1 2>/dev/null || exit 1
fi

if [ ! -f "$SOPS_AGE_KEY_FILE" ]; then
    echo "ERROR: Age key file not found: $SOPS_AGE_KEY_FILE"
    return 1 2>/dev/null || exit 1
fi

# Decrypt and export secrets
export SOPS_AGE_KEY_FILE

# Database
export POSTGRES_PASSWORD=$(sops -d --extract '["database"]["postgres_password"]' "$SECRETS_FILE")
export DB_PASSWORD=$(sops -d --extract '["database"]["db_password"]' "$SECRETS_FILE")
export DATABASE_URL=$(sops -d --extract '["database"]["database_url"]' "$SECRETS_FILE")

# Redis
export REDIS_PASSWORD=$(sops -d --extract '["redis"]["password"]' "$SECRETS_FILE")

# Auth
export JWT_SECRET=$(sops -d --extract '["auth"]["jwt_secret"]' "$SECRETS_FILE")
export INTERNAL_API_KEY=$(sops -d --extract '["auth"]["internal_api_key"]' "$SECRETS_FILE")
export GRPC_TOKEN=$(sops -d --extract '["auth"]["grpc_token"]' "$SECRETS_FILE")
export SECRET_KEY=$(sops -d --extract '["auth"]["secret_key"]' "$SECRETS_FILE")
export NEXTAUTH_SECRET=$(sops -d --extract '["auth"]["nextauth_secret"]' "$SECRETS_FILE")

# External APIs
export OPENAI_API_KEY=$(sops -d --extract '["external_apis"]["openai_api_key"]' "$SECRETS_FILE")
export GOOGLE_CLIENT_SECRET=$(sops -d --extract '["external_apis"]["google_client_secret"]' "$SECRETS_FILE")
export TURNSTILE_SECRET_KEY=$(sops -d --extract '["external_apis"]["turnstile_secret_key"]' "$SECRETS_FILE")

# Backup
export RESTIC_PASSWORD=$(sops -d --extract '["backup"]["restic_password"]' "$SECRETS_FILE")

echo "Secrets loaded successfully from encrypted store"
