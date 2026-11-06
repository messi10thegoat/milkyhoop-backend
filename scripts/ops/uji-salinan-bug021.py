"""BUG-021 uji DB di SALINAN (bukan prod): kode cabang feat/impor-barang-r2 (rencana/tulis/rute ASLI) atas klon milkydb.
T1 atomik (blok baris 5 + galat di tengah tulis) · T2 paritas pratinjau == tulis · T3 Law 28 · T4 replay idem. Tiap T punya kontrol merah."""
import asyncio
import os
import sys
import uuid
from decimal import Decimal

import asyncpg
from fastapi import HTTPException, Response
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import items as R  # noqa: E402
from app.services import impor_barang as IB  # noqa: E402

NYAWIT, KOSONG = "pt-kita-nyawit-bersama", "adhita-ariyani"
HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(ok)
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


async def sambung():
    return await asyncpg.connect(**R.settings.get_db_config())


async def ctx_dari(conn, tid, kunci=None):
    uid = await conn.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY (role::text = \'OWNER\') DESC LIMIT 1', tid)
    req = Request({"type": "http", "method": "POST", "path": "/api/items/impor",
                   "headers": [(b"x-idempotency-key", kunci.encode())] if kunci else []})
    req.state.user = {"tenant_id": tid, "user_id": str(uid), "username": "uji-salinan"}
    return R.get_user_context(req), req


TABEL = ["products", "unit_conversions", "journal_entries", "inventory_ledger", "item_categories", "product_units", "item_activities"]


async def hitung(conn, tid):
    return {t: await conn.fetchval(f"SELECT count(*) FROM {t} WHERE tenant_id = $1", tid) for t in TABEL}


def selisih(a, b):
    return {k: b[k] - a[k] for k in a if b[k] != a[k]}


def sembilan(tag):
    """9 barang grosir: Excel (kolom) + chatbox (teks), satuan besar baru 'Jeriken'+tag, kategori baru."""
    s = f"U21{tag}"
    baris = [{"nama": f"{s} Minyak Bimoli 2L", "satuan_dasar": "Pcs", "satuan_besar": "Karton", "isi": 6,
              "harga_jual_besar": "243rb", "harga_beli_besar": "210000", "kategori": f"Minyak {s}"},
             {"nama": f"{s} Minyak Sania 1L", "satuan_dasar": "Pcs", "harga_jual": 19500, "harga_beli": "17.500", "kategori": f"Minyak {s}"},
             {"nama": f"{s} Minyak curah", "satuan_dasar": "Liter", "satuan_besar": f"Jeriken{tag}", "isi": 18, "harga_jual": "16rb",
              "harga_jual_besar": 280000},
             {"nama": f"{s} Ongkos antar", "tipe": "jasa", "satuan_dasar": "Kali", "harga_jual": 15000}]
    baris += IB.tafsir_teks(f"{s} Filma 2L karton isi 6, beli 205rb, jual 238rb\n"
                            f"{s} Tropical 2L karton=12 pcs jual 36rb\n"
                            f"{s} Fortune 1L jual 18rb beli 16rb\n"
                            f"{s} Kunci Mas 2L kode {s}-KM2 jual 34rb\n"
                            f"{s} Sovia 5L jerigen isi 4 jual 380rb/jerigen")
    return baris


async def T1(conn):
    print("== T1 ATOMIK", flush=True)
    ctx, _ = await ctx_dari(conn, NYAWIT)
    b = sembilan("a")
    b[4] = {**b[4], "isi": None}  # baris 5 rusak: satuan besar tanpa isi
    a = await hitung(conn, NYAWIT)
    try:
        async with conn.transaction():
            await IB.tulis(conn, ctx, b, ctx["user_id"], "uji")
        cek("T1a baris 5 rusak ditolak", False, "tulis lolos")
    except HTTPException as e:
        no = [x["no"] for x in e.detail["baris"]]
        cek("T1a baris 5 rusak -> 422 IMPOR_DITOLAK, hanya baris 5", e.status_code == 422 and no == [5], f"{e.status_code} {no}")
    d = selisih(a, await hitung(conn, NYAWIT))
    cek("T1a 0 barang / 0 jurnal / 0 tulis apa pun", d == {}, str(d))

    # T1b galat DI TENGAH tulis (balapan sesudah validasi): barang 1-4 sudah tertulis di tx, baris 5 melempar -> rollback seluruhnya
    asli, n = R.tulis_barang_baru, {"i": 0}

    async def palsu(conn_, ctx_, body, uid, un):
        n["i"] += 1
        if n["i"] == 5:
            raise HTTPException(status_code=409, detail="Nama barang sudah dipakai (balapan, uji).")
        return await asli(conn_, ctx_, body, uid, un)
    R.tulis_barang_baru = palsu
    try:
        b = sembilan("b")
        a = await hitung(conn, NYAWIT)
        try:
            async with conn.transaction():
                await IB.tulis(conn, ctx, b, ctx["user_id"], "uji")
        except HTTPException as e:
            cek("T1b galat baris 5 saat tulis -> 409", e.status_code == 409 and n["i"] == 5, f"{e.status_code} panggilan={n['i']}")
        d = selisih(a, await hitung(conn, NYAWIT))
        cek("T1b sesudah 4 barang tertulis lalu galat: 0 sisa di DB", d == {}, str(d))
        # KONTROL MERAH alat ukur: tiap baris di tx sendiri (bukan atomik) -> alat ukur WAJIB melihat 4 barang tersisa
        n["i"], a = 0, await hitung(conn, NYAWIT)
        for x in sembilan("c"):
            try:
                async with conn.transaction():
                    await IB.tulis(conn, ctx, [x], ctx["user_id"], "uji")
            except HTTPException:
                break
        d = selisih(a, await hitung(conn, NYAWIT))
        cek("T1 KONTROL MERAH: tulis per-baris non-atomik -> alat ukur melihat 4 barang", d.get("products") == 4, str(d))
    finally:
        R.tulis_barang_baru = asli


