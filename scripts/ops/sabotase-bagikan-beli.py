"""Sabotase L7 bagikan massal Pembelian pada SALINAN pohon (argumen: <akar backend> <S1..S9>). Tiap sabotase WAJIB merah."""
import sys

akar, s = sys.argv[1], sys.argv[2]
G = "api_gateway/app/"
A = {
    # izin share PO tunggal jatuh ke modul Penjualan (mutasi MODUL aturan, bukan hapus -- pola penampung menyusul)
    "S1": (G + "middleware/permission_middleware.py",
           '(r"^/api/documents/purchase_order/[^/]+/(share|share/preview|shares/[^/]+/revoke)$", ["POST"], "purchase_order", "E")',
           '(r"^/api/documents/purchase_order/[^/]+/(share|share/preview|shares/[^/]+/revoke)$", ["POST"], "sales_order", "E")'),
    # izin bagikan massal faktur pembelian = modul faktur Penjualan
    "S2": (G + "middleware/permission_middleware.py",
           '(r"^/api/bills/bulk/(share|share/preview)$", ["POST"], "purchase_invoice", "E")',
           '(r"^/api/bills/bulk/(share|share/preview)$", ["POST"], "sales_invoice", "E")'),
    # draf PO lolos penentu tunggal (flag draf hilang)
    "S3": (G + "routers/dokumen.py",
           '"purchase_order": ("SELECT po_number AS nomor, status FROM purchase_orders WHERE id = $1 AND tenant_id = $2", "draft")',
           '"purchase_order": ("SELECT po_number AS nomor, status FROM purchase_orders WHERE id = $1 AND tenant_id = $2", None)'),
    # pagar tenant hilang di SQL faktur pembelian
    "S4": (G + "routers/dokumen.py",
           '"bill": ("SELECT invoice_number AS nomor, status FROM bills WHERE id = $1 AND tenant_id = $2", "draft")',
           '"bill": ("SELECT invoice_number AS nomor, status FROM bills WHERE id = $1", "draft")'),
    # satu rute share tetap JENIS lama (daftar tautan PO 404)
    "S5": (G + "routers/dokumen.py",
           'async def daftar_tautan_dokumen(request: Request, kind: str, doc_id: str):\n    from ..services import dokumen_bagikan as DB\n    if kind not in JENIS_BAGIKAN:',
           'async def daftar_tautan_dokumen(request: Request, kind: str, doc_id: str):\n    from ..services import dokumen_bagikan as DB\n    if kind not in JENIS:'),
    # bundel GRN lupa lapis terkirim
    "S6": (G + "services/grn_daftar.py",
           'return await lapis_kirim(conn, tenant_id, hasil)  # L7',
           'return hasil  # L7'),
    # lapis menimpa juga dokumen TANPA tautan (PO sent_at hilang)
    "S7": (G + "services/dokumen_bagikan.py",
           '        if k:\n            d["sent"] = dict(k)\n    return kelompok_per',
           '        d["sent"] = dict(k) if k else {"state": "none", "sent_at": None, "viewed_at": None}\n    return kelompok_per'),
    # migrasi kehilangan satu jenis cetak
    "S8": ("migrations/V468__document_shares_kind_pembelian.sql",
           "'purchase_order', 'vendor_deposit'));", "'purchase_order'));"),
    # bagikan massal terbuka untuk GRN tanpa jenisnya dibuka (assert impor / rute)
    "S9": (G + "routers/bulk_dokumen.py",
           'MODUL_BAGIKAN = frozenset(SPEC) | {"purchase-orders", "bills"}',
           'MODUL_BAGIKAN = frozenset(SPEC) | {"purchase-orders", "bills", "vendor-credits"}'),
    # lapis per dokumen (N+1)
    "S10": (G + "services/dokumen_bagikan.py",
            '    kirim = await keadaan_kirim(conn, tenant_id, sorted({(d["kind"], d["id"]) for d in dok}))',
            '    kirim = {}\n    for x in dok:\n        kirim.update(await keadaan_kirim(conn, tenant_id, [(x["kind"], x["id"])]))'),
    # tanpa unik
    "S11": (G + "services/dokumen_bagikan.py",
            'sorted({(d["kind"], d["id"]) for d in dok})', '[(d["kind"], d["id"]) for d in dok]'),
}
f, lama, baru = A[s]
p = f"{akar}/{f}"
t = open(p).read()
if t.count(lama) != 1:
    sys.exit(f"pola {s} n={t.count(lama)}")
open(p, "w").write(t.replace(lama, baru))
print(f"sabotase {s} terpasang")
