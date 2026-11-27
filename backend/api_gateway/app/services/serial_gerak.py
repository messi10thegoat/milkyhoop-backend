"""SATU penulis nomor seri (Serial S1, 27 Nov 2026; DESAIN-serial-20261127 §2; Q1-Q3 putusan: serial lahir hanya dari dokumen MASUK,
HPP tetap per BARANG, serial WAJIB saat masuk). Dipanggil HANYA dari pintu kartu stok (ledger_kartu.tulis_kartu) di transaksi pemanggil, jadi
serial tak pernah lepas dari stok. Flag gelap `serial_v1`: mati = tak ada yang berubah (barang track_serial diperlakukan seperti sebelumnya).

Aturan:
- Barang `track_serial` + flag nyala: tiap baris kartu WAJIB membawa nomor seri (|serial| = qty bulat) ATAU merupakan pembalik baris asal
  (`balik_ledger`) -- selain itu 422 SERIAL_WAJIB berkalimat (dokumen yang belum mendukung serial menolak, bukan diam-diam lolos).
- MASUK: nomor baru -> item_serials 'available' di gudang; nomor lama yang 'available' -> 422 SERIAL_SUDAH_ADA; nomor lama yang sudah keluar
  (sold/returned_vendor/adjusted_out/returned) -> masuk lagi (retur/temuan), jejak tetap.
- KELUAR: nomor harus 'available' DI GUDANG baris -> status keluar per sumber; selain itu 422 per nomor.
- PEMBALIK (void): keadaan tiap serial dipulihkan dari taut baris asal (status_sebelum / gudang_sebelum); serial yang sudah bergerak lagi
  sesudah baris asal -> 409 SERIAL_SUDAH_BERGERAK (void ditolak, pola T5 FIFO).
Tiap gerak -> inventory_ledger_serials (taut, V513) + serial_movements (jejak V048)."""
from decimal import Decimal
from typing import Optional

from .galat_berkode import GalatBerkode

FLAG = "serial_v1"
STATUS_KELUAR = {"STOCK_ADJUSTMENT": "adjusted_out", "VENDOR_CREDIT": "returned_vendor", "PURCHASE_RETURN": "returned_vendor"}
GERAK_MASUK, GERAK_KELUAR, GERAK_BALIK = "received", "sold", "reversed"


async def wajib(conn, tenant_id: str, product_id) -> bool:
    """Barang ini wajib bernomor seri di kartu stok? = track_serial DAN flag serial_v1 nyala (urutan: kolom dulu, flag hanya bila perlu)."""
    if not await conn.fetchval("SELECT COALESCE(track_serial, false) FROM products WHERE id = $1 AND tenant_id = $2", product_id, tenant_id):
        return False
    from .tenant_features import fitur_aktif
    return FLAG in await fitur_aktif(conn, tenant_id)


def rapikan(serials) -> list:
    """Nomor dipangkas; kosong dibuang; kembar (tanpa beda huruf besar/kecil) -> 422. Murni."""
    hasil, lihat, kembar = [], set(), []
    for s in serials or []:
        s = " ".join(str(s or "").split())
        if not s:
            continue
        if s.upper() in lihat:
            kembar.append(s)
        lihat.add(s.upper())
        hasil.append(s)
    if kembar:
        raise GalatBerkode(422, "SERIAL_KEMBAR", "Nomor seri kembar: " + ", ".join(sorted(set(kembar))) + ".")
    return hasil


def cek_jumlah(nama: str, qty, serials: list) -> None:
    q = Decimal(str(qty or 0))
    if q != q.to_integral_value():
        raise GalatBerkode(422, "SERIAL_QTY_PECAHAN", f"{nama} memakai nomor seri; jumlahnya harus bilangan bulat.")
    if len(serials) != int(q):
        raise GalatBerkode(422, "SERIAL_JUMLAH_TAK_SAMA", f"{nama}: jumlah {int(q)}, nomor seri yang diisi {len(serials)}.")


