"""Sabotase B1b-1 pada SALINAN pohon (WAJIB merah). Argumen: <pohon backend/api_gateway> <S1..S7>. Tiap sabotase = daftar (berkas, lama, baru);
setiap pola harus ditemukan TEPAT 1x."""
import sys
from pathlib import Path

P = Path(sys.argv[1])
INC_BULK = 'app.include_router(bulk_po_router.router, prefix="/api/purchase-orders", tags=["bulk"])\n'
INC_PO = '    purchase_orders.router, prefix="/api/purchase-orders", tags=["purchase-orders"]\n)\n'
S = {
    # urutan rute: bulk_po didaftarkan SESUDAH router PO -> /{po_id}/send|cancel|.../preview menelan /bulk/*
    "S1": [("app/main.py", INC_BULK, ""), ("app/main.py", INC_PO, INC_PO + INC_BULK)],
    # tulis massal melewati penentu di bawah kunci -> kode galat tak stabil (teks inti tanpa kode)
    "S2": [("app/routers/bulk_po.py", "        if blok:\n            raise HTTPException(", "        if False:\n            raise HTTPException(")],
    # pratinjau memakai kunci (advisory + FOR UPDATE) -> bukan baca saja
    "S3": [("app/routers/bulk_po.py", "blok, isi = await _blok(conn, ctx, i, aksi, alasan, kunci=False)",
            "blok, isi = await _blok(conn, ctx, i, aksi, alasan, kunci=True)")],
    # izin batal massal != izin batal tunggal (V)
    "S4": [("app/middleware/permission_middleware.py", '(r"^/api/purchase-orders/bulk/(cancel|cancel/preview)$", ["POST"], "purchase_order", "V")',
            '(r"^/api/purchase-orders/bulk/(cancel|cancel/preview)$", ["POST"], "purchase_order", "C")')],
    # inti kirim kehilangan penentu status (regresi rute TUNGGAL lewat ekstraksi)
    "S5": [("app/routers/purchase_orders.py", '    _tolak(PR.rencana_kirim(po)["blocks"])  # = /send/preview\n', "")],
    # inti hapus tak lagi men-set app.user_id (V230: jejak penghapus hilang)
    "S6": [("app/routers/purchase_orders.py",
            '    await conn.execute("SELECT set_config(\'app.user_id\', $1, true)", str(ctx["user_id"] or ""))\n    po = await _kunci_po(conn, ctx["tenant_id"], po_id, "id, status, po_number")\n',
            '    po = await _kunci_po(conn, ctx["tenant_id"], po_id, "id, status, po_number")\n')],
    # idem rute tunggal batal kehilangan isi (alasan) -> kunci sama + alasan beda tak lagi 409
    "S7": [("app/routers/purchase_orders.py", '"PO_CANCEL", po_id, lambda: body.model_dump(mode="json"), response)', '"PO_CANCEL", po_id, {}, response)')],
}
for berkas, lama, baru in S[sys.argv[2]]:
    f = P / berkas
    s = f.read_text(encoding="utf-8")
    if s.count(lama) != 1:
        sys.exit(f"pola sabotase {sys.argv[2]} tidak ditemukan tepat 1x di {berkas}")
    f.write_text(s.replace(lama, baru), encoding="utf-8")
print("terpasang", sys.argv[2])
