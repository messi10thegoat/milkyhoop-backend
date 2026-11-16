"""K-2 (17 Nov 2026; putusan pemilik 14 Nov, V498): aksi master KATEGORI barang -- SATU penentu untuk pratinjau dan tulis: buat, UBAH NAMA, nonaktifkan/aktifkan (+ audit/riwayat).

rencana_* MURNI (tanpa DB; uji unit) -> {ok, blocks[{code, message}], peringatan[], rencana}; ukur_* hanya SELECT (pratinjau di transaksi READ ONLY dan penulis); tulis_* di transaksi PEMANGGIL.
Putusan pemilik: UBAH NAMA mengubah label di SEMUA barangnya (peringatan N barang; atomik: registry lebih dulu lalu products.kategori -- trigger register_item_category V257 melihat nama baru
sudah terdaftar); NONAKTIF kategori ber-barang aktif = PERINGATAN, tetap boleh (barang lama tetap membawa label; hanya tak jadi pilihan baru); kembar nama beda huruf = blok KATEGORI_GANDA (K5);
kategori BAWAAN tak boleh dinonaktifkan (dijaga DB: bawaan harus aktif). products.kategori TETAP teks bebas. Audit: audit_logs (entity_type item_category), append-only."""
import json
from typing import Optional

from .barang_aksi import _blok, _hasil

NAMA_MAKS = 100
EVENT = {"buat": "ITEM_CATEGORY_CREATED", "ubah_nama": "ITEM_CATEGORY_RENAMED", "nonaktif": "ITEM_CATEGORY_DEACTIVATED", "aktif": "ITEM_CATEGORY_ACTIVATED",
         "hapus": "ITEM_CATEGORY_DELETED"}
SUMBER_AUDIT = "api:item-kategori"

SQL_UKUR = ("SELECT c.id, c.name, c.is_active, c.is_default, c.parent_id, c.description, c.created_at, c.updated_at, "
            "(SELECT COUNT(*) FROM products p WHERE p.tenant_id = c.tenant_id AND lower(p.kategori) = lower(c.name) AND p.deleted_at IS NULL) AS jumlah_barang, "
            "(SELECT COUNT(*) FROM products p WHERE p.tenant_id = c.tenant_id AND lower(p.kategori) = lower(c.name) AND p.deleted_at IS NULL AND p.status = 'active') AS jumlah_aktif "
            "FROM item_categories c WHERE c.tenant_id = $1 AND c.id = $2")
SQL_BENTROK = "SELECT id FROM item_categories WHERE tenant_id = $1 AND lower(name) = lower($2) AND ($3::uuid IS NULL OR id <> $3)"


def _nama_sah(nama) -> tuple:
    n = " ".join(str(nama or "").split())
    if not n:
        return n, _blok("NAMA_KOSONG", "Nama kategori wajib diisi.")
    if len(n) > NAMA_MAKS:
        return n, _blok("NAMA_TERLALU_PANJANG", f"Nama kategori maksimal {NAMA_MAKS} karakter.")
    return n, None


def rencana_buat(nama, ada: bool) -> dict:
    n, bl = _nama_sah(nama)
    if bl:
        return _hasil([bl], None)
    if ada:
        return _hasil([_blok("KATEGORI_GANDA", f"Kategori '{n}' sudah ada (huruf besar/kecil tidak dibedakan).")], None)
    return _hasil([], {"nama": n})


def rencana_ubah_nama(k: Optional[dict], nama_baru, bentrok: bool) -> dict:
    """k = baris ukur_kategori (atau None). bentrok = kategori LAIN bernama sama (tanpa peka huruf)."""
    if not k:
        return _hasil([_blok("KATEGORI_TAK_ADA", "Kategori tidak ditemukan.")], None)
    n, bl = _nama_sah(nama_baru)
    if bl:
        return _hasil([bl], None)
    if n == k["name"]:
        return _hasil([_blok("NAMA_TIDAK_BERUBAH", f"Nama kategori sudah '{n}'; tidak ada yang diubah.")], None)
    if bentrok:
        return _hasil([_blok("KATEGORI_GANDA", f"Kategori '{n}' sudah ada (huruf besar/kecil tidak dibedakan).")], None)
    jb = int(k["jumlah_barang"])
    per = [f"{jb} barang ikut berganti kategori dari '{k['name']}' ke '{n}'."] if jb else []
    return _hasil([], {"id": str(k["id"]), "dari": k["name"], "ke": n, "jumlah_barang": jb}, per)


