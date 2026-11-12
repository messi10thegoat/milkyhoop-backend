"""Biaya B8a uji DB di SALINAN (bukan prod): bukti Biaya = lampiran NYATA (document_attachments entity 'expense' + documents belum dihapus), bukan tabel mati expense_attachments.
H0 ANGKA sebelum/sesudah per tenant (predikat lama vs baru vs hitungan SQL mandiri) · H1 daftar/summary/pill tanpa_bukti == SQL mandiri · H2 biaya terbit dgn lampiran: attachment_count,
position_text, aksi tanpa 'tambah_bukti'-nya benar · H3 detail.attachments == GET /{id}/attachments · H4 /documents kelompok Bukti + /history BUKTI_DITAMBAH · H5 lampiran dihapus (deleted_at) -> kembali tanpa bukti
· H6 LINTAS TENANT: lampiran tenant lain tak dihitung."""
import asyncio
import sys
import uuid

from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import expenses as EX  # noqa: E402

NY, KA = "pt-kita-nyawit-bersama", "kaos-biru-konveksi"
HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


async def req(c, tid, path="/x", method="GET"):
    r = Request({"type": "http", "method": method, "path": path, "headers": []})
    uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY (role::text = \'OWNER\') DESC LIMIT 1', tid)
    r.state.user = {"tenant_id": tid, "user_id": str(uid), "username": "uji-salinan"}
    return r


# Pembanding MANDIRI (bukan predikat kode): hitung tanpa_bukti langsung dari dua tabel.
async def tanpa_bukti_baru(c, tid):
    return await c.fetchval("""SELECT count(*) FROM expenses e WHERE e.tenant_id = $1 AND e.status = 'posted' AND NOT EXISTS (
        SELECT 1 FROM document_attachments da WHERE da.entity_type = 'expense' AND da.entity_id = e.id AND da.tenant_id = $1
          AND EXISTS (SELECT 1 FROM documents d WHERE d.id = da.document_id AND d.tenant_id = $1 AND d.deleted_at IS NULL))""", tid)


async def tanpa_bukti_lama(c, tid):
    return await c.fetchval("""SELECT count(*) FROM expenses e WHERE e.tenant_id = $1 AND e.status = 'posted'
        AND NOT EXISTS (SELECT 1 FROM expense_attachments ea WHERE ea.expense_id = e.id AND ea.tenant_id = $1)""", tid)


async def lampirkan(c, tid, eid, nama):
    """Salin SATU baris documents milik tenant itu (salinan uji) lalu tautkan ke biaya eid."""
    did = uuid.uuid4()
    src = await c.fetchval("SELECT id FROM documents WHERE tenant_id = $1 AND deleted_at IS NULL ORDER BY uploaded_at DESC LIMIT 1", tid)
    if src is None:
        return None
    row = dict(await c.fetchrow("SELECT * FROM documents WHERE id = $1 AND tenant_id = $2", src, tid))
    row.update(id=did, file_name=nama, file_path="uji-b8a/" + str(did), file_url=None, deleted_at=None)
    kol = list(row)
    await c.execute("INSERT INTO documents (" + ", ".join(kol) + ") VALUES (" + ", ".join(f"${i + 1}" for i in range(len(kol))) + ")", *[row[k] for k in kol])
    await c.execute("""INSERT INTO document_attachments (id, tenant_id, document_id, entity_type, entity_id, display_order)
        VALUES (gen_random_uuid(), $1, $2, 'expense', $3, 0)""", tid, did, eid)
    return did


