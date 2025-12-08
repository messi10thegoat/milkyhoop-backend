#!/bin/bash
# ===========================================
# TLS Certificate Generation Script
# For production-grade security
# ===========================================

set -e

# Configuration
CERT_DIR="${CERT_DIR:-/etc/ssl/milkyhoop}"
DOMAIN="${DOMAIN:-milkyhoop.com}"
ORG="${ORG:-MilkyHoop}"
COUNTRY="${COUNTRY:-ID}"
STATE="${STATE:-Jakarta}"
LOCALITY="${LOCALITY:-Jakarta}"
DAYS="${DAYS:-365}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}===========================================
TLS Certificate Generator for MilkyHoop
===========================================${NC}"

# Create directory
mkdir -p "$CERT_DIR"
cd "$CERT_DIR"

# ===========================================
# 1. Generate CA (Certificate Authority)
# ===========================================
echo -e "\n${YELLOW}[1/4] Generating CA certificate...${NC}"

if [ ! -f ca.key ]; then
    # Generate CA private key
    openssl genrsa -out ca.key 4096

    # Generate CA certificate
    openssl req -new -x509 -days 3650 -key ca.key -out ca.crt \
        -subj "/C=$COUNTRY/ST=$STATE/L=$LOCALITY/O=$ORG/CN=$ORG Root CA"

    echo -e "${GREEN}  CA certificate created: ca.crt${NC}"
else
    echo -e "${YELLOW}  CA already exists, skipping...${NC}"
fi

# ===========================================
# 2. Generate Server Certificate (for HTTPS)
# ===========================================
echo -e "\n${YELLOW}[2/4] Generating server certificate for HTTPS...${NC}"

# Create server config
cat > server.cnf << EOF
[req]
default_bits = 2048
prompt = no
default_md = sha256
req_extensions = req_ext
distinguished_name = dn

[dn]
C = $COUNTRY
ST = $STATE
L = $LOCALITY
O = $ORG
CN = $DOMAIN

[req_ext]
subjectAltName = @alt_names

[alt_names]
DNS.1 = $DOMAIN
DNS.2 = www.$DOMAIN
DNS.3 = api.$DOMAIN
DNS.4 = dev.$DOMAIN
DNS.5 = localhost
IP.1 = 127.0.0.1
EOF

# Generate server private key
openssl genrsa -out server.key 2048

# Generate CSR
openssl req -new -key server.key -out server.csr -config server.cnf

# Sign with CA
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
    -out server.crt -days $DAYS -extensions req_ext -extfile server.cnf

echo -e "${GREEN}  Server certificate created: server.crt${NC}"

# ===========================================
# 3. Generate gRPC Certificates
# ===========================================
echo -e "\n${YELLOW}[3/4] Generating gRPC certificates...${NC}"

# Create gRPC config
cat > grpc.cnf << EOF
[req]
default_bits = 2048
prompt = no
default_md = sha256
req_extensions = req_ext
distinguished_name = dn

[dn]
C = $COUNTRY
ST = $STATE
L = $LOCALITY
O = $ORG
CN = grpc.$DOMAIN

[req_ext]
subjectAltName = @alt_names

[alt_names]
DNS.1 = grpc.$DOMAIN
DNS.2 = auth_service
DNS.3 = api_gateway
DNS.4 = inventory_service
DNS.5 = transaction_service
DNS.6 = tenant_orchestrator
DNS.7 = localhost
IP.1 = 127.0.0.1
EOF

# Generate gRPC key and cert
openssl genrsa -out grpc.key 2048
openssl req -new -key grpc.key -out grpc.csr -config grpc.cnf
openssl x509 -req -in grpc.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
    -out grpc.crt -days $DAYS -extensions req_ext -extfile grpc.cnf

echo -e "${GREEN}  gRPC certificate created: grpc.crt${NC}"

# ===========================================
# 4. Generate Database SSL Certificate
# ===========================================
echo -e "\n${YELLOW}[4/4] Generating database SSL certificate...${NC}"

# Create DB config
cat > db.cnf << EOF
[req]
default_bits = 2048
prompt = no
default_md = sha256
distinguished_name = dn

[dn]
C = $COUNTRY
ST = $STATE
L = $LOCALITY
O = $ORG
CN = postgres.$DOMAIN
EOF

# Generate DB key and cert
openssl genrsa -out db.key 2048
openssl req -new -key db.key -out db.csr -config db.cnf
openssl x509 -req -in db.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
    -out db.crt -days $DAYS

# Set permissions
chmod 600 *.key
chmod 644 *.crt

echo -e "${GREEN}  Database certificate created: db.crt${NC}"

# ===========================================
# Cleanup and Summary
# ===========================================
rm -f *.csr *.cnf *.srl

echo -e "\n${GREEN}===========================================
Certificates generated successfully!
===========================================${NC}"
echo -e "
Location: $CERT_DIR

Files created:
  ${GREEN}ca.crt${NC}      - CA certificate (share with clients)
  ${GREEN}ca.key${NC}      - CA private key (KEEP SECRET!)
  ${GREEN}server.crt${NC}  - HTTPS server certificate
  ${GREEN}server.key${NC}  - HTTPS server private key
  ${GREEN}grpc.crt${NC}    - gRPC server certificate
  ${GREEN}grpc.key${NC}    - gRPC server private key
  ${GREEN}db.crt${NC}      - Database SSL certificate
  ${GREEN}db.key${NC}      - Database SSL private key

Usage:
  1. For HTTPS (nginx):
     ssl_certificate $CERT_DIR/server.crt;
     ssl_certificate_key $CERT_DIR/server.key;

  2. For gRPC (in code):
     GRPC_TLS_ENABLED=true
     GRPC_TLS_CERT_PATH=$CERT_DIR/grpc.crt
     GRPC_TLS_KEY_PATH=$CERT_DIR/grpc.key

  3. For PostgreSQL:
     ssl_cert_file = '$CERT_DIR/db.crt'
     ssl_key_file = '$CERT_DIR/db.key'
     ssl_ca_file = '$CERT_DIR/ca.crt'

${YELLOW}IMPORTANT: For production, use Let's Encrypt for HTTPS!${NC}
  certbot certonly --webroot -w /var/www/certbot -d $DOMAIN
"
