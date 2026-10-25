"""Sabotase GABUNG-VENDOR pada SALINAN api_gateway (tiap ubahan tepat sekali, dicek). argv: <api_gateway> <mode>
S_hantu = kolom hantu merged_into_id kembali; S_urut = kunci SESUDAH penentu; S_tabel/M_ap = bill_payments_v2/bills keluar dari GABUNG_TABEL;
M_pratinjau = pratinjau selalu ok; S_status = penolak tagihan tanpa status dimatikan."""
import sys
p = sys.argv[1] + "/app/routers/vendors.py"; m = sys.argv[2]
s = open(p).read()
UB = {
    "S_hantu": [('"SELECT id, name, code, is_active, notes FROM vendors WHERE id = ANY($1::uuid[]) AND tenant_id = $2"',
                 '"SELECT id, name, code, is_active, notes, merged_into_id FROM vendors WHERE id = ANY($1::uuid[]) AND tenant_id = $2"')],
    "S_urut": [('''                await _kunci_vendor(conn, ctx["tenant_id"], sumber, tujuan, saldo_awal=True)
                r = await _gabung_rencana(conn, ctx["tenant_id"], sumber, tujuan)
''', '''                r = await _gabung_rencana(conn, ctx["tenant_id"], sumber, tujuan)
                await _kunci_vendor(conn, ctx["tenant_id"], sumber, tujuan, saldo_awal=True)
''')],
    "S_tabel": [('    ("bill_payments_v2", "vendor_id", True, True),\n', '')],
    "M_ap": [('    ("bills", "vendor_id", True, True),\n', '')],
    "S_status": [('    if tanpa_status:\n        blok.append({"code": "TAGIHAN_TANPA_STATUS",', '    if False:\n        blok.append({"code": "TAGIHAN_TANPA_STATUS",')],
    "M_pratinjau": [('"ok": not r["blocks"], "source"', '"ok": True, "source"')],
}
for a, b in UB[m]:
    assert s.count(a) == 1, f"titik {m} tak tepat sekali: {s.count(a)}"
    s = s.replace(a, b)
open(p, "w").write(s)
print("terpasang", m)
