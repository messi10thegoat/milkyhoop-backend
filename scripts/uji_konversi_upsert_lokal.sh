#!/usr/bin/env bash
# BUG-373: uji LOKAL (Postgres sementara di Mac, BUKAN produksi) pernyataan UPSERT konversi yang DIAMBIL dari items.py (tak ditulis ulang).
# SABOTASE=polos: ganti UPSERT dengan INSERT polos (perilaku lama) -> harus MERAH.
set -u
PGBIN=/opt/homebrew/opt/postgresql@16/bin; D=$(mktemp -d); PORT=54998; ITEMS="$(cd "$(dirname "$0")/../backend/api_gateway/app/routers" && pwd)/items.py"
$PGBIN/initdb -D $D/d -U u -A trust >/dev/null || exit 2
$PGBIN/pg_ctl -D $D/d -o "-p $PORT -k $D" -l $D/log -w start >/dev/null || exit 2
trap "$PGBIN/pg_ctl -D $D/d -m immediate stop >/dev/null 2>&1; rm -rf $D" EXIT
Q() { $PGBIN/psql -h $D -p $PORT -U u -d postgres -qtAX -c "$1" 2>&1; }
Q "CREATE TABLE unit_conversions(id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id text, product_id uuid, base_unit text, conversion_unit text, conversion_factor numeric CHECK (conversion_factor>0),
   purchase_price numeric, sales_price numeric, is_active boolean DEFAULT true, created_at timestamptz DEFAULT now(), updated_at timestamptz DEFAULT now(), CONSTRAINT uq_unit_conversion UNIQUE(tenant_id, product_id, base_unit, conversion_unit))" >/dev/null
# ambil pernyataan INSERT di jalur ubah barang (antara "Update conversions if provided" dan "Log activity"); $1..$7 -> parameter
SQL=$(python3 - "$ITEMS" "${SABOTASE:-}" <<'PY'
import re,sys
s=open(sys.argv[1]).read(); a=s.index("# Update conversions if provided"); seg=s[a:a+5000]
m=re.search(r'INSERT INTO unit_conversions \((.*?)"""', seg, re.S); q="INSERT INTO unit_conversions ("+m.group(1)
if sys.argv[2]=="polos": q=q.split("ON CONFLICT")[0]
print(q)
PY
)
P=00000000-0000-0000-0000-0000000000c1
pasang() { # $1 unit $2 faktor ; $1..$7 diganti literal oleh python (pernyataan asli items.py, urut abjad terbalik agar $1 tak menimpa $10)
  local q; q=$(SQLIN="$SQL" python3 - "$P" "$1" "$2" <<'PY'
import os,sys
q=os.environ["SQLIN"]; P,u,f=sys.argv[1:4]
for k,v in (("$7","NULL"),("$6","NULL"),("$5",f),("$4","'"+u+"'"),("$3","'pcs'"),("$2","'"+P+"'"),("$1","'t1'")):
    q=q.replace(k,v)
print(q)
PY
)
  Q "$q"; }
nonaktif_semua() { Q "UPDATE unit_conversions SET is_active=false WHERE product_id='$P' AND tenant_id='t1'" >/dev/null; }
gagal=0
cek() { if [ "$2" = "$3" ]; then echo "OK   $1"; else echo "GAGAL $1 (harap=$3 dapat=$2)"; gagal=1; fi; }
Q "INSERT INTO unit_conversions(tenant_id,product_id,base_unit,conversion_unit,conversion_factor) VALUES ('t1','$P','pcs','Dus',12)" >/dev/null
# 1 ubah barang TANPA mengubah konversi: nonaktifkan semua lalu pasang ulang Dus=12 -> tetap 1 baris, aktif, tak dobel
nonaktif_semua; out=$(pasang Dus 12)
cek "1 pasang ulang konversi sama tanpa galat" "$(echo "$out" | grep -c 'duplicate key')" "0"
cek "1 tetap satu baris Dus"                   "$(Q "SELECT count(*) FROM unit_conversions WHERE product_id='$P' AND conversion_unit='Dus'")" "1"
cek "1 baris itu aktif"                        "$(Q "SELECT count(*) FROM unit_conversions WHERE product_id='$P' AND conversion_unit='Dus' AND is_active")" "1"
# 2 konversi dibuang (nonaktif, tak dipasang ulang) lalu dipasang lagi (faktor baru) -> baris lama aktif lagi, faktor baru
nonaktif_semua
cek "2 dibuang: tak ada konversi aktif"        "$(Q "SELECT count(*) FROM unit_conversions WHERE product_id='$P' AND is_active")" "0"
out=$(pasang Dus 24)
cek "2 pasang Dus lagi tanpa galat"            "$(echo "$out" | grep -c 'duplicate key')" "0"
cek "2 faktor baru, satu baris, aktif"         "$(Q "SELECT conversion_factor::int||'/'||count(*)||'/'||bool_and(is_active) FROM unit_conversions WHERE product_id='$P' AND conversion_unit='Dus' GROUP BY conversion_factor")" "24/1/true"
# 3 nama konversi BERBEDA = baris terpisah (tak menimpa Dus)
nonaktif_semua; pasang Dus 24 >/dev/null; pasang Karton 48 >/dev/null
cek "3 dua nama berbeda = dua baris aktif"     "$(Q "SELECT count(*) FROM unit_conversions WHERE product_id='$P' AND is_active")" "2"
cek "3 Dus tak tertimpa Karton"                "$(Q "SELECT conversion_factor::int FROM unit_conversions WHERE product_id='$P' AND conversion_unit='Dus'")" "24"
[ $gagal -eq 0 ] && echo "SEMUA OK" || { echo "ADA GAGAL"; exit 1; }
