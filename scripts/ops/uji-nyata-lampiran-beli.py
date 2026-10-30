"""Harness NYATA LAMPIRAN-PEMBELIAN F1 + BKK (kaos SAJA, penanda 'TES E2E LAMPIRAN-BELI'): SATU transaksi luar pada satu koneksi SELALU di-ROLLBACK
(pola F2 / ND-BUAT: db_pool tertambal, transaksi dalam = savepoint; PolicyEngine NYATA di koneksi yang sama). Sesudah ROLLBACK diukur di
koneksi LAIN: nol sisa documents / document_attachments / bill_attachments.

Di dalam transaksi: satu PK kaos yang mengalokasikan FPB posted + satu penerimaan kaos. Lampiran:
  A = hub /documents/{id}/attach entity_type 'payment' ke PK (jalur F1b FE)   B = hub attach 'payment' ke PENERIMAAN
  C = document_attachments 'bill' ke FPB (sumber 2)                            D = bill_attachments ke FPB (sumber 1, unggah lama)
Harap: /api/lampiran/payment_out/PK = grup payment_out{A} + grup bill{C,D} (keluarga); /api/lampiran/bill/FPB = bill{C,D} + payment_out{A};
B TAK PERNAH muncul di keduanya; /bill-payments/PK/attachments = {A}; /receive-payments/PK/attachments tanpa A (dan sebaliknya untuk B);
/api/lampiran/receive_payment/RP memuat B dan TIDAK A; tenant lain (baca saja, tenant JWT grapgrap-manado) -> 404 untuk id kaos.
Juga BKK: render nyata payment_out + vendor_deposit kaos (judul "Bukti Kas Keluar", tanpa stempel) + kwitansi penerimaan kaos tetap DITERIMA.
Contoh respons nyata (FPB + PK) ditulis ke keluaran untuk kontrak FRONTEND. Kanal keluar di-stub: WAJIB 0. Argumen: <keluaran.json>."""
import asyncio
import json
import os
import sys
import uuid

import asyncpg

sys.path.insert(0, os.getcwd())
sys.path.insert(0, "/app")  # middleware mengimpor backend.api_gateway.app.* (== kontainer gateway)
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

from app.routers import bill_payments as BP  # noqa: E402
from app.routers import documents as DOC  # noqa: E402
from app.routers import lampiran_dokumen as LD  # noqa: E402
from app.routers import receive_payments as RP  # noqa: E402
from app.schemas.documents import AttachDocumentRequest  # noqa: E402
from app.services import db_pool  # noqa: E402
from app.services import policy_engine_client as PEC  # noqa: E402

T = "kaos-biru-konveksi"
LAIN = "grapgrap-manado"   # BACA saja: hanya rute GET yang dipanggil dgn tenant ini
PENANDA = "TES E2E LAMPIRAN-BELI"


def req(tid, uid):
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    r = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""}, receive)
    r.state.user = {"tenant_id": tid, "user_id": str(uid), "role": "OWNER"}
    return r


def lid(out, jenis=None):
    """{jenis: set(lampiran id)} dari respons /api/lampiran."""
    h = {}
    for g in out["data"]:
        if g.get("tersembunyi_izin"):
            continue
        h.setdefault(g["dokumen"]["jenis"], set()).update(lp["id"] for lp in g["lampiran"])
    return h


