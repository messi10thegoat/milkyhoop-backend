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
         "hapus": "ITEM_CATEGORY_DELETED", "bawaan": "ITEM_CATEGORY_DEFAULT_SET", "pindah": "ITEM_CATEGORY_ITEMS_MOVED", "gabung": "ITEM_CATEGORY_MERGED"}
BATAS_ID = 500          # item_ids sebagian pada pindah massal
CONTOH_MAKS = 20        # contoh barang di pratinjau pindah/gabung
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


TEKS_EVENT = {"ITEM_CATEGORY_DEFAULT_SET": "Dijadikan kategori bawaan", "ITEM_CATEGORY_ITEMS_MOVED": "Barang dipindahkan ke kategori ini", "ITEM_CATEGORY_MERGED": "Kategori digabung ke kategori ini",
              "ITEM_CATEGORY_CREATED": "Kategori dibuat", "ITEM_CATEGORY_RENAMED": "Nama kategori diubah", "ITEM_CATEGORY_DEACTIVATED": "Kategori dinonaktifkan",
              "ITEM_CATEGORY_ACTIVATED": "Kategori diaktifkan", "ITEM_CATEGORY_DELETED": "Kategori dihapus"}


# ---------------------------------------------------------------- K-3: bawaan, pindah massal, gabung
SQL_BAWAAN = "SELECT name FROM item_categories WHERE tenant_id = $1 AND is_default AND is_active"
# Calon barang pindah: $1 tenant, $2 daftar id (NULL = semua), $3 nama kategori sumber (hanya varian KATEGORI). Barang terhapus-lunak TAK ikut pindah massal (gabung memindahkan SEMUA).
_COND_TANPA = "(p.kategori IS NULL OR btrim(p.kategori) = '')"
_COND_KAT = "lower(p.kategori) = lower($3)"
_BASE = "FROM products p WHERE p.tenant_id = $1 AND p.deleted_at IS NULL AND ($2::uuid[] IS NULL OR p.id = ANY($2::uuid[])) AND "
SQL_PINDAH_HITUNG = {"tanpa": "SELECT COUNT(*) AS n, COUNT(*) FILTER (WHERE p.status = 'active') AS a " + _BASE + _COND_TANPA,
                     "kategori": "SELECT COUNT(*) AS n, COUNT(*) FILTER (WHERE p.status = 'active') AS a " + _BASE + _COND_KAT}
SQL_PINDAH_CONTOH = {"tanpa": "SELECT p.id, p.item_code, p.nama_produk, p.status " + _BASE + _COND_TANPA + " ORDER BY p.nama_produk, p.id LIMIT $4",
                     "kategori": "SELECT p.id, p.item_code, p.nama_produk, p.status " + _BASE + _COND_KAT + " ORDER BY p.nama_produk, p.id LIMIT $4"}
SQL_PINDAH_TULIS = {"tanpa": "UPDATE products p SET kategori = $4, updated_at = NOW() WHERE p.tenant_id = $1 AND p.deleted_at IS NULL AND ($2::uuid[] IS NULL OR p.id = ANY($2::uuid[])) AND " + _COND_TANPA,
                    "kategori": "UPDATE products p SET kategori = $4, updated_at = NOW() WHERE p.tenant_id = $1 AND p.deleted_at IS NULL AND ($2::uuid[] IS NULL OR p.id = ANY($2::uuid[])) AND " + _COND_KAT}


def rencana_bawaan(k: Optional[dict]) -> dict:
    if not k:
        return _hasil([_blok("KATEGORI_TAK_ADA", "Kategori tidak ditemukan.")], None)
    if not k["is_active"]:
        return _hasil([_blok("KATEGORI_NONAKTIF", f"Kategori '{k['name']}' nonaktif; aktifkan dulu sebelum dijadikan bawaan.")], None)
    if k["is_default"]:
        return _hasil([_blok("KATEGORI_SUDAH_BAWAAN", f"Kategori '{k['name']}' sudah menjadi kategori bawaan.")], None)
    return _hasil([], {"id": str(k["id"]), "nama": k["name"]},
                  [f"Barang baru tanpa kategori akan otomatis masuk '{k['name']}'. Barang lama tanpa kategori tidak berubah; pindahkan lewat Pindahkan barang."])


def rencana_pindah(sumber_nama: Optional[str], target: Optional[dict], n: int, a: int, contoh: list, sumber_kategori: bool) -> dict:
    """sumber_nama = nama kategori sumber (None = barang TANPA kategori). target = baris ukur_kategori. n/a = barang tak terhapus / yang aktif (calon pindah)."""
    if not target:
        return _hasil([_blok("KATEGORI_TUJUAN_TAK_ADA", "Kategori tujuan tidak ditemukan.")], None)
    if not target["is_active"]:
        return _hasil([_blok("KATEGORI_TUJUAN_NONAKTIF", f"Kategori tujuan '{target['name']}' nonaktif; aktifkan dulu.")], None)
    if sumber_kategori and sumber_nama and sumber_nama.lower() == target["name"].lower():
        return _hasil([_blok("KATEGORI_SAMA", "Kategori asal dan tujuan sama.")], None)
    if n == 0:
        return _hasil([_blok("TIDAK_ADA_BARANG", "Tidak ada barang yang cocok untuk dipindahkan.")], None)
    dari = f"kategori '{sumber_nama}'" if sumber_kategori else "tanpa kategori"
    return _hasil([], {"dari": sumber_nama if sumber_kategori else None, "ke": target["name"], "ke_id": str(target["id"]), "jumlah_barang": n, "jumlah_aktif": a, "contoh": contoh},
                  [f"{n} barang ({a} aktif) dari {dari} berpindah ke kategori '{target['name']}'."])


