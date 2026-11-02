"""Gudang BAWAAN tenant -- SATU penentu (GUDANG-NONSTOK, 2 Nov 2026; rancangan disetujui MASTER, pola NetSuite: lokasi wajib
untuk barang ber-stok, non-stok tak butuh gudang).

bawaan(conn, tid) = satu-satunya gudang AKTIF, atau satu-satunya gudang aktif is_default; selain itu None (pemanggil memblok
"pilih gudang"). Dipindah UTUH dari ekor so_pengiriman._gudang (aturan kirim SO); dipakai juga SJ dari SO (sj_so) dan GRN
(grn_rencana) yang dulu memakai "aktif ORDER BY is_default DESC, created_at LIMIT 1" (gudang aktif mana pun bisa terpilih).
Terukur 2 Nov: grapgrap & kaos masing-masing 2 aktif + 1 is_default -> hasil lama == hasil baru (Gudang Utama).
Pemanggil hanya memanggilnya bila ADA baris ber-stok.
"""

KOLOM = "SELECT id, name, is_active, is_default FROM warehouses WHERE tenant_id = $1"


async def bawaan(conn, tid: str):
    """-> baris gudang (id, name, is_active, is_default) atau None."""
    aktif = await conn.fetch(KOLOM + " AND is_active ORDER BY name", tid)
    for calon in (list(aktif) if len(aktif) == 1 else [], [r for r in aktif if r["is_default"]]):
        if len(calon) == 1:
            return calon[0]
    return None
