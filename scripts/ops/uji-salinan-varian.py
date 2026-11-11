"""B1 VARIAN -- uji di SALINAN (klon milkydb), per tenant (kaos, nyawit); RUTE ASLI (handler) + jalur tulis resmi; tulis HANYA di salinan.
T1 POST ukuran x warna (3x2): induk (is_matrix_parent, tanpa stok) + 6 anak (matrix_parent_id, atribut, berstok, kode barang unik BRG-).
T2 pencarian chat (milkybook cari_barang): induk muncul SEKALI, anak tak muncul sebagai barang dasar, `ukuran` terisi (1 dimensi urut XS..); 2 dimensi -> info duplikat ukuran.
T3 pratinjau == tulis: nama bentrok terlapor; POST ulang -> 409 VARIAN_DITOLAK, jumlah barang TIDAK berubah.
T4 atomik: satu anak bentrok nama -> 409, induk TIDAK terbuat.
T5 tambah varian (XL): dibuat; dimensi salah -> 422; induk tenant lain -> 404.
T6 daftar + detail.
T7 idempotensi: kunci sama+isi sama = replay (jumlah tetap); isi beda = 409 IDEMPOTENCY_KEY_REUSED.
T8 BALAPAN: dua POST identik serentak -> tepat satu 201 + satu 409, tanpa induk/anak setengah."""
import asyncio
import json
import sys
import time

from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import matrix_items as MR  # noqa: E402
from app.schemas.barang_varian import BuatMatriksRequest, TambahVarianRequest  # noqa: E402
from app.milkybook.v2 import cari_barang as CB  # noqa: E402

HASIL = []
TENANT = ("kaos-biru-konveksi", "pt-kita-nyawit-bersama")
INFO = {}


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid, kunci=None):
    h = [(b"x-idempotency-key", kunci.encode())] if kunci else []
    r = Request({"type": "http", "method": "POST", "path": "/x", "headers": h})
    r.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "username": "uji-varian"}
    return r


def badan(nama, dimensi, **kw):
    d = dict(name=nama, base_unit="Pcs", kategori="Kaos", sales_price=75000, purchase_price=40000, dimensi=dimensi)
    d.update(kw)
    return BuatMatriksRequest(**d)


async def buat(tid, b, kunci=None):
    resp = Response()
    try:
        h = await MR.buat_matriks(req(tid, kunci), b, resp)
        return 201, h, resp
    except HTTPException as e:
        return e.status_code, e.detail, resp


async def jumlah(c, tid):
    return await c.fetchval("SELECT count(*) FROM products WHERE tenant_id = $1 AND deleted_at IS NULL", tid)


