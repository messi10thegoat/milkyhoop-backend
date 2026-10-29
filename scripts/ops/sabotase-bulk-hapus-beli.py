"""Sabotase B1b-2 pada SALINAN pohon (WAJIB merah). Argumen: <pohon backend/api_gateway> <S1..S8|G1..G9>. Tiap sabotase = daftar (berkas, lama, baru);
setiap pola harus ditemukan TEPAT 1x."""
import sys
from pathlib import Path

P = Path(sys.argv[1])
INC = ('for _m, _r in bulk_hapus_beli_router.ROUTERS.items():\n    app.include_router(_r, prefix=f"/api/{_m}", tags=["bulk"])\n'
       'app.include_router(bills.router, prefix="/api/bills", tags=["bills"])\n')
INC_BALIK = ('app.include_router(bills.router, prefix="/api/bills", tags=["bills"])\n'
             'for _m, _r in bulk_hapus_beli_router.ROUTERS.items():\n    app.include_router(_r, prefix=f"/api/{_m}", tags=["bulk"])\n')
S = {
    # urutan rute: router bills didaftarkan SEBELUM bulk -> /{bill_id}/delete/preview menelan /bulk/delete/preview
    "S1": [("app/main.py", INC, INC_BALIK)],
    # hapus massal tagihan tak lagi menghapus objek lampiran sesudah commit (#30: objek yatim)
    "S2": [("app/routers/bulk_hapus_beli.py", 'return {**hasil, "_sesudah_commit": bersih}', "return hasil")],
    # mesin bersama: langkah sesudah-commit dijalankan SEBELUM commit (di dalam transaksi item)
    "S3": [("app/services/bulk.py", '                        item["result"] = await _ib.simpan(conn, ctx, kp, sd, kode_aksi, resp, i)\n',
            '                        if sesudah is not None:\n                            await sesudah()\n                            sesudah = None\n'
            '                        item["result"] = await _ib.simpan(conn, ctx, kp, sd, kode_aksi, resp, i)\n')],
    # pratinjau tagihan mengunci baris (bukan baca saja)
    "S4": [("app/routers/bulk_hapus_beli.py", 'FROM bills WHERE id = $1 AND tenant_id = $2""", _uuid(i)',
            'FROM bills WHERE id = $1 AND tenant_id = $2 FOR UPDATE""", _uuid(i)')],
    # izin hapus massal ND != izin DELETE tunggal (D)
    "S5": [("app/middleware/permission_middleware.py", '(r"^/api/vendor-credits/bulk/(delete|delete/preview)$", ["POST"], "debit_note", "D")',
            '(r"^/api/vendor-credits/bulk/(delete|delete/preview)$", ["POST"], "debit_note", "U")')],
    # inti tagihan kehilangan app.user_id (V230: jejak penghapus hilang) -- regresi rute TUNGGAL
    "S6": [("app/services/bills_service.py", '        await conn.execute(\n            "SELECT set_config(\'app.user_id\', $1, true)",\n            str(user_id or ""),\n        )\n', "")],
    # inti pembayaran keluar kehilangan kunci BILL_PAYMENT (serialisasi dengan posting draf) -- regresi rute TUNGGAL
    "S7": [("app/routers/bill_payments.py", '    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"BILL_PAYMENT:{payment_id}")\n    r = await BKR.rencana_hapus_draf_bayar', '    r = await BKR.rencana_hapus_draf_bayar')],
    # kode galat ND bukan draf tak stabil (peta kode hilang)
    "S8": [("app/routers/bulk_hapus_beli.py", '{404: "ND_TAK_ADA", 400: "ND_BUKAN_DRAF", 409: "ND_BERUBAH"}', '{404: "ND_TAK_ADA", 409: "ND_BERUBAH"}')],
}
# ---- G1..G9: sabotase KHUSUS penjaga lama yang target bacanya dipindah ke handler+inti (syarat MASTER 29 Okt): masing-masing WAJIB
# memerahkan penjaga yang disebut, dan penjaga itu hijau tanpa sabotase.
S.update({
    # G1 test_as_gp2: kunci domain SEBELUM idem di delete_bill
    "G1": [("app/services/bills_service.py", '                _kp, _sidik, _lama = await _idem_mulai(conn, idem, "BILL_DELETE", bill_id)',
            '                await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"BILL:{bill_id}")\n'
            '                _kp, _sidik, _lama = await _idem_mulai(conn, idem, "BILL_DELETE", bill_id)')],
    # G2 test_as_gp3: penentu SEBELUM kunci BILL_PAYMENT di inti bayar
    "G2": [("app/routers/bill_payments.py",
            '    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"BILL_PAYMENT:{payment_id}")\n    r = await BKR.rencana_hapus_draf_bayar(conn, ctx["tenant_id"], payment_id)\n',
            '    r = await BKR.rencana_hapus_draf_bayar(conn, ctx["tenant_id"], payment_id)\n    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"BILL_PAYMENT:{payment_id}")\n')],
    # G3 test_as_gp4: inti UMV tanpa FOR UPDATE
    "G3": [("app/routers/vendor_deposits.py", '"SELECT * FROM vendor_deposits WHERE id = $1 AND tenant_id = $2 FOR UPDATE",\n        deposit_id,\n        ctx["tenant_id"],\n    )\n    if not existing:\n        raise HTTPException(status_code=404, detail="Uang muka vendor tidak ditemukan.")\n\n    _tolak((await R.rencana_hapus(',
            '"SELECT * FROM vendor_deposits WHERE id = $1 AND tenant_id = $2",\n        deposit_id,\n        ctx["tenant_id"],\n    )\n    if not existing:\n        raise HTTPException(status_code=404, detail="Uang muka vendor tidak ditemukan.")\n\n    _tolak((await R.rencana_hapus(')],
    # G4 test_g5_bills_po: PO tertaut dibaca SESUDAH DELETE (bill_items sudah CASCADE)
    "G4": [("app/services/bills_service.py",
            '        _po_ids = await _pd.po_terkait_bill(conn, tenant_id, bill_id)\n        await conn.execute(\n            "DELETE FROM bills WHERE id = $1 AND tenant_id = $2",\n            bill_id,\n            tenant_id,\n        )\n',
            '        await conn.execute(\n            "DELETE FROM bills WHERE id = $1 AND tenant_id = $2",\n            bill_id,\n            tenant_id,\n        )\n        _po_ids = await _pd.po_terkait_bill(conn, tenant_id, bill_id)\n')],
    # G5 test_lampiran_yatim: objek lampiran dihapus DI DALAM transaksi (sebelum commit)
    "G5": [("app/services/bills_service.py", '                _hasil = await _idem_simpan(conn, idem, _kp, _sidik, "BILL_DELETE",',
            '                await hapus_objek_sesudah_commit(berkas, "sabotase")\n                _hasil = await _idem_simpan(conn, idem, _kp, _sidik, "BILL_DELETE",')],
    # G6 test_p2p3a: kunci VENDOR_CREDIT SESUDAH FOR UPDATE di inti ND
    "G6": [("app/routers/vendor_credits.py",
            '    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"VENDOR_CREDIT:{vendor_credit_id}")\n    vc = await conn.fetchrow(\n        "SELECT id, status, credit_number FROM vendor_credits WHERE id = $1 AND tenant_id = $2 FOR UPDATE",\n        vendor_credit_id,\n        ctx["tenant_id"],\n    )\n',
            '    vc = await conn.fetchrow(\n        "SELECT id, status, credit_number FROM vendor_credits WHERE id = $1 AND tenant_id = $2 FOR UPDATE",\n        vendor_credit_id,\n        ctx["tenant_id"],\n    )\n    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"VENDOR_CREDIT:{vendor_credit_id}")\n')],
    # G7 test_tagihan_aksi: inti tagihan tak memakai penentu TA.blok_hapus
    "G7": [("app/services/bills_service.py", "    _bloks = TA.blok_hapus(bill, await TA.ref_hapus(conn, bill_id))\n", "    _bloks = []\n")],
    # G8 test_um_vendor_pratinjau: DELETE UMV tanpa syarat status draf
    "G8": [("app/routers/vendor_deposits.py", '"DELETE FROM vendor_deposits WHERE id = $1 AND tenant_id = $2 AND status = \'draft\'"',
            '"DELETE FROM vendor_deposits WHERE id = $1 AND tenant_id = $2"')],
    # G9 test_bulk_hapus_beli_b1b2 (penjaga kode, bukan docstring): handler UMV mengunci sendiri
    "G9": [("app/routers/vendor_deposits.py", "            await hapus_draf_umv_core(conn, ctx, deposit_id)\n",
            '            await conn.execute("SELECT 1 FROM vendor_deposits WHERE id = $1 FOR UPDATE", deposit_id)\n            await hapus_draf_umv_core(conn, ctx, deposit_id)\n')],
    # G10 test_p2p3a (kontrol penyisipan penentu): rencana_hapus_nd tak lagi memeriksa status -> cek '["status"] != "draft"' hilang dari badan
    "G10": [("app/routers/vendor_credits.py", '    if vc["status"] != "draft":\n        return [{"code": "ND_BUKAN_DRAF"', '    if False:\n        return [{"code": "ND_BUKAN_DRAF"')],
})
for berkas, lama, baru in S[sys.argv[2]]:
    f = P / berkas
    s = f.read_text(encoding="utf-8")
    if s.count(lama) != 1:
        sys.exit(f"pola sabotase {sys.argv[2]} tidak ditemukan tepat 1x di {berkas}")
    f.write_text(s.replace(lama, baru), encoding="utf-8")
print("terpasang", sys.argv[2])
