#!/usr/bin/env bash
# Gerbang V315 (SO selesai otomatis). DB scratch milkydb_v315 = skema + data SO/faktur/Surat Jalan/produk prod
# (TIDAK menulis ke milkydb; data dimuat --disable-triggers). Terapkan V315 DUA kali lalu uji kejadian nyata.
#   gate_v315.sh <V315.sql> <V315_ROLLBACK.sql>  -> LULUS exit 0
#   gate_v315.sh                                  -> KONTROL MERAH: tanpa migrasi, pemeriksaan HARUS GAGAL (exit 1)
set -u
P=milkyhoop-dev-postgres-1; DB=milkydb_v315; MIG=${1:-}; RB=${2:-}
q() { docker exec -i "$P" psql -U postgres -v ON_ERROR_STOP=1 -At "$@"; }
bersih() { q -d postgres -qc "DROP DATABASE IF EXISTS $DB" >/dev/null 2>&1; }
trap bersih EXIT
bersih; q -d postgres -qc "CREATE DATABASE $DB" >/dev/null || exit 2
docker exec "$P" sh -c "pg_dump -U postgres -s milkydb | psql -U postgres -d $DB -q -o /dev/null" 2>/dev/null
T="-t public.\"Tenant\" -t public.sales_orders -t public.sales_order_items -t public.products -t public.sales_invoices -t public.sales_invoice_items -t public.invoice_fulfillments -t public.invoice_fulfillment_items"
docker exec "$P" sh -c "pg_dump -U postgres --data-only --disable-triggers $T milkydb | psql -U postgres -d $DB -q -o /dev/null" || exit 2
q -d "$DB" -c "CREATE TEMP TABLE x AS SELECT 1" >/dev/null   # sanity
q -d "$DB" -c "CREATE TABLE _pra AS SELECT id, status FROM sales_orders" >/dev/null
GAGAL=0; periksa() { if [ "$2" = "$3" ]; then echo "  OK   $1 ($2)"; else echo "  GAGAL $1: dapat [$2] harap [$3]"; GAGAL=1; fi; }
if [ -n "$MIG" ]; then
  for i in 1 2; do docker exec -i "$P" psql -U postgres -d "$DB" -v ON_ERROR_STOP=1 -q -1 < "$MIG" >/tmp/gate_v315_$i.log 2>&1 || { echo "GAGAL: migrasi putaran $i"; tail -5 /tmp/gate_v315_$i.log; exit 1; }; done
  echo "migrasi diterapkan 2x (idempoten)"
fi
echo "== pemeriksaan"
periksa "migrasi tak mengubah status" "$(q -d $DB -c "SELECT count(*) FROM sales_orders s JOIN _pra p USING(id) WHERE s.status IS DISTINCT FROM p.status" 2>&1)" "0"
periksa "completed lama = manual" "$(q -d $DB -c "SELECT count(*) FROM sales_orders WHERE status='completed' AND completed_source='manual'" 2>&1)" "$(q -d $DB -c "SELECT count(*) FROM sales_orders WHERE status='completed'")"
# sensus INDEPENDEN (kueri terpisah dari fungsi) vs dry-run fungsi
SENSUS="WITH b AS (SELECT so.tenant_id t, so.id, soi.quantity q, COALESCE(p.track_inventory,false) stok,
 COALESCE((SELECT SUM(sii.quantity) FROM sales_invoice_items sii JOIN sales_invoices si ON si.id=sii.invoice_id WHERE sii.sales_order_item_id=soi.id AND si.status NOT IN ('draft','void')),0) tg,
 COALESCE((SELECT SUM(ifi.quantity) FROM sales_invoice_items sii JOIN invoice_fulfillment_items ifi ON ifi.invoice_item_id=sii.id JOIN invoice_fulfillments f ON f.id=ifi.fulfillment_id AND f.voided_at IS NULL AND f.status<>'voided' WHERE sii.sales_order_item_id=soi.id),0) kr
 FROM sales_orders so JOIN sales_order_items soi ON soi.sales_order_id=so.id LEFT JOIN products p ON p.id=soi.item_id
 WHERE so.status NOT IN ('draft','cancelled','completed')),
 t AS (SELECT DISTINCT si.sales_order_id id FROM sales_invoices si JOIN sales_invoice_items sii ON sii.invoice_id=si.id WHERE si.status NOT IN ('draft','void') AND COALESCE(sii.allocated_amount,0)-COALESCE(sii.recognized_amount,0)>0.005 AND si.sales_order_id IS NOT NULL)
 SELECT count(*) FROM (SELECT t, id FROM b GROUP BY 1,2 HAVING bool_and(tg>=q AND (NOT stok OR kr>=q))) z WHERE z.t='__T__' AND z.id NOT IN (SELECT id FROM t)"
for TN in grapgrap-manado kaos-biru-konveksi; do
  ind=$(q -d $DB -c "${SENSUS//__T__/$TN}" 2>&1)
  fn=$(q -d $DB -c "SELECT count(*) FROM so_selesai_otomatis_backfill('$TN', false)" 2>&1)
  periksa "dry-run $TN == sensus independen" "$fn" "$ind"
