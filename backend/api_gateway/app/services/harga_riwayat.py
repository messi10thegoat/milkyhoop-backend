"""BUG-252 (16 Nov 2026; putusan pemilik via MASTER): RIWAYAT perubahan Daftar Harga -- siapa, kapan, lama -> baru. SATU penulis + SATU pembaca.
Sumber = `audit_logs` (immutable: trigger anti ubah/hapus, Law 12), tanpa tabel/migrasi baru: eventType `PRICE_LIST_<AKSI>`, entity_type `price_list`, entity_id = id daftar,
entity_number = kode daftar, metadata jsonb {ringkas, jumlah, terpotong, baris[{...dari, ke}], header{dari, ke}}. Ditulis di TRANSAKSI pemanggil (gagal tulis = batal; jejak tak bohong).
Impor/ubah massal = SATU event berbaris (dibatasi MAKS_BARIS, `terpotong` + `jumlah` sebenarnya). Riwayat dimulai dari rilis ini (tanpa backfill; RIWAYAT_MULAI_TEKS)."""
import json
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

MAKS_BARIS = 200
ENTITAS = "price_list"
AKSI_TEKS = {
    "BUAT": "Daftar dibuat", "UBAH_HEADER": "Header daftar diubah", "STATUS": "Status daftar diubah", "BAWAAN": "Dijadikan daftar bawaan", "HAPUS": "Daftar dihapus",
    "TAMBAH_BARIS": "Harga ditambahkan", "UBAH_BARIS": "Harga diubah", "HAPUS_BARIS": "Harga dihapus", "UBAH_MASSAL": "Ubah harga massal", "IMPOR": "Impor Excel",
}
RIWAYAT_MULAI_TEKS = "Riwayat tercatat sejak {tanggal} (perubahan sebelum itu tidak tersimpan)."


