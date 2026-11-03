"""QTY-DASAR (3 Nov 2026, R1; desain disetujui MASTER): SATU penentu satuan dokumen -> SATUAN DASAR barang untuk setiap jalur stok.

Kartu stok (inventory_ledger), warehouse_stock, dan WAC SELALU dalam satuan dasar barang (milkyhoop-inventory Rule 1/3). Dokumen tetap
menyimpan satuan + qty aslinya; yang diturunkan di sini hanya qty stok dan biaya per satuan dasar.

  satuan dasar = COALESCE(NULLIF(products.base_unit, ''), products.satuan)
  sama        = satuan kosong ATAU sama dengan dasar sesudah normalisasi (huruf kecil, spasi tunggal) + alias kamus tenant product_units
                (name <-> abbreviation: "Kg" == "kilogram"; "Pcs" != "Kg")       -> faktor 1
  konversi    = unit_conversions aktif barang itu (1 conversion_unit = conversion_factor base_unit), dicocokkan lewat alias yang sama
  selain itu  = DITOLAK berkalimat (bukan 1:1 diam seperti unit_helpers lama)

Mode:
  "konversi"  jalur MASUK R1 (GRN, posting FPB, FPB terbit langsung, retur penjualan NK): satuan ber-konversi dikonversi.
  "tolak"     jalur KELUAR R1 (pengiriman faktur/SO, SJ, kasir, retur pembelian ND, direct_action): satuan selain dasar DITOLAK
              (SATUAN_BELUM_DIDUKUNG) sampai R2 memasang konversi -- tak pernah ada keadaan campur (masuk dikonversi, keluar mentah).
Uang/qty Decimal presisi penuh (Law 9/25); pembulatan urusan pemanggil. Hanya MEMBACA (aman untuk pratinjau READ ONLY)."""
from decimal import Decimal
from typing import Iterable, Optional

from . import teks_galat as tg
from .galat_berkode import GalatBerkode

SATU = Decimal("1")
KODE_TAK_TERKONVERSI = "SATUAN_TAK_TERKONVERSI"
KODE_BELUM_DIDUKUNG = "SATUAN_BELUM_DIDUKUNG"


def normal(u: Optional[str]) -> str:
    return " ".join(str(u or "").split()).lower()


async def _alias(conn, tid: str) -> dict:
    """{bentuk ternormalisasi: kanon} dari kamus satuan tenant: nama dan singkatan menunjuk satu kanon (nama)."""
    peta = {}
    for r in await conn.fetch("SELECT name, abbreviation FROM product_units WHERE tenant_id = $1 AND COALESCE(is_active, true)", tid):
        kanon = normal(r["name"]) or normal(r["abbreviation"])
        for x in (r["name"], r["abbreviation"]):
            if normal(x):
                peta.setdefault(normal(x), kanon)
    return peta


def kanon(u: Optional[str], alias: dict) -> str:
    n = normal(u)
    return alias.get(n, n)


async def muat(conn, tid: str, product_ids: Iterable) -> dict:
    """-> {"alias": {...}, "barang": {str(product_id): {nama, dasar, konversi: {kanon_satuan: faktor}}}} -- satu kali baca per dokumen."""
    ids = list({str(p) for p in product_ids if p is not None})
    if not ids:  # tak ada baris bersatuan: nol baca (baris tanpa satuan = satuan dasar)
        return {"alias": {}, "barang": {}}
    alias = await _alias(conn, tid)
    barang = {}
    if ids:
        for r in await conn.fetch(
                """SELECT id, nama_produk, COALESCE(NULLIF(base_unit, ''), satuan) AS dasar FROM products
                   WHERE tenant_id = $1 AND id = ANY($2::uuid[])""", tid, ids):
            barang[str(r["id"])] = {"nama": r["nama_produk"], "dasar": r["dasar"], "konversi": {}}
        for r in await conn.fetch(
                """SELECT product_id, base_unit, conversion_unit, conversion_factor FROM unit_conversions
                   WHERE tenant_id = $1 AND product_id = ANY($2::uuid[]) AND COALESCE(is_active, true) AND conversion_factor > 0""", tid, ids):
            b = barang.get(str(r["product_id"]))
            if b is not None and kanon(r["base_unit"], alias) == kanon(b["dasar"], alias):
                b["konversi"].setdefault(kanon(r["conversion_unit"], alias), Decimal(str(r["conversion_factor"])))
    return {"alias": alias, "barang": barang}


