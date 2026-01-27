#!/bin/bash
# API Test Script for Milkyhoop Backend
# Usage: source scripts/api-test.sh

API_URL="${API_URL:-http://localhost:8001}"

# Login and get token
login() {
    local email="${1:-grapmanado@gmail.com}"
    local password="${2:-Jalanatputno.4}"

    echo "Logging in as $email..."

    RESPONSE=$(curl -s -X POST "$API_URL/api/auth/login" \
        -H "Content-Type: application/json" \
        -d "{\"email\": \"$email\", \"password\": \"$password\"}")

    # Extract token using jq or grep
    if command -v jq &> /dev/null; then
        TOKEN=$(echo "$RESPONSE" | jq -r '.data.access_token')
        TENANT_ID=$(echo "$RESPONSE" | jq -r '.data.tenant_id')
        USER_ID=$(echo "$RESPONSE" | jq -r '.data.user_id')
    else
        TOKEN=$(echo "$RESPONSE" | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4)
        TENANT_ID=$(echo "$RESPONSE" | grep -o '"tenant_id":"[^"]*"' | cut -d'"' -f4)
        USER_ID=$(echo "$RESPONSE" | grep -o '"user_id":"[^"]*"' | cut -d'"' -f4)
    fi

    if [ "$TOKEN" != "null" ] && [ -n "$TOKEN" ]; then
        export TOKEN
        export TENANT_ID
        export USER_ID
        echo "Login successful!"
        echo "TENANT_ID: $TENANT_ID"
        echo "USER_ID: $USER_ID"
        echo "TOKEN exported to \$TOKEN"
    else
        echo "Login failed:"
        echo "$RESPONSE"
        return 1
    fi
}

# Helper function for authenticated requests
api() {
    local method="${1:-GET}"
    local endpoint="$2"
    local data="$3"

    if [ -z "$TOKEN" ]; then
        echo "Error: Not logged in. Run 'login' first."
        return 1
    fi

    if [ -n "$data" ]; then
        curl -s -X "$method" "$API_URL$endpoint" \
            -H "Authorization: Bearer $TOKEN" \
            -H "Content-Type: application/json" \
            -d "$data" | jq . 2>/dev/null || cat
    else
        curl -s -X "$method" "$API_URL$endpoint" \
            -H "Authorization: Bearer $TOKEN" | jq . 2>/dev/null || cat
    fi
}

# Shorthand functions
get() { api GET "$1"; }
post() { api POST "$1" "$2"; }
patch() { api PATCH "$1" "$2"; }
delete() { api DELETE "$1"; }

# Quick test functions
test_bills() {
    echo "=== Testing Bills API ==="
    echo "1. List bills:"
    get "/api/bills?limit=5"
}

test_create_draft_bill() {
    echo "=== Creating Draft Bill ==="
    post "/api/bills/v2" '{
        "vendor_name": "Test Vendor",
        "due_date": "2026-02-18",
        "status": "draft",
        "tax_rate": 11,
        "items": [
            {
                "product_name": "Test Product",
                "qty": 10,
                "price": 50000,
                "unit": "pcs"
            }
        ]
    }'
}

test_vendors() {
    echo "=== Testing Vendors API ==="
    get "/api/vendors?limit=5"
}

# Print usage
usage() {
    echo "API Test Script"
    echo ""
    echo "Commands:"
    echo "  login [email] [password]  - Login and get token"
    echo "  api METHOD /endpoint [data] - Make authenticated request"
    echo "  get /endpoint             - GET request"
    echo "  post /endpoint '{json}'   - POST request"
    echo "  patch /endpoint '{json}'  - PATCH request"
    echo "  delete /endpoint          - DELETE request"
    echo ""
    echo "Quick tests:"
    echo "  test_bills                - List bills"
    echo "  test_create_draft_bill    - Create a draft bill"
    echo "  test_vendors              - List vendors"
    echo ""
    echo "Example:"
    echo "  source scripts/api-test.sh"
    echo "  login"
    echo "  get /api/bills?limit=5"
    echo "  test_create_draft_bill"
}

echo "API Test Script loaded. Run 'usage' for help, 'login' to start."
