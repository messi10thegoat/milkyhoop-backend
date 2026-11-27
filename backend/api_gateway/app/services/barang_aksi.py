"""Aksi Barang & Jasa standar SO (kontrak KONTRAK-barang-standar-so-20261113; putusan pemilik 13 Nov 2026): SATU penentu untuk pratinjau
dan tulis -- keadaan aktif/nonaktif + teks server, nonaktif/aktifkan (satu + massal), hapus (rincian jejak), duplikat, hapus kategori.
Fungsi rencana_* MURNI (tanpa DB; uji unit); ukur_* hanya SELECT (dipanggil di transaksi READ ONLY untuk pratinjau, dan oleh penulis).
Nonaktif barang ber-stok / pesanan terbuka BOLEH dengan PERINGATAN (stok & nilai tetap tercatat, tanpa jurnal)."""
from decimal import Decimal
from typing import Optional

from .barang_daftar import _d, _q, aksi_tersedia, dilacak

# SATU daftar jejak: penulis DELETE /items/{id} (smart_delete) dan pratinjau hapus memakai daftar yang sama.
JEJAK_BARANG = [
    # (tabel, kolom barang, None) -- BUG U3-3 (25 Nov 2026): SEMUA rujukan ke products (diukur dari information_schema live): FK NO ACTION + tabel tanpa FK.
    ("inventory_ledger", "product_id", None),
    ("sales_invoice_items", "item_id", None),
    ("sales_receipt_items", "item_id", None),
    ("sales_order_items", "item_id", None),
    ("quote_items", "item_id", None),
    ("credit_note_items", "item_id", None),
    ("invoice_fulfillment_items", "product_id", None),
    ("bill_items", "product_id", None),
    ("purchase_order_items", "item_id", None),
    ("goods_receipt_items", "item_id", None),
    ("vendor_credit_items", "item_id", None),
    ("recurring_bill_items", "item_id", None),
    ("stock_adjustment_items", "product_id", None),
    ("stock_opname_lines", "product_id", None),
    ("stock_transfer_items", "item_id", None),
    ("branch_transfer_lines", "product_id", None),
    ("item_batches", "item_id", None),
    ("item_serials", "item_id", None),
    ("price_list_items", "item_id", None),
    ("journal_lines", "item_id", None),
    ("production_orders", "product_id", None),
    ("production_order_materials", "product_id", None),
    ("bill_of_materials", "product_id", None),
    ("bom_components", "component_product_id", None),
    ("bom_substitutes", "substitute_product_id", None),
    ("recipes", "product_id", None),
    ("recipe_ingredients", "ingredient_product_id", None),
    ("standard_costs", "product_id", None),
    ("cost_variances", "product_id", None),
    ("kds_order_items", "product_id", None),
    ("kds_item_history", "product_id", None),
    ("products", "matrix_parent_id", None),
]
# Teks ramah per sumber jejak (server; FE tak membuat peta label). Tabel tak berlabel -> nama generik, bukan nama tabel mentah.
LABEL_JEJAK = {
    "inventory_ledger": "Kartu stok", "sales_invoice_items": "Faktur penjualan", "sales_receipt_items": "Penjualan tunai", "sales_order_items": "Pesanan penjualan",
    "quote_items": "Penawaran", "credit_note_items": "Nota kredit", "invoice_fulfillment_items": "Pengiriman barang", "bill_items": "Faktur pembelian",
    "purchase_order_items": "Pesanan pembelian", "goods_receipt_items": "Penerimaan barang", "vendor_credit_items": "Nota debit", "recurring_bill_items": "Tagihan berulang",
    "stock_adjustment_items": "Penyesuaian stok", "stock_opname_lines": "Stok opname", "stock_transfer_items": "Pemindahan barang", "branch_transfer_lines": "Transfer cabang",
    "item_batches": "Batch", "item_serials": "Nomor seri", "price_list_items": "Daftar harga", "journal_lines": "Jurnal", "production_orders": "Perintah produksi",
    "production_order_materials": "Bahan perintah produksi", "bill_of_materials": "Formula produksi (BOM)", "bom_components": "Komponen formula produksi",
    "bom_substitutes": "Pengganti komponen formula", "recipes": "Resep", "recipe_ingredients": "Bahan resep", "standard_costs": "Biaya standar",
    "cost_variances": "Selisih biaya", "kds_order_items": "Pesanan dapur", "kds_item_history": "Riwayat dapur", "products": "Varian barang",
}
SQL_HITUNG_JEJAK = "SELECT COUNT(*) FROM (" + " UNION ALL ".join(f"SELECT 1 FROM {t} WHERE {c} = p.id" for t, c, _ in JEJAK_BARANG) + ") txns"   # detail satu barang; sama dgn JEJAK_BARANG