async def main(keluar):
    c = await asyncpg.connect(os.environ["URL"])
    c2 = await asyncpg.connect(os.environ["URL"])  # koneksi LAIN: saksi sisa
    assert await c.fetchval('SELECT 1 FROM "Tenant" WHERE id = $1', T), "tenant tak ada -- harness menolak"
    uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY "createdAt" LIMIT 1', T)  # == harness F2 / ND-BUAT

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
    PEC.init_policy_engine(Pool())
    from backend.api_gateway.app.services import policy_engine_client as PEC_MW  # middleware mengimpor lewat jalur ini (objek modul LAIN)
    PEC_MW.init_policy_engine(Pool())
    cek, catat = [], {}

    def harus(nama, kondisi, info=None):
        cek.append({"cek": nama, "ok": bool(kondisi), "info": info})
        print(("OK   " if kondisi else "GAGAL ") + nama + ("" if kondisi else f" -> {info}"), flush=True)

    async def saksi():
        return {"doc": await c2.fetchval("SELECT count(*) FROM documents WHERE tenant_id = $1 AND file_name LIKE $2", T, f"{PENANDA}%"),
                "da": await c2.fetchval("SELECT count(*) FROM document_attachments WHERE tenant_id = $1", T),
                "ba": await c2.fetchval("SELECT count(*) FROM bill_attachments WHERE filename LIKE $1", f"{PENANDA}%")}

    sebelum = await saksi()
    luar = c.transaction()
    await luar.start()
    try:
        pk = await c.fetchrow("""SELECT p.id, p.payment_number, a.bill_id FROM bill_payments_v2 p
                                 JOIN bill_payment_allocations a ON a.payment_id = p.id
                                 JOIN bills b ON b.id = a.bill_id AND b.tenant_id = $1
                                 WHERE p.tenant_id = $1 ORDER BY p.created_at DESC LIMIT 1""", T)
        rp = await c.fetchrow("SELECT id, payment_number FROM receive_payments WHERE tenant_id = $1 ORDER BY created_at DESC LIMIT 1", T)
        harus("ada PK kaos beralokasi FPB + penerimaan kaos", pk is not None and rp is not None)
        fpb = pk["bill_id"]
        catat["pk"], catat["rp"] = pk["payment_number"], rp["payment_number"]
        catat["fpb"] = await c.fetchval("SELECT invoice_number FROM bills WHERE id = $1", fpb)

        async def dok(nama):
            return await c.fetchval("""INSERT INTO documents (tenant_id, file_name, file_path, file_type, file_size, storage_type, uploaded_by)
                                       VALUES ($1, $2, $3, 'image/png', 10, 's3', $4) RETURNING id""",
                                    T, f"{PENANDA} {nama}.png", f"{T}/uploads/lampiran/uji-{nama}.png", uid)
        A, B, C = await dok("A-pk"), await dok("B-rp"), await dok("C-fpb")
        # A + B lewat HUB NYATA (POST /documents/{id}/attach): PK id dan penerimaan id dgn entity_type 'payment' yang SAMA
        ra = await DOC.attach_document(req(T, uid), A, AttachDocumentRequest(entity_type="payment", entity_id=pk["id"]))
        rb = await DOC.attach_document(req(T, uid), B, AttachDocumentRequest(entity_type="payment", entity_id=rp["id"]))
        harus("hub attach 'payment' ke PK dan ke penerimaan berhasil", str(ra.attachment.entity_id) == str(pk["id"])
              and str(rb.attachment.entity_id) == str(rp["id"]))
        await c.execute("""INSERT INTO document_attachments (tenant_id, document_id, entity_type, entity_id) VALUES ($1, $2, 'bill', $3)""",
                        T, C, fpb)
        D = await c.fetchval("""INSERT INTO bill_attachments (bill_id, filename, file_path, file_size, mime_type, uploaded_by)
                                VALUES ($1, $2, 'uji/d.pdf', 10, 'application/pdf', $3) RETURNING id""", fpb, f"{PENANDA} D-lama.pdf", uid)
        A, B, C, D = str(A), str(B), str(C), str(D)

        o_pk = await LD.lampiran_dokumen(req(T, uid), "payment_out", str(pk["id"]))
        o_fb = await LD.lampiran_dokumen(req(T, uid), "bill", str(fpb))
        o_rp = await LD.lampiran_dokumen(req(T, uid), "receive_payment", str(rp["id"]))
        catat["contoh_payment_out"], catat["contoh_bill"] = o_pk, o_fb
        h_pk, h_fb, h_rp = lid(o_pk), lid(o_fb), lid(o_rp)
        harus("lampiran/payment_out/PK: grup payment_out memuat A, grup bill (keluarga) memuat C + D",
              A in h_pk.get("payment_out", set()) and {C, D} <= h_pk.get("bill", set()), h_pk)
        harus("lampiran/bill/FPB: grup bill memuat C (hub) + D (bill_attachments), grup payment_out memuat A",
              {C, D} <= h_fb.get("bill", set()) and A in h_fb.get("payment_out", set()), h_fb)
        semua_beli = set().union(*h_pk.values(), *h_fb.values())
        harus("lampiran penerimaan B TAK PERNAH muncul di keluarga Pembelian", B not in semua_beli, sorted(semua_beli))
        harus("lampiran/receive_payment/RP memuat B dan TIDAK A/C/D", B in set().union(*h_rp.values())
              and not ({A, C, D} & set().union(*h_rp.values())), h_rp)
        harus("sumber.mode keluarga_pembelian + nomor", o_pk["sumber"]["mode"] == "keluarga_pembelian"
              and o_pk["sumber"]["nomor"] == pk["payment_number"] and o_fb["sumber"]["jenis"] == "bill", [o_pk["sumber"], o_fb["sumber"]])
        url_d = [lp["url"] for g in o_fb["data"] if g["dokumen"]["jenis"] == "bill" for lp in g["lampiran"] if lp["id"] == D]
        harus("url D = rute unduh FPB yang ada", url_d == [f"/api/bills/{fpb}/attachments/{D}/download"], url_d)

        bp_pk = {x["id"] for x in (await BP.list_payment_attachments(req(T, uid), str(pk["id"])))["data"]}
        bp_rp = {x["id"] for x in (await BP.list_payment_attachments(req(T, uid), str(rp["id"])))["data"]}
        harus("/bill-payments/PK/attachments = A saja (tanpa B)", A in bp_pk and B not in bp_pk, bp_pk)
        harus("/bill-payments/RP/attachments kosong (id penerimaan tak terbaca lewat PK)", bp_rp == set(), bp_rp)
        try:
            r_pk = await RP.list_payment_attachments(req(T, uid), str(pk["id"]))
            ids = {x["id"] for x in (r_pk.get("data") or [])} if isinstance(r_pk, dict) else set()
            harus("/receive-payments/PK/attachments tanpa A (kosong / 404)", A not in ids, ids)
        except HTTPException as e:
            harus("/receive-payments/PK/attachments tanpa A (kosong / 404)", e.status_code == 404, e.detail)

        for jenis, did in (("payment_out", pk["id"]), ("bill", fpb)):
            try:
                await LD.lampiran_dokumen(req(LAIN, uid), jenis, str(did))
                harus(f"lintas tenant: {jenis} kaos dibaca tenant lain -> 404", False, "tak 404")
            except HTTPException as e:
                harus(f"lintas tenant: {jenis} kaos dibaca tenant lain -> 404", e.status_code == 404, e.detail)
        bp_lain = (await BP.list_payment_attachments(req(LAIN, uid), str(pk["id"])))["data"]
        harus("lintas tenant: /bill-payments/PK/attachments tenant lain kosong", bp_lain == [], bp_lain)

        # BKK: render nyata (pemuat + templat) dokumen kaos
        from app.services import cetak_payment_out as CPO
        from app.services import cetak_vendor_deposit as CVD
        from app.services.pdf_service import get_pdf_service
        ps = get_pdf_service()
        kop = await c.fetchrow('SELECT display_name AS name FROM "Tenant" WHERE id = $1', T)
        ten = {"name": kop["name"], "address": None, "phone": None, "logo_data": None}
        h1 = ps.render_payment_out((await CPO.muat_pdf_payment_out(c, {"tenant_id": T}, pk["id"]))["receipt_data"], ten).html
        harus("BKK PK kaos: 'Bukti Kas Keluar' + subjudul, TANPA stempel DIBAYAR/DITERIMA",
              "Bukti Kas Keluar" in h1 and "Pemberitahuan pembayaran ke pemasok" in h1 and "kw-stempel-diterima" not in h1
              and "DIBAYAR" not in h1 and "DITERIMA" not in h1)
        vd = await c.fetchval("SELECT id FROM vendor_deposits WHERE tenant_id = $1 AND status <> 'draft' ORDER BY created_at DESC LIMIT 1", T)
        if vd:
            h2 = ps.render_receipt((await CVD.muat_pdf_vendor_deposit(c, {"tenant_id": T}, vd))["receipt_data"], ten).html
            harus("BKK UM vendor kaos: 'Bukti Kas Keluar' + 'Uang muka pembelian ke pemasok', TANPA stempel",
                  "Bukti Kas Keluar" in h2 and "Uang muka pembelian ke pemasok" in h2 and "kw-stempel-diterima" not in h2
                  and "DIBAYAR" not in h2)
        rp_pdf = await RP.muat_pdf_kwitansi_penerimaan(c, {"tenant_id": T}, str(rp["id"])) if hasattr(RP, "muat_pdf_kwitansi_penerimaan") else None
        if rp_pdf is not None:
            d = rp_pdf.get("receipt_data", rp_pdf)
            h3 = ps.render_receipt(d, ten).html
            st = (d.get("status") or "posted").lower()
            harus("kontrol: kwitansi penerimaan kaos tetap DITERIMA (bila posted)", ("DITERIMA" in h3) == (st not in ("draft", "void", "voided")),
                  st)
    finally:
        await luar.rollback()
    sesudah = await saksi()
    harus("ROLLBACK: dari koneksi LAIN nol sisa documents/document_attachments/bill_attachments", sesudah == sebelum, [sebelum, sesudah])
    harus("nol panggilan kanal keluar", KELUAR == [], KELUAR)
    await c.close()
    await c2.close()
    gagal = [x["cek"] for x in cek if not x["ok"]]
    json.dump({"cek": cek, "catat": catat, "gagal": gagal}, open(keluar, "w"), default=str, indent=1)
    print("HASIL", "LULUS" if not gagal else f"GAGAL {gagal}", f"({len(cek)} cek)")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
