"""Kontrol merah AS-G1b pada SALINAN pohon: If-Match dikembalikan ke posisi lama (sebelum replay & kunci) -> tes/harness WAJIB merah.
argv: <akar api_gateway>"""
import sys
from pathlib import Path

p = Path(sys.argv[1]) / "app/routers/quotes.py"
s = p.read_text()
a = '''                await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"QUOTE:{ctx['tenant_id']}:{quote_id}")
                await assert_if_match_row(request, "quotes", quote_id)
'''
b = '''        from ..services.optimistic_concurrency import assert_if_match_row
'''
assert s.count(a) == 1 and s.count(b) == 1
s = s.replace(a, "").replace(b, b + '        await assert_if_match_row(request, "quotes", quote_id)\n')
p.write_text(s)
print("sabotase AS-G1b terpasang (If-Match kembali sebelum replay)")
