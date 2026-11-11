# Konfig gerbang unit penyesuaian-2 (akun lawan pilihan, putusan pemilik 10 Nov #7a). Sabotase = bukti unit ini; tiap baris WAJIB membuat tes merah.
W=/root/mh-lapfix; CABANG=feat/penyesuaian-2; NAMA_JENDELA=pen-2
POLA_UNIT='persediaan|stok_sesuai|stock_adj|penyesuaian|akun_lawan|izin_rute|inventaris_rute|pagar_rute|items'
HARNESS=scripts/ops/uji-salinan-persediaan.sh
PENANDA_BERKAS=services/stok_sesuai.py; PENANDA='def alasan_tolak_lawan'
A=backend/api_gateway/app; T=tests/unit/test_penyesuaian_akun_lawan.py
SAB=(
 "q1 posting mengabaikan pilihan|$A/services/stok_sesuai.py|s#^        penyesuaian = pilihan\$#        pass#|$T"
 "q2 kas/bank lolos sebagai lawan|$A/services/stok_sesuai.py|s#    if a\[\"is_cash\"\]:#    if False and a[\"is_cash\"]:#|$T"
 "q3 quick adjust mengabaikan pilihan|$A/routers/items.py|s#adj_account_id = akun_lawan_pilihan or await resolve_account_id(#adj_account_id = await resolve_account_id(#|$T"
 "q4 idempotensi tak membedakan pilihan|$A/routers/stock_adjustments.py|s#{\"counter_account_id\": lawan} if lawan else {}#{}#|$T"
)
