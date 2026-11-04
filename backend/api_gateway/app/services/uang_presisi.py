"""Presisi uang per tenant (pembulatan rupiah, putusan pemilik LANGSUNG 4 Nov 2026; pola Accurate "digit desimal" IDR = 0).

SATU fungsi pembulatan nominal dokumen (Law 9: Decimal, ROUND_HALF_UP). Bawaan 0 desimal (rupiah penuh): tiap nominal yang LAHIR
(neto baris, diskon, alokasi, DPP, PPN, ongkir, total) dibulatkan di titik lahirnya, header = jumlah komponen -> tanpa akun
pembulatan. Pintu keluar per tenant: flag `uang_bersen` (tenant_features) = 2 desimal (perilaku lama). Harga satuan / qty / HPP
tidak disentuh. Data lama tidak diubah.
"""
from decimal import ROUND_HALF_UP, Decimal

FLAG_BERSEN = "uang_bersen"
_SATUAN = {0: Decimal("1"), 2: Decimal("0.01")}


def bulat(v, presisi: int = 2) -> Decimal:
    """Nominal -> Decimal HALF_UP ke 10^-presisi (0 = rupiah penuh, 2 = sen)."""
    x = v if isinstance(v, Decimal) else Decimal(str(v if v not in (None, "") else 0))
    return x.quantize(_SATUAN[presisi], rounding=ROUND_HALF_UP)


async def presisi_tenant(conn, tid: str) -> int:
    """0 bawaan; 2 bila flag uang_bersen aktif (pembaca flag tunggal)."""
    from .tenant_features import fitur_aktif
    return 2 if FLAG_BERSEN in await fitur_aktif(conn, tid) else 0


_KOLOM_UANG = ("total_amount", "subtotal", "tax_amount", "discount_amount", "shipping_amount", "amount", "grand_total")


def bersen(v) -> bool:
    return v is not None and Decimal(str(v)) % 1 != 0


async def presisi_dokumen(conn, tid: str, *sumber) -> int:
    """Presisi untuk MENGHITUNG dokumen. Baru: presisi tenant. Ubah / turunan (SO->faktur, penawaran->SO, nota kredit dari faktur):
    sumber tersimpan yang BERSEN (data lama) mewarisi 2 desimal -- supaya turunan bisa menutup sumbernya persis (data lama tak
    diubah, retur penuh faktur bersen tak menyisakan Rp 0,50)."""
    p = await presisi_tenant(conn, tid)
    if p == 2:
        return 2
    for s in sumber:
        r = dict(s) if s is not None else {}
        if any(bersen(r.get(k)) for k in _KOLOM_UANG):
            return 2
    return p


_TABEL_DOKUMEN = ("sales_invoices", "sales_orders", "quotes", "bills")


async def presisi_dari(conn, tid: str, tabel: str, doc_id) -> int:
    """presisi_dokumen untuk dokumen TERSIMPAN (ubah / turunan) dibaca dari tabelnya."""
    if tabel not in _TABEL_DOKUMEN:
        raise ValueError(f"tabel dokumen tak dikenal: {tabel}")
    row = await conn.fetchrow(f"SELECT * FROM {tabel} WHERE id = $1 AND tenant_id = $2", doc_id, tid)  # nosec B608 (daftar tetap)
    return await presisi_dokumen(conn, tid, row)
