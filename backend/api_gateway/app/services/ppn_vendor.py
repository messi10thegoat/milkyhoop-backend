"""PPN dari vendor tercatat BUKAN PKP di PO / faktur pembelian (BUG-030-BE; putusan pemilik LANGSUNG 5 Nov 2026: "Peringatan saja").

Vendor bukan PKP tak bisa menerbitkan faktur pajak, jadi PPN-nya tak bisa dikreditkan. Tetapi vendors.is_pkp BAWAAN false: vendor yang
datanya belum diisi tampak "bukan PKP". Maka Tinjau (POST /purchase-orders/calculate, POST /bills/calculate) memberi PERINGATAN non-fatal
-- dokumen tetap boleh disimpan; tulis tidak berubah. Tenant non-PKP + vendor PKP = PPN boleh + dikapitalisasi (putusan 4 Nov), tanpa
peringatan. Baca saja, tenant eksplisit."""
from decimal import Decimal
from uuid import UUID

KODE = "VENDOR_BUKAN_PKP"
PESAN = ("Vendor ini tercatat bukan PKP. PPN dari vendor bukan PKP tidak bisa dikreditkan; "
         "periksa faktur pajaknya atau ubah status PKP vendor.")


async def peringatan(conn, tenant_id: str, vendor_id, pajak) -> list:
    """-> [{code, message, field}] bila dokumen ber-PPN (pajak > 0) dan vendor milik tenant tercatat is_pkp bukan true; selain itu []."""
    if not vendor_id or Decimal(str(pajak or 0)) <= 0:
        return []
    try:
        vid = UUID(str(vendor_id))
    except (ValueError, TypeError):
        return []  # vendor tak sah sudah jadi BLOK di kalkulator
    v = await conn.fetchrow("SELECT is_pkp FROM vendors WHERE id = $1 AND tenant_id = $2", vid, tenant_id)
    if v is None or v["is_pkp"] is True:
        return []
    return [{"code": KODE, "message": PESAN, "field": "vendor_id"}]
