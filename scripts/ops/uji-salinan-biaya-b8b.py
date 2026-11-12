"""Biaya B8b uji DB di SALINAN (bukan prod): hub GET /api/lampiran/expense/{id} (handler ASLI) vs pabrik lama GET /api/expenses/{id}/attachments, tenant nyawit + kaos.
D  lampiran uji (2 s3-like + 1 'local' + 1 terhapus lunak) pada satu biaya terbit tiap tenant: HIMPUNAN (id, nama, mime, ukuran, tersedia) hub == daftar Biaya; nomor == expense_number;
   url hub (tersedia) == url daftar Biaya; terhapus tak muncul di keduanya · X lintas tenant: biaya tenant A dibaca tenant B -> hub 404 DAN daftar Biaya 404
· XS kueri baris hub langsung: tenant sendiri >= 1 baris, tenant lain 0 · A biaya tanpa lampiran: hub 200 grup kosong · J jenis lain tetap: bank_transaction id acak -> 404."""
import asyncio
import json
import sys
import uuid
from types import SimpleNamespace

from fastapi import HTTPException

sys.path.insert(0, "/app/backend/api_gateway")
from app.middleware import permission_middleware as pm  # noqa: E402
from app.routers import expenses as EX  # noqa: E402
from app.routers import lampiran_dokumen as LD  # noqa: E402
from app.routers import sales_order_lampiran as SL  # noqa: E402

NY, KA = "pt-kita-nyawit-bersama", "kaos-biru-konveksi"
HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


class PESemua:
    async def get_user_context(self, user_id, tenant_id, subscription_role):
        return SimpleNamespace(membership_active=True, business_role_id="r", business_role_code="OWNER")

    async def can(self, ctx, aksi, modul):
        return True


def req(tid, uid):
    return SimpleNamespace(state=SimpleNamespace(user={"tenant_id": tid, "user_id": uid, "role": "OWNER"}))


def j(v):
    return json.loads(json.dumps(v, default=str))


async def panggil(fn, *a):
    try:
        return 200, j(await fn(*a))
    except HTTPException as e:
        return e.status_code, j(e.detail)


async def tautkan(c, tid, eid, nama, storage="s3", hapus=False):
    src = await c.fetchval("SELECT id FROM documents WHERE tenant_id = $1 AND deleted_at IS NULL ORDER BY uploaded_at DESC LIMIT 1", tid)
    did = uuid.uuid4()
    row = dict(await c.fetchrow("SELECT * FROM documents WHERE id = $1 AND tenant_id = $2", src, tid))
    row.update(id=did, file_name=nama, file_path="uji-b8b/" + str(did), file_url=None, storage_type=storage, deleted_at=None)
    kol = list(row)
    await c.execute("INSERT INTO documents (" + ", ".join(kol) + ") VALUES (" + ", ".join(f"${i + 1}" for i in range(len(kol))) + ")", *[row[k] for k in kol])
    await c.execute("""INSERT INTO document_attachments (id, tenant_id, document_id, entity_type, entity_id, display_order)
        VALUES (gen_random_uuid(), $1, $2, 'expense', $3, 0)""", tid, did, eid)
    if hapus:
        await c.execute("UPDATE documents SET deleted_at = now() WHERE id = $1 AND tenant_id = $2", did, tid)
    return did


def himpunan_hub(out):
    return {(x["id"], x["display_name"], x["mime"], x["ukuran"], x["tersedia"]) for g in out["data"] for x in g.get("lampiran", [])}


def himpunan_biaya(daftar):
    return {(str(x["id"]), x["file_name"], x["mime_type"], x["file_size"], x["tersedia"]) for x in daftar}


