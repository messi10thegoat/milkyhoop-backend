"""Skenario C3/C6/C7 (26 Sep 2026, BACKEND3) — handler NYATA di salinan terisolasi.
P: proforma DP 50% dgn X-Idempotency-Key -> ulang isi sama = SAMA (satu baris) -> isi beda 409 IDEMPOTENCY_KEY_REUSED;
   dua draf 60% tanpa kunci -> terbit pertama 200, terbit kedua DITOLAK (plafon, kunci SO).
C6: id jalur buruk 404; to-invoice qty 0 / so_item_id buruk 422; buat SO customer_id buruk 422.
C7: uang muka untuk SO DRAF -> 400 SO_NOT_ACCEPTING_DEPOSIT. DELETE draf tetap jalan."""
import uuid

from journey_lib import KAS, NAMA, PELANGGAN
from skenario_so_penuh import so_body

DIKENAL = {}


def _kode(b):
    d = (b or {}).get("detail")
    return d.get("code") if isinstance(d, dict) else None


async def _so(J, awal, konfirmasi=True):
    _, so = await J.langkah(f"{awal}_buat", "POST", "/api/sales-orders", so_body(J),
                            headers={"X-Idempotency-Key": f"journey-{uuid.uuid4()}"})
    so_id = ((so or {}).get("data") or {}).get("id")
    if so_id and konfirmasi:
        await J.langkah(f"{awal}_confirm", "POST", f"/api/sales-orders/{so_id}/confirm")
    return so_id


async def jalankan(J):
    a = await _so(J, "P01")
    if not a:
        return J.gagal("P01", "SO gagal")
    kunci = {"X-Idempotency-Key": f"journey-pf-{uuid.uuid4()}"}
    badan = {"sales_order_id": a, "purpose": "DP", "percent_of_order": 50}
    _, r1 = await J.langkah("P02_proforma_berkunci", "POST", "/api/proformas", badan, headers=kunci)
    _, r2 = await J.langkah("P03_ulang_isi_sama", "POST", "/api/proformas", badan, headers=kunci)
    if ((r1 or {}).get("data") or {}).get("id") != ((r2 or {}).get("data") or {}).get("id"):
        J.gagal("P03_sama", f"replay beda: {r1} / {r2}")
    _, r3 = await J.langkah("P04_isi_beda_409", "POST", "/api/proformas", {**badan, "percent_of_order": 40},
                            headers=kunci, harap=(409,))
    if _kode(r3) != "IDEMPOTENCY_KEY_REUSED":
        J.gagal("P04_kode", str(r3)[:200])
    async with J.pool.acquire() as c:
        n = await c.fetchval("SELECT count(*) FROM proformas WHERE sales_order_id = $1::uuid", a)
    if n != 1:
        J.gagal("P05_satu_baris", f"proforma untuk SO = {n}, harap 1")

    b = await _so(J, "Q01")
    if b:
        _, x = await J.langkah("Q02_draf1_60", "POST", "/api/proformas", {"sales_order_id": b, "purpose": "DP", "percent_of_order": 60})
        _, y = await J.langkah("Q03_draf2_60", "POST", "/api/proformas", {"sales_order_id": b, "purpose": "DP", "percent_of_order": 60})
        ix, iy = ((x or {}).get("data") or {}).get("id"), ((y or {}).get("data") or {}).get("id")
        await J.langkah("Q04_terbit1", "POST", f"/api/proformas/{ix}/issue")
        await J.langkah("Q05_terbit2_lewat_plafon_ditolak", "POST", f"/api/proformas/{iy}/issue", harap=(400, 409))

    await J.langkah("C6a_detail_uuid_buruk_404", "GET", "/api/sales-orders/bukan-uuid", harap=(404,))
    await J.langkah("C6b_confirm_uuid_buruk_404", "POST", "/api/sales-orders/bukan-uuid/confirm", harap=(404,))
    if a:
        _, det = await J.langkah("C6c_detail", "GET", f"/api/sales-orders/{a}")
        soi = det["data"]["items"][0]["id"]
        await J.langkah("C6d_to_invoice_qty0_422", "POST", f"/api/sales-orders/{a}/to-invoice",
                        {"items": [{"so_item_id": soi, "quantity": 0}]}, harap=(422,))
        await J.langkah("C6e_to_invoice_item_buruk_422", "POST", f"/api/sales-orders/{a}/to-invoice",
                        {"items": [{"so_item_id": "x", "quantity": 1}]}, harap=(422,))
    await J.langkah("C6f_buat_so_customer_buruk_422", "POST", "/api/sales-orders", {**so_body(J), "customer_id": "x"},
                    harap=(422,))

    d = await _so(J, "D01", konfirmasi=False)
    if d:
        dp = {"customer_id": PELANGGAN, "customer_name": NAMA, "amount": 100000, "deposit_date": J.hari.isoformat(),
              "payment_method": "cash", "account_id": KAS, "sales_order_id": d, "auto_post": True,
              "idempotency_key": f"journey-dp-{uuid.uuid4()}"}
        _, r = await J.langkah("D02_dp_so_draf_400", "POST", "/api/customer-deposits", dp, harap=(400,))
        if _kode(r) != "SO_NOT_ACCEPTING_DEPOSIT":
            J.gagal("D02_kode", str(r)[:200])
        await J.langkah("D03_hapus_draf", "DELETE", f"/api/sales-orders/{d}")
        await J.langkah("D04_hapus_lagi_404", "DELETE", f"/api/sales-orders/{d}", harap=(404,))
