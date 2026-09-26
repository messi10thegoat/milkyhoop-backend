"""Skenario 1 — perjalanan SO penuh (26 Sep 2026, urutan dari WORKSPACE/CW):
buat SO (+ulang kunci sama / isi beda 409) -> confirm (+kolaborator, non-draft, PATCH/DELETE ditolak) -> DP (+409)
-> to-invoice 4 (draf) -> post -> kirim -> to-invoice sisa -> post -> kirim -> open-invoices -> terima bayar -> close;
SO2 hapus draf; SO3 confirm -> cancel."""
import json
import uuid
from datetime import timedelta
from decimal import Decimal as D

from journey_lib import BARANG, GUDANG, KAS, KOLAB, NAMA, PELANGGAN

DIKENAL = {"fulfill_partial_paid": "fulfill guard status=posted saja (BACKEND sedang memperbaiki, 26 Sep)"}


def so_body(J, qty="10"):
    return {"order_date": J.hari.isoformat(), "customer_id": PELANGGAN, "customer_name": NAMA,
            "expected_ship_date": (J.hari + timedelta(days=3)).isoformat(),
            "items": [{"item_id": BARANG, "description": "Cotton Combet 24s (journey)", "quantity": qty,
                       "unit_price": 50000, "warehouse_id": GUDANG}]}


async def kirim(J, inv_id, awal, kenal="fulfill_partial_paid"):
    await J.langkah(f"{awal}_fulfillments_sebelum", "GET", f"/api/sales-invoices/{inv_id}/fulfillments")
    _, si = await J.langkah(f"{awal}_si_untuk_baris", "GET", f"/api/sales-invoices/{inv_id}")
    items = ((si or {}).get("data") or {}).get("items") or []
    if not items:
        return J.gagal(f"{awal}_fulfill", "faktur tanpa baris")
    body = {"warehouse_id": GUDANG, "recognize_revenue": True, "idempotency_key": f"journey-ff-{uuid.uuid4()}",
            "items": [{"invoice_item_id": str(b["id"]), "quantity": float(b.get("quantity") or b.get("qty"))}
                      for b in items]}
    return await J.langkah(f"{awal}_fulfill", "POST", f"/api/sales-invoices/{inv_id}/fulfill", body, kenal=kenal)


async def bayar_semua(J, inv, nama):
    _, oi = await J.langkah(f"{nama}_open_invoices", "GET", f"/api/customers/{PELANGGAN}/open-invoices")
    semua = (oi or {}).get("invoices") or ((oi or {}).get("data") or {}).get("invoices") or []
    alok = [{"invoice_id": str(x["id"]), "amount_applied": str(x["remaining_amount"])}
            for x in semua if str(x.get("id")) in inv and D(str(x.get("remaining_amount") or 0)) > 0]
    total = sum((D(a["amount_applied"]) for a in alok), D(0))
    if total <= 0:
        return J.gagal(f"{nama}_terima_bayar", f"open-invoices tak memuat faktur journey: {str(oi)[:200]}")
    return await J.langkah(f"{nama}_terima_bayar", "POST", "/api/receive-payments",
                           {"customer_id": PELANGGAN, "payment_date": J.hari.isoformat(), "payment_method": "cash",
                            "bank_account_id": KAS, "total_amount": str(total), "allocations": alok},
                           headers={"X-Idempotency-Key": f"journey-rp-{uuid.uuid4()}"})


