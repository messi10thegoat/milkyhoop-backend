"""PDF faktur per status pembayaran (pemilik 27 Sep 2026; spek milkyhoop-docs-rute/pdf-faktur-status).
Di salinan kaos, lewat API nyata: 5 faktur (belum / terlambat / sebagian+DP / lunas DP+pelunasan / batal) + 1 faktur
30 baris (terlambat + bayar sebagian -> multi-halaman). Tiap faktur: detail server + PDF inline disimpan ke
/out/pdf_status/<nama>.{json,pdf}; isi PDF diperiksa di host (cek_pdf_status.py, pdftotext) terhadap angka server."""
import json
import os
import uuid
from datetime import timedelta

from journey_lib import NAMA, OWNER, PELANGGAN, T
from skenario_kirim_non_stok import _barang

BCA_BA, BCA_COA = "56842900-0ae4-4c25-8ad5-d0726a36e21b", "15105bf6-183e-4f93-abcc-5689f22fa394"
REKENING = {"payment_bank_name": "Bank BCA", "payment_account_number": "1111222233",
            "payment_account_holder": "BCA Operasional"}   # bentuk data kaos nyata (nama akun sebagai pemilik)
KELUAR = "/out/pdf_status"


async def _faktur(J, nama, pid, tgl, due, baris=1, qty=11, harga=270000):
    b = {"customer_id": PELANGGAN, "customer_name": NAMA, "invoice_date": tgl.isoformat(), "due_date": due.isoformat(),
         "notes": None, **REKENING,
         "items": [{"item_id": pid, "description": f"TES E2E PDF {nama} baris {i + 1}", "unit": "pcs",
                    "quantity": str(qty), "unit_price": harga} for i in range(baris)]}
    _, r = await J.langkah(f"{nama}_buat", "POST", "/api/sales-invoices", b)
    inv = ((r or {}).get("data") or {}).get("id")
    if not inv:
        J.gagal(f"{nama}_id", str(r)[:200])
        return None
    await J.langkah(f"{nama}_post", "POST", f"/api/sales-invoices/{inv}/post", {})
    return inv


async def _dp(J, nama, inv, jumlah, tgl):
    _, r = await J.langkah(f"{nama}_dp", "POST", "/api/customer-deposits", {
        "customer_id": PELANGGAN, "customer_name": NAMA, "amount": jumlah, "deposit_date": tgl.isoformat(),
        "payment_method": "transfer", "account_id": BCA_COA, "bank_account_id": BCA_BA, "auto_post": True,
        "idempotency_key": f"journey-dp-{uuid.uuid4()}"})
    dp = ((r or {}).get("data") or {}).get("id")
    if not dp:
        return J.gagal(f"{nama}_dp_id", str(r)[:200])
    await J.langkah(f"{nama}_dp_apply", "POST", f"/api/customer-deposits/{dp}/apply",
                    {"applications": [{"invoice_id": inv, "amount": jumlah}]})


async def _bayar(J, nama, inv, jumlah, tgl):
    await J.langkah(f"{nama}_bayar", "POST", "/api/receive-payments", {
        "customer_id": PELANGGAN, "payment_date": tgl.isoformat(), "payment_method": "bank_transfer",
        "bank_account_id": BCA_BA, "total_amount": str(jumlah),
        "allocations": [{"invoice_id": inv, "amount_applied": str(jumlah)}]},
        headers={"X-Idempotency-Key": f"journey-rp-{uuid.uuid4()}"})


async def _simpan(J, nama, inv):
    _, det = await J.langkah(f"{nama}_detail", "GET", f"/api/sales-invoices/{inv}")
    r = await J.cl.get(f"/api/sales-invoices/{inv}/pdf?format=inline", headers=J._hdr(OWNER))
    if r.status_code != 200 or not r.content.startswith(b"%PDF"):
        return J.gagal(f"{nama}_pdf", f"{r.status_code} {r.text[:200]}")
    os.makedirs(KELUAR, exist_ok=True)
    open(f"{KELUAR}/{nama}.pdf", "wb").write(r.content)
    async with J.pool.acquire() as c:
        tgl_bisnis = await c.fetchval("select tanggal_bisnis($1)", T)
    json.dump({"detail": det, "tanggal_bisnis": str(tgl_bisnis)}, open(f"{KELUAR}/{nama}.json", "w"), default=str)
    print(f"PDF {nama} {len(r.content)} bytes")


async def jalankan(J):
    pid = await _barang(J, "PDF", False)
    if not pid:
        return
    h = J.hari
    f = await _faktur(J, "belum", pid, h, h + timedelta(days=14))
    if f:
        await _simpan(J, "belum", f)
    f = await _faktur(J, "terlambat", pid, h - timedelta(days=20), h - timedelta(days=5))
    if f:
        await _simpan(J, "terlambat", f)
    f = await _faktur(J, "sebagian", pid, h, h + timedelta(days=14))
    if f:
        await _dp(J, "sebagian", f, 1500000, h)
        await _simpan(J, "sebagian", f)
    f = await _faktur(J, "lunas", pid, h - timedelta(days=3), h + timedelta(days=11))
    if f:
        await _dp(J, "lunas", f, 1500000, h - timedelta(days=3))
        await _bayar(J, "lunas", f, 1470000, h)
        await _simpan(J, "lunas", f)
    f = await _faktur(J, "batal", pid, h, h + timedelta(days=14))
    if f:
        await J.langkah("batal_void", "POST", f"/api/sales-invoices/{f}/void",
                        {"reason": "pesanan diganti dengan faktur baru (TES E2E)"})
        await _simpan(J, "batal", f)
    f = await _faktur(J, "multi", pid, h - timedelta(days=20), h - timedelta(days=5), baris=30, qty=2, harga=125000)
    if f:
        await _bayar(J, "multi", f, 500000, h - timedelta(days=2))
        await _simpan(J, "multi", f)
