"""Skenario F3 termin jatuh tempo (26 Sep 2026, BACKEND3): to-invoice TANPA due_date memakai termin.
X: SO "NET 30" -> faktur due_date = invoice_date + 30, sumber so_terms (dicek di respons DAN di detail faktur).
Y: SO "DP 60% di muka ..." -> jatuh ke termin pelanggan (salinan prod; nilai pelanggan dibaca apa adanya)
   -> sumber customer_terms bila >0, selain itu default (= invoice_date).
Z: SO "NET 30" + due_date di body -> body menang."""
import uuid
from datetime import date, timedelta

from skenario_so_penuh import so_body

DIKENAL = {}


async def _faktur(J, awal, termin, body_inv):
    b = so_body(J)
    b["payment_terms"] = termin
    _, so = await J.langkah(f"{awal}_buat", "POST", "/api/sales-orders", b,
                            headers={"X-Idempotency-Key": f"journey-{uuid.uuid4()}"})
    so_id = ((so or {}).get("data") or {}).get("id")
    if not so_id:
        J.gagal(f"{awal}_buat_id", "SO gagal dibuat")
        return None, None
    await J.langkah(f"{awal}_confirm", "POST", f"/api/sales-orders/{so_id}/confirm")
    _, det = await J.langkah(f"{awal}_detail", "GET", f"/api/sales-orders/{so_id}")
    soi = det["data"]["items"][0]["id"]
    _, r = await J.langkah(f"{awal}_to_invoice", "POST", f"/api/sales-orders/{so_id}/to-invoice",
                           {**body_inv, "items": [{"so_item_id": soi, "quantity": 10}]})
    d = (r or {}).get("data") or {}
    _, si = await J.langkah(f"{awal}_detail_faktur", "GET", f"/api/sales-invoices/{d.get('invoice_id')}")
    s = (si or {}).get("data") or {}
    return d, s


def _tgl(x):
    return date.fromisoformat(str(x)[:10])


async def jalankan(J):
    d, s = await _faktur(J, "X", "NET 30", {"invoice_date": J.hari.isoformat()})
    if d is not None:
        harap = J.hari + timedelta(days=30)
        if d.get("due_date_source") != "so_terms" or _tgl(d.get("due_date")) != harap:
            J.gagal("X_respons", f"harap so_terms {harap}, dapat {d}")
        if _tgl(s.get("due_date")) != harap:
            J.gagal("X_tersimpan", f"faktur tersimpan due_date {s.get('due_date')} != {harap}")

    async with J.pool.acquire() as c:  # salinan TERISOLASI (pagar harness), baca saja
        hari_pel = await c.fetchval("SELECT payment_terms_days FROM customers WHERE id = $1::uuid", PELANGGAN_ID)
    d, s = await _faktur(J, "Y", "DP 60% di muka, pelunasan sebelum pengiriman", {"invoice_date": J.hari.isoformat()})
    if d is not None:
        if hari_pel and hari_pel > 0:
            harap, sumber = J.hari + timedelta(days=hari_pel), "customer_terms"
        else:
            harap, sumber = J.hari, "default"
        if d.get("due_date_source") != sumber or _tgl(s.get("due_date")) != harap:
            J.gagal("Y_termin_pelanggan", f"pelanggan {hari_pel} hari: harap {sumber} {harap}, dapat {d} / {s.get('due_date')}")

    isian = J.hari + timedelta(days=5)
    d, s = await _faktur(J, "Z", "NET 30", {"invoice_date": J.hari.isoformat(), "due_date": isian.isoformat()})
    if d is not None and (d.get("due_date_source") != "body" or _tgl(s.get("due_date")) != isian):
        J.gagal("Z_body_menang", f"harap body {isian}, dapat {d} / {s.get('due_date')}")


from journey_lib import PELANGGAN as PELANGGAN_ID  # noqa: E402
