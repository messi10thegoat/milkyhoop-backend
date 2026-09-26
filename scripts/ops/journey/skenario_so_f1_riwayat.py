"""Skenario F1 + Riwayat SO (26 Sep 2026, BACKEND3): short close & tolak pendapatan tertahan, dicek KODE badan,
lalu GET /history memuat kejadian dengan aktor.
SO-A: buat -> PATCH draf -> confirm -> to-invoice penuh -> post -> close = 400 SO_REVENUE_NOT_RECOGNIZED
      -> kirim -> close 200 -> riwayat (dibuat, diubah, dikonfirmasi, faktur dibuat/diposting, Surat Jalan, ditutup).
SO-B: buat -> confirm -> close tanpa alasan = 400 SO_NOT_FULLY_INVOICED -> close beralasan 200 (qty batal 10).
SO-C: buat -> confirm -> cancel beralasan -> riwayat memuat SALES_ORDER_CANCELLED beralasan."""
import uuid

from journey_lib import KOLAB
from skenario_so_penuh import kirim, so_body

DIKENAL = {}


def _kode(b):
    d = (b or {}).get("detail")
    return d.get("code") if isinstance(d, dict) else None


async def _riwayat(J, nama, so_id, wajib, user=None):
    kw = {"user": user} if user else {}
    _, r = await J.langkah(nama, "GET", f"/api/sales-orders/{so_id}/history", **kw)
    ev = ((r or {}).get("data") or {}).get("events") or []
    om = ((r or {}).get("data") or {}).get("omitted")
    if J.lengan == "A":
        # lengan A = app mini TANPA PolicyEngine -> izin dokumen terkait GAGAL TERTUTUP: semua modul di
        # omitted, hanya kejadian kolom SO yang tampil. Isi penuh dinilai di lengan B (middleware nyata).
        if om != ["customer_deposit", "proforma", "receive_payment", "sales_invoice", "sales_order"]:
            J.gagal(nama + "_tertutup", f"lengan A harus gagal tertutup, omitted={om}")
        wajib = [w for w in wajib if w in ("SO_DIBUAT", "SO_DIKONFIRMASI")]
    elif om != ([] if not user else om):
        J.gagal(nama + "_omitted", f"OWNER tak boleh kehilangan apa pun: {om}")
    ada = {e["jenis"] for e in ev}
    kurang = [w for w in wajib if w not in ada]
    if kurang:
        J.gagal(nama + "_isi", f"kejadian hilang {kurang}; ada {sorted(ada)}")
    tanpa_aktor = [e["jenis"] for e in ev if e["jenis"] in wajib and not e.get("aktor")]
    if tanpa_aktor:
        J.gagal(nama + "_aktor", f"kejadian baru tanpa aktor: {tanpa_aktor}")
    if any(not str(e.get("at", "")).endswith("+07:00") for e in ev):
        J.gagal(nama + "_wib", "waktu bukan zona tenant +07:00")
    return r


async def _buat_konfirmasi(J, awal):
    _, so = await J.langkah(f"{awal}_buat", "POST", "/api/sales-orders", so_body(J),
                            headers={"X-Idempotency-Key": f"journey-{uuid.uuid4()}"})
    so_id = ((so or {}).get("data") or {}).get("id")
    if not so_id:
        J.gagal(f"{awal}_buat_id", "SO gagal dibuat")
        return None
    return so_id


