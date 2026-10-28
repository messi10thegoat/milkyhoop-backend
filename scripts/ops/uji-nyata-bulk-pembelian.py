"""Harness NYATA B1a (BACA SAJA): koneksi default_transaction_read_only=on; tak ada tulisan (audit BULK_EXPORT di-no-op).

Per tenant x 6 modul Pembelian: SEMUA baris daftar (handler daftar dipanggil langsung, halaman 100) dibandingkan dengan baris CSV
(bulk_export_beli.susun_baris) id demi id: vendor, nomor, tanggal, kolom ke-4, nilai, sisa, dan Status = sel Status LAYAR
(PO position_text; GRN status_text; tagihan position_text; Pembayaran Keluar peta FE billPayment.ts Draf/Dibayar/Batal; UMP/ND status_text).
Lalu handler ekspor ASLI (rute) ujung-ke-ujung per modul, dan render 1 PDF per kind lewat dokumen._render + tulis_pdf (tanpa cache Redis).
Argumen: <keluaran.json> [tenant ...]."""
import asyncio
import json
import os
import sys
import uuid
from datetime import date, datetime
from decimal import Decimal

import asyncpg

sys.path.insert(0, os.getcwd())
os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-harness")

from app.routers import bills as RBILL  # noqa: E402
from app.routers import bill_payments as RBP  # noqa: E402
from app.routers import bulk as RB  # noqa: E402
from app.routers import bulk_dokumen as BD  # noqa: E402
from app.routers import dokumen as D  # noqa: E402
from app.routers import goods_receipts as RGR  # noqa: E402
from app.routers import purchase_orders as RPO  # noqa: E402
from app.routers import vendor_credits as RVC  # noqa: E402
from app.routers import vendor_deposits as RVD  # noqa: E402
from app.services import bulk as B  # noqa: E402
from app.services import bulk_export_beli as BB  # noqa: E402

STATUS_BAYAR_FE = {"draft": "Draf", "posted": "Dibayar", "voided": "Batal"}  # == FE recipes/billPayment.ts statusPembayaranKeluar


class Req:
    def __init__(self, tid):
        self.headers = {}
        self.state = type("S", (), {"user": {"tenant_id": tid, "user_id": str(uuid.UUID(int=1)), "role": "OWNER"}})()
        self.query_params = {}


def n(v):
    if v is None or v == "":
        return None
    if isinstance(v, (datetime, date)):
        return v.isoformat()[:10]
    if isinstance(v, (int, float, Decimal)) and not isinstance(v, bool):
        return str(Decimal(str(v)).quantize(Decimal("0.01")))
    s = str(v)
    try:
        return str(Decimal(s).quantize(Decimal("0.01")))
    except Exception:
        return s[:10] if len(s) >= 10 and s[4] == "-" and s[7] == "-" and s[:4].isdigit() else s


def harap(modul, it):
    """sel layar dari baris daftar ASLI -> [vendor, nomor, tgl, kol4, ..., status]"""
    if modul == "purchase-orders":
        return [it["vendor_name"], it["po_number"], it["po_date"], it.get("expected_date"), it["total_amount"], it["sisa_ditagih"], it["position_text"]]
    if modul == "goods-receipts":
        return [it["vendor_name"], it["receipt_number"], it["receipt_date"], it["po_number"], it.get("warehouse_name"),
                it.get("status_text") or it.get("position_text")]
    if modul == "bills":
        return [it["vendor"]["name"], it["invoice_number"], it["issue_date"], it["due_date"], it["amount"], it.get("outstanding_amount"), it["position_text"]]
    if modul == "bill-payments":
        k, t = it.get("alokasi_keadaan"), it.get("alokasi_teks")
        return [it["vendor_name"], it["payment_number"], it["payment_date"], t if k and t else None, it["total_amount"],
                it.get("belum_dialokasikan"), STATUS_BAYAR_FE.get(it["status"], it["status"])]
    if modul == "vendor-deposits":
        return [it["vendor_name"], it["deposit_number"], it["deposit_date"], it["amount"], it.get("sisa"), it.get("status_text") or it["status"]]
    if modul == "vendor-credits":
        sisa = None if it.get("sisa_keadaan") == "draf" else it.get("remaining_amount")
        return [it["vendor_name"], it["vendor_credit_number"], it["vendor_credit_date"], it.get("original_bill_number"), it["total_amount"], sisa,
                it.get("status_text") or it["status"]]
    raise KeyError(modul)


def ke_dict(x):
    return x.model_dump(mode="json") if hasattr(x, "model_dump") else (x if isinstance(x, dict) else dict(x))


