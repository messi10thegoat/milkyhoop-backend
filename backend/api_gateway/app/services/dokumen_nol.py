"""Faktur bernilai Rp 0 (SI-FPB-RP0, 3 Nov 2026; putusan pemilik LANGSUNG): posting faktur PENJUALAN maupun PEMBELIAN dengan total
<= 0 DITOLAK berkalimat 400 -- dulu jurnal Rp 0 ditolak pagar DB jl_has_amount -> 500 / 400 generik, dan pratinjau FPB menjanjikan
posting. Draf Rp 0 tetap boleh disimpan (belum di buku). Faktur barang gratis tanpa jurnal (pola SAP) = tiket terpisah.

SATU fungsi tolak dipakai penulis (raise) DAN pratinjau (blok kalimat sama) di kedua modul:
  SI : routers.sales_invoices._internal_post_invoice (create auto_post, POST /post, SO->faktur), /calculate (auto_post),
       faktur_terbit_rencana.rencana_terbit_ro (POST /{id}/post/preview)
  FPB: bills_service.create_bill_v2 (status posted), tagihan_aksi.blok_posting (POST /{id}/post + /post/preview), /bills/calculate."""
from decimal import Decimal

from . import teks_galat as tg
from .galat_berkode import GalatBerkode

KODE_FAKTUR_NOL = "FAKTUR_NOL"


def tolak_faktur_nol(jenis: str, total, field: str = "total_amount") -> None:
    if Decimal(str(total or 0)) <= 0:
        raise GalatBerkode(400, KODE_FAKTUR_NOL, tg.faktur_nol(jenis), field)


def blok_faktur_nol(jenis: str, total) -> list:
    """Bentuk pratinjau: [{code, message}] dari fungsi tolak YANG SAMA (kosong bila lolos)."""
    try:
        tolak_faktur_nol(jenis, total)
    except GalatBerkode as e:
        return [{"code": e.badan()["code"], "message": e.detail}]
    return []