def hitung(ktx: dict, product_id, unit: Optional[str], qty, mode: str, aksi: str = "Pengiriman") -> dict:
    """-> {qty_dasar, faktor, satuan, satuan_dasar, blok}. blok = None atau {code, message, status} (pemanggil memilih raise/kumpul)."""
    q = Decimal(str(qty or 0))
    b = ktx["barang"].get(str(product_id)) if product_id is not None else None
    if b is None:  # barang tak ada di tenant: bukan urusan penentu satuan (pemanggil sudah memeriksa barang)
        return {"qty_dasar": q, "faktor": SATU, "satuan": unit, "satuan_dasar": None, "blok": None}
    alias = ktx["alias"]
    hasil = {"qty_dasar": q, "faktor": SATU, "satuan": unit, "satuan_dasar": b["dasar"], "blok": None}
    if not normal(unit) or kanon(unit, alias) == kanon(b["dasar"], alias):
        return hasil
    if mode == "tolak":
        hasil["blok"] = {"code": KODE_BELUM_DIDUKUNG, "status": 400, "message": tg.satuan_belum_didukung(aksi, unit, b["dasar"])}
        return hasil
    f = b["konversi"].get(kanon(unit, alias))
    if f is None:
        hasil["blok"] = {"code": KODE_TAK_TERKONVERSI, "status": 400, "message": tg.satuan_tak_terkonversi(unit, b["nama"], b["dasar"])}
        return hasil
    hasil.update(qty_dasar=q * f, faktor=f)
    return hasil


def tolak_bila(h: dict) -> dict:
    """Penulis: blok -> GalatBerkode (kalimat sama dengan blok pratinjau)."""
    if h["blok"]:
        raise GalatBerkode(h["blok"]["status"], h["blok"]["code"], h["blok"]["message"])
    return h


async def qty_dasar(conn, tid: str, product_id, unit: Optional[str], qty, mode: str, aksi: str = "Pengiriman") -> dict:
    """Satu baris, penulis (raise bila diblok). Tanpa satuan = satuan dasar: faktor 1 tanpa membaca DB."""
    if not normal(unit):
        return {"qty_dasar": Decimal(str(qty or 0)), "faktor": SATU, "satuan": unit, "satuan_dasar": None, "blok": None}
    return tolak_bila(hitung(await muat(conn, tid, [product_id]), product_id, unit, qty, mode, aksi))


async def blok_baris(conn, tid: str, baris: list, mode: str, aksi: str = "Pengiriman") -> list:
    """Pratinjau / penjaga massal: baris = [{product_id, unit, quantity}] (hanya baris BER-STOK) -> [blok] unik, urutan baris."""
    ktx = await muat(conn, tid, ids_bersatuan(baris))
    out = []
    for b in baris:
        h = hitung(ktx, b["product_id"], b.get("unit"), b.get("quantity"), mode, aksi)
        if h["blok"] and h["blok"] not in out:
            out.append(h["blok"])
    return out


def ids_bersatuan(baris: list, kunci_produk: str = "product_id", kunci_satuan: str = "unit") -> list:
    """product_id baris yang MEMBAWA satuan (baris tanpa satuan = dasar -> tak perlu dibaca)."""
    return [b[kunci_produk] for b in baris if b.get(kunci_produk) and normal(b.get(kunci_satuan))]


def biaya_dasar(nilai, qty_dasar_) -> Decimal:
    """Biaya per satuan dasar = nilai baris / qty dasar (presisi penuh; pola posting FPB). qty 0 -> 0."""
    q = Decimal(str(qty_dasar_ or 0))
    return Decimal(str(nilai or 0)) / q if q else Decimal("0")
