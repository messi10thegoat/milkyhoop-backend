"""Sabotase BELUM-DIFAKTUR pada SALINAN pohon. argv: <akar api_gateway salinan> <nama>. Tiap ganti TEPAT sekali (cek kering).
merah_dp     : kartu memakai nilai TANPA DP (harness 2/3 merah)
merah_filter : filter ?aksi= memanggil fakta tanpa laporan (harness 1 merah)
U1..U4       : bukti tes unit yang disunting/baru BISA merah."""
import sys
from pathlib import Path

A = Path(sys.argv[1])
SAB = {
    "merah_dp": [("app/services/so_aksi.py", '                out[r["id"]]["nilai"]["belum_difaktur"] = bd["sisa_belum_difaktur"]',
                  '                out[r["id"]]["nilai"]["belum_difaktur"] = bd["nilai_belum_difaktur"]')],
    "merah_filter": [("app/routers/sales_orders.py", "laporan=aksi in so_aksi.KUNCI_LAPORAN), aksi))", "laporan=False), aksi))")],
    # U1: rute tak menerima kunci laporan -> test_pill_aksi_so (diperluas) MERAH
    "U1": [("app/routers/sales_orders.py", '"perlu_ditagih", "belum_difaktur"]] = Query(None)', '"perlu_ditagih"]] = Query(None)')],
    # U2: fakta menghitung laporan secara bawaan (pemanggil lama menanggung kueri jurnal) -> MERAH
    "U2": [("app/services/so_aksi.py", "so_ids: Optional[list] = None, laporan: bool = False) -> dict:",
            "so_ids: Optional[list] = None, laporan: bool = True) -> dict:")],
    # U3: sisa tanpa DP -> MERAH
    "U3": [("app/services/so_aksi.py", '"sisa_belum_difaktur": max(NOL, nilai - dp)}', '"sisa_belum_difaktur": nilai}')],
    # U4: detail tak membawa medan -> MERAH
    "U4": [("app/routers/sales_orders.py", "                    **so_aksi.medan_belum_difaktur(_fakta_det),  # BELUM-DIFAKTUR (24 Okt) == baris daftar\n", "")],
}
for berkas, lama, baru in SAB[sys.argv[2]]:
    p = A / berkas
    s = p.read_text()
    assert s.count(lama) == 1, (sys.argv[2], lama[:60], s.count(lama))
    p.write_text(s.replace(lama, baru))
print(f"sabotase BELUM-DIFAKTUR {sys.argv[2]} terpasang")