def rencana_status(k: Optional[dict], ke_aktif: bool) -> dict:
    if not k:
        return _hasil([_blok("KATEGORI_TAK_ADA", "Kategori tidak ditemukan.")], None)
    if bool(k["is_active"]) == ke_aktif:
        return _hasil([_blok("KATEGORI_SUDAH_AKTIF" if ke_aktif else "KATEGORI_SUDAH_NONAKTIF", f"Kategori '{k['name']}' sudah {'aktif' if ke_aktif else 'nonaktif'}.")], None)
    if not ke_aktif and k["is_default"]:
        return _hasil([_blok("KATEGORI_BAWAAN", f"Kategori '{k['name']}' adalah kategori bawaan; jadikan kategori lain bawaan dulu.")], None)
    a = int(k["jumlah_aktif"])
    per = [f"{a} barang aktif masih memakai kategori '{k['name']}'. Barangnya tetap membawa kategori ini; hanya tak muncul sebagai pilihan baru."] if (not ke_aktif and a) else []
    return _hasil([], {"id": str(k["id"]), "nama": k["name"], "ke": "aktif" if ke_aktif else "nonaktif", "jumlah_aktif": a}, per)


async def ukur_kategori(conn, tid: str, kategori_id) -> Optional[dict]:
    r = await conn.fetchrow(SQL_UKUR, tid, str(kategori_id))
    return dict(r) if r else None


async def ada_nama(conn, tid: str, nama: str, kecuali_id=None) -> bool:
    return await conn.fetchval(SQL_BENTROK, tid, nama, None if kecuali_id is None else str(kecuali_id)) is not None


async def _kunci(conn, tid: str) -> None:
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"ITEM_KATEGORI:{tid}")


async def catat_audit(conn, tid: str, user_id, kunci: str, kategori_id, nama: str, ringkas: dict) -> None:
    await conn.execute(
        """INSERT INTO audit_logs (id, "eventType", entity_type, entity_id, entity_number, tenant_id, source, metadata, success, "createdAt", "userId")
           VALUES (gen_random_uuid()::text, $1, 'item_category', $2::uuid, $3, $4, $5, $6::jsonb, true, now(), $7)""",
        EVENT[kunci], str(kategori_id), nama, tid, SUMBER_AUDIT, json.dumps(ringkas), str(user_id or "") or None)


async def tulis_ubah_nama(conn, tid: str, user_id, k: dict, nama_baru: str) -> int:
    """Registry lebih dulu, lalu products.kategori (atomik di transaksi pemanggil). -> jumlah baris products yang berganti label (termasuk yang terhapus-lunak)."""
    await _kunci(conn, tid)
    await conn.execute("UPDATE item_categories SET name = $3, updated_at = NOW() WHERE tenant_id = $1 AND id = $2", tid, str(k["id"]), nama_baru)
    r = await conn.execute("UPDATE products SET kategori = $3, updated_at = NOW() WHERE tenant_id = $1 AND lower(kategori) = lower($2)", tid, k["name"], nama_baru)
    n = int(r.split()[-1]) if r and r.split()[-1].isdigit() else 0
    await catat_audit(conn, tid, user_id, "ubah_nama", k["id"], nama_baru, {"dari": k["name"], "ke": nama_baru, "barang_diubah": n})
    return n


async def tulis_status(conn, tid: str, user_id, k: dict, ke_aktif: bool) -> None:
    await _kunci(conn, tid)
    await conn.execute("UPDATE item_categories SET is_active = $3, updated_at = NOW() WHERE tenant_id = $1 AND id = $2", tid, str(k["id"]), ke_aktif)
    await catat_audit(conn, tid, user_id, "aktif" if ke_aktif else "nonaktif", k["id"], k["name"], {"jumlah_aktif": int(k["jumlah_aktif"])})


async def riwayat(conn, tid: str, kategori_id, batas: int = 50) -> list:
    rows = await conn.fetch(
        """SELECT "eventType", metadata, "createdAt", "userId" FROM audit_logs WHERE tenant_id = $1 AND entity_type = 'item_category' AND entity_id = $2::uuid
           ORDER BY "createdAt" DESC, id DESC LIMIT $3""", tid, str(kategori_id), max(1, min(int(batas), 200)))
    return [{"event": r["eventType"], "teks": TEKS_EVENT.get(r["eventType"], r["eventType"]), "detail": r["metadata"] if isinstance(r["metadata"], dict) else json.loads(r["metadata"] or "{}"),
             "waktu": r["createdAt"].isoformat() if r["createdAt"] else None, "oleh": r["userId"]} for r in rows]


TEKS_EVENT = {"ITEM_CATEGORY_CREATED": "Kategori dibuat", "ITEM_CATEGORY_RENAMED": "Nama kategori diubah", "ITEM_CATEGORY_DEACTIVATED": "Kategori dinonaktifkan",
              "ITEM_CATEGORY_ACTIVATED": "Kategori diaktifkan", "ITEM_CATEGORY_DELETED": "Kategori dihapus"}
