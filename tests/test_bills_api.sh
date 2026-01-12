#!/bin/bash
# Test script for Bills API endpoints
# Run after restarting API Gateway: docker-compose restart api_gateway

set -e

BASE_URL="http://localhost:8001"
EMAIL="grapmanado@gmail.com"
PASSWORD="Jalanatputno.4"

echo "================================================"
echo "Bills API Test Suite"
echo "================================================"

# Login
echo ""
echo "=== LOGIN ==="
RESPONSE=$(curl -s -X POST "${BASE_URL}/api/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"${EMAIL}\",\"password\":\"${PASSWORD}\"}")

TOKEN=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['access_token'])")
echo "Login successful, token obtained"

# Test 1: Health Check
echo ""
echo "=== TEST 1: Health Check ==="
curl -s "${BASE_URL}/api/bills/health" -H "Authorization: Bearer $TOKEN"
echo ""

# Test 2: List Bills (empty)
echo ""
echo "=== TEST 2: List Bills (should be empty) ==="
curl -s "${BASE_URL}/api/bills" -H "Authorization: Bearer $TOKEN" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Total: {d.get(\"total\", 0)}, Items: {len(d.get(\"items\", []))}')"

# Test 3: Get Summary
echo ""
echo "=== TEST 3: Get Summary ==="
curl -s "${BASE_URL}/api/bills/summary?period=current_month" -H "Authorization: Bearer $TOKEN" | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin), indent=2))"

# Test 4: Create Bill
echo ""
echo "=== TEST 4: Create Bill ==="
cat > /tmp/create_bill.json << 'EOF'
{
  "vendor_name": "PT Supplier Test",
  "due_date": "2026-02-10",
  "notes": "Test bill from API",
  "items": [
    {
      "description": "Barang A",
      "quantity": 10,
      "unit": "pcs",
      "unit_price": 50000
    },
    {
      "description": "Barang B",
      "quantity": 5,
      "unit": "pcs",
      "unit_price": 100000
    }
  ]
}
EOF

BILL_RESPONSE=$(curl -s -X POST "${BASE_URL}/api/bills" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d @/tmp/create_bill.json)

echo "$BILL_RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Success: {d.get(\"success\")}, Message: {d.get(\"message\")}')"

BILL_ID=$(echo "$BILL_RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('id',''))" 2>/dev/null)
echo "Bill ID: $BILL_ID"

if [ -z "$BILL_ID" ] || [ "$BILL_ID" = "None" ]; then
  echo "❌ Failed to create bill, stopping tests"
  exit 1
fi

# Test 5: Get Bill Detail
echo ""
echo "=== TEST 5: Get Bill Detail ==="
curl -s "${BASE_URL}/api/bills/${BILL_ID}" -H "Authorization: Bearer $TOKEN" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Invoice: {d.get(\"data\",{}).get(\"invoice_number\")}, Amount: Rp {d.get(\"data\",{}).get(\"amount\",0):,}')"

# Test 6: List Bills (should have 1)
echo ""
echo "=== TEST 6: List Bills (should have 1) ==="
curl -s "${BASE_URL}/api/bills" -H "Authorization: Bearer $TOKEN" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Total: {d.get(\"total\", 0)}')"

# Test 7: Record Partial Payment
echo ""
echo "=== TEST 7: Record Partial Payment ==="
# First, get a valid account_id from CoA
ACCOUNT_ID=$(curl -s "${BASE_URL}/api/reports/chart-of-accounts" -H "Authorization: Bearer $TOKEN" 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    accounts = d.get('data', d) if isinstance(d, dict) else d
    for acc in (accounts if isinstance(accounts, list) else []):
        if 'kas' in str(acc.get('nama_akun', '')).lower() or 'cash' in str(acc.get('nama_akun', '')).lower():
            print(acc.get('id'))
            break
    else:
        print('00000000-0000-0000-0000-000000000001')
except:
    print('00000000-0000-0000-0000-000000000001')
")

echo "Using account_id: $ACCOUNT_ID"

cat > /tmp/payment.json << EOF
{
  "amount": 200000,
  "payment_method": "transfer",
  "account_id": "${ACCOUNT_ID}",
  "reference": "TRF-001",
  "notes": "Partial payment test"
}
EOF

PAYMENT_RESPONSE=$(curl -s -X POST "${BASE_URL}/api/bills/${BILL_ID}/payments" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d @/tmp/payment.json)

echo "$PAYMENT_RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Success: {d.get(\"success\")}, Message: {d.get(\"message\")}')"

# Test 8: Check Status (should be partial)
echo ""
echo "=== TEST 8: Check Status After Payment ==="
curl -s "${BASE_URL}/api/bills/${BILL_ID}" -H "Authorization: Bearer $TOKEN" | python3 -c "import sys,json; d=json.load(sys.stdin); data=d.get('data',{}); print(f'Status: {data.get(\"status\")}, Paid: Rp {data.get(\"amount_paid\",0):,}, Due: Rp {data.get(\"amount_due\",0):,}')"

# Test 9: Mark as Paid
echo ""
echo "=== TEST 9: Mark as Paid (Full Remaining) ==="
cat > /tmp/mark_paid.json << EOF
{
  "payment_method": "transfer",
  "account_id": "${ACCOUNT_ID}",
  "reference": "TRF-002",
  "notes": "Final payment"
}
EOF

PAID_RESPONSE=$(curl -s -X PATCH "${BASE_URL}/api/bills/${BILL_ID}/mark-paid" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d @/tmp/mark_paid.json)

echo "$PAID_RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Success: {d.get(\"success\")}, Message: {d.get(\"message\")}')"

# Test 10: Check Final Status (should be paid)
echo ""
echo "=== TEST 10: Check Final Status ==="
curl -s "${BASE_URL}/api/bills/${BILL_ID}" -H "Authorization: Bearer $TOKEN" | python3 -c "import sys,json; d=json.load(sys.stdin); data=d.get('data',{}); print(f'Status: {data.get(\"status\")}, Paid: Rp {data.get(\"amount_paid\",0):,}')"

# Test 11: Create Another Bill for Void Test
echo ""
echo "=== TEST 11: Create Bill for Void Test ==="
cat > /tmp/create_bill2.json << 'EOF'
{
  "vendor_name": "PT Void Test",
  "due_date": "2026-02-10",
  "items": [
    {
      "description": "Item to void",
      "quantity": 1,
      "unit": "pcs",
      "unit_price": 100000
    }
  ]
}
EOF

BILL2_RESPONSE=$(curl -s -X POST "${BASE_URL}/api/bills" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d @/tmp/create_bill2.json)

BILL2_ID=$(echo "$BILL2_RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('id',''))" 2>/dev/null)
echo "Created Bill2 ID: $BILL2_ID"

# Test 12: Void Bill
echo ""
echo "=== TEST 12: Void Bill ==="
VOID_RESPONSE=$(curl -s -X POST "${BASE_URL}/api/bills/${BILL2_ID}/void" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"reason":"Test void functionality"}')

echo "$VOID_RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Success: {d.get(\"success\")}, Message: {d.get(\"message\")}')"

# Test 13: Final Summary
echo ""
echo "=== TEST 13: Final Summary ==="
curl -s "${BASE_URL}/api/bills/summary" -H "Authorization: Bearer $TOKEN" | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin), indent=2))"

echo ""
echo "================================================"
echo "All tests completed!"
echo "================================================"
