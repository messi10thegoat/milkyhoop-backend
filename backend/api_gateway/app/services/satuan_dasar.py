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


def qty_dasar_baris(ktx: dict, product_id, unit: Optional[str], qty) -> dict:
    """BUG-095: qty baris dalam SATUAN DASAR untuk teks FE ("= 12 botol") -- server yang mengonversi. Baris tanpa barang /
    satuan tak terkonversi -> None (FE tak menulis padanan)."""
    if product_id is None:
        return {"qty_dasar": None, "satuan_dasar": None}
    h = hitung(ktx, product_id, unit, qty, "konversi")
    if h["blok"] or not h["satuan_dasar"]:
        return {"qty_dasar": None, "satuan_dasar": None}
    return {"qty_dasar": float(h["qty_dasar"]), "satuan_dasar": h["satuan_dasar"]}


def total_dasar(baris: Iterable) -> list:
    """BUG-095: Σ qty_dasar dokumen PER satuan dasar (urutan kemunculan) -> [{qty, satuan}]; barang beda satuan tak dijumlah."""
    jumlah = {}
    for b in baris:
        if b.get("qty_dasar") is not None:
            jumlah[b["satuan_dasar"]] = jumlah.get(b["satuan_dasar"], Decimal("0")) + Decimal(str(b["qty_dasar"]))
    return [{"qty": float(v), "satuan": k} for k, v in jumlah.items()]


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


# BUG-085 (6 Nov 2026): qty ketikan dalam satuan bebas ("retur 1 dus") -> qty dalam satuan BARIS dokumen asal (ND/NK dari faktur,
# SJ dari SO). Baris asal tetap memegang satuannya; satu penentu dipakai pratinjau DAN tulis. Satuan absen = perilaku lama.
KODE_TIDAK_HABIS = "SATUAN_TIDAK_HABIS_DIBAGI"
SKALA_QTY = Decimal("0.0001")  # numeric(15,4) di semua tabel baris dokumen (information_schema prod 6 Nov)


def ke_satuan_baris(ktx: dict, product_id, unit: Optional[str], qty, unit_baris: Optional[str], nama: str) -> dict:
    """-> {qty dalam satuan baris, blok}. Lewat satuan dasar: qty x faktor(unit) / faktor(unit_baris); harus pas di 4 desimal."""
    q = Decimal(str(qty or 0))
    alias = ktx["alias"]
    if not normal(unit) or kanon(unit, alias) == kanon(unit_baris, alias):
        return {"qty": q, "blok": None}
    if ktx["barang"].get(str(product_id)) is None:  # baris tanpa barang (jasa / teks bebas): tak ada konversi untuk dipakai
        return {"qty": q, "blok": {"code": KODE_TAK_TERKONVERSI, "status": 400,
                                   "message": tg.satuan_tak_terkonversi(unit, nama, unit_baris or "satuan baris")}}
    hk, hb = hitung(ktx, product_id, unit, q, "konversi"), hitung(ktx, product_id, unit_baris, SATU, "konversi")
    if hk["blok"] or hb["blok"]:
        return {"qty": q, "blok": hk["blok"] or hb["blok"]}
    hasil = hk["qty_dasar"] / hb["faktor"]
    if hasil != hasil.quantize(SKALA_QTY):
        return {"qty": q, "blok": {"code": KODE_TIDAK_HABIS, "status": 400, "message": tg.satuan_tidak_habis(
            q, unit, nama, unit_baris or hb["satuan_dasar"], hb["faktor"], hb["satuan_dasar"])}}
    q = hasil.quantize(SKALA_QTY)  # tanpa nol buntut: kalimat blok lain menulis "1", bukan "1.0000"
    return {"qty": q.quantize(SATU) if q == q.to_integral_value() else q.normalize(), "blok": None}


# ------------------------------------------------------------------ P1: konversi TERKUNCI sesudah dipakai (putusan pemilik LANGSUNG 5 Nov,
# pola NetSuite Units of Measure): faktor tak dibekukan di baris dokumen -> konversi yang SUDAH muncul di transaksi (baris dokumen atau
# kartu stok) tak boleh diubah faktornya, diganti satuannya, atau dinonaktifkan; harga beli/jual konversi tetap boleh. Buat satuan baru.
KODE_KONVERSI_TERPAKAI = "KONVERSI_TERPAKAI"
# (tabel baris dokumen, kolom barang) yang menyimpan satuan transaksi (information_schema prod 5 Nov; daftar harga bukan transaksi)
BARIS_BERSATUAN = (("bill_items", "product_id"), ("purchase_order_items", "item_id"), ("quote_items", "item_id"),
                   ("sales_order_items", "item_id"), ("sales_invoice_items", "item_id"), ("sales_receipt_items", "item_id"),
                   ("credit_note_items", "item_id"), ("vendor_credit_items", "item_id"), ("stock_adjustment_items", "product_id"),
                   ("stock_transfer_items", "item_id"))