async def _muat(conn, tenant_id: str, product_id, serials: list) -> dict:
    rows = await conn.fetch(
        "SELECT id, serial_number, status, warehouse_id FROM item_serials WHERE tenant_id = $1 AND item_id = $2 "
        "AND upper(serial_number) = ANY($3::text[]) FOR UPDATE", tenant_id, product_id, [s.upper() for s in serials])
    return {r["serial_number"].upper(): r for r in rows}


async def _taut(conn, tenant_id, ledger_id, serial_id, arah, status_sebelum, gudang_sebelum):
    await conn.execute(
        "INSERT INTO inventory_ledger_serials (ledger_id, serial_id, tenant_id, arah, status_sebelum, gudang_sebelum) VALUES ($1, $2, $3, $4, $5, $6)",
        ledger_id, serial_id, tenant_id, arah, status_sebelum, gudang_sebelum)


async def _jejak(conn, tenant_id, serial_id, jenis, dari_gudang, ke_gudang, dari_status, ke_status, ref: dict):
    await conn.execute(
        """INSERT INTO serial_movements (tenant_id, serial_id, movement_type, from_warehouse_id, to_warehouse_id, from_status, to_status,
                                         reference_type, reference_id, reference_number, performed_by)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)""",
        tenant_id, serial_id, jenis, dari_gudang, ke_gudang, dari_status, ke_status,
        ref.get("source_type"), ref.get("source_id"), ref.get("source_number"), ref.get("created_by"))


async def masuk(conn, tenant_id: str, product_id, nama: str, warehouse_id, ledger_id, serials: list, ref: dict) -> None:
    ada = await _muat(conn, tenant_id, product_id, serials)
    salah = [s for s in serials if s.upper() in ada and ada[s.upper()]["status"] in ("available", "reserved", "transit")]
    if salah:
        raise GalatBerkode(422, "SERIAL_SUDAH_ADA", f"Nomor seri {', '.join(salah)} untuk {nama} masih tercatat di stok.")
    for s in serials:
        lama = ada.get(s.upper())
        if lama:
            sid, st0, g0 = lama["id"], lama["status"], lama["warehouse_id"]
            await conn.execute("UPDATE item_serials SET status = 'available', warehouse_id = $3, updated_at = now() WHERE id = $1 AND tenant_id = $2",
                               sid, tenant_id, warehouse_id)
        else:
            sid, st0, g0 = await conn.fetchval(
                """INSERT INTO item_serials (tenant_id, item_id, serial_number, status, warehouse_id, received_date, created_by)
                   VALUES ($1, $2, $3, 'available', $4, $5, $6) RETURNING id""",
                tenant_id, product_id, s, warehouse_id, ref.get("movement_date"), ref.get("created_by")), None, None
        await _taut(conn, tenant_id, ledger_id, sid, "masuk", st0, g0)
        await _jejak(conn, tenant_id, sid, GERAK_MASUK, g0, warehouse_id, st0, "available", ref)


async def keluar(conn, tenant_id: str, product_id, nama: str, warehouse_id, ledger_id, serials: list, ref: dict) -> None:
    ada = await _muat(conn, tenant_id, product_id, serials)
    tak_ada = [s for s in serials if s.upper() not in ada]
    bukan = [s for s in serials if s.upper() in ada and (ada[s.upper()]["status"] != "available" or ada[s.upper()]["warehouse_id"] != warehouse_id)]
    if tak_ada or bukan:
        bagian = ([f"tidak dikenal: {', '.join(tak_ada)}"] if tak_ada else []) + ([f"tidak tersedia di gudang ini: {', '.join(bukan)}"] if bukan else [])
        raise GalatBerkode(422, "SERIAL_TAK_TERSEDIA", f"Nomor seri {nama} " + "; ".join(bagian) + ".")
    ke = STATUS_KELUAR.get(str(ref.get("source_type") or "").upper(), "sold")
    for s in serials:
        r = ada[s.upper()]
        await conn.execute("UPDATE item_serials SET status = $3, updated_at = now() WHERE id = $1 AND tenant_id = $2", r["id"], tenant_id, ke)
        await _taut(conn, tenant_id, ledger_id, r["id"], "keluar", r["status"], r["warehouse_id"])
        await _jejak(conn, tenant_id, r["id"], GERAK_KELUAR, r["warehouse_id"], None, r["status"], ke, ref)


