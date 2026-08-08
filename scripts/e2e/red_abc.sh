#!/bin/bash
# GATE UJI-MERAH batch permission A+B+C. Terhadap gateway UJI :8002 (worktree),
# BUKAN gateway live :8001. Empat uji WAJIB merah + satu pagar keselamatan
# yang WAJIB hijau sebelum DAN sesudah fix.
B=${B:-http://localhost:8002/api}
PW='KaosBiru2026!'
# Kutip ganda ("User") tak selamat melewati ssh -> sh -> psql. Kirim base64.
# (Versi pertama helper ini DIAM-DIAM gagal dan mencetak baris kosong — instrumen
#  rusak yang menyamar sebagai data. Law 33.)
Q(){ local b=$(printf '%s' "$1" | base64); ssh root@159.89.202.160 "echo $b | base64 -d > /tmp/q.sql && docker cp /tmp/q.sql milkyhoop-dev-postgres-1:/tmp/q.sql >/dev/null && docker exec milkyhoop-dev-postgres-1 sh -c 'PGPASSWORD=Proyek771977 psql -U postgres -d milkydb -tA -f /tmp/q.sql'" 2>&1 | tr -d '\r' | tr -d ' '; }
# Jeda antar-login: rangkaian login cepat memicu rate limit (401/429) dan
# hasilnya menyamar sebagai "uji tidak merah". Itu persis kelas kesalahan yang
# sedang kita perangi — kegagalan alat dibaca sebagai hasil pengukuran.
# Lihat DOCS/issues/E2E-LOGIN-FIXTURE-FLAKY-LOCALHOST-001.md
LOGIN_GAP=${LOGIN_GAP:-6}
login(){
  sleep "$LOGIN_GAP"
  local r
  r=$(curl -s -X POST "$B/auth/login" -H 'Content-Type: application/json' \
        -d "{\"email\":\"$1\",\"password\":\"$PW\"}")
  local tok
  tok=$(printf '%s' "$r" | python3 -c 'import sys,json
try:
    d=json.load(sys.stdin); print(d.get("data",d).get("access_token") or "")
except Exception: print("")' 2>/dev/null)
  if [ -z "$tok" ]; then
    # `exit` di dalam $(...) hanya mematikan SUBSHELL — skrip induk lanjut dan
    # hasilnya terbaca sebagai "tidak merah". Pakai sentinel supaya induk mati.
    echo "!! GAGAL LOGIN untuk $1 — INI KEGAGALAN ALAT, BUKAN HASIL UJI." >&2
    echo "   respons: $(printf '%s' "$r" | head -c 160)" >&2
    touch /tmp/red_abort
    return 1
  fi
  printf '%s' "$r"
}
# Sesudah fix, GAGAL LOGIN adalah HASIL YANG BENAR untuk sebagian pengguna
# (409 belum punya peran / 403 dinonaktifkan). login() yang meng-abort hanya
# untuk pengguna yang MEMANG harus bisa masuk.
login_raw(){
  sleep "$LOGIN_GAP"
  curl -s -o /tmp/lr.json -w '%{http_code}' -X POST "$B/auth/login" \
    -H 'Content-Type: application/json' -d "{\"email\":\"$1\",\"password\":\"$PW\"}"
}
errcode(){ python3 -c "
import json
try:
    d=json.load(open('/tmp/lr.json')); det=d.get('detail')
    print(det.get('error_code') if isinstance(det,dict) else str(det)[:40])
except Exception: print('(tak terbaca)')" 2>/dev/null; }
abort_if_tool_failed(){ if [ -f /tmp/red_abort ]; then
  echo; echo "===== DIHENTIKAN: kegagalan alat. NOL hasil uji yang sah. ====="; exit 2; fi; }
rm -f /tmp/red_abort
jq_(){ python3 -c "import sys,json;d=json.load(sys.stdin);print(json.loads('null') if False else (d.get('data',d).get('$1')))" 2>/dev/null; }
PASS=0; FAIL=0

# PREFLIGHT. Tanpa ini, tunnel yang putus menghasilkan HTTP 000 di semua uji
# dan laporannya berbunyi "tidak merah" — kegagalan alat yang menyamar sebagai
# hasil, untuk KETIGA kalinya di gate ini. Cek dulu, jangan asumsikan.
HZ=$(curl -s -o /dev/null -w '%{http_code}' "${B%/api}/healthz")
if [ "$HZ" != "200" ]; then
  echo "!! GATEWAY UJI TIDAK TERJANGKAU (healthz=$HZ) di ${B%/api}"
  echo "   Nyalakan tunnel:  ssh -f -N -L 8002:127.0.0.1:8002 root@159.89.202.160"
  echo "   INI KEGAGALAN ALAT — TIDAK ADA HASIL UJI YANG SAH DARI RUN INI."
  exit 2
fi
echo "PREFLIGHT: gateway uji hidup (healthz=200)"

# SEED ULANG tiap run. Tanpa ini, uji 3 (DELETE) menghapus barisnya sendiri dan
# run BERIKUTNYA lulus karena barisnya sudah tak ada — lulus untuk alasan yang
# SALAH. Ditemukan saat run kedua; jangan dihapus.
echo "=== SEED: menyiapkan pengguna sekali-pakai ==="
scp -q "$(dirname "$0")/red_setup.sql" root@159.89.202.160:/tmp/red_setup.sql
ssh root@159.89.202.160 'docker cp /tmp/red_setup.sql milkyhoop-dev-postgres-1:/tmp/ >/dev/null && docker exec milkyhoop-dev-postgres-1 sh -c "PGPASSWORD=Proyek771977 psql -U postgres -d milkydb -q -v ON_ERROR_STOP=1 -f /tmp/red_setup.sql"' | tail -4

red(){ if [ "$2" = "$3" ]; then echo "  MERAH ✓ $1 (dapat: $2)"; PASS=$((PASS+1)); else echo "  !! TIDAK MERAH $1: dapat=$2 harap=$3"; FAIL=$((FAIL+1)); fi; }
grn(){ if [ "$2" = "$3" ]; then echo "  HIJAU ✓ $1 ($2)"; PASS=$((PASS+1)); else echo "  !! PAGAR JEBOL $1: dapat=$2 harap=$3"; FAIL=$((FAIL+1)); fi; }

echo "=== 0. PAGAR KESELAMATAN — owner normal WAJIB HIJAU ==="
R=$(login delivered+owner@resend.dev); abort_if_tool_failed
OROLE=$(echo "$R" | jq_ business_role_code)
OTOK=$(echo "$R" | jq_ access_token)
grn "login owner -> business_role_code" "$OROLE" "OWNER"
PM=$(curl -s "$B/permissions/me" -H "Authorization: Bearer $OTOK")
grn "permissions/me role_code" "$(echo "$PM" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("role_code"))')" "OWNER"
NMOD=$(echo "$PM" | python3 -c 'import sys,json;print(len(json.load(sys.stdin).get("effective_permissions",{})))')
grn "permissions/me jumlah modul >0" "$([ "$NMOD" -gt 0 ] && echo ya || echo tidak)" "ya"

echo
echo "=== 1. (B) peran di-LOOKUP, bukan ditebak ==="
echo "    Peran di DB: $(Q "SELECT r.code FROM user_tenant_roles utr JOIN roles r ON r.id=utr.role_id JOIN \"User\" u ON u.id::uuid=utr.user_id WHERE u.email='redtest+bendahara@kaosbiru.co.id';")"
BR=$(login redtest+bendahara@kaosbiru.co.id | jq_ business_role_code); abort_if_tool_failed
BTOK=$(python3 -c "
import json;print(json.load(open('/tmp/lr.json')).get('data',{}).get('access_token','')) " 2>/dev/null)
grn "BENDAHARA login -> business_role_code (PRA-FIX: OWNER)" "$BR" "BENDAHARA"

echo
echo "=== 2. (A) token masih hidup tapi peran DICABUT -> /permissions/me harus GALAT ==="
BTOK=$(curl -s -X POST "$B/auth/login" -H 'Content-Type: application/json' -d "{\"email\":\"redtest+bendahara@kaosbiru.co.id\",\"password\":\"$PW\"}" | python3 -c "
import sys,json;print(json.load(sys.stdin).get('data',{}).get('access_token','')) " 2>/dev/null)
MEMBERS=$(curl -s "$B/team-members" -H "Authorization: Bearer $OTOK")
MID=$(echo "$MEMBERS" | python3 -c "
import sys,json
d=json.load(sys.stdin); rows=d.get('data',d)
rows=rows if isinstance(rows,list) else rows.get('members',[])
print(next((r.get('id') for r in rows if 'redtest+bendahara' in str(r.get('email',''))), ''))" 2>/dev/null)
if [ -z "$MID" ]; then
  echo "!! member_id TAK DITEMUKAN di GET /team-members — uji 2/3 tak sah."
  echo "   isi daftar: $(echo "$MEMBERS" | head -c 300)"
  touch /tmp/red_abort; abort_if_tool_failed
fi
DEL=$(curl -s -o /dev/null -w '%{http_code}' -X DELETE "$B/team-members/$MID" -H "Authorization: Bearer $OTOK")
echo "    DELETE /team-members/{id} -> HTTP $DEL   (token lama masih dipegang klien)"
PCODE=$(curl -s -o /tmp/pm2.json -w '%{http_code}' "$B/permissions/me" -H "Authorization: Bearer $BTOK")
PROLE=$(python3 -c "
import json
try: print(json.load(open('/tmp/pm2.json')).get('role_code'))
except Exception: print('(tak terbaca)')" 2>/dev/null)
echo "    /permissions/me -> HTTP $PCODE role_code=$PROLE"
grn "peran dicabut -> /permissions/me HTTP (PRA-FIX: 200)" "$([ "$PCODE" = "409" ] && echo 409 || echo "$PCODE")" "409"
grn "peran dicabut -> BUKAN VIEWER (PRA-FIX: VIEWER)" "$([ "$PROLE" = "VIEWER" ] && echo VIEWER || echo bukan-viewer)" "bukan-viewer"

echo
echo "=== 3. (C) anggota DIHAPUS -> login berikutnya BUKAN OWNER ==="
CODE3=$(login_raw redtest+bendahara@kaosbiru.co.id)
R3=$(python3 -c "
import json
try: print(json.load(open('/tmp/lr.json')).get('data',{}).get('business_role_code'))
except Exception: print(None)" 2>/dev/null)
echo "    HTTP $CODE3  business_role_code=$R3  error_code=$(errcode)"
grn "anggota terhapus -> HTTP 409 (PRA-FIX: 200)" "$CODE3" "409"
grn "anggota terhapus -> BUKAN OWNER (PRA-FIX: OWNER)" "$([ "$R3" = "OWNER" ] && echo OWNER || echo bukan-owner)" "bukan-owner"
grn "error_code bisa ditindaklanjuti" "$(errcode)" "ROLE_NOT_PROVISIONED"

echo
echo "=== 4. keanggotaan DINONAKTIFKAN (SUSPENDED) -> 403 ==="
echo "    status di DB: $(Q "SELECT utr.status FROM user_tenant_roles utr JOIN \"User\" u ON u.id::uuid=utr.user_id WHERE u.email='redtest+suspended@kaosbiru.co.id';")"
CODE4=$(login_raw redtest+suspended@kaosbiru.co.id)
echo "    HTTP $CODE4  error_code=$(errcode)"
grn "SUSPENDED -> HTTP 403 (PRA-FIX: 200 + OWNER)" "$CODE4" "403"
grn "SUSPENDED -> error_code" "$(errcode)" "ROLE_INACTIVE"

echo
echo "=== 5. (pembaca kelima, auth.py:237) last_active_tenant_id BERTAHAN ==="
Q "UPDATE \"User\" SET last_active_tenant_id='redtest-tenant-kedua' WHERE email='delivered+owner@resend.dev';" >/dev/null
LA_BEFORE=$(Q "SELECT COALESCE(last_active_tenant_id,'(NULL)') FROM \"User\" WHERE email='delivered+owner@resend.dev';")
echo "    sebelum login: $LA_BEFORE  (keanggotaan di sana: $(Q "SELECT count(*) FROM user_tenant_roles WHERE tenant_id='redtest-tenant-kedua';") baris ACTIVE)"
login delivered+owner@resend.dev >/dev/null; abort_if_tool_failed
LA_AFTER=$(Q "SELECT COALESCE(last_active_tenant_id,'(NULL)') FROM \"User\" WHERE email='delivered+owner@resend.dev';")
echo "    sesudah login: $LA_AFTER"
grn "last_active BERTAHAN (PRA-FIX: di-NULL-kan tiap login)" "$LA_AFTER" "redtest-tenant-kedua"

echo
echo "=== 6. switch-tenant ke tenant UTAMA tak boleh otomatis OWNER ==="
# Uji 2/3 sengaja MENGHAPUS bendahara. Pulihkan dulu — kalau tidak, uji 6/7
# gagal karena prasyaratnya hilang, bukan karena perilakunya salah.
ssh root@159.89.202.160 'docker cp /tmp/red_setup.sql milkyhoop-dev-postgres-1:/tmp/ >/dev/null && docker exec milkyhoop-dev-postgres-1 sh -c "PGPASSWORD=Proyek771977 psql -U postgres -d milkydb -q -f /tmp/red_setup.sql"' >/dev/null 2>&1
# Cabang ketiga: `if target_tenant_id == primary_tenant_id: role_code = "OWNER"`.
# redtest+bendahara punya User.tenantId = kaos-biru-konveksi (tenant utamanya)
# TAPI perannya di sana BENDAHARA. Pra-fix, switch ke sana mengembalikan OWNER
# tanpa memeriksa baris peran sama sekali.
BT2=$(curl -s -X POST "$B/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"redtest+bendahara@kaosbiru.co.id\",\"password\":\"$PW\"}" \
  | python3 -c "
import sys,json;print(json.load(sys.stdin).get('data',{}).get('access_token','')) " 2>/dev/null)
SW=$(curl -s -X POST "$B/auth/switch-tenant" -H "Authorization: Bearer $BT2" \
  -H 'Content-Type: application/json' -d '{"tenant_id":"kaos-biru-konveksi"}')
# Bentuk respons: {access_token, refresh_token, role_code, tenant_id} — TOP LEVEL.
SWROLE=$(printf '%s' "$SW" | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin); print(d.get('role_code'))
except Exception: print('(tak terbaca)')" 2>/dev/null)
echo "    switch-tenant -> role_code=$SWROLE"
grn "switch ke tenant utama -> peran NYATA (PRA-FIX: OWNER)" "$SWROLE" "BENDAHARA"

echo
echo "=== 7. daftar tenant tak boleh menyuntik OWNER ==="
TL=$(curl -s "$B/auth/tenants" -H "Authorization: Bearer $BT2")
# Bentuk respons: {tenants:[...], current_tenant_id} — TOP LEVEL, bukan di data.
TLROLE=$(printf '%s' "$TL" | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin)
    print(next((t.get('role_code') for t in d.get('tenants',[]) if t.get('tenant_id')=='kaos-biru-konveksi'),'(tak ada)'))
except Exception: print('(tak terbaca)')" 2>/dev/null)
echo "    daftar tenant -> role_code=$TLROLE"
grn "daftar tenant -> peran NYATA (PRA-FIX: OWNER)" "$TLROLE" "BENDAHARA"

echo
echo "===== HASIL: $PASS lulus, $FAIL gagal ====="

# --- BERSIH-BERSIH (ditambah 2026-08-08) ---
# Gate ini dulu MENINGGALKAN fixture-nya. Residu itu bukan kotoran kosmetik:
# baris tenant-kedua milik owner yang ditinggalkan red_abc membuat lookup
# tanpa filter tenant mengembalikan DUA uuid, yang lalu menyambung jadi URL
# tak sah dan dibaca gate lain sebagai kegagalan PRODUK. Gate yang tak
# membersihkan diri membuat gate BERIKUTNYA berbohong.
_bersih(){ local b=$(printf '%s' "$1"|base64); ssh root@159.89.202.160 "echo $b|base64 -d>/tmp/c.sql && docker cp /tmp/c.sql milkyhoop-dev-postgres-1:/tmp/c.sql>/dev/null && docker exec milkyhoop-dev-postgres-1 sh -c 'PGPASSWORD=Proyek771977 psql -U postgres -d milkydb -q -f /tmp/c.sql'" >/dev/null 2>&1; }
_bersih "DELETE FROM user_permission_overrides WHERE user_id IN (SELECT id FROM \"User\" WHERE email LIKE 'redtest+%' OR email LIKE 'deact+%');
DELETE FROM user_tenant_roles utr USING \"User\" u WHERE u.id::uuid=utr.user_id AND (u.email LIKE 'redtest+%' OR u.email LIKE 'deact+%');
DELETE FROM user_tenant_roles WHERE tenant_id='redtest-tenant-kedua';
DELETE FROM \"User\" WHERE email LIKE 'redtest+%' OR email LIKE 'deact+%';"
echo "  (fixture dibersihkan)"

[ "$FAIL" -eq 0 ] || exit 1
