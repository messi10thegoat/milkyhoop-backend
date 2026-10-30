"""Harness NYATA LAMPIRAN-PEMBELIAN F2a (kaos SAJA, penanda 'TES E2E LAMPIRAN-F2A'): SATU transaksi luar pada satu koneksi SELALU di-ROLLBACK
(pola F1: db_pool tertambal, transaksi dalam = savepoint; PolicyEngine NYATA). Sesudah ROLLBACK diukur di koneksi LAIN: nol sisa.

Data: ND kaos VC-2610-0010 (original_bill_id -> PB-2610-0015) + PO kaos PO-2610-0001. FIXTURE di dalam ROLLBACK: PB-2610-0015.purchase_order_id
:= PO-2610-0001 (kaos tak punya FPB ber-PO). Lampiran (unggah lewat HUB /documents/{id}/attach -- tanpa objek MinIO; jalur unggah pabrik
sudah teruji NK/penawaran/SO):
  P = entity_type 'purchase_order' ke PO     N = 'vendor_credit' ke ND     K = 'credit_note' ke satu NK kaos (TAK boleh bocor ke ND)
  F = 'bill' ke FPB
Harap: /api/lampiran/purchase_order/PO = grup PO{P} + FPB{F}; /api/lampiran/vendor_credit/ND = ND{N} + FPB{F}; /api/lampiran/bill/FPB = PO{P} +
FPB{F} + ND{N} (urutan PO, FPB, ND); K tak pernah muncul; rute pabrik GET /purchase-orders/{po}/attachments = {P}, /vendor-credits/{nd}/
attachments = {N}; DELETE pabrik melepas P -> daftar PO kosong; lintas tenant (BACA SAJA sebagai grapgrap-manado) -> 404.
Kanal keluar di-stub: WAJIB 0. Argumen: <keluaran.json>."""
import asyncio
import json
import os
import sys

import asyncpg

sys.path.insert(0, os.getcwd())
sys.path.insert(0, "/app")
os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-harness")

KELUAR = []


def _pasang_pagar_keluar():
    import smtplib

    def catat(nama):
        def f(*a, **k):
            KELUAR.append(nama)
            raise RuntimeError(f"harness: kanal keluar {nama} diblokir")
        return f

    async def acatat(*a, **k):
        KELUAR.append("async-http")
        raise RuntimeError("harness: kanal keluar async-http diblokir")
    smtplib.SMTP.__init__ = catat("smtp")
    smtplib.SMTP_SSL.__init__ = catat("smtp_ssl")
    for modul, kelas, attr, f in (("httpx", "Client", "send", catat("httpx")), ("httpx", "AsyncClient", "send", acatat),
                                  ("requests", "Session", "send", catat("requests")), ("aiohttp", "ClientSession", "_request", acatat)):
        try:
            setattr(getattr(__import__(modul), kelas), attr, f)
        except ImportError:
            pass


_pasang_pagar_keluar()

from fastapi import HTTPException  # noqa: E402
from starlette.requests import Request  # noqa: E402

from app.routers import documents as DOC  # noqa: E402
from app.routers import lampiran_dokumen as LD  # noqa: E402
from app.routers import lampiran_induk as LI  # noqa: E402
from app.schemas.documents import AttachDocumentRequest  # noqa: E402
from app.services import db_pool  # noqa: E402
from app.services import policy_engine_client as PEC  # noqa: E402

T = "kaos-biru-konveksi"
LAIN = "grapgrap-manado"   # BACA saja
PENANDA = "TES E2E LAMPIRAN-F2A"


def req(tid, uid, metode="GET"):
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    r = Request({"type": "http", "method": metode, "path": "/", "headers": [], "query_string": b""}, receive)
    r.state.user = {"tenant_id": tid, "user_id": str(uid), "role": "OWNER"}
    return r


def lid(out):
    h = {}
    for g in out["data"]:
        if not g.get("tersembunyi_izin"):
            h.setdefault(g["dokumen"]["jenis"], set()).update(lp["id"] for lp in g["lampiran"])
    return h


def ep(router, nama):
    return {r.endpoint.__name__: r.endpoint for r in router.routes}[nama]