def jsonable(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return str(v)


def _rp(v) -> str:
    d = Decimal(str(v))
    return "Rp " + (f"{d:,.0f}" if d == d.to_integral_value() else f"{d:,.2f}").replace(",", "X").replace(".", ",").replace("X", ".")


async def snapshot(conn, tid: str, line_id) -> Optional[dict]:
    """Keadaan SATU baris daftar (bersaring tenant) untuk dari/ke; None bila tak ada."""
    r = await conn.fetchrow(
        """SELECT pli.id, pli.item_id, COALESCE(NULLIF(pli.item_code, ''), p.item_code, p.sku) AS kode, p.nama_produk AS nama, pli.unit,
                  COALESCE(NULLIF(p.base_unit, ''), p.satuan) AS dasar, pli.min_quantity, pli.price, pli.min_price, pli.discount_percent, pli.start_date, pli.end_date
           FROM price_list_items pli JOIN price_lists pl ON pl.id = pli.price_list_id
           LEFT JOIN products p ON p.id = pli.item_id AND p.tenant_id = pl.tenant_id
           WHERE pl.tenant_id = $1 AND pli.id = $2""", tid, line_id)
    return bentuk(r) if r else None


async def snapshot_daftar(conn, tid: str, pl_id, batas: int = MAKS_BARIS) -> list:
    """Semua baris SATU daftar (bersaring tenant), urut kode/nama/satuan/qty/mulai, paling banyak `batas` -> [bentuk()]."""
    return [bentuk(r) for r in await conn.fetch(
        """SELECT pli.id, pli.item_id, COALESCE(NULLIF(pli.item_code, ''), p.item_code, p.sku) AS kode, p.nama_produk AS nama, pli.unit,
                  COALESCE(NULLIF(p.base_unit, ''), p.satuan) AS dasar, pli.min_quantity, pli.price, pli.min_price, pli.discount_percent, pli.start_date, pli.end_date
           FROM price_list_items pli JOIN price_lists pl ON pl.id = pli.price_list_id
           LEFT JOIN products p ON p.id = pli.item_id AND p.tenant_id = pl.tenant_id
           WHERE pl.tenant_id = $1 AND pli.price_list_id = $2
           ORDER BY kode, p.nama_produk, pli.unit NULLS FIRST, pli.min_quantity, pli.start_date NULLS FIRST, pli.id LIMIT $3""", tid, pl_id, batas)]


def bentuk(r) -> dict:
    """Baris (DB/dict) -> bentuk catatan riwayat: sama untuk dari dan ke."""
    unit = " ".join(str(r["unit"] or "").split()) or (" ".join(str(r["dasar"] or "").split()) or None)
    return {"line_id": str(r["id"]) if r.get("id") else None, "item_id": str(r["item_id"]) if r.get("item_id") else None, "kode": r.get("kode"), "nama": r.get("nama"),
            "satuan": unit, "min_quantity": float(r["min_quantity"]), "harga": float(r["price"]),
            "min_price": float(r["min_price"]) if r.get("min_price") is not None else None,
            "diskon_persen": float(r["discount_percent"]) if r.get("discount_percent") is not None else None,
            "mulai": r["start_date"].isoformat() if r.get("start_date") else None, "berakhir": r["end_date"].isoformat() if r.get("end_date") else None}


def ringkas_baris(dari: Optional[dict], ke: Optional[dict]) -> str:
    """Satu kalimat: 'Minyak (Pcs, min 1): Rp 10.000 -> Rp 11.000' (diubah) / 'Minyak (Pcs, min 1): Rp 10.000' (tambah/hapus)."""
    b = ke or dari
    ket = f"{b.get('nama') or 'Barang'} ({b.get('satuan') or 'satuan dasar'}, min {Decimal(str(b['min_quantity'])).normalize():f})"
    if b.get("mulai"):
        ket += f" mulai {b['mulai']}"
    if dari and ke:
        bagian = []
        for k, label in (("harga", "harga"), ("min_price", "minimum"), ("diskon_persen", "diskon")):
            if dari.get(k) != ke.get(k):
                a, c = dari.get(k), ke.get(k)
                f = (lambda v: "-" if v is None else (f"{v:g}%" if k == "diskon_persen" else _rp(v)))
                bagian.append(f"{label} {f(a)} → {f(c)}")
        if dari.get("berakhir") != ke.get("berakhir"):
            bagian.append(f"berakhir {dari.get('berakhir') or '-'} → {ke.get('berakhir') or '-'}")
        return f"{ket}: " + ("; ".join(bagian) or "tanpa perubahan nilai")
    return f"{ket}: {_rp(b['harga'])}"


async def catat(conn, ctx: dict, pl_id, kode_daftar: Optional[str], aksi: str, ringkas: str, baris: Optional[List[dict]] = None,
                header: Optional[dict] = None, jumlah: Optional[int] = None) -> None:
    """Tulis SATU event riwayat di transaksi pemanggil. baris = [{dari, ke, ringkas?}] (dari/ke = bentuk()), dipotong MAKS_BARIS."""
    if aksi not in AKSI_TEKS:
        raise ValueError(f"aksi riwayat tak dikenal: {aksi}")
    if kode_daftar is None:
        kode_daftar = await conn.fetchval("SELECT code FROM price_lists WHERE id = $1 AND tenant_id = $2", pl_id, ctx["tenant_id"])
    baris = baris or []
    meta = {"aksi": aksi, "ringkas": ringkas, "jumlah": jumlah if jumlah is not None else len(baris), "terpotong": len(baris) > MAKS_BARIS, "baris": baris[:MAKS_BARIS]}
    if header is not None:
        meta["header"] = header
    await conn.execute(
        """INSERT INTO audit_logs (id, "eventType", entity_type, entity_id, entity_number, tenant_id, source, metadata, success, "createdAt", "userId")
           VALUES (gen_random_uuid()::text, $1, $2, $3::uuid, $4, $5, 'api:price_list', $6::jsonb, true, now(), $7)""",
        f"PRICE_LIST_{aksi}", ENTITAS, str(pl_id), kode_daftar, ctx["tenant_id"], json.dumps(meta, default=jsonable), str(ctx.get("user_id") or "") or None)


async def daftar(conn, tid: str, pl_id, limit: int = 50, offset: int = 0, aksi: Optional[str] = None, dari: Optional[date] = None, sampai: Optional[date] = None) -> dict:
    """Riwayat SATU daftar (bersaring tenant + entity_id), terbaru dulu. -> {items[{id, waktu, aksi, aksi_text, ringkas, jumlah, terpotong, oleh{id,nama,peran}, baris[], header?}], total, mulai_teks}."""
    syarat, p = "", [tid, ENTITAS, str(pl_id)]
    if aksi:
        p.append(f"PRICE_LIST_{aksi}")
        syarat += f' AND a."eventType" = ${len(p)}'
    if dari:
        p.append(datetime.combine(dari, datetime.min.time()))
        syarat += f' AND a."createdAt" >= ${len(p)}'
    if sampai:
        p.append(datetime.combine(sampai, datetime.max.time()))
        syarat += f' AND a."createdAt" <= ${len(p)}'
    base = f'FROM audit_logs a WHERE a.tenant_id = $1 AND a.entity_type = $2 AND a.entity_id = $3::uuid{syarat}'
    total = await conn.fetchval(f"SELECT count(*) {base}", *p)
    rows = await conn.fetch(
        f"""SELECT a.id, a."eventType", a."createdAt", a.metadata, a."userId", u.name AS nama, u.username, u.role::text AS peran
            FROM audit_logs a LEFT JOIN "User" u ON u.id = a."userId" AND u."tenantId" = a.tenant_id
            WHERE a.tenant_id = $1 AND a.entity_type = $2 AND a.entity_id = $3::uuid{syarat} ORDER BY a."createdAt" DESC, a.id DESC LIMIT ${len(p) + 1} OFFSET ${len(p) + 2}""",
        *p, limit, offset)
    items = []
    for r in rows:
        m = r["metadata"] if isinstance(r["metadata"], dict) else json.loads(r["metadata"] or "{}")
        kode = r["eventType"][len("PRICE_LIST_"):]
        items.append({"id": r["id"], "waktu": r["createdAt"].isoformat(), "aksi": kode, "aksi_text": AKSI_TEKS.get(kode, kode), "ringkas": m.get("ringkas"),
                      "jumlah": m.get("jumlah", 0), "terpotong": bool(m.get("terpotong")), "baris": m.get("baris", []), **({"header": m["header"]} if "header" in m else {}),
                      "oleh": {"id": r["userId"], "nama": r["nama"] or r["username"], "peran": r["peran"]}})
    awal = await conn.fetchval("""SELECT min(a."createdAt") FROM audit_logs a WHERE a.tenant_id = $1 AND a."eventType" LIKE 'PRICE_LIST_%'""", tid)
    mulai = (RIWAYAT_MULAI_TEKS.format(tanggal=awal.date().isoformat()) if awal else
             "Belum ada perubahan tercatat. Riwayat dicatat sejak fitur ini aktif; perubahan sebelumnya tidak tersimpan.")
    return {"items": items, "total": int(total or 0), "tercatat_sejak": awal.date().isoformat() if awal else None, "mulai_teks": mulai}
