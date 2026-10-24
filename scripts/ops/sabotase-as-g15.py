"""Kontrol merah AS-G15 (syarat MASTER): pada SALINAN pohon, cabut kunci QUOTE + FOR UPDATE dari convert_to_invoice NYATA dan sisipkan jeda
0,8 dtk sesudah baca penawaran (jendela balapan). Tiap ubahan WAJIB tepat sekali di tubuh convert_to_invoice. Pemakaian: <api_gateway salinan>"""
import sys
p = f"{sys.argv[1]}/app/routers/quotes.py"
s = open(p).read()
i = s.index("async def convert_to_invoice(")
j = s.index("\nasync def ", i + 1)
b = s[i:j]
UBAH = [
    ('''                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))", f"QUOTE:{ctx['tenant_id']}:{quote_id}"
                )
''', ""),
    ('''                    WHERE id = $1 AND tenant_id = $2
                    FOR UPDATE
                """,''', '''                    WHERE id = $1 AND tenant_id = $2
                """,'''),
    ('''                if not quote:
                    raise HTTPException(status_code=404, detail="Penawaran tidak ditemukan.")
''', '''                import asyncio as _asyncio_sabotase
                await _asyncio_sabotase.sleep(0.8)  # SABOTASE uji merah: jendela balapan
                if not quote:
                    raise HTTPException(status_code=404, detail="Penawaran tidak ditemukan.")
'''),
]
for lama, baru in UBAH:
    if b.count(lama) != 1:
        sys.exit(f"SABOTASE GAGAL: pola {b.count(lama)}x: {lama[:60]!r}")
    b = b.replace(lama, baru)
open(p, "w").write(s[:i] + b + s[j:])
print("sabotase AS-G15 terpasang (kunci QUOTE + FOR UPDATE dicabut, jeda 0,8 dtk)")
