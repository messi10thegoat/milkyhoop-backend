"""V318 Surat Jalan barang NON-STOK ber-flag bisa_dikirim (27 Sep 2026, BACKEND; pola NetSuite/SAP NLAG).
X (flag NYALA): barang non_inventory bisa_dikirim -> SO -> confirm -> to-invoice -> post (pendapatan diakui saat
   posting; status kirim 'pending'; SO BELUM selesai) -> SJ -> NOL jurnal & NOL inventory_ledger baru, pendapatan
   tak berubah, status kirim 'fulfilled', SO 'completed' (V315 + snapshot perlu_kirim), SJ memuat barisnya.
Y (flag MATI, kontrol perilaku lama): barang non_inventory biasa -> SO -> faktur -> post -> status 'not_applicable',
   SO langsung 'completed'; /fulfill baris itu = 409 FULFILL_LINE_NOT_SHIPPABLE, nol tulisan.
Z (snapshot): flag X dimatikan SESUDAH SO dibuat -> baris SO/faktur tetap perlu_kirim (dokumen tak berubah).
Barang dibuat lewat POST /api/items (lengan B, middleware nyata); lengan A lewat INSERT di salinan."""
import uuid
from datetime import timedelta
from decimal import Decimal as D

from journey_lib import GUDANG, NAMA, PELANGGAN, T


async def _barang(J, nama, flag):
    if J.lengan == "B":
        _, r = await J.langkah(f"{nama}_barang", "POST", "/api/items", {
            "name": f"TES E2E {nama} {uuid.uuid4().hex[:6]}", "item_type": "non_inventory", "track_inventory": False,
            "bisa_dikirim": flag, "base_unit": "pcs", "sales_price": 100000})
        pid = ((r or {}).get("data") or {}).get("id")
    else:
        async with J.pool.acquire() as c:
            pid = str(await c.fetchval(
                """INSERT INTO products (tenant_id, nama_produk, satuan, base_unit, item_type, track_inventory,
                                          bisa_dikirim, created_at, updated_at)
                   VALUES ($1, $2, 'pcs', 'pcs', 'non_inventory', false, $3, now(), now()) RETURNING id""",
                T, f"TES E2E {nama}", flag))
    if not pid:
        J.gagal(f"{nama}_barang_id", "barang tak dibuat")
    return pid


async def _hitung(J):
    async with J.pool.acquire() as c:
        return (await c.fetchval("select count(*) from journal_entries where tenant_id=$1", T),
                await c.fetchval("select count(*) from inventory_ledger where tenant_id=$1", T))


async def _so_faktur(J, nama, pid):
    _, so = await J.langkah(f"{nama}_so", "POST", "/api/sales-orders", {
        "order_date": J.hari.isoformat(), "customer_id": PELANGGAN, "customer_name": NAMA,
        "expected_ship_date": (J.hari + timedelta(days=3)).isoformat(),
        "items": [{"item_id": pid, "description": f"TES E2E {nama}", "quantity": "3", "unit_price": 100000}]},
        headers={"X-Idempotency-Key": f"journey-{uuid.uuid4()}"})
    so_id = ((so or {}).get("data") or {}).get("id")
    if not so_id:
        return J.gagal(f"{nama}_so_id", str(so)[:200]), None
    await J.langkah(f"{nama}_confirm", "POST", f"/api/sales-orders/{so_id}/confirm")
    _, det = await J.langkah(f"{nama}_detail", "GET", f"/api/sales-orders/{so_id}")
    baris = det["data"]["items"][0]
    await J.langkah(f"{nama}_to_invoice", "POST", f"/api/sales-orders/{so_id}/to-invoice",
                    {"items": [{"so_item_id": baris["id"], "quantity": 3}]})
    inv = (await J.faktur_so(so_id))[0]
    await J.langkah(f"{nama}_post", "POST", f"/api/sales-invoices/{inv}/post", {})
    return so_id, inv, baris


async def _keadaan(J, so_id, inv):
    async with J.pool.acquire() as c:
        return dict(await c.fetchrow(
            """select (select status from sales_orders where id=$1 and tenant_id=$3) so_status,
                      (select fulfillment_status from sales_invoices where id=$2 and tenant_id=$3) f_status,
                      (select coalesce(sum(recognized_amount),0) from sales_invoice_items where invoice_id=$2) diakui,
                      (select bool_and(perlu_kirim) from sales_invoice_items where invoice_id=$2) sii_pk,
                      (select bool_and(perlu_kirim) from sales_order_items where sales_order_id=$1) soi_pk""",
            uuid.UUID(so_id), uuid.UUID(inv), T))


