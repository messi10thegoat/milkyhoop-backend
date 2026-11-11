# Konfig bukti merah harness salinan V494 (dibaca /root/gerbang-log/bukti-merah.sh). Unit = migrasi-saja (tanpa tes unit):
# tiap sabotase merusak berkas migrasi/rollback dan WAJIB membuat scripts/ops/uji-salinan-v494.sh rc != 0; kontrol bersih WAJIB rc=0.
W=/root/mh-bf1-r4; CABANG=feat/bankfeed-mig-v494-r4; NAMA_JENDELA=v494-r4
HARNESS=scripts/ops/uji-salinan-v494.sh
M=backend/migrations; V=$M/V494__bankfeed_mutasi_bank.sql; RB=$M/V494__bankfeed_mutasi_bank_ROLLBACK.sql
SAB=(
 "m1 FORCE RLS aturan_bank dicabut|$V|s#ALTER TABLE aturan_bank FORCE ROW LEVEL SECURITY;##|-"
 "m2 unik tautan aktif tanpa WHERE parsial|$V|57s# WHERE dilepas_pada IS NULL;#;#|-"
 "m3 penjaga rollback tak lagi gagal keras|$RB|s#RAISE EXCEPTION 'V494 rollback#RAISE NOTICE 'V494 rollback#|-"
)
