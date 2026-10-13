"""UJI NYATA W-20261013-VENDORFIELD -- kaos, scratch DB dari backup terbaru + V420 (kolom nitku/negara), SATU transaksi, ROLLBACK.
Handler ASLI create/get/update pemasok; payload = persis yang dikirim useVendorForm.ts; baca balik per kunci dari DB dan
dari GET lewat VendorDetailResponse. MODE=master -> kode master (kontrol merah: kunci dibuang / 422)."""
import asyncio, os, sys, uuid
sys.path.insert(0, "/repo/backend/api_gateway")
import asyncpg
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request
import app.routers.vendors as V
from app.schemas.vendors import CreateVendorRequest as C, UpdateVendorRequest as U, VendorDetailResponse

T = "kaos-biru-konveksi"
USER = "0bccdb25-fdf0-4e99-9024-b9a20846f76c"
NITKU = "0123456789012345000000"
H = []


def cek(n, ok, info=""):
    H.append(bool(ok)); print(("PASS " if ok else "FAIL ") + n + (f" | {info}" if info else ""), flush=True)


class _Acq:
    def __init__(s, c): s.c = c
    async def __aenter__(s): return s.c
    async def __aexit__(s, *a): return False


class PoolSatu:
    def __init__(s, c): s.c = c
    def acquire(s): return _Acq(s.c)


def req(m="POST"):
    r = Request({"type": "http", "method": m, "headers": [], "path": "/", "query_string": b"", "state": {}})
    r.state.user = {"tenant_id": T, "user_id": USER, "role": "OWNER", "username": "TES E2E"}
    return r


def FE(nama):
    """Payload useVendorForm.ts L471-525 (form lengkap, vendor ORANG_PRIBADI)."""
    return {
        "name": nama, "company_name": nama, "display_name": nama, "email": "tes@e2e.id", "phone": "0431123",
        "mobile": "0812345", "website": "e2e.id", "vendor_type": "ORANG_PRIBADI", "tax_id": "0123456789012345",
        "nik": "3271011234567890", "is_pkp": False, "default_tax_code": "PPN_11", "default_pph_type": "PPH_23",
        "default_pph_rate": 2, "currency": "IDR", "payment_terms_days": 30, "address": "Jl Penagihan 1", "city": "Manado",
        "province": "Sulut", "postal_code": "95111", "notes": "tes", "account_number": "ACC-1", "credit_limit": 1000000,
        "bank_name": "BCA", "bank_account_number": "123", "bank_account_holder": "Budi", "nitku": NITKU, "negara": "IDN",
        "shipping_address": {"street": "Gudang 9", "city": "Bitung", "province": "Sulut", "postal_code": "95512"},
        "tax_address": {"street": "Jl Pajak 2", "city": "Tomohon", "province": "Sulut", "postal_code": "95362"},
        "contacts": [{"name": "Budi", "position": "Sales", "email": "b@e2e.id", "phone": "0431"},
                     {"name": "Sari", "position": "", "email": "", "phone": ""}],
    }


async def jejak(c):
    return [await c.fetchval(f"SELECT count(*) FROM {t} WHERE tenant_id=$1", T)
            for t in ("vendors", "vendor_contacts", "vendor_addresses", "vendor_activities")]


async def langkah(nama, coro):
    try:
        return await coro
    except HTTPException as e:
        return e
    except ValidationError as e:
        return e
    except Exception as e:  # noqa
        cek(f"GALAT {nama}", False, f"{type(e).__name__}: {str(e)[:160]}")
        return e


