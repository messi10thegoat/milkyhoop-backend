"""Aksi Barang & Jasa standar SO (kontrak KONTRAK-barang-standar-so-20261113; putusan pemilik 13 Nov 2026): SATU penentu untuk pratinjau
dan tulis -- keadaan aktif/nonaktif + teks server, nonaktif/aktifkan (satu + massal), hapus (rincian jejak), duplikat, hapus kategori.
Fungsi rencana_* MURNI (tanpa DB; uji unit); ukur_* hanya SELECT (dipanggil di transaksi READ ONLY untuk pratinjau, dan oleh penulis).
Nonaktif barang ber-stok / pesanan terbuka BOLEH dengan PERINGATAN (stok & nilai tetap tercatat, tanpa jurnal)."""
from decimal import Decimal
from typing import Optional

from .barang_daftar import _d, _q, aksi_tersedia, dilacak

# SATU daftar jejak: penulis DELETE /items/{id} (smart_delete) dan pratinjau hapus memakai daftar yang sama.
JEJAK_BARANG = [
    ("inventory_ledger", "product_id", None),
    ("sales_invoice_items", "item_id", None),
    ("bill_items", "product_id", None),
    ("stock_adjustment_items", "product_id", None),
    ("stock_transfer_items", "item_id", None),
    ("production_orders", "product_id", None),
    ("recipes", "product_id", None),
]
SO_TERBUKA = ("draft", "confirmed", "partial_shipped", "shipped", "partial_invoiced", "invoiced")   # belum completed/cancelled
PO_TERBUKA = ("draft", "sent", "partial_received", "received", "partial_billed", "billed")         # belum closed/cancelled


def keadaan(b: dict) -> str:
    return "aktif" if b.get("status", "active") == "active" else "nonaktif"


def keadaan_text(b: dict) -> str:
    return "Aktif" if keadaan(b) == "aktif" else "Nonaktif"


def aksi_tersedia_detail(b: dict) -> dict:
    """Detail barang: aksi_tersedia standar + hapus dikoreksi dgn jejak (b['transaction_count'] = hitung 7 sumber JEJAK_BARANG di kueri detail)."""
    t = aksi_tersedia(b)
    if int(b.get("transaction_count") or 0) > 0:
        t["hapus"] = {"tersedia": False, "alasan": "Barang sudah punya transaksi; gunakan Nonaktifkan."}
    return t


def rp(x) -> str:
    return "Rp " + f"{int(_d(x).quantize(Decimal('1'))):,}".replace(",", ".")


def _blok(code: str, message: str, **kw) -> dict:
    return {"code": code, "message": message, **kw}


def _hasil(blocks: list, rencana, peringatan=None) -> dict:
    return {"ok": not blocks, "blocks": blocks, "peringatan": peringatan or [], "rencana": rencana}


def rencana_status(b: Optional[dict], stok, nilai, pesanan_terbuka: int, ke_aktif: bool, satuan: Optional[str] = None) -> dict:
    if not b:
        return _hasil([_blok("BARANG_TAK_ADA", "Barang tidak ditemukan.")], None)
    nama, dari = b.get("nama_produk") or b.get("name") or "Barang", keadaan(b)
    ke = "aktif" if ke_aktif else "nonaktif"
    if dari == ke:
        return _hasil([_blok("BARANG_SUDAH_AKTIF" if ke_aktif else "BARANG_SUDAH_NONAKTIF",
                             f"{nama} sudah {ke}.", id=str(b.get("id")))], None)
    per = []
    s = _d(stok)
    if not ke_aktif:
        if dilacak(b) and s != 0:
            sat = f" {satuan}" if satuan else ""
            per.append(_blok("BARANG_MASIH_BERSTOK", f"Stok {_q(s)}{sat} senilai {rp(nilai)} tetap tercatat; barang hilang dari pilihan transaksi baru.",
                             id=str(b.get("id"))))
        if pesanan_terbuka:
            per.append(_blok("PESANAN_TERBUKA", f"{pesanan_terbuka} pesanan terbuka masih memuat barang ini.", id=str(b.get("id"))))
    rencana = {"id": str(b.get("id")), "nama": nama, "dari": dari, "ke": ke, "stok": str(s), "nilai_stok": str(_d(nilai)),
               "pesanan_terbuka": pesanan_terbuka, "jurnal": False}
    return _hasil([], rencana, per)


def gabung_status(rencana: list) -> dict:
    if not rencana:
        return {"ok": False, "blocks": [_blok("DAFTAR_KOSONG", "Pilih minimal satu barang.")], "peringatan": [], "items": [], "ringkasan": None}
    diblokir = sum(1 for r in rencana if r["blocks"])
    per = sum(1 for r in rencana if r["peringatan"])
    return {"ok": diblokir == 0, "blocks": [], "items": rencana,
            "peringatan": [p for r in rencana for p in r["peringatan"]],
            "ringkasan": {"jumlah": len(rencana), "dapat_diubah": len(rencana) - diblokir, "diblokir": diblokir, "peringatan": per}}


