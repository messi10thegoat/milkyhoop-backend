"""BUG-040 (16 Nov 2026; V501 min_price): penentu HARGA MINIMUM per baris daftar harga -- SATU sakelar perilaku sampai bukti Accurate (TESTER) tiba.
Baris daftar yang menang (harga_jual.pilih_baris) membawa `min_price`; harga satuan dokumen setelah diskon di bawahnya = pelanggaran lantai.
MODE_PENEGAKAN: "peringatan" (bawaan; bentuk BUG-207 {code, butuh_konfirmasi, pesan, message}, tulis tak diblok) | "blok" (409 JUAL_DI_BAWAH_MINIMUM) | "mati".
Putusan pemilik via MASTER 16 Nov (bukti Accurate PT BIS, TESTER): jual di bawah Harga Minimum = PERINGATAN konfirmasi (bukan blok), bentuk BUG-207, muncul saat
SIMPAN dokumen tempat harga diketik (Penawaran/SO/Faktur); konversi SO->Faktur TIDAK mengulang bila harga baris tak berubah; tanpa pembatasan peran;
pesan menyebut minimum + selisih dari server. Dipasang di titik yang sama dengan jual_bawah_hpp (/calculate + pratinjau dokumen tersimpan)."""
import logging
from decimal import Decimal
from typing import Iterable, List, Optional

from . import satuan_dasar as SD
from .galat_berkode import GalatBerkode

logger = logging.getLogger(__name__)

KODE = "JUAL_DI_BAWAH_MINIMUM"
MODE_PENEGAKAN = "peringatan"
MODE_SAH = ("peringatan", "blok", "mati")


def _rp(v: Decimal) -> str:
    return f"{v:,.0f}".replace(",", ".") if v == v.to_integral_value() else f"{v:,.2f}".replace(",", "#").replace(".", ",").replace("#", ".")


def periksa(harga_satuan, minimum, nama: Optional[str] = None, mode: Optional[str] = None) -> Optional[dict]:
    """Harga satuan efektif vs lantai. None = aman (tanpa lantai, mode mati, atau harga >= lantai). Selain itu dict peringatan; mode 'blok' memuat blok=True."""
    mode = mode or MODE_PENEGAKAN
    if mode not in MODE_SAH:
        raise ValueError(f"mode tak dikenal: {mode}")
    if mode == "mati" or minimum is None or harga_satuan is None:
        return None
    h, m = Decimal(str(harga_satuan)), Decimal(str(minimum))
    if h >= m:
        return None
    pesan = f"Harga {nama + ' ' if nama else ''}Rp {_rp(h)} di bawah harga minimum Rp {_rp(m)} (selisih Rp {_rp(m - h)})."
    return {"code": KODE, "butuh_konfirmasi": mode == "peringatan", "pesan": pesan, "message": pesan, "harga_minimum": float(m), "blok": mode == "blok"}


def tolak_bila_blok(r: Optional[dict]) -> Optional[dict]:
    """Mode blok -> 409; selain itu kembalikan peringatannya (atau None)."""
    if r and r["blok"]:
        raise GalatBerkode(409, KODE, r["pesan"])
    return r


# ---------------------------------------------------------------------------------------------------- dokumen penjualan
JENIS_TERSIMPAN = {  # jenis -> (header, tanggal, kolom asal SO | None, tabel baris, kolom penunjuk)
    "faktur": ("sales_invoices", "invoice_date", "sales_order_id", "sales_invoice_items", "invoice_id"),
    "so": ("sales_orders", "order_date", None, "sales_order_items", "sales_order_id"),
    "penawaran": ("quotes", "quote_date", None, "quote_items", "quote_id"),
}


def baris_dari_hasil(items: Iterable[dict]) -> List[dict]:
    """Baris hasil compute_document -> masukan hitung (nilai = dpp_harga_jual: bersih sesudah diskon baris + diskon dokumen, sebelum PPN)."""
    return [{"baris": n, "item_id": ln.get("item_id"), "quantity": ln.get("quantity"), "unit": ln.get("unit"), "nilai": ln.get("dpp_harga_jual"),
             "unit_price": ln.get("unit_price")} for n, ln in enumerate(items, 1)]