async def utama():
    pm.get_policy_engine = lambda: PESemua()
    pool = await SL.get_pool()
    async with pool.acquire() as c:
        owner = {}
        for tid in (NY, KA):
            owner[tid] = str(await c.fetchval("""SELECT utr.user_id FROM user_tenant_roles utr JOIN roles r ON r.id = utr.role_id
                WHERE utr.tenant_id = $1 AND r.code = 'OWNER' AND lower(utr.status) = 'active' ORDER BY utr.user_id LIMIT 1""", tid))
        for tid in (NY, KA):
            lain = KA if tid == NY else NY
            e = await c.fetchrow("""SELECT id, expense_number FROM expenses e WHERE e.tenant_id = $1 AND e.status = 'posted' AND NOT EXISTS (
                SELECT 1 FROM document_attachments da WHERE da.entity_type = 'expense' AND da.entity_id = e.id AND da.tenant_id = $1) ORDER BY created_at DESC LIMIT 1""", tid)
            cek(f"prasyarat {tid}: ada biaya terbit tanpa lampiran", e is not None)
            if e is None:
                continue
            # A tanpa lampiran
            st, out = await panggil(LD.lampiran_dokumen, req(tid, owner[tid]), "expense", str(e["id"]))
            cek(f"A {tid} biaya tanpa lampiran: 200, nomor benar, tanpa lampiran", st == 200 and out["sumber"]["nomor"] == e["expense_number"] and himpunan_hub(out) == set(), str(out)[:200])
            await tautkan(c, tid, e["id"], "UJI-b8b-1.png")
            await tautkan(c, tid, e["id"], "UJI-b8b-2.pdf")
            await tautkan(c, tid, e["id"], "UJI-b8b-lama.pdf", storage="local")
            await tautkan(c, tid, e["id"], "UJI-b8b-hapus.png", hapus=True)
            st, out = await panggil(LD.lampiran_dokumen, req(tid, owner[tid]), "expense", str(e["id"]))
            stp, pabrik = await panggil(EX.list_expense_attachments, req(tid, owner[tid]), e["id"])
            hub_h, biaya_h = himpunan_hub(out), himpunan_biaya(pabrik["data"]) if stp == 200 else None
            cek(f"D {tid} hub == daftar Biaya (id, nama, mime, ukuran, tersedia), 3 lampiran, terhapus tak muncul", st == 200 and hub_h == biaya_h and len(hub_h) == 3
                and not any("hapus" in x[1] for x in hub_h), f"{sorted(hub_h)} vs {biaya_h}")
            cek(f"D {tid} nomor hub == expense_number; sumber mode dokumen", out["sumber"] == {"jenis": "expense", "id": str(e["id"]), "nomor": e["expense_number"], "mode": "dokumen", "sales_order": None}, str(out["sumber"]))
            url_hub = {x["id"]: x["url"] for g in out["data"] for x in g["lampiran"] if x["tersedia"]}
            url_biaya = {str(x["id"]): x["url"] for x in pabrik["data"] if x["tersedia"]}
            cek(f"D {tid} url hub (tersedia) == url daftar Biaya; berkas 'local' tersedia=false", url_hub == url_biaya and len(url_hub) == 2 and any(not x[4] for x in hub_h), f"{url_hub} vs {url_biaya}")
            # X lintas tenant
            sx, _ = await panggil(LD.lampiran_dokumen, req(lain, owner[lain]), "expense", str(e["id"]))
            sxp, _ = await panggil(EX.list_expense_attachments, req(lain, owner[lain]), e["id"])
            cek(f"X {tid} biaya dibaca tenant {lain}: hub 404 DAN daftar Biaya 404", (sx, sxp) == (404, 404), f"{sx} {sxp}")
            # XS kueri baris langsung
            n_sendiri = len(await c.fetch(LD.sql_tunggal("expense"), e["id"], tid))
            n_lain = len(await c.fetch(LD.sql_tunggal("expense"), e["id"], lain))
            cek(f"XS {tid} kueri baris: tenant sendiri 3 baris (terhapus tak ikut), tenant lain 0", (n_sendiri, n_lain) == (3, 0), f"{n_sendiri} {n_lain}")
            # J jenis lain tetap
            sj, _ = await panggil(LD.lampiran_dokumen, req(tid, owner[tid]), "bank_transaction", str(uuid.uuid4()))
            cek(f"J {tid} jenis lain (bank_transaction id acak) tetap 404", sj == 404, sj)
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(utama())
