"""Gerbang POST /api/quotes/{id}/send -- DUA SISI, NOL PENULISAN ke basis data.

Dijalankan DI DALAM image api_gateway (butuh fastapi/asyncpg), dengan pohon
kode yang dipasang di /app/backend:
  SISI=lama  -> pohon utama (kode produksi)  harus MERAH
  SISI=baru  -> worktree cabang              harus HIJAU

Menguji sisi lama di produksi berarti benar-benar menandai sebuah penawaran
"terkirim". Jadi handler dipanggil langsung dengan KoneksiPerekam -- SENTINEL
milik gerbang, bukan produk -- yang merekam setiap SQL dan tak menyentuh DB.

  [P] PURA-PURA  -- send_email=true: sisi baru MENOLAK (4xx) dan TIDAK
                    menjalankan `UPDATE quotes ... 'sent'`. Sisi lama
                    menjalankan UPDATE lalu menjawab sukses.
  [K] KONTROL    -- send_email=false: KEDUA sisi menjalankan UPDATE dan sukses.
                    Kalau ini merah, penolakannya terlalu lebar dan gerbang
                    tak membuktikan apa pun.
"""
import asyncio, os, sys
from types import SimpleNamespace

SISI = os.environ["SISI"]
assert SISI in ("lama", "baru"), SISI
sys.path.insert(0, "/app")

from fastapi import HTTPException  # noqa: E402
import backend.api_gateway.app.routers.quotes as q  # noqa: E402
from backend.api_gateway.app.schemas.quotes import SendQuoteRequest  # noqa: E402

QID = "00000000-0000-4000-8000-000000000001"


class KoneksiPerekam:
    """SENTINEL -- milik gerbang. Merekam SQL, tak menyentuh DB."""

    def __init__(self):
        self.sql = []

    async def fetchrow(self, sql, *a):
        self.sql.append(sql)
        return {"id": QID, "status": "draft", "quote_number": "QT-UJI-GERBANG"}

    async def fetchval(self, sql, *a):
        self.sql.append(sql)
        return "pelanggan@contoh.invalid"

    async def execute(self, sql, *a):
        self.sql.append(sql)
        return "UPDATE 1"


class PoolPerekam:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        class _Ctx:
            async def __aenter__(self_):
                return conn

            async def __aexit__(self_, *e):
                return False

        return _Ctx()


async def satu(send_email):
    conn = KoneksiPerekam()

    async def get_pool():
        return PoolPerekam(conn)

    q.get_pool = get_pool
    req = SimpleNamespace(state=SimpleNamespace(
        # user_id WAJIB UUID: `get_user_context` mengurainya dengan
        # uuid.UUID(). Versi pertama gerbang ini memakai "u-uji", dan KEDUA
        # sisi pulang 500 identik -- kontrol positif [K] memerah dan gerbang
        # menyatakan dirinya tak sah. 500 itu milik ALAT, bukan produk.
        user={"tenant_id": "tenant-uji-gerbang",
              "user_id": "00000000-0000-4000-8000-0000000000aa"}))
    try:
        res = await q.send_quote(req, QID, SendQuoteRequest(send_email=send_email))
        hasil = f"SUKSES ({getattr(res, 'message', res)})"
        kode = 200
    except HTTPException as e:
        hasil, kode = f"{e.status_code}", e.status_code
    update = any("UPDATE quotes" in s and "'sent'" in s for s in conn.sql)
    return kode, hasil, update


async def main():
    kode_p, hasil_p, upd_p = await satu(True)
    kode_k, hasil_k, upd_k = await satu(False)
    ok_p = 400 <= kode_p < 500 and not upd_p
    ok_k = kode_k == 200 and upd_k

    print(f"=== {SISI.upper()}")
    print(f"  [P] {'HIJAU' if ok_p else 'MERAH'}  send_email=true  -> {hasil_p}; "
          f"UPDATE status dijalankan: {upd_p}")
    print(f"  [K] {'HIJAU' if ok_k else 'MERAH'}  send_email=false -> {hasil_k}; "
          f"UPDATE status dijalankan: {upd_k}")
    print("  (KoneksiPerekam = SENTINEL milik gerbang; nol baris DB tersentuh)")

    if SISI == "baru":
        lulus = ok_p and ok_k
        print("PUTUSAN:", "LULUS" if lulus else "GAGAL")
        return 0 if lulus else 1
    # Sisi lama: [P] harus MERAH, [K] harus HIJAU (kontrol berlaku di kedua sisi).
    sah = (not ok_p) and ok_k
    print("PUTUSAN:", "KONTROL MERAH SAH" if sah else
          "KONTROL TIDAK SAH -- gerbang tak membuktikan apa pun")
    return 0 if sah else 1


sys.exit(asyncio.run(main()))
