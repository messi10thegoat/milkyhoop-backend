"""BUG-277 (17 Nov 2026): EKSPOR BARANG xlsx/csv = kolom PERSIS templat Impor Barang (impor_barang.KOLOM) -> ekspor lalu impor ke usaha lain/ubah di Excel tanpa pemetaan ulang.
Murni (uji unit): baris_ekspor() + aman() + bangun_xlsx()/bangun_csv(); kueri SATU tempat (SQL_BARANG), berpagar tenant. Tanpa tulis, tanpa migrasi.
Aturan: satu baris = satu barang; konversi PERTAMA (faktor terkecil) -> kolom satuan besar, sisanya dihitung (dilewati, diberitahukan); Stok awal / HPP awal KOSONG
(ekspor = master barang, bukan saldo; saldo lewat Penyesuaian stok). Sel teks berawalan = + - @ TAB CR diberi ' di depan (cegah rumus Excel/Sheets menjalankan isi data)."""
import csv
import io
from decimal import Decimal
from typing import Optional

from . import impor_barang as IB

MAKS_BARIS = 20000
_AWAL_RUMUS = ("=", "+", "-", "@", "\t", "\r")
_TIPE_TEKS = {"goods": "barang", "non_inventory": "non stok", "service": "jasa"}

SQL_BARANG = """
SELECT p.id, p.item_code, p.nama_produk, p.item_type, p.base_unit, p.sales_price, p.purchase_price, p.kategori, p.barcode, p.status, p.deskripsi,
       COALESCE((SELECT SUM(il.quantity_in) - SUM(il.quantity_out) FROM inventory_ledger il WHERE il.product_id = p.id AND il.tenant_id = p.tenant_id), 0) AS stok_kini,
       COALESCE((SELECT json_agg(json_build_object('u', uc.conversion_unit, 'f', uc.conversion_factor, 'j', uc.sales_price, 'b', uc.purchase_price)
                 ORDER BY uc.conversion_factor, uc.conversion_unit)
                 FROM unit_conversions uc WHERE uc.product_id = p.id AND uc.tenant_id = p.tenant_id AND uc.is_active = true), '[]'::json) AS konversi
FROM products p
WHERE p.tenant_id = $1 AND p.deleted_at IS NULL
"""


def aman(v):
    """Teks berawalan pemicu rumus -> awalan '. Angka/None tak disentuh."""
    if isinstance(v, str) and v.startswith(_AWAL_RUMUS):
        return "'" + v
    return v


def _num(v) -> Optional[float]:
    if v is None or v == "":
        return None
    d = Decimal(str(v))
    return float(d)


def baris_ekspor(r: dict, konversi: list) -> tuple:
    """-> (baris sesuai urutan IB.KOLOM, jumlah konversi yang dilewati)."""
    k = konversi[0] if konversi else None
    nilai = {
        "nama": r["nama_produk"], "kode": r["item_code"], "tipe": _TIPE_TEKS.get(r["item_type"], r["item_type"]), "satuan_dasar": r["base_unit"],
        "harga_jual": _num(r["sales_price"]), "harga_beli": _num(r["purchase_price"]),
        "satuan_besar": k["u"] if k else None, "isi": _num(k["f"]) if k else None,
        "harga_jual_besar": _num(k["j"]) if k else None, "harga_beli_besar": _num(k["b"]) if k else None,
        "kategori": r["kategori"], "barcode": r["barcode"], "stok_awal": None, "harga_pokok_awal": None,
    }
    baris = [aman(nilai[kunci]) for kunci, _, _ in IB.KOLOM]
    baris += [stok_teks(r.get("stok_kini"), r["base_unit"], r["item_type"]), "Ya" if r["status"] == "active" else "Tidak", aman(r.get("deskripsi"))]
    return baris, max(0, len(konversi) - 1)


def siapkan(rows: list) -> tuple:
    """rows = baris SQL_BARANG -> (baris[], jumlah barang yang punya >1 konversi, total konversi dilewati)."""
    out, banyak, lewat = [], 0, 0
    for r in rows:
        kv = r["konversi"]
        if isinstance(kv, str):
            import json
            kv = json.loads(kv)
        b, l = baris_ekspor(r, kv or [])
        out.append(b)
        banyak += 1 if l else 0
        lewat += l
    return out, banyak, lewat


EKSTRA = ["Stok kini (satuan dasar)", "Aktif", "Deskripsi"]      # informatif; tak dikenal pembaca impor -> disebut "tidak dipakai", tak mengganggu


def judul() -> list:
    return [j for _, j, _ in IB.KOLOM] + EKSTRA


def stok_teks(qty, satuan, tipe) -> Optional[str]:
    if tipe != "goods" or qty is None:
        return None      # jasa/non stok tak punya stok
    d = Decimal(str(qty)).normalize()
    return f"{format(d, 'f')} {satuan or ''}".strip()


def bangun_csv(baris: list) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(judul())
    for b in baris:
        w.writerow(["" if v is None else (format(v, "f").rstrip("0").rstrip(".") if isinstance(v, float) else v) for v in b])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")      # BOM: Excel Windows membaca UTF-8


def bangun_xlsx(baris: list, banyak: int, lewat: int) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = "Barang"
    ws.append(judul())
    for c in ws[1]:
        c.font = Font(bold=True)
    for b in baris:
        ws.append(b)
    p = wb.create_sheet("Petunjuk")
    for t in ["14 kolom pertama sama dengan templat Impor Barang: berkas ini bisa diimpor lagi (ke usaha lain) tanpa pemetaan ulang. Kolom Stok kini, Aktif, Deskripsi hanya informasi dan diabaikan saat impor.",
              "Stok awal dan Harga pokok awal sengaja kosong: ekspor ini master barang, bukan saldo stok.",
              "Bila satu barang punya beberapa satuan besar, hanya yang pertama (isi terkecil) yang ikut."
              + (f" Pada ekspor ini {banyak} barang punya satuan besar lain yang tidak ikut ({lewat} satuan)." if lewat else "")]:
        p.append([t])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