async def _kirim(J, nama, inv, harap=(200, 201)):
    _, f = await J.langkah(f"{nama}_ringkasan_kirim", "GET", f"/api/sales-invoices/{inv}/fulfillments")
    rs = ((f or {}).get("data") or {}).get("item_summary") or []
    body = {"warehouse_id": GUDANG, "recognize_revenue": True, "idempotency_key": f"journey-ff-{uuid.uuid4()}",
            "items": [{"invoice_item_id": s["id"], "quantity": s["quantity"]} for s in rs]}
    return rs, await J.langkah(f"{nama}_kirim", "POST", f"/api/sales-invoices/{inv}/fulfill", body, harap=harap)


async def jalankan(J):
    # ---------- X: flag NYALA ----------
    px = await _barang(J, "X", True)
    if not px:
        return
    so, inv, baris = await _so_faktur(J, "X", px)
    if not so:
        return
    if baris.get("requires_fulfillment") is not True:
        J.gagal("X_detail_requires", f"detail SO requires_fulfillment={baris.get('requires_fulfillment')}")
    k0 = await _keadaan(J, so, inv)
    if not (k0["f_status"] == "pending" and k0["so_status"] != "completed" and k0["sii_pk"] and k0["soi_pk"]
            and D(str(k0["diakui"])) == D("300000")):
        J.gagal("X_sesudah_post", f"harap pending/belum selesai/diakui 300000/snapshot true: {k0}")
    # Z: matikan flag SESUDAH dokumen dibuat -> dokumen tak berubah
    async with J.pool.acquire() as c:
        await c.execute("update products set bisa_dikirim=false where id=$1 and tenant_id=$2", uuid.UUID(px), T)
    sebelum = await _hitung(J)
    rs, (_, kr) = await _kirim(J, "X", inv)
    if not rs or rs[0].get("requires_fulfillment") is not True or rs[0].get("tracks_inventory") is not False:
        J.gagal("X_ringkasan", f"item_summary: {rs}")
    sesudah = await _hitung(J)
    if sesudah != sebelum:
        J.gagal("X_nol_jurnal_stok", f"jurnal/ledger berubah {sebelum} -> {sesudah}")
    k1 = await _keadaan(J, so, inv)
    if not (k1["f_status"] == "fulfilled" and k1["so_status"] == "completed" and k1["diakui"] == k0["diakui"]):
        J.gagal("X_sesudah_kirim", f"harap fulfilled/completed/pendapatan tetap: {k1}")
    fid = ((kr or {}).get("data") or {}).get("fulfillment_id")
    if fid and J.lengan == "B":
        _, d = await J.langkah("X_sj_detail", "GET", f"/api/deliveries/{fid}")
        teks = str(d)
        if "TES E2E X" not in teks:
            J.gagal("X_sj_baris", f"SJ tak memuat baris non-stok: {teks[:300]}")
        _, pdf = await J.langkah("X_sj_pdf", "GET", f"/api/deliveries/{fid}/pdf")

    # ---------- Y: flag MATI (perilaku lama) ----------
    py = await _barang(J, "Y", False)
    so2, inv2, baris2 = await _so_faktur(J, "Y", py)
    if not so2:
        return
    k2 = await _keadaan(J, so2, inv2)
    if not (k2["f_status"] == "not_applicable" and k2["so_status"] == "completed" and k2["sii_pk"] is False):
        J.gagal("Y_perilaku_lama", f"harap not_applicable/completed/snapshot false: {k2}")
    sebelum = await _hitung(J)
    _, (_, ky) = await _kirim(J, "Y", inv2, harap=(400, 409))
    dy = (ky or {}).get("detail")
    kode = dy.get("code") if isinstance(dy, dict) else dy
    if (await _hitung(J)) != sebelum:
        J.gagal("Y_nol_tulisan", "penolakan menulis jurnal/ledger")
    J._catat("Y_kode", {"langkah": "Y_kode", "hasil": "PASS", "nyata": str(kode)[:200]})
    print(f"{J.no:02d} INFO  Y kirim ditolak: {str(kode)[:120]}")
