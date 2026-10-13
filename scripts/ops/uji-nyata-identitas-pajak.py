"""UJI NYATA V420 identitas pajak pelanggan -- kaos, SATU transaksi luar ROLLBACK (DDL V420 dipasang DI DALAM tx).
Handler ASLI create/get/list/update. Kontrol merah: kode master (kunci dibuang -> baca balik NULL)."""
import asyncio, os, sys, uuid
sys.path.insert(0, "/app/backend/api_gateway")
import asyncpg
from fastapi import HTTPException
from starlette.requests import Request
import app.routers.customers as CU
from app.schemas.customers import CreateCustomerRequest, UpdateCustomerRequest
T = "kaos-biru-konveksi"
USER = "0bccdb25-fdf0-4e99-9024-b9a20846f76c"
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
NITKU = "0123456789012345000000"
async def jumlah(c):
    return (await c.fetchval("SELECT count(*) FROM customers WHERE tenant_id=$1", T),
            await c.fetchval("SELECT count(*) FROM customer_activities WHERE tenant_id=$1", T),
            await c.fetchval("SELECT count(*) FROM information_schema.columns WHERE table_name IN ('customers','vendors') AND column_name IN ('nitku','jenis_id','negara','nomor_dokumen')"))
async def main():
    mig = open("/v420.sql").read()
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    sebelum = await jumlah(c)
    tr = c.transaction(); await tr.start()
    try:
        await c.execute("SET LOCAL lock_timeout = '3s'")
        await c.execute(mig)
        async def gp(): return PoolSatu(c)
        CU.get_pool = gp
        nama = f"TES E2E IDPAJAK {uuid.uuid4().hex[:6]}"
        badan = CreateCustomerRequest(name=nama, tax_id="0123456789012345", nitku="0123456789012345 000000",
                                      jenis_id="Passport", negara="sgp", nomor_dokumen="A1234567")
        r = await CU.create_customer(req(), badan)
        cid = r["data"]["id"]
        db = await c.fetchrow("SELECT nitku, jenis_id, negara, nomor_dokumen FROM customers WHERE id=$1", uuid.UUID(cid))
        cek("1 POST simpan: 4 kolom tertulis (ternormalisasi)", dict(db) == {"nitku": NITKU, "jenis_id": "Passport", "negara": "SGP", "nomor_dokumen": "A1234567"}, str(dict(db)))
        d = (await CU.get_customer(req("GET"), cid))
        d = d["data"] if isinstance(d, dict) else d.data
        d = d if isinstance(d, dict) else d.model_dump()
        cek("2 GET detail mengembalikan 4 kunci", (d.get("nitku"), d.get("jenis_id"), d.get("negara"), d.get("nomor_dokumen")) == (NITKU, "Passport", "SGP", "A1234567"))
        # response_model: jalankan validasi model respons seperti FastAPI
        from app.schemas.customers import CustomerDetailResponse, CustomerListResponse
        dv = CustomerDetailResponse.model_validate({"success": True, "data": d}).model_dump()["data"]
        cek("2b detail lewat response_model tak dipangkas", dv.get("nitku") == NITKU and dv.get("nomor_dokumen") == "A1234567")
        lst = await CU.list_customers(req("GET"), skip=0, limit=100, search=nama, tipe=None, is_active=None, sort_by="created_at", sort_order="desc")
        it = [x for x in lst["items"] if x["id"] == cid]
        lv = CustomerListResponse.model_validate(lst).model_dump()["items"]
        lv = [x for x in lv if x["id"] == cid]
        cek("3 GET daftar (lewat response_model) mengembalikan 4 kunci", it and lv and lv[0]["jenis_id"] == "Passport" and lv[0]["negara"] == "SGP" and lv[0]["nitku"] == NITKU)
        # PATCH: ganti ke NIK, kosongkan nomor dokumen
        await CU.update_customer(req("PATCH"), cid, UpdateCustomerRequest(jenis_id="TIN", negara="IDN", nomor_dokumen=None, nitku=None))
        db = await c.fetchrow("SELECT nitku, jenis_id, negara, nomor_dokumen FROM customers WHERE id=$1", uuid.UUID(cid))
        cek("4 PATCH ubah: TIN/IDN, nitku & dokumen dikosongkan", dict(db) == {"nitku": None, "jenis_id": "TIN", "negara": "IDN", "nomor_dokumen": None}, str(dict(db)))
        await CU.update_customer(req("PATCH"), cid, UpdateCustomerRequest(jenis_id="Other", nomor_dokumen="KITAS-9"))
        # PATCH yang hanya mengosongkan nomor dokumen saat DB = Other -> 422 bermedan (bukan 500 CheckViolation)
        try:
            await CU.update_customer(req("PATCH"), cid, UpdateCustomerRequest(nomor_dokumen=""))
            cek("5 PATCH kosongkan dokumen saat Other", False, "lolos")
        except HTTPException as e:
            cek("5 PATCH kosongkan dokumen saat Other = 422 IDENTITAS_PAJAK_TIDAK_LENGKAP", e.status_code == 422 and isinstance(e.detail, dict) and e.detail.get("code") == "IDENTITAS_PAJAK_TIDAK_LENGKAP", str(e.detail)[:120])
        db = await c.fetchrow("SELECT jenis_id, nomor_dokumen FROM customers WHERE id=$1", uuid.UUID(cid))
        cek("6 sesudah 422: DB tetap Other/KITAS-9", tuple(db) == ("Other", "KITAS-9"), str(tuple(db)))
        akt = await c.fetchval("SELECT description FROM customer_activities WHERE customer_id=$1 AND type<>'created' ORDER BY created_at DESC LIMIT 1", uuid.UUID(cid)) if False else None
        # pelanggan lama (sebelum V420) tetap terbaca: kunci None
        lama = await c.fetchval("SELECT id FROM customers WHERE tenant_id=$1 AND id<>$2 LIMIT 1", T, uuid.UUID(cid))
        d2 = (await CU.get_customer(req("GET"), str(lama)))["data"]
        cek("7 pelanggan lama: 4 kunci None (nol backfill)", all(d2.get(k) is None for k in ("nitku", "jenis_id", "negara", "nomor_dokumen")))
    except Exception as e:
        cek("HARNESS galat", False, f"{type(e).__name__}: {str(e)[:200]}")
    finally:
        await tr.rollback()
    cek("AKHIR nol jejak (pelanggan, aktivitas, kolom V420 tak terpasang)", await jumlah(c) == sebelum, str(sebelum))
    await c.close()
    print(f"RINGKAS {sum(H)}/{len(H)}")
asyncio.run(main())
