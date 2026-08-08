#!/bin/bash
# UJI ENAM ARAH lapis 1+3 (Law 33). Gateway uji :8002 melayani worktree.
B=http://localhost:8002/api
T=$(curl -s -X POST $B/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"delivered+owner@resend.dev","password":"KaosBiru2026!"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['data']['access_token'])")
H=(-H "Authorization: Bearer $T" -H "Content-Type: application/json")
VEND=57b00d79-c627-4dc6-9014-ee33a08102c4
BANK=c4d3874e-06ab-4c60-a220-69b078d52fbe
BILL=315be101-4841-4ee6-b023-dcbc0a9881a6
Q(){ docker exec milkyhoop-dev-postgres-1 sh -c "PGPASSWORD=Proyek771977 psql -U postgres -d milkydb -tAc \"$1\"" | tr -d '[:space:]'; }
POST(){ curl -s -X POST "$B/bill-payments" "${H[@]}" -d "$1"; }
N(){ Q "SELECT count(*) FROM bill_payments_v2"; }
KEYS(){ Q "SELECT count(*) FROM idempotency_keys"; }
PASS=0; FAIL=0
chk(){ if [ "$2" = "$3" ]; then echo "  PASS  $1 ($2)"; PASS=$((PASS+1)); else echo "  !!! FAIL $1: dapat=$2 harap=$3"; FAIL=$((FAIL+1)); fi; }

P1='{"vendor_id":"'$VEND'","payment_date":"2026-08-06","payment_method":"bank_transfer","bank_account_id":"'$BANK'","total_amount":1000000,"allocations":[{"bill_id":"'$BILL'","amount_applied":1000000}]}'

echo "=== 1. DUA REQUEST IDENTIK -> REPLAY ==="
b=$(N); r1=$(POST "$P1"); r2=$(POST "$P1"); a=$(N)
c1=$(echo "$r1" | python3 -c "import sys,json;print(json.load(sys.stdin).get('was_cached'))" 2>/dev/null)
c2=$(echo "$r2" | python3 -c "import sys,json;print(json.load(sys.stdin).get('was_cached'))" 2>/dev/null)
id1=$(echo "$r1" | python3 -c "import sys,json;print(json.load(sys.stdin)['data']['id'])" 2>/dev/null)
id2=$(echo "$r2" | python3 -c "import sys,json;print(json.load(sys.stdin)['data']['id'])" 2>/dev/null)
chk "hanya 1 transaksi dibuat" "$((a-b))" "1"
chk "req1 was_cached=False"    "$c1" "False"
chk "req2 was_cached=True"     "$c2" "True"
chk "payment_id SAMA (replay)" "$([ "$id1" = "$id2" ] && echo ya || echo tidak)" "ya"

echo "=== 2. BEDA ALOKASI -> DUA TRANSAKSI ==="
P2='{"vendor_id":"'$VEND'","payment_date":"2026-08-06","payment_method":"bank_transfer","bank_account_id":"'$BANK'","total_amount":1200000,"allocations":[{"bill_id":"'$BILL'","amount_applied":1200000}]}'
b=$(N); POST "$P2" >/dev/null; a=$(N)
chk "transaksi bertambah" "$((a-b))" "1"

echo "=== 3. BEDA bank_fee_amount -> DUA TRANSAKSI ==="
P3='{"vendor_id":"'$VEND'","payment_date":"2026-08-06","payment_method":"bank_transfer","bank_account_id":"'$BANK'","total_amount":500000,"bank_fee_amount":2500,"allocations":[{"bill_id":"'$BILL'","amount_applied":500000}]}'
P3b='{"vendor_id":"'$VEND'","payment_date":"2026-08-06","payment_method":"bank_transfer","bank_account_id":"'$BANK'","total_amount":500000,"bank_fee_amount":6500,"allocations":[{"bill_id":"'$BILL'","amount_applied":500000}]}'
b=$(N); POST "$P3" >/dev/null; POST "$P3b" >/dev/null; a=$(N)
chk "dua transaksi (fee beda)" "$((a-b))" "2"

echo "=== 4. int vs string nominal -> KUNCI SAMA (koreksi 2) ==="
P4a='{"vendor_id":"'$VEND'","payment_date":"2026-08-06","payment_method":"bank_transfer","bank_account_id":"'$BANK'","total_amount":300000,"allocations":[{"bill_id":"'$BILL'","amount_applied":300000}]}'
P4b='{"vendor_id":"'$VEND'","payment_date":"2026-08-06","payment_method":"bank_transfer","bank_account_id":"'$BANK'","total_amount":"300000.00","allocations":[{"bill_id":"'$BILL'","amount_applied":"300000.00"}]}'
b=$(N); POST "$P4a" >/dev/null; rr=$(POST "$P4b"); a=$(N)
cc=$(echo "$rr" | python3 -c "import sys,json;print(json.load(sys.stdin).get('was_cached'))" 2>/dev/null)
chk "hanya 1 transaksi (int==string)" "$((a-b))" "1"
chk "yang kedua was_cached=True"      "$cc" "True"

echo "=== 5. OVERPAYMENT GUARD masih hidup (koreksi 1) ==="
# sisa tagihan sudah tipis; minta jauh lebih besar -> harus DITOLAK
P5='{"vendor_id":"'$VEND'","payment_date":"2026-08-06","payment_method":"bank_transfer","bank_account_id":"'$BANK'","total_amount":9000000,"allocations":[{"bill_id":"'$BILL'","amount_applied":9000000}]}'
b=$(N); code=$(curl -s -o /tmp/ov.json -w '%{http_code}' -X POST "$B/bill-payments" "${H[@]}" -d "$P5"); a=$(N)
echo "     HTTP $code  $(head -c 90 /tmp/ov.json)"
chk "nol transaksi dibuat" "$((a-b))" "0"
chk "ditolak (4xx)" "$([ "${code:0:1}" = "4" ] && echo ya || echo tidak)" "ya"

echo
echo "baris idempotency_keys: $(KEYS)"
echo "===== HASIL: PASS=$PASS FAIL=$FAIL ====="
[ "$FAIL" -eq 0 ] || exit 1
