"""Harness NYATA LAMPIRAN-PEMBELIAN F2b (kaos SAJA, penanda 'TES E2E LAMPIRAN-F2B'): SATU transaksi luar SELALU di-ROLLBACK (pola F2a).
Dijalankan SESUDAH jendela (putusan MASTER 31 Okt: TANPA ALTER di transaksi produksi saat jam kerja): V466 SUDAH diterapkan (/root/apply_mig.sh),
kode LIVE. Harness MENOLAK jalan bila CHECK produksi belum memuat goods_receipt + vendor_deposit.

Data nyata kaos: GRN-2610-0002 (PO-2610-0006), FPB PB-2610-0017 + PB-2610-0016 (void), UMP VD-2026-00003, DP pelanggan DEP-2609-0089.
FIXTURE (ROLLBACK): tautan GRN-2610-0002 -> baris PB-2610-0017 status 'active' + tautan -> PB-2610-0016 status 'reversed' (kontrol: TAK ikut);
VD-2026-00003.purchase_order_id := PO-2610-0006; penerapan VD-2026-00003 -> PB-2610-0017 'active'. Lampiran (tautan langsung, tanpa MinIO):
  G='goods_receipt'->GRN  U='vendor_deposit'->UMP  F='bill'->PB-17  O='purchase_order'->PO-6  R='bill'->PB-16  C='customer_deposit'->DEP (tak boleh bocor)
Harap: /lampiran/goods_receipt = PO{O}+GRN{G}+FPB{F} (tanpa R); /lampiran/vendor_deposit = PO{O}+UMP{U}+FPB{F}; /lampiran/bill/PB-17 = UMP{U}+GRN{G}+
FPB{F}; /lampiran/purchase_order = PO{O}+UMP{U}+GRN{G}; urutan PO,UMP,GRN,FPB; C tak pernah muncul; rute pabrik GET GRN={G}, UMP={U}; DELETE
pabrik melepas G; GET /goods-receipts/{id} & /vendor-deposits/{id} lama berfungsi; lintas tenant 404. Kanal keluar 0. Argumen: <keluaran.json>."""
import asyncio
import json
import os
import sys
import uuid

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

from app.routers import goods_receipts as RG  # noqa: E402
from app.routers import lampiran_dokumen as LD  # noqa: E402
from app.routers import lampiran_induk as LI  # noqa: E402
from app.routers import vendor_deposits as RV  # noqa: E402
from app.services import db_pool  # noqa: E402
from app.services import policy_engine_client as PEC  # noqa: E402

