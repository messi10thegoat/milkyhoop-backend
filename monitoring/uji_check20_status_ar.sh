# Uji Check 20 (5 Okt 2026): (a) nyata kaos+grapgrap harus 0, (b) kontrol merah BEGIN..ROLLBACK harus 1, (c) logika fungsi.
# Pemakaian: bash monitoring/uji_check20_status_ar.sh [path accounting_health_check.sh]. Nol tulis (rollback).
F=${1:-/root/milkyhoop-dev/monitoring/accounting_health_check.sh}
sed -n '/^check_20_ar_status_turunan() {/,/^}/p' $F > /tmp/c20.sh
SQL=$(python3 - <<'P'
import re
s=open('/tmp/c20.sh').read()
m=re.search(r'cnt=\$\(psql_cmd "(.*?)"\)\n', s, re.S); print(m.group(1))
P
)
echo "== (a) nyata"
for T in kaos-biru-konveksi grapgrap-manado; do
  q=${SQL//\$tenant/$T}
  echo "$T: $(docker exec milkyhoop-dev-postgres-1 psql -U postgres -d milkydb -At -c "$q")"
done
echo "== (b) kontrol merah (rollback)"
T=kaos-biru-konveksi; q=${SQL//\$tenant/$T}
printf "BEGIN;\nUPDATE sales_invoices SET status='paid' WHERE id=(SELECT si.id FROM sales_invoices si WHERE si.tenant_id='%s' AND si.status='partial' AND si.id IN (SELECT invoice_id FROM compute_ar_outstanding('%s') WHERE outstanding>0) LIMIT 1);\n%s\nROLLBACK;\n" "$T" "$T" "$q" > /tmp/c20k.sql
docker cp /tmp/c20k.sql milkyhoop-dev-postgres-1:/tmp/c20k.sql && docker exec milkyhoop-dev-postgres-1 psql -U postgres -d milkydb -At -f /tmp/c20k.sql | sed -n 3p
echo "== (c) logika fungsi"
detail() { :; }
. /tmp/c20.sh
for out in 0 3 __GAGAL__ "ERROR: x"; do psql_cmd() { echo "$out"; }; check_20_ar_status_turunan t; echo "keluaran=[$out] -> CHK_PASS=$CHK_PASS DETAIL=[$CHK_DETAIL]"; done
