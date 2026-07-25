#!/bin/bash
# Consolidated golden path — hardened-recipe DB gate (2026-07-25)
# Tenant konveksi-cemerlang, HTTP via gateway :8001, single continuous run.
set -u
B=http://localhost:8001/api
EMAIL="owner@konveksicemerlang.co.id"
DB=milkydb
PSQL()  { docker exec -i milkyhoop-dev-postgres-1 psql -U postgres -d $DB -tAc "$1" | tr -d '[:space:]'; }
PSQLm() { docker exec -i milkyhoop-dev-postgres-1 psql -U postgres -d $DB -c "$1"; }
gid()   { python3 -c "import sys,json;d=json.load(sys.stdin);print((d.get('data') or d).get('id',''))" 2>/dev/null; }
hdr()   { echo; echo "############################## $* ##############################"; }

hdr "STEP 1 — SIGNUP"
curl -s -X POST "$B/auth/signup/register" -H "Content-Type: application/json" -d "{\"email\":\"$EMAIL\"}" | head -c 160; echo
TOKm=$(PSQL "SELECT magic_token FROM pending_registrations WHERE email='$EMAIL' ORDER BY created_at DESC LIMIT 1;")
LOC=$(curl -s -i "$B/auth/signup/verify-link/$TOKm" 2>&1 | grep -i '^location' | tr -d '\r' | sed 's/^[Ll]ocation: //')
ST=$(echo "$LOC" | sed -n 's/.*token=\([^&]*\).*/\1/p')
R=$(curl -s -X POST "$B/auth/signup/complete-setup" -H "Authorization: Bearer $ST" -H "Content-Type: application/json" \
  -d '{"password":"Cemerlang2026!","business_name":"Konveksi Cemerlang"}')
TOK=$(echo "$R" | python3 -c "import sys,json;print(json.load(sys.stdin).get('data',{}).get('access_token',''))")
TEN=$(echo "$R" | python3 -c "import sys,json;print(json.load(sys.stdin).get('data',{}).get('tenant_id',''))")
echo "TENANT=$TEN  token=${TOK:0:14}..."
H=(-H "Authorization: Bearer $TOK" -H "X-Tenant-Slug: $TEN" -H "Content-Type: application/json")
J() { local m=$1 p=$2 d=${3:-'{}'}; curl -s -X "$m" "$B$p" "${H[@]}" -d "$d"; }
echo "SEED: CoA=$(PSQL "SELECT count(*) FROM chart_of_accounts WHERE tenant_id='$TEN';") roles=$(PSQL "SELECT count(*) FROM account_roles WHERE tenant_id='$TEN';") tax=$(PSQL "SELECT count(*) FROM tax_codes WHERE tenant_id='$TEN';")"
[ "$TEN" = "konveksi-cemerlang" ] || { echo "!!! TENANT SLUG != konveksi-cemerlang (invariant SQL hardcodes it) — ABORT"; exit 1; }