async def jalankan(J):
    await J.langkah("tenant_today", "GET", "/api/tenant/today")
    body = so_body(J)
    kunci = f"journey-{J.lengan}-{uuid.uuid4()}"
    _, so = await J.langkah("01_buat_so", "POST", "/api/sales-orders", body, headers={"X-Idempotency-Key": kunci})
    so_id = ((so or {}).get("data") or {}).get("id")
    if not so_id:
        return J.gagal("01_buat_so_id", "SO gagal dibuat")
    await J.langkah("01b_ulang_kunci_sama_isi_sama", "POST", "/api/sales-orders", body,
                    headers={"X-Idempotency-Key": kunci})
    beda = json.loads(json.dumps(body))
    beda["items"][0]["quantity"] = "11"
    await J.langkah("01c_kunci_sama_isi_beda_409", "POST", "/api/sales-orders", beda,
                    headers={"X-Idempotency-Key": kunci}, harap=(409,))
    await J.potret("01_sesudah_buat", so_id)
    await J.langkah("02_patch_draft", "PATCH", f"/api/sales-orders/{so_id}", {"notes": "journey"})
    # A: tanpa authz -> 200; B: middleware izin nyata -> 403
    await J.langkah("02b_confirm_kolaborator", "POST", f"/api/sales-orders/{so_id}/confirm", user=KOLAB,
                    harap=(403,) if J.lengan == "B" else (200,))
    await J.langkah("02c_confirm", "POST", f"/api/sales-orders/{so_id}/confirm",
                    harap=(200,) if J.lengan == "B" else (400,))
    await J.langkah("02d_confirm_non_draft_400", "POST", f"/api/sales-orders/{so_id}/confirm", harap=(400, 409))
    await J.langkah("02e_patch_confirmed_400", "PATCH", f"/api/sales-orders/{so_id}", {"notes": "x"}, harap=(400,))
    await J.langkah("02f_delete_non_draft_400", "DELETE", f"/api/sales-orders/{so_id}", harap=(400, 409))
    await J.potret("02_sesudah_confirm", so_id)

    dp = {"customer_id": PELANGGAN, "customer_name": NAMA, "amount": 100000, "deposit_date": J.hari.isoformat(),
          "payment_method": "cash", "account_id": KAS, "sales_order_id": so_id, "auto_post": True,
          "idempotency_key": f"journey-dp-{uuid.uuid4()}"}
    await J.langkah("03_dp", "POST", "/api/customer-deposits", dp)
    await J.langkah("03b_dp_ulang_isi_sama", "POST", "/api/customer-deposits", dp)
    await J.langkah("03c_dp_isi_beda_409", "POST", "/api/customer-deposits", {**dp, "amount": 200000}, harap=(409,))
    await J.langkah("03d_cancel_dp_aktif_400", "POST", f"/api/sales-orders/{so_id}/cancel", {"reason": "uji"},
                    harap=(400, 409))
    await J.potret("03_sesudah_dp", so_id)

    _, det = await J.langkah("04_detail_baris", "GET", f"/api/sales-orders/{so_id}")
    soi = det["data"]["items"][0]["id"]
    await J.langkah("04_to_invoice_4", "POST", f"/api/sales-orders/{so_id}/to-invoice",
                    {"items": [{"so_item_id": soi, "quantity": 4}]})
    inv1 = await J.faktur_so(so_id)
    if not inv1:
        return J.gagal("04_faktur_1", "to-invoice tak menghasilkan faktur")
    await J.potret("04_sesudah_to_invoice_draf", so_id, inv1)
    # FE: rencana DP yang diterapkan saat posting (faktur masih DRAF) -- dulu fixture FE disintesis
    await J.langkah("04a_deposit_plan_faktur_1_draf", "GET", f"/api/sales-invoices/{inv1[0]}/deposit-plan")
    await J.langkah("04b_post_faktur_1", "POST", f"/api/sales-invoices/{inv1[0]}/post", {})
    await J.potret("04_sesudah_post", so_id, inv1)
    await kirim(J, inv1[0], "05")
    await J.potret("05_sesudah_kirim_1", so_id, inv1)

    await J.langkah("06_to_invoice_sisa", "POST", f"/api/sales-orders/{so_id}/to-invoice",
                    {"items": [{"so_item_id": soi, "quantity": 6}]})
    inv2 = await J.faktur_so(so_id, inv1)
    if not inv2:
        return J.gagal("06_faktur_2", "to-invoice sisa tak menghasilkan faktur")
    inv = inv1 + inv2
    await J.potret("06a_sesudah_to_invoice_sisa_draf", so_id, inv)
    await J.langkah("06a_deposit_plan_faktur_2_draf", "GET", f"/api/sales-invoices/{inv2[0]}/deposit-plan")
    await J.langkah("06b_post_faktur_2", "POST", f"/api/sales-invoices/{inv2[0]}/post", {})
    await J.potret("06b_sesudah_post_faktur_2_sebelum_kirim", so_id, inv)
    await kirim(J, inv2[0], "06c")
    await J.potret("06_sesudah_sisa", so_id, inv)
    await bayar_semua(J, inv, "07")
    await J.potret("07_sesudah_bayar", so_id, inv)
    await J.langkah("08_close", "POST", f"/api/sales-orders/{so_id}/close", {}, harap=(200, 201, 400))
    await J.potret("08_akhir", so_id, inv)

    _, so2 = await J.langkah("09_so2_buat", "POST", "/api/sales-orders", body,
                             headers={"X-Idempotency-Key": f"journey-{uuid.uuid4()}"})
    so2_id = ((so2 or {}).get("data") or {}).get("id")
    if so2_id:
        await J.langkah("09b_so2_delete_draft", "DELETE", f"/api/sales-orders/{so2_id}")
    _, so3 = await J.langkah("10_so3_buat", "POST", "/api/sales-orders", body,
                             headers={"X-Idempotency-Key": f"journey-{uuid.uuid4()}"})
    so3_id = ((so3 or {}).get("data") or {}).get("id")
    if so3_id:
        await J.langkah("10b_so3_confirm", "POST", f"/api/sales-orders/{so3_id}/confirm")
        await J.langkah("10c_so3_cancel_tanpa_dp", "POST", f"/api/sales-orders/{so3_id}/cancel", {"reason": "uji"})
        await J.potret("10_so3_sesudah_cancel", so3_id)
