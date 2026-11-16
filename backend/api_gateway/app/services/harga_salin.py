"""BUG-256 (16 Nov 2026; D4 nilai tambah vs Accurate): SALIN daftar harga -- kode + nama baru, header + SEMUA baris apa adanya (tingkat qty, berjangka, diskon %, minimum, berakhir, satuan).
SATU penentu pratinjau == tulis (rencana), tulis = INSERT ... SELECT atomik di transaksi pemanggil + riwayat 252 (BUAT 'Disalin dari ...'). Daftar baru BUKAN bawaan dan tak terikat pelanggan
(pelanggan tetap di daftar sumber). Tahap 1 salin apa adanya (tanpa naik/turun %: itu Ubah harga massal D3 sesudah menyalin)."""
from typing import Optional

from . import harga_riwayat as HR
from .galat_berkode import GalatBerkode


def _blok(code: str, message: str, field: Optional[str] = None) -> dict:
    return {"code": code, "message": message, **({"field": field} if field else {})}


def _per(code: str, pesan: str) -> dict:
    return {"code": code, "butuh_konfirmasi": True, "pesan": pesan, "message": pesan}


def rapikan(kode, nama) -> tuple:
    return " ".join(str(kode or "").split()), " ".join(str(nama or "").split())


async def rencana(conn, tid: str, sumber_id, kode, nama, kunci: bool = False) -> dict:
    """READ ONLY (kunci=True: baris sumber dikunci FOR UPDATE). -> {ok, blocks[], peringatan[], rencana|None}. Blok: sumber tak ada, kode/nama kosong, kode sudah dipakai."""
    s = await conn.fetchrow(
        f"SELECT id, code, name, price_type, default_discount, default_markup, start_date, end_date, priority, description, is_active FROM price_lists WHERE id = $1 AND tenant_id = $2"
        f"{' FOR UPDATE' if kunci else ''}", sumber_id, tid)
    if s is None:
        return {"ok": False, "blocks": [_blok("DH_TAK_ADA", "Daftar harga tidak ditemukan.")], "peringatan": [], "rencana": None}
    kode, nama = rapikan(kode, nama)
    bl = []
    if not kode:
        bl.append(_blok("KODE_DAFTAR_KOSONG", "Kode daftar harga baru wajib diisi.", "code"))
    elif len(kode) > 50:
        bl.append(_blok("KODE_DAFTAR_TERLALU_PANJANG", "Kode daftar harga maksimal 50 karakter.", "code"))
    elif await conn.fetchval("SELECT 1 FROM price_lists WHERE tenant_id = $1 AND code = $2", tid, kode):
        bl.append(_blok("KODE_DAFTAR_GANDA", "Kode daftar harga itu sudah dipakai.", "code"))
    if not nama:
        bl.append(_blok("NAMA_DAFTAR_KOSONG", "Nama daftar harga baru wajib diisi.", "name"))
    ukur = await conn.fetchrow(
        """SELECT count(*) AS n, count(DISTINCT pli.item_id) AS barang, count(*) FILTER (WHERE pli.start_date IS NOT NULL) AS berjangka,
                  count(*) FILTER (WHERE p.id IS NULL OR p.deleted_at IS NOT NULL OR p.status <> 'active' OR NOT COALESCE(p.for_sales, true)) AS tak_aktif
           FROM price_list_items pli JOIN price_lists pl ON pl.id = pli.price_list_id
           LEFT JOIN products p ON p.id = pli.item_id AND p.tenant_id = pl.tenant_id
           WHERE pl.tenant_id = $1 AND pli.price_list_id = $2""", tid, sumber_id)
    per = []
    if ukur["tak_aktif"]:
        per.append(_per("DH_SALIN_BARANG_TIDAK_AKTIF", f"{ukur['tak_aktif']} harga milik barang yang sudah nonaktif atau tidak dijual ikut disalin apa adanya."))
    r = {"sumber": {"id": str(s["id"]), "code": s["code"], "name": s["name"]}, "baru": {"code": kode, "name": nama}, "jumlah_baris": int(ukur["n"]),
         "jumlah_barang": int(ukur["barang"]), "jumlah_berjangka": int(ukur["berjangka"]),
         "teks": f"Salin {s['code']} — {s['name']} menjadi {kode} — {nama}: {ukur['n']} harga ({ukur['barang']} barang"
                 + (f", {ukur['berjangka']} berjangka" if ukur["berjangka"] else "") + "). Daftar baru bukan bawaan dan belum dipakai pelanggan."}
    return {"ok": not bl, "blocks": bl, "peringatan": per, "rencana": r if not bl else None}


async def tulis(conn, ctx: dict, sumber_id, kode, nama) -> dict:
    """Tulis ATOMIK di transaksi pemanggil: validasi ULANG di bawah kunci, header + baris lewat INSERT ... SELECT, riwayat. Blok -> GalatBerkode (400/404/409)."""
    tid = ctx["tenant_id"]
    p = await rencana(conn, tid, sumber_id, kode, nama, kunci=True)
    if not p["ok"]:
        b = p["blocks"][0]
        st = 404 if b["code"] == "DH_TAK_ADA" else (409 if b["code"] == "KODE_DAFTAR_GANDA" else 400)
        raise GalatBerkode(st, b["code"], b["message"], field=b.get("field"))
    kode, nama = rapikan(kode, nama)
    baru = await conn.fetchval(
        """INSERT INTO price_lists (tenant_id, code, name, price_type, default_discount, default_markup, start_date, end_date, priority, description, is_default, created_by)
           SELECT tenant_id, $3, $4, price_type, default_discount, default_markup, start_date, end_date, priority, description, false, $5
           FROM price_lists WHERE id = $1 AND tenant_id = $2 RETURNING id""", sumber_id, tid, kode, nama, ctx.get("user_id"))
    n = await conn.fetchval(
        """WITH ins AS (
             INSERT INTO price_list_items (price_list_id, item_id, item_code, unit, price, min_quantity, discount_percent, markup_percent, start_date, end_date, is_active,
                                           is_default_unit, min_price)
             SELECT $1, i.item_id, i.item_code, i.unit, i.price, i.min_quantity, i.discount_percent, i.markup_percent, i.start_date, i.end_date, i.is_active,
                    i.is_default_unit, i.min_price
             FROM price_list_items i JOIN price_lists pl ON pl.id = i.price_list_id WHERE i.price_list_id = $2 AND pl.tenant_id = $3 RETURNING 1)
           SELECT count(*) FROM ins""", baru, sumber_id, tid)
    r = p["rencana"]
    await HR.catat(conn, ctx, baru, kode, "BUAT", f"Disalin dari {r['sumber']['code']} — {r['sumber']['name']}: {n} harga",
                   [{"dari": None, "ke": x} for x in await HR.snapshot_daftar(conn, tid, baru)], header={"dari": {"sumber": r["sumber"]["code"]}, "ke": {"code": kode, "name": nama}}, jumlah=int(n))
    return {"id": str(baru), "code": kode, "name": nama, "jumlah_baris": int(n), "sumber_id": str(sumber_id)}