T = "kaos-biru-konveksi"
LAIN = "grapgrap-manado"   # BACA saja
PENANDA = "TES E2E LAMPIRAN-F2B"


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
    q = {k: await c.fetchrow(sql, T) for k, sql in {
        "grn": "SELECT id, receipt_number, purchase_order_id FROM goods_receipts WHERE tenant_id = $1 AND receipt_number = 'GRN-2610-0002'",
        "po": "SELECT id, po_number FROM purchase_orders WHERE tenant_id = $1 AND po_number = 'PO-2610-0006'",
        "f17": "SELECT id, invoice_number FROM bills WHERE tenant_id = $1 AND invoice_number = 'PB-2610-0017'",
        "f16": "SELECT id, invoice_number FROM bills WHERE tenant_id = $1 AND invoice_number = 'PB-2610-0016'",
        "ump": "SELECT id, deposit_number FROM vendor_deposits WHERE tenant_id = $1 AND deposit_number = 'VD-2026-00003'",
        "dp": "SELECT id, deposit_number FROM customer_deposits WHERE tenant_id = $1 ORDER BY created_at LIMIT 1"}.items()}
    gi = await c.fetchval("SELECT id FROM goods_receipt_items WHERE goods_receipt_id = $1 ORDER BY line_number LIMIT 1", q["grn"]["id"])
    bi17 = await c.fetchval("SELECT id FROM bill_items WHERE bill_id = $1 LIMIT 1", q["f17"]["id"])
    bi16 = await c.fetchval("SELECT id FROM bill_items WHERE bill_id = $1 LIMIT 1", q["f16"]["id"])

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
    RG.get_pool = gp
    RV.get_pool = gp
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
                "tautan": await c2.fetchval("SELECT count(*) FROM goods_receipt_bill_links"),
                "penerapan": await c2.fetchval("SELECT count(*) FROM vendor_deposit_applications"),
                "ump_po": await c2.fetchval("SELECT count(*) FROM vendor_deposits WHERE tenant_id = $1 AND purchase_order_id IS NOT NULL", T),
                "chk": await c2.fetchval("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'chk_da_entity'")}

    harus("prasyarat: GRN-2610-0002 (PO-2610-0006), PB-17/16 berbaris, VD-2026-00003, DP pelanggan, baris GRN",
          all(q.values()) and q["grn"]["purchase_order_id"] == q["po"]["id"] and gi and bi17 and bi16, {k: bool(v) for k, v in q.items()})
    sebelum = await saksi()
    harus("prasyarat: CHECK produksi memuat goods_receipt + vendor_deposit (V466 sudah diterapkan)",
          "goods_receipt" in sebelum["chk"] and "vendor_deposit" in sebelum["chk"], sebelum["chk"][-120:])
    if "goods_receipt" not in sebelum["chk"]:
        raise SystemExit("TOLAK: V466 belum diterapkan di produksi")
    luar = c.transaction()
    await luar.start()
    try:
        await c.execute("""INSERT INTO goods_receipt_bill_links (goods_receipt_item_id, bill_item_id, quantity, grni_amount, status, kind)
                           VALUES ($1, $2, 1, 0, 'active', 'terima_dulu'), ($1, $3, 1, 0, 'reversed', 'terima_dulu')""", gi, bi17, bi16)
        await c.execute("UPDATE vendor_deposits SET purchase_order_id = $1 WHERE id = $2 AND tenant_id = $3", q["po"]["id"], q["ump"]["id"], T)
        # VD-2026-00003 sudah dikembalikan penuh (refund 50.003 = amount) -> ruang 1.000 untuk penerapan fixture (ROLLBACK)
        await c.execute("UPDATE vendor_deposits SET amount = amount + 1000 WHERE id = $1 AND tenant_id = $2", q["ump"]["id"], T)
        await c.execute("""INSERT INTO vendor_deposit_applications (vendor_deposit_id, bill_id, amount, applied_date, status)
                           VALUES ($1, $2, 1000, CURRENT_DATE, 'active')""", q["ump"]["id"], q["f17"]["id"])
        ids = {}
        for k, et, eid in (("G", "goods_receipt", q["grn"]["id"]), ("U", "vendor_deposit", q["ump"]["id"]), ("F", "bill", q["f17"]["id"]),
                           ("O", "purchase_order", q["po"]["id"]), ("R", "bill", q["f16"]["id"]), ("C", "customer_deposit", q["dp"]["id"])):
            d = await c.fetchval("""INSERT INTO documents (tenant_id, file_name, file_path, file_type, file_size, storage_type, uploaded_by)
                                    VALUES ($1, $2, $3, 'image/png', 10, 's3', $4) RETURNING id""",
                                 T, f"{PENANDA} {k}.png", f"{T}/uploads/lampiran/uji-f2b-{k}.png", uid)
            await c.execute("INSERT INTO document_attachments (tenant_id, document_id, entity_type, entity_id) VALUES ($1, $2, $3, $4)", T, d, et, eid)
            ids[k] = str(d)
        G, U, F, O, R, C = (ids[k] for k in "GUFORC")
        o_g = await LD.lampiran_dokumen(req(T, uid), "goods_receipt", str(q["grn"]["id"]))
        o_u = await LD.lampiran_dokumen(req(T, uid), "vendor_deposit", str(q["ump"]["id"]))
        o_b = await LD.lampiran_dokumen(req(T, uid), "bill", str(q["f17"]["id"]))
        o_p = await LD.lampiran_dokumen(req(T, uid), "purchase_order", str(q["po"]["id"]))
        catat.update({"contoh_goods_receipt": o_g, "contoh_vendor_deposit": o_u, "contoh_bill": o_b, "contoh_purchase_order": o_p})
        hg, hu, hb, hp = lid(o_g), lid(o_u), lid(o_b), lid(o_p)
        harus("lampiran/goods_receipt: PO{O} + GRN{G} + FPB{F}", O in hg.get("purchase_order", set()) and G in hg.get("goods_receipt", set())
              and F in hg.get("bill", set()), hg)
        harus("lampiran/goods_receipt: FPB bertaut 'reversed' (R) TAK ikut", R not in set().union(*hg.values()), hg)
        harus("lampiran/vendor_deposit: PO{O} + UMP{U} + FPB{F}", O in hu.get("purchase_order", set()) and U in hu.get("vendor_deposit", set())
              and F in hu.get("bill", set()), hu)
        harus("lampiran/bill PB-17: UMP{U} + GRN{G} + FPB{F}", U in hb.get("vendor_deposit", set()) and G in hb.get("goods_receipt", set())
              and F in hb.get("bill", set()), hb)
        harus("lampiran/purchase_order: PO{O} + UMP{U} + GRN{G}", O in hp.get("purchase_order", set()) and U in hp.get("vendor_deposit", set())
              and G in hp.get("goods_receipt", set()), hp)
        urut = [g["dokumen"]["jenis"] for g in o_g["data"] if not g.get("tersembunyi_izin")]
        harus("urutan grup lampiran/goods_receipt = purchase_order, goods_receipt, bill", urut == ["purchase_order", "goods_receipt", "bill"], urut)
        urut_b = [g["dokumen"]["jenis"] for g in o_b["data"] if not g.get("tersembunyi_izin")]
        harus("urutan grup lampiran/bill: vendor_deposit, goods_receipt, bill", urut_b[:3] == ["vendor_deposit", "goods_receipt", "bill"], urut_b)
        semua = set().union(*hg.values(), *hu.values(), *hb.values(), *hp.values())
        harus("lampiran DP pelanggan (customer_deposit) TAK PERNAH muncul di keluarga Pembelian", C not in semua, sorted(semua))
        dg = await ep(LI.router_penerimaan_barang, "daftar_lampiran_goods_receipt")(req(T, uid), q["grn"]["id"])
        du = await ep(LI.router_uang_muka_pembelian, "daftar_lampiran_vendor_deposit")(req(T, uid), q["ump"]["id"])
        harus("rute pabrik GET /goods-receipts/{id}/attachments = {G}", {x["id"] for x in dg["data"]} == {G}, dg["data"])
        harus("rute pabrik GET /vendor-deposits/{id}/attachments = {U}", {x["id"] for x in du["data"]} == {U}, du["data"])
        await ep(LI.router_penerimaan_barang, "lepas_lampiran_goods_receipt")(req(T, uid, "DELETE"), q["grn"]["id"], uuid.UUID(G))
        dg2 = await ep(LI.router_penerimaan_barang, "daftar_lampiran_goods_receipt")(req(T, uid), q["grn"]["id"])
        harus("rute pabrik DELETE melepas G -> daftar GRN kosong", dg2["data"] == [], dg2["data"])
        rg = await RG.get_goods_receipt(req(T, uid), q["grn"]["id"])
        rv = await RV.get_vendor_deposit(req(T, uid), q["ump"]["id"])
        harus("GET /goods-receipts/{id} lama tetap berfungsi", q["grn"]["receipt_number"] in json.dumps(rg, default=str), str(rg)[:200])
        harus("GET /vendor-deposits/{id} lama tetap berfungsi", q["ump"]["deposit_number"] in json.dumps(rv, default=str), str(rv)[:200])
        for jenis, did in (("goods_receipt", q["grn"]["id"]), ("vendor_deposit", q["ump"]["id"])):
            try:
                await LD.lampiran_dokumen(req(LAIN, uid), jenis, str(did))
                harus(f"lintas tenant: lampiran/{jenis} kaos dibaca tenant lain -> 404", False, "tak 404")
            except HTTPException as e:
                harus(f"lintas tenant: lampiran/{jenis} kaos dibaca tenant lain -> 404", e.status_code == 404, e.detail)
    finally:
        await luar.rollback()
    sesudah = await saksi()
    harus("ROLLBACK: dari koneksi LAIN nol sisa (dokumen/tautan/penerapan/UMP ber-PO), CHECK tak berubah", sesudah == sebelum,
          [sebelum, sesudah])
    harus("nol panggilan kanal keluar", KELUAR == [], KELUAR)
    await c.close()
    await c2.close()
    gagal = [x["cek"] for x in cek if not x["ok"]]
    json.dump({"cek": cek, "catat": catat, "gagal": gagal}, open(keluar, "w"), default=str, indent=1)
    print("HASIL", "LULUS" if not gagal else f"GAGAL {gagal}", f"({len(cek)} cek)")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
