"""BUG-215 uji DB di SALINAN (bukan prod): handler ASLI ubah PO + riwayat PO atas klon, tenant kaos + nyawit. Mutasi HANYA di klon.
Ubah syarat bayar + tanggal kirim + alamat kirim pada PO Draf/Terkirim -> riwayat memuat 'syarat bayar' (Indonesia), TANPA nama kolom mentah (payment_terms_days, expected_date, ship_to_address)."""
import asyncio
import json
import sys
from datetime import date, timedelta

from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import purchase_orders as PO  # noqa: E402
from app.schemas.purchase_orders import UpdatePurchaseOrderRequest  # noqa: E402

HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid, uid):
    r = Request({"type": "http", "method": "PATCH", "path": "/x", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": str(uid), "username": "uji-salinan", "role": "OWNER"}
    return r


async def main():
    pool = await PO.get_pool()
    from backend.api_gateway.app.services import policy_engine_client as PEC  # singleton YANG SAMA dipakai middleware (impor MUTLAK; pola harness 199/207)
    PEC.init_policy_engine(pool)
    async with pool.acquire() as c:
        for tid in ("kaos-biru-konveksi", "pt-kita-nyawit-bersama"):
            uid = await c.fetchval("/* tenant_id */ SELECT utr.user_id FROM user_tenant_roles utr JOIN roles r ON r.id = utr.role_id WHERE utr.tenant_id = $1 AND utr.status = 'ACTIVE' AND r.code = 'OWNER' ORDER BY utr.is_primary DESC LIMIT 1", tid)
            po = await c.fetchrow("/* tenant_id */ SELECT id, po_number FROM purchase_orders WHERE tenant_id = $1 AND status IN ('draft', 'sent') ORDER BY created_at DESC LIMIT 1", tid)
            if uid is None or po is None:
                print(f"CATATAN {tid}: tak ada pemilik atau PO Draf/Terkirim di klon; dilewati", flush=True)
                continue
            rq = req(tid, uid)
            await PO.update_purchase_order(rq, po["id"], UpdatePurchaseOrderRequest(payment_terms_days=14, expected_date=date.today() + timedelta(days=5), ship_to_address="Alamat uji 215"))
            h = await PO.get_purchase_order_history(rq, po["id"], 200)
            teks = json.dumps(h, default=str, ensure_ascii=False)
            ubah = [x for x in (h["data"].get("items") or h["data"].get("events") or []) if "diubah" in json.dumps(x, default=str, ensure_ascii=False)] if isinstance(h.get("data"), dict) else []
            cek(f"{tid} {po['po_number']}: riwayat memuat 'syarat bayar' berlabel Indonesia", "syarat bayar" in teks, teks[:300])
            cek(f"{tid} {po['po_number']}: riwayat TANPA nama kolom mentah", not any(k in teks for k in ("payment_terms_days", "expected_date", "ship_to_address")), f"baris ubah={ubah[:1]}")
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