async def semua_baris(modul, req, pool):
    out, skip = [], 0
    while True:
        if modul == "purchase-orders":
            r = await RPO.list_purchase_orders(req, status="all", vendor_id=None, search=None, date_from=None, date_to=None, skip=skip, limit=100,
                                               page=1, per_page=None, offset=None, sort_by="created_at", sort_order="desc", aksi=None, with_totals=False)
            items, total = r["items"], r["total"]
        elif modul == "goods-receipts":
            r = await RGR.list_goods_receipts(req, purchase_order_id=None, status=None, aksi=None, limit=100, offset=skip)
            items, total = r["data"]["items"], r["data"]["total"]
        elif modul == "bills":
            r = await RBILL.BillsService(pool).list_bills(tenant_id=req.state.user["tenant_id"], skip=skip, limit=100)
            items, total = r["items"], r["total"]
        elif modul == "bill-payments":
            r = await RBP.list_bill_payments(req, skip=skip, limit=100, **_bawaan(RBP.list_bill_payments, {"request", "skip", "limit"}))
            r = ke_dict(r)
            items, total = r["items"], r["total"]
        elif modul == "vendor-deposits":
            r = await RVD.list_vendor_deposits(req, skip=skip, limit=100, status=None, vendor_id=None, start_date=None, end_date=None, search=None,
                                               page=1, per_page=None, offset=None, sort_by="deposit_date", sort_order="desc", aksi=None, with_totals=False)
            r = ke_dict(r)
            items, total = r["items"], r["total"]
        else:
            r = await RVC.list_vendor_credits(req, status="all", vendor_id=None, search=None, date_from=None, date_to=None, skip=skip, limit=100,
                                              sort_by="created_at", sort_order="desc", aksi=None, with_totals=False)
            items, total = r["items"], r["total"]
        out += [ke_dict(i) for i in items]
        skip += 100
        if skip >= total:
            return out


def _bawaan(fn, kecuali):
    """argumen bawaan non-Query (default Query(...) diganti default-nya) untuk panggilan langsung."""
    import inspect
    hasil = {}
    for nama, p in inspect.signature(fn).parameters.items():
        if nama in kecuali:
            continue
        d = p.default
        hasil[nama] = getattr(d, "default", d)
    return hasil


async def main(keluar, tenants):
    url = os.environ["URL"]
    pool = await asyncpg.create_pool(url, min_size=1, max_size=4, server_settings={"default_transaction_read_only": "on"})

    async def gp(): return pool
    for mod in (RPO, RGR, RBILL, RBP, RVD, RVC, RB, BD):
        if hasattr(mod, "get_pool"):
            mod.get_pool = gp

    async def tanpa_audit(*a, **k): return None
    B.catat_audit = tanpa_audit
    async with pool.acquire() as c:
        assert await c.fetchval("SHOW default_transaction_read_only") == "on"
    hasil, gagal = {}, []
    for tid in tenants:
        req = Req(tid)
        for modul, spec in BB.SPEC_BELI.items():
            daftar = await semua_baris(modul, req, pool)
            ids = [uuid.UUID(str(i["id"])) for i in daftar]
            csv = {}
            for k in range(0, len(ids), 500):
                async with pool.acquire() as conn:
                    baris, ada, lewat = await BB.susun_baris(conn, tid, spec, ids[k:k + 500])
                assert lewat == 0, (tid, modul, lewat)
                csv.update({b[-1]: b for b in baris})
            beda = []
            for it in daftar:
                b = csv[str(it["id"])]
                h = [n(x) for x in harap(modul, it)]
                c = [n(x) for x in b[:len(spec.kolom)]]
                if h != c:
                    beda.append({"id": str(it["id"]), "daftar": h, "csv": c})
            st = {}
            for it in daftar:
                k = n(harap(modul, it)[-1])
                st[k] = st.get(k, 0) + 1
            # handler ekspor ASLI (rute) untuk <= 500 id pertama
            ep = next(r.endpoint for r in RB.ROUTERS[modul].routes if r.endpoint.__name__ == "bulk_export")
            rute = None
            if ids:
                resp = await ep(req, RB.BulkExportRequest(ids=[str(i) for i in ids[:500]]))
                teks = resp.body.decode("utf-8-sig").splitlines()
                rute = {"header": teks[0], "baris": len(teks) - 1, "ditemukan": resp.headers["X-Bulk-Ditemukan"]}
                if len(teks) - 1 != min(len(ids), 500) or rute["header"] != ",".join(list(spec.kolom) + list(BB.KOLOM_TAMBAHAN)):
                    beda.append({"rute": rute})
            # render 1 PDF per kind (tanpa cache)
            pdf = None
            if ids:
                async with pool.acquire() as conn:
                    r, nomor = await D._render(conn, {"tenant_id": tid, "user_id": uuid.UUID(int=1)}, req, BD.MODUL_KIND[modul], str(ids[0]))
                isi = D.get_pdf_service().tulis_pdf(r)
                pdf = {"nomor": nomor, "bytes": len(isi), "pdf": isi[:5] == b"%PDF-"}
                if not pdf["pdf"]:
                    beda.append({"pdf": pdf})
            hasil[f"{tid}|{modul}"] = {"n": len(daftar), "beda": len(beda), "status": st, "rute": rute, "pdf": pdf, "contoh_beda": beda[:3]}
            if beda:
                gagal.append(f"{tid}|{modul}")
            print(f"{tid}|{modul} n={len(daftar)} beda={len(beda)} status={st} pdf={pdf}", flush=True)
    await pool.close()
    json.dump({"hasil": hasil, "gagal": gagal}, open(keluar, "w"), default=str, indent=1)
    print("HASIL", "LULUS" if not gagal else f"GAGAL {gagal}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2:] or ["kaos-biru-konveksi", "grapgrap-manado"]))
