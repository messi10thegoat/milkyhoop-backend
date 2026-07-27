#!/bin/bash
# =============================================================================
# step_-1_provision.sh  —  FASE 4, step -1: provision the DP-flow tenant + master
# data via the REAL API path a user walks (signup magic-link + REST), never raw
# INSERT. This is the one path that proves onboarding_service actually provisions.
#
# Signup bypass = the PROVEN goldenpath.sh mechanism: read pending_registrations.
# magic_token (plaintext) -> GET /auth/signup/verify-link/<token> -> setup token
# -> complete-setup. NOT the 6-digit code (bcrypt, unrecoverable). No new mechanism.
#
# IDEMPOTENT: login-first (reuse tenant if it already exists), and every master-data
# row is lookup-before-create. Safe to re-run against a non-pristine milkydb.
#
# DP SPEC (trading, non-PKP): item "Kaos Biru 30s" goods/track_inventory, buy 35.000
# sell 50.000, for_sales+for_purchases. Vendor, customer, bank (opening 20.000.000),
# warehouse. No BOM / work-center / payroll / tax on lines (non-PKP).
#
# Writes state.env (TEN, TOK, EMAIL, PASS, BANK, BANK_COA, WH, VND, CUS, ITEM) for
# steps 0-9. Env overridable: B, DB, CONTAINER, STATE, EMAIL, PASS, BIZ.
# =============================================================================
set -u
B=${B:-http://localhost:8001/api}
DB=${DB:-milkydb}
CONTAINER=${CONTAINER:-milkyhoop-dev-postgres-1}
DIR="$(cd "$(dirname "$0")" && pwd)"
source "$DIR/dates.env"   # explicit forward-ordered date plan (opening balance first)
source "$DIR/verdict.sh"
STATE=${STATE:-$DIR/state.env}
EMAIL=${EMAIL:-owner@kaosbiru.co.id}
PASS=${PASS:-KaosBiru2026!}
BIZ=${BIZ:-Kaos Biru Konveksi}

PSQL(){ docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -tAc "$1" | tr -d '[:space:]'; }
gid(){  python3 -c "import sys,json;d=json.load(sys.stdin);print((d.get('data') or d).get('id',''))" 2>/dev/null; }
jval(){ python3 -c "import sys,json;d=json.load(sys.stdin);print((d.get('data') or d).get('$1',''))" 2>/dev/null; }
hdr(){ echo; echo "===================== $* ====================="; }
esc(){ printf "%s" "$1" | sed "s/'/''/g"; }

# ---------------------------------------------------------------------------
# AUTH — login if the owner already exists, else signup via magic-link.
# ---------------------------------------------------------------------------
hdr "AUTH ($EMAIL)"
R=$(curl -s -X POST "$B/auth/login" -H "Content-Type: application/json" \
    -d "{\"email\":\"$EMAIL\",\"password\":\"$PASS\"}")
TOK=$(echo "$R" | jval access_token)
if [ -n "$TOK" ]; then
  echo "LOGIN ok (tenant already provisioned) — idempotent reuse"
else
  echo "no existing login -> SIGNUP (magic-link)"
  curl -s -X POST "$B/auth/signup/register" -H "Content-Type: application/json" \
       -d "{\"email\":\"$EMAIL\"}" | head -c 160; echo
  TOKm=$(PSQL "SELECT magic_token FROM pending_registrations WHERE email='$(esc "$EMAIL")' ORDER BY created_at DESC LIMIT 1;")
  [ -z "$TOKm" ] && { echo "!!! no magic_token in pending_registrations — ABORT"; exit 1; }
  LOC=$(curl -s -i "$B/auth/signup/verify-link/$TOKm" 2>&1 | grep -i '^location' | tr -d '\r' | sed 's/^[Ll]ocation: //')
  ST=$(echo "$LOC" | sed -n 's/.*token=\([^&]*\).*/\1/p')
  [ -z "$ST" ] && { echo "!!! no setup token from verify-link redirect (LOC=$LOC) — ABORT"; exit 1; }
  R=$(curl -s -X POST "$B/auth/signup/complete-setup" -H "Authorization: Bearer $ST" \
       -H "Content-Type: application/json" \
       -d "{\"password\":\"$PASS\",\"business_name\":\"$BIZ\"}")
  TOK=$(echo "$R" | jval access_token)
fi
TEN=$(echo "$R" | jval tenant_id)
[ -z "$TEN" ] && TEN=$(PSQL "SELECT id FROM \"Tenant\" WHERE slug=(SELECT slug FROM \"Tenant\" ORDER BY created_at DESC LIMIT 1) LIMIT 1;")
[ -z "$TOK" ] && { echo "!!! no access_token — ABORT (resp: $(echo "$R" | head -c 200))"; exit 1; }
[ -z "$TEN" ] && { echo "!!! no tenant_id — ABORT"; exit 1; }
echo "TENANT=$TEN token=${TOK:0:14}..."
H=(-H "Authorization: Bearer $TOK" -H "X-Tenant-Slug: $TEN" -H "Content-Type: application/json")
J(){ local m=$1 p=$2 d=${3:-'{}'}; curl -s -X "$m" "$B$p" "${H[@]}" -d "$d"; }

echo "SEED by onboarding: CoA=$(PSQL "SELECT count(*) FROM chart_of_accounts WHERE tenant_id='$TEN';") roles=$(PSQL "SELECT count(*) FROM account_roles WHERE tenant_id='$TEN';") tax=$(PSQL "SELECT count(*) FROM tax_codes WHERE tenant_id='$TEN';")"

# ---------------------------------------------------------------------------
# TENANT CONFIG: NON-PKP  (documented API-gap workaround)
# FINDING (see DOCS/issues/2026-07-26-no-api-for-tenant-is_pkp.md): onboarding defaults
# "Tenant".is_pkp = TRUE (column default true, NOT NULL) and NO user-facing path can change
# the flag role_resolver actually gates VAT on:
#   - PATCH /api/settings/pkp  -> writes tax_info.is_pkp (DIFFERENT table; absent in this DB)
#   - PATCH /api/tenant/profile -> allows only display_name/address/phone/tax_id (not is_pkp)
#   - onboarding_service       -> hardcodes the default
# role_resolver reads "Tenant".is_pkp for VAT_INPUT/VAT_OUTPUT; false -> returns None -> the
# posting path emits NO VAT line. To represent the non-PKP UMKM target tenant we set it directly.
# This is a config flag on the tenant we own, not a financial-data shortcut. Idempotent.
BEFORE_PKP=$(PSQL "SELECT is_pkp FROM \"Tenant\" WHERE id='$TEN';")
PSQL "UPDATE \"Tenant\" SET is_pkp=false WHERE id='$TEN' AND is_pkp IS DISTINCT FROM false;" >/dev/null
echo "is_pkp: was=$BEFORE_PKP now=$(PSQL "SELECT is_pkp FROM \"Tenant\" WHERE id='$TEN';") (non-PKP for the DP spec)"