done
# kejadian: SO grapgrap yang memenuhi syarat -> sentuh baris (pemicu trigger lama) -> completed auto + audit
SO=$(q -d $DB -c "SELECT so_id FROM so_selesai_otomatis_backfill('grapgrap-manado', false) LIMIT 1" 2>/dev/null)
periksa "ada SO uji" "$([ -n "$SO" ] && echo ya || echo tidak)" "ya"
q -d $DB -c "UPDATE sales_order_items SET quantity_invoiced = quantity_invoiced WHERE sales_order_id='$SO'" >/dev/null 2>&1
periksa "kejadian -> completed auto" "$(q -d $DB -c "SELECT status||'/'||coalesce(completed_source,'-') FROM sales_orders WHERE id='$SO'" 2>&1)" "completed/auto"
periksa "audit AUTO_COMPLETED" "$(q -d $DB -c "SELECT count(*) FROM audit_logs WHERE entity_id='$SO' AND \"eventType\"='SALES_ORDER_AUTO_COMPLETED' AND tenant_id='grapgrap-manado'" 2>&1)" "1"
# void faktur -> dibuka kembali + audit
INV=$(q -d $DB -c "SELECT id FROM sales_invoices WHERE sales_order_id='$SO' AND status NOT IN ('draft','void') LIMIT 1" 2>/dev/null)
q -d $DB -c "ALTER TABLE sales_invoices DISABLE TRIGGER trg_law19_bekukan_nominal; UPDATE sales_invoices SET status='void' WHERE id='$INV'; ALTER TABLE sales_invoices ENABLE TRIGGER trg_law19_bekukan_nominal" >/dev/null 2>&1
periksa "void faktur -> dibuka kembali" "$(q -d $DB -c "SELECT (status<>'completed' AND completed_source IS NULL)::text FROM sales_orders WHERE id='$SO'" 2>&1)" "true"
periksa "audit REOPENED" "$(q -d $DB -c "SELECT count(*) FROM audit_logs WHERE entity_id='$SO' AND \"eventType\"='SALES_ORDER_REOPENED'" 2>&1)" "1"
# manual = terminal
MAN=$(q -d $DB -c "SELECT id FROM sales_orders WHERE status='completed' LIMIT 1" 2>/dev/null)
q -d $DB -c "UPDATE sales_order_items SET quantity_invoiced = 0 WHERE sales_order_id='$MAN'" >/dev/null 2>&1
periksa "completed manual tak dibuka" "$(q -d $DB -c "SELECT status FROM sales_orders WHERE id='$MAN'" 2>&1)" "completed"
# pendapatan tertahan: SO memenuhi tagih+kirim tapi ada sisa pengakuan -> TIDAK selesai; diakui -> selesai
TH=$(q -d $DB -c "SELECT si.sales_order_id FROM sales_invoices si JOIN sales_invoice_items sii ON sii.invoice_id=si.id JOIN sales_orders so ON so.id=si.sales_order_id WHERE so.tenant_id='grapgrap-manado' AND so.status='invoiced' AND si.status NOT IN ('draft','void') AND COALESCE(sii.allocated_amount,0)-COALESCE(sii.recognized_amount,0)>0.005 LIMIT 1" 2>/dev/null)
if [ -n "$TH" ]; then
  q -d $DB -c "UPDATE sales_order_items SET quantity_invoiced = quantity_invoiced WHERE sales_order_id='$TH'" >/dev/null 2>&1
  periksa "pendapatan tertahan -> TIDAK selesai" "$(q -d $DB -c "SELECT status FROM sales_orders WHERE id='$TH'" 2>&1)" "invoiced"
  q -d $DB -c "ALTER TABLE sales_invoice_items DISABLE TRIGGER trg_law19_baris_beku; UPDATE sales_invoice_items sii SET recognized_amount=allocated_amount FROM sales_invoices si WHERE si.id=sii.invoice_id AND si.sales_order_id='$TH'; ALTER TABLE sales_invoice_items ENABLE TRIGGER trg_law19_baris_beku" >/dev/null 2>&1
  periksa "pengakuan -> selesai auto" "$(q -d $DB -c "SELECT status||'/'||coalesce(completed_source,'-') FROM sales_orders WHERE id='$TH'" 2>&1)" "completed/auto"
else echo "  CATAT: tak ada SO grapgrap bertahan-pendapatan di salinan (dilewati)"; fi
# baris stok belum terkirim: SO kaos invoiced dengan baris stok kurang kirim -> tidak selesai
ST=$(q -d $DB -c "SELECT so.id FROM sales_orders so WHERE so.tenant_id='kaos-biru-konveksi' AND so.status='invoiced' AND NOT so_memenuhi_selesai(so.id) LIMIT 1" 2>/dev/null)
if [ -n "$ST" ]; then
  q -d $DB -c "UPDATE sales_order_items SET quantity_invoiced = quantity_invoiced WHERE sales_order_id='$ST'" >/dev/null 2>&1
  periksa "stok belum terkirim -> tetap invoiced" "$(q -d $DB -c "SELECT status FROM sales_orders WHERE id='$ST'" 2>&1)" "invoiced"
else periksa "ada SO stok-belum-kirim" "tidak" "ya"; fi
if [ -n "$RB" ]; then
  docker exec -i "$P" psql -U postgres -d "$DB" -v ON_ERROR_STOP=1 -q -1 < "$RB" >/tmp/gate_v315_rb.log 2>&1 || { echo "GAGAL: rollback"; tail -3 /tmp/gate_v315_rb.log; GAGAL=1; }
  periksa "rollback: fungsi baru hilang" "$(q -d $DB -c "SELECT count(*) FROM pg_proc WHERE proname IN ('terapkan_selesai_so','so_memenuhi_selesai','so_selesai_otomatis_backfill')")" "0"
  periksa "rollback: trigger lama tanpa PERFORM" "$(q -d $DB -c "SELECT (pg_get_functiondef('update_sales_order_status'::regproc) LIKE '%terapkan_selesai_so%')::text")" "false"
fi
[ $GAGAL = 0 ] && { echo "LULUS: gate V315"; exit 0; } || { echo "DITOLAK: gate V315"; exit 1; }
