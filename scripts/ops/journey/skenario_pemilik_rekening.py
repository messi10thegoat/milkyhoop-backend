"""Nama pemilik rekening (V329, 28 Sep 2026) lewat app PENUH (lengan B) di salinan kaos.
Data yang dikirim RUTE NYATA ke pdf_service ditangkap (PDF tetap dirender sungguhan):
  sebelum diisi: PDF faktur/proforma/penawaran bernomor 1111222233 (snapshot 'BCA Operasional') -> a.n. TIDAK dicetak
  (kode lama: 'Operasional'); PATCH Kas & Bank account_holder_name -> detail/daftar/dropdown membawanya -> ketiga PDF +
  WA dashboard mencetak 'a.n. TES E2E Pemilik Kaos'; SO baru dengan pemilik 'BCA Operasional' tersnapshot pemilik
  sebenarnya; dikosongkan lagi -> NULL -> a.n. hilang."""
import uuid
from datetime import timedelta

from journey_lib import NAMA, PELANGGAN, T
from skenario_kirim_non_stok import _barang

NOMOR = "1111222233"
PEMILIK = "TES E2E Pemilik Kaos"
TANGKAP = {}


def _pasang(J):
    svc = J.mod("services.pdf_service").get_pdf_service()
    for nama in ("generate_sales_invoice_pdf", "generate_proforma_pdf", "generate_quote_pdf"):
        asli = getattr(svc, nama)

        def bungkus(data, *a, _asli=asli, _nama=nama, **kw):
            TANGKAP[_nama] = dict(data)
            return _asli(data, *a, **kw)
        setattr(svc, nama, bungkus)


async def _pdf(J, nama, jalur, kunci):
    TANGKAP.pop(kunci, None)
    r = await J.cl.get(jalur, headers=J._hdr(J.owner))
    if r.status_code != 200 or not r.content.startswith(b"%PDF") or kunci not in TANGKAP:
        J.gagal(f"{nama}_pdf", f"{r.status_code} {r.text[:150]} tangkap={kunci in TANGKAP}")
        return "GAGAL"
    return TANGKAP[kunci].get("rekening_pemilik_cetak")


async def _semua_pdf(J, label, dok):
    hasil = {"faktur": await _pdf(J, f"{label}_faktur", f"/api/sales-invoices/{dok['si']}/pdf?format=inline",
                                  "generate_sales_invoice_pdf")}
    if dok.get("pf"):
        hasil["proforma"] = await _pdf(J, f"{label}_proforma", f"/api/proformas/{dok['pf']}/pdf", "generate_proforma_pdf")
    if dok.get("q"):
        hasil["penawaran"] = await _pdf(J, f"{label}_penawaran", f"/api/quotes/{dok['q']}/pdf", "generate_quote_pdf")
    print(label, hasil, flush=True)
    return hasil


