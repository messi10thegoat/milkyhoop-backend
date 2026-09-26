"""F3 SI langsung (26 Sep 2026, BACKEND): jatuh tempo faktur langsung dari termin pelanggan; detail pelanggan
membawa sumber termin. Semua tulisan di SALINAN (pgjourney).
1. pelanggan termin 0 -> GET detail: payment_terms_source 'default'; POST faktur tanpa due_date -> due = tgl faktur, 'default'
2. PATCH pelanggan payment_terms_days=14 -> detail 'customer_terms'; faktur tanpa due_date -> tgl+14, 'customer_terms'
3. faktur dengan due_date isian -> isian menang, 'body'
Nilai yang DISIMPAN (DB) == yang DIBALAS."""
import uuid
from datetime import timedelta

from journey_lib import BARANG, NAMA, PELANGGAN, T


async def _faktur(J, nama, due=None):
    b = {"customer_id": PELANGGAN, "customer_name": NAMA, "invoice_date": J.hari.isoformat(),
         "items": [{"item_id": BARANG, "description": "Kaos journey F3", "quantity": "1", "unit_price": 50000}]}
    if due:
        b["due_date"] = due.isoformat()
    _, r = await J.langkah(nama, "POST", "/api/sales-invoices", b, harap=(200, 201))
    d = (r or {}).get("data") or {}
    async with J.pool.acquire() as c:
        db = await c.fetchval("select due_date from sales_invoices where id=$1 and tenant_id=$2",
                              uuid.UUID(d["id"]), T) if d.get("id") else None
    return d, db


async def _detail(J, nama):
    _, r = await J.langkah(nama, "GET", f"/api/customers/{PELANGGAN}")
    return (r or {}).get("data") or {}


def _cek(J, nama, d, db, due, sumber):
    if d.get("due_date") != due.isoformat() or d.get("due_date_source") != sumber or db != due:
        J.gagal(nama, f"harap {due} {sumber}; balasan {d.get('due_date')} {d.get('due_date_source')}; DB {db}")


async def jalankan(J):
    h = J.hari
    await J.langkah("00_termin_0", "PATCH", f"/api/customers/{PELANGGAN}", {"payment_terms_days": 0})
    det = await _detail(J, "01_detail_default")
    if det.get("payment_terms_source") != "default" or det.get("payment_terms_days") != 0:
        J.gagal("01_detail", f"harap 0/default, dapat {det.get('payment_terms_days')}/{det.get('payment_terms_source')}")
    d, db = await _faktur(J, "02_faktur_default")
    _cek(J, "02_cek", d, db, h, "default")

    await J.langkah("03_termin_14", "PATCH", f"/api/customers/{PELANGGAN}", {"payment_terms_days": 14})
    det = await _detail(J, "04_detail_termin")
    if det.get("payment_terms_source") != "customer_terms" or det.get("payment_terms_days") != 14:
        J.gagal("04_detail", f"harap 14/customer_terms, dapat {det.get('payment_terms_days')}/{det.get('payment_terms_source')}")
    d, db = await _faktur(J, "05_faktur_termin")
    _cek(J, "05_cek", d, db, h + timedelta(days=14), "customer_terms")

    isian = h + timedelta(days=3)
    d, db = await _faktur(J, "06_faktur_isian", due=isian)
    _cek(J, "06_cek", d, db, isian, "body")

    # ---- konversi penawaran -> faktur (lengan B: router quotes hanya di app.main penuh) ----
    if J.lengan != "B":
        return
    async def _penawaran(nama, terms):
        _, q = await J.langkah(f"{nama}_buat", "POST", "/api/quotes", {
            "quote_date": h.isoformat(), "customer_id": PELANGGAN, "customer_name": NAMA, "terms": terms,
            "items": [{"item_id": BARANG, "description": "Kaos journey F3", "quantity": "1", "unit_price": 50000}]})
        qid = ((q or {}).get("data") or {}).get("id")
        if not qid:
            J.gagal(f"{nama}_id", f"penawaran tak dibuat: {str(q)[:200]}")
            return None, None
        await J.langkah(f"{nama}_kirim", "POST", f"/api/quotes/{qid}/send", {})
        _, r = await J.langkah(f"{nama}_ke_faktur", "POST", f"/api/quotes/{qid}/to-invoice", {})
        d = (r or {}).get("data") or {}
        async with J.pool.acquire() as c:
            db = await c.fetchval("select due_date from sales_invoices where id=$1 and tenant_id=$2",
                                  uuid.UUID(d["invoice_id"]), T) if d.get("invoice_id") else None
        return d, db
    # pelanggan masih termin 14; syarat penawaran "NET 7" menang atas pelanggan
    d, db = await _penawaran("07_penawaran_net7", "NET 7 hari")
    if d is not None:
        _cek(J, "07_cek", d, db, h + timedelta(days=7), "so_terms")
    # syarat non-NET -> termin pelanggan (14), BUKAN +30 hardcode lama
    d, db = await _penawaran("08_penawaran_teks", "Pembayaran transfer bank")
    if d is not None:
        _cek(J, "08_cek", d, db, h + timedelta(days=14), "customer_terms")