def label_jejak(tabel: str) -> str:
    return LABEL_JEJAK.get(tabel, "Transaksi lain")


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


def rencana_hapus(b: Optional[dict], jejak: list) -> dict:
    if not b:
        return _hasil([_blok("BARANG_TAK_ADA", "Barang tidak ditemukan.")], None)
    nama = b.get("nama_produk") or b.get("name") or "Barang"
    rinci = [{"sumber": s, "sumber_text": label_jejak(s), "jumlah": int(n)} for s, n in jejak if n]
    if rinci:
        label = list(dict.fromkeys(r["sumber_text"] for r in rinci))
        sebut = ", ".join(label[:3]) + (f" dan {len(label) - 3} lainnya" if len(label) > 3 else "")
        teks = f"{nama} sudah dipakai di {sebut}, jadi tidak bisa dihapus. Gunakan Nonaktifkan."
        return _hasil([_blok("BARANG_BERJEJAK", teks, id=str(b.get("id")))],
                      {"jenis": "tak_bisa", "jejak": rinci, "saran": "Nonaktifkan", "teks": teks})
    return _hasil([], {"jenis": "hapus_permanen", "jejak": [], "saran": None,
                       "teks": f"{nama} belum pernah dipakai. Menghapus bersifat permanen dan tidak bisa dibatalkan."})


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


async def terapkan_status(conn, tenant_id: str, product_id: str, ke_aktif: bool, actor_id=None, actor_name: Optional[str] = None) -> None:
    """TULIS status barang + satu baris item_activities (riwayat /items/{id}/activity). Di transaksi PEMANGGIL; penulis tunggal dan massal
    memakai fungsi ini (tanpa jurnal, stok/nilai tak disentuh). Penentu boleh/tidak = rencana_status (pemanggil)."""
    await conn.execute("UPDATE products SET status = $1, updated_at = NOW() WHERE id = $2::uuid AND tenant_id = $3",
                       "active" if ke_aktif else "inactive", product_id, tenant_id)
    await conn.execute(
        """INSERT INTO item_activities (item_id, tenant_id, type, description, actor_id, actor_name)
           VALUES ($1::uuid, $2, $3, $4, $5::uuid, $6)""",
        product_id, tenant_id, "activated" if ke_aktif else "deactivated",
        "Barang diaktifkan" if ke_aktif else "Barang dinonaktifkan", str(actor_id) if actor_id else None, actor_name)


_PETA_UBAH = {"name": "nama_produk", "sales_price": "sales_price", "purchase_price": "purchase_price", "base_unit": "base_unit",
              "reorder_level": "reorder_level", "item_type": "item_type", "track_inventory": "track_inventory", "bisa_dikirim": "bisa_dikirim",
              "kategori": "kategori", "deskripsi": "deskripsi", "barcode": "barcode", "is_returnable": "is_returnable",
              "sales_tax": "sales_tax", "purchase_tax": "purchase_tax",
              # FORM BARANG CW (Lanjutan): akun/pajak/merek ikut pratinjau
              "sales_account_id": "sales_account_id", "purchase_account_id": "purchase_account_id", "inventory_account_id": "inventory_account_id",
              "cogs_account_id": "cogs_account_id", "sales_tax_id": "sales_tax_id", "purchase_tax_id": "purchase_tax_id", "merek": "merek"}