async def balik(conn, tenant_id: str, ledger_asal, ledger_id, ref: dict) -> None:
    """Pulihkan serial baris asal. Serial yang punya taut LEBIH BARU dari baris asal (sudah bergerak lagi) -> 409, void ditolak."""
    taut = await conn.fetch(
        """SELECT t.serial_id, t.arah, t.status_sebelum, t.gudang_sebelum, s.serial_number, s.status, s.warehouse_id,
                  EXISTS (SELECT 1 FROM inventory_ledger_serials t2 WHERE t2.serial_id = t.serial_id AND t2.tenant_id = t.tenant_id
                          AND t2.created_at > t.created_at) AS bergerak
           FROM inventory_ledger_serials t JOIN item_serials s ON s.id = t.serial_id AND s.tenant_id = t.tenant_id
           WHERE t.ledger_id = $1 AND t.tenant_id = $2 FOR UPDATE OF s""", ledger_asal, tenant_id)
    lanjut = [t["serial_number"] for t in taut if t["bergerak"]]
    if lanjut:
        raise GalatBerkode(409, "SERIAL_SUDAH_BERGERAK", "Dokumen tidak bisa dibatalkan: nomor seri " + ", ".join(lanjut)
                           + " sudah dipakai transaksi sesudahnya. Batalkan transaksi itu dulu.")
    for t in taut:
        # masuk dibatalkan: kembali ke status sebelum masuk; serial yang LAHIR di baris asal (status_sebelum None) -> 'scrapped' (baris & jejak tetap, Law 2)
        st, g = t["status_sebelum"] or "scrapped", t["gudang_sebelum"]
        await conn.execute("UPDATE item_serials SET status = $3, warehouse_id = $4, updated_at = now() WHERE id = $1 AND tenant_id = $2",
                           t["serial_id"], tenant_id, st, g)
        await _taut(conn, tenant_id, ledger_id, t["serial_id"], "keluar" if t["arah"] == "masuk" else "masuk", t["status"], t["warehouse_id"])
        await _jejak(conn, tenant_id, t["serial_id"], GERAK_BALIK, t["warehouse_id"], g, t["status"], st, ref)


async def pada_kartu(conn, *, tenant_id, product_id, product_name, warehouse_id, ledger_id, quantity_in, quantity_out,
                     serials: Optional[list], balik_ledger, ref: dict) -> None:
    """Kait pintu kartu stok. serials=None & balik_ledger=None & barang tak wajib = tak ada apa pun (perilaku lama, satu SELECT)."""
    perlu = await wajib(conn, tenant_id, product_id)
    if not perlu:
        if serials:
            raise GalatBerkode(422, "SERIAL_TAK_DILACAK", f"{product_name} tidak memakai nomor seri; kosongkan nomor seri.")
        return
    if balik_ledger is not None:
        return await balik(conn, tenant_id, balik_ledger, ledger_id, ref)
    if serials is None:
        raise GalatBerkode(422, "SERIAL_WAJIB", f"{product_name} memakai nomor seri; transaksi ini belum mendukung nomor seri.")
    daftar = rapikan(serials)
    masuk_q, keluar_q = Decimal(str(quantity_in or 0)), Decimal(str(quantity_out or 0))
    cek_jumlah(product_name, masuk_q if masuk_q > 0 else keluar_q, daftar)
    if masuk_q > 0:
        await masuk(conn, tenant_id, product_id, product_name, warehouse_id, ledger_id, daftar, ref)
    else:
        await keluar(conn, tenant_id, product_id, product_name, warehouse_id, ledger_id, daftar, ref)