async def hitung(conn, tid: str, customer_id, tanggal, lines: Iterable[dict], harga_asal: Optional[dict] = None, mode: Optional[str] = None) -> List[dict]:
    """lines = [{baris, item_id, quantity, unit, nilai, unit_price}]. Daftar yang berlaku untuk pelanggan pada `tanggal` (harga_jual.daftar_untuk); baris daftar yang MENANG
    (satuan sama, qty minimum terpenuhi, berlaku pada tanggal; pilih_baris) membawa min_price. Harga satuan efektif = nilai / qty (satuan dokumen) < min_price -> peringatan.
    harga_asal = {item_id: unit_price} dokumen asal (SO) pada konversi: baris yang harganya SAMA dengan asal tidak diperiksa ulang. Tak menebak: tanpa daftar, tanpa
    lantai, satuan baris tak sama dengan satuan baris daftar, qty 0 = dilewati."""
    from . import harga_jual as HJ
    mode = mode or MODE_PENEGAKAN
    lines = [x for x in lines if x.get("item_id")]
    if mode == "mati" or not lines:
        return []
    daftar = await HJ.daftar_untuk(conn, tid, customer_id, tanggal)
    if not daftar:
        return []
    ids = sorted({str(x["item_id"]) for x in lines})
    ada = {}
    for r in await conn.fetch(
            """SELECT pli.item_id, pli.unit, pli.price, pli.min_price, pli.discount_percent, pli.min_quantity, pli.start_date, pli.end_date FROM price_list_items pli
               JOIN price_lists pl ON pl.id = pli.price_list_id WHERE pl.tenant_id = $1 AND pli.price_list_id = $2
               AND pli.item_id = ANY($3::uuid[]) AND COALESCE(pli.is_active, true)""", tid, daftar["id"], ids):
        ada.setdefault(str(r["item_id"]), []).append(r)
    if not any(r["min_price"] is not None for rs in ada.values() for r in rs):
        return []
    alias = await SD._alias(conn, tid)
    info = {str(r["id"]): r for r in await conn.fetch(
        "SELECT id, nama_produk, COALESCE(NULLIF(base_unit, ''), satuan) AS dasar FROM products WHERE tenant_id = $1 AND id = ANY($2::uuid[])", tid, ids)}
    out = []
    ktx = None
    for x in lines:
        i = str(x["item_id"])
        brg = info.get(i)
        q = Decimal(str(x.get("quantity") or 0))
        if brg is None or q <= 0 or x.get("nilai") is None:
            continue
        if harga_asal and x.get("unit_price") is not None and i in harga_asal and Decimal(str(harga_asal[i])) == Decimal(str(x["unit_price"])):
            continue                                                       # konversi: harga tak berubah -> tak mengulang peringatan
        kanon = SD.kanon(x.get("unit") or brg["dasar"], alias)
        cocok = [r for r in ada.get(i, []) if SD.kanon(r["unit"] or brg["dasar"], alias) == kanon and Decimal(str(r["min_quantity"])) <= q
                 and (r["start_date"] is None or r["start_date"] <= tanggal) and (r["end_date"] is None or r["end_date"] >= tanggal)]
        if cocok:
            menang = max(cocok, key=lambda r: (Decimal(str(r["min_quantity"])), r["start_date"] or __import__("datetime").date.min))
            lantai = menang["min_price"]                                   # baris satuan eksplisit MENANG (tanpa minimum sendiri = tanpa lantai)
        else:
            # BUG-339 (penjaga): satuan turunan tanpa baris DH sendiri -> lantai = minimum DH satuan dasar x faktor, SATU penentu dgn resolver
            # (harga_jual._dari_daftar_dasar + minimum_turunan; tingkat qty dievaluasi pada qty dasar). SD.muat SEKALI per hitung (batch, bukan per baris).
            lantai = None
            if brg["dasar"] and kanon != SD.kanon(brg["dasar"], alias):    # hanya satuan TURUNAN (satuan dasar tanpa baris cocok = tetap dilewati)
                if ktx is None:
                    ktx = await SD.muat(conn, tid, ids)
                h = SD.hitung(ktx, i, x.get("unit"), q, "konversi")
                konv = HJ._dari_daftar_dasar(ada.get(i, []), alias, brg["dasar"], kanon, q, h, tanggal)
                lantai = HJ.minimum_turunan(konv, h) if konv else None
        if lantai is None:
            continue
        harga = Decimal(str(x["nilai"])) / q
        r = periksa(harga, lantai, brg["nama_produk"], mode)
        if r:
            m = Decimal(str(lantai))
            out.append({**r, "baris": x.get("baris"), "product_id": i, "nama": brg["nama_produk"], "satuan": x.get("unit") or brg["dasar"],
                        "harga": float(harga.quantize(Decimal("0.01"))), "selisih": float((m - harga).quantize(Decimal("0.01")))})
    return out


