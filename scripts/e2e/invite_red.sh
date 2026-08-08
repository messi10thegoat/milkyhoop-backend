#!/bin/bash
# GATE MERAH — batch INVITE. Menegaskan keadaan PASCA-FIX.
# PRA-FIX: butir inti MERAH, seluruh pagar HIJAU.
B=${B:-http://localhost:8001/api}; PW='KaosBiru2026!'
PASS=0; FAIL=0; rm -f /tmp/inv_abort
ok(){ if [ "$2" = "$3" ]; then echo "  ✓ $1: $2"; PASS=$((PASS+1));
      else echo "  ✗ $1: dapat=$2 HARAP=$3"; FAIL=$((FAIL+1)); fi; }
Q(){ local b=$(printf '%s' "$1"|base64); ssh root@159.89.202.160 "echo $b|base64 -d>/tmp/q.sql && docker cp /tmp/q.sql milkyhoop-dev-postgres-1:/tmp/q.sql>/dev/null && docker exec milkyhoop-dev-postgres-1 sh -c 'PGPASSWORD=Proyek771977 psql -U postgres -d milkydb -tA -f /tmp/q.sql'" 2>&1|tr -d '\r '; }
c(){ local x=$(curl -s -o /dev/null -w '%{http_code}' "$@"); [ "$x" = "000" ] && { echo "!! HTTP 000 = KEGAGALAN ALAT" >&2; touch /tmp/inv_abort; }; printf '%s' "$x"; }
abort(){ [ -f /tmp/inv_abort ] && { echo; echo "===== DIHENTIKAN: kegagalan alat ====="; exit 2; }; }

# login() memisahkan KEGAGALAN ALAT dari kegagalan produk.
# 429 = pembatas laju kena karena gate ini sendiri terlalu sering login; itu
# BUKAN "anggota tak bisa login". Sekali ia dibaca sebagai hasil, gate melapor
# dua assertion menyimpang untuk sebab yang tak ada hubungannya dengan undangan.
# Ditemukan 2026-08-08 saat run berulang. LOGIN_GAP dinaikkan + backoff sekali.
LOGIN_GAP=${LOGIN_GAP:-12}
login(){ # $1=email $2=password -> "token|business_role_code"
  local body code i
  for i in 1 2; do
    sleep "$LOGIN_GAP"
    code=$(curl -s -o /tmp/inv_login -w '%{http_code}' -X POST "$B/auth/login" \
      -H 'Content-Type: application/json' -d "{\"email\":\"$1\",\"password\":\"$2\"}")
    [ "$code" = "429" ] || break
    echo "    (429 pembatas laju — menunggu, lalu satu kali coba lagi)" >&2
    sleep 45
  done
  if [ "$code" = "429" ] || [ "$code" = "000" ]; then
    echo "!! login $1 -> HTTP $code = KEGAGALAN ALAT, bukan hasil" >&2; touch /tmp/inv_abort; printf '|'; return
  fi
  python3 -c 'import sys,json
try:
  d=json.load(open("/tmp/inv_login")).get("data",{}) or {}
  print((d.get("access_token") or "")+"|"+str(d.get("business_role_code")))
except Exception: print("|")'
}

INV="inv+$(date +%s)@kaosbiru.co.id"

HZ=$(curl -s -o /dev/null -w '%{http_code}' "${B%/api}/healthz"); [ "$HZ" = "200" ] || { echo "!! healthz=$HZ"; exit 2; }
echo "PREFLIGHT: $B hidup · undangan untuk $INV"
OT="$(login delivered+owner@resend.dev "$PW")"; OT="${OT%%|*}"
abort; [ -z "$OT" ] && { echo "!! login owner gagal = ALAT"; exit 2; }
RID=$(Q "SELECT id FROM roles WHERE code='CASHIER' LIMIT 1;")
UTR0=$(Q "SELECT count(*) FROM user_tenant_roles;"); USR0=$(Q "SELECT count(*) FROM \"User\";")
echo "    baseline: user_tenant_roles=$UTR0  User=$USR0  role CASHIER=$RID"

echo; echo "--- 1. POST /invite MENULIS team_invitations ---"
R=$(curl -s -X POST "$B/team-members/invite" -H "Authorization: Bearer $OT" -H 'Content-Type: application/json' -d "{\"email\":\"$INV\",\"role_id\":\"$RID\",\"name\":\"Kasir Undangan\"}")
echo "    respons: $(printf '%s' "$R"|head -c 120)"
ok "undangan tertulis" "$(Q "SELECT count(*) FROM team_invitations WHERE email='$INV' AND status='pending';")" "1"
TOK=$(Q "SELECT invite_token FROM team_invitations WHERE email='$INV' LIMIT 1;")
ok "token undangan terbentuk" "$([ -n "$TOK" ] && echo ada || echo kosong)" "ada"

echo; echo "--- ★ BATAS: keanggotaan LAHIR HANYA saat accept ---"
ok "★ user_tenant_roles TIDAK bertambah sebelum accept" "$(Q "SELECT count(*) FROM user_tenant_roles;")" "$UTR0"
ok "★ NOL User tanpa passwordHash" "$(Q "SELECT count(*) FROM \"User\" WHERE \"passwordHash\" IS NULL OR \"passwordHash\"='';")" "0"

echo; echo "--- 2. GET /api/invite/{token} ---"
ok "validasi undangan" "$(c "$B/invite/$TOK")" "200"
abort

