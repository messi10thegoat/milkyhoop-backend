"""KEADAAN-LIST Penawaran (17 Okt 2026, PENJUALAN-SERAGAM): data daftar/detail Penawaran dihitung SERVER, FE hanya memformat.

  kedaluwarsa_terbuka = menunggu jawaban (sent/viewed) DAN bertanggal kedaluwarsa  (= predikat `kedaluwarsa` minus tanggal)
  kedaluwarsa_hari    = expiry_date - hari ini (hari kalender, tanggal BISNIS tenant), None bila tak terbuka; <0 == is_expired
  kedaluwarsa_keadaan = telat (lewat masa; == is_expired) | hari_ini | akan | selesai (sudah dijawab/dikonversi, tanggal redup) | tanpa
  kedaluwarsa_teks    = teks sel: "Hari ini" / "Besok" / "12 Okt" / "12 Okt, lewat masa" / "—"
  documents/doc_count = [{type}] : quotation + (bila sudah jadi SO) dokumen SO itu -- fungsi YANG SAMA dgn kolom Dok. Pesanan
  syarat_cari         = predikat ?search= SATU sumber untuk daftar DAN /summary?search=
"""
from datetime import date
from typing import Optional

from .penawaran_kedaluwarsa import MENUNGGU
from .so_posisi import BULAN

SELESAI_JAWAB = ("accepted", "converted", "declined")


def kedaluwarsa_fakta(status: Optional[str], expiry_date: Optional[date], hari_ini: date) -> dict:
    terbuka = status in MENUNGGU and expiry_date is not None
    hari = (expiry_date - hari_ini).days if terbuka else None
    tgl = f"{expiry_date.day} {BULAN[expiry_date.month - 1]}" if expiry_date is not None else None
    if terbuka:
        keadaan = "telat" if hari < 0 else "hari_ini" if hari == 0 else "akan"
        teks = f"{tgl}, lewat masa" if hari < 0 else "Hari ini" if hari == 0 else "Besok" if hari == 1 else tgl
    elif status in SELESAI_JAWAB and expiry_date is not None:
        keadaan, teks = "selesai", tgl
    else:
        keadaan, teks = "tanpa", "—"
    return {"kedaluwarsa_terbuka": terbuka, "kedaluwarsa_hari": hari, "kedaluwarsa_keadaan": keadaan, "kedaluwarsa_teks": teks}


POSISI = {"draft": "Draf", "sent": "Terkirim", "viewed": "Dilihat", "accepted": "Diterima", "declined": "Ditolak",
          "converted": "Jadi pesanan", "void": "Dibatalkan", "expired": "Kedaluwarsa"}


def position_text(status: Optional[str], expiry_date: Optional[date], hari_ini: date) -> str:
    """Kolom Status FE (SATU fungsi: baris daftar, detail, anchor bundel). sent/viewed lewat tanggal -> "Kedaluwarsa" (== is_expired)."""
    from .penawaran_kedaluwarsa import kedaluwarsa
    if kedaluwarsa(status, expiry_date, hari_ini):
        return POSISI["expired"]
    return POSISI.get(status or "", status or "")


def syarat_cari(search: Optional[str], mulai: int) -> tuple:
    """(predikat, parameter) ?search= daftar Penawaran (tabel `quotes`, $1 = tenant); tiap kata satu parameter mulai $mulai,
    semua kata wajib cocok. Kosong -> (None, [])."""
    from .kode_order import sql_cari_so_induk as _cari_so
    if not isinstance(search, str):  # panggilan langsung (uji) tanpa argumen = objek Query -> tanpa saring
        return None, []
    kata = search.strip().split()
    if not kata:
        return None, []
    syarat = [f"(quote_number ILIKE ${mulai + i} OR customer_name ILIKE ${mulai + i} OR subject ILIKE ${mulai + i} OR search_text ILIKE ${mulai + i} "
              f"OR customer_id::text IN (SELECT c.id::text FROM customers c WHERE c.tenant_id = quotes.tenant_id AND c.search_text ILIKE ${mulai + i}) "
              f"OR {_cari_so('quote', 'quotes', '$1', f'${mulai + i}')})" for i in range(len(kata))]
    return (syarat[0] if len(syarat) == 1 else f"({' AND '.join(syarat)})"), [f"%{w}%" for w in kata]


async def dokumen_penawaran(conn, tenant_id: str, so_id_per_quote: dict) -> dict:
    """{quote_id(str): [{type}]}. so_id_per_quote = {quote_id(str): so_id(str)|None}. Tanpa SO -> [quotation]."""
    import uuid
    from .so_posisi import dokumen_per_so
    so_ids = sorted({uuid.UUID(v) for v in so_id_per_quote.values() if v})
    baris = [dict(r) for r in await conn.fetch(
        "SELECT id, status, quote_id FROM sales_orders WHERE tenant_id = $1 AND id = ANY($2::uuid[])", tenant_id, so_ids)] if so_ids else []
    dok_so = await dokumen_per_so(conn, tenant_id, baris)
    hasil = {}
    for q, so in so_id_per_quote.items():
        d = dok_so.get(uuid.UUID(so)) if so else None
        hasil[q] = d if d else [{"type": "quotation"}]
    return hasil
