"""Satuan G3-b -- uji di SALINAN (klon milkydb + V504 dipasang di salinan); tulis HANYA di salinan. GUARD: pool harus scratch_sg3b*.
A ganti nama MENYEBAR: pratinjau read-only (hitungan barang/konversi/harga), tulis -> teks barang/konversi/harga berganti, nama lama = alias, kamus mengenali nama lama.
B gabung A->B: teks ikut tujuan, sumber nonaktif, alias sumber ikut tujuan, replay idempoten. C konflik faktor beda = blok bernama + data tak berubah; faktor sama = duplikat dibuang.
D gabung nama yang hanya tertulis di barang ('terpakai:') ke satuan master. E tenant lain 404. F aksi_tersedia gabung (kustom ya, bawaan tidak)."""
import asyncio
import os
import sys
import uuid

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import units as U  # noqa: E402
from app.services import satuan_dasar as SD  # noqa: E402
from app.services.db_pool import get_db_pool  # noqa: E402
from app.services.galat_berkode import GalatBerkode  # noqa: E402

HASIL = []
TENANT = tuple(x for x in os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(",") if x)


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


class Req:
    def __init__(self, tid, body=None, kunci=None):
        self.state = type("S", (), {"user": {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "username": "uji-satuan-g3b"}})()
        self._b, self.headers = body, ({"X-Idempotency-Key": kunci} if kunci else {})

    async def json(self):
        if self._b is None:
            raise ValueError("kosong")
        return self._b


async def galat(coro):
    try:
        await coro
    except Exception as e:  # noqa: BLE001
        return e
    return None


async def baru(tid, nama, singk):
    return (await U.create_unit(Req(tid, {"name": nama, "abbreviation": singk})))["data"]["id"]


