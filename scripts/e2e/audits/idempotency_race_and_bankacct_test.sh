#!/bin/bash
# Menutup DUA lubang uji: bank_account_id discrimination + RACE konkuren.
B=http://localhost:8002/api
T=$(curl -s -X POST $B/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"delivered+owner@resend.dev","password":"KaosBiru2026!"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['data']['access_token'])")
H=(-H "Authorization: Bearer $T" -H "Content-Type: application/json")
VEND=$(docker exec milkyhoop-dev-postgres-1 sh -c "PGPASSWORD=Proyek771977 psql -U postgres -d milkydb -tAc \"SELECT id FROM vendors LIMIT 1\"" | tr -d '[:space:]')
BILL=$(docker exec milkyhoop-dev-postgres-1 sh -c "PGPASSWORD=Proyek771977 psql -U postgres -d milkydb -tAc \"SELECT id FROM bills LIMIT 1\"" | tr -d '[:space:]')
BK1=$(docker exec milkyhoop-dev-postgres-1 sh -c "PGPASSWORD=Proyek771977 psql -U postgres -d milkydb -tAc \"SELECT id FROM bank_accounts WHERE account_name='BCA Operasional'\"" | tr -d '[:space:]')
BK2=$(docker exec milkyhoop-dev-postgres-1 sh -c "PGPASSWORD=Proyek771977 psql -U postgres -d milkydb -tAc \"SELECT id FROM bank_accounts WHERE account_name='Kas Kecil'\"" | tr -d '[:space:]')
Q(){ docker exec milkyhoop-dev-postgres-1 sh -c "PGPASSWORD=Proyek771977 psql -U postgres -d milkydb -tAc \"$1\"" | tr -d '[:space:]'; }
N(){ Q "SELECT count(*) FROM bill_payments_v2"; }
ALLOC(){ Q "SELECT COALESCE(SUM(amount_applied),0)::bigint FROM bill_payment_allocations a JOIN bill_payments_v2 p ON p.id=a.payment_id WHERE a.bill_id='$BILL' AND p.status<>'voided'"; }
PASS=0; FAIL=0
chk(){ if [ "$2" = "$3" ]; then echo "  PASS  $1 ($2)"; PASS=$((PASS+1)); else echo "  !!! FAIL $1: dapat=$2 harap=$3"; FAIL=$((FAIL+1)); fi; }
pay(){ printf '{"vendor_id":"%s","payment_date":"2026-08-06","payment_method":"bank_transfer","bank_account_id":"%s","total_amount":%s,"allocations":[{"bill_id":"%s","amount_applied":%s}]}' "$VEND" "$1" "$2" "$BILL" "$2"; }

echo "=== LUBANG 1: BEDA bank_account_id -> HARUS DUA TRANSAKSI ==="
b=$(N)
curl -s -X POST $B/bill-payments "${H[@]}" -d "$(pay $BK1 400000)" >/dev/null
r=$(curl -s -X POST $B/bill-payments "${H[@]}" -d "$(pay $BK2 400000)")
a=$(N)
cc=$(echo "$r" | python3 -c "import sys,json;print(json.load(sys.stdin).get('was_cached'))" 2>/dev/null)
chk "dua transaksi (rekening beda)" "$((a-b))" "2"
chk "yang kedua BUKAN replay"       "$cc" "False"

echo "=== LUBANG 2: RACE — dua alokasi BERBEDA ke tagihan SAMA, KONKUREN ==="
REM=$(Q "SELECT (grand_total - COALESCE(amount_paid,0))::bigint FROM bills WHERE id='$BILL'")
echo "     sisa tagihan: $REM"
HALF=$(( REM / 2 + 200000 ))   # dua-duanya < sisa, TAPI jumlahnya MELEBIHI sisa
echo "     dua request paralel @ $HALF (jumlah $((HALF*2)) > sisa $REM)"
b=$(N); ab=$(ALLOC)
curl -s -o /tmp/c1.json -w '%{http_code}\n' -X POST $B/bill-payments "${H[@]}" -d "$(pay $BK1 $HALF)" > /tmp/rc1 &
curl -s -o /tmp/c2.json -w '%{http_code}\n' -X POST $B/bill-payments "${H[@]}" -d "$(pay $BK2 $HALF)" > /tmp/rc2 &
wait
C1=$(cat /tmp/rc1); C2=$(cat /tmp/rc2); a=$(N); aa=$(ALLOC)
echo "     HTTP: $C1 / $C2"
echo "     resp1: $(head -c 70 /tmp/c1.json)"
echo "     resp2: $(head -c 70 /tmp/c2.json)"
OK2=$(( $(echo "$C1" | grep -c '^2') + $(echo "$C2" | grep -c '^2') ))
chk "tepat SATU yang sukses" "$OK2" "1"
chk "hanya 1 transaksi baru"  "$((a-b))" "1"
echo "     total teralokasi: $ab -> $aa (sisa awal $REM)"
if [ "$aa" -le "$((ab + REM))" ]; then echo "  PASS  total teralokasi TIDAK melebihi sisa"; PASS=$((PASS+1));
else echo "  !!! FAIL over-application: $aa > $((ab+REM))"; FAIL=$((FAIL+1)); fi

echo
echo "===== HASIL: PASS=$PASS FAIL=$FAIL ====="
[ "$FAIL" -eq 0 ] || exit 1
