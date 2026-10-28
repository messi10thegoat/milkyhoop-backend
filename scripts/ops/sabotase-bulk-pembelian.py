"""Sabotase B1a pada SALINAN pohon (WAJIB merah). Argumen: <pohon backend/api_gateway> <S1..S5>."""
import sys
from pathlib import Path

P = Path(sys.argv[1])
S = {
    "S1": ("app/routers/bulk_dokumen.py", "    if modul not in MODUL_BAGIKAN:\n        return router\n", ""),  # bagikan massal terbuka utk Pembelian
    "S2": ("app/services/bulk_export_beli.py", 'TD.posisi_tagihan(r["keadaan"], r["dibayar"])', 'r["keadaan"]'),  # status tagihan = kode, bukan layar
    "S3": ("app/middleware/permission_middleware.py", '(r"^/api/bills/bulk/(export|pdf)$", ["POST"], "purchase_invoice", "R")',
           '(r"^/api/bills/bulk/(export|pdf)$", ["POST"], "purchase_invoice", "C")'),  # izin bukan baca
    "S4": ("app/services/bulk_export_beli.py", '("Vendor", "No. penerimaan", "Tgl. terima", "Pesanan pembelian", "Gudang", "Status")',
           '("Vendor", "No. penerimaan", "Tgl. terima", "Pesanan pembelian", "Gudang", "Nilai", "Status")'),  # GRN memuat nilai
    "S5": ("app/services/bulk_export_beli.py", 'tg.status_id("bp", r["status"])', 'f["alokasi_teks"]'),  # status bayar != layar
}
berkas, lama, baru = S[sys.argv[2]]
f = P / berkas
s = f.read_text(encoding="utf-8")
if s.count(lama) != 1:
    sys.exit(f"pola sabotase {sys.argv[2]} tidak ditemukan tepat 1x")
f.write_text(s.replace(lama, baru), encoding="utf-8")
print("terpasang", sys.argv[2])