_NORM_SQL = "regexp_replace(lower(btrim({k})), '\\s+', ' ', 'g')"
SQL_KONVERSI_TERPAKAI = "SELECT EXISTS (" + " UNION ALL ".join(
    [f"SELECT 1 FROM {t} WHERE {k} = $1 AND {_NORM_SQL.format(k='unit')} = ANY($3::text[])" for t, k in BARIS_BERSATUAN]
    + [f"SELECT 1 FROM inventory_ledger WHERE tenant_id = $2 AND product_id = $1 AND {_NORM_SQL.format(k='transaction_unit')} = ANY($3::text[])"]) + ")"


async def konversi_terpakai(conn, tid: str, product_id, satuan: Optional[str]) -> bool:
    """Satuan (dengan semua aliasnya di kamus tenant) sudah dipakai transaksi barang ini? Pemanggil sudah memastikan barang milik tenant."""
    alias = await _alias(conn, tid)
    k = kanon(satuan, alias)
    bentuk = sorted({normal(satuan), k} | {x for x, v in alias.items() if v == k} - {""})
    return bool(bentuk) and bool(await conn.fetchval(SQL_KONVERSI_TERPAKAI, product_id, tid, bentuk))


async def tolak_ubah_konversi(conn, tid: str, product_id, lama: list, baru: Optional[list]) -> None:
    """lama = konversi AKTIF sekarang [{conversion_unit, conversion_factor}]; baru = yang akan aktif [(satuan, faktor)] (None = semua
    dinonaktifkan/dihapus). Konversi lama yang hilang / berubah faktor DAN sudah terpakai -> GalatBerkode 409 KONVERSI_TERPAKAI."""
    tetap = {(normal(u), Decimal(str(f))) for u, f in (baru or [])}
    for r in lama:
        if (normal(r["conversion_unit"]), Decimal(str(r["conversion_factor"]))) in tetap:
            continue
        if await konversi_terpakai(conn, tid, product_id, r["conversion_unit"]):
            raise GalatBerkode(409, KODE_KONVERSI_TERPAKAI, tg.konversi_terpakai(r["conversion_unit"]))


SQL_BARANG_TERPAKAI = "SELECT EXISTS (" + " UNION ALL ".join(
    [f"SELECT 1 FROM {t} WHERE {k} = $1" for t, k in BARIS_BERSATUAN]
    + ["SELECT 1 FROM inventory_ledger WHERE tenant_id = $2 AND product_id = $1"]) + ")"
KODE_SATUAN_DASAR_TERPAKAI = "SATUAN_DASAR_TERPAKAI"


async def tolak_ubah_dasar(conn, tid: str, product_id, dasar_lama: Optional[str], dasar_baru: Optional[str]) -> None:
    """Satuan DASAR barang terkunci sesudah barang punya riwayat stok / baris dokumen (putusan pemilik LANGSUNG 5 Nov, pola NetSuite
    units type): mengganti dasar = menafsir ulang seluruh riwayat. Sama (alias kamus) atau belum terpakai -> boleh; else 409."""
    if dasar_baru is None or not normal(dasar_lama):
        return
    alias = await _alias(conn, tid)
    if kanon(dasar_baru, alias) == kanon(dasar_lama, alias):
        return
    if await conn.fetchval(SQL_BARANG_TERPAKAI, product_id, tid):
        raise GalatBerkode(409, KODE_SATUAN_DASAR_TERPAKAI, tg.satuan_dasar_terpakai(dasar_lama))


# ------------------------------------------------------------------ faktor konversi DESIMAL (putusan pemilik LANGSUNG 5 Nov, pola Accurate)
FAKTOR_MAKS = Decimal("10000")
PESAN_FAKTOR = "Faktor konversi harus lebih dari 0, paling besar 10.000, dan paling banyak 6 angka desimal."


def faktor_sah(v) -> Decimal:
    """SATU validator faktor konversi (form barang + layar satuan): Decimal > 0, <= 10.000, <= 6 desimal. Salah -> ValueError(PESAN_FAKTOR)."""
    try:
        f = v if isinstance(v, Decimal) else Decimal(str(v))
    except Exception:
        raise ValueError(PESAN_FAKTOR)
    if isinstance(v, bool) or not f.is_finite() or f <= 0 or f > FAKTOR_MAKS or f.as_tuple().exponent < -6:
        raise ValueError(PESAN_FAKTOR)
    return f.normalize() if f == f.to_integral_value() else f


def faktor_json(v) -> float:
    """Bentuk BACA tunggal faktor (get/list/detail): angka JSON (float), bukan campuran int/float."""
    return float(v)
