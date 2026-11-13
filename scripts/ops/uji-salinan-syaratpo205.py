"""BUG-205 uji DB di SALINAN (bukan prod; V496 diterapkan DI SALINAN oleh .sh): handler ASLI /purchase-orders (buat, ubah, kirim, terima, to-bill/preview, detail) + PDF PO, tenant kaos + nyawit.
1 buat tanpa termin: snapshot = termin vendor saat itu (45); isian 0 = Tunai, 7 = 7; tanpa vendor = NULL; PO LAMA = NULL · 2 termin vendor diubah ke 60 SESUDAH PO dibuat: snapshot tetap 45 (terkunci)
· 3 PATCH di Draf dan Terkirim berhasil (edit/preview perubahan before/after), sesudah diterima: blok PO_TAK_BISA_DIUBAH · 4 detail memuat payment_terms_days + syarat_bayar_text; PDF mencetak
"Syarat bayar" dari snapshot, PO lama (NULL) tidak · 5 to-bill/preview: sumber po_terms, jatuh tempo = tanggal tagihan + termin PO; PO lama NULL tetap vendor_terms (tak berubah) · 6 CHECK menolak 400 ·
7 ROLLBACK lalu V496 ulang idempoten."""
import asyncio
import re
import sys
import uuid
from datetime import date, timedelta
from decimal import Decimal as D
from pathlib import Path

from fastapi import HTTPException
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import purchase_orders as PO  # noqa: E402
from app.schemas import purchase_orders as SP  # noqa: E402
from app.services.pdf_service import PDFService  # noqa: E402
from app.services.po_cetak import muat_pdf_po  # noqa: E402
from app.utils.tanggal_tenant import tanggal_dokumen  # noqa: E402

NY, KA = "pt-kita-nyawit-bersama", "kaos-biru-konveksi"
MIG = Path("/app/backend/migrations")
HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid, uid):
    r = Request({"type": "http", "method": "POST", "path": "/x", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": str(uid), "username": "uji-salinan", "role": "OWNER"}
    return r


def d(x):
    return x.model_dump() if hasattr(x, "model_dump") else x


async def galat(coro):
    try:
        return d(await coro)
    except HTTPException as e:
        return e


def teks(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"<style.*?</style>", "", html, flags=re.S))).strip()