async def paritas(conn, tid, r, ids):
    beda = []
    for x, h in zip(r["baris"], ids):
        p = await conn.fetchrow("SELECT item_code, nama_produk, base_unit, kategori, sales_price, purchase_price FROM products WHERE id = $1",
                                uuid.UUID(h["id"]))
        rc = x["rencana"]
        konv = [f"1 {c['conversion_unit']} = {Decimal(str(c['conversion_factor'])).normalize():f} {p['base_unit']}" for c in
                await conn.fetch("SELECT conversion_unit, conversion_factor FROM unit_conversions WHERE product_id = $1 AND is_active", p and uuid.UUID(h["id"]))]
        rk = [k.replace(k.split(" = ")[1].split(" ")[0], f"{Decimal(k.split(' = ')[1].split(' ')[0]).normalize():f}") for k in rc["konversi"]]
        harga_db = sorted([f"jual {Decimal(str(p['sales_price'] or 0)).normalize():f}", f"beli {Decimal(str(p['purchase_price'] or 0)).normalize():f}"])
        pl = x["payload"]
        harga_rc = sorted([f"jual {Decimal(str(pl.get('sales_price', 0))).normalize():f}", f"beli {Decimal(str(pl.get('purchase_price', 0))).normalize():f}"])
        for medan, v_r, v_d in (("kode", rc["item_code"], p["item_code"]), ("nama", rc["name"], p["nama_produk"]),
                                ("satuan", rc["base_unit"], p["base_unit"]), ("kategori", rc["kategori"], p["kategori"]),
                                ("konversi", sorted(rk), sorted(konv)), ("harga", harga_rc, harga_db)):
            if v_r != v_d:
                beda.append(f"baris {x['no']} {medan}: pratinjau={v_r} db={v_d}")
    return beda


async def T2(conn):
    print("== T2 PARITAS pratinjau == tulis", flush=True)
    ctx, _ = await ctx_dari(conn, NYAWIT)
    b = sembilan("d")
    tr = conn.transaction(readonly=True)
    await tr.start()
    r = await IB.rencana(conn, ctx, b)
    await tr.rollback()
    cek("T2 pratinjau 9 baris ok", r["ok"] and len(r["baris"]) == 9, str([x["blocks"] for x in r["baris"] if not x["ok"]]))
    a = await hitung(conn, NYAWIT)
    async with conn.transaction():
        t = await IB.tulis(conn, ctx, b, ctx["user_id"], "uji")
    d = selisih(a, await hitung(conn, NYAWIT))
    beda = await paritas(conn, NYAWIT, r, t["barang"])
    cek("T2 kode/nama/satuan/kategori/konversi/harga DB == pratinjau (9 baris)", not beda, "; ".join(beda[:5]))
    cek("T2 satuan & kategori baru == ringkas pratinjau, dibuat 1x", t["satuan_baru"] == r["ringkas"]["satuan_baru"]
        and t["kategori_baru"] == r["ringkas"]["kategori_baru"] and d.get("products") == 9, f"{t['satuan_baru']} {t['kategori_baru']} {d}")
    sat = await conn.fetchval("SELECT count(*) FROM product_units WHERE tenant_id = $1 AND lower(abbreviation) = 'jerikend'", NYAWIT)
    cek("T2 satuan baru Jerikend tepat 1 baris", sat == 1, str(sat))
    # KONTROL MERAH: sesi lain membuat barang dgn kode berikutnya di antara pratinjau & tulis -> pembanding WAJIB melihat beda kode
    b = sembilan("e")
    tr = conn.transaction(readonly=True)
    await tr.start()
    r = await IB.rencana(conn, ctx, b)
    await tr.rollback()
    kode1 = next(x["rencana"]["item_code"] for x in r["baris"] if x["payload"]["item_type"] == "goods")
    pre, num = kode1.rsplit("-", 1)
    await conn.execute("INSERT INTO products (id, tenant_id, nama_produk, item_code, item_type, base_unit) VALUES ($1, $2, $3, $4, 'goods', 'Pcs')",
                       uuid.uuid4(), NYAWIT, f"U21e penyela {uuid.uuid4().hex[:6]}", kode1)
    async with conn.transaction():
        t = await IB.tulis(conn, ctx, b, ctx["user_id"], "uji")
    beda = await paritas(conn, NYAWIT, r, t["barang"])
    cek("T2 KONTROL MERAH: kode disela sesi lain -> pembanding melihat beda kode", any("kode" in x for x in beda), "; ".join(beda[:2]))


