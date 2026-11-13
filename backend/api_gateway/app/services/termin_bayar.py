"""Jatuh tempo faktur dari TERMIN pembayaran — SATU aturan untuk semua jalur pembuat faktur (F3, 26 Sep 2026).

Latar (diukur BACKEND, prod baca-saja): to-invoice tanpa due_date -> due_date = invoice_date (termin 0) ->
is_overdue sejak besok. sales_orders.payment_terms (TEKS, mis. "NET 30") TAK PERNAH dipakai (0 pengurai di
server); customers.payment_terms_days hampir selalu 0. grapgrap 4/28 faktur jatuh tempo = tanggal faktur
(semua dari SO), kaos 13/35.

Putusan pemilik (pola QuickBooks/Xero, via MASTER): jatuh tempo bawaan = termin, untuk SEMUA faktur (dari SO
maupun langsung), kecuali pengguna mengisinya sendiri:
  body.due_date  -> 'body'            (isian pengguna SELALU menang)
  "NET <n>" SO   -> 'so_terms'        (hanya faktur dari SO)
  customers.payment_terms_days > 0 -> 'customer_terms'
  selain itu     -> invoice_date, 'default' (perilaku lama)
Teks termin non-NET (mis. "DP 60% di muka, pelunasan sebelum pengiriman") tak memuat jumlah hari -> jatuh ke
termin pelanggan. Tenant eksplisit di kueri pelanggan (Law 24).

Sisi PEMBELIAN (tempo-termin-vendor, 5 Nov 2026, putusan pemilik via MASTER; pola Accurate/QBO/NetSuite): jatuh tempo
tagihan dari PO = body.due_date ('body') -> vendors.payment_terms_days > 0 ('vendor_terms', bawaan DB 30 dipakai apa
adanya; vendor tunai = 0) -> bill_date ('default'). Termin PO = tahap 2 (perlu migrasi, DITUNDA). Faktur lama tak diubah.
"""
import re
from datetime import date, timedelta
from typing import Optional, Tuple

# awalan ^ WAJIB (teks termin DIMULAI "NET"); tanpa \b supaya "NET30hari" = 30 (bukan jatuh ke pelanggan)
_NET = re.compile(r"^\s*NET\s*(\d+)", re.IGNORECASE)
HARI_MAKS = 365  # termin di atas setahun = hampir pasti salah ketik -> diabaikan, bukan dipakai


def hari_dari_termin(teks: Optional[str]) -> Optional[int]:
    """"NET 30" / "net14" / " Net 7 hari" -> jumlah hari; selain itu None."""
    if not teks:
        return None
    m = _NET.match(str(teks))
    if not m:
        return None
    n = int(m.group(1))
    return n if 0 <= n <= HARI_MAKS else None


async def termin_hari(conn, tenant_id: str, termin_so: Optional[str] = None, customer_id=None) -> Tuple[int, str]:
    """(jumlah hari, sumber) TANPA tanggal — dipakai detail SO supaya FE menampilkan bawaan dari aturan YANG SAMA
    (FE tak punya pengurai sendiri). sumber: so_terms | customer_terms | default."""
    n = hari_dari_termin(termin_so)
    if n is not None:
        return n, "so_terms"
    if customer_id:
        hari = await conn.fetchval(
            "SELECT payment_terms_days FROM customers WHERE id = $1::uuid AND tenant_id = $2",
            str(customer_id), tenant_id,
        )
        if hari and 0 < int(hari) <= HARI_MAKS:
            return int(hari), "customer_terms"
    return 0, "default"


async def tentukan_jatuh_tempo(conn, tenant_id: str, invoice_date: date, due_date_body: Optional[date] = None,
                               termin_so: Optional[str] = None, customer_id=None) -> Tuple[date, str]:
    """(due_date, due_date_source)."""
    if due_date_body:
        return due_date_body, "body"
    n, sumber = await termin_hari(conn, tenant_id, termin_so, customer_id)
    return invoice_date + timedelta(days=n), sumber


async def tentukan_jatuh_tempo_tagihan(conn, tenant_id: str, bill_date: date, due_date_body: Optional[date] = None,
                                       vendor_id=None, po_terms=None) -> dict:
    """SATU penentu jatuh tempo tagihan untuk /to-bill DAN /to-bill/preview -> {due_date, due_date_source (body|po_terms|vendor_terms|default),
    payment_terms_days, due_date_text}. payment_terms_days = None bila isian pengguna (termin tak dibaca)."""
    if due_date_body:
        return {"due_date": due_date_body, "due_date_source": "body", "payment_terms_days": None,
                "due_date_text": "Diisi pengguna"}
    # BUG-205 (putusan pemilik 13 Nov): tagihan dari PO MEWARISI syarat bayar PO bila tercatat (snapshot di PO), mengalahkan termin vendor. 0 = Tunai (jatuh tempo = tanggal tagihan).
    if po_terms is not None and 0 <= int(po_terms) <= HARI_MAKS:
        n_po = int(po_terms)
        return {"due_date": bill_date + timedelta(days=n_po), "due_date_source": "po_terms", "payment_terms_days": n_po,
                "due_date_text": "Tunai" if n_po == 0 else f"Termin PO {n_po} hari"}
    n = None
    if vendor_id:
        n = await conn.fetchval(
            "SELECT payment_terms_days FROM vendors WHERE id = $1::uuid AND tenant_id = $2",
            str(vendor_id), tenant_id,
        )
    if n and 0 < int(n) <= HARI_MAKS:
        return {"due_date": bill_date + timedelta(days=int(n)), "due_date_source": "vendor_terms",
                "payment_terms_days": int(n), "due_date_text": f"Termin vendor {int(n)} hari"}
    return {"due_date": bill_date, "due_date_source": "default", "payment_terms_days": 0, "due_date_text": "Tanggal faktur"}