hdr "STEP 2 — MASTER DATA"
PG=$(J POST /pay-groups '{"name":"Operator Produksi","description":"Grup gaji produksi","is_default":true}' | gid); echo "PG=$PG"
VND=$(J POST /vendors '{"code":"VND-001","name":"PT Tekstil Nusantara","contact_person":"Hartono","phone":"021-5551234","email":"sales@tekstilnusantara.co.id","address":"Jl. Industri Raya 45","city":"Bandung","province":"Jawa Barat","postal_code":"40292","tax_id":"01.234.567.8-054.000","payment_terms_days":30}' | gid); echo "VND=$VND"
CUS=$(J POST /customers '{"code":"CUS-001","name":"CV Sinar Harapan","company_name":"CV Sinar Harapan","contact_person":"Ratna","phone":"022-7778899","email":"order@sinarharapan.co.id","address":"Jl. Merdeka 12","city":"Jakarta Selatan","province":"DKI Jakarta","postal_code":"12190","tax_id":"02.345.678.9-013.000","payment_terms_days":30}' | gid); echo "CUS=$CUS"
RAW=$(J POST /items '{"name":"Kain Katun Combed 30s","item_type":"goods","track_inventory":true,"base_unit":"meter","item_code":"RAW-KTN-30S","kategori":"Bahan Baku","purchase_price":45000,"sales_price":0,"for_sales":false,"for_purchases":true}' | gid); echo "RAW=$RAW"
FG=$(J POST /items '{"name":"Kaos Polos Combed 30s","item_type":"goods","track_inventory":true,"base_unit":"pcs","item_code":"FG-KAOS-30S","kategori":"Barang Jadi","sales_price":85000,"purchase_price":0,"for_sales":true,"for_purchases":false}' | gid); echo "FG=$FG"
# warehouse: prefer existing (signup default), else create
WH=$(PSQL "SELECT id FROM warehouses WHERE tenant_id='$TEN' ORDER BY created_at LIMIT 1;")
[ -z "$WH" ] && WH=$(J POST /warehouses '{"name":"Gudang Utama","code":"WH-01","is_default":true}' | gid)
echo "WH=$WH"
# work center: try API then SQL fallback
WC=$(J POST /bom/work-centers '{"code":"WC-JAHIT","name":"Lini Jahit","labor_rate_per_hour":15000,"overhead_rate_per_hour":5000}' | gid)
[ -z "$WC" ] && WC=$(PSQL "INSERT INTO work_centers (tenant_id,code,name,labor_rate_per_hour,overhead_rate_per_hour,is_active) VALUES ('$TEN','WC-JAHIT','Lini Jahit',15000,5000,true) RETURNING id;")
echo "WC=$WC"
BOM=$(J POST /bom "{\"product_id\":\"$FG\",\"bom_code\":\"BOM-KAOS-01\",\"bom_name\":\"BoM Kaos\",\"output_quantity\":1,\"output_unit\":\"pcs\",\"estimated_time_minutes\":20,\"work_center_id\":\"$WC\",\"components\":[{\"component_product_id\":\"$RAW\",\"quantity\":1.5,\"unit\":\"meter\",\"unit_cost\":45000,\"sequence_order\":1}]}" | gid); echo "BOM=$BOM"
J POST /bom/$BOM/activate '{}' >/dev/null 2>&1
E1=$(J POST /employees "{\"name\":\"Budi Santoso\",\"pay_group_id\":\"$PG\",\"employee_code\":\"EMP-001\",\"position\":\"Operator Jahit\",\"department\":\"Produksi\",\"email\":\"budi@konveksicemerlang.co.id\",\"nik\":\"3273010101900001\",\"npwp\":\"09.876.543.2-054.000\",\"ptkp_status\":\"K1\",\"tax_method\":\"gross\",\"employee_type\":\"tetap\",\"join_date\":\"2024-01-15\",\"is_bpjs_kes\":true,\"is_bpjs_jht\":true,\"is_bpjs_jp\":true,\"jkk_risk_level\":2}" | gid); echo "E1=$E1"
E2=$(J POST /employees "{\"name\":\"Siti Rahayu\",\"pay_group_id\":\"$PG\",\"employee_code\":\"EMP-002\",\"position\":\"Operator Potong\",\"department\":\"Produksi\",\"email\":\"siti@konveksicemerlang.co.id\",\"nik\":\"3273014505920002\",\"ptkp_status\":\"TK0\",\"tax_method\":\"gross\",\"employee_type\":\"tetap\",\"join_date\":\"2024-03-01\",\"is_bpjs_kes\":true,\"is_bpjs_jht\":true,\"is_bpjs_jp\":true,\"jkk_risk_level\":2}" | gid); echo "E2=$E2"
BANK=$(J POST /bank-accounts '{"account_name":"BCA Operasional","account_number":"1234567890","bank_name":"Bank BCA","account_type":"bank","opening_balance":50000000,"is_default":true}' | gid); echo "BANK=$BANK"
PPN_IN=$(PSQL "SELECT id FROM tax_codes WHERE tenant_id='$TEN' AND code='PPN-11-IN';")
PPN_OUT=$(PSQL "SELECT id FROM tax_codes WHERE tenant_id='$TEN' AND code='PPN-11-OUT';")
echo "PPN_IN=$PPN_IN PPN_OUT=$PPN_OUT"
echo "master check: VND=$VND CUS=$CUS RAW=$RAW FG=$FG WH=$WH WC=$WC BOM=$BOM E1=$E1 E2=$E2 BANK=$BANK"
[ -n "$VND$CUS$RAW$FG$WH$WC$BOM$E1$E2$BANK$PPN_IN$PPN_OUT" ] || { echo "!!! master data incomplete — ABORT"; exit 1; }

