"""BUG-255 (17 Nov 2026; putusan MASTER, DH #1 celah vs Accurate): PASANG DAFTAR HARGA ke BANYAK pelanggan sekaligus -- jalur SENDIRI pratinjau == tulis (bukan bulk.jalankan_per_item:
itu transaksi PER ITEM + batas 50, tak cocok untuk "atomik" ratusan pelanggan; AKSI_DIIZINKAN tak ditambah).

rencana() MURNI (uji unit): dari baris pelanggan terukur + target -> {ok, blocks, target, ringkas, kelompok_lama, baris (contoh), payload}. Penentu SAMA dgn tulis (tulis memanggil
rencana lagi di bawah kunci). Target = daftar harga aktif milik usaha, ATAU None = daftar BAWAAN (price_list_id NULL). Tulis: satu UPDATE, yang sudah sama dilewati; per pelanggan berubah
satu baris customer_activities; satu baris audit_logs CUSTOMER_PRICE_LIST_BULK. Tidak menyentuh harga/jurnal/dokumen lama (daftar hanya dibaca saat resolve harga berikutnya)."""
import json
from typing import Optional

BATAS_ID = 500
BATAS_SARINGAN = 2000
CONTOH = 50
NAMA_BAWAAN = "Daftar bawaan"

SQL_TARGET = "SELECT id, name, code, is_active FROM price_lists WHERE id = $1 AND tenant_id = $2"
SQL_PELANGGAN = ("SELECT c.id, c.nomor_member AS kode, c.nama, c.is_active, c.price_list_id, pl.name AS daftar_nama FROM customers c "
                 "LEFT JOIN price_lists pl ON pl.id = c.price_list_id AND pl.tenant_id = c.tenant_id "
                 "WHERE c.tenant_id = $1 AND c.id = ANY($2::uuid[]) ORDER BY lower(c.nama), c.id")
SQL_UBAH = ("UPDATE customers SET price_list_id = $3, updated_at = NOW() WHERE tenant_id = $1 AND id = ANY($2::uuid[]) "
            "AND price_list_id IS DISTINCT FROM $3 RETURNING id")


def _blok(code: str, message: str) -> dict:
    return {"code": code, "message": message}


def _nama(id_, nama) -> dict:
    return {"id": str(id_) if id_ else None, "nama": nama if id_ else NAMA_BAWAAN}


def rencana(target: Optional[dict], target_diminta: bool, rows: list, diminta_n: int, dari_saringan: bool) -> dict:
    """target = baris price_lists (atau None); target_diminta = price_list_id bukan null; rows = baris pelanggan terukur; diminta_n = jumlah id yang diminta/terurai."""
    bl = []
    if target_diminta and not target:
        bl.append(_blok("DAFTAR_TIDAK_ADA", "Daftar harga tujuan tidak ditemukan di usaha ini."))
    elif target_diminta and not target["is_active"]:
        bl.append(_blok("DAFTAR_NONAKTIF", f"Daftar harga '{target['name']}' nonaktif; aktifkan dulu."))
    batas = BATAS_SARINGAN if dari_saringan else BATAS_ID
    if diminta_n == 0:
        bl.append(_blok("TANPA_PELANGGAN", "Pilih minimal satu pelanggan."))
    elif diminta_n > batas:
        bl.append(_blok("TERLALU_BANYAK", f"Maksimal {batas} pelanggan per permintaan (ada {diminta_n}); persempit saringan atau bagi menjadi beberapa kali."))
    ke_id = None if not target_diminta or not target else str(target["id"])
    ke = _nama(ke_id, target["name"] if target else None)
    semua, sama, kelompok = [], 0, {}
    for r in rows:
        dari_id = str(r["price_list_id"]) if r["price_list_id"] else None
        berubah = dari_id != ke_id
        sama += 0 if berubah else 1
        g = kelompok.setdefault(dari_id, {"daftar_id": dari_id, "nama": r["daftar_nama"] if dari_id else NAMA_BAWAAN, "jumlah": 0})
        g["jumlah"] += 1
        semua.append({"id": str(r["id"]), "kode": r["kode"], "nama": r["nama"], "nonaktif": not r["is_active"], "dari": _nama(dari_id, r["daftar_nama"]), "ke": ke, "berubah": berubah})
    tak = max(0, diminta_n - len(rows)) if diminta_n <= batas else 0
    berubah_n = sum(1 for x in semua if x["berubah"])
    per = []
    if semua and berubah_n == 0 and not bl:
        per.append(f"Semua {len(semua)} pelanggan sudah memakai '{ke['nama']}'; tidak ada yang berubah.")
    if sum(1 for x in semua if x["nonaktif"]):
        per.append(f"{sum(1 for x in semua if x['nonaktif'])} pelanggan nonaktif ikut dipilih.")
    ok = not bl and bool(semua)
    return {"ok": ok, "blocks": bl, "peringatan": per, "target": {"id": ke_id, "nama": ke["nama"], "status_text": ("Aktif" if (not target_diminta or (target and target["is_active"])) else "Nonaktif")},
            "ringkas": {"dipilih": len(semua), "berubah": berubah_n, "sama": sama, "tak_ditemukan": tak, "nonaktif": sum(1 for x in semua if x["nonaktif"])},
            "kelompok_lama": sorted(kelompok.values(), key=lambda g: (-g["jumlah"], g["nama"])),
            "baris": semua[:CONTOH], "payload": {"price_list_id": ke_id, "customer_ids": [x["id"] for x in semua]}, "_semua": semua}