async def main():
    c = await asyncpg.connect(os.environ["URL"])
    sebelum = await jejak(c)
    tr = c.transaction(); await tr.start()
    try:
        async def gp(): return PoolSatu(c)
        V.get_pool = gp
        nama = f"TES E2E VENDORFIELD {uuid.uuid4().hex[:6]}"
        # --- 1 CREATE: payload FE persis
        try:
            body = C(**FE(nama))
        except ValidationError as e:
            cek("1 payload FE lolos skema CreateVendorRequest", False, str(e.errors()[0])[:150])
            body = C(**{k: v for k, v in FE(nama).items() if k not in ("tax_address", "shipping_address", "contacts")})
        r = await langkah("create", V.create_vendor(req(), body))
        vid = uuid.UUID(r["data"]["id"]) if isinstance(r, dict) else None
        row = await c.fetchrow("SELECT * FROM vendors WHERE id=$1", vid) if vid else None
        if row is None:
            cek("1 create menulis baris", False); raise SystemExit
        cek("1a mobile -> mobile_phone", row["mobile_phone"] == "0812345", str(row["mobile_phone"]))
        cek("1b nik tersimpan", row["nik"] == "3271011234567890", str(row["nik"]))
        cek("1c nitku & negara tersimpan", (row["nitku"], (row["negara"] or "").strip()) == (NITKU, "IDN"), f'{row["nitku"]}/{row["negara"]}')
        cek("1d tax_address terstruktur (jalan/kota/prov/pos)",
            (row["tax_address"], row["tax_city"], row["tax_province"], row["tax_postal_code"]) == ("Jl Pajak 2", "Tomohon", "Sulut", "95362"),
            str((row["tax_address"], row["tax_city"], row["tax_province"], row["tax_postal_code"])))
        cek("1e account_number tersimpan", row["account_number"] == "ACC-1", str(row["account_number"]))
        cek("1f default_tax_code/pph_type/pph_rate tersimpan",
            (row["default_tax_code"], row["default_pph_type"], float(row["default_pph_rate"] or 0)) == ("PPN_11", "PPH_23", 2.0),
            str((row["default_tax_code"], row["default_pph_type"], row["default_pph_rate"])))
        sh = await c.fetch("SELECT street, city, province, postal_code FROM vendor_addresses WHERE vendor_id=$1 AND tenant_id=$2 AND address_type='shipping'", vid, T)
        cek("1g shipping_address -> vendor_addresses", len(sh) == 1 and sh[0]["street"] == "Gudang 9" and sh[0]["postal_code"] == "95512", str([dict(x) for x in sh]))
        ct = await c.fetch("SELECT name, position, email FROM vendor_contacts WHERE vendor_id=$1 AND tenant_id=$2 ORDER BY name", vid, T)
        cek("1h contacts -> vendor_contacts (2 baris)", [x["name"] for x in ct] == ["Budi", "Sari"], str([dict(x) for x in ct]))
        # --- 2 GET detail lewat response_model (seperti HTTP)
        g = await langkah("get", V.get_vendor(req("GET"), vid))
        if isinstance(g, dict):
            d = VendorDetailResponse.model_validate(g).model_dump()["data"]
            cek("2a GET mobile & mobile_phone", d.get("mobile") == "0812345" and d.get("mobile_phone") == "0812345")
            cek("2b GET nik/nitku/negara/account_number", (d.get("nik"), d.get("nitku"), (d.get("negara") or "").strip(), d.get("account_number")) == ("3271011234567890", NITKU, "IDN", "ACC-1"))
            cek("2c GET tax_address objek", d.get("tax_address") == {"street": "Jl Pajak 2", "city": "Tomohon", "province": "Sulut", "postal_code": "95362"}, str(d.get("tax_address")))
            cek("2d GET shipping_address objek", (d.get("shipping_address") or {}).get("street") == "Gudang 9")
            cek("2e GET contacts[] dgn id, urutan input", [k["name"] for k in d.get("contacts", [])] == ["Budi", "Sari"] and all(k.get("id") for k in d["contacts"]))
            cek("2f GET default_*", (d.get("default_tax_code"), d.get("default_pph_type"), d.get("default_pph_rate")) == ("PPN_11", "PPH_23", 2.0))
        else:
            cek("2 GET detail", False, repr(g)[:150])
        # --- 3 PATCH hanya notes: anak tak tersentuh
        await langkah("patch notes", V.update_vendor(req("PATCH"), vid, U(notes="ubah")))
        n_ct = await c.fetchval("SELECT count(*) FROM vendor_contacts WHERE vendor_id=$1 AND tenant_id=$2", vid, T)
        n_sh = await c.fetchval("SELECT count(*) FROM vendor_addresses WHERE vendor_id=$1 AND tenant_id=$2", vid, T)
        cek("3 PATCH notes saja: kontak 2 & alamat 1 tak tersentuh", (n_ct, n_sh) == (2, 1), f"{n_ct},{n_sh}")
        # --- 4 PATCH dari form edit-dibuka-dari-baris-DAFTAR: contacts [] + shipping/tax null (FE kirim begitu)
        await langkah("patch fe-daftar", V.update_vendor(req("PATCH"), vid, U(**{"name": nama, "notes": "dari daftar", "contacts": [],
                                                                               "shipping_address": None, "tax_address": None})))
        n_ct = await c.fetchval("SELECT count(*) FROM vendor_contacts WHERE vendor_id=$1 AND tenant_id=$2", vid, T)
        n_sh = await c.fetchval("SELECT count(*) FROM vendor_addresses WHERE vendor_id=$1 AND tenant_id=$2", vid, T)
        ta = await c.fetchval("SELECT tax_address FROM vendors WHERE id=$1", vid)
        cek("4 contacts:[] + null alamat TANPA penanda: kontak 2, alamat 1, tax_address utuh", (n_ct, n_sh, ta) == (2, 1, "Jl Pajak 2"), f"{n_ct},{n_sh},{ta}")
        # --- 5 tambah kontak tanpa id; id=ubah
        ids = [k["id"] for k in (await V.get_vendor(req("GET"), vid))["data"]["contacts"]]
        await langkah("patch tambah", V.update_vendor(req("PATCH"), vid, U(contacts=[{"name": "Tono", "phone": "0899"}])))
        n_ct = await c.fetchval("SELECT count(*) FROM vendor_contacts WHERE vendor_id=$1 AND tenant_id=$2", vid, T)
        cek("5a kontak tanpa id = TAMBAH (3 baris), lama tak terhapus", n_ct == 3, str(n_ct))
        await langkah("patch id", V.update_vendor(req("PATCH"), vid, U(contacts=[{"id": ids[0], "name": "Budi Santoso", "position": "Sales", "email": "b@e2e.id", "phone": "0431"}])))
        nm = await c.fetchval("SELECT name FROM vendor_contacts WHERE id=$1 AND tenant_id=$2", uuid.UUID(ids[0]), T)
        cek("5b kontak dengan id = UBAH baris itu", nm == "Budi Santoso", str(nm))
        # --- 6 penanda eksplisit: ganti seluruhnya
        await langkah("replace", V.update_vendor(req("PATCH"), vid, U(contacts=[{"id": ids[0], "name": "Budi Santoso"}], contacts_replace=True)))
        sisa = await c.fetch("SELECT name FROM vendor_contacts WHERE vendor_id=$1 AND tenant_id=$2", vid, T)
        cek("6 contacts_replace=true: hanya yang ada di daftar tersisa", [x["name"] for x in sisa] == ["Budi Santoso"], str([x["name"] for x in sisa]))
        await langkah("hapus semua", V.update_vendor(req("PATCH"), vid, U(contacts=[], contacts_replace=True)))
        n_ct = await c.fetchval("SELECT count(*) FROM vendor_contacts WHERE vendor_id=$1 AND tenant_id=$2", vid, T)
        cek("7 [] + contacts_replace=true: semua terhapus", n_ct == 0, str(n_ct))
        # --- 8 tax_address objek lewat PATCH + nik pada BADAN
        await langkah("patch tax", V.update_vendor(req("PATCH"), vid, U(tax_address={"street": "Jl Baru 5", "city": "Manado"})))
        row = await c.fetchrow("SELECT tax_address, tax_city, tax_province FROM vendors WHERE id=$1", vid)
        cek("8 PATCH tax_address objek: jalan+kota, provinsi dikosongkan", tuple(row) == ("Jl Baru 5", "Manado", None), str(tuple(row)))
        badan = f"TES E2E VF-BADAN {uuid.uuid4().hex[:6]}"
        r2 = await langkah("create badan", V.create_vendor(req(), C(name=badan)))
        bid = uuid.UUID(r2["data"]["id"]) if isinstance(r2, dict) else None
        e = await langkah("nik badan", V.update_vendor(req("PATCH"), bid, U(nik="3271011234567890"))) if bid else None
        cek("9 PATCH nik ke pemasok BADAN = 422", isinstance(e, HTTPException) and e.status_code == 422, repr(e)[:100])
        # --- 10 pemasok lama (sebelum fitur) tetap terbaca
        lama = await c.fetchval("SELECT id FROM vendors WHERE tenant_id=$1 AND id<>$2 AND id<>$3 LIMIT 1", T, vid, bid)
        if lama:
            gl = VendorDetailResponse.model_validate(await V.get_vendor(req("GET"), lama)).model_dump()["data"]
            cek("10 pemasok lama terbaca: kunci baru None/kosong", gl.get("nik") is None and gl.get("shipping_address") is None and gl.get("contacts") == [], str(gl.get("contacts"))[:60])
    except SystemExit:
        pass
    except Exception as e:
        cek("HARNESS galat", False, f"{type(e).__name__}: {str(e)[:200]}")
    finally:
        await tr.rollback()
    cek("AKHIR nol jejak (vendors/kontak/alamat/aktivitas)", await jejak(c) == sebelum, f"{sebelum}")
    await c.close()
    print(f"RINGKAS {sum(H)}/{len(H)}")


asyncio.run(main())
