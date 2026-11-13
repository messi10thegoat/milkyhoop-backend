"""BUG-216 (putusan pemilik 13 Nov, ikut QuickBooks/SAP; MASTER: opsi C TERBATAS): alamat kirim PO opsional, server mengisi bawaan SNAPSHOT.
Urutan: manual (diketik) MENANG; gudang tujuan DIPILIH eksplisit -> alamatnya; tanpa pilihan -> gudang UTAMA aktif bila beralamat; lalu "Tenant".address (alamat usaha).
purchase_orders.warehouse_id HANYA diisi bila pengguna MEMILIH gudang tujuan (bawaan tak pernah disimpan ke kolom): GRN tetap mengikuti gudang bawaan tenant
terkini bila PO tanpa gudang (grn_rencana._gudang), dan memakai gudang PO bila dipilih (perilaku GRN yang sudah ada, tak diubah di sini).
SATU penentu untuk buat, ubah, dan pratinjau ubah; `sumber_tersimpan` menurunkan teks sumber saat BACA (tanpa kolom baru)."""
from typing import Optional
from uuid import UUID

from .po_cetak import alamat_gudang

SUMBER_MANUAL = "Diisi manual"
SUMBER_USAHA = "Alamat usaha"


def sumber_gudang(nama) -> str:
    return f"Gudang {nama}"


def teks_gudang(w) -> Optional[str]:
    """Alamat gudang = format YANG SAMA dgn PDF PO (po_cetak.alamat_gudang: jalan, kota)."""
    return alamat_gudang({"warehouse_address": w["address"], "warehouse_city": w["city"]})


async def alamat_usaha(conn, tid: str) -> Optional[str]:
    a = await conn.fetchval('SELECT address FROM "Tenant" WHERE id = $1', tid)
    return (a or "").strip() or None


async def gudang_utama(conn, tid: str):
    return await conn.fetchrow("SELECT id, name, address, city FROM warehouses WHERE tenant_id = $1 AND is_default AND is_active ORDER BY created_at LIMIT 1", tid)


async def tentukan(conn, tid: str, gudang_dipilih, alamat_manual, periksa_gudang: bool = True) -> dict:
    """-> {warehouse_id (UUID|None; HANYA bila dipilih), ship_to_address (str|None), sumber (str|None), blocks [(kode, kalimat, status)]}.
    periksa_gudang=False: gudang yang SUDAH tersimpan di PO tak diperiksa aktif/tidaknya lagi (ubah alamat tak boleh terhalang gudang yang kemudian dinonaktifkan)."""
    blocks, pilih = [], None
    if gudang_dipilih:
        try:
            gid = UUID(str(gudang_dipilih))
        except ValueError:
            gid = None
            blocks.append(("GUDANG_TIDAK_SAH", "ID gudang tidak sah.", 422))
        if gid is not None:
            w = await conn.fetchrow("SELECT id, name, address, city, is_active FROM warehouses WHERE id = $1 AND tenant_id = $2", gid, tid)
            if w is None:
                blocks.append(("GUDANG_TIDAK_DITEMUKAN", "Gudang tujuan tidak ditemukan di usaha ini.", 422))
            elif periksa_gudang and not w["is_active"]:
                blocks.append(("GUDANG_NONAKTIF", "Gudang tujuan nonaktif; pilih gudang lain.", 422))
            else:
                pilih = w
    gid_simpan = pilih["id"] if pilih else None                     # HANYA gudang yang dipilih eksplisit yang disimpan ke kolom
    manual = (alamat_manual or "").strip()
    if manual:
        return {"warehouse_id": gid_simpan, "ship_to_address": manual, "sumber": SUMBER_MANUAL, "blocks": blocks}
    asal = pilih if gudang_dipilih else await gudang_utama(conn, tid)   # dipilih -> gudang itu; tanpa pilihan -> gudang utama aktif (bawaan, TIDAK disimpan)
    alamat = sumber = None
    if asal is not None:
        alamat = teks_gudang(asal)
        sumber = sumber_gudang(asal["name"]) if alamat else None
    if alamat is None:
        alamat = await alamat_usaha(conn, tid)
        sumber = SUMBER_USAHA if alamat else None
    return {"warehouse_id": gid_simpan, "ship_to_address": alamat, "sumber": sumber, "blocks": blocks}


async def sumber_tersimpan(conn, tid: str, ship_to, warehouse_id) -> Optional[str]:
    """Teks sumber alamat TERSIMPAN, diturunkan saat baca: sama dgn alamat gudang (PO / utama) -> "Gudang X"; sama dgn alamat usaha -> "Alamat usaha"; selain itu manual."""
    s = (ship_to or "").strip()
    if not s:
        return None
    w = None
    if warehouse_id:
        w = await conn.fetchrow("SELECT id, name, address, city FROM warehouses WHERE id = $1 AND tenant_id = $2", warehouse_id, tid)
    else:
        w = await gudang_utama(conn, tid)
    if w is not None and teks_gudang(w) == s:
        return sumber_gudang(w["name"])
    if await alamat_usaha(conn, tid) == s:
        return SUMBER_USAHA
    return SUMBER_MANUAL
