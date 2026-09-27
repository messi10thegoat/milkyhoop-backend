"""MENUNGGU KIRIM nyata (27 Sep 2026, BACKEND): GET /api/sales-orders/summary pending_shipment_value/_count dihitung
dari baris PERLU DIKIRIM (COALESCE(perlu_kirim, track_inventory)) bersisa qty, bukan status SO.
G (grapgrap, salinan): semua barang non-stok -> 0 / 0 padahal status confirmed ada (bukti dulu Rp 56 jt / 21).
P (kaos): SO1 = baris X (non-stok bisa_dikirim) + baris Y (non-stok biasa) -> +1 SO, +300000 (baris X saja, bukan
   total SO 600000). SO2 = baris Y saja -> ringkasan TAK berubah meski statusnya confirmed.
   Faktur SO1 + Surat Jalan baris X -> kembali ke keadaan awal."""
import uuid
from datetime import timedelta
from decimal import Decimal as D

from journey_lib import GUDANG, NAMA, PELANGGAN, T
from skenario_kirim_non_stok import _barang


async def _ringkas(J, nama):
    _, r = await J.langkah(f"{nama}_summary", "GET", "/api/sales-orders/summary")
    d = (r or {}).get("data") or {}
    n = d.get("pending_shipment_count")
    if n is None:   # kode lama: rumus kartu FE lama (confirmed + partial_shipped) supaya kontrol merah memerah DI LANGKAHNYA
        J.gagal(f"{nama}_count_ada", "pending_shipment_count tak ada -> rumus FE lama")
        n = (d.get("confirmed_count") or 0) + (d.get("partial_shipped_count") or 0)
    return D(str(d.get("pending_shipment_value", "NaN"))), n, d.get("confirmed_count")


async def _so(J, nama, baris):
    _, so = await J.langkah(f"{nama}_so", "POST", "/api/sales-orders", {
        "order_date": J.hari.isoformat(), "customer_id": PELANGGAN, "customer_name": NAMA,
        "expected_ship_date": (J.hari + timedelta(days=3)).isoformat(),
        "items": [{"item_id": pid, "description": f"TES E2E {nama}{i}", "quantity": "3", "unit_price": 100000}
                  for i, pid in enumerate(baris)]},
        headers={"X-Idempotency-Key": f"journey-{uuid.uuid4()}"})
    so_id = ((so or {}).get("data") or {}).get("id")
    if not so_id:
        J.gagal(f"{nama}_so_id", str(so)[:200])
        return None
    await J.langkah(f"{nama}_confirm", "POST", f"/api/sales-orders/{so_id}/confirm")
    return so_id


async def jalankan(J):
    # ---------- G: grapgrap (salinan) ----------
    K, A = J.mod("services.so_kirim"), J.mod("services.so_agregat")
    async with J.pool.acquire() as c:
        g = (await K.ringkasan_menunggu_kirim(c, "grapgrap-manado", A.AKTIF_TIDAK)
             if hasattr(K, "ringkasan_menunggu_kirim") else {"total": None, "count": None})
        lama = await c.fetchrow("""select count(*) n, coalesce(sum(total_amount),0) v from sales_orders
                                   where tenant_id='grapgrap-manado' and status in ('confirmed','partial_shipped')""")
    print("G grapgrap baru", g, "| definisi status lama", dict(lama))
    if not (g["total"] == 0 and g["count"] == 0):
        J.gagal("G_grapgrap_nol", f"harap 0/0: {g}")
    # ---------- P: kaos ----------
    v0, n0, c0 = await _ringkas(J, "P0")
    px, py = await _barang(J, "X", True), await _barang(J, "Y", False)
    if not (px and py):
        return
    so1 = await _so(J, "P1", [px, py])
    if not so1:
        return
    v1, n1, c1 = await _ringkas(J, "P1")
    if not (n1 == n0 + 1 and v1 == v0 + D("300000")):
        J.gagal("P1_hanya_baris_kirim", f"harap +1 SO / +300000: {n0}->{n1}, {v0}->{v1}")
    so2 = await _so(J, "P2", [py])
    v2, n2, c2 = await _ringkas(J, "P2")
    if not (so2 and c2 == c1 + 1 and n2 == n1 and v2 == v1):
        J.gagal("P2_nonstok_tak_menunggu", f"harap confirmed +1 tapi menunggu tetap: c {c1}->{c2}, n {n1}->{n2}, v {v1}->{v2}")
    # faktur + Surat Jalan baris X -> kembali
    _, det = await J.langkah("P3_detail", "GET", f"/api/sales-orders/{so1}")
    items = det["data"]["items"]
    await J.langkah("P3_to_invoice", "POST", f"/api/sales-orders/{so1}/to-invoice",
                    {"items": [{"so_item_id": b["id"], "quantity": 3} for b in items]})
    inv = (await J.faktur_so(so1))[0]
    await J.langkah("P3_post", "POST", f"/api/sales-invoices/{inv}/post", {})
    _, f = await J.langkah("P3_ringkasan_kirim", "GET", f"/api/sales-invoices/{inv}/fulfillments")
    rs = [s for s in (((f or {}).get("data") or {}).get("item_summary") or []) if s.get("requires_fulfillment")]
    if len(rs) != 1:
        return J.gagal("P3_satu_baris_kirim", f"item_summary perlu kirim: {rs}")
    await J.langkah("P3_kirim", "POST", f"/api/sales-invoices/{inv}/fulfill", {
        "warehouse_id": GUDANG, "recognize_revenue": True, "idempotency_key": f"journey-ff-{uuid.uuid4()}",
        "items": [{"invoice_item_id": rs[0]["id"], "quantity": rs[0]["quantity"]}]})
    v3, n3, _ = await _ringkas(J, "P3")
    if not (n3 == n0 and v3 == v0):
        J.gagal("P3_kembali_awal", f"harap {n0}/{v0}: {n3}/{v3}")