echo; echo "--- 3. accept -> keanggotaan lahir, status ACTIVE ---"
A=$(curl -s -X POST "$B/invite/$TOK/accept" -H 'Content-Type: application/json' -d "{\"name\":\"Kasir Undangan\",\"password\":\"$PW\",\"password_confirm\":\"$PW\"}")
ok "keanggotaan bertambah" "$(Q "SELECT count(*) FROM user_tenant_roles;")" "$((UTR0+1))"
ok "status lolos V223" "$(Q "SELECT utr.status FROM user_tenant_roles utr JOIN \"User\" u ON u.id::uuid=utr.user_id WHERE u.email='$INV';")" "ACTIVE"
ok "undangan jadi accepted" "$(Q "SELECT status FROM team_invitations WHERE email='$INV';")" "accepted"

echo; echo "--- 4/5. anggota login: peran YANG DIUNDANG, izin TERBATAS ---"
MT="$(login "$INV" "$PW")"
abort
ok "business_role_code" "${MT#*|}" "CASHIER"
MTOK="${MT%%|*}"
if [ -n "$MTOK" ]; then
  NM=$(curl -s "$B/permissions/me" -H "Authorization: Bearer $MTOK"|python3 -c 'import sys,json
try:
  d=json.load(sys.stdin); print(d.get("role_code"), len(d.get("effective_permissions",{})))
except Exception: print("? ?")')
  ok "role_code di /permissions/me" "${NM% *}" "CASHIER"
  ok "izin TERBATAS (bukan 35 modul)" "$([ "${NM#* }" -lt 35 ] 2>/dev/null && echo terbatas || echo "${NM#* }")" "terbatas"
else
  ok "anggota bisa login" "tidak" "ya"
fi

echo; echo "--- 7. undangan dicabut -> token mati ---"
INV2="inv2+$(date +%s)@kaosbiru.co.id"
curl -s -X POST "$B/team-members/invite" -H "Authorization: Bearer $OT" -H 'Content-Type: application/json' -d "{\"email\":\"$INV2\",\"role_id\":\"$RID\"}" >/dev/null
IID=$(Q "SELECT id FROM team_invitations WHERE email='$INV2' LIMIT 1;"); TOK2=$(Q "SELECT invite_token FROM team_invitations WHERE email='$INV2' LIMIT 1;")
curl -s -X DELETE "$B/team-members/invitations/$IID" -H "Authorization: Bearer $OT" >/dev/null
ok "token yang dicabut ditolak" "$(c "$B/invite/$TOK2")" "410"

echo; echo "--- 8. resend MEMUTAR token: yang LAMA harus mati ---"
INV3="inv3+$(date +%s)@kaosbiru.co.id"
curl -s -X POST "$B/team-members/invite" -H "Authorization: Bearer $OT" -H 'Content-Type: application/json' -d "{\"email\":\"$INV3\",\"role_id\":\"$RID\"}" >/dev/null
IID3=$(Q "SELECT id FROM team_invitations WHERE email='$INV3' LIMIT 1;")
TOKA=$(Q "SELECT invite_token FROM team_invitations WHERE email='$INV3' LIMIT 1;")
ok "token lama berlaku sebelum resend" "$(c "$B/invite/$TOKA")" "200"
curl -s -X POST "$B/team-members/invitations/$IID3/resend" -H "Authorization: Bearer $OT" >/dev/null
TOKB=$(Q "SELECT invite_token FROM team_invitations WHERE email='$INV3' LIMIT 1;")
ok "token BERUBAH sesudah resend" "$([ "$TOKA" != "$TOKB" ] && echo berubah || echo sama)" "berubah"
ok "★ token LAMA mati" "$(c "$B/invite/$TOKA")" "404"
ok "token BARU berlaku" "$(c "$B/invite/$TOKB")" "200"

echo; echo "--- daftar undangan menunggu ---"
ok "list memuat undangan" "$(curl -s "$B/team-members/invitations" -H "Authorization: Bearer $OT" | python3 -c 'import sys,json
d=json.load(sys.stdin).get("data",[]); print(len([x for x in d if x.get("invite_link")]))')" "1"

echo; echo "--- 9. PAGAR ---"
ok "owner tetap OWNER" "$(curl -s "$B/permissions/me" -H "Authorization: Bearer $OT"|python3 -c 'import sys,json;print(json.load(sys.stdin).get("role_code"))')" "OWNER"
ok "owner tetap 35 modul" "$(curl -s "$B/permissions/me" -H "Authorization: Bearer $OT"|python3 -c 'import sys,json;print(len(json.load(sys.stdin).get("effective_permissions",{})))')" "35"
ok "owner /dashboard/all" "$(c "$B/dashboard/all" -H "Authorization: Bearer $OT")" "200"

echo; echo "--- bersih-bersih ---"
Q "DELETE FROM user_tenant_roles utr USING \"User\" u WHERE u.id::uuid=utr.user_id AND u.email LIKE 'inv%@kaosbiru.co.id';
   DELETE FROM \"User\" WHERE email LIKE 'inv%@kaosbiru.co.id';
   DELETE FROM team_invitations WHERE email LIKE 'inv%@kaosbiru.co.id';" >/dev/null
echo "    user=$(Q "SELECT count(*) FROM \"User\";") peran=$(Q "SELECT count(*) FROM user_tenant_roles;") undangan=$(Q "SELECT count(*) FROM team_invitations;")"
echo; echo "===== $PASS sesuai, $FAIL menyimpang ====="
[ $FAIL -eq 0 ]