async def jalankan(J):
    # ---------- SO-A ----------
    a = await _buat_konfirmasi(J, "A01")
    if not a:
        return
    await J.langkah("A02_patch_draf", "PATCH", f"/api/sales-orders/{a}", {"notes": "journey f1"})
    await J.langkah("A03_confirm", "POST", f"/api/sales-orders/{a}/confirm")
    _, det = await J.langkah("A04_detail", "GET", f"/api/sales-orders/{a}")
    soi = det["data"]["items"][0]["id"]
    await J.langkah("A05_to_invoice_penuh", "POST", f"/api/sales-orders/{a}/to-invoice",
                    {"items": [{"so_item_id": soi, "quantity": 10}]})
    inv = await J.faktur_so(a)
    if not inv:
        return J.gagal("A05_faktur", "to-invoice tak menghasilkan faktur")
    await J.langkah("A06_post", "POST", f"/api/sales-invoices/{inv[0]}/post", {})
    _, b = await J.langkah("A07_close_belum_kirim_400", "POST", f"/api/sales-orders/{a}/close",
                           {"reason": "coba"}, harap=(400,))
    if _kode(b) != "SO_REVENUE_NOT_RECOGNIZED":
        J.gagal("A07_kode", f"harap SO_REVENUE_NOT_RECOGNIZED, dapat {str(b)[:200]}")
    await kirim(J, inv[0], "A08", kenal=None)
    await J.langkah("A09_close_sesudah_kirim", "POST", f"/api/sales-orders/{a}/close", {})
    await J.potret("A_akhir", a, inv)
    await _riwayat(J, "A10_riwayat", a, ["SO_DIBUAT", "SO_DIKONFIRMASI", "SALES_ORDER_UPDATED", "FAKTUR_DIBUAT",
                                          "FAKTUR_DITERBITKAN", "SURAT_JALAN_DIBUAT", "SALES_ORDER_CLOSED"])
    if J.lengan == "B":
        _, r = await J.langkah("A11_riwayat_kolaborator", "GET", f"/api/sales-orders/{a}/history", user=KOLAB)
        om = ((r or {}).get("data") or {}).get("omitted")
        if om != ["proforma"]:  # COLLABORATOR: R semua modul penjualan KECUALI PROFORMA (matriks peran)
            J.gagal("A11_omitted", f"kolaborator harap omitted [proforma], dapat {om}")
    await J.langkah("A12_riwayat_uuid_buruk_404", "GET", "/api/sales-orders/bukan-uuid/history", harap=(404,))
    await J.langkah("A13_riwayat_tak_ada_404", "GET", f"/api/sales-orders/{uuid.uuid4()}/history", harap=(404,))

    # ---------- SO-B ----------
    s = await _buat_konfirmasi(J, "B01")
    if s:
        await J.langkah("B02_confirm", "POST", f"/api/sales-orders/{s}/confirm")
        _, b = await J.langkah("B03_close_tanpa_alasan_400", "POST", f"/api/sales-orders/{s}/close", {}, harap=(400,))
        if _kode(b) != "SO_NOT_FULLY_INVOICED":
            J.gagal("B03_kode", f"harap SO_NOT_FULLY_INVOICED, dapat {str(b)[:200]}")
        _, b = await J.langkah("B04_short_close", "POST", f"/api/sales-orders/{s}/close",
                               {"reason": "pelanggan batal sisa"})
        baris = ((b or {}).get("data") or {}).get("cancelled_lines") or []
        if [x.get("quantity_cancelled") for x in baris] != [10]:
            J.gagal("B04_baris", f"cancelled_lines {baris}")
        await _riwayat(J, "B05_riwayat", s, ["SO_DIBUAT", "SO_DIKONFIRMASI", "SALES_ORDER_FORCE_CLOSED"])

    # ---------- SO-C ----------
    c = await _buat_konfirmasi(J, "C01")
    if c:
        await J.langkah("C02_confirm", "POST", f"/api/sales-orders/{c}/confirm")
        await J.langkah("C03_cancel", "POST", f"/api/sales-orders/{c}/cancel", {"reason": "salah input"})
        await J.langkah("C04_cancel_ulang_ditolak", "POST", f"/api/sales-orders/{c}/cancel", {"reason": "x"},
                        harap=(400, 409))
        r = await _riwayat(J, "C05_riwayat", c, ["SO_DIBUAT", "SALES_ORDER_CANCELLED"])
        ev = ((r or {}).get("data") or {}).get("events") or []
        if J.lengan == "B" and not any(e["jenis"] == "SALES_ORDER_CANCELLED" and "salah input" in e["ringkas"] for e in ev):
            J.gagal("C05_alasan", "alasan batal tak tampil di riwayat")
