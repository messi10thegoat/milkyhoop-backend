#!/bin/bash
# ==============================================
# MilkyHoop Secret Management Helper
# Uses SOPS + age for encryption
# ==============================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$PROJECT_DIR/.env"
ENCRYPTED_FILE="$PROJECT_DIR/.env.encrypted"
AGE_KEY_FILE="/root/.config/sops/age/keys.txt"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

usage() {
    echo "Usage: $0 {encrypt|decrypt|edit|show-public-key}"
    echo ""
    echo "Commands:"
    echo "  encrypt          Encrypt .env to .env.encrypted"
    echo "  decrypt          Decrypt .env.encrypted to .env"
    echo "  edit             Edit encrypted secrets in-place"
    echo "  show-public-key  Show the age public key"
    exit 1
}

check_deps() {
    if ! command -v sops &> /dev/null; then
        echo -e "${RED}Error: sops not installed${NC}"
        exit 1
    fi
    if ! command -v age &> /dev/null; then
        echo -e "${RED}Error: age not installed${NC}"
        exit 1
    fi
}

encrypt() {
    check_deps
    if [ ! -f "$ENV_FILE" ]; then
        echo -e "${RED}Error: $ENV_FILE not found${NC}"
        exit 1
    fi

    echo -e "${YELLOW}Encrypting .env...${NC}"
    sops --encrypt --input-type dotenv --output-type dotenv "$ENV_FILE" > "$ENCRYPTED_FILE"
    echo -e "${GREEN}Encrypted to .env.encrypted${NC}"
    echo -e "${YELLOW}Consider removing .env from git and adding .env.encrypted${NC}"
}

decrypt() {
    check_deps
    if [ ! -f "$ENCRYPTED_FILE" ]; then
        echo -e "${RED}Error: $ENCRYPTED_FILE not found${NC}"
        exit 1
    fi

    echo -e "${YELLOW}Decrypting .env.encrypted...${NC}"
    sops --decrypt --input-type dotenv --output-type dotenv "$ENCRYPTED_FILE" > "$ENV_FILE"
    echo -e "${GREEN}Decrypted to .env${NC}"
}

edit() {
    check_deps
    if [ ! -f "$ENCRYPTED_FILE" ]; then
        echo -e "${RED}Error: $ENCRYPTED_FILE not found${NC}"
        exit 1
    fi

    echo -e "${YELLOW}Opening encrypted secrets for editing...${NC}"
    EDITOR="${EDITOR:-nano}" sops --input-type dotenv --output-type dotenv "$ENCRYPTED_FILE"
}

show_public_key() {
    if [ ! -f "$AGE_KEY_FILE" ]; then
        echo -e "${RED}Error: age key not found at $AGE_KEY_FILE${NC}"
        exit 1
    fi

    echo -e "${GREEN}Age Public Key:${NC}"
    grep "public key:" "$AGE_KEY_FILE" | awk '{print $NF}'
}

case "${1:-}" in
    encrypt)
        encrypt
        ;;
    decrypt)
        decrypt
        ;;
    edit)
        edit
        ;;
    show-public-key)
        show_public_key
        ;;
    *)
        usage
        ;;
esac