def rencana_gabung(sumber: Optional[dict], target: Optional[dict]) -> dict:
    if not sumber or not target:
        return _hasil([_blok("KATEGORI_TAK_ADA", "Kategori asal atau tujuan tidak ditemukan.")], None)
    if str(sumber["id"]) == str(target["id"]):
        return _hasil([_blok("KATEGORI_SAMA", "Kategori asal dan tujuan sama.")], None)
    if sumber["is_default"]:
        return _hasil([_blok("KATEGORI_BAWAAN", f"Kategori '{sumber['name']}' adalah kategori bawaan; jadikan kategori lain bawaan dulu.")], None)
    if not target["is_active"]:
        return _hasil([_blok("KATEGORI_TUJUAN_NONAKTIF", f"Kategori tujuan '{target['name']}' nonaktif; aktifkan dulu.")], None)
    jb = int(sumber["jumlah_barang"])
    return _hasil([], {"dari": sumber["name"], "dari_id": str(sumber["id"]), "ke": target["name"], "ke_id": str(target["id"]), "jumlah_barang": jb},
                  [f"{jb} barang berpindah dari '{sumber['name']}' ke '{target['name']}', lalu kategori '{sumber['name']}' dihapus."] if jb else
                  [f"Kategori '{sumber['name']}' tidak punya barang; hanya dihapus."])


async def kategori_bawaan(conn, tid: str) -> Optional[str]:
    """Nama kategori bawaan AKTIF usaha ini (None = belum ada). Aturan barang baru tanpa kategori -> terisi bawaan ada di penentu buat barang, bukan FE."""
    return await conn.fetchval(SQL_BAWAAN, tid)


def _kunci_sumber(sumber_nama: Optional[str], sumber_kategori: bool):
    return ("kategori", sumber_nama) if sumber_kategori else ("tanpa", None)


async def ukur_pindah(conn, tid: str, sumber_nama: Optional[str], sumber_kategori: bool, ids=None) -> tuple:
    """-> (n, aktif, contoh[{id, kode, nama, status}]). ids = daftar UUID str atau None (semua yang cocok)."""
    j, nama = _kunci_sumber(sumber_nama, sumber_kategori)
    arg = list(ids) if ids else None
    pr = (tid, arg) + ((nama,) if j == "kategori" else ())
    r = await conn.fetchrow(SQL_PINDAH_HITUNG[j], *pr)
    rows = await conn.fetch(SQL_PINDAH_CONTOH[j], *(pr + (CONTOH_MAKS,))) if int(r["n"]) else []
    return int(r["n"]), int(r["a"]), [{"id": str(x["id"]), "kode": x["item_code"], "nama": x["nama_produk"], "status": x["status"]} for x in rows]


async def tulis_pindah(conn, tid: str, user_id, sumber_nama: Optional[str], sumber_kategori: bool, target: dict, ids=None) -> int:
    await _kunci(conn, tid)
    j, nama = _kunci_sumber(sumber_nama, sumber_kategori)
    arg = list(ids) if ids else None
    pr = (tid, arg) + ((nama,) if j == "kategori" else (None,)) + (target["name"],)
    r = await conn.execute(SQL_PINDAH_TULIS[j], *pr)
    n = int(r.split()[-1]) if r and r.split()[-1].isdigit() else 0
    await catat_audit(conn, tid, user_id, "pindah", target["id"], target["name"], {"dari": sumber_nama if sumber_kategori else None, "dari_tanpa_kategori": not sumber_kategori, "jumlah_barang": n,
                                                                                    "sebagian": bool(ids)})
    return n


async def tulis_bawaan(conn, tid: str, user_id, k: dict) -> None:
    """Bawaan lama dicabut LEBIH DULU, baru yang baru dipasang (indeks unik parsial: satu bawaan per usaha) dalam satu transaksi."""
    await _kunci(conn, tid)
    await conn.execute("UPDATE item_categories SET is_default = false, updated_at = NOW() WHERE tenant_id = $1 AND is_default AND id <> $2", tid, str(k["id"]))
    await conn.execute("UPDATE item_categories SET is_default = true, updated_at = NOW() WHERE tenant_id = $1 AND id = $2", tid, str(k["id"]))
    await catat_audit(conn, tid, user_id, "bawaan", k["id"], k["name"], {"nama": k["name"]})


async def tulis_gabung(conn, tid: str, user_id, sumber: dict, target: dict) -> int:
    """SEMUA barang bersumber (termasuk terhapus-lunak, agar tak tersisa label yatim) -> label tujuan, lalu baris registry sumber dihapus. Atomik di transaksi pemanggil."""
    await _kunci(conn, tid)
    r = await conn.execute("UPDATE products SET kategori = $3, updated_at = NOW() WHERE tenant_id = $1 AND lower(kategori) = lower($2)", tid, sumber["name"], target["name"])
    n = int(r.split()[-1]) if r and r.split()[-1].isdigit() else 0
    await conn.execute("DELETE FROM item_categories WHERE tenant_id = $1 AND id = $2", tid, str(sumber["id"]))
    await catat_audit(conn, tid, user_id, "gabung", target["id"], target["name"], {"dari": sumber["name"], "dari_id": str(sumber["id"]), "jumlah_barang": n})
    return n