async def jalankan(J):
    if J.lengan == "A":
        print("lengan A: dilewati (butuh router Kas & Bank + PDF penuh)")
        return
    from journey_lib import OWNER
    J.owner = OWNER
    _pasang(J)
    async with J.pool.acquire() as c:
        ba = await c.fetchval("SELECT id FROM bank_accounts WHERE tenant_id=$1 AND account_number=$2 AND is_active", T, NOMOR)
        dok = {
            "si": await c.fetchval("SELECT id FROM sales_invoices WHERE tenant_id=$1 AND payment_account_number=$2 "
                                   "AND payment_account_holder='BCA Operasional' ORDER BY created_at DESC LIMIT 1", T, NOMOR),
            "pf": await c.fetchval("SELECT id FROM proformas WHERE tenant_id=$1 AND payment_account_number=$2 "
                                   "ORDER BY created_at DESC LIMIT 1", T, NOMOR),
            "q": await c.fetchval("SELECT id FROM quotes WHERE tenant_id=$1 AND payment_account_number=$2 "
                                  "ORDER BY created_at DESC LIMIT 1", T, NOMOR),
        }
    print("data uji:", {k: bool(v) for k, v in dok.items()}, "bank", bool(ba), flush=True)
    if not (ba and dok["si"]):
        return J.gagal("00_data", f"bank={ba} faktur={dok['si']}")

    awal = await _semua_pdf(J, "01_sebelum_diisi", dok)
    for k, v in awal.items():
        if v is not None:
            J.gagal(f"01_{k}_tanpa_an", f"tercetak {v!r} (harap None: snapshot = nama akun)")

    await J.langkah("02_patch_pemilik", "PATCH", f"/api/bank-accounts/{ba}", {"account_holder_name": f"  {PEMILIK} "})
    _, d = await J.langkah("03_detail", "GET", f"/api/bank-accounts/{ba}")
    if ((d or {}).get("data") or {}).get("account_holder_name") != PEMILIK:
        J.gagal("03_detail_isi", str((d or {}).get("data"))[:200])
    _, ls = await J.langkah("04_daftar", "GET", "/api/bank-accounts?limit=100")
    it = [x for x in ((ls or {}).get("items") or (ls or {}).get("data") or []) if isinstance(x, dict) and x.get("id") == str(ba)]
    if not it or it[0].get("account_holder_name") != PEMILIK:
        J.gagal("04_daftar_isi", str(ls)[:200])
    _, dd = await J.langkah("05_dropdown", "GET", "/api/bank-accounts/dropdown")
    it = [x for x in (dd or {}).get("accounts", []) if x.get("id") == str(ba)]
    if not it or it[0].get("account_holder_name") != PEMILIK:
        J.gagal("05_dropdown_isi", str(dd)[:200])

    isi = await _semua_pdf(J, "06_sesudah_diisi", dok)
    for k, v in isi.items():
        if v != PEMILIK:
            J.gagal(f"06_{k}_an_pemilik", f"{v!r}")

    DV = J.mod("services.dashboard_v2")
    async with J.pool.acquire() as c:
        wa = await DV.rekening_per_faktur(c, T, [dok["si"]])
    if wa.get(str(dok["si"])) != f"Bank BCA {NOMOR} a.n. {PEMILIK}" and not str(wa.get(str(dok["si"]))).endswith(f"a.n. {PEMILIK}"):
        J.gagal("07_wa", str(wa))
    print("07 WA", wa, flush=True)

    pid = await _barang(J, "PEMILIK", False)
    _, r = await J.langkah("08_so_baru_snapshot", "POST", "/api/sales-orders", {
        "order_date": J.hari.isoformat(), "customer_id": PELANGGAN, "customer_name": NAMA,
        "expected_ship_date": (J.hari + timedelta(days=3)).isoformat(),
        "payment_bank_name": "Bank BCA", "payment_account_number": NOMOR, "payment_account_holder": "BCA Operasional",
        "items": [{"item_id": pid, "description": "TES E2E pemilik rekening", "quantity": "1", "unit_price": 100000}]},
        headers={"X-Idempotency-Key": f"journey-{uuid.uuid4()}"})
    so = ((r or {}).get("data") or {}).get("id")
    async with J.pool.acquire() as c:
        snap = await c.fetchval("SELECT payment_account_holder FROM sales_orders WHERE id=$1::uuid AND tenant_id=$2", so, T) if so else "SO?"
    if snap != PEMILIK:
        J.gagal("08_snapshot", repr(snap))

    await J.langkah("09_kosongkan", "PATCH", f"/api/bank-accounts/{ba}", {"account_holder_name": ""})
    async with J.pool.acquire() as c:
        nil = await c.fetchval("SELECT account_holder_name FROM bank_accounts WHERE id=$1", ba)
    if nil is not None:
        J.gagal("09_null", repr(nil))
    akhir = await _semua_pdf(J, "10_sesudah_dikosongkan", dok)
    if akhir.get("faktur") is not None:
        J.gagal("10_faktur_tanpa_an", repr(akhir.get("faktur")))
