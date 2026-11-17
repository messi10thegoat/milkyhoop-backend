# Konfig gerbang unit BUG-261 (Penyesuaian Persediaan: gudang PER BARIS + baris penyelesaian selisih terima Pemindahan = Pengurangan di Transit TERKUNCI; V503 aditif nullable;
# tertunda = dikirim - diterima - penyesuaian POSTED bertaut; revalidasi di kunci STOCK:<barang>:<Transit>; void kirim/batal diblok; K9 + Terima + status_text ikut). Tiap sabotase WAJIB membuat tes merah.
# URUTAN RILIS: V503 (apply_mig, dari pohon utama) -> kode.
W=/root/mh-b261; CABANG=feat/bug261-penyesuaian-gudang-baris-transit-r2; NAMA_JENDELA=b261
POLA_UNIT='bug261|bug246|penyesuaian|persediaan_standar_so|stock_adjust|stock_transfer|pemindahan|k8w|bug099|sql_placeholder|izin_rute|k9'
HARNESS=scripts/ops/uji-salinan-b261.sh
PENANDA_BERKAS=services/stok_sesuai.py; PENANDA='tarik_pemindahan'
A=backend/api_gateway/app; T=tests/unit/test_bug261_penyesuaian_pemindahan.py
SS=$A/services/stok_sesuai.py; D2=$A/services/pemindahan_dua_langkah.py; RA=$A/routers/stock_adjustments.py; RT=$A/routers/stock_transfers.py
SAB=(
 "e1 tertunda tak dikurangi penyelesaian|$D2|s#d\\[\"quantity_pending\"\\] = d\\[\"quantity_variance\"\\] - d\\[\"quantity_resolved\"\\]#d[\"quantity_pending\"] = d[\"quantity_variance\"]#|$T"
 "e2 SQL penyelesaian ikut non-posted|$D2|s#s.status = 'posted' AND i.source_transfer_id = \\\$2#s.status <> 'xx' AND i.source_transfer_id = \$2#|$T"
 "e3 gudang Transit tak terkunci|$SS|s#    if wid and str(wid) != str(transit):#    if False:#|$T"
 "e4 buat: qty boleh melebihi tertunda|$SS|s#            if -ubah > tertunda:#            if False:#|$T"
 "e5 jenis penyelesaian tak dicek|$SS|s#            if tipe != \"decrease\":#            if False:#|$T"
 "e6 posting tanpa revalidasi tertunda|$SS|s#    await _revalidasi_pemindahan(conn, tid, items, bl, kunci)#    pass#|$T"
 "e7 posting tanpa kunci stok Transit|$SS|s#    if kunci and transit:#    if False:#|$T"
 "e8 gerak stok selalu di gudang dokumen|$SS|s#        gid = i.get(\"warehouse_id\") or g\\[\"id\"\\]#        gid = g[\"id\"]#|$T"
 "e9 tulis ledger selalu di gudang dokumen|$RA|s#adj_warehouse_id = g.get(\"warehouse_id\") or r\\[\"gudang\"\\]\\[\"id\"\\]#adj_warehouse_id = r[\"gudang\"][\"id\"]#|$T"
 "e10 barang ganda lagi per barang saja|$SS|s#if (pid, gid) in terlihat:#if pid in terlihat:#|$T"
 "e11 teks selisih diselesaikan hilang|$D2|s#return \"Diterima · selisih diselesaikan\"#return \"Diterima\"#|$T tests/unit/test_bug246_247_248.py"
 "e12 void kirim lolos walau ada penyelesaian|$D2|s#    elif any(_d(i.get(\"quantity_resolved\")) > _NOL for i in items):#    elif False:#|$T"
 "e13 Terima tak mengurangi penyelesaian|$D2|s# - _d(i.get(\"quantity_resolved\")) for i in items}# for i in items}#|$T"
 "e14 stok Transit kurang tak diblok|$SS|s#        if i.get(\"source_transfer_id\") and sebelum + q < 0:#        if False:#|$T"
 "e15 K9 variance tak mengurangi penyelesaian|$RT|s#d.diterima + r.selesai#d.diterima#|$T"
 "e16 batal tak cek status pemindahan|$SS|s#^        if t\\[\"status\"\\] != \"in_transit\":#        if False:#|$T"
 "e17 detail pemindahan tak meneruskan penyelesaian|$RT|s#pd2.teks_status(row\\[\"status\"\\], dikirim, diterima, selesai)#pd2.teks_status(row[\"status\"], dikirim, diterima)#|$T"
 "e18 draf tak menulis kolom baru|$RA|s#b.get(\"warehouse_id\"), b.get(\"source_transfer_id\"),#None, None,#|$T"
 "e19 tarik menyertakan barang tertunda 0|$SS|s#            if tertunda <= NOL:#            if False:#|$T"
 "e20 alasan bawaan bukan Hilang|$SS|s#Hilang (selisih terima#Rusak (selisih terima#|$T"
)
