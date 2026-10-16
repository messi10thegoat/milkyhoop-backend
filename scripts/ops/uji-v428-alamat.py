"""Harness V428 alamat pelanggan terstruktur -- HANYA terhadap DB scratch milkydb_v428 (scripts/gate_v428_alamat.sh).
argv[1] = lama | baru. lama (kode pohon utama): buang respons GET /customers + detail pelanggan benih ke /out.
baru (kode cabang): respons lama identik untuk kunci lama, lalu migrasi + cermin + operasi per baris + invarian DB.
Pembaca lama lain (faktur/SJ/penawaran/e-Faktur/pengiriman) tak diubah di tahap 1 dan membaca kolom datar ->
identitasnya = M2 (kolom datar byte-identik sesudah migrasi + cermin)."""
import asyncio, inspect, json, os, sys
sys.path.insert(0, "/app"); os.environ.setdefault("OPENAI_API_KEY", "x")
import asyncpg
from types import SimpleNamespace
from uuid import UUID
from fastapi import HTTPException

MODE = sys.argv[1]
T = "kaos-biru-konveksi"
C = lambda n: UUID(f"00000000-0000-4000-8000-{n:012d}")
gagal = 0


def cek(ok, label, info=""):
    global gagal
    print(("PASS " if ok else "FAIL ") + label + (f" | {info}" if info else "")); gagal += 0 if ok else 1


def js(o):
    return json.loads(json.dumps(o, sort_keys=True, default=str))


def kw(fn, **o):
    d = {n: getattr(p.default, "default", p.default) for n, p in inspect.signature(fn).parameters.items()
         if n not in ("request",) and p.default is not inspect.Parameter.empty}
    d.update(o); return d


