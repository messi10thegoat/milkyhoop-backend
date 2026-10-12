"""UJI NYATA nama lampiran per tautan (V411) -- kaos, SEMUA ROLLBACK. document_attachments dibayangi TABEL TEMP sesi ini
(salinan baris kaos + kolom display_name) -> tabel prod tak dikunci/diubah. Bytes = objek MinIO dokumen lama yang SUDAH ada
(kunci sha sama -> PUT idempoten, tanpa objek baru). Handler ASLI SO + SJ."""
import asyncio, io, os, sys, uuid
sys.path.insert(0, "/app/backend/api_gateway")
import asyncpg
from fastapi import UploadFile
from starlette.datastructures import Headers
from types import SimpleNamespace

T = "kaos-biru-konveksi"
USER = "0bccdb25-fdf0-4e99-9024-b9a20846f76c"
DOK_LAMA = uuid.UUID("d3c32d59-f4bd-4b1f-80e9-fb143f752285")      # uji-r139-sah.png
SJ_LAMA = uuid.UUID("ebdfa78e-1984-4be7-808c-6a8d0c239c3a")       # tautan lama dokumen itu
SJ_BARU = uuid.UUID("081deacc-2fd3-4522-8a15-7cbb752f0338")       # SJ-2609-0006
SO_ID = uuid.UUID("5933637c-5b27-407c-bf36-1ca4d966bf81")         # SO-2609-0263
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
    return SimpleNamespace(state=SimpleNamespace(user={"tenant_id": T, "user_id": USER}))


def berkas(nama, isi):
    return UploadFile(file=io.BytesIO(isi), filename=nama, headers=Headers({"content-type": "image/png"}))


async def main():
    from app.routers import delivery_lampiran as DL, sales_order_lampiran as SL
    from app.services.storage_service import get_storage_service
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    tr = c.transaction(); await tr.start()
    try:
        pool = PoolSatu(c)

        async def gp(): return pool
        DL.get_pool = gp; SL.get_pool = gp
        doc = await c.fetchrow("SELECT file_name, file_path FROM documents WHERE id=$1 AND tenant_id=$2", DOK_LAMA, T)
        st = get_storage_service()
        isi = st.client.get_object(Bucket=st.config.bucket, Key=doc["file_path"])["Body"].read()
        n_dok0 = await c.fetchval("SELECT count(*) FROM documents WHERE tenant_id=$1", T)
        await c.execute("CREATE TEMP TABLE document_attachments (LIKE public.document_attachments INCLUDING ALL) ON COMMIT DROP")
        await c.execute("INSERT INTO pg_temp.document_attachments SELECT * FROM public.document_attachments WHERE tenant_id=$1", T)
        mig = open("/mig/V411__lampiran_nama_per_tautan.sql").read().replace("ALTER TABLE document_attachments", "ALTER TABLE pg_temp.document_attachments")
        await c.execute(mig.split("DO $$")[0])
        cek("0 tabel TEMP membayangi (nama tak berkualifikasi -> pg_temp)", await c.fetchval("SELECT to_regclass('document_attachments')::oid = to_regclass('pg_temp.document_attachments')::oid"))

        nama_sj = "TES E2E resi nama-baru.png"
        o = await DL.unggah_lampiran_sj(req(), SJ_BARU, [berkas(nama_sj, isi)])
        cek("1 unggah SJ: ok, dedup ke dokumen LAMA (penyimpanan tetap dedup)", o["hasil"][0]["ok"] and o["hasil"][0]["id"] == str(DOK_LAMA), str(o["hasil"]))
        d_baru = await DL.daftar_lampiran_sj(req(), SJ_BARU)
        d_lama = await DL.daftar_lampiran_sj(req(), SJ_LAMA)
        cek("2 daftar SJ baru = nama UNGGAHAN baru", [x["nama"] for x in d_baru["data"] if x["id"] == str(DOK_LAMA)] == [nama_sj], str([x["nama"] for x in d_baru["data"]]))
        cek("3 daftar SJ lama tetap 'uji-r139-sah.png' (tautan lama, display_name NULL -> nama dokumen)",
            [x["nama"] for x in d_lama["data"] if x["id"] == str(DOK_LAMA)] == ["uji-r139-sah.png"])
        r = await DL.unduh_lampiran_sj(req(), SJ_BARU, DOK_LAMA)
        cek("4 unduh SJ baru: Content-Disposition memakai nama baru", "nama-baru.png" in r.headers.get("content-disposition", ""), r.headers.get("content-disposition"))
        nama_so = "TES E2E so nama-baru.png"
        o = await SL.unggah_lampiran_so(req(), SO_ID, [berkas(nama_so, isi)])
        d_so = await SL.daftar_lampiran_so(req(), SO_ID)
        cek("5 SO: unggah ok + daftar = nama unggahan baru", o["hasil"][0]["ok"] and [x["nama"] for x in d_so["data"] if x["id"] == str(DOK_LAMA)] == [nama_so], str([x["nama"] for x in d_so["data"]]))
        r = await SL.unduh_lampiran_so(req(), SO_ID, DOK_LAMA)
        cek("6 unduh SO: Content-Disposition nama baru", "so%20nama-baru.png" in r.headers.get("content-disposition", "") or "so nama-baru.png" in r.headers.get("content-disposition", ""), r.headers.get("content-disposition"))
        cek("7 documents TAK berubah: nama dokumen lama tetap, tak ada dokumen baru",
            await c.fetchval("SELECT file_name FROM documents WHERE id=$1", DOK_LAMA) == "uji-r139-sah.png"
            and await c.fetchval("SELECT count(*) FROM documents WHERE tenant_id=$1", T) == n_dok0)
        pub = await c.fetchval("SELECT count(*) FROM public.document_attachments WHERE document_id=$1", DOK_LAMA)
        cek("8 tabel prod tak tersentuh (tautan dokumen lama tetap 1)", pub == 1, str(pub))
    finally:
        await tr.rollback()
    cek("AKHIR tabel prod tanpa kolom display_name (temp hilang)",
        not await c.fetchval("SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='document_attachments' AND column_name='display_name')"))
    await c.close()
    g = [n for n, ok in HASIL if not ok]
    print(f"RINGKAS {len(HASIL)-len(g)}/{len(HASIL)}", "GAGAL: " + "; ".join(g) if g else "SEMUA LULUS")

asyncio.run(main())
