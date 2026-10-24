"""Kontrol merah AS-GP4b pada SALINAN pohon: If-Match dikembalikan ke posisi lama (sebelum replay) -> tes GP4b WAJIB merah. argv: <akar api_gateway>"""
import sys
from pathlib import Path

p = Path(sys.argv[1]) / "app/routers/vendor_deposits.py"
s = p.read_text()
a = '''            # Optimistic concurrency (opt-in If-Match): reject a stale write -- sesudah replay & kunci (AS-GP4b)
            await assert_if_match_row(request, "vendor_deposits", deposit_id)
'''
b = '''    from ..services.optimistic_concurrency import assert_if_match_row
'''
assert s.count(a) == 1 and s.count(b) == 1
s = s.replace(a, "").replace(b, b + '    await assert_if_match_row(request, "vendor_deposits", deposit_id)\n')
p.write_text(s)
print("sabotase AS-GP4b terpasang (If-Match kembali sebelum replay)")
