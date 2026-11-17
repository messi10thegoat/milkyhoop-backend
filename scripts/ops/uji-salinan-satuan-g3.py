"""Satuan G3 aksi tulis -- uji di SALINAN (klon milkydb); tulis HANYA di salinan. GUARD: pool harus scratch_sg3*.
H1 pratinjau READ ONLY (jumlah baris product_units tak berubah); bawaan -> blok 409; bentrok bawaan/tenant -> blok.
H2 buat idempoten (kunci sama = replay, satu baris; kunci sama + isi beda = 409 IDEMPOTENCY_KEY_REUSED); ubah satuan bebas berhasil.
H3 satuan TERPAKAI barang (nama dipasang ke 1 barang nyata): ubah nama + nonaktif = blok; sesudah barang dilepas: nonaktif berhasil, hilang dari daftar, tak terbaca tenant lain (404).
H4 aksi_tersedia seluruh daftar (data nyata) sejalan dengan vonis rencana (ubah_nama / nonaktifkan)."""
import asyncio
import os
import sys
import uuid

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import units as U  # noqa: E402
from app.services import satuan_master as SM  # noqa: E402
from app.services import satuan_rencana as SR  # noqa: E402
from app.services.db_pool import get_db_pool  # noqa: E402
from app.services.galat_berkode import GalatBerkode  # noqa: E402

HASIL = []
TENANT = tuple(x for x in os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(",") if x)


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


class Req:
    def __init__(self, tid, body=None, kunci=None):
        self.state = type("S", (), {"user": {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "username": "uji-satuan-g3"}})()
        self._b, self.headers = body, ({"X-Idempotency-Key": kunci} if kunci else {})

    async def json(self):
        if self._b is None:
            raise ValueError("kosong")
        return self._b


async def galat(coro):
    try:
        await coro
    except GalatBerkode as e:
        return e
    except Exception as e:  # noqa: BLE001
        return e
    return None


async def jumlah_unit(pool, tid):
    async with pool.acquire() as c:
        return await c.fetchval("SELECT count(*) FROM product_units WHERE tenant_id = $1", tid)


