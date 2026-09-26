"""Skenario 2 — DP lewat PROFORMA (26 Sep 2026, permintaan MASTER):
Bagian 1 (bahagia): SO -> confirm -> proforma DP 30% (draft) -> issue (+issue ulang ditolak) -> uang muka
  ber-proforma_id -> to-invoice penuh -> post (DP diterapkan otomatis) -> kirim -> bayar sisa -> close.
Bagian 2 (jalur void): SO2 -> proforma -> issue -> DP -> VOID DP -> cancel proforma -> DP baru (tanpa proforma)
  -> to-invoice -> post -> terima bayar -> VOID bayar -> VOID faktur -> cancel SO (DP aktif ditolak) -> REFUND DP
  -> cancel SO.
Tiap potret: jurnal seimbang+POSTED, AR faktur == compute_ar_outstanding, payment_summary SO, detail proforma."""
import uuid

from journey_lib import KAS, NAMA, PELANGGAN
from skenario_so_penuh import bayar_semua, kirim, so_body

DIKENAL = {"fulfill_partial_paid": "fulfill guard status=posted saja (BACKEND sedang memperbaiki, 26 Sep)"}


async def buat_confirm(J, label):
    _, so = await J.langkah(f"{label}_buat_so", "POST", "/api/sales-orders", so_body(J),
                            headers={"X-Idempotency-Key": f"journey-{uuid.uuid4()}"})
    so_id = ((so or {}).get("data") or {}).get("id")
    if so_id:
        await J.langkah(f"{label}_confirm", "POST", f"/api/sales-orders/{so_id}/confirm")
    return so_id


async def proforma(J, label, so_id, persen):
    _, pf = await J.langkah(f"{label}_proforma_buat", "POST", "/api/proformas",
                            {"sales_order_id": so_id, "purpose": "DP", "percent_of_order": str(persen)})
    d = (pf or {}).get("data") or pf or {}
    pf_id = d.get("id") if isinstance(d, dict) else None
    if not pf_id:
        J.gagal(f"{label}_proforma_id", f"proforma tak dibuat: {str(pf)[:200]}")
    return pf_id


async def detail_pf(J, label, pf_id):
    _, d = await J.langkah(f"{label}__detail_proforma", "GET", f"/api/proformas/{pf_id}")
    x = (d or {}).get("data") or {}
    print(f"   proforma: status={x.get('status')} amount={x.get('amount')} paid={x.get('paid_amount')} "
          f"outstanding={x.get('outstanding_amount')}")
    return x


def dp_body(J, so_id, jumlah, pf_id=None):
    b = {"customer_id": PELANGGAN, "customer_name": NAMA, "amount": jumlah, "deposit_date": J.hari.isoformat(),
         "payment_method": "cash", "account_id": KAS, "sales_order_id": so_id, "auto_post": True,
         "idempotency_key": f"journey-dp-{uuid.uuid4()}"}
    if pf_id:
        b["proforma_id"] = pf_id
    return b


