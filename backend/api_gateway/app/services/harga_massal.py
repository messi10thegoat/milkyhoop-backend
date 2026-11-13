"""DAFTAR HARGA D3 (putusan pemilik LANGSUNG 13 Nov 2026): UBAH HARGA MASSAL per daftar -- persen ATAU nominal (naik/turun) atas DASAR harga lama atau HPP
(cost-plus), pembulatan, tanggal berlaku, pratinjau PER BARANG. SATU penentu pratinjau + penulis (ukur_rencana -> rencana_barang -> terapkan); tanpa jurnal
(data induk harga, bukan transaksi uang).
- Dasar `harga_lama`: harga baris sekarang. Dasar `hpp`: HPP satuan baris = biaya rata-rata tertimbang barang (get_weighted_average_cost, satuan dasar, dari
  ledger -- Iron Law 16) x faktor konversi satuan baris (penentu satuan_dasar yang SAMA dgn jalur stok). Tanpa HPP (None/<=0/satuan tak terkonversi) = BLOK
  per barang DH_HPP_TAK_ADA; barang lain tetap bisa diterapkan.
- Pembulatan: bawaan TANPA (2 desimal HALF_UP, Law 9/25); kelipatan 1/10/100/500/1000 x arah terdekat(HALF_UP)/atas/bawah, SEBELUM cek negatif.
- Hasil < 0 = blok DH_HARGA_NEGATIF; hasil = 0 = PERINGATAN DH_HARGA_NOL (bentuk BUG-207: butuh_konfirmasi + pesan; FE meminta konfirmasi, tulis TIDAK diblok), tetap boleh.
- Tanggal berlaku = start_date baris yang diubah (kunci unik daftar: barang+satuan+qty minimum, tanpa tanggal, jadi dua harga berjangka pada kunci sama belum
  bisa). Berlaku di MASA DEPAN: sebelum tanggal itu baris tak berlaku -> (diukur dari harga_jual.harga_saran) tingkat qty lebih rendah di DAFTAR YANG SAMA bila
  ada, selain itu harga barang (harga satuan konversi, lalu harga dasar x faktor); TIDAK pindah ke daftar bawaan (PERINGATAN)."""
from datetime import date
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Optional

from ..utils.tanggal_tenant import tanggal_dokumen
from . import satuan_dasar as SD
from .harga_daftar import tgl_text, unit_text

SEN = Decimal("0.01")
SATU = Decimal(1)
MODE_BULAT = {"terdekat": ROUND_HALF_UP, "atas": ROUND_CEILING, "bawah": ROUND_FLOOR}
KELIPATAN = (0, 1, 10, 100, 500, 1000)


def _blok(code: str, message: str) -> dict:
    return {"code": code, "message": message}


def _per(code: str, pesan: str) -> dict:
    """Peringatan = bentuk BUG-207 (satu komponen FE): {code, butuh_konfirmasi:true, pesan}; `message` = salinan pesan. Tulis TIDAK diblok."""
    return {"code": code, "butuh_konfirmasi": True, "pesan": pesan, "message": pesan}


def _hasil(blocks: list, rencana, peringatan=None) -> dict:
    return {"ok": not blocks, "blocks": blocks, "peringatan": peringatan or [], "rencana": rencana}


def bulatkan(v, kelipatan: int = 0, arah: str = "terdekat") -> Decimal:
    v = Decimal(str(v))
    if not kelipatan:
        return v.quantize(SEN, rounding=ROUND_HALF_UP)
    n = Decimal(kelipatan)
    return ((v / n).quantize(SATU, rounding=MODE_BULAT[arah]) * n).quantize(SEN)


def hitung_baru(dasar_nilai, mode: str, nilai, kelipatan: int = 0, arah: str = "terdekat") -> Optional[Decimal]:
    p, n = Decimal(str(dasar_nilai)), Decimal(str(nilai))
    baru = bulatkan(p * (SATU + n / Decimal(100)) if mode == "persen" else p + n, kelipatan, arah)
    return None if baru < 0 else baru