async def T3(conn):
    print("== T3 LAW 28 (stok awal)", flush=True)
    stok = [{"nama": "U21f Minyak stok awal", "satuan_dasar": "Pcs", "harga_jual": 36000, "stok_awal": 10, "harga_pokok_awal": 31000}]
    ctx, _ = await ctx_dari(conn, NYAWIT)
    tr = conn.transaction(readonly=True)
    await tr.start()
    r = await IB.rencana(conn, ctx, stok)
    await tr.rollback()
    pesan = " ".join(str(bk.get("message")) for bk in r["baris"][0]["blocks"])
    cek("T3 usaha sudah bertransaksi (nyawit): blok per baris 'sudah punya transaksi'", not r["ok"] and "sudah punya transaksi" in pesan, pesan)
    a = await hitung(conn, NYAWIT)
    try:
        async with conn.transaction():
            await IB.tulis(conn, ctx, stok, ctx["user_id"], "uji")
        cek("T3 tulis ditolak", False, "lolos")
    except HTTPException as e:
        cek("T3 tulis 422, nol tulis", e.status_code == 422 and selisih(a, await hitung(conn, NYAWIT)) == {}, str(e.status_code))
    # KONTROL MERAH: usaha BELUM bertransaksi (adhita, 0 jurnal) -> baris sama TIDAK diblok; jurnal saldo awal == 10 x 31.000
    ctx2, _ = await ctx_dari(conn, KOSONG)
    a = await hitung(conn, KOSONG)
    async with conn.transaction():
        t = await IB.tulis(conn, ctx2, stok, ctx2["user_id"], "uji")
    d = selisih(a, await hitung(conn, KOSONG))
    deb = await conn.fetchval("""SELECT COALESCE(SUM(jl.debit), 0) FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id
                                 WHERE je.tenant_id = $1 AND je.source_type = 'OPENING_BALANCE'""", KOSONG)
    led = await conn.fetchval("SELECT COALESCE(SUM(quantity_in), 0) FROM inventory_ledger WHERE tenant_id = $1 AND product_id = $2",
                              KOSONG, uuid.UUID(t["barang"][0]["id"]))
    cek("T3 KONTROL MERAH: usaha kosong -> tertulis, jurnal saldo awal Σdebit == 310.000, kartu stok 10",
        Decimal(str(deb)) == Decimal("310000") and Decimal(str(led)) == 10 and d.get("journal_entries") == 1, f"{deb} {led} {d}")


async def T4(conn):
    print("== T4 REPLAY IDEMPOTENSI (rute POST /items/impor ASLI)", flush=True)
    k = f"u21-{uuid.uuid4().hex[:8]}"
    body = {"baris": sembilan("g")}
    _, req = await ctx_dari(conn, NYAWIT, k)
    a = await hitung(conn, NYAWIT)
    r1 = await R.impor_barang(req, body, Response())
    d1 = selisih(a, await hitung(conn, NYAWIT))
    rp = Response()
    r2 = await R.impor_barang(req, body, rp)
    d2 = selisih(a, await hitung(conn, NYAWIT))
    cek("T4 kirim ulang kunci sama: respons identik + X-Idempotent-Replay, barang tetap 9 (bukan 18)",
        r1 == r2 and rp.headers.get("X-Idempotent-Replay") == "true" and d1.get("products") == 9 and d2 == d1, f"{d1} {d2}")
    # KONTROL MERAH: kunci BEDA -> bukan replay (penulis jalan lagi, ditolak nama ganda) -> alat ukur membedakan replay vs tulis ulang
    _, req2 = await ctx_dari(conn, NYAWIT, k + "x")
    try:
        await R.impor_barang(req2, body, Response())
        cek("T4 KONTROL MERAH kunci beda", False, "lolos 200")
    except HTTPException as e:
        cek("T4 KONTROL MERAH: kunci beda -> bukan replay (422 nama sudah ada), barang tetap 9", e.status_code in (409, 422)
            and selisih(a, await hitung(conn, NYAWIT)) == d1, str(e.status_code))
    try:
        await R.impor_barang(req, {"baris": body["baris"][:3]}, Response())
        cek("T4 kunci sama isi beda", False, "lolos")
    except HTTPException as e:
        cek("T4 kunci sama + isi beda -> 409 IDEMPOTENCY_KEY_REUSED", e.status_code == 409, str(e.detail))


async def main():
    assert R.settings.DB_NAME.startswith("scratch_ib021_"), R.settings.DB_NAME  # PAGAR: tak pernah milkydb
    conn = await sambung()
    try:
        for t in (T1, T2, T3, T4):
            try:
                await t(conn)
            except Exception as e:
                import traceback
                traceback.print_exc()
                cek(f"{t.__name__} tanpa galat tak terduga", False, repr(e))
    finally:
        await conn.close()
    print(f"RINGKAS: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)


asyncio.run(main())
