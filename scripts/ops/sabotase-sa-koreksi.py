"""Sabotase SA-KOREKSI pada SALINAN api_gateway (tiap ubahan tepat sekali, dicek). argv: <api_gateway> <mode>
S_um/M_um = penolak UM tagihan pembuka mati; S_ro = pratinjau tanpa readonly; S_src/M_src = pembalik ber-source VENDOR_OPENING_BALANCE."""
import sys
r = sys.argv[1]; m = sys.argv[2]
TA = r + "/app/services/tagihan_aksi.py"; V = r + "/app/routers/vendors.py"
UB = {
    "um": [(TA, '    if await tagihan_pembuka(conn, tid, bill["id"]):\n        um = await um_aktif', '    if False:\n        um = await um_aktif')],
    "ro": [(V, '''        async with pool.acquire() as conn, conn.transaction(readonly=True):
            r = await _rencana_balik_saldo_awal_lama(''', '''        async with pool.acquire() as conn, conn.transaction():
            r = await _rencana_balik_saldo_awal_lama(''')],
    "src": [(V, "VALUES ($1, $2, $3, $4, 'REVERSAL', $5, $6, $6, 'DRAFT', $7, $8, $9) RETURNING id",
             "VALUES ($1, $2, $3, $4, 'VENDOR_OPENING_BALANCE', $5, $6, $6, 'DRAFT', $7, $8, $9) RETURNING id")],
}
j = {"S_um": "um", "M_um": "um", "S_ro": "ro", "S_src": "src", "M_src": "src"}[m]
for p, a, b in UB[j]:
    s = open(p).read(); assert s.count(a) == 1, f"titik {m} tak tepat sekali: {s.count(a)}"
    open(p, "w").write(s.replace(a, b))
print("terpasang", m)
