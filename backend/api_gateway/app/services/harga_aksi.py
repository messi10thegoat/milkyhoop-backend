"""DAFTAR HARGA D2 (putusan pemilik 13 Nov 2026; PETA-be-daftar-harga-20261113): SATU penentu pratinjau + penulis untuk aksi tulis daftar harga --
nonaktifkan/aktifkan, jadikan bawaan, hapus, ubah header, hapus baris -- plus penjaga barang TIDAK DIJUAL dan idempotensi tulis.

Pratinjau = {ok, blocks, peringatan, rencana} dari rencana_* (fungsi murni di atas hasil ukur_*), READ ONLY. Penulis memakai rencana_* YANG SAMA
(kalimat blok == galat tulis) lalu terapkan_*. Putusan: hapus daftar yang dipakai pelanggan DITOLAK menyebut N pelanggan; nonaktifkan BOLEH
(peringatan: pelanggan beralih ke daftar bawaan / harga barang). Tanpa jurnal (bukan transaksi uang)."""
from typing import Optional

from .galat_berkode import GalatBerkode

KOLOM_UBAH = ("code", "name", "start_date", "end_date", "priority", "description")


def _blok(code: str, message: str, **kw) -> dict:
    return {"code": code, "message": message, **kw}


def _hasil(blocks: list, rencana, peringatan=None) -> dict:
    return {"ok": not blocks, "blocks": blocks, "peringatan": peringatan or [], "rencana": rencana}


def _tak_ada():
    return _hasil([_blok("DH_TAK_ADA", "Daftar harga tidak ditemukan.")], None)


# --- ukur (bersaring tenant) -----------------------------------------------------------------------------------------------------------
async def ukur_daftar(conn, tid: str, pl_id):
    return await conn.fetchrow(
        """SELECT id, code, name, is_active, is_default, start_date, end_date, priority, description FROM price_lists
           WHERE id = $1 AND tenant_id = $2""", pl_id, tid)


async def ukur_pelanggan(conn, tid: str, pl_id) -> int:
    return int(await conn.fetchval("SELECT COUNT(*) FROM customers WHERE tenant_id = $1 AND price_list_id = $2", tid, pl_id) or 0)


async def ukur_baris(conn, tid: str, pl_id) -> int:
    return int(await conn.fetchval(
        """SELECT COUNT(*) FROM price_list_items pli JOIN price_lists pl ON pl.id = pli.price_list_id
           WHERE pl.tenant_id = $1 AND pli.price_list_id = $2""", tid, pl_id) or 0)


async def ukur_bawaan(conn, tid: str):
    return await conn.fetchrow(
        "SELECT id, code, name, is_active, is_default FROM price_lists WHERE tenant_id = $1 AND is_default AND is_active", tid)


async def ukur_baris_harga(conn, tid: str, pl_id, line_id):
    return await conn.fetchrow(
        """SELECT pli.id, pli.unit, pli.price, pli.min_quantity, p.nama_produk AS item_name,
                  COALESCE(NULLIF(p.base_unit, ''), p.satuan) AS dasar
           FROM price_list_items pli JOIN price_lists pl ON pl.id = pli.price_list_id
           LEFT JOIN products p ON p.id = pli.item_id AND p.tenant_id = pl.tenant_id
           WHERE pli.id = $1 AND pli.price_list_id = $2 AND pl.tenant_id = $3""", line_id, pl_id, tid)


async def kode_dipakai(conn, tid: str, kode: str, pl_id) -> bool:
    return bool(await conn.fetchval("SELECT 1 FROM price_lists WHERE tenant_id = $1 AND code = $2 AND id <> $3", tid, kode, pl_id))


# --- rencana (murni) -------------------------------------------------------------------------------------------------------------------
def rencana_status(r, n_pelanggan: int, ke_aktif: bool) -> dict:
    if not r:
        return _tak_ada()
    if bool(r["is_active"]) == ke_aktif:
        return _hasil([_blok("DH_SUDAH_AKTIF" if ke_aktif else "DH_SUDAH_NONAKTIF",
                             "Daftar ini sudah aktif." if ke_aktif else "Daftar ini sudah nonaktif.")], None)
    per = []
    if not ke_aktif:
        if n_pelanggan:
            per.append(_blok("DH_DIPAKAI_PELANGGAN", f"{n_pelanggan} pelanggan memakai daftar ini; harga mereka beralih ke daftar bawaan (atau harga barang)."))
        if r["is_default"]:
            per.append(_blok("DH_BAWAAN_DINONAKTIFKAN", "Ini daftar bawaan; sesudah nonaktif usaha tak punya daftar bawaan (harga memakai harga barang)."))
    return _hasil([], {"dari": "aktif" if r["is_active"] else "nonaktif", "ke": "aktif" if ke_aktif else "nonaktif", "pelanggan": n_pelanggan,
                       "bawaan_dicabut": bool(r["is_default"]) and not ke_aktif}, per)


def rencana_hapus(r, n_pelanggan: int, n_baris: int) -> dict:
    if not r:
        return _tak_ada()
    if n_pelanggan:
        return _hasil([_blok("DH_DIPAKAI_PELANGGAN", f"{r['name']} dipakai {n_pelanggan} pelanggan; pindahkan pelanggannya dulu atau pilih Nonaktifkan.")], None)
    per = [_blok("DH_BAWAAN_DIHAPUS", "Ini daftar bawaan; sesudah dihapus harga memakai harga barang.")] if r["is_default"] else []
    return _hasil([], {"jenis": "hapus_permanen", "baris_terhapus": n_baris}, per)