async def main():
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    assert await c.fetchval("SELECT current_database()") == "milkydb_v428", "BUKAN DB scratch -- berhenti"
    from backend.api_gateway.app.routers import customers as CU
    class Pool:
        def acquire(self_):
            class A:
                async def __aenter__(s): return c
                async def __aexit__(s, *a): return False
            return A()
    async def gp(): return Pool()
    CU.get_pool = gp
    req = SimpleNamespace(state=SimpleNamespace(user={"tenant_id": T, "user_id": "00000000-0000-4000-8000-0000000000ff", "username": "uji"}), headers={})
    benih = [C(i) for i in range(1, 13)]
    dump = {"list": js(await CU.list_customers(req, **kw(CU.list_customers, limit=100)))}
    for i in benih:
        dump[str(i)] = js(await CU.get_customer(req, str(i)))
    if MODE == "lama":
        json.dump(dump, open("/out/v428_lama.json", "w"), sort_keys=True)
        print("RINGKAS gagal=0"); await c.close(); return
    # ---------------- baru
    lama = json.load(open("/out/v428_lama.json"))
    beda = []
    for k, v in lama.items():
        b = dump[k]
        if k == "list":
            if b != v: beda.append(k)
        else:
            bd = {kk: vv for kk, vv in b["data"].items() if kk in v["data"]}
            if bd != v["data"] or b["success"] != v["success"]: beda.append(k)
    cek(not beda, f"respons lama identik (GET /customers + detail {len(benih)} pelanggan, kunci lama)", str(beda))
    from backend.api_gateway.app.services import alamat_pelanggan as AP
    from backend.api_gateway.app.services.penawaran_cetak import alamat_pelanggan as alamat_penawaran
    # M1 migrasi
    rows = {r["customer_id"]: r for r in await c.fetch("SELECT * FROM customer_addresses")}
    cust = {r["id"]: r for r in await c.fetch("SELECT * FROM customers")}
    isi = lambda v: v is not None and v.strip() != ""
    harap = {i for i, r in cust.items() if any(isi(r[k]) for k in ("alamat", "city", "province", "postal_code"))}
    cek(set(rows) == harap, "M1 satu baris per pelanggan beralamat (spasi/kosong tak dibuat)", f"{len(rows)} vs {len(harap)}")
    ok = all(r["is_default_billing"] and r["is_default_shipping"] and r["sumber"] == "migrasi_v428"
             and r["addr1"] == (cust[i]["alamat"] if isi(cust[i]["alamat"]) else None)
             and r["city"] == (cust[i]["city"] if isi(cust[i]["city"]) else None)
             and r["state"] == (cust[i]["province"] if isi(cust[i]["province"]) else None)
             and r["zip"] == (cust[i]["postal_code"] if isi(cust[i]["postal_code"]) else None)
             and r["country"] == cust[i]["negara"] and r["addr2"] is None and r["addr3"] is None
             for i, r in rows.items())
    cek(ok, "M1 addr1 = alamat APA ADANYA (byte, termasuk spasi tepi), kota/provinsi/kode pos/negara apa adanya")
    cek(rows[C(13)]["tenant_id"] == "grapgrap-manado", "M1 tenant baris = tenant pelanggan")
    so = {r["order_number"]: r["shipping_address_id"] for r in await c.fetch("SELECT order_number, shipping_address_id FROM sales_orders")}
    cek(so == {"TES-SO-1": rows[C(1)]["id"], "TES-SO-2": rows[C(2)]["id"], "TES-SO-3": None, "TES-SO-4": None},
        "M1 SO ditautkan hanya bila teks kirim == alamat pelanggan (btrim)", str(so))
    # M2 cermin identitas
    sebelum = {r["id"]: tuple(r) for r in await c.fetch("SELECT id, alamat, city, province, postal_code FROM customers")}
    for i in rows:
        async with c.transaction():
            await AP.cermin_ke_datar(c, rows[i]["tenant_id"], i)
    sesudah = {r["id"]: tuple(r) for r in await c.fetch("SELECT id, alamat, city, province, postal_code FROM customers")}
    cek(sebelum == sesudah, "M2 cermin bawaan tagih -> kolom datar byte-identik (pembaca lama faktur/SJ/e-Faktur/pengiriman)")
    # M3 format == aturan penawaran (kota) bila provinsi & kode pos kosong
    bandingan = [(i, AP.format_alamat(dict(r)), alamat_penawaran(cust[i]["alamat"], None, cust[i]["city"]))
                 for i, r in rows.items() if not r["state"] and not r["zip"] and r["country"] in (None, "IDN")]
    cek(all(a == b for _, a, b in bandingan), f"M3 format_alamat == penawaran alamat_pelanggan ({len(bandingan)} pelanggan)",
        str([x for x in bandingan if x[1] != x[2]]))
    cek(AP.format_alamat(dict(rows[C(4)])) == "Jl. Contoh 4, Bitung, Sulawesi Utara, 95511", "M3 lengkap: kota, provinsi, kode pos")
    cek(AP.format_alamat(dict(rows[C(9)])) == "1 Main St, Singapore, 018989, SGP", "M3 luar negeri: kode negara di akhir")
    # M4 idempoten
    n0 = await c.fetchval("SELECT count(*) FROM customer_addresses")
    await c.execute(open("/app/backend/migrations/V428__customer_addresses.sql").read())
    cek(await c.fetchval("SELECT count(*) FROM customer_addresses") == n0, "M4 migrasi diulang = tanpa baris baru")
    # ---- operasi per baris lewat RUTE (izin di middleware; di sini logika)
    from backend.api_gateway.app.routers import alamat_pelanggan as R
    from backend.api_gateway.app.schemas.customers import AlamatPelangganMasuk, BawaanAlamat
    R.get_pool = gp
    k = C(10)
    a2 = (await R.tambah_alamat(req, k, AlamatPelangganMasuk(label="Gudang", addr1="Jl. Gudang 2", city="Bitung")))["data"]
    cek(not a2["is_default_billing"] and not a2["is_default_shipping"], "O1 alamat kedua bukan bawaan")
    d = (await R.jadikan_alamat_bawaan(req, k, UUID(a2["id"]), BawaanAlamat(shipping=True)))["data"]
    st = await c.fetchrow("SELECT count(*) FILTER (WHERE is_default_shipping) ks, count(*) FILTER (WHERE is_default_billing) kt FROM customer_addresses WHERE customer_id=$1", k)
    cek(d["is_default_shipping"] and st["ks"] == 1 and st["kt"] == 1, "O2 pindah bawaan kirim: tetap tepat satu")
    flat = await c.fetchrow("SELECT alamat, city FROM customers WHERE id=$1", k)
    cek(tuple(flat) == ("Jl. API 10", "Manado"), "O2 bawaan KIRIM pindah -> kolom datar (tagih) tak berubah")
    await R.jadikan_alamat_bawaan(req, k, UUID(a2["id"]), BawaanAlamat(billing=True))
    flat = await c.fetchrow("SELECT alamat, city, province, postal_code FROM customers WHERE id=$1", k)
    cek(tuple(flat) == ("Jl. Gudang 2", "Bitung", None, None), "O3 bawaan TAGIH pindah -> kolom datar = cermin baris baru", str(tuple(flat)))
    await R.ubah_alamat(req, k, UUID(a2["id"]), AlamatPelangganMasuk(addr2="Blok B"))
    r2 = await c.fetchrow("SELECT * FROM customer_addresses WHERE id=$1", UUID(a2["id"]))
    flat = await c.fetchval("SELECT alamat FROM customers WHERE id=$1", k)
    cek(r2["label"] == "Gudang" and r2["city"] == "Bitung" and r2["addr2"] == "Blok B", "O4 PATCH hanya medan dikirim")
    cek(flat == "Jl. Gudang 2, Blok B", "O4 cermin alamat = addr1, addr2 digabung", flat)
    try:
        await R.hapus_alamat(req, k, UUID(a2["id"])); cek(False, "O5 hapus bawaan ditolak")
    except HTTPException as e:
        cek(e.status_code == 409 and e.detail["code"] == "ALAMAT_BAWAAN", "O5 hapus bawaan -> 409 ALAMAT_BAWAAN")
    a1 = rows[k]["id"]
    await R.hapus_alamat(req, k, a1)
    cek(len((await R.daftar_alamat(req, k))["data"]) == 1, "O6 hapus alamat biasa = nonaktif, hilang dari daftar")
    cek(await c.fetchval("SELECT is_active FROM customer_addresses WHERE id=$1", a1) is False, "O6 baris tetap ada (rujukan SO)")
    try:
        await R.tambah_alamat(req, k, AlamatPelangganMasuk(label="kosong")); cek(False, "O7 alamat tanpa isi ditolak")
    except HTTPException as e:
        cek(e.status_code == 422, "O7 alamat tanpa jalan/kota/provinsi/kode pos -> 422")
    try:
        await R.daftar_alamat(req, C(13)); cek(False, "O8 pelanggan tenant lain")
    except HTTPException as e:
        cek(e.status_code == 404, "O8 pelanggan tenant lain -> 404")
    cek(await c.fetchval("SELECT count(*) FROM customer_activities WHERE customer_id=$1 AND description LIKE 'Alamat%'", k) >= 4,
        "O9 riwayat pelanggan mencatat operasi alamat")
    # ---- API LAMA (PATCH /customers) -> cermin ke baris
    await CU.update_customer(req, str(k), CU.UpdateCustomerRequest(address="Jl. Baru 99"))
    r2 = await c.fetchrow("SELECT addr1, addr2, city FROM customer_addresses WHERE id=$1", UUID(a2["id"]))
    cek(tuple(r2) == ("Jl. Baru 99", None, "Bitung"), "L1 PATCH address lama -> addr1 baru, addr2/3 dikosongkan (form lama memegang seluruh jalan)", str(tuple(r2)))
    await CU.update_customer(req, str(k), CU.UpdateCustomerRequest(city="Tondano"))
    r2 = await c.fetchrow("SELECT addr1, city FROM customer_addresses WHERE id=$1", UUID(a2["id"]))
    cek(tuple(r2) == ("Jl. Baru 99", "Tondano"), "L2 PATCH city saja -> jalan tak disentuh")
    await CU.update_customer(req, str(k), CU.UpdateCustomerRequest(address=None, city=None, province=None, postal_code=None))
    cek(await c.fetchval("SELECT count(*) FROM customer_addresses WHERE customer_id=$1 AND is_active", k) == 0,
        "L3 alamat datar dikosongkan -> baris bawaan dilepas (nonaktif)")
    await CU.update_customer(req, str(k), CU.UpdateCustomerRequest(name="TES E2E untuk diuji API 2"))
    cek(await c.fetchval("SELECT count(*) FROM customer_addresses WHERE customer_id=$1 AND is_active", k) == 0,
        "L4 PATCH tanpa medan alamat -> tak menyentuh alamat")
    baru = await CU.create_customer(req, CU.CreateCustomerRequest(
        name="TES E2E buat beralamat", address="Jl. Buat 1", city="Manado",
        shipping_address={"addr1": "Jl. Kirim 2", "city": "Bitung"}))
    nid = baru["data"]["id"]
    al = (await CU.get_customer(req, nid))["data"]
    cek(len(al["addresses"]) == 2 and al["default_billing_address_id"] != al["default_shipping_address_id"]
        and al["address"] == "Jl. Buat 1", "L5 POST /customers + shipping_address -> bawaan tagih & kirim terpisah")
    baru = await CU.create_customer(req, CU.CreateCustomerRequest(name="TES E2E buat tanpa alamat"))
    cek((await CU.get_customer(req, baru["data"]["id"]))["data"]["addresses"] == [], "L6 POST tanpa alamat -> tanpa baris")
    # ---- invarian DB (kontrol merah di DB)
    for label, sql in (
        ("D1 indeks unik: dua bawaan tagih ditolak",
         f"INSERT INTO customer_addresses (tenant_id, customer_id, addr1, is_default_billing) VALUES ('{T}', '{C(1)}', 'x', true)"),
        ("D2 trigger: tenant beda dari pelanggan ditolak",
         f"INSERT INTO customer_addresses (tenant_id, customer_id, addr1) VALUES ('grapgrap-manado', '{C(1)}', 'x')"),
        ("D3 CHECK: bawaan nonaktif ditolak",
         f"UPDATE customer_addresses SET is_active=false WHERE customer_id='{C(2)}' AND is_default_billing"),
        ("D4 CHECK: baris aktif tanpa isi ditolak",
         f"INSERT INTO customer_addresses (tenant_id, customer_id, label) VALUES ('{T}', '{C(1)}', 'kosong')"),
        ("D5 CHECK: negara bukan 3 huruf besar ditolak",
         f"INSERT INTO customer_addresses (tenant_id, customer_id, addr1, country) VALUES ('{T}', '{C(1)}', 'x', 'id1')")):
        try:
            async with c.transaction():
                await c.execute(sql)
            cek(False, label)
        except asyncpg.PostgresError:
            cek(True, label)
    # ---- gabung pelanggan
    src = C(12)
    pv = await CU._gabung_rincian(c, T, C(2), [src])
    async with c.transaction():
        n = await AP.pindah_saat_gabung(c, T, C(2), [src])
    st = await c.fetchrow("SELECT count(*) FILTER (WHERE is_default_billing) kt, count(*) n FROM customer_addresses WHERE customer_id=$1", C(2))
    cek(pv[0]["customer_addresses"] == n == 1 and st["kt"] == 1 and st["n"] == 2,
        "G1 gabung: alamat sumber pindah sebagai alamat biasa, bawaan tujuan tetap satu")
    await c.close()
    print("RINGKAS gagal=%d" % gagal)

asyncio.run(main())
