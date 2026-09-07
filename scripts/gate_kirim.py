"""Gerbang penyaring status /api/deliveries — LAMA vs BARU, satu basis data.

Kontrol merah bukan hiasan: modul LAMA dijalankan berdampingan dengan yang BARU.
Kalau keduanya hijau, gerbang ini tak membedakan apa pun dan wajib dianggap rusak.

Dua lapis, sengaja dipisah karena mengukur hal berbeda:
  A. NILAI SAH  -> handler dipanggil LANGSUNG (loop + kolam sendiri).
     TestClient di sini memberi "another operation is in progress" saat
     MELEPAS koneksi -- artefak lapis uji, bukan cacat produk.
  B. NILAI ASING -> lewat TestClient. Validasi Literal menolak SEBELUM handler
     jalan, jadi lapis ini memang tak pernah menyentuh basis data.
"""
import asyncio
import importlib.util
import os
import sys

sys.path.insert(0, "/app/backend/api_gateway")

import asyncpg  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.routers.deliveries as lama  # noqa: E402

TENANT = "kaos-biru-konveksi"
USER = "a2d3129a-91a6-4144-8b3b-39f149790d87"
BARU_PATH = os.environ.get("BARU_PATH", "/tmp/deliveries_baru.py")


def muat(path, nama):
    spec = importlib.util.spec_from_file_location(nama, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[nama] = mod
    spec.loader.exec_module(mod)
    return mod


baru = muat(BARU_PATH, "app.routers.deliveries_baru")

for m in (lama, baru):
    m.get_user_context = lambda request: {"tenant_id": TENANT, "user_id": USER}


class ReqPalsu:
    pass


async def lapis_a():
    """NILAI SAH lewat handler langsung."""
    kolam = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=2)

    async def get_pool():
        return kolam

    for m in (lama, baru):
        m.get_pool = get_pool

    hasil = {}
    for nama, m in (("LAMA", lama), ("BARU", baru)):
        for st in (None, "posted", "voided"):
            # Dipanggil LANGSUNG, jadi default Query() tak diisi FastAPI --
            # harus dioper sendiri, kalau tidak `sort_order.lower()` meledak.
            r = await m.list_deliveries(
                ReqPalsu(), status=st, customer_id=None, search=None,
                sort_by="delivery_date", sort_order="desc", page=1, per_page=20,
            )
            hasil[(nama, st)] = r["total"]
    await kolam.close()
    return hasil


def lapis_b():
    """NILAI ASING lewat HTTP. Tak menyentuh DB: validasi menolak lebih dulu."""
    # LAMA tak punya validasi, jadi nilai asing SAMPAI ke handler dan butuh DB.
    # Kolamnya sengaja dibuat MELEDAK: kalau LAMA menjawab 500 dan bukan 422,
    # itu justru buktinya -- permintaan lolos validasi. BARU tak pernah sampai
    # sini karena Literal menolaknya lebih dulu.
    class Meledak(Exception):
        pass

    async def kolam_meledak():
        raise Meledak("handler TERCAPAI: tak ada validasi di depannya")

    lama.get_pool = kolam_meledak
    baru.get_pool = kolam_meledak

    api = FastAPI()
    api.include_router(lama.router, prefix="/lama")
    api.include_router(baru.router, prefix="/baru")
    c = TestClient(api, raise_server_exceptions=False)
    return {
        (p, v): c.get(f"/{p.lower()}?status={v}").status_code
        for p in ("LAMA", "BARU")
        for v in ("zzz", "POSTED", "draft")
    }


gagal = []
a = asyncio.run(lapis_a())
b = lapis_b()

print("A. NILAI SAH (total baris)      LAMA   BARU   harap-BARU")
for st, harap in ((None, 4), ("posted", 4), ("voided", 0)):
    la, ba = a[("LAMA", st)], a[("BARU", st)]
    ok = ba == harap
    print(f"   status={str(st):<10}        {la:<6} {ba:<6} {harap}  {'OK' if ok else 'GAGAL'}")
    if not ok:
        gagal.append(f"BARU status={st} -> {ba}, harap {harap}")

print("\nB. NILAI ASING (kode HTTP)      LAMA   BARU   harap-BARU")
for v in ("zzz", "POSTED", "draft"):
    lb, bb = b[("LAMA", v)], b[("BARU", v)]
    ok = bb == 422
    print(f"   status={v:<10}        {lb:<6} {bb:<6} 422  {'OK' if ok else 'GAGAL'}")
    if not ok:
        gagal.append(f"BARU status={v} -> {bb}, harap 422")

print("\nKONTROL MERAH:")
if a[("LAMA", "posted")] == 4:
    gagal.append("KONTROL MERAH GAGAL: LAMA sudah benar, gerbang tak mengukur apa pun")
    print("   GAGAL: LAMA status=posted menjawab 4 -- cacatnya tak ada")
else:
    print(f"   OK  LAMA status=posted -> {a[('LAMA','posted')]} (cacat NYATA pada kode lama)")
for v in ("zzz", "POSTED", "draft"):
    if b[("LAMA", v)] == 422:
        gagal.append(f"KONTROL MERAH GAGAL: LAMA menolak {v}")
        print(f"   GAGAL: LAMA sudah 422 untuk {v}")
    else:
        print(f"   OK  LAMA status={v} -> {b[('LAMA', v)]} (lolos validasi, sampai ke handler)")
if a[("LAMA", None)] != a[("BARU", None)]:
    gagal.append("daftar POLOS berubah -- perbaikan menyentuh yang bukan urusannya")
else:
    print(f"   OK  daftar polos TAK berubah ({a[('LAMA', None)]} di kedua sisi)")

if gagal:
    print("\nGAGAL:")
    for g in gagal:
        print("  - " + g)
    sys.exit(1)
print("\nOK: gerbang hijau, dan terbukti bisa merah.")