async def main(keluar):
    c = await asyncpg.connect(os.environ["URL"])
    c2 = await asyncpg.connect(os.environ["URL"])
    assert await c.fetchval('SELECT 1 FROM "Tenant" WHERE id = $1', T), "tenant tak ada -- harness menolak"
    uid = await c.fetchval("""SELECT utr.user_id FROM user_tenant_roles utr JOIN roles r ON r.id = utr.role_id
                              WHERE utr.tenant_id = $1 AND r.code = 'OWNER' LIMIT 1""", T)

    class Kon:
        def __getattr__(s, a): return getattr(c, a)
        def transaction(s, **kw): return c.transaction()

    class CM:
        async def __aenter__(s): return Kon()
        async def __aexit__(s, *a): return False

    class Pool:
        def acquire(s): return CM()

    async def gp(): return Pool()
    db_pool.get_db_pool = gp
    LI.get_pool = gp
    PEC.init_policy_engine(Pool())
    from backend.api_gateway.app.services import policy_engine_client as PEC_MW
    PEC_MW.init_policy_engine(Pool())
    cek, catat = [], {}

    def harus(nama, kondisi, info=None):
        cek.append({"cek": nama, "ok": bool(kondisi), "info": info})
        print(("OK   " if kondisi else "GAGAL ") + nama + ("" if kondisi else f" -> {info}"), flush=True)

    async def saksi():
        return {"doc": await c2.fetchval("SELECT count(*) FROM documents WHERE tenant_id = $1 AND file_name LIKE $2", T, f"{PENANDA}%"),
                "da": await c2.fetchval("SELECT count(*) FROM document_attachments WHERE tenant_id = $1", T),
                "po_fpb": await c2.fetchval("SELECT count(*) FROM bills WHERE tenant_id = $1 AND purchase_order_id IS NOT NULL", T),
                "audit": await c2.fetchval("SELECT count(*) FROM audit_logs WHERE tenant_id = $1", T)}

    sebelum = await saksi()
    luar = c.transaction()
    await luar.start()
    try:
        nd = await c.fetchrow("SELECT id, credit_number, original_bill_id FROM vendor_credits WHERE tenant_id = $1 AND credit_number = 'VC-2610-0010'", T)
        po = await c.fetchrow("SELECT id, po_number FROM purchase_orders WHERE tenant_id = $1 AND po_number = 'PO-2610-0001'", T)
        nk = await c.fetchrow("SELECT id, credit_note_number FROM credit_notes WHERE tenant_id = $1 ORDER BY created_at DESC LIMIT 1", T)
        harus("prasyarat: ND VC-2610-0010 ber-FPB, PO-2610-0001, satu NK kaos", nd and nd["original_bill_id"] and po and nk)
        fpb = nd["original_bill_id"]
        await c.execute("UPDATE bills SET purchase_order_id = $1 WHERE id = $2 AND tenant_id = $3", po["id"], fpb, T)   # fixture (ROLLBACK)

        async def dok(nama):
            return await c.fetchval("""INSERT INTO documents (tenant_id, file_name, file_path, file_type, file_size, storage_type, uploaded_by)
                                       VALUES ($1, $2, $3, 'image/png', 10, 's3', $4) RETURNING id""",
                                    T, f"{PENANDA} {nama}.png", f"{T}/uploads/lampiran/uji-f2a-{nama}.png", uid)
        ids = {}
        for k, et, eid in (("P", "purchase_order", po["id"]), ("N", "vendor_credit", nd["id"]), ("K", "credit_note", nk["id"]), ("F", "bill", fpb)):
            d = await dok(k)
            await DOC.attach_document(req(T, uid, "POST"), d, AttachDocumentRequest(entity_type=et, entity_id=eid))
            ids[k] = str(d)
        P, N, K, F = ids["P"], ids["N"], ids["K"], ids["F"]
        o_po = await LD.lampiran_dokumen(req(T, uid), "purchase_order", str(po["id"]))
        o_nd = await LD.lampiran_dokumen(req(T, uid), "vendor_credit", str(nd["id"]))
        o_fb = await LD.lampiran_dokumen(req(T, uid), "bill", str(fpb))
        catat["contoh_purchase_order"], catat["contoh_vendor_credit"], catat["contoh_bill"] = o_po, o_nd, o_fb
        h_po, h_nd, h_fb = lid(o_po), lid(o_nd), lid(o_fb)
        harus("lampiran/purchase_order: grup PO{P} + FPB{F}", P in h_po.get("purchase_order", set()) and F in h_po.get("bill", set()), h_po)
        harus("lampiran/vendor_credit: grup ND{N} + FPB{F}", N in h_nd.get("vendor_credit", set()) and F in h_nd.get("bill", set()), h_nd)
        harus("lampiran/bill: PO{P} + FPB{F} + ND{N}", P in h_fb.get("purchase_order", set()) and F in h_fb.get("bill", set())
              and N in h_fb.get("vendor_credit", set()), h_fb)
        urut = [g["dokumen"]["jenis"] for g in o_fb["data"] if not g.get("tersembunyi_izin")]
        harus("urutan grup di lampiran/bill: purchase_order, bill, ..., vendor_credit",
              urut[0] == "purchase_order" and urut[-1] == "vendor_credit" and "bill" in urut, urut)
        semua = set().union(*h_po.values(), *h_nd.values(), *h_fb.values())
        harus("lampiran NK (credit_note) TAK PERNAH muncul di keluarga Pembelian", K not in semua, sorted(semua))
        harus("sumber.mode keluarga_pembelian + nomor", o_po["sumber"]["mode"] == "keluarga_pembelian" and o_po["sumber"]["nomor"] == po["po_number"]
              and o_nd["sumber"]["nomor"] == nd["credit_number"], [o_po["sumber"], o_nd["sumber"]])
        urlp = [lp["url"] for g in o_po["data"] if g["dokumen"]["jenis"] == "purchase_order" for lp in g["lampiran"]]
        harus("url P = rute unduh pabrik PO", urlp == [f"/api/purchase-orders/{po['id']}/attachments/{P}/download"], urlp)

        d_po = await ep(LI.router_pesanan_pembelian, "daftar_lampiran_purchase_order")(req(T, uid), po["id"])
        d_nd = await ep(LI.router_nota_debit, "daftar_lampiran_vendor_credit")(req(T, uid), nd["id"])
        harus("rute pabrik GET /purchase-orders/{po}/attachments = {P}", {x["id"] for x in d_po["data"]} == {P}, d_po["data"])
        harus("rute pabrik GET /vendor-credits/{nd}/attachments = {N} (tanpa K)", {x["id"] for x in d_nd["data"]} == {N}, d_nd["data"])
        await ep(LI.router_pesanan_pembelian, "lepas_lampiran_purchase_order")(req(T, uid, "DELETE"), po["id"], __import__("uuid").UUID(P))
        d_po2 = await ep(LI.router_pesanan_pembelian, "daftar_lampiran_purchase_order")(req(T, uid), po["id"])
        harus("rute pabrik DELETE melepas P -> daftar PO kosong", d_po2["data"] == [], d_po2["data"])
        # Rute LAMA modul tetap berfungsi (pemenang rute dibuktikan di tes unit test_lampiran_pembelian_f2a: kontainer ini tanpa app.main)
        from app.routers import purchase_orders as RPO
        from app.routers import vendor_credits as RVC
        RPO.get_pool = gp
        RVC.get_pool = gp
        dpo = await RPO.get_purchase_order(req(T, uid), po["id"])
        dvc = await RVC.get_vendor_credit(req(T, uid), nd["id"])
        harus("GET /purchase-orders/{id} lama tetap berfungsi (nomor PO)", po["po_number"] in json.dumps(dpo, default=str), str(dpo)[:200])
        harus("GET /vendor-credits/{id} lama tetap berfungsi (nomor ND)", nd["credit_number"] in json.dumps(dvc, default=str), str(dvc)[:200])
        for jenis, did in (("purchase_order", po["id"]), ("vendor_credit", nd["id"])):
            try:
                await LD.lampiran_dokumen(req(LAIN, uid), jenis, str(did))
                harus(f"lintas tenant: lampiran/{jenis} kaos dibaca tenant lain -> 404", False, "tak 404")
            except HTTPException as e:
                harus(f"lintas tenant: lampiran/{jenis} kaos dibaca tenant lain -> 404", e.status_code == 404, e.detail)
        try:
            await ep(LI.router_nota_debit, "daftar_lampiran_vendor_credit")(req(LAIN, uid), nd["id"])
            harus("lintas tenant: rute pabrik ND tenant lain -> 404", False, "tak 404")
        except HTTPException as e:
            harus("lintas tenant: rute pabrik ND tenant lain -> 404", e.status_code == 404, e.detail)
    finally:
        await luar.rollback()
    sesudah = await saksi()
    harus("ROLLBACK: dari koneksi LAIN nol sisa documents/tautan/FPB ber-PO/audit", sesudah == sebelum, [sebelum, sesudah])
    harus("nol panggilan kanal keluar", KELUAR == [], KELUAR)
    await c.close()
    await c2.close()
    gagal = [x["cek"] for x in cek if not x["ok"]]
    json.dump({"cek": cek, "catat": catat, "gagal": gagal}, open(keluar, "w"), default=str, indent=1)
    print("HASIL", "LULUS" if not gagal else f"GAGAL {gagal}", f"({len(cek)} cek)")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