# ---------------------------------------------------------------------------
# DELIVERY-MODE revenue recognition (documented API-gap workaround)
# FINDING (DOCS/issues/2026-07-26-delivery-mode-revenue-recognition-unreachable.md): the defer
# lever tenant_config.revenue_recognition_policy has NO write path (no API, no settings UI, no
# onboarding row; FE never sends per-invoice recognize_at). Default 'invoice' -> sell-from-stock
# AUTO-FULFILLS (3 events at post), contradicting the konveksi "stock leaves at Pengiriman" model.
# A konveksi delivery-mode tenant can only get it via this raw write. Verified safe: the only other
# gateway reader (userguide_rag_enabled) behaves identically for no-row vs default-row; no CHECK
# constraint; code matches exact lowercase 'delivery'. REMOVE this once a real control is built.
# Idempotent via ON CONFLICT (tenant_id). Minimal columns; the rest take safe table defaults.
BEFORE_POL=$(PSQL "SELECT COALESCE((SELECT revenue_recognition_policy FROM tenant_config WHERE tenant_id='$TEN'),'<no-row>');")
PSQL "INSERT INTO tenant_config (tenant_id, revenue_recognition_policy) VALUES ('$TEN','delivery')
      ON CONFLICT (tenant_id) DO UPDATE SET revenue_recognition_policy='delivery', updated_at=now();" >/dev/null
echo "revenue_recognition_policy: was=$BEFORE_POL now=$(PSQL "SELECT revenue_recognition_policy FROM tenant_config WHERE tenant_id='$TEN';") (delivery mode: defer revenue to Pengiriman)"

# ---------------------------------------------------------------------------
# MASTER DATA — lookup-before-create (idempotent).
# ---------------------------------------------------------------------------
hdr "MASTER DATA"

# Bank (opening 20.000.000 — covers supplier pay 3.5M + DP flow)
BANK=$(PSQL "SELECT id FROM bank_accounts WHERE tenant_id='$TEN' AND account_number='1111222233';")
# opening_date = D_OPENING (earliest in the plan) so the opening bank_transaction is
# chronologically first and running_balance stays date-consistent (see backdating issue).
[ -z "$BANK" ] && BANK=$(J POST /bank-accounts "{\"account_name\":\"BCA Operasional\",\"account_number\":\"1111222233\",\"bank_name\":\"Bank BCA\",\"account_type\":\"bank\",\"opening_balance\":20000000,\"opening_date\":\"$D_OPENING\",\"is_default\":true}" | gid)
echo "BANK=$BANK"
BANK_COA=$(PSQL "SELECT coa_id FROM bank_accounts WHERE id='$BANK';")
echo "BANK_COA=$BANK_COA"

