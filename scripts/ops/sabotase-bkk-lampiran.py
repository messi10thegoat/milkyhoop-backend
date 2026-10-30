"""Sabotase BKK + LAMPIRAN-PEMBELIAN F1 pada SALINAN pohon (argumen: <akar backend/api_gateway> <K1..K3|L1..L5>). Tiap sabotase WAJIB merah."""
import sys

akar, s = sys.argv[1], sys.argv[2]
A = {
    "K1": ("app/templates/pdf/kwitansi.html", " and not receipt.get('hide_stamp')", ""),                        # stempel BKK kembali (DITERIMA)
    "K2": ("app/services/cetak_payment_out.py", '    "hide_stamp": True,', '    "stamp_label": "DIBAYAR",'),     # stempel DIBAYAR kembali
    "K3": ("app/templates/pdf/kwitansi.html", "and not receipt.get('hide_stamp')", "and receipt.get('stamp_label')"),  # penerimaan kehilangan DITERIMA
    "L1": ("app/routers/lampiran_dokumen.py", "FROM bill_payments_v2 p WHERE p.tenant_id = $2 AND p.id IN", "FROM bill_payments_v2 p WHERE p.id IN"),
    "L2": ("app/routers/lampiran_dokumen.py", "FROM bill_payments_v2 p WHERE p.tenant_id = $2", "FROM receive_payments p WHERE p.tenant_id = $2"),
    "L3": ("app/routers/lampiran_dokumen.py", "       'tagihan_lama'", "       'tagihan_lama' WHERE false AND"),
    "L4": ("app/middleware/permission_middleware.py",                                                        # izin unduh PK (sudah ada) dibuang
           '    (r"^/api/bill\\-payments/[^/]+/attachments/[^/]+/download$", ["GET"], "send_payment", "R"),  # Unit 1a\n', ""),
    "L5": ("app/routers/lampiran_dokumen.py", "AND b0x.id = $1 AND $3 = 'bill'", "AND b0x.id = $1"),
}
f, lama, baru = A[s]
p = f"{akar}/{f}"
t = open(p).read()
if t.count(lama) != 1:
    sys.exit(f"pola {s} n={t.count(lama)}")
open(p, "w").write(t.replace(lama, baru))
print(f"sabotase {s} terpasang")