async def periksa(pool, tid, lain):
    st = uuid.uuid4().hex[:8]
    # ---- H1 pratinjau read-only + bawaan + bentrok
    n0 = await jumlah_unit(pool, tid)
    p1 = await U.preview_buat_satuan(Req(tid, {"name": f"UJI-SG3 {st}", "abbreviation": f"ujisg3{st}"}))
    p2 = await U.preview_buat_satuan(Req(tid, {"name": "Kilogram", "abbreviation": "kg"}))
    p3 = await U.preview_ubah_satuan(Req(tid, {"name": "Biji"}), SM.ID_BAWAAN + "Pcs")
    p4 = await U.preview_nonaktif_satuan(Req(tid), SM.ID_BAWAAN + "Kg")
    cek(f"{tid} H1 pratinjau read-only + buat ok", await jumlah_unit(pool, tid) == n0 and p1["data"]["ok"] is True and p1["data"]["rencana"]["teks"].startswith("Buat satuan"))
    cek(f"{tid} H1 bentrok bawaan = blok SATUAN_SUDAH_ADA", [b["code"] for b in p2["data"]["blocks"]] == ["SATUAN_SUDAH_ADA"])
    cek(f"{tid} H1 ubah/nonaktif bawaan = SATUAN_BAWAAN_TAK_DAPAT_DIUBAH", [b["code"] for b in p3["data"]["blocks"] + p4["data"]["blocks"]] == ["SATUAN_BAWAAN_TAK_DAPAT_DIUBAH"] * 2)
    e = await galat(U.update_unit(Req(tid, {"name": "Biji"}), SM.ID_BAWAAN + "Pcs"))
    cek(f"{tid} H1 tulis bawaan ditolak 409 berkode", isinstance(e, GalatBerkode) and (e.status_code, e.code) == (409, "SATUAN_BAWAAN_TAK_DAPAT_DIUBAH"), repr(e))
    # ---- H2 buat idempoten + ubah
    kunci = f"uji-sg3-{st}"
    a = await U.create_unit(Req(tid, {"name": f"UJI-SG3 {st}", "abbreviation": f"ujisg3{st}"}, kunci))
    b = await U.create_unit(Req(tid, {"name": f"UJI-SG3 {st}", "abbreviation": f"ujisg3{st}"}, kunci))
    uid = a["data"]["id"]
    cek(f"{tid} H2 buat idempoten: replay = respons asli, satu baris", b == a and await jumlah_unit(pool, tid) == n0 + 1, f"{a['data']} / n={await jumlah_unit(pool, tid)}")
    e = await galat(U.create_unit(Req(tid, {"name": f"UJI-SG3 {st} beda", "abbreviation": f"ujisg3b{st}"}, kunci)))
    det = getattr(e, "detail", None)
    cek(f"{tid} H2 kunci sama isi beda = 409 IDEMPOTENCY_KEY_REUSED", getattr(e, "status_code", None) == 409 and isinstance(det, dict) and det.get("code") == "IDEMPOTENCY_KEY_REUSED", repr(e)[:120])
    e = await galat(U.create_unit(Req(tid, {"name": "x", "abbreviation": f"UJISG3{st}"})))
    cek(f"{tid} H2 singkatan ganda (huruf beda) = SATUAN_SUDAH_ADA", isinstance(e, GalatBerkode) and e.code == "SATUAN_SUDAH_ADA", repr(e))
    r = await U.update_unit(Req(tid, {"name": f"UJI-SG3 {st} Besar"}), uid)
    d = (await U.get_unit(Req(tid), uid))["data"]
    cek(f"{tid} H2 ubah satuan bebas: nama berubah, *_text ada", d["name"] == f"UJI-SG3 {st} Besar" and d["pemakai_text"] == "Belum dipakai" and d["jenis_text"] == "Kustom" and r["success"], str(d)[:160])
    # ---- H3 satuan terpakai
    async with pool.acquire() as c:
        barang = await c.fetchrow("SELECT id, satuan, base_unit FROM products WHERE tenant_id = $1 AND deleted_at IS NULL ORDER BY id LIMIT 1", tid)
    if barang is None:
        print(f"INFO {tid}: tak ada barang -- H3 dilewati")
    else:
        async with pool.acquire() as c:
            await c.execute("UPDATE products SET satuan = $3, base_unit = $3 WHERE id = $1 AND tenant_id = $2", barang["id"], tid, f"ujisg3{st}")
        d = (await U.get_unit(Req(tid), uid))["data"]
        e1 = await galat(U.update_unit(Req(tid, {"abbreviation": f"ujisg3x{st}"}), uid))
        e2 = await galat(U.nonaktifkan_satuan(Req(tid), uid))
        e3 = await galat(U.delete_unit(Req(tid), uid))
        cek(f"{tid} H3 terpakai: ubah = SATUAN_TERPAKAI_UBAH, nonaktif/hapus = SATUAN_TERPAKAI",
            d["jumlah_barang"] == 1 and d["pemakai_text"] == "Dipakai 1 barang" and [getattr(x, "code", None) for x in (e1, e2, e3)] == ["SATUAN_TERPAKAI_UBAH", "SATUAN_TERPAKAI", "SATUAN_TERPAKAI"],
            repr([getattr(x, "code", x) for x in (e1, e2, e3)]))
        cek(f"{tid} H3 aksi_tersedia terpakai: ubah_nama/nonaktifkan tidak tersedia", d["aksi_tersedia"]["ubah_nama"]["tersedia"] is False and d["aksi_tersedia"]["nonaktifkan"]["tersedia"] is False)
        async with pool.acquire() as c:
            await c.execute("UPDATE products SET satuan = $3, base_unit = $4 WHERE id = $1 AND tenant_id = $2", barang["id"], tid, barang["satuan"], barang["base_unit"])
    # ---- nonaktif sesudah dilepas + tenant lain
    if lain:
        e = await galat(U.preview_nonaktif_satuan(Req(lain), uid))
        cek(f"{tid} H3 tenant lain ({lain}) tak melihat satuan ini: 404 SATUAN_TIDAK_ADA", isinstance(e, GalatBerkode) and (e.status_code, e.code) == (404, "SATUAN_TIDAK_ADA"), repr(e))
    kunci2 = f"uji-sg3-n-{st}"
    r1 = await U.nonaktifkan_satuan(Req(tid, None, kunci2), uid)
    r2 = await U.nonaktifkan_satuan(Req(tid, None, kunci2), uid)
    semua = await U.list_units(Req(tid), search=None, is_active=None, aksi=None, limit=500)
    cek(f"{tid} H3 nonaktif berhasil (replay sama), hilang dari daftar", r1 == r2 and r1["success"] and uid not in {x["id"] for x in semua["items"]})
    e = await galat(U.nonaktifkan_satuan(Req(tid), uid))
    cek(f"{tid} H3 nonaktif lagi = SATUAN_SUDAH_NONAKTIF", isinstance(e, GalatBerkode) and e.code == "SATUAN_SUDAH_NONAKTIF", repr(e))
    # ---- H4 aksi_tersedia seluruh daftar == vonis rencana (data nyata)
    salah = []
    async with pool.acquire() as c:
        for x in semua["items"]:
            u = await SR.muat(c, tid, x["id"])
            ubah = await SR.rencana_ubah(c, tid, u, {"name": x["name"] + " x"})
            non = await SR.rencana_status(c, tid, u)
            if x["aksi_tersedia"]["ubah_nama"]["tersedia"] != (not ubah["blocks"]) or x["aksi_tersedia"]["nonaktifkan"]["tersedia"] != (not non["blocks"]):
                salah.append(x["id"])
    cek(f"{tid} H4 aksi_tersedia == vonis rencana untuk {len(semua['items'])} satuan", not salah, f"salah={salah[:3]}")


async def utama():
    pool = await get_db_pool()
    async with pool.acquire() as c:
        db = await c.fetchval("SELECT current_database()")
    if not db.startswith("scratch_sg3"):
        print(f"TOLAK: bukan salinan (pool={db})")
        return 2
    for i, tid in enumerate(TENANT):
        await periksa(pool, tid, TENANT[(i + 1) % len(TENANT)] if len(TENANT) > 1 else None)
    print(f"HASIL {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    return 0 if HASIL and all(HASIL) else 1


sys.exit(asyncio.run(utama()))