LABEL_MEDAN = {"name": "Nama", "sales_price": "Harga jual", "purchase_price": "Harga beli", "base_unit": "Satuan dasar", "reorder_level": "Titik pesan ulang",
               "item_type": "Jenis", "track_inventory": "Lacak stok", "bisa_dikirim": "Bisa dikirim", "kategori": "Kategori", "deskripsi": "Deskripsi", "barcode": "Barcode",
               "is_returnable": "Bisa diretur", "sales_tax": "Pajak jual", "purchase_tax": "Pajak beli", "sales_account_id": "Akun penjualan",
               "purchase_account_id": "Akun pembelian", "inventory_account_id": "Akun persediaan", "cogs_account_id": "Akun HPP", "sales_tax_id": "Pajak jual",
               "purchase_tax_id": "Pajak beli", "merek": "Merek"}
MEDAN_AKUN = ("sales_account_id", "purchase_account_id", "inventory_account_id", "cogs_account_id")
MEDAN_PAJAK = ("sales_tax_id", "purchase_tax_id")
TEKS_KEDEPAN = "Akun dan pajak yang diubah hanya berlaku untuk transaksi berikutnya; jurnal dan dokumen yang sudah ada tidak berubah."


def _s(v) -> str:
    if v is None:
        return ""
    if isinstance(v, Decimal):
        t = f"{v:f}"
        return t.rstrip("0").rstrip(".") if "." in t else t
    return str(int(v)) if isinstance(v, float) and v == int(v) else str(v)


def ids_perubahan(r: dict) -> list:
    """id akun/pajak (dari + ke) pada rencana ubah -- bahan pencarian nama oleh router."""
    if not r.get("rencana"):
        return []
    return [v for p in r["rencana"]["perubahan"] if p["medan"] in MEDAN_AKUN + MEDAN_PAJAK for v in (p["dari"], p["ke"]) if v]


def beri_teks_ubah(r: dict, nama: dict) -> dict:
    """FORM BARANG CW: tiap perubahan + medan_text / dari_text / ke_text (id akun/pajak -> 'kode nama'; boolean -> Ya/Tidak; kosong -> '(kosong)'), + peringatan bila akun/pajak berubah.
    nama = {id: teks}. Mengubah di tempat; hanya penyajian (angka & aturan tetap dari rencana_ubah)."""
    if not r.get("rencana"):
        return r
    ada_akun_pajak = False
    for p in r["rencana"]["perubahan"]:
        m = p["medan"]
        p["medan_text"] = LABEL_MEDAN.get(m, m)
        for k in ("dari", "ke"):
            v = p[k]
            if m in MEDAN_AKUN + MEDAN_PAJAK:
                p[f"{k}_text"] = nama.get(v) or v or TEKS_KOSONG
                ada_akun_pajak = True
            elif v in ("True", "False"):
                p[f"{k}_text"] = "Ya" if v == "True" else "Tidak"
            else:
                p[f"{k}_text"] = v or TEKS_KOSONG   # BUG-381: "Manado → (kosong)"
    if ada_akun_pajak:
        r["peringatan"].append(_blok("AKUN_PAJAK_BERLAKU_KEDEPAN", TEKS_KEDEPAN))
    return r


# BUG-381: medan opsional yang BOLEH dikosongkan lewat PUT/pratinjau (null atau "" -> NULL). Harga & nama TIDAK boleh (422).
BOLEH_KOSONG = ("barcode", "kategori", "merek", "deskripsi", "reorder_level")
WAJIB_ISI = {"name": "Nama barang", "sales_price": "Harga jual", "purchase_price": "Harga beli"}
TEKS_KOSONG = "(kosong)"