# Warehouse — reuse signup default if present, else create
WH=$(PSQL "SELECT id FROM warehouses WHERE tenant_id='$TEN' ORDER BY created_at LIMIT 1;")
[ -z "$WH" ] && WH=$(J POST /warehouses '{"name":"Gudang Utama","code":"WH-01","is_default":true}' | gid)
echo "WH=$WH"

# Vendor
VND=$(PSQL "SELECT id FROM vendors WHERE tenant_id='$TEN' AND code='VND-KB-01';")
[ -z "$VND" ] && VND=$(J POST /vendors '{"code":"VND-KB-01","name":"PT Grosir Kaos","contact_person":"Andi","phone":"021-5559000","email":"sales@grosirkaos.co.id","address":"Jl. Grosir 1","city":"Bandung","province":"Jawa Barat","postal_code":"40111","payment_terms_days":30}' | gid)
echo "VND=$VND"

# Customer — NOTE: the /customers endpoint does NOT persist `code` (Bahasa Indonesia schema:
# it stores `nama`, leaves `code`/`name` NULL), so idempotency keys on the persisted `email`,
# not code. (vendors/items DO keep code; customers are the exception — verified in DB.)
CUS=$(PSQL "SELECT id FROM customers WHERE tenant_id='$TEN' AND email='order@tokomerdeka.co.id' AND deleted_at IS NULL;")
[ -z "$CUS" ] && CUS=$(J POST /customers '{"code":"CUS-KB-01","name":"Toko Merdeka","company_name":"Toko Merdeka","contact_person":"Rina","phone":"022-7770001","email":"order@tokomerdeka.co.id","address":"Jl. Merdeka 5","city":"Jakarta","province":"DKI Jakarta","postal_code":"10110","payment_terms_days":30}' | gid)
echo "CUS=$CUS"

# Item — Kaos Biru 30s (trading FG: buy 35.000, sell 50.000)
ITEM=$(PSQL "SELECT id FROM products WHERE tenant_id='$TEN' AND item_code='FG-KAOS-BIRU-30S';")
[ -z "$ITEM" ] && ITEM=$(J POST /items '{"name":"Kaos Biru 30s","item_type":"goods","track_inventory":true,"base_unit":"pcs","item_code":"FG-KAOS-BIRU-30S","kategori":"Barang Jadi","purchase_price":35000,"sales_price":50000,"for_sales":true,"for_purchases":true}' | gid)
echo "ITEM=$ITEM"

# ---------------------------------------------------------------------------
# PERSIST STATE  (GITIGNORED — contains PASS + a live JWT; ephemeral, regenerated
# every run via login-first. Credentials themselves are documented as EMAIL/PASS
# defaults in this script's header, so the committed script fully reproduces state.)
# ---------------------------------------------------------------------------
# UPSERT each var — never truncate (a re-run of step_-1 must NOT wipe downstream step IDs
# like QID/SOID/DEPID/INVID that steps 1-5 append to this file).
[ -f "$STATE" ] || printf '# DP-flow harness state — step_-1_provision.sh %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$STATE"
set_state(){ local k=$1 v=$2; grep -q "^export $k=" "$STATE" 2>/dev/null && sed -i "s|^export $k=.*|export $k=\"$v\"|" "$STATE" || echo "export $k=\"$v\"" >> "$STATE"; }
set_state B "$B"; set_state DB "$DB"; set_state CONTAINER "$CONTAINER"
set_state EMAIL "$EMAIL"; set_state PASS "$PASS"; set_state TEN "$TEN"; set_state TOK "$TOK"
set_state BANK "$BANK"; set_state BANK_COA "$BANK_COA"; set_state WH "$WH"
set_state VND "$VND"; set_state CUS "$CUS"; set_state ITEM "$ITEM"
echo; echo "state -> $STATE (upsert, downstream IDs preserved)"

# ---------------------------------------------------------------------------
# COMPLETENESS GATE
# ---------------------------------------------------------------------------
for kv in "BANK=$BANK" "BANK_COA=$BANK_COA" "WH=$WH" "VND=$VND" "CUS=$CUS" "ITEM=$ITEM"; do
  [ -z "${kv#*=}" ] && { echo "!!! master data incomplete: $kv — ABORT"; exit 1; }
done
echo "PROVISION OK — all master data present."

echo; echo "--- spec-precondition assertions (non-PKP + delivery mode) ---"
ISPKP=$(PSQL "SELECT is_pkp FROM \"Tenant\" WHERE id='$TEN';")
POLICY=$(PSQL "SELECT revenue_recognition_policy FROM tenant_config WHERE tenant_id='$TEN';")
OPENBAL=$(PSQL "SELECT COALESCE(opening_balance,0)::bigint FROM bank_accounts WHERE id='$BANK';")
aeq "tenant is_pkp = false (non-PKP spec)" "$ISPKP" "f"
aeq "revenue_recognition_policy = delivery" "$POLICY" "delivery"
aeq "bank opening_balance = 20.000.000" "$OPENBAL" "20000000"
finish