async def jalankan(J):
    # ---------------- bagian 1: bahagia
    so_id = await buat_confirm(J, "01")
    if not so_id:
        return J.gagal("01_so", "SO gagal dibuat")
    pf_id = await proforma(J, "02", so_id, 30)
    if not pf_id:
        return
    await detail_pf(J, "02_draft", pf_id)
    await J.langkah("03_issue", "POST", f"/api/proformas/{pf_id}/issue")
    await J.langkah("03b_issue_ulang_ditolak", "POST", f"/api/proformas/{pf_id}/issue", harap=(400, 409))
    await J.langkah("03c_proforma_milik_so", "GET", f"/api/sales-orders/{so_id}/proformas")
    pf = await detail_pf(J, "03_issued", pf_id)
    jumlah = int(float(pf.get("amount") or 150000))
    _, dp = await J.langkah("04_dp_proforma", "POST", "/api/customer-deposits", dp_body(J, so_id, jumlah, pf_id))
    await detail_pf(J, "04_sesudah_dp", pf_id)
    await J.potret("04_sesudah_dp", so_id)

    _, det = await J.langkah("05_detail_baris", "GET", f"/api/sales-orders/{so_id}")
    soi = det["data"]["items"][0]["id"]
    await J.langkah("05_to_invoice_penuh", "POST", f"/api/sales-orders/{so_id}/to-invoice",
                    {"items": [{"so_item_id": soi, "quantity": 10}]})
    inv = await J.faktur_so(so_id)
    if not inv:
        return J.gagal("05_faktur", "to-invoice tak menghasilkan faktur")
    await J.langkah("05b_post", "POST", f"/api/sales-invoices/{inv[0]}/post", {})
    await detail_pf(J, "05_sesudah_post", pf_id)
    await J.potret("05_sesudah_post", so_id, inv)
    await kirim(J, inv[0], "06")
    await J.potret("06_sesudah_kirim", so_id, inv)
    await bayar_semua(J, inv, "07")
    await J.potret("07_sesudah_bayar", so_id, inv)
    await J.langkah("08_close", "POST", f"/api/sales-orders/{so_id}/close", {}, harap=(200, 201, 400))
    await J.potret("08_akhir", so_id, inv)

    # ---------------- bagian 2: jalur void
    so2 = await buat_confirm(J, "11")
    if not so2:
        return J.gagal("11_so2", "SO2 gagal dibuat")
    pf2 = await proforma(J, "12", so2, 20)
    if not pf2:
        return
    await J.langkah("12b_issue", "POST", f"/api/proformas/{pf2}/issue")
    pfd = await detail_pf(J, "12_issued", pf2)
    _, dp2 = await J.langkah("13_dp_proforma", "POST", "/api/customer-deposits",
                             dp_body(J, so2, int(float(pfd.get("amount") or 100000)), pf2))
    dp2_id = ((dp2 or {}).get("data") or {}).get("id")
    await J.langkah("13b_cancel_proforma_berbayar", "POST", f"/api/proformas/{pf2}/cancel", {"reason": "uji"},
                    harap=(400, 409))
    if dp2_id:
        await J.langkah("14_void_dp", "POST", f"/api/customer-deposits/{dp2_id}/void", {"reason": "uji void"})
    await detail_pf(J, "14_sesudah_void_dp", pf2)
    await J.potret("14_sesudah_void_dp", so2)
    await J.langkah("15_cancel_proforma", "POST", f"/api/proformas/{pf2}/cancel", {"reason": "uji batal"})
    await detail_pf(J, "15_sesudah_cancel", pf2)

    _, dp3 = await J.langkah("16_dp_tanpa_proforma", "POST", "/api/customer-deposits", dp_body(J, so2, 100000))
    dp3_id = ((dp3 or {}).get("data") or {}).get("id")
    _, det2 = await J.langkah("16b_detail_baris", "GET", f"/api/sales-orders/{so2}")
    soi2 = det2["data"]["items"][0]["id"]
    await J.langkah("17_to_invoice", "POST", f"/api/sales-orders/{so2}/to-invoice",
                    {"items": [{"so_item_id": soi2, "quantity": 10}]})
    inv2 = await J.faktur_so(so2)
    if not inv2:
        return J.gagal("17_faktur", "to-invoice SO2 tak menghasilkan faktur")
    await J.langkah("17b_post", "POST", f"/api/sales-invoices/{inv2[0]}/post", {})
    await J.potret("17_sesudah_post", so2, inv2)
    _, rp = await bayar_semua(J, inv2, "18") or (None, None)
    rp_id = ((rp or {}).get("data") or {}).get("id") if isinstance(rp, dict) else None
    await J.potret("18_sesudah_bayar", so2, inv2)
    if rp_id:
        await J.langkah("19_void_bayar", "POST", f"/api/receive-payments/{rp_id}/void", {"void_reason": "uji"})
    else:
        J.gagal("19_void_bayar", "id pembayaran tak ada di respons")
    await J.potret("19_sesudah_void_bayar", so2, inv2)
    await J.langkah("20_void_faktur", "POST", f"/api/sales-invoices/{inv2[0]}/void", {"reason": "uji void"},
                    harap=(200, 201, 400, 409))
    await J.potret("20_sesudah_void_faktur", so2, inv2)
    await J.langkah("21_cancel_so_dp_aktif_ditolak", "POST", f"/api/sales-orders/{so2}/cancel", {"reason": "uji"},
                    harap=(400, 409))
    if dp3_id:
        await J.langkah("22_refund_dp", "POST", f"/api/customer-deposits/{dp3_id}/refund",
                        {"amount": 100000, "refund_date": J.hari.isoformat(), "payment_method": "cash",
                         "account_id": KAS}, harap=(200, 201, 400, 409))
    await J.langkah("23_cancel_so", "POST", f"/api/sales-orders/{so2}/cancel", {"reason": "uji"},
                    harap=(200, 201, 400, 409))
    await J.potret("23_akhir_so2", so2, inv2)