def normal_kosong(baru: dict) -> dict:
    """SATU penentu PUT + edit/preview: null/"" pada BOLEH_KOSONG = kosongkan (None); null pada WAJIB_ISI = 422; null lain = tak diubah (dibuang)."""
    from fastapi import HTTPException
    hasil = {}
    for k, v in baru.items():
        if isinstance(v, str) and not v.strip() and k in BOLEH_KOSONG:
            v = None
        if v is None:
            if k in WAJIB_ISI:
                raise HTTPException(status_code=422, detail=f"{WAJIB_ISI[k]} tidak boleh dikosongkan.")
            if k not in BOLEH_KOSONG:
                continue
        hasil[k] = v
    return hasil


def rencana_ubah(lama: Optional[dict], baru: dict) -> dict:
    """Pratinjau UBAH barang: daftar perubahan {medan, dari, ke} (hanya medan terkirim yang BERBEDA). Blok penghalang datang dari penentu
    tulis (_periksa_ubah/_periksa_konversi di router), bukan di sini. Tanpa perubahan = peringatan TANPA_PERUBAHAN (bukan blok)."""
    if not lama:
        return _hasil([_blok("BARANG_TAK_ADA", "Barang tidak ditemukan.")], None)
    perubahan = []
    for medan, kolom in _PETA_UBAH.items():
        if medan not in baru or (baru[medan] is None and medan not in BOLEH_KOSONG):
            continue
        dari = lama.get(kolom)
        if medan == "reorder_level" and not dari:   # 0 = belum diisi
            dari = None
        if _s(baru[medan]) != _s(dari):
            perubahan.append({"medan": medan, "dari": _s(dari), "ke": _s(baru[medan])})
    per = [] if perubahan else [_blok("TANPA_PERUBAHAN", "Tidak ada perubahan pada data barang.")]
    return _hasil([], {"perubahan": perubahan, "efek": []}, per)


def _uuid_atau_none(x):
    from uuid import UUID
    try:
        return x if isinstance(x, UUID) else UUID(str(x))
    except (ValueError, TypeError, AttributeError):
        return None


async def idem_aksi(conn, ctx: dict, request, prefix: str, doc_id, isi, kerja, response=None):
    """Idempotensi tulis barang (X-Idempotency-Key opsional): kunci sama + isi sama = respons asli (replay), isi beda = 409. WAJIB di dalam
    transaksi. kerja() -> respons (dict); disimpan di transaksi yang sama bila berkunci."""
    from . import idem_buat
    kp, sd, _lama = await idem_buat.mulai_aksi(conn, ctx, idem_buat.kunci_dari(request), prefix, doc_id, isi, response)
    if _lama is not None:
        return _lama
    resp = await kerja()
    # BUG-407: result_id = kolom uuid -> doc_id teks (nama kategori, "id:ke", ...) JANGAN dikirim; ganti id di respons bila UUID, selain itu None
    data = resp.get("data") if isinstance(resp, dict) else None
    rid = _uuid_atau_none(doc_id) or _uuid_atau_none(data.get("id") if isinstance(data, dict) else None)
    return await idem_buat.simpan(conn, ctx, kp, sd, prefix, resp, rid)


async def catat_hapus(conn, tenant_id: str, product_id: str, nama: str, kode: Optional[str], user_id) -> None:
    """Audit hapus permanen barang (baris item_activities ikut terhapus bersama barang -> jejak di audit_logs)."""
    from .so_riwayat import catat_riwayat
    await catat_riwayat(conn, tenant_id, "products", product_id, kode, "ITEM_DELETED", user_id, f"Barang {nama} dihapus",
                        {"nama": nama}, source="api:items.delete")


async def catat_duplikat(conn, tenant_id: str, baru_id: str, sumber_id: str, nama_sumber: str, actor_id=None, actor_name: Optional[str] = None) -> None:
    await conn.execute(
        """INSERT INTO item_activities (item_id, tenant_id, type, description, actor_id, actor_name)
           VALUES ($1::uuid, $2, 'duplicated', $3, $4::uuid, $5)""",
        baru_id, tenant_id, f"Disalin dari {nama_sumber}", str(actor_id) if actor_id else None, actor_name)
