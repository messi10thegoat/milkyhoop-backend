#!/bin/bash
# sabotase.sh -- kontrol merah: tiap sabotase pada SALINAN apply_mig.sh WAJIB membuat kasus penangkapnya MERAH.
set -uo pipefail
D=$(dirname "$0"); A=$D/apply_mig.sh; U=$D/uji-apply-mig.sh; W=$(mktemp -d); trap 'rm -rf "$W"' EXIT
semua=0
sab() {  # sab <id> <kasus-penangkap> <python: s=s.replace(...)>
  cp "$A" "$W/a.sh"
  python3 - "$W/a.sh" "$3" <<'PY' || { echo "SABOTASE $1 TAK TERPASANG"; exit 3; }
import sys; p, kode = sys.argv[1], sys.argv[2]; s = open(p).read(); asli = s
exec(kode)
assert s != asli, "tak berubah"; open(p, "w").write(s)
PY
  out=$(bash "$U" "$W/a.sh" 2>&1); rc=$?
  if [ $rc -ne 0 ] && echo "$out" | grep -q "^MERAH $2"; then echo "S$1 MERAH (benar) via: $(echo "$out" | grep "^MERAH" | cut -c1-90 | paste -sd';' -)"
  else echo "S$1 TIDAK MERAH di '$2' -> BUTA"; semua=1; fi
}
sab 1 "T4 kunci ditahan" 's=s.replace("SET LOCAL lock_timeout = \x27$LOCK\x27;\n","")'
sab 2 "T5 DDL batal bersama catatan" 's=s.replace("INSERT INTO schema_migrations(version, checksum, status, applied_at, applied_by) VALUES (\x27$F\x27, \x27$SUM\x27, \x27applied\x27, now(), \x27$BY\x27);\n","").replace("psqlc -q -1 -f \"/tmp/mig-$V-wrap.sql\" > \"/tmp/mig_$V.log\" 2>&1; rc=$?", "psqlc -q -1 -f \"/tmp/mig-$V-wrap.sql\" > \"/tmp/mig_$V.log\" 2>&1; rc=$?; [ $rc -eq 0 ] && { psqlc -Atc \"insert into schema_migrations(version, checksum, status, applied_at, applied_by) values (\x27$F\x27, \x27$SUM\x27, \x27applied\x27, now(), \x27$BY\x27)\" >/dev/null 2>&1 || exit 8; }")'
sab 3 "T6 kendali transaksi ditolak" 's=s.replace("(BEGIN|COMMIT|ROLLBACK|START[[:space:]]+TRANSACTION)","(XBEGINX)")'
sab 4 "T7 CONCURRENTLY ditolak" 's=s.replace("grep -niE \x27CONCURRENTLY\x27","grep -niE \x27XCONCX\x27")'
sab 5 "T10 statement_timeout" 's=s.replace("SET LOCAL statement_timeout = \x27$STMT\x27;\n","")'
sab 6 "T9b nama tak sah" 's=s.replace("^V[0-9]+__[A-Za-z0-9_]+\\.sql$","^V.*$")'
sab 7 "T11 tanpa kata sandi" 's=s.replace("set -uo pipefail\n","set -uo pipefail\nPGE=\"PGPASSWORD=bukanRahasiaUji1\"\n",1)'
sab 8 "T9 berkas ROLLBACK" 's=s.replace("[[ \"$F\" == *_ROLLBACK.sql ]] && ","false && ")'
sab 9 "T3 nol catatan" 's=s.replace("psqlc -q -1 -f","psqlc -q -f")'
# Lantai keamanan: harness WAJIB menolak sasaran prod (milkydb / kontainer non-uji) SEBELUM menjalankan apply_mig.
# Sasaran palsu dipakai (milkydb di kontainer sekali-pakai; nama kontainer yang tak ada) -> bila pagar jebol pun tak menyentuh prod.
for sas in "MIG_DB=ujimig|MIG_DB=milkydb" "MIG_PG=\$C|MIG_PG=milkyhoop-prod-palsu"; do
  cp "$U" "$W/u.sh"; python3 -c "import sys; p,a,b=sys.argv[1:]; s=open(p).read(); assert s.count(a)==1; open(p,w).write(s.replace(a,b))" "$W/u.sh" "${sas%%|*}" "${sas##*|}" || { echo "SASARAN ${sas} TAK TERPASANG"; exit 3; }
  out=$(bash "$W/u.sh" "$A" 2>&1); rc=$?
  if [ $rc -eq 2 ] && echo "$out" | grep -q "^PAGAR" && ! echo "$out" | grep -q "^OK "; then echo "SASARAN ${sas##*|} DITOLAK (benar)"; else echo "SASARAN ${sas##*|} TIDAK DITOLAK -> BAHAYA"; semua=1; fi
done
[ $semua = 0 ] && echo "SEMUA SABOTASE MERAH" || echo "ADA SABOTASE BUTA"; exit $semua
