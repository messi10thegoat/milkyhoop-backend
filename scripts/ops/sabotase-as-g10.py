"""Sabotase AS-G10 pada SALINAN pohon (bukan tiruan di harness). argv: <akar api_gateway salinan> <nama>
merah_kunci = PATCH tanpa kunci DEPOSIT_POST, _ubah_dp tanpa FOR UPDATE + jeda 0,8 dtk sesudah baca, UPDATE tanpa syarat draf (harness B: dokumen != jurnal).
P1..P7 = sabotase tunggal untuk membuktikan tiap penjaga teks/pemindai yang disunting/ditunjuk-ulang BISA merah.
Tiap ganti WAJIB kena TEPAT sekali (cek kering)."""
import sys
from pathlib import Path

A = Path(sys.argv[1])
NAMA = sys.argv[2]
R = A / "app/routers/customer_deposits.py"

SAB = {
    "merah_kunci": [
        ('                await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"DEPOSIT_POST:{deposit_id}")\n'
         '                # Optimistic concurrency', '                # Optimistic concurrency'),
        ('    """ + ("FOR UPDATE" if tulis else ""),\n        deposit_id,\n        ctx["tenant_id"],\n    )\n',
         '    """,\n        deposit_id,\n        ctx["tenant_id"],\n    )\n    import asyncio as _a\n    await _a.sleep(0.8)\n'),
        ("WHERE id = ${param_idx} AND tenant_id = ${param_idx + 1} AND status = 'draft'", "WHERE id = ${param_idx} AND tenant_id = ${param_idx + 1}"),
    ],
    # P1: pemeriksaan pelanggan dicabut dari _ubah_dp -> test_pelanggan_satu_tenant (ditunjuk-ulang) MERAH
    "P1": [('        update_data["customer_id"] = await pelanggan_kanonik_tenant(conn, ctx["tenant_id"], update_data["customer_id"])\n',
            '        update_data["customer_id"] = str(update_data["customer_id"])\n')],
    # P2: riwayat ubah tak tercatat -> test_audit_uang_muka (ditunjuk-ulang) MERAH
    "P2": [('                            "DEPOSIT_UPDATED", ctx["user_id"],\n', '                            "DEPOSIT_DIUBAH", ctx["user_id"],\n')],
    # P3: buat UM tak lagi membaca kunci -> pemindai pola kolom lama MERAH untuk create_customer_deposit
    "P3": [("        body.idempotency_key = _kunci_dari(request, body.idempotency_key)\n", "        body.idempotency_key = body.idempotency_key\n")],
    # P4: UPDATE tanpa syarat draf -> test_as_g10 MERAH
    "P4": [("WHERE id = ${param_idx} AND tenant_id = ${param_idx + 1} AND status = 'draft'", "WHERE id = ${param_idx} AND tenant_id = ${param_idx + 1}")],
    # P5: pratinjau memakai jalur tulis -> test_as_g10 MERAH
    "P5": [("_ubah_dp(conn, ctx, deposit_id, body, tulis=False)", "_ubah_dp(conn, ctx, deposit_id, body, tulis=True)")],
    # P6: _post_deposit tanpa FOR UPDATE -> test_as_g10 MERAH
    "P6": [("        WHERE id = $1 AND tenant_id = $2\n        FOR UPDATE\n    \"\"\",", "        WHERE id = $1 AND tenant_id = $2\n    \"\"\",")],
    # P7: hapus kehilangan idem -> pemindai (WAJIB) + test_as_g10 MERAH
    "P7": [('            _kp, _sd, _lama = await _ib.mulai_aksi(conn, ctx, _ib.kunci_dari(request), "DEPOSIT_DELETE", deposit_id, {}, response)\n',
            '            _kp, _sd, _lama = None, None, None\n')],
}
s = R.read_text()
for lama, baru in SAB[NAMA]:
    assert s.count(lama) == 1, (NAMA, lama[:70], s.count(lama))
    s = s.replace(lama, baru)
R.write_text(s)
print(f"sabotase AS-G10 {NAMA} terpasang")