def rencana_hapus(b: Optional[dict], jejak: list) -> dict:
    if not b:
        return _hasil([_blok("BARANG_TAK_ADA", "Barang tidak ditemukan.")], None)
    nama = b.get("nama_produk") or b.get("name") or "Barang"
    rinci = [{"sumber": s, "jumlah": int(n)} for s, n in jejak if n]
    if rinci:
        return _hasil([_blok("BARANG_BERJEJAK", f"{nama} tidak bisa dihapus karena masih memiliki transaksi. Gunakan Nonaktifkan.", id=str(b.get("id")))],
                      {"jenis": "tak_bisa", "jejak": rinci, "saran": "Nonaktifkan"})
    return _hasil([], {"jenis": "hapus_permanen", "jejak": [], "saran": None})


def rencana_kategori_hapus(nama: str, dipakai: int, ada: bool) -> dict:
    if not ada:
        return _hasil([_blok("KATEGORI_TAK_ADA", f"Kategori '{nama}' tidak ditemukan.")], None)
    r = {"nama": nama, "jumlah_barang_terpakai": int(dipakai)}
    if dipakai:
        return _hasil([_blok("KATEGORI_DIPAKAI", f"Kategori '{nama}' dipakai {dipakai} barang aktif; pindahkan barangnya dulu.")], r)
    return _hasil([], r)


def rencana_duplikat(b: Optional[dict], nama_baru: str, kode_baru: Optional[str], n_konversi: int, n_harga: int) -> dict:
    if not b:
        return _hasil([_blok("BARANG_TAK_ADA", "Barang tidak ditemukan.")], None)
    disalin = ["data barang"]
    if n_konversi:
        disalin.append(f"konversi satuan ({n_konversi})")
    if n_harga:
        disalin.append(f"harga bertingkat ({n_harga})")
    return _hasil([], {"nama_baru": nama_baru, "kode_baru": kode_baru, "yang_disalin": disalin,
                       "yang_tidak_disalin": ["stok", "saldo awal", "batch", "riwayat"]})


# --- ukur (SELECT saja) --------------------------------------------------------------------------------------------------
async def ukur_stok(conn, tenant_id: str, product_id: str):
    """(stok, nilai) dari inventory_ledger (Law 16) -- sama dengan BD.SQL_PER_STOK."""
    r = await conn.fetchrow(
        """SELECT COALESCE(SUM(quantity_in) - SUM(quantity_out), 0) AS jumlah,
                  COALESCE(SUM(total_cost * SIGN(quantity_in - quantity_out)), 0) AS nilai
           FROM inventory_ledger WHERE product_id = $1::uuid AND tenant_id = $2""", product_id, tenant_id)
    return r["jumlah"], r["nilai"]


async def ukur_pesanan_terbuka(conn, tenant_id: str, product_id: str) -> int:
    so = await conn.fetchval(
        """SELECT COUNT(DISTINCT so.id) FROM sales_order_items soi JOIN sales_orders so ON so.id = soi.sales_order_id
           WHERE soi.item_id = $1::uuid AND so.tenant_id = $2 AND so.status = ANY($3::text[])""", product_id, tenant_id, list(SO_TERBUKA))
    po = await conn.fetchval(
        """SELECT COUNT(DISTINCT po.id) FROM purchase_order_items poi JOIN purchase_orders po ON po.id = poi.po_id
           WHERE poi.item_id = $1::uuid AND po.tenant_id = $2 AND po.status = ANY($3::text[])""", product_id, tenant_id, list(PO_TERBUKA))
    return int(so or 0) + int(po or 0)


async def ukur_barang(conn, tenant_id: str, product_id: str) -> Optional[dict]:
    r = await conn.fetchrow(
        "SELECT * FROM products WHERE id = $1::uuid AND tenant_id = $2 AND deleted_at IS NULL", product_id, tenant_id)
    return dict(r) if r else None


async def ukur_rencana_status(conn, tenant_id: str, product_id: str, ke_aktif: bool) -> dict:
    b = await ukur_barang(conn, tenant_id, product_id)
    if not b:
        return rencana_status(None, 0, 0, 0, ke_aktif)
    stok, nilai = await ukur_stok(conn, tenant_id, product_id)
    return rencana_status(b, stok, nilai, await ukur_pesanan_terbuka(conn, tenant_id, product_id), ke_aktif,
                          b.get("base_unit") or b.get("satuan"))


async def ukur_jejak(conn, product_id: str) -> list:
    hasil = []
    for tabel, kolom, _ in JEJAK_BARANG:
        n = await conn.fetchval(f"SELECT COUNT(*) FROM {tabel} WHERE {kolom}::uuid = $1::uuid", product_id)
        hasil.append((tabel, n or 0))
    return hasil


async def ukur_kategori(conn, tenant_id: str, nama: str):
    """(ada, dipakai) -- dipakai = barang AKTIF berkategori itu (aturan sama dgn DELETE /items/categories)."""
    ada = await conn.fetchval("SELECT 1 FROM item_categories WHERE tenant_id = $1 AND lower(name) = lower($2)", tenant_id, nama)
    n = await conn.fetchval("SELECT count(*) FROM products WHERE tenant_id = $1 AND lower(kategori) = lower($2) AND status = 'active'", tenant_id, nama)
    return bool(ada), int(n or 0)
