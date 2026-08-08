#!/bin/bash
# GATE MERAH — batch "tutup baris 241" (pengguna tanpa baris peran -> DENY).
# Menegaskan keadaan PASCA-FIX. Dijalankan PRA-FIX: butir inti harus MERAH,
# seluruh pagar harus HIJAU.
B=${B:-http://localhost:8001/api}; PW='KaosBiru2026!'
PASS=0; FAIL=0; rm -f /tmp/l241_abort
ok(){ if [ "$2" = "$3" ]; then echo "  ✓ $1: $2"; PASS=$((PASS+1));
      else echo "  ✗ $1: dapat=$2 HARAP=$3"; FAIL=$((FAIL+1)); fi; }
Q(){ local b=$(printf '%s' "$1"|base64); ssh root@159.89.202.160 "echo $b|base64 -d>/tmp/q.sql && docker cp /tmp/q.sql milkyhoop-dev-postgres-1:/tmp/q.sql>/dev/null && docker exec milkyhoop-dev-postgres-1 sh -c 'PGPASSWORD=Proyek771977 psql -U postgres -d milkydb -tA -f /tmp/q.sql'" 2>&1|tr -d '\r '; }
c(){ local x=$(curl -s -o /dev/null -w '%{http_code}' "$@"); [ "$x" = "000" ] && { echo "!! HTTP 000 = KEGAGALAN ALAT" >&2; touch /tmp/l241_abort; }; printf '%s' "$x"; }
abort(){ [ -f /tmp/l241_abort ] && { echo; echo "===== DIHENTIKAN: kegagalan alat, TIDAK ADA HASIL SAH ====="; exit 2; }; }
tok(){ sleep 6; local t=$(curl -s -X POST "$B/auth/login" -H 'Content-Type: application/json' -d "{\"email\":\"$1\",\"password\":\"$2\"}"|python3 -c 'import sys,json
try: print(json.load(sys.stdin).get("data",{}).get("access_token") or "")
except Exception: print("")'); printf '%s' "$t"; }

HZ=$(curl -s -o /dev/null -w '%{http_code}' "${B%/api}/healthz")
[ "$HZ" = "200" ] || { echo "!! healthz=$HZ — TIDAK ADA HASIL SAH"; exit 2; }
echo "PREFLIGHT: $B hidup"

# ---------- fixture: satu anggota CASHIER ----------
scp -q "$(dirname "$0")/deact_setup.sql" root@159.89.202.160:/tmp/deact_setup.sql
ssh root@159.89.202.160 'docker cp /tmp/deact_setup.sql milkyhoop-dev-postgres-1:/tmp/>/dev/null && docker exec milkyhoop-dev-postgres-1 sh -c "PGPASSWORD=Proyek771977 psql -U postgres -d milkydb -q -f /tmp/deact_setup.sql"' >/dev/null

OT=$(tok owner@kaosbiru.co.id "$PW"); [ -z "$OT" ] && { echo "!! login owner gagal = ALAT"; exit 2; }
KT=$(tok deact+kasir@kaosbiru.co.id "$PW"); [ -z "$KT" ] && { echo "!! login anggota gagal = ALAT"; exit 2; }

echo; echo "--- PAGAR: keadaan sehat ---"
ok "owner /sales-invoices" "$(c "$B/sales-invoices" -H "Authorization: Bearer $OT")" "200"
ok "owner /dashboard/all"  "$(c "$B/dashboard/all"  -H "Authorization: Bearer $OT")" "200"
ok "anggota AKTIF /sales-invoices" "$(c "$B/sales-invoices" -H "Authorization: Bearer $KT")" "200"
abort

echo; echo "--- ★ INTI: anggota DIHAPUS, token masih hidup ---"
Q "DELETE FROM user_tenant_roles utr USING \"User\" u WHERE u.id::uuid=utr.user_id AND u.email='deact+kasir@kaosbiru.co.id';" >/dev/null
ok "baris peran tersisa" "$(Q "SELECT count(*) FROM user_tenant_roles utr JOIN \"User\" u ON u.id::uuid=utr.user_id WHERE u.email='deact+kasir@kaosbiru.co.id';")" "0"
ok "★ token SAMA /sales-invoices" "$(c "$B/sales-invoices" -H "Authorization: Bearer $KT")" "409"
ok "★ token SAMA /dashboard/all"  "$(c "$B/dashboard/all"  -H "Authorization: Bearer $KT")" "409"
abort

echo; echo "--- ★ PAGAR ONBOARDING PENUH: signup NYATA -> login -> dashboard ---"
TS=$(date +%s); NEWMAIL="l241+$TS@kaosbiru.co.id"; NEWPW='OnboardTest2026!'
R1=$(curl -s -X POST "$B/auth/signup/register" -H 'Content-Type: application/json' -d "{\"email\":\"$NEWMAIL\"}")
echo "    register -> $(printf '%s' "$R1" | head -c 90)"
# Kode diverifikasi bcrypt sehingga tak terbaca dari DB. Kita TANAM hash kode
# yang diketahui. Yang dilewati HANYA pengiriman email — endpoint register,
# verify-code, dan complete-setup tetap dipanggil sungguhan.
HASH=$(ssh root@159.89.202.160 "docker exec milkyhoop-dev-api_gateway python3 -c \"import bcrypt;print(bcrypt.hashpw(b'123456',bcrypt.gensalt(rounds=10)).decode())\"" 2>/dev/null | tr -d '\r')
[ -z "$HASH" ] && { echo "!! hash tak terbentuk = KEGAGALAN ALAT"; exit 2; }
Q "UPDATE pending_registrations SET verification_code='$HASH', attempt_count=0 WHERE email='$NEWMAIL';" >/dev/null
R2=$(curl -s -X POST "$B/auth/signup/verify-code" -H 'Content-Type: application/json' -d "{\"email\":\"$NEWMAIL\",\"code\":\"123456\"}")
ST=$(printf '%s' "$R2"|python3 -c 'import sys,json
try:
  d=json.load(sys.stdin); print(d.get("setup_token") or d.get("data",{}).get("setup_token") or "")
except Exception: print("")')
[ -z "$ST" ] && { echo "!! setup_token kosong: $(printf '%s' "$R2"|head -c 200)"; echo "   = KEGAGALAN ALAT"; exit 2; }
R3=$(curl -s -X POST "$B/auth/signup/complete-setup" -H "Authorization: Bearer $ST" -H 'Content-Type: application/json' -d "{\"password\":\"$NEWPW\",\"business_name\":\"Uji Onboarding $TS\"}")
NT=$(printf '%s' "$R3"|python3 -c 'import sys,json
try:
  d=json.load(sys.stdin); print(d.get("access_token") or d.get("data",{}).get("access_token") or "")
except Exception: print("")')
ok "signup NYATA menerbitkan token" "$([ -n "$NT" ] && echo ya || echo tidak)" "ya"
if [ -n "$NT" ]; then
  ok "pengguna BARU /permissions/me role" "$(curl -s "$B/permissions/me" -H "Authorization: Bearer $NT"|python3 -c 'import sys,json;print(json.load(sys.stdin).get("role_code"))')" "OWNER"
  ok "pengguna BARU /dashboard/all" "$(c "$B/dashboard/all" -H "Authorization: Bearer $NT")" "200"
  ok "pengguna BARU /sales-invoices" "$(c "$B/sales-invoices" -H "Authorization: Bearer $NT")" "200"
  NT2=$(tok "$NEWMAIL" "$NEWPW")
  ok "pengguna BARU login ULANG lalu dashboard" "$(c "$B/dashboard/all" -H "Authorization: Bearer $NT2")" "200"
fi
abort

echo; echo "--- bersih-bersih ---"
Q "DELETE FROM user_tenant_roles utr USING \"User\" u WHERE u.id::uuid=utr.user_id AND (u.email LIKE 'deact+%' OR u.email LIKE 'l241+%');
   DELETE FROM \"User\" WHERE email LIKE 'deact+%' OR email LIKE 'l241+%';
   DELETE FROM pending_registrations WHERE email LIKE 'l241+%';" >/dev/null
echo "    user tersisa: $(Q "SELECT count(*) FROM \"User\";")  baris peran: $(Q "SELECT count(*) FROM user_tenant_roles;")"
echo; echo "===== $PASS sesuai, $FAIL menyimpang ====="

# Verdict WAJIB terbaca mesin. Versi pertama berkas ini keluar 0 walau ada
# assertion menyimpang — sebuah gate yang exit code-nya tak mencerminkan
# verdictnya akan lulus di CI mana pun tanpa pernah diperiksa manusia.
[ $FAIL -eq 0 ]
