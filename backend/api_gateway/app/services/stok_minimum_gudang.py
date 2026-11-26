"""MN-1 (26 Nov 2026): stok minimum/maksimum PER GUDANG (Accurate: Minimum/Maksimum Kuantitas per gudang). Tabel V511 stok_minimum_gudang.
Flag gelap stok_minimum_gudang_v1. Stok per gudang = Σ masuk − Σ keluar inventory_ledger per gudang (Law 16; bukan cache warehouse_stock).
Usulan beli per gudang = max(minimum − stok gudang, 0); PO terbuka tak dibagi per gudang (PO tanpa gudang tujuan) -> tak dihitung di sini, dinyatakan di teks.
Gudang sistem (Transit) tak pernah diatur/didaftar."""
from decimal import Decimal
from typing import List, Optional

from .galat_berkode import GalatBerkode
from .reorder_persediaan import _f

FLAG = "stok_minimum_gudang_v1"
NOL = Decimal(0)


async def flag_nyala(conn, tid: str) -> bool:
    return (await conn.fetchval("SELECT EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id = $1 AND feature = $2 AND enabled)", tid, FLAG)) is True


async def wajib_flag(conn, tid: str) -> None:
    if not await flag_nyala(conn, tid):
        raise GalatBerkode(404, "FITUR_BELUM_AKTIF", "Fitur stok minimum per gudang belum aktif.")


def periksa(minimum, maksimum) -> Optional[str]:
    """Pesan galat Indonesia atau None. MURNI (cermin CHECK V511)."""
    mi = Decimal(str(minimum if minimum is not None else 0))
    if mi < 0:
        return "Minimum tidak boleh negatif."
    if maksimum is not None and Decimal(str(maksimum)) < mi:
        return "Maksimum tidak boleh lebih kecil dari minimum."
    return None


_SQL_BARIS = """
SELECT w.id::text AS gudang_id, w.name AS gudang, COALESCE(m.minimum, 0) AS minimum, m.maksimum,
       COALESCE((SELECT SUM(l.quantity_in) - SUM(l.quantity_out) FROM inventory_ledger l
                 WHERE l.tenant_id = w.tenant_id AND l.product_id = $2 AND l.warehouse_id = w.id), 0) AS stok
FROM warehouses w
LEFT JOIN stok_minimum_gudang m ON m.tenant_id = w.tenant_id AND m.warehouse_id = w.id AND m.product_id = $2
WHERE w.tenant_id = $1 AND NOT w.is_system AND (w.is_active OR m.warehouse_id IS NOT NULL)
ORDER BY lower(w.name), w.id
"""


def qt(x, satuan: str) -> Optional[str]:
    """Angka + satuan format Indonesia (1.000 pcs · 1,5 kg); None tetap None (maksimum kosong)."""
    from .barang_daftar import _q
    return None if x is None else f"{_q(Decimal(str(x)))} {satuan}"


def beri_teks(b: dict, satuan: str) -> dict:
    """KONTRAK-mn1: *_text per baris + status_text bila stok < minimum (MURNI)."""
    b.update(minimum_text=qt(b["minimum"], satuan) if b["minimum"] > 0 else None, maksimum_text=qt(b["maksimum"], satuan),
             stok_text=qt(b["stok"], satuan), usulan_beli_text=qt(b["usulan_beli"], satuan),
             di_bawah_minimum=b["minimum"] > 0 and b["stok"] < b["minimum"])
    b["status_text"] = "Di bawah minimum" if b["di_bawah_minimum"] else None
    return b


async def satuan_barang(conn, tid: str, product_id) -> Optional[str]:
    """None = barang tak ada (atau terhapus)."""
    r = await conn.fetchrow("SELECT COALESCE(NULLIF(base_unit, ''), satuan, 'pcs') AS u FROM products WHERE tenant_id = $1 AND id = $2 AND deleted_at IS NULL",
                            tid, product_id)
    return (r["u"] or "pcs") if r else None


async def baca(conn, tid: str, product_id) -> List[dict]:
    rows = await conn.fetch(_SQL_BARIS, tid, product_id)
    satuan = await satuan_barang(conn, tid, product_id) or "pcs"
    return [beri_teks({"gudang_id": r["gudang_id"], "gudang": r["gudang"], "minimum": Decimal(str(r["minimum"])),
                       "maksimum": Decimal(str(r["maksimum"])) if r["maksimum"] is not None else None, "stok": Decimal(str(r["stok"])),
                       "usulan_beli": usulan(r["stok"], r["minimum"])}, satuan) for r in rows]


def _teks_nilai(mi, mk, satuan: str) -> str:
    mi = Decimal(str(mi or 0))
    if mi == 0 and mk is None:
        return "(tidak diatur)"
    t = f"minimum {qt(mi, satuan)}"
    return t + (f", maksimum {qt(mk, satuan)}" if mk is not None else "")


