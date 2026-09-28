"""Plafon proforma (celah 2) + PDF (celah 1), MASTER 28 Sep 2026, di salinan kaos lewat API nyata.
SO 5.000.000 + uang muka 2.000.000 TANPA proforma:
  - PELUNASAN 5 jt -> 400 menyebut komponen (dulu lolos = tagihan ganda); 3 jt -> lolos, terbit.
  - GET /sales-orders/{id}/proformas: billable_remaining 0 + billable_breakdown.
  - PDF PELUNASAN: 'Pelunasan yang Ditagih', 'Uang muka sudah diterima -Rp 2.000.000', 'Sisa setelah tagihan ini Rp 0',
    TANPA 'Pelunasan setelah'. Lalu SO kedua: DP 1 (30%) terbit + dibayar tanpa tautan, DP 2 (10%) -> PDF DP 2 menyebut
    pelunasan 3.000.000 (bukan 4.500.000 = total - DP ini)."""
import subprocess
import uuid
from datetime import timedelta

from journey_lib import NAMA, OWNER, PELANGGAN, T
from skenario_kirim_non_stok import _barang

KAS = "28ec7814-f10e-4688-af93-ef82f1b6f71f"


async def _so(J, nama, pid, total):
    _, r = await J.langkah(f"{nama}_so", "POST", "/api/sales-orders", {
        "order_date": J.hari.isoformat(), "customer_id": PELANGGAN, "customer_name": NAMA,
        "expected_ship_date": (J.hari + timedelta(days=3)).isoformat(),
        "items": [{"item_id": pid, "description": f"TES E2E plafon {nama}", "quantity": "1", "unit_price": total}]},
        headers={"X-Idempotency-Key": f"journey-{uuid.uuid4()}"})
    so = ((r or {}).get("data") or {}).get("id")
    await J.langkah(f"{nama}_confirm", "POST", f"/api/sales-orders/{so}/confirm")
    return so


async def _dp(J, nama, so, jumlah):
    await J.langkah(f"{nama}_dp", "POST", "/api/customer-deposits", {
        "customer_id": PELANGGAN, "customer_name": NAMA, "amount": jumlah, "deposit_date": J.hari.isoformat(),
        "payment_method": "cash", "account_id": KAS, "sales_order_id": so, "auto_post": True,
        "idempotency_key": f"journey-dp-{uuid.uuid4()}"})


async def _pdf_teks(J, nama, pf_id):
    r = await J.cl.get(f"/api/proformas/{pf_id}/pdf", headers=J._hdr(OWNER))
    if r.status_code != 200 or not r.content.startswith(b"%PDF"):
        J.gagal(f"{nama}_pdf", f"{r.status_code} {r.text[:150]}")
        return ""
    p = f"/out/{nama}.pdf"
    open(p, "wb").write(r.content)
    try:
        return subprocess.run(["pdftotext", "-raw", p, "-"], capture_output=True, text=True).stdout
    except FileNotFoundError:   # runner tanpa poppler -> teks dari berkas di host (lihat cek di laporan)
        return None


async def jalankan(J):
    pid = await _barang(J, "PLAFON", False)
    so = await _so(J, "A", pid, 5000000)
    await _dp(J, "A", so, 2000000)
    _, r = await J.langkah("A_pelunasan_5jt_ditolak", "POST", "/api/proformas",
                           {"sales_order_id": so, "purpose": "PELUNASAN", "amount": "5000000"}, harap=(201, 400))
    buat = ((r or {}).get("data") or {}).get("id")
    if buat:   # draf boleh dibuat? pagar juga di issue -> coba terbitkan harus 400
        _, ri = await J.langkah("A_pelunasan_5jt_issue_ditolak", "POST", f"/api/proformas/{buat}/issue", harap=(400,))
        pesan = str((ri or {}).get("detail"))
    else:
        pesan = str((r or {}).get("detail"))
    for bagian in ("uang muka diterima", "sisa yang bisa ditagih", "2.000.000", "3.000.000"):
        if bagian not in pesan:
            J.gagal("A_pesan_komponen", f"'{bagian}' tak ada: {pesan[:250]}")
    if buat:
        await J.langkah("A_batal_draf", "POST", f"/api/proformas/{buat}/cancel", {"reason": "TES E2E"}, harap=(200, 400))
    _, r = await J.langkah("A_pelunasan_3jt", "POST", "/api/proformas",
                           {"sales_order_id": so, "purpose": "PELUNASAN", "amount": "3000000"})
    pf = ((r or {}).get("data") or {}).get("id")
    await J.langkah("A_pelunasan_3jt_issue", "POST", f"/api/proformas/{pf}/issue")
    _, g = await J.langkah("A_daftar", "GET", f"/api/sales-orders/{so}/proformas")
    rb = (g or {}).get("billable_breakdown") or ((g or {}).get("data") or {}).get("billable_breakdown")
    br = (g or {}).get("billable_remaining", ((g or {}).get("data") or {}).get("billable_remaining"))
    if br != 0 or not rb or rb.get("received_total") != 2000000 or rb.get("issued_total") != 3000000 \
            or rb.get("received_not_billed") != 2000000:
        J.gagal("A_rincian", f"billable_remaining={br} breakdown={rb}")
    teks = await _pdf_teks(J, "A_pelunasan", pf)
    if teks is not None:
        for w in ("Pelunasan yang Ditagih", "Uang muka sudah diterima", "-Rp 2.000.000", "Sisa setelah tagihan ini"):
            if w not in teks:
                J.gagal("A_pdf", f"'{w}' tak ada di PDF")
        if "Pelunasan setelah" in teks:
            J.gagal("A_pdf_tanpa_baris_pelunasan_setelah", "baris muncul di PELUNASAN")
    # SO kedua: DP 30% dibayar tanpa tautan, lalu DP 10%
    so2 = await _so(J, "B", pid, 5000000)
    _, r = await J.langkah("B_dp1", "POST", "/api/proformas", {"sales_order_id": so2, "purpose": "DP", "percent_of_order": "30"})
    p1 = ((r or {}).get("data") or {}).get("id")
    await J.langkah("B_dp1_issue", "POST", f"/api/proformas/{p1}/issue")
    await _dp(J, "B", so2, 1500000)
    _, r = await J.langkah("B_dp2", "POST", "/api/proformas", {"sales_order_id": so2, "purpose": "DP", "percent_of_order": "10"})
    p2 = ((r or {}).get("data") or {}).get("id")
    await J.langkah("B_dp2_issue", "POST", f"/api/proformas/{p2}/issue")
    teks = await _pdf_teks(J, "B_dp2", p2)
    if teks is not None:
        if "3.000.000" not in teks or "4.500.000" in teks:
            J.gagal("B_pdf_pelunasan_sesudah", "harap pelunasan 3.000.000 (bukan 4.500.000)")