hdr "STEP 3 — BILL + PAYMENT (PPN 11%, 100m@45000)"
BILL=$(J POST /bills/v2 "{\"vendor_id\":\"$VND\",\"issue_date\":\"2026-07-05\",\"due_date\":\"2026-08-04\",\"ref_no\":\"INV-TN-0912\",\"tax_rate\":11,\"tax_code_id\":\"$PPN_IN\",\"tax_inclusive\":false,\"status\":\"draft\",\"notes\":\"Pembelian kain\",\"items\":[{\"product_id\":\"$RAW\",\"product_name\":\"Kain Katun Combed 30s\",\"qty\":100,\"unit\":\"meter\",\"price\":45000}]}" | gid); echo "BILL=$BILL"
J POST /bills/$BILL/post '{}' | head -c 100; echo " <- post"
BTOT=$(PSQL "SELECT COALESCE(grand_total,0)::bigint FROM bills WHERE id='$BILL';"); echo "grand_total=$BTOT (expect 49950000)"
J POST /bill-payments "{\"vendor_id\":\"$VND\",\"payment_date\":\"2026-07-10\",\"payment_method\":\"bank_transfer\",\"bank_account_id\":\"$BANK\",\"total_amount\":$BTOT,\"reference_number\":\"TRF-0710\",\"allocations\":[{\"bill_id\":\"$BILL\",\"amount_applied\":$BTOT}]}" | head -c 100; echo " <- pay"
PSQLm "SELECT je.source_type, c.account_code, LEFT(c.name,22) akun, jl.debit, jl.credit FROM journal_entries je JOIN journal_lines jl ON jl.journal_id=je.id JOIN chart_of_accounts c ON c.id=jl.account_id WHERE je.tenant_id='$TEN' AND je.source_type IN ('BILL','PAYMENT_MADE','PAYMENT_BILL') ORDER BY je.chain_sequence, jl.line_number;"

hdr "STEP 4 — MANUFACTURING (WO 50pcs, issue 75m, labor 10h, output 50)"
WO=$(J POST /production "{\"product_id\":\"$FG\",\"bom_id\":\"$BOM\",\"planned_quantity\":50,\"unit\":\"pcs\",\"work_center_id\":\"$WC\",\"warehouse_id\":\"$WH\",\"planned_start_date\":\"2026-07-12\",\"planned_end_date\":\"2026-07-15\",\"notes\":\"Batch 1\"}" | gid); echo "WO=$WO"
J POST /production/$WO/release '{}' | head -c 80; echo " <- release"
J POST /production/$WO/issue-materials "[{\"product_id\":\"$RAW\",\"quantity\":75,\"unit\":\"meter\",\"warehouse_id\":\"$WH\",\"posting_date\":\"2026-07-12\"}]" | head -c 120; echo " <- issue"
J POST /production/$WO/labor "{\"operation_name\":\"Jahit\",\"actual_hours\":10,\"worker_name\":\"Budi Santoso\",\"posting_date\":\"2026-07-13\"}" | head -c 120; echo " <- labor"
J POST /production/$WO/report-output "{\"good_quantity\":50,\"scrap_quantity\":0,\"quality_status\":\"passed\",\"warehouse_id\":\"$WH\",\"posting_date\":\"2026-07-15\"}" | head -c 120; echo " <- output"
echo "--- WIP 1-10650 net (expect 0) + FG WAC (expect 71500/pcs) ---"
PSQL "SELECT COALESCE(SUM(jl.debit-jl.credit),0) FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id JOIN chart_of_accounts c ON c.id=jl.account_id WHERE je.tenant_id='$TEN' AND je.status='POSTED' AND c.account_code='1-10650';" | sed 's/^/WIP_net=/'
PSQL "SELECT ROUND(average_cost) FROM inventory_ledger WHERE tenant_id='$TEN' AND product_id='$FG' ORDER BY movement_date DESC, created_at DESC LIMIT 1;" | sed 's/^/FG_WAC=/'