async def ukur_target(conn, tid: str, price_list_id) -> Optional[dict]:
    r = await conn.fetchrow(SQL_TARGET, price_list_id, tid) if price_list_id else None
    return dict(r) if r else None


async def ukur_pelanggan(conn, tid: str, ids: list) -> list:
    return [dict(r) for r in await conn.fetch(SQL_PELANGGAN, tid, ids)] if ids else []


async def tulis(conn, ctx: dict, r: dict, user_name: Optional[str]) -> dict:
    """WAJIB di transaksi pemanggil. r = rencana() yang SUDAH ok (pemanggil memvalidasi ulang di bawah kunci). -> {jumlah_berubah, jumlah_sama, target}."""
    if not conn.is_in_transaction():
        raise RuntimeError("dh_pelanggan_massal.tulis wajib di dalam transaksi pemanggil (Law 23)")
    tid, uid = ctx["tenant_id"], ctx.get("user_id")
    target_id = r["payload"]["price_list_id"]
    ke_nama = r["target"]["nama"]
    berubah = [x for x in r["_semua"] if x["berubah"]]
    ids_ubah = [x["id"] for x in berubah]
    diubah = {str(x["id"]) for x in await conn.fetch(SQL_UBAH, tid, ids_ubah, target_id)} if ids_ubah else set()
    for x in berubah:
        if x["id"] not in diubah:
            continue   # balapan: sudah sama/berubah oleh penulis lain di antara pratinjau dan tulis (dikunci advisory; tetap aman)
        await conn.execute(
            """INSERT INTO customer_activities (customer_id, tenant_id, type, description, field_name, old_value, new_value, actor_id, actor_name)
               VALUES ($1::uuid, $2, 'updated', $3, 'price_list_id', $4, $5, $6, $7)""",
            x["id"], tid, f"Daftar harga diubah menjadi '{ke_nama}' (massal)", x["dari"]["nama"], ke_nama, uid, user_name)
    ringkas = {"modul": "customers", "jumlah": len(diubah), "ids": sorted(diubah)[:50], "ke": ke_nama, "ke_id": target_id,
               "dari": {g["nama"]: g["jumlah"] for g in r["kelompok_lama"]}}
    await conn.execute(
        """INSERT INTO audit_logs (id, "eventType", entity_type, tenant_id, source, metadata, success, "createdAt", "userId")
           VALUES (gen_random_uuid()::text, 'CUSTOMER_PRICE_LIST_BULK', 'customer', $1, 'api:customers.bulk', $2::jsonb, true, now(), $3)""",
        tid, json.dumps(ringkas), str(uid or "") or None)
    return {"jumlah_berubah": len(diubah), "jumlah_sama": r["ringkas"]["sama"], "target": r["target"]}
