"""Kontrol merah SALDO-AWAL-VENDOR-C pada SALINAN pohon: penahan dimatikan (jalur lama terbalik hidup lagi) -> tes & harness WAJIB merah.
argv: <akar api_gateway>"""
import sys
from pathlib import Path

p = Path(sys.argv[1]) / "app/routers/vendors.py"
s = p.read_text()
a = "SALDO_AWAL_VENDOR_DITAHAN = True\n"
assert s.count(a) == 1
p.write_text(s.replace(a, "SALDO_AWAL_VENDOR_DITAHAN = False\n"))
print("sabotase SALDO-AWAL-VENDOR-C terpasang (penahan MATI)")