async def periksa(pool, tid, lain):
    st = uuid.uuid4().hex[:6]
    async with pool.acquire() as c:
        ps = await c.fetch("SELECT id, nama_produk, satuan, base_unit FROM products WHERE tenant_id = $1 AND deleted_at IS NULL ORDER BY id LIMIT 3", tid)
    if len(ps) < 3:
        print(f"INFO {tid}: butuh 3 barang -- dilewati")
        return
    p1, p2, p3 = ps

    async def satuan_barang(pid):
        async with pool.acquire() as c:
            r = await c.fetchrow("SELECT satuan, base_unit FROM products WHERE id = $1 AND tenant_id = $2", pid, tid)
        return r["satuan"], r["base_unit"]

    async def konv(pid):
        async with pool.acquire() as c:
            return [(r["base_unit"], r["conversion_unit"], float(r["conversion_factor"])) for r in
                    await c.fetch("SELECT base_unit, conversion_unit, conversion_factor FROM unit_conversions WHERE tenant_id = $1 AND product_id = $2 ORDER BY conversion_unit", tid, pid)]

    ua, ub = f"ujia{st}", f"ujic{st}"
    # ---- A: ganti nama menyebar
    A = await baru(tid, f"UJI-SG3B A {st}", ua)
    B = await baru(tid, f"UJI-SG3B B {st}", ub)
    async with pool.acquire() as c:
        await c.execute("UPDATE products SET satuan = $3, base_unit = $3 WHERE id = $1 AND tenant_id = $2", p1["id"], tid, ua)
        await c.execute("UPDATE products SET satuan = $3, base_unit = $3 WHERE id = $1 AND tenant_id = $2", p2["id"], tid, ub)
        await c.execute("INSERT INTO unit_conversions (tenant_id, product_id, base_unit, conversion_unit, conversion_factor) VALUES ($1, $2, $3, $4, 12)", tid, p1["id"], ua, f"dusuji{st}")
        pl = await c.fetchval("INSERT INTO price_lists (tenant_id, code, name) VALUES ($1, $2, $3) RETURNING id", tid, f"UJI-SG3B-{st}", f"UJI SG3B {st}")
        await c.execute("INSERT INTO price_list_items (price_list_id, item_id, price, unit) VALUES ($1, $2, 100, $3)", pl, p1["id"], ua)
    ub1 = f"ujib{st}"
    pv = (await U.preview_ubah_satuan(Req(tid, {"abbreviation": ub1}), A))["data"]
    j = pv["rencana"].get("penyebaran", {})
    cek(f"{tid} A pratinjau ganti nama: ok, dampak barang/konversi/harga + peringatan", pv["ok"] and (j.get("barang"), j.get("konversi"), j.get("harga")) == (1, 1, 1)
        and [x["code"] for x in pv["peringatan"]] == ["SATUAN_NAMA_MENYEBAR"], str(pv["rencana"].get("penyebaran")))
    cek(f"{tid} A pratinjau READ ONLY: teks barang tak berubah", await satuan_barang(p1["id"]) == (ua, ua))
    await U.update_unit(Req(tid, {"abbreviation": ub1}), A)
    kv = await konv(p1["id"])
    async with pool.acquire() as c:
        harga_unit = await c.fetchval("SELECT t.unit FROM price_list_items t JOIN price_lists p ON p.id = t.price_list_id AND p.tenant_id = $1 WHERE t.price_list_id = $2", tid, pl)
        alias = [r["alias"] for r in await c.fetch("SELECT alias FROM product_unit_aliases WHERE tenant_id = $1 AND unit_id::text = $2", tid, A)]
        peta = await SD._alias(c, tid)
    cek(f"{tid} A tulis: barang+konversi+harga berganti teks ke {ub1}", await satuan_barang(p1["id"]) == (ub1, ub1) and (ub1, f"dusuji{st}", 12.0) in kv and harga_unit == ub1, f"{kv} {harga_unit}")
    cek(f"{tid} A nama lama = alias; kamus mengenali nama lama sebagai satuan yang sama", ua in alias and SD.kanon(ua, peta) == SD.kanon(ub1, peta), f"alias={alias}")
    cek(f"{tid} A barang lain (satuan {ub}) tak tersentuh", await satuan_barang(p2["id"]) == (ub, ub))
    # ---- C konflik faktor beda (E -> F) sebelum B
    E, F = await baru(tid, f"UJI-SG3B E {st}", f"ujie{st}"), await baru(tid, f"UJI-SG3B F {st}", f"ujif{st}")
    async with pool.acquire() as c:
        await c.execute("INSERT INTO unit_conversions (tenant_id, product_id, base_unit, conversion_unit, conversion_factor) VALUES ($1, $2, $3, $4, 12)", tid, p2["id"], ub, f"ujie{st}")
        await c.execute("INSERT INTO unit_conversions (tenant_id, product_id, base_unit, conversion_unit, conversion_factor) VALUES ($1, $2, $3, $4, 10)", tid, p2["id"], ub, f"ujif{st}")
    antes = await konv(p2["id"])
    pv = (await U.preview_gabung_satuan(Req(tid, {"ke_unit_id": F}), E))["data"]
    cek(f"{tid} C gabung faktor beda: pratinjau blok SATUAN_FAKTOR_BEDA menyebut barang", pv["ok"] is False and [b["code"] for b in pv["blocks"]] == ["SATUAN_FAKTOR_BEDA"] and p2["nama_produk"] in pv["blocks"][0]["message"], str(pv["blocks"])[:160])
    e = await galat(U.gabung_satuan(Req(tid, {"ke_unit_id": F}), E))
    cek(f"{tid} C tulis diblok berkode dan data TAK berubah", isinstance(e, GalatBerkode) and e.code == "SATUAN_FAKTOR_BEDA" and await konv(p2["id"]) == antes, repr(e)[:100])
    async with pool.acquire() as c:
        await c.execute("UPDATE unit_conversions SET conversion_factor = 12 WHERE tenant_id = $1 AND product_id = $2 AND conversion_unit = $3", tid, p2["id"], f"ujif{st}")
    pv = (await U.preview_gabung_satuan(Req(tid, {"ke_unit_id": F}), E))["data"]
    cek(f"{tid} C faktor sama: pratinjau ok + SATUAN_DUPLIKAT_DIBUANG", pv["ok"] and "SATUAN_DUPLIKAT_DIBUANG" in [x["code"] for x in pv["peringatan"]], str([x["code"] for x in pv["peringatan"]]))
    await U.gabung_satuan(Req(tid, {"ke_unit_id": F}), E)
    sisa = [x for x in await konv(p2["id"]) if x[1] in (f"ujie{st}", f"ujif{st}")]
    cek(f"{tid} C duplikat identik digabung: tinggal satu konversi ke ujif", sisa == [(ub, f"ujif{st}", 12.0)], str(sisa))
    # ---- B gabung A -> B (+ replay idempoten)
    k = f"uji-sg3b-{st}"
    pv = (await U.preview_gabung_satuan(Req(tid, {"ke_unit_id": B}), A))["data"]
    cek(f"{tid} B pratinjau gabung: ok, tujuan master, barang 1", pv["ok"] and pv["rencana"]["tujuan"]["id"] == B and pv["rencana"]["penyebaran"]["barang"] == 1, str(pv["rencana"]["penyebaran"]))
    r1 = await U.gabung_satuan(Req(tid, {"ke_unit_id": B}, k), A)
    r2 = await U.gabung_satuan(Req(tid, {"ke_unit_id": B}, k), A)
    items = (await U.list_units(Req(tid), search=None, is_active=None, aksi=None, limit=500))["items"]
    async with pool.acquire() as c:
        alias_b = {r["alias"].lower() for r in await c.fetch("SELECT alias FROM product_unit_aliases WHERE tenant_id = $1 AND unit_id::text = $2", tid, B)}
        peta = await SD._alias(c, tid)
    cek(f"{tid} B tulis: barang/konversi ikut {ub}, sumber nonaktif (hilang dari daftar), replay sama", await satuan_barang(p1["id"]) == (ub, ub) and r1 == r2 and A not in {x["id"] for x in items}
        and any(x[0] == ub for x in await konv(p1["id"])), str(await konv(p1["id"])))
    cek(f"{tid} B semua nama lama sumber (termasuk alias lama) menunjuk tujuan", {ua, ub1, f"uji-sg3b a {st}"} <= alias_b and SD.kanon(ua, peta) == SD.kanon(ub, peta) == SD.kanon(f"UJI-SG3B A {st}", peta), f"alias={sorted(alias_b)}")
    # ---- D 'terpakai:' -> master
    async with pool.acquire() as c:
        await c.execute("UPDATE products SET satuan = $3, base_unit = $3 WHERE id = $1 AND tenant_id = $2", p3["id"], tid, f"ujih{st}.")
    pv = (await U.preview_gabung_satuan(Req(tid, {"ke_unit_id": B}), f"terpakai:ujih{st}."))["data"]
    await U.gabung_satuan(Req(tid, {"ke_unit_id": B}), f"terpakai:ujih{st}.")
    cek(f"{tid} D gabung nama-teks ('terpakai:') ke satuan master: barang ikut, nama lama = alias", pv["ok"] and await satuan_barang(p3["id"]) == (ub, ub), str(await satuan_barang(p3["id"])))
    # ---- bawaan: sumber diblok, tujuan bawaan dibuat otomatis / dipakai
    daftar = (await U.list_units(Req(tid), search=None, is_active=None, aksi=None, limit=500))["items"]
    bw = next(x for x in daftar if x["jenis"] == "bawaan")
    e = await galat(U.preview_gabung_satuan(Req(tid, {"ke_unit_id": B}), bw["id"]))
    pvb = (await U.preview_gabung_satuan(Req(tid, {"ke_unit_id": B}), bw["id"]))["data"]
    cek(f"{tid} F sumber bawaan diblok SATUAN_BAWAAN_TAK_DAPAT_DIUBAH; aksi_tersedia gabung mati untuk bawaan", [b["code"] for b in pvb["blocks"]] == ["SATUAN_BAWAAN_TAK_DAPAT_DIUBAH"] and bw["aksi_tersedia"]["gabung"]["tersedia"] is False, repr(e))
    kus = next(x for x in daftar if x["id"] == B)
    cek(f"{tid} F aksi_tersedia gabung/ubah_nama tersedia untuk satuan kustom terpakai", kus["aksi_tersedia"]["gabung"]["tersedia"] and kus["aksi_tersedia"]["ubah_nama"]["tersedia"])
    G = await baru(tid, f"UJI-SG3B G {st}", f"ujig{st}")
    async with pool.acquire() as c:
        await c.execute("UPDATE products SET satuan = $3, base_unit = $3 WHERE id = $1 AND tenant_id = $2", p3["id"], tid, f"ujig{st}")
    tujuan = bw["id"]
    pvt = (await U.preview_gabung_satuan(Req(tid, {"ke_unit_id": tujuan}), G))["data"]
    r = await U.gabung_satuan(Req(tid, {"ke_unit_id": tujuan}), G)
    async with pool.acquire() as c:
        s_aktif = await c.fetchval("SELECT COALESCE(is_active, true) FROM product_units WHERE id::text = $1 AND tenant_id = $2", G, tid)
        t_ada = await c.fetchval("SELECT count(*) FROM product_units WHERE id::text = $1 AND tenant_id = $2 AND COALESCE(is_active, true)", r["data"]["id"], tid)
    sat_p3 = (await satuan_barang(p3["id"]))[0]
    cek(f"{tid} B gabung ke satuan bawaan ({tujuan[:14]}): barang ikut teks bawaan, sumber nonaktif, tujuan punya baris master aktif", pvt["ok"] and s_aktif is False and t_ada == 1 and sat_p3 == bw["abbreviation"], f"{sat_p3} vs {bw['abbreviation']}")
    # ---- E tenant lain
    if lain:
        e = await galat(U.preview_gabung_satuan(Req(lain, {"ke_unit_id": B}), B))
        e2 = await galat(U.gabung_satuan(Req(lain, {"ke_unit_id": B}), A))
        cek(f"{tid} E tenant lain ({lain}) tak bisa menggabung satuan ini: 404 berkode", isinstance(e, GalatBerkode) and e.code == "SATUAN_TIDAK_ADA" and isinstance(e2, GalatBerkode) and e2.code == "SATUAN_TIDAK_ADA", repr((e, e2))[:140])


async def utama():
    pool = await get_db_pool()
    async with pool.acquire() as c:
        db = await c.fetchval("SELECT current_database()")
    if not db.startswith("scratch_sg3b"):
        print(f"TOLAK: bukan salinan (pool={db})")
        return 2
    for i, tid in enumerate(TENANT):
        await periksa(pool, tid, TENANT[(i + 1) % len(TENANT)] if len(TENANT) > 1 else None)
    print(f"HASIL {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    return 0 if HASIL and all(HASIL) else 1


sys.exit(asyncio.run(utama()))