def rencana_simpan(lama: List[dict], baris: List[dict], satuan: Optional[str]) -> dict:
    """SATU penentu pratinjau + PUT (MURNI). lama = baca(); baris = badan PUT. -> {ok, blocks[{code,message,field}], baris[{gudang,dari_text,ke_text}], teks}."""
    if satuan is None:
        return {"ok": False, "blocks": [{"code": "BARANG_TAK_ADA", "message": "Barang tidak ditemukan.", "field": None}], "baris": [], "teks": None}
    peta = {g["gudang_id"]: g for g in lama}
    blocks, ubah = [], []
    for b in baris:
        gid = str(b["gudang_id"])
        pesan = periksa(b.get("minimum"), b.get("maksimum"))
        if pesan:
            blocks.append({"code": "MINIMUM_TAK_SAH", "message": pesan, "field": "minimum", "gudang_id": gid})
            continue
        g = peta.get(gid)
        if not g:
            blocks.append({"code": "GUDANG_TAK_SAH", "message": "Gudang tidak ditemukan.", "field": "gudang_id", "gudang_id": gid})
            continue
        mk = b.get("maksimum")
        dari, ke = _teks_nilai(g["minimum"], g["maksimum"], satuan), _teks_nilai(b.get("minimum"), Decimal(str(mk)) if mk is not None else None, satuan)
        if dari != ke:
            ubah.append({"gudang_id": gid, "gudang": g["gudang"], "dari_text": dari, "ke_text": ke})
    if blocks:
        teks = blocks[0]["message"]
    elif ubah:
        teks = f"Stok minimum di {len(ubah)} gudang akan diubah."
    else:
        teks = "Tidak ada perubahan."
    return {"ok": not blocks, "blocks": blocks, "baris": ubah, "teks": teks}


def tolak(r: dict) -> None:
    if r["blocks"]:
        b = r["blocks"][0]
        raise GalatBerkode(404 if b["code"] == "BARANG_TAK_ADA" else 422, b["code"], b["message"], field=b["field"])


def usulan(stok, minimum) -> Decimal:
    mi = Decimal(str(minimum or 0))
    return max(mi - Decimal(str(stok or 0)), NOL) if mi > 0 else NOL


async def simpan(conn, tid: str, product_id, baris: List[dict], user_id=None) -> None:
    """Ganti pengaturan barang ini per gudang yang disebut. minimum 0 + maksimum kosong = hapus pengaturan gudang itu.
    Penghalang = rencana_simpan (penentu SAMA dgn POST .../preview)."""
    tolak(await ukur_rencana(conn, tid, product_id, baris))
    for b in baris:
        mi, mk = Decimal(str(b.get("minimum") or 0)), b.get("maksimum")
        if mi == 0 and mk is None:
            await conn.execute("DELETE FROM stok_minimum_gudang WHERE tenant_id = $1 AND product_id = $2 AND warehouse_id = $3", tid, product_id, b["gudang_id"])
            continue
        await conn.execute(
            "INSERT INTO stok_minimum_gudang (tenant_id, product_id, warehouse_id, minimum, maksimum, updated_by) VALUES ($1, $2, $3, $4, $5, $6) "
            "ON CONFLICT (tenant_id, product_id, warehouse_id) DO UPDATE SET minimum = EXCLUDED.minimum, maksimum = EXCLUDED.maksimum, "
            "updated_by = EXCLUDED.updated_by, updated_at = now()",
            tid, product_id, b["gudang_id"], mi, Decimal(str(mk)) if mk is not None else None, user_id)


async def ukur_rencana(conn, tid: str, product_id, baris: List[dict]) -> dict:
    satuan = await satuan_barang(conn, tid, product_id)
    return rencana_simpan(await baca(conn, tid, product_id) if satuan else [], baris, satuan)


_SQL_PESAN = """
SELECT p.id::text AS id, p.nama_produk, COALESCE(NULLIF(p.base_unit, ''), p.satuan) AS unit, w.id::text AS gudang_id, w.name AS gudang, m.minimum,
       COALESCE((SELECT SUM(l.quantity_in) - SUM(l.quantity_out) FROM inventory_ledger l
                 WHERE l.tenant_id = m.tenant_id AND l.product_id = m.product_id AND l.warehouse_id = m.warehouse_id), 0) AS stok
FROM stok_minimum_gudang m
JOIN products p ON p.id = m.product_id AND p.tenant_id = m.tenant_id
JOIN warehouses w ON w.id = m.warehouse_id AND w.tenant_id = m.tenant_id
WHERE m.tenant_id = $1 AND m.minimum > 0 AND p.deleted_at IS NULL AND NOT w.is_system
"""


async def daftar_pesan_ulang(conn, tid: str, limit: Optional[int] = None, search: Optional[str] = None, status: Optional[str] = None,
                            skip: int = 0, warehouse_id: Optional[str] = None) -> dict:
    """Pesan Ulang per gudang: baris (barang, gudang) yang stoknya di bawah minimum gudang. Urut kekurangan terbesar."""
    semua = []
    for r in await conn.fetch(_SQL_PESAN, tid):
        u, s = usulan(r["stok"], r["minimum"]), r["unit"] or "pcs"
        teks = f"{r['nama_produk']} di {r['gudang']}: stok {_f(r['stok'])} {s}, minimum {_f(r['minimum'])}"
        teks += f" — usul pesan {_f(u)} {s}." if u > 0 else " — aman."
        semua.append({"id": r["id"], "nama_produk": r["nama_produk"], "satuan": s, "gudang_id": r["gudang_id"], "gudang": r["gudang"],
                      "stok": Decimal(str(r["stok"])), "minimum": Decimal(str(r["minimum"])), "usulan_beli": u, "teks": teks})
    if warehouse_id:
        semua = [x for x in semua if x["gudang_id"] == str(warehouse_id)]
    perlu = sorted((x for x in semua if x["usulan_beli"] > 0), key=lambda x: (-x["usulan_beli"], x["nama_produk"].lower(), x["gudang"].lower()))
    from .reorder_persediaan import halaman
    gudang = sorted({(x["gudang_id"], x["gudang"]) for x in perlu}, key=lambda g: g[1].lower())
    return {**halaman(perlu, len(semua), search, status, skip, limit), "gudang_tersedia": [{"id": i, "nama": n} for i, n in gudang],
            "catatan": "Pesanan pembelian terbuka belum dibagi per gudang; usulan hanya memakai stok gudang."}
