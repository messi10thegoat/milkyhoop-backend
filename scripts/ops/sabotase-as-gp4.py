"""Kontrol merah AS-GP4: pada SALINAN pohon, PATCH UM kembali seperti dulu (tanpa kunci VENDOR_DEPOSIT, tanpa FOR UPDATE, UPDATE tanpa syarat
status) + jeda 0,8 dtk sesudah baca -> balapan PATCH || post menghasilkan dokumen != jurnal. Tiap ubahan TEPAT SEKALI di update_vendor_deposit."""
import sys
p = f"{sys.argv[1]}/app/routers/vendor_deposits.py"
s = open(p).read()
i = s.index("async def update_vendor_deposit(")
j = s.index("\nasync def ", i + 1)
b = s[i:j]
UBAH = [
    ('            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"VENDOR_DEPOSIT:{deposit_id}")\n', ""),
    ('"SELECT * FROM vendor_deposits WHERE id = $1 AND tenant_id = $2 FOR UPDATE",', '"SELECT * FROM vendor_deposits WHERE id = $1 AND tenant_id = $2",'),
    ("AND tenant_id = ${len(params)} AND status = 'draft'", "AND tenant_id = ${len(params)}"),
    ('            if not existing:\n                raise HTTPException(status_code=404, detail="Vendor deposit not found")\n',
     '            import asyncio as _asb\n            await _asb.sleep(0.8)  # SABOTASE uji merah: jendela balapan\n'
     '            if not existing:\n                raise HTTPException(status_code=404, detail="Vendor deposit not found")\n'),
]
for lama, baru in UBAH:
    if b.count(lama) != 1:
        sys.exit(f"SABOTASE GAGAL: pola {b.count(lama)}x: {lama[:70]!r}")
    b = b.replace(lama, baru)
open(p, "w").write(s[:i] + b + s[j:])
print("sabotase AS-GP4 terpasang (PATCH tanpa kunci/FOR UPDATE/syarat status + jeda 0,8 dtk)")