async def main():
    pool = await EX.get_pool()
    async with pool.acquire() as c:
        for tid in (NY, KA):
            lama, baru = await tanpa_bukti_lama(c, tid), await tanpa_bukti_baru(c, tid)
            tot = await c.fetchval("SELECT count(*) FROM expenses WHERE tenant_id = $1 AND status = 'posted'", tid)
            nlamp = await c.fetchval("SELECT count(*) FROM document_attachments WHERE tenant_id = $1 AND entity_type = 'expense'", tid)
            print(f"ANGKA {tid}: biaya terbit={tot} · tanpa_bukti predikat LAMA (expense_attachments)={lama} · BARU (lampiran nyata)={baru} · tautan lampiran expense={nlamp}", flush=True)
            # H1 daftar + summary == SQL mandiri
            s = (await EX.get_expenses_summary(await req(c, tid), "month", None))["data"]
            cek(f"H1 {tid} summary.tanpa_bukti_count == SQL mandiri", s["tanpa_bukti_count"] == baru, f"{s['tanpa_bukti_count']} vs {baru}")
            ls = await EX.list_expenses(await req(c, tid), 0, 100, "all", None, None, None, None, None, "expense_date", "desc", None, None, "tanpa_bukti", True)
            cek(f"H1 {tid} daftar ?aksi=tanpa_bukti: total == SQL mandiri", ls["pagination"]["total"] == baru if "pagination" in ls else ls["total"] == baru, str({k: ls[k] for k in ls if k != 'data' and k != 'items'})[:160])

            # biaya terbit TANPA lampiran sebagai sasaran
            e = await c.fetchrow("""SELECT id, status FROM expenses e WHERE e.tenant_id = $1 AND e.status = 'posted' AND NOT EXISTS (
                SELECT 1 FROM document_attachments da WHERE da.entity_type = 'expense' AND da.entity_id = e.id AND da.tenant_id = $1) ORDER BY created_at DESC LIMIT 1""", tid)
            cek(f"prasyarat {tid}: ada biaya terbit tanpa lampiran", e is not None)
            if e is None:
                continue
            det0 = (await EX.get_expense_detail(await req(c, tid), e["id"]))["data"]
            cek(f"H2 {tid} sebelum lampir: position_text berakhir 'tanpa bukti', attachments kosong", det0["position_text"].endswith("tanpa bukti") and det0["attachments"] == [], det0["position_text"])
            did = await lampirkan(c, tid, e["id"], "UJI-b8a-nota.pdf")
            cek(f"prasyarat {tid}: salinan dokumen uji terlampir", did is not None)
            if did is None:
                continue
            # H2 daftar baris itu
            ls1 = await EX.list_expenses(await req(c, tid), 0, 100, "all", None, None, None, None, None, "expense_date", "desc", None, None, None, False)
            baris = [x for x in (ls1.get("data") or ls1.get("items") or []) if str(x["id"]) == str(e["id"])]
            cek(f"H2 {tid} daftar: attachment_count=1, position_text TANPA 'tanpa bukti'", len(baris) == 1 and baris[0]["attachment_count"] == 1 and "tanpa bukti" not in baris[0]["position_text"], str(baris)[:200])
            s2 = (await EX.get_expenses_summary(await req(c, tid), "month", None))["data"]
            cek(f"H2 {tid} summary tanpa_bukti_count turun 1 (== SQL mandiri)", s2["tanpa_bukti_count"] == baru - 1 == await tanpa_bukti_baru(c, tid), f"{s2['tanpa_bukti_count']}")
            # H3 detail == GET /attachments
            det = (await EX.get_expense_detail(await req(c, tid), e["id"]))["data"]
            ga = (await EX.list_expense_attachments(await req(c, tid), e["id"]))
            cek(f"H3 {tid} detail.attachments == GET /attachments (id, file_name, url)", [(str(a['id']), a['file_name']) for a in det["attachments"]] == [(str(a['id']), a['file_name']) for a in ga["data"]] and len(det["attachments"]) == 1,
                str(det["attachments"])[:200])
            cek(f"H3 {tid} detail.position_text tanpa 'tanpa bukti'", "tanpa bukti" not in det["position_text"], det["position_text"])
            # H4 documents + history
            dk = (await EX.get_expense_documents(await req(c, tid), e["id"]))["data"]
            g = [x for x in dk["groups"] if x["key"] == "bukti"]
            cek(f"H4 {tid} /documents: kelompok Bukti = lampiran, anchor tanpa 'tanpa bukti'", len(g) == 1 and len(g[0]["docs"]) == 1 and "tanpa bukti" not in dk["anchor"]["position_text"], str(dk)[:240])
            hs = (await EX.get_expense_history(await req(c, tid), e["id"], 200))["data"]
            cek(f"H4 {tid} /history: BUKTI_DITAMBAH 1", sum(1 for x in str(hs).split("BUKTI_DITAMBAH")) - 1 == 1, str(hs)[:200])
            # H5 hapus lunak dokumen -> kembali tanpa bukti
            await c.execute("UPDATE documents SET deleted_at = now() WHERE id = $1 AND tenant_id = $2", did, tid)
            det2 = (await EX.get_expense_detail(await req(c, tid), e["id"]))["data"]
            cek(f"H5 {tid} lampiran dihapus -> detail kosong + 'tanpa bukti' lagi", det2["attachments"] == [] and det2["position_text"].endswith("tanpa bukti"), det2["position_text"])
            cek(f"H5 {tid} summary kembali == angka awal", (await EX.get_expenses_summary(await req(c, tid), "month", None))["data"]["tanpa_bukti_count"] == baru)
            # H6 lintas tenant: lampiran tenant LAIN pada id biaya ini tak dihitung
            await c.execute("UPDATE documents SET deleted_at = NULL WHERE id = $1 AND tenant_id = $2", did, tid)
            tl = await c.fetchval("SELECT count(*) FROM document_attachments da JOIN documents d ON d.id = da.document_id WHERE da.entity_type='expense' AND da.entity_id = $1 AND da.tenant_id <> $2", e["id"], tid)
            cek(f"H6 {tid} tak ada tautan lintas tenant ke biaya ini", tl == 0)
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