def rencana_bawaan(r, lama) -> dict:
    if not r:
        return _tak_ada()
    if r["is_default"] and r["is_active"]:
        return _hasil([_blok("DH_SUDAH_BAWAAN", "Daftar ini sudah bawaan.")], None)
    if not r["is_active"]:
        return _hasil([_blok("DH_NONAKTIF_BUKAN_BAWAAN", "Daftar nonaktif tidak bisa jadi bawaan.")], None)
    return _hasil([], {"bawaan_lama": lama["name"] if lama else None, "bawaan_baru": r["name"]})


def rencana_ubah(r, body: dict, kode_ganda: bool) -> dict:
    if not r:
        return _tak_ada()
    bloks = []
    if "name" in body and not " ".join(str(body["name"] or "").split()):
        bloks.append(_blok("NAMA_DAFTAR_KOSONG", "Nama daftar harga wajib diisi.", field="name"))
    if "code" in body and not " ".join(str(body["code"] or "").split()):
        bloks.append(_blok("KODE_DAFTAR_KOSONG", "Kode daftar harga wajib diisi.", field="code"))
    if kode_ganda:
        bloks.append(_blok("KODE_DAFTAR_GANDA", "Kode daftar harga itu sudah dipakai.", field="code"))
    mulai = body["start_date"] if "start_date" in body else r["start_date"]
    sampai = body["end_date"] if "end_date" in body else r["end_date"]
    if mulai and sampai and sampai < mulai:
        bloks.append(_blok("DH_MASA_BERLAKU", "Tanggal akhir tidak boleh sebelum tanggal mulai.", field="end_date"))
    per = []
    for k in KOLOM_UBAH:
        if k in body:
            ke = " ".join(str(body[k]).split()) if k in ("code", "name") and body[k] is not None else body[k]
            if ke != r[k]:
                per.append({"medan": k, "dari": r[k], "ke": ke})
    return _hasil(bloks, None if bloks else {"perubahan": per})


def rencana_hapus_baris(b) -> dict:
    if not b:
        return _hasil([_blok("DH_BARIS_TAK_ADA", "Harga itu tidak ditemukan di daftar ini.")], None)
    return _hasil([], {"barang": b["item_name"], "satuan": b["unit"] or b["dasar"], "min_quantity": float(b["min_quantity"]), "harga": b["price"]})


def tolak_bila_blok(x: dict) -> None:
    """Penulis: blok pertama rencana -> galat berkode (404 bila tak ada, selain itu 409) -- kalimat sama dengan pratinjau."""
    if x["blocks"]:
        b = x["blocks"][0]
        raise GalatBerkode(404 if b["code"] == "DH_TAK_ADA" else 409, b["code"], b["message"], field=b.get("field"))


async def periksa_barang_dijual(conn, tid: str, pid) -> None:
    """Barang ditandai tidak dijual / nonaktif tak boleh diberi harga jual baru (DH_BARANG_TIDAK_DIJUAL); barang tak ada = urusan penentu lain."""
    b = await conn.fetchrow("SELECT nama_produk, status, for_sales FROM products WHERE id = $1 AND tenant_id = $2", pid, tid)
    if b and (b["status"] != "active" or b["for_sales"] is False):
        raise GalatBerkode(400, "DH_BARANG_TIDAK_DIJUAL",
                           f"{b['nama_produk']} ditandai tidak dijual atau nonaktif; harga jualnya tidak bisa disiapkan.", field="item_id")


# --- penulis ---------------------------------------------------------------------------------------------------------------------------
async def terapkan_status(conn, tid: str, pl_id, ke_aktif: bool) -> None:
    if ke_aktif:
        await conn.execute("UPDATE price_lists SET is_active = true, updated_at = NOW() WHERE id = $1 AND tenant_id = $2", pl_id, tid)
    else:
        await conn.execute("UPDATE price_lists SET is_active = false, is_default = false, updated_at = NOW() WHERE id = $1 AND tenant_id = $2", pl_id, tid)


async def terapkan_bawaan(conn, tid: str, pl_id) -> None:
    await conn.execute("UPDATE price_lists SET is_default = false, updated_at = NOW() WHERE tenant_id = $1 AND is_default AND id <> $2", tid, pl_id)
    await conn.execute("UPDATE price_lists SET is_default = true, updated_at = NOW() WHERE id = $2 AND tenant_id = $1", tid, pl_id)


async def terapkan_hapus(conn, tid: str, pl_id) -> None:
    """Hapus permanen: baris harga lalu daftar. Penjaga pelanggan = rencana_hapus (dipanggil pemanggil di transaksi yang sama)."""
    await conn.execute("DELETE FROM price_list_items WHERE price_list_id IN (SELECT id FROM price_lists WHERE id = $1 AND tenant_id = $2)", pl_id, tid)
    await conn.execute("DELETE FROM price_lists WHERE id = $1 AND tenant_id = $2", pl_id, tid)


async def idem_aksi(conn, ctx: dict, request, prefix: str, doc_id, isi, kerja, response=None):
    """Idempotensi tulis (X-Idempotency-Key opsional): kunci sama + isi sama = respons asli, isi beda = 409. WAJIB di dalam transaksi."""
    from . import idem_buat
    kp, sd, lama = await idem_buat.mulai_aksi(conn, ctx, idem_buat.kunci_dari(request), prefix, doc_id, isi, response)
    if lama is not None:
        return lama
    resp = await kerja()
    return await idem_buat.simpan(conn, ctx, kp, sd, prefix, resp, doc_id)
