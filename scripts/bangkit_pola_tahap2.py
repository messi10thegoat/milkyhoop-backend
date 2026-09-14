"""Bangkitkan blok pola tahap 2 (literal Python) dari aturan draf yang DISETUJUI pemilik (15 modul DB yang ada).
Keluaran: /tmp/pola_tahap2.txt (dimasukkan ke ROUTE_PERMISSIONS oleh patch berjangkar). chat & document_intake TIDAK dipola."""
import re
import sys

sys.path.insert(0, "/tmp")
sys.path.insert(0, "/app/backend/api_gateway")
sys.path.insert(0, "/app")
import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location("draf", "/tmp/draf_peta_tahap2.py")
d = importlib.util.module_from_spec(spec)
src = open("/tmp/draf_peta_tahap2.py", encoding="utf-8").read().split("async def main():")[0]
exec(compile(src, "draf", "exec"), d.__dict__)   # ambil ROUTER_MODUL, VERBA, aksi(), regex_dari() tanpa menjalankan main

from fastapi.routing import APIRoute  # noqa: E402
from app.main import app  # noqa: E402
from app.middleware import permission_middleware as pm  # noqa: E402

pola = [(re.compile(p), m) for p, m, *_ in pm.ROUTE_PERMISSIONS]
skip = [re.compile(p) for p in pm.SKIP_PATTERNS]
contoh = lambda p: re.sub(r"\{[^}]+\}", "00000000-0000-4000-8000-000000000001", p)  # noqa: E731
baris = []
for r in app.routes:
    if not isinstance(r, APIRoute):
        continue
    rt = r.endpoint.__module__.split(".")[-1]
    if rt not in d.ROUTER_MODUL:
        continue
    for m in sorted(r.methods & {"POST", "PUT", "PATCH", "DELETE"}):
        p = contoh(r.path)
        if any(s.match(p) for s in skip) or any(m in ms and c.match(p) for c, ms in pola):
            continue
        baris.append((rt, d.regex_dari(r.path), m, d.ROUTER_MODUL[rt], d.aksi(m, r.path)))
keluar = ["    # === 14 Sep 2026 sweep izin TAHAP 2: tulis modul pemindah uang yang dulu TANPA pola (lolos tanpa cek). ===",
          "    # Dibangkitkan dari aturan tertulis (putusan pemilik: pakai 15 modul DB yang ada) oleh scripts/bangkit_pola_tahap2.py;",
          "    # aksi: POST->C PUT/PATCH->U DELETE->D, verba jalur void|cancel|reject|bounce->V approve|confirm->A post|complete|...->P",
          "    # export->E calculate|validate|preview->R. chat & document_intake SENGAJA belum dipola (otorisasi modul tujuan, terbuka)."]
kini = None
for rt, rx, m, mod, act in sorted(baris):
    if rt != kini:
        keluar.append(f"    # {rt}")
        kini = rt
    keluar.append(f'    (r"{rx}", ["{m}"], "{mod}", "{act}"),')
open("/tmp/pola_tahap2.txt", "w", encoding="utf-8").write("\n".join(keluar) + "\n")
print("baris pola:", len(baris))
