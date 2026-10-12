"""UJI NYATA unggahan form = baris documents BARU bernama sendiri, objek MinIO sama (12 Okt 2026). Kaos, SEMUA ROLLBACK.
Bytes = objek MinIO dokumen form kaos yang SUDAH ada (kunci sha sama -> PUT idempoten, tanpa objek baru). Handler ASLI
POST /api/uploads/document + GET /api/expenses/{id}/attachments (pembaca d.file_name di modul penaut)."""
import asyncio, io, os, sys, uuid
sys.path.insert(0, "/app/backend/api_gateway")
import asyncpg
from fastapi import UploadFile
from starlette.datastructures import Headers
from starlette.requests import Request

T = "kaos-biru-konveksi"
USER = "0bccdb25-fdf0-4e99-9024-b9a20846f76c"
DOK_LAMA = uuid.UUID("2f84a28e-2149-4398-81bd-134f9d8599ad")   # uji-gerbang-r137.png (source form)
EXP = uuid.UUID("c6587f45-97ba-4e22-8544-ac5cc96a2fe3")        # EXP-2609-0026 (void, tanpa lampiran)
HASIL = []


def cek(n, ok, info=""):
    HASIL.append((n, bool(ok))); print(("PASS " if ok else "FAIL ") + n + (f" | {info}" if info else ""), flush=True)


class _Acq:
    def __init__(self, c): self.c = c
    async def __aenter__(self): return self.c
    async def __aexit__(self, *a): return False


class PoolSatu:
    def __init__(self, c): self.c = c
    def acquire(self): return _Acq(self.c)


def req():
    r = Request({"type": "http", "method": "GET", "headers": [], "path": "/", "query_string": b"", "state": {}})
    r.state.user = {"tenant_id": T, "user_id": USER, "role": "OWNER"}
    return r


async def main():
    from app.routers import uploads as UP, expenses as EX
    from app.services import policy_engine_client as PEC
    from app.services.storage_service import get_storage_service
    pe_pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=2)
    PEC.init_policy_engine(pe_pool)
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    tr = c.transaction(); await tr.start()
    try:
        pool = PoolSatu(c)

        async def gp(): return pool
        UP.get_session_db_pool = gp
        EX.get_pool = gp
        lama = await c.fetchrow("SELECT file_path, file_name FROM documents WHERE id=$1", DOK_LAMA)
        st = get_storage_service()
        isi = st.client.get_object(Bucket=st.config.bucket, Key=lama["file_path"])["Body"].read()
        n0 = await c.fetchval("SELECT count(*) FROM documents WHERE tenant_id=$1", T)

        def berkas(nama):
            return UploadFile(file=io.BytesIO(isi), filename=nama, headers=Headers({"content-type": "image/png"}))
        o1 = (await UP.upload_document_for_form(req(), berkas("TES E2E nota-A.png")))["data"]
        o2 = (await UP.upload_document_for_form(req(), berkas("TES E2E nota-B.png")))["data"]
        rows = {r["id"]: r for r in await c.fetch("SELECT id, file_name, file_path FROM documents WHERE id = ANY($1::uuid[])",
                                                [uuid.UUID(o1["id"]), uuid.UUID(o2["id"])])}
        cek("1 dua unggahan isi sama -> dua baris BARU (bukan dokumen lama)", len({o1["id"], o2["id"], str(DOK_LAMA)}) == 3
            and await c.fetchval("SELECT count(*) FROM documents WHERE tenant_id=$1", T) == n0 + 2)
        cek("2 nama per baris = nama unggahan", [rows[uuid.UUID(o["id"])]["file_name"] for o in (o1, o2)] == ["TES E2E nota-A.png", "TES E2E nota-B.png"])
        cek("3 objek SAMA dengan dokumen lama (dedup objek)", {r["file_path"] for r in rows.values()} == {lama["file_path"]})
        cek("4 dokumen lama tak berubah", (await c.fetchval("SELECT file_name FROM documents WHERE id=$1", DOK_LAMA)) == lama["file_name"])
        await c.execute("""INSERT INTO document_attachments (tenant_id, document_id, entity_type, entity_id, attachment_type, display_order)
                           VALUES ($1, $2, 'expense', $3, 'attachment', 0)""", T, uuid.UUID(o2["id"]), EXP)
        lst = await EX.list_expense_attachments(req(), EXP)
        data = lst.get("data", lst) if isinstance(lst, dict) else lst
        nama = [x.get("file_name") or x.get("nama") or x.get("name") for x in (data if isinstance(data, list) else data.get("attachments", []))]
        cek("5 modul penaut (daftar lampiran biaya) menampilkan nama unggahan B", nama == ["TES E2E nota-B.png"], str(lst)[:300])
    finally:
        await tr.rollback(); await pe_pool.close()
    cek("AKHIR nol jejak (dokumen kaos kembali)", await c.fetchval("SELECT count(*) FROM documents WHERE tenant_id=$1", T) == n0)
    await c.close()
    g = [n for n, ok in HASIL if not ok]
    print(f"RINGKAS {len(HASIL)-len(g)}/{len(HASIL)}", "GAGAL: " + "; ".join(g) if g else "SEMUA LULUS")

asyncio.run(main())