async def peringatan_jual(conn, tid: str, customer_id, tanggal, lines: Iterable[dict], harga_asal: Optional[dict] = None) -> List[dict]:
    """Jalan pintas router (pola jual_bawah_hpp.peringatan_jual): ISYARAT, gagal hitung tak merusak kalkulasi utama (dicatat, daftar kosong)."""
    try:
        return await hitung(conn, tid, customer_id, tanggal, lines, harga_asal)
    except Exception:  # noqa: BLE001
        logger.error("BUG-040 peringatan_jual gagal", exc_info=True)
        return []


async def harga_asal_so(conn, tid: str, so_id) -> dict:
    """{item_id: unit_price} baris SO asal (bersaring tenant) untuk konversi SO -> Faktur."""
    if not so_id:
        return {}
    return {str(r["item_id"]): r["unit_price"] for r in await conn.fetch(
        """SELECT i.item_id, i.unit_price FROM sales_order_items i WHERE i.sales_order_id = $1::uuid
           AND EXISTS (SELECT 1 FROM sales_orders h WHERE h.id = i.sales_order_id AND h.tenant_id = $2)""", str(so_id), tid) if r["item_id"]}


async def peringatan_tersimpan(conn, tid: str, jenis: str, doc_id) -> List[dict]:
    """Dokumen TERSIMPAN (pratinjau terbit/konfirmasi/kirim): lantai dihitung dari baris + pelanggan + tanggal dokumen lewat kalkulator bersama (SAMA dengan
    jual_bawah_hpp.baris_tersimpan). Faktur hasil konversi SO: baris berharga sama dengan SO asal tak diperiksa ulang."""
    try:
        return await _tersimpan(conn, tid, jenis, doc_id)
    except Exception:  # noqa: BLE001
        logger.error("BUG-040 peringatan_tersimpan gagal (%s)", jenis, exc_info=True)
        return []


async def _tersimpan(conn, tid: str, jenis: str, doc_id) -> List[dict]:
    from . import jual_bawah_hpp as JB
    from . import uang_presisi as UP
    from .sales_doc_calc import compute_document
    hdr, kolom_tgl, kolom_so, items, fk = JENIS_TERSIMPAN[jenis]
    kolom_so_sql = kolom_so or "NULL::uuid"
    h = await conn.fetchrow(f"SELECT customer_id, {kolom_tgl} AS tanggal, {kolom_so_sql} AS so_id, discount_amount FROM {hdr} WHERE id = $1::uuid AND tenant_id = $2",
                            str(doc_id), tid)
    if h is None or h["customer_id"] is None:
        return []
    _, _, _, urut, kolom_rp = JB.JENIS_TERSIMPAN[jenis]
    rows = await conn.fetch(
        f"SELECT i.item_id, i.quantity, i.unit, i.unit_price, i.discount_percent, {kolom_rp} AS discount_amount, i.jumlah_baris FROM {items} i "
        f"WHERE i.{fk} = $1::uuid AND EXISTS (SELECT 1 FROM {hdr} x WHERE x.id = i.{fk} AND x.tenant_id = $2) ORDER BY {urut}", str(doc_id), tid)
    if not rows:
        return []
    presisi = await UP.presisi_dari(conn, tid, hdr, doc_id)
    baris = [dict(r) for r in rows]
    hasil = compute_document(baris, doc_discount_amount=Decimal(str(h["discount_amount"] or 0)), presisi=presisi)["items"]
    tanggal = h["tanggal"].date() if hasattr(h["tanggal"], "date") else h["tanggal"]
    return await hitung(conn, tid, h["customer_id"], tanggal, baris_dari_hasil(hasil), await harga_asal_so(conn, tid, h["so_id"]))