def rencana_barang(nama: str, baris: list, mode: str, nilai, mulai: Optional[date], hari_ini: date,
                   dasar: str = "harga_lama", kelipatan: int = 0, arah: str = "terdekat") -> dict:
    if not baris:
        return _hasil([_blok("DH_BARANG_TAK_DI_DAFTAR", f"{nama or 'Barang'} tidak punya harga di daftar ini.")], None)
    if Decimal(str(nilai)) == 0:
        return _hasil([_blok("DH_TANPA_PERUBAHAN", "Nilai perubahan 0: harga tidak berubah.")], None)
    if dasar == "hpp" and any(b.get("hpp") is None for b in baris):
        return _hasil([_blok("DH_HPP_TAK_ADA", f"{nama} belum punya HPP (belum ada stok masuk, barang tanpa stok, atau satuan tak terkonversi); "
                                               "pakai dasar harga lama.")], None)
    bloks, hasil = [], []
    for b in baris:
        hpp = Decimal(str(b["hpp"])).quantize(SEN) if b.get("hpp") is not None else None
        dasar_nilai = hpp if dasar == "hpp" else Decimal(str(b["price"]))
        ke = hitung_baru(dasar_nilai, mode, nilai, kelipatan, arah)
        ut = unit_text(b["unit"], b.get("dasar"))
        if ke is None:
            bloks.append(_blok("DH_HARGA_NEGATIF", f"{nama} ({ut}, minimum {Decimal(str(b['min_quantity'])).normalize():f}) akan jadi di bawah Rp 0."))
            continue
        dari = Decimal(str(b["price"]))
        margin = ((ke - hpp) / hpp * Decimal(100)).quantize(SEN, rounding=ROUND_HALF_UP) if hpp else None
        hasil.append({"id": str(b["id"]), "unit_text": ut, "min_quantity": float(b["min_quantity"]), "dari": dari, "ke": ke, "selisih": ke - dari,
                      "dasar_nilai": dasar_nilai, "hpp": hpp, "margin_persen": margin})
    if bloks:
        return _hasil(bloks, None)
    per = []
    if mulai and mulai > hari_ini:
        per.append(_per("DH_BERLAKU_NANTI", f"Sebelum {tgl_text(mulai)}, harga {nama} di daftar ini belum berlaku: dipakai tingkat jumlah yang lebih rendah di daftar yang sama bila ada, "
                                                    "selain itu harga barang (harga satuan konversi atau harga dasar di data barang)."))
    nol = any(h["ke"] == 0 for h in hasil)
    if nol:
        per.append(_per("DH_HARGA_NOL", f"Sebagian harga {nama} akan jadi Rp 0. Konfirmasi dulu bila memang disengaja."))
    return _hasil([], {"barang": nama, "jumlah_baris": len(hasil), "baris": hasil, "mode": mode, "nilai": Decimal(str(nilai)), "dasar": dasar,
                       "pembulatan": {"kelipatan": kelipatan, "arah": arah}, "ada_harga_nol": nol,
                       "berlaku_mulai": tgl_text(mulai) if mulai else None, "mulai": mulai}, per)


async def _hpp_baris(conn, tid: str, item_id, baris: list) -> None:
    """Isi b["hpp"] = WAC satuan dasar x faktor konversi satuan baris (None bila WAC kosong/<=0 atau satuan tak terkonversi)."""
    wac = await conn.fetchval("SELECT get_weighted_average_cost($1, $2)", tid, item_id)
    ktx = await SD.muat(conn, tid, [item_id])
    for b in baris:
        h = SD.hitung(ktx, item_id, b["unit"], 1, "konversi")
        b["hpp"] = None if (wac is None or Decimal(str(wac)) <= 0 or h["blok"]) else Decimal(str(wac)) * h["faktor"]


async def ukur_rencana(conn, tid: str, pl_id, item_id, mode: str, nilai, mulai: Optional[date], kunci: bool = False,
                       dasar: str = "harga_lama", kelipatan: int = 0, arah: str = "terdekat") -> dict:
    """Ukur baris barang itu di daftar (bersaring tenant) lalu rencana_barang -- DIPAKAI pratinjau DAN penulis (kunci=True: FOR UPDATE)."""
    baris = [dict(r) for r in await conn.fetch(
        f"""SELECT pli.id, pli.unit, pli.price, pli.min_quantity, pli.start_date, COALESCE(NULLIF(p.base_unit, ''), p.satuan) AS dasar, p.nama_produk
            FROM price_list_items pli JOIN price_lists pl ON pl.id = pli.price_list_id
            LEFT JOIN products p ON p.id = pli.item_id AND p.tenant_id = pl.tenant_id
            WHERE pl.tenant_id = $1 AND pli.price_list_id = $2 AND pli.item_id = $3
            ORDER BY pli.unit NULLS FIRST, pli.min_quantity{' FOR UPDATE OF pli' if kunci else ''}""", tid, pl_id, item_id)]
    nama = (baris[0]["nama_produk"] if baris else None) or await conn.fetchval(
        "SELECT nama_produk FROM products WHERE id = $1 AND tenant_id = $2", item_id, tid)
    if dasar == "hpp" and baris:
        await _hpp_baris(conn, tid, item_id, baris)
    return rencana_barang(nama or "Barang", baris, mode, nilai, mulai, await tanggal_dokumen(conn, tid), dasar, kelipatan, arah)


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