hdr "STEP 5 — PAYROLL (BPJS multi-line)"
GAJI=$(J POST /salary-components '{"code":"BASIC","name":"Gaji Pokok","type":"earning","category":"basic","is_taxable":true,"is_fixed":true,"default_amount":5000000,"calculation_method":"fixed","sort_order":1}' | gid); echo "GAJI=$GAJI"
J PUT /payroll-config/bpjs '{"configs":[{"component":"kes","employer_rate":4.0,"employee_rate":1.0,"ceiling_amount":12000000,"effective_date":"2026-07-01"},{"component":"jht","employer_rate":3.7,"employee_rate":2.0,"ceiling_amount":0,"effective_date":"2026-07-01"},{"component":"jp","employer_rate":2.0,"employee_rate":1.0,"ceiling_amount":9559600,"effective_date":"2026-07-01"}]}' | head -c 80; echo " <- bpjs"
J PUT /employees/$E1/salary-config "{\"configs\":[{\"component_id\":\"$GAJI\",\"amount\":5000000,\"effective_date\":\"2026-07-01\"}]}" | head -c 70; echo " <- E1 salary"
J PUT /employees/$E2/salary-config "{\"configs\":[{\"component_id\":\"$GAJI\",\"amount\":4000000,\"effective_date\":\"2026-07-01\"}]}" | head -c 70; echo " <- E2 salary"
RUN=$(J POST /payroll "{\"period_start\":\"2026-07-01\",\"period_end\":\"2026-07-31\",\"payment_date\":\"2026-07-31\",\"employee_ids\":[\"$E1\",\"$E2\"],\"payment_method\":\"bank_transfer\",\"bank_account_id\":\"$BANK\"}" | gid); echo "RUN=$RUN"
J POST /payroll/$RUN/calculate '{}' | head -c 80; echo " <- calc"
J POST /payroll/$RUN/submit '{}' | head -c 60; echo " <- submit"
J POST /payroll/$RUN/approve '{}' | head -c 60; echo " <- approve"
J POST /payroll/$RUN/post '{}' | head -c 100; echo " <- post"
PSQLm "SELECT c.account_code, LEFT(c.name,26) akun, jl.debit, jl.credit FROM journal_entries je JOIN journal_lines jl ON jl.journal_id=je.id JOIN chart_of_accounts c ON c.id=jl.account_id WHERE je.tenant_id='$TEN' AND je.source_type ILIKE '%PAYROLL%' ORDER BY je.journal_number, jl.line_number;"

hdr "STEP 6 — SALES INVOICE PSAK-72 3-EVENT (30pcs@85000, PPN 11%)"
INV=$(J POST /sales-invoices "{\"customer_id\":\"$CUS\",\"customer_name\":\"CV Sinar Harapan\",\"invoice_date\":\"2026-07-18\",\"due_date\":\"2026-08-17\",\"recognize_at\":\"delivery\",\"tax_rate\":11,\"auto_post\":false,\"items\":[{\"item_id\":\"$FG\",\"description\":\"Kaos Polos Combed 30s\",\"quantity\":30,\"unit\":\"pcs\",\"unit_price\":85000,\"tax_rate\":11,\"tax_code_id\":\"$PPN_OUT\"}]}" | gid); echo "INV=$INV"
J POST /sales-invoices/$INV/post '{}' | head -c 100; echo " <- post (Event1: Dr AR/Cr Deferred+PPN)"
IIID=$(PSQL "SELECT id FROM sales_invoice_items WHERE invoice_id='$INV' LIMIT 1;"); echo "invoice_item=$IIID"
J POST /sales-invoices/$INV/fulfill "{\"warehouse_id\":\"$WH\",\"fulfillment_date\":\"2026-07-20\",\"recognize_revenue\":true,\"items\":[{\"invoice_item_id\":\"$IIID\",\"quantity\":30}]}" | head -c 120; echo " <- fulfill (Event2 COGS + Event3 recognize)"
STOT=$(PSQL "SELECT COALESCE(total_amount,0)::bigint FROM sales_invoices WHERE id='$INV';"); echo "invoice total_amount=$STOT (expect 2830500)"
BANK_COA=$(PSQL "SELECT coa_id FROM bank_accounts WHERE id='$BANK';")
J POST /sales-invoices/$INV/payments "{\"payment_date\":\"2026-07-25\",\"payment_method\":\"transfer\",\"account_id\":\"$BANK_COA\",\"bank_account_id\":\"$BANK\",\"amount\":$STOT,\"reference_number\":\"RCV-01\"}" | head -c 120; echo " <- pelunasan"
PSQLm "SELECT je.source_type, c.account_code, LEFT(c.name,22) akun, jl.debit, jl.credit FROM journal_entries je JOIN journal_lines jl ON jl.journal_id=je.id JOIN chart_of_accounts c ON c.id=jl.account_id WHERE je.tenant_id='$TEN' AND je.source_type IN ('INVOICE','INVOICE_FULFILLMENT','INVOICE_REVENUE','SALES_INVOICE_COGS') ORDER BY je.chain_sequence, jl.line_number;"