async def periksa(pool, tid):
    stamp = int(time.time())
    nama = f"UJI Varian {stamp}"
    dua = [{"nama": "Ukuran", "nilai": ["S", "M", "L"]}, {"nama": "warna", "nilai": ["Hitam", "Putih"]}]
    async with pool.acquire() as c:
        n0 = await jumlah(c, tid)
        parents0 = await c.fetchval("SELECT count(*) FROM products WHERE tenant_id = $1 AND is_matrix_parent", tid)

    # ---- T1
    st, h, _ = await buat(tid, badan(nama, dua))
    cek(f"{tid} T1 POST ukuran x warna -> 201 dengan 6 varian", st == 201 and h["success"] and len(h["data"]["varian"]) == 6, f"status={st}")
    induk_id = h["data"]["id"]
    async with pool.acquire() as c:
        ip = await c.fetchrow("SELECT is_matrix_parent, track_inventory, item_type, matrix_parent_id, item_code, base_unit FROM products WHERE tenant_id = $1 AND id = $2::uuid", tid, induk_id)
        anak = await c.fetch("SELECT nama_produk, matrix_attributes, track_inventory, base_unit, sales_price, purchase_price, item_code, matrix_parent_id::text AS mp, item_type FROM products WHERE tenant_id = $1 AND matrix_parent_id = $2::uuid ORDER BY nama_produk", tid, induk_id)
        cek(f"{tid} T1 induk: is_matrix_parent, tanpa stok, barang, tanpa induk lagi",
            ip["is_matrix_parent"] and ip["track_inventory"] is False and ip["item_type"] == "goods" and ip["matrix_parent_id"] is None and (ip["item_code"] or "").startswith("BRG-"))
        kombinasi = sorted(json.dumps(json.loads(a["matrix_attributes"]) if isinstance(a["matrix_attributes"], str) else dict(a["matrix_attributes"]), sort_keys=True) for a in anak)
        harap = sorted(json.dumps({"ukuran": u, "warna": w}, sort_keys=True) for u in ("S", "M", "L") for w in ("Hitam", "Putih"))
        cek(f"{tid} T1 anak: 6 kombinasi benar, berstok, satuan+harga induk, kode barang unik",
            len(anak) == 6 and kombinasi == harap and all(a["track_inventory"] and a["base_unit"] == "Pcs" and float(a["sales_price"]) == 75000.0
                                                          and float(a["purchase_price"]) == 40000.0 and a["item_type"] == "goods" for a in anak)
            and len({a["item_code"] for a in anak}) == 6 and all((a["item_code"] or "").startswith("BRG-") for a in anak), f"nama={[a['nama_produk'] for a in anak][:2]}")

        # ---- T2 (satu dimensi: kontrak pencarian chat)
    satu = f"UJI SatuDim {stamp}"
    st1, h1, _ = await buat(tid, badan(satu, [{"nama": "ukuran", "nilai": ["XL", "S", "M", "L"]}]))
    async with pool.acquire() as c:
        hasil = await CB.cari(c, tid, f"UJI SatuDim {stamp}")
        sama = [x for x in hasil if x["nama"] == satu]
        cek(f"{tid} T2 pencarian chat: induk satu dimensi muncul SEKALI dengan ukuran S,M,L,XL berurut + harga",
            st1 == 201 and len(sama) == 1 and [u["ukuran"] for u in sama[0]["ukuran"]] == ["S", "M", "L", "XL"] and all(u["harga"] == 75000 for u in sama[0]["ukuran"]),
            str([u["ukuran"] for u in sama[0]["ukuran"]]) if sama else "tak muncul")
        dua_hasil = [x for x in await CB.cari(c, tid, nama) if x["nama"] == nama]
        cek(f"{tid} T2 anak tak muncul sebagai barang dasar (hanya induk)", len(dua_hasil) == 1 and not [x for x in await CB.cari(c, tid, f"{nama} S Hitam") if x["nama"].endswith("S Hitam")])
        INFO[tid] = [u["ukuran"] for u in dua_hasil[0]["ukuran"]] if dua_hasil else None

    # ---- T3
    async with pool.acquire() as c:
        n1 = await jumlah(c, tid)
    cek(f"{tid} T3 jumlah barang bertambah tepat 1 induk + 6 + 1 + 4", n1 - n0 == 12, f"delta={n1 - n0}")
    pv = (await MR.pratinjau_matriks(req(tid), badan(nama, dua)))["data"]
    cek(f"{tid} T3 pratinjau melaporkan 7 nama bentrok (induk + 6) dan ok=false", pv["ok"] is False and len(pv["blocks"]) == 7 and pv["jumlah"] == 6, f"blocks={len(pv['blocks'])}")
    st, d, _ = await buat(tid, badan(nama, dua))
    async with pool.acquire() as c:
        cek(f"{tid} T3 POST ulang -> 409 VARIAN_DITOLAK, jumlah barang TIDAK berubah", st == 409 and d["code"] == "VARIAN_DITOLAK" and await jumlah(c, tid) == n1)

    # ---- T4 atomik
    async with pool.acquire() as c:
        bentrok = f"UJI Atomik {stamp} M"
        await c.execute("INSERT INTO products (tenant_id, nama_produk, satuan, base_unit, status) VALUES ($1, $2, 'Pcs', 'Pcs', 'active')", tid, bentrok)
        n2 = await jumlah(c, tid)
    st, d, _ = await buat(tid, badan(f"UJI Atomik {stamp}", [{"nama": "ukuran", "nilai": ["S", "M", "L"]}]))
    async with pool.acquire() as c:
        induk_ada = await c.fetchval("SELECT count(*) FROM products WHERE tenant_id = $1 AND nama_produk = $2", tid, f"UJI Atomik {stamp}")
        cek(f"{tid} T4 satu anak bentrok -> 409 dan induk TIDAK terbuat (atomik)", st == 409 and induk_ada == 0 and await jumlah(c, tid) == n2)

    # ---- T5 tambah
    st5, h5 = 0, None
    try:
        h5 = await MR.tambah_varian(req(tid), __import__("uuid").UUID(st1 == 201 and h1["data"]["id"]), TambahVarianRequest(dimensi=[{"nama": "ukuran", "nilai": ["XXL"]}]), Response())
        st5 = 201
    except HTTPException as e:
        st5, h5 = e.status_code, e.detail
    cek(f"{tid} T5 tambah varian XXL ke induk satu dimensi", st5 == 201 and len(h5["data"]["varian"]) == 1 and h5["data"]["varian"][0]["name"] == f"{satu} XXL", str(h5)[:120])
    try:
        await MR.tambah_varian(req(tid), __import__("uuid").UUID(h1["data"]["id"]), TambahVarianRequest(dimensi=[{"nama": "warna", "nilai": ["Hitam"]}]), Response())
        cek(f"{tid} T5 dimensi beda dengan varian yang ada -> 422", False, "tak ditolak")
    except HTTPException as e:
        cek(f"{tid} T5 dimensi beda dengan varian yang ada -> 422", e.status_code == 422 and "ukuran" in e.detail["message"], e.detail["message"])
    lain = [t for t in TENANT if t != tid][0]
    try:
        await MR.detail_matriks(req(lain), __import__("uuid").UUID(induk_id))
        cek(f"{tid} T5 induk milik tenant lain -> 404 (isolasi)", False)
    except HTTPException as e:
        cek(f"{tid} T5 induk milik tenant lain -> 404 (isolasi)", e.status_code == 404)

    # ---- T6
    async with pool.acquire() as c:
        d6 = await MR.daftar_matriks(req(tid), cari=f"uji", limit=50, offset=0)
        ada = {x["name"]: x["jumlah_varian"] for x in d6["items"]}
        cek(f"{tid} T6 daftar memuat kedua induk dengan jumlah varian benar", ada.get(nama) == 6 and ada.get(satu) == 5, str({k: v for k, v in ada.items() if "UJI" in k}))
        det = (await MR.detail_matriks(req(tid), __import__("uuid").UUID(induk_id)))["data"]
        cek(f"{tid} T6 detail: 6 varian beratribut", len(det["varian"]) == 6 and all(set(v["atribut"]) == {"ukuran", "warna"} for v in det["varian"]))
        n3 = await jumlah(c, tid)

    # ---- T7 idempotensi
    kunci = f"idem-{stamp}-{tid[:4]}"
    b7 = badan(f"UJI Idem {stamp}", [{"nama": "ukuran", "nilai": ["S", "M"]}])
    s_a, h_a, _ = await buat(tid, b7, kunci)
    s_b, h_b, r_b = await buat(tid, b7, kunci)
    async with pool.acquire() as c:
        n4 = await jumlah(c, tid)
    cek(f"{tid} T7 kunci sama + isi sama = replay (id sama, header, jumlah tetap)",
        s_a == s_b == 201 and h_a["data"]["id"] == h_b["data"]["id"] and r_b.headers.get("X-Idempotent-Replay") == "true" and n4 - n3 == 3, f"delta={n4 - n3}")
    s_c, d_c, _ = await buat(tid, badan(f"UJI Idem {stamp} Lain", [{"nama": "ukuran", "nilai": ["S"]}]), kunci)
    cek(f"{tid} T7 kunci sama + isi beda -> 409 IDEMPOTENCY_KEY_REUSED", s_c == 409 and d_c["code"] == "IDEMPOTENCY_KEY_REUSED")

    # ---- T8 balapan
    b8 = badan(f"UJI Balap {stamp}", [{"nama": "ukuran", "nilai": ["S", "M", "L"]}])
    hasil8 = await asyncio.gather(buat(tid, b8), buat(tid, b8))
    kode = sorted(x[0] for x in hasil8)
    async with pool.acquire() as c:
        induk8 = await c.fetchval("SELECT count(*) FROM products WHERE tenant_id = $1 AND nama_produk = $2 AND deleted_at IS NULL", tid, f"UJI Balap {stamp}")
        anak8 = await c.fetchval("SELECT count(*) FROM products WHERE tenant_id = $1 AND nama_produk LIKE $2 AND matrix_parent_id IS NOT NULL", tid, f"UJI Balap {stamp} %")
    cek(f"{tid} T8 balapan serentak: tepat satu 201 + satu 409, satu induk + 3 anak (tanpa setengah jadi)",
        kode == [201, 409] and induk8 == 1 and anak8 == 3, f"status={kode} induk={induk8} anak={anak8}")
    async with pool.acquire() as c:
        cek(f"{tid} T0 induk matriks lama tak tersentuh", await c.fetchval("SELECT count(*) FROM products WHERE tenant_id = $1 AND is_matrix_parent", tid) - parents0 == 4)


async def utama():
    pool = await MR.get_pool()
    async with pool.acquire() as c:
        db = await c.fetchval("SELECT current_database()")
        if not db.startswith("scratch_varian"):
            print(f"TOLAK: bukan salinan ({db})")
            return 2
    for tid in TENANT:
        await periksa(pool, tid)
    print(f"[info] ukuran yang dilihat pencarian chat utk induk UKURAN x WARNA (2 dimensi): {INFO}", flush=True)
    print(f"HASIL {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    return 0 if HASIL and all(HASIL) else 1


sys.exit(asyncio.run(utama()))
