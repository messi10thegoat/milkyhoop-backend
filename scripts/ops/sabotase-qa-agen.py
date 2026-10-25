"""Sabotase QA-AGEN pada SALINAN pohon (tiap ubahan tepat sekali, dicek). argv: <api_gateway> <mode>
S_idem / M_ulang = idem dicabut dari accept+send (ulang ber-kunci = tulis kedua/400); S_paritas / M_pratinjau = pratinjau (kunci=False) meloloskan draf; S_tx = DELETE tunggal di luar transaksi (tes spek test_bulk_f3 WAJIB merah)."""
import sys
p = sys.argv[1] + "/app/routers/quotes.py"; m = sys.argv[2]
s = open(p).read()
UB = {
    "idem": [('_kp, _sd, _lama = await idem_buat.mulai_aksi(conn, ctx, idem_buat.kunci_dari(request), "QUOTE_ACCEPT", quote_id, {}, response)',
              '_kp, _sd, _lama = None, None, None'),
             ('_kp, _sd, _lama = await idem_buat.mulai_aksi(\n                conn, ctx, idem_buat.kunci_dari(request), "QUOTE_SEND", quote_id, lambda: (body.model_dump(mode="json") if body else {}), response)',
              '_kp, _sd, _lama = None, None, None')],
    "paritas": [('    if quote["status"] not in ("sent", "viewed"):\n        raise HTTPException(\n            status_code=400,\n            detail=tg.tak_bisa_status("quote", quote["status"], aksi, quote["quote_number"]),',
                 '    if quote["status"] not in (("sent", "viewed") if kunci else ("sent", "viewed", "draft")):\n        raise HTTPException(\n            status_code=400,\n            detail=tg.tak_bisa_status("quote", quote["status"], aksi, quote["quote_number"]),')],
    "tx": [('            async with conn.transaction():\n                _kp, _sd, _lama = await idem_buat.mulai_aksi(conn, ctx, idem_buat.kunci_dari(request), "QUOTE_DELETE"',
            '            if True:  # SABOTASE: hapus tunggal di luar transaksi\n                _kp, _sd, _lama = await idem_buat.mulai_aksi(conn, ctx, idem_buat.kunci_dari(request), "QUOTE_DELETE"')],
}
jenis = {"S_idem": "idem", "M_ulang": "idem", "S_paritas": "paritas", "M_pratinjau": "paritas", "S_tx": "tx"}[m]
for lama, baru in UB[jenis]:
    assert s.count(lama) == 1, f"titik sabotase {m} tak tepat sekali: {s.count(lama)}"
    s = s.replace(lama, baru)
open(p, "w").write(s)
print("terpasang", m)
