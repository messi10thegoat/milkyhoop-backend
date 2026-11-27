"""Serial S4 (27 Nov 2026; DESAIN-serial-20261127 §4): BACA nomor seri -- daftar, detail + riwayat, tersedia per barang+gudang.
Kueri langsung ke item_serials / serial_movements (fungsi DB lama V048 diparkir: gagal di setiap panggilan). Teks tampil dari server
(status_text, gudang_text, dokumen_terakhir_text, gerak_text); FE tidak menerjemahkan kode. Baca saja, tanpa kunci."""
from typing import Optional

STATUS_TEKS = {
    "available": "Tersedia", "reserved": "Dipesan", "transit": "Dalam pemindahan", "sold": "Terjual",
    "returned": "Diretur", "returned_vendor": "Diretur ke pemasok", "adjusted_out": "Dikeluarkan (penyesuaian)",
    "damaged": "Rusak", "scrapped": "Tidak berlaku",
}
STATUS_SAH = set(STATUS_TEKS)


def status_teks(s: Optional[str]) -> str:
    return STATUS_TEKS.get(s or "", s or "-")


def gerak_teks(m: dict) -> str:
    """Kalimat satu gerak: '<status asal> → <status tujuan>' (masuk pertama = 'Masuk')."""
    if not m.get("from_status"):
        return "Masuk"
    return f"{status_teks(m['from_status'])} → {status_teks(m.get('to_status'))}"


_DASAR = """
    SELECT s.id, s.item_id, s.serial_number, s.status, s.warehouse_id, s.received_date, s.created_at, s.updated_at,
           p.item_code AS item_code, p.nama_produk AS item_name, w.name AS warehouse_name,
           m.reference_number AS dok_no, m.reference_type AS dok_jenis, m.movement_date AS dok_tgl
    FROM item_serials s
    LEFT JOIN products p ON p.id = s.item_id AND p.tenant_id = s.tenant_id
    LEFT JOIN warehouses w ON w.id = s.warehouse_id AND w.tenant_id = s.tenant_id
    LEFT JOIN LATERAL (SELECT reference_number, reference_type, movement_date FROM serial_movements
                       WHERE serial_id = s.id AND tenant_id = s.tenant_id ORDER BY movement_date DESC, created_at DESC LIMIT 1) m ON true
"""


def baris(r) -> dict:
    r = dict(r)
    return {
        "id": str(r["id"]), "item_id": str(r["item_id"]), "item_code": r.get("item_code"), "item_name": r.get("item_name"),
        "serial_number": r["serial_number"], "status": r["status"], "status_text": status_teks(r["status"]),
        "warehouse_id": str(r["warehouse_id"]) if r.get("warehouse_id") else None,
        # gudang hanya bermakna saat stok masih di tangan (tersedia/dipesan/pindah)
        "gudang_text": (r.get("warehouse_name") or "-") if r["status"] in ("available", "reserved", "transit") else "-",
        "dokumen_terakhir_text": r.get("dok_no") or "-",
        "received_date": r["received_date"].isoformat() if r.get("received_date") else None,
        "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
    }


async def daftar(conn, tid: str, *, item_id=None, warehouse_id=None, status=None, cari=None, skip=0, limit=50) -> dict:
    w, p = ["s.tenant_id = $1"], [tid]
    for kol, v in (("s.item_id", item_id), ("s.warehouse_id", warehouse_id), ("s.status", status)):
        if v is not None:
            p.append(v)
            w.append(f"{kol} = ${len(p)}")
    if cari:
        p.append(f"%{cari.strip()}%")
        w.append(f"(s.serial_number ILIKE ${len(p)} OR p.nama_produk ILIKE ${len(p)} OR p.item_code ILIKE ${len(p)})")
    where = " AND ".join(w)
    total = await conn.fetchval(f"SELECT COUNT(*) FROM item_serials s LEFT JOIN products p ON p.id = s.item_id AND p.tenant_id = s.tenant_id WHERE {where}", *p)
    rows = await conn.fetch(f"{_DASAR} WHERE {where} ORDER BY s.created_at DESC, s.serial_number LIMIT {int(limit)} OFFSET {int(skip)}", *p)
    return {"data": [baris(r) for r in rows], "total": total, "has_more": skip + limit < total}


async def riwayat(conn, tid: str, serial_id) -> list:
    rows = await conn.fetch(
        """SELECT m.id, m.movement_type, m.movement_date, m.from_status, m.to_status, m.from_warehouse_id, m.to_warehouse_id,
                  fw.name AS dari_gudang, tw.name AS ke_gudang, m.reference_type, m.reference_id, m.reference_number
           FROM serial_movements m
           LEFT JOIN warehouses fw ON fw.id = m.from_warehouse_id AND fw.tenant_id = m.tenant_id
           LEFT JOIN warehouses tw ON tw.id = m.to_warehouse_id AND tw.tenant_id = m.tenant_id
           WHERE m.serial_id = $1 AND m.tenant_id = $2 ORDER BY m.movement_date, m.created_at""", serial_id, tid)
    hasil = []
    for m in rows:
        m = dict(m)
        hasil.append({
            "id": str(m["id"]), "tanggal": m["movement_date"].isoformat() if m["movement_date"] else None,
            "gerak_text": gerak_teks(m), "status_sebelum": m["from_status"], "status_sesudah": m["to_status"],
            "gudang_dari_text": m["dari_gudang"] or "-", "gudang_ke_text": m["ke_gudang"] or "-",
            "dokumen_text": m["reference_number"] or "-", "reference_type": m["reference_type"],
            "reference_id": str(m["reference_id"]) if m["reference_id"] else None,
        })
    return hasil


async def detail(conn, tid: str, serial_id) -> Optional[dict]:
    r = await conn.fetchrow(f"{_DASAR} WHERE s.id = $1 AND s.tenant_id = $2", serial_id, tid)
    if not r:
        return None
    return {**baris(r), "riwayat": await riwayat(conn, tid, serial_id)}


async def tersedia(conn, tid: str, item_id, warehouse_id, cari=None, limit=200) -> dict:
    """Pemilih dokumen KELUAR: nomor 'available' di gudang itu (aturan SAMA dengan serial_gerak.keluar)."""
    p = [tid, item_id, warehouse_id]
    w = "s.tenant_id = $1 AND s.item_id = $2 AND s.warehouse_id = $3 AND s.status = 'available'"
    if cari:
        p.append(f"%{cari.strip()}%")
        w += f" AND s.serial_number ILIKE ${len(p)}"
    rows = await conn.fetch(f"SELECT s.id, s.serial_number, s.received_date FROM item_serials s WHERE {w} "
                            f"ORDER BY s.received_date NULLS LAST, s.serial_number LIMIT {int(limit)}", *p)
    total = await conn.fetchval(f"SELECT COUNT(*) FROM item_serials s WHERE {w}", *p)
    return {"item_id": str(item_id), "warehouse_id": str(warehouse_id), "total": total,
            "data": [{"id": str(r["id"]), "serial_number": r["serial_number"],
                      "received_date": r["received_date"].isoformat() if r["received_date"] else None} for r in rows]}
