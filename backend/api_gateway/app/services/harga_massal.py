"""DAFTAR HARGA D3 (putusan pemilik 13 Nov 2026): UBAH HARGA MASSAL per daftar -- persen ATAU nominal (naik/turun), tanggal berlaku, pratinjau PER BARANG.
SATU penentu pratinjau + penulis (ukur_rencana -> rencana_barang -> terapkan); tanpa jurnal (data induk harga, bukan transaksi uang).
Harga hasil dibulatkan 2 desimal HALF_UP (Law 9/25); hasil < 0 = blok DH_HARGA_NEGATIF; 0 diizinkan (CHECK price >= 0).
Tanggal berlaku = start_date baris yang diubah (kunci unik daftar: barang+satuan+qty minimum, tanpa tanggal, jadi dua harga berjangka pada
kunci sama belum bisa). Berlaku di MASA DEPAN: sebelum tanggal itu baris tak berlaku -> harga jatuh ke harga dasar/daftar bawaan (PERINGATAN)."""
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from ..utils.tanggal_tenant import tanggal_dokumen
from .harga_daftar import tgl_text, unit_text

SEN = Decimal("0.01")


def _blok(code: str, message: str) -> dict:
    return {"code": code, "message": message}


def _hasil(blocks: list, rencana, peringatan=None) -> dict:
    return {"ok": not blocks, "blocks": blocks, "peringatan": peringatan or [], "rencana": rencana}


def hitung_baru(price, mode: str, nilai) -> Optional[Decimal]:
    p, n = Decimal(str(price)), Decimal(str(nilai))
    baru = (p * (Decimal(1) + n / Decimal(100)) if mode == "persen" else p + n).quantize(SEN, rounding=ROUND_HALF_UP)
    return None if baru < 0 else baru


def rencana_barang(nama: str, baris: list, mode: str, nilai, mulai: Optional[date], hari_ini: date) -> dict:
    if not baris:
        return _hasil([_blok("DH_BARANG_TAK_DI_DAFTAR", f"{nama or 'Barang'} tidak punya harga di daftar ini.")], None)
    if Decimal(str(nilai)) == 0:
        return _hasil([_blok("DH_TANPA_PERUBAHAN", "Nilai perubahan 0: harga tidak berubah.")], None)
    bloks, hasil = [], []
    for b in baris:
        ke = hitung_baru(b["price"], mode, nilai)
        ut = unit_text(b["unit"], b.get("dasar"))
        if ke is None:
            bloks.append(_blok("DH_HARGA_NEGATIF", f"{nama} ({ut}, minimum {Decimal(str(b['min_quantity'])).normalize():f}) akan jadi di bawah Rp 0."))
            continue
        dari = Decimal(str(b["price"]))
        hasil.append({"id": str(b["id"]), "unit_text": ut, "min_quantity": float(b["min_quantity"]), "dari": dari, "ke": ke, "selisih": ke - dari})
    if bloks:
        return _hasil(bloks, None)
    per = []
    if mulai and mulai > hari_ini:
        per.append({"code": "DH_BERLAKU_NANTI",
                    "message": f"Sebelum {tgl_text(mulai)}, {nama} tidak memakai harga daftar ini (memakai harga dasar atau daftar bawaan)."})
    return _hasil([], {"barang": nama, "jumlah_baris": len(hasil), "baris": hasil, "mode": mode, "nilai": Decimal(str(nilai)),
                       "berlaku_mulai": tgl_text(mulai) if mulai else None, "mulai": mulai}, per)


async def ukur_rencana(conn, tid: str, pl_id, item_id, mode: str, nilai, mulai: Optional[date], kunci: bool = False) -> dict:
    """Ukur baris barang itu di daftar (bersaring tenant) lalu rencana_barang -- DIPAKAI pratinjau DAN penulis (kunci=True: FOR UPDATE)."""
    baris = [dict(r) for r in await conn.fetch(
        f"""SELECT pli.id, pli.unit, pli.price, pli.min_quantity, pli.start_date, COALESCE(NULLIF(p.base_unit, ''), p.satuan) AS dasar, p.nama_produk
            FROM price_list_items pli JOIN price_lists pl ON pl.id = pli.price_list_id
            LEFT JOIN products p ON p.id = pli.item_id AND p.tenant_id = pl.tenant_id
            WHERE pl.tenant_id = $1 AND pli.price_list_id = $2 AND pli.item_id = $3
            ORDER BY pli.unit NULLS FIRST, pli.min_quantity{' FOR UPDATE OF pli' if kunci else ''}""", tid, pl_id, item_id)]
    nama = (baris[0]["nama_produk"] if baris else None) or await conn.fetchval(
        "SELECT nama_produk FROM products WHERE id = $1 AND tenant_id = $2", item_id, tid)
    return rencana_barang(nama or "Barang", baris, mode, nilai, mulai, await tanggal_dokumen(conn, tid))


async def terapkan(conn, tid: str, pl_id, rencana: dict) -> None:
    """Satu penulis: UPDATE harga tiap baris rencana (+ start_date bila tanggal berlaku diisi); bersaring tenant lewat price_lists."""
    mulai = rencana.get("mulai")
    for b in rencana["baris"]:
        if mulai:
            await conn.execute(
                """UPDATE price_list_items SET price = $4, start_date = $5 WHERE id = $1 AND price_list_id = $2
                   AND price_list_id IN (SELECT id FROM price_lists WHERE id = $2 AND tenant_id = $3)""", b["id"], pl_id, tid, b["ke"], mulai)
        else:
            await conn.execute(
                """UPDATE price_list_items SET price = $4 WHERE id = $1 AND price_list_id = $2
                   AND price_list_id IN (SELECT id FROM price_lists WHERE id = $2 AND tenant_id = $3)""", b["id"], pl_id, tid, b["ke"])