async def main():
    pool = await PO.get_pool()
    ps = PDFService()
    async with pool.acquire() as c:
        for tid in (KA, NY):
            uid = await c.fetchval("SELECT utr.user_id FROM user_tenant_roles utr JOIN roles r ON r.id = utr.role_id WHERE utr.tenant_id = $1 AND utr.status = 'ACTIVE' AND r.code = 'OWNER' ORDER BY utr.is_primary DESC LIMIT 1", tid)
            prod = await c.fetchrow("SELECT id, kode_produk FROM products WHERE tenant_id = $1 AND item_type = 'goods' AND track_inventory ORDER BY kode_produk LIMIT 1", tid)
            vendor = await c.fetchrow("SELECT id, name FROM vendors WHERE tenant_id = $1 ORDER BY name LIMIT 1", tid)
            gudang = await c.fetchval("SELECT id FROM warehouses WHERE tenant_id = $1 AND is_active ORDER BY is_default DESC NULLS LAST, created_at LIMIT 1", tid)
            cek(f"prasyarat {tid}: pemilik, barang ber-stok, vendor, gudang", all(x is not None for x in (uid, prod, vendor, gudang)))
            if any(x is None for x in (uid, prod, vendor, gudang)):
                continue
            await c.execute("INSERT INTO tenant_features (tenant_id, feature, enabled) VALUES ($1, 'penerimaan_barang_v1', true) ON CONFLICT (tenant_id, feature) DO UPDATE SET enabled = true", tid)
            hari = await tanggal_dokumen(c, tid)
            rq = req(tid, uid)
            lama = await c.fetchrow("SELECT id FROM purchase_orders WHERE tenant_id = $1 AND payment_terms_days IS NULL AND vendor_id IS NOT NULL ORDER BY created_at LIMIT 1", tid)
            cek(f"1 {tid} PO LAMA (sebelum V496): payment_terms_days NULL, tanpa backfill", lama is not None and await c.fetchval("SELECT count(*) FROM purchase_orders WHERE tenant_id = $1 AND payment_terms_days IS NOT NULL", tid) == 0)
            await c.execute("UPDATE vendors SET payment_terms_days = 45 WHERE tenant_id = $1 AND id = $2", tid, vendor["id"])

            async def buat(**k):
                body = SP.CreatePurchaseOrderRequest(vendor_id=str(vendor["id"]) if k.pop("pakai_vendor", True) else None, vendor_name=vendor["name"], po_date=hari, ref_no="UJI205", notes="UJI205",
                                                     items=[{"item_id": str(prod["id"]), "item_code": prod["kode_produk"], "description": "UJI205 barang", "quantity": D("10"), "unit_price": D("1000")}], **k)
                r = d(await PO.create_purchase_order(rq, body))
                return uuid.UUID(str(r["data"]["id"]))
            snap = lambda pid: c.fetchval("SELECT payment_terms_days FROM purchase_orders WHERE tenant_id = $1 AND id = $2", tid, pid)
            p1 = await buat()
            p0, p7, pn = await buat(payment_terms_days=0), await buat(payment_terms_days=7), await buat(pakai_vendor=False)
            cek(f"1 {tid} buat tanpa termin: snapshot = termin vendor saat itu (45); isian 0 = 0, 7 = 7; tanpa vendor = NULL", (await snap(p1), await snap(p0), await snap(p7), await snap(pn)) == (45, 0, 7, None),
                str((await snap(p1), await snap(p0), await snap(p7), await snap(pn))))
            await c.execute("UPDATE vendors SET payment_terms_days = 60 WHERE tenant_id = $1 AND id = $2", tid, vendor["id"])
            cek(f"2 {tid} termin vendor diubah ke 60 sesudah PO dibuat: snapshot PO tetap 45 (terkunci per dokumen)", await snap(p1) == 45)
            # 3 ubah
            pe = d(await PO.preview_edit_purchase_order(rq, p1, SP.UpdatePurchaseOrderRequest(payment_terms_days=14)))
            pe = pe.get("data", pe)
            perub = {x["field"]: x for x in pe["perubahan"]}
            cek(f"3 {tid} edit/preview: perubahan payment_terms_days 45 -> 14, nol tulis", "payment_terms_days" in perub and perub["payment_terms_days"]["before"] == 45 and perub["payment_terms_days"]["after"] == 14 and await snap(p1) == 45, str(pe["perubahan"]))
            g = await galat(PO.update_purchase_order(rq, p1, SP.UpdatePurchaseOrderRequest(payment_terms_days=14)))
            cek(f"3 {tid} PATCH di Draf: berhasil, snapshot 14", not isinstance(g, HTTPException) and await snap(p1) == 14, str(g))
            await PO.send_purchase_order(rq, p1)
            g = await galat(PO.update_purchase_order(rq, p1, SP.UpdatePurchaseOrderRequest(payment_terms_days=21)))
            cek(f"3 {tid} PATCH di Terkirim: berhasil, snapshot 21", not isinstance(g, HTTPException) and await snap(p1) == 21, str(g))
            item = await c.fetchval("SELECT i.id FROM purchase_order_items i JOIN purchase_orders p ON p.id = i.po_id AND p.tenant_id = $2 WHERE i.po_id = $1 ORDER BY i.line_number LIMIT 1", p1, tid)
            await PO.receive_goods(rq, p1, SP.ReceiveGoodsRequest(receive_date=hari, warehouse_id=gudang, items=[SP.ReceiveItemRequest(po_item_id=str(item), quantity_received=D("10"))]))
            g = await galat(PO.update_purchase_order(rq, p1, SP.UpdatePurchaseOrderRequest(payment_terms_days=5)))
            cek(f"3 {tid} PATCH sesudah diterima: ditolak (PO_TAK_BISA_DIUBAH), snapshot tetap 21", isinstance(g, HTTPException) and await snap(p1) == 21, f"{getattr(g, 'status_code', g)} {getattr(g, 'detail', '')}")
            # 4 detail + PDF
            det = d(await PO.get_purchase_order(rq, p1))["data"]
            cek(f"4 {tid} detail: payment_terms_days 21 + syarat_bayar_text '21 hari'", det["payment_terms_days"] == 21 and det["syarat_bayar_text"] == "21 hari", f"{det.get('payment_terms_days')} {det.get('syarat_bayar_text')}")
            m = await muat_pdf_po(c, {"tenant_id": tid}, p1)
            t1 = teks(ps.render_purchase_order(m["po_data"], m["tenant_info"]).html)
            ml = await muat_pdf_po(c, {"tenant_id": tid}, lama["id"])
            tl = teks(ps.render_purchase_order(ml["po_data"], ml["tenant_info"]).html)
            m0 = await muat_pdf_po(c, {"tenant_id": tid}, p0)
            t0 = teks(ps.render_purchase_order(m0["po_data"], m0["tenant_info"]).html)
            cek(f"4 {tid} PDF: snapshot 21 -> 'Syarat bayar 21 hari' (bukan vendor 60); 0 -> 'Tunai'; PO lama NULL -> tanpa baris", "Syarat bayar 21 hari" in t1 and "60 hari" not in t1 and "Syarat bayar Tunai" in t0 and "Syarat bayar" not in tl)
            # 5 to-bill/preview
            tgl = hari + timedelta(days=2)
            pv = d(await PO.preview_convert_to_bill(rq, p1, SP.ToBillPreviewRequest(bill_date=tgl)))
            pv = pv.get("data", pv)
            cek(f"5 {tid} to-bill/preview PO bertermin: sumber po_terms, termin 21, jatuh tempo = tanggal tagihan + 21", pv["due_date_source"] == "po_terms" and pv["payment_terms_days"] == 21 and pv["due_date"] == (tgl + timedelta(days=21)).isoformat(), f"{pv.get('due_date_source')} {pv.get('payment_terms_days')} {pv.get('due_date')}")
            await c.execute("UPDATE purchase_orders SET status = 'received' WHERE tenant_id = $1 AND id = $2 AND status = 'draft'", tid, lama["id"])
            pvl = d(await PO.preview_convert_to_bill(rq, lama["id"], SP.ToBillPreviewRequest(bill_date=tgl)))
            pvl = pvl.get("data", pvl)
            n_lama = await c.fetchval("SELECT v.payment_terms_days FROM purchase_orders p JOIN vendors v ON v.id = p.vendor_id AND v.tenant_id = p.tenant_id WHERE p.tenant_id = $1 AND p.id = $2", tid, lama["id"])
            harap_sumber, harap_n = ("vendor_terms", int(n_lama)) if n_lama and 0 < int(n_lama) <= 365 else ("default", 0)
            cek(f"5 {tid} to-bill/preview PO LAMA (NULL): sumber {harap_sumber} dari termin vendor {n_lama} (perilaku lama tak berubah)", pvl["due_date_source"] == harap_sumber and pvl["payment_terms_days"] == harap_n
                and pvl["due_date"] == (tgl + timedelta(days=harap_n)).isoformat(), f"{pvl.get('due_date_source')} {pvl.get('payment_terms_days')} {pvl.get('due_date')}")
            # 6 CHECK
            try:
                async with c.transaction():
                    await c.execute("UPDATE purchase_orders SET payment_terms_days = 400 WHERE tenant_id = $1 AND id = $2", tid, p1)
                cek(f"6 {tid} CHECK menolak 400", False, "400 diterima")
            except Exception as e:  # noqa: BLE001
                cek(f"6 {tid} CHECK menolak 400 (chk_po_payment_terms_days)", "chk_po_payment_terms_days" in str(e), str(e)[:150])
        # 7 rollback + ulang (satu kali)
        await c.execute((MIG / "V496__po_payment_terms_days_ROLLBACK.sql").read_text())
        ada = await c.fetchval("SELECT count(*) FROM information_schema.columns WHERE table_name = 'purchase_orders' AND column_name = 'payment_terms_days' /* skema, bukan data tenant: tenant_id tak berlaku */")
        await c.execute((MIG / "V496__po_payment_terms_days.sql").read_text())
        await c.execute((MIG / "V496__po_payment_terms_days.sql").read_text())      # idempoten (dua kali)
        ada2 = await c.fetchval("SELECT count(*) FROM information_schema.columns WHERE table_name = 'purchase_orders' AND column_name = 'payment_terms_days' /* skema, bukan data tenant: tenant_id tak berlaku */")
        cek("7 ROLLBACK membuang kolom; V496 diterapkan ulang (dua kali, idempoten) memulihkannya", ada == 0 and ada2 == 1, f"{ada} {ada2}")
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