hdr "STEP 7 — EXPENSE + BANK TRANSFER (BANK_FEE) + MANUAL JV"
LISTRIK=$(PSQL "SELECT id FROM chart_of_accounts WHERE tenant_id='$TEN' AND account_code='5-20300';")
J POST /expenses "{\"expense_date\":\"2026-07-22\",\"paid_through_id\":\"$BANK\",\"account_id\":\"$LISTRIK\",\"amount\":750000,\"notes\":\"Listrik Juli\"}" | head -c 110; echo " <- expense"
BANK2=$(J POST /bank-accounts '{"account_name":"Kas Kecil","account_type":"petty_cash","opening_balance":0}' | gid); echo "BANK2=$BANK2"
J POST /bank-transfers "{\"from_bank_id\":\"$BANK\",\"to_bank_id\":\"$BANK2\",\"amount\":5000000,\"fee_amount\":6500,\"transfer_date\":\"2026-07-23\",\"ref_no\":\"TRF-INT-01\",\"auto_post\":true}" | head -c 140; echo " <- transfer+fee"
BL=$(PSQL "SELECT id FROM chart_of_accounts WHERE tenant_id='$TEN' AND account_code='5-20900';")
KK=$(PSQL "SELECT coa_id FROM bank_accounts WHERE id='$BANK2';")
J POST /journals "{\"entry_date\":\"2026-07-24\",\"description\":\"Koreksi biaya admin kecil\",\"lines\":[{\"account_id\":\"$BL\",\"debit\":25000,\"credit\":0,\"description\":\"beban lain\"},{\"account_id\":\"$KK\",\"debit\":0,\"credit\":25000,\"description\":\"kas kecil\"}]}" | head -c 110; echo " <- manual JV"
echo "--- BANK_FEE 5-20850 (expect 6500) ---"
PSQL "SELECT COALESCE(SUM(jl.debit),0) FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id JOIN chart_of_accounts c ON c.id=jl.account_id WHERE je.tenant_id='$TEN' AND je.status='POSTED' AND c.account_code='5-20850';" | sed 's/^/BANK_FEE=/'

hdr "STEP 8 — MFG RECONCILE + PERIOD CLOSE + LOCK TEST"
J POST /production/month-end-reconcile '{"period":"2026-07"}' | head -c 160; echo " <- reconcile"
PID=$(PSQL "SELECT id FROM fiscal_periods WHERE tenant_id='$TEN' AND start_date<='2026-07-15' AND end_date>='2026-07-15' LIMIT 1;"); echo "PERIOD=$PID"
J POST /periods/$PID/close '{"closing_notes":"Tutup buku Juli 2026 golden path","force":true}' | head -c 200; echo " <- close"
echo "period status: $(PSQL "SELECT status FROM fiscal_periods WHERE id='$PID';")"
echo "--- period lock test: backdate JV to Juli MUST be rejected ---"
J POST /journals "{\"entry_date\":\"2026-07-15\",\"description\":\"Uji period lock (harus gagal)\",\"lines\":[{\"account_id\":\"$BL\",\"debit\":1000,\"credit\":0},{\"account_id\":\"$KK\",\"debit\":0,\"credit\":1000}]}" | head -c 200; echo

hdr "DONE — running invariant gate next"
echo "TENANT=$TEN"
