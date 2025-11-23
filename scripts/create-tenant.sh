#!/bin/bash

echo "═══════════════════════════════════════════════════════════════"
echo "🧪 TEST: Create Tenant Full Flow Verification"
echo "═══════════════════════════════════════════════════════════════"
echo ""

TENANT_USERNAME="testshop$(date +%s)"
TENANT_EMAIL="test${TENANT_USERNAME}@example.com"
TENANT_PASSWORD="TestPass123!@#"
TENANT_DISPLAY="Test Shop $(date +%H:%M)"

echo "--- 1. Create Tenant via CLI ---"
cd ~/milkyhoop-dev/scripts
./create-tenant.sh \
  --username="${TENANT_USERNAME}" \
  --email="${TENANT_EMAIL}" \
  --password="${TENANT_PASSWORD}" \
  --display-name="${TENANT_DISPLAY}"

echo ""
echo "--- 2. Verify Database Entry ---"
PGPASSWORD=Proyek771977 psql -h db.ltrqrejrkbusvmknpnwb.supabase.co -U postgres -d postgres -c "
SELECT id, display_name, status FROM \"Tenant\" WHERE id='${TENANT_USERNAME}';
"

echo ""
echo "--- 3. Verify User Entry ---"
PGPASSWORD=Proyek771977 psql -h db.ltrqrejrkbusvmknpnwb.supabase.co -U postgres -d postgres -c "
SELECT email, username, \"tenantId\", role FROM \"User\" WHERE email='${TENANT_EMAIL}';
"

echo ""
echo "--- 4. Test API Endpoint ---"
curl -s https://dev.milkyhoop.com/api/tenant/${TENANT_USERNAME}/info | python3 -m json.tool

echo ""
echo "--- 5. Test Login ---"
TOKEN=$(curl -s -X POST https://dev.milkyhoop.com/api/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"email\": \"${TENANT_EMAIL}\", \"password\": \"${TENANT_PASSWORD}\"}" \
  | python3 -c "import sys, json; print(json.load(sys.stdin)['data']['access_token'])" 2>/dev/null)

if [ -z "$TOKEN" ]; then
  echo "❌ Login failed!"
else
  echo "✅ Login successful, token obtained"
fi

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "✅ TEST COMPLETE"
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "Manual verification URLs:"
echo "  Landing: https://dev.milkyhoop.com/${TENANT_USERNAME}"
echo "  Chat:    https://dev.milkyhoop.com/${TENANT_USERNAME}/chat"
echo ""
echo "Credentials:"
echo "  Email:    ${TENANT_EMAIL}"
echo "  Password: ${TENANT_PASSWORD}"
echo ""
